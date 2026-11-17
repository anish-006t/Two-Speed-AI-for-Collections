"""Clustering, stable golden IDs and the crosswalk (architecture §7 "Clustering and stable IDs").

- Clusters are connected components over accepted links only: deterministic links, auto-links and human links.
- A cluster with more than MAX_CLUSTER_SIZE keys, or with two different national ID hashes, sends its weakest
  Splink links back to review until it is within limits.
- A cluster that overlaps an existing golden ID keeps it. On a merge the oldest ID survives and the others go to
  identity.golden_alias. On a split the largest part keeps the ID and the other parts get new IDs.
"""
from collections import Counter, defaultdict

import numpy as np

MAX_CLUSTER_SIZE = 10
ID_PREFIX = "GC"


def connected_components(n_nodes: int, edges_a: np.ndarray, edges_b: np.ndarray) -> np.ndarray:
    """Union-find over integer node ids. Returns a component label (the root node id) per node."""
    parent = list(range(n_nodes))   # a Python list: element access is much faster than numpy in this loop

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:          # path compression
            parent[x], x = root, parent[x]
        return root

    for a, b in zip(edges_a.tolist(), edges_b.tolist()):
        ra, rb = find(a), find(b)
        if ra != rb:
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb
    return np.fromiter((find(i) for i in range(n_nodes)), dtype=np.int64, count=n_nodes)


def enforce_cluster_limits_arrays(n_nodes, a, b, prob, removable, hash_code, max_size=MAX_CLUSTER_SIZE,
                                  max_rounds=50):
    """Array version of the cluster limits (memory-light for millions of links).

    a, b: int node ids per edge; prob: float per edge; removable: bool per edge (Splink auto-links);
    hash_code: int per node (national ID hash as a code, -1 when missing).
    Returns (kept_mask, labels, still_bad_roots). Edges not kept are demoted to review.
    """
    import pandas as pd

    kept = np.ones(len(a), dtype=bool)
    has_hash = hash_code >= 0
    for _ in range(max_rounds):
        labels = connected_components(n_nodes, a[kept], b[kept])
        sizes = np.bincount(labels, minlength=n_nodes)
        bad = set(np.nonzero(sizes > max_size)[0].tolist())
        lh = pd.DataFrame({"label": labels[has_hash], "h": hash_code[has_hash]}).drop_duplicates()
        multi = lh.groupby("label").size()
        bad |= set(multi[multi > 1].index.tolist())
        if not bad:
            return kept, labels, set()
        edge_root = labels[a]
        cand = kept & removable & np.isin(edge_root, list(bad))
        if not cand.any():
            return kept, labels, bad          # only deterministic or human links left: flag, don't break
        weakest = pd.Series(prob[cand]).groupby(edge_root[cand]).min()
        threshold = np.full(n_nodes, -1.0)
        threshold[weakest.index.to_numpy()] = weakest.to_numpy()
        kept &= ~(cand & (prob <= threshold[edge_root]))
    labels = connected_components(n_nodes, a[kept], b[kept])
    return kept, labels, bad


def enforce_cluster_limits(keys, edges, id_hash, max_size=MAX_CLUSTER_SIZE, max_rounds=50):
    """Dict-friendly wrapper over enforce_cluster_limits_arrays (used by tests and small inputs).

    keys: list of node keys. edges: list of dicts with key_l, key_r, prob, removable (True for Splink auto-links).
    id_hash: dict key -> national_id_hash (or None). Returns (kept_edges, demoted_edges, labels, still_bad_roots).
    """
    index = {k: i for i, k in enumerate(keys)}
    codes = {}
    hash_code = np.array([codes.setdefault(id_hash[k], len(codes)) if id_hash.get(k) is not None else -1
                          for k in keys], dtype=np.int64)
    a = np.array([index[e["key_l"]] for e in edges], dtype=np.int64)
    b = np.array([index[e["key_r"]] for e in edges], dtype=np.int64)
    prob = np.array([e["prob"] for e in edges], dtype=float)
    removable = np.array([e["removable"] for e in edges], dtype=bool)
    kept, labels, bad = enforce_cluster_limits_arrays(len(keys), a, b, prob, removable, hash_code, max_size, max_rounds)
    return ([e for e, k in zip(edges, kept) if k], [e for e, k in zip(edges, kept) if not k], labels, bad)


def assign_golden_ids(components, previous, created_order, next_seq):
    """Give every component a golden ID, keeping existing IDs stable.

    components: dict component_label -> list of keys. previous: dict key -> golden id from the current crosswalk.
    created_order: dict golden id -> sortable creation rank (older = smaller). next_seq: next new-ID number.
    Returns (key -> golden id, alias rows [(alias_id, golden_id, reason)], changed golden ids, next_seq).
    """
    winner = {}
    for comp, members in components.items():
        counts = Counter(previous[k] for k in members if previous.get(k))
        if counts:
            # Most members wins; tie -> oldest ID (architecture: on a merge the oldest ID survives).
            winner[comp] = min(counts, key=lambda g: (-counts[g], created_order.get(g, float("inf")), g))
        else:
            winner[comp] = None

    # Splits: one old ID won by several components -> the largest keeps it.
    by_id = defaultdict(list)
    for comp, gid in winner.items():
        if gid:
            by_id[gid].append(comp)
    aliases, changed = [], set()
    for gid, comps in by_id.items():
        if len(comps) > 1:
            comps.sort(key=lambda c: (-len(components[c]), min(components[c])))
            for c in comps[1:]:
                winner[c] = None
                changed.add(gid)

    assignment = {}
    for comp in sorted(components, key=lambda c: min(components[c])):
        members = components[comp]
        gid = winner[comp]
        if gid is None:
            gid = f"{ID_PREFIX}{next_seq:08d}"
            next_seq += 1
            split_from = {previous[k] for k in members if previous.get(k)}
            for old in sorted(split_from):
                aliases.append((gid, old, "split_from"))
                changed.add(gid)
        for old in sorted({previous[k] for k in members if previous.get(k)} - {gid}):
            if not any(a[0] == gid and a[1] == old for a in aliases):
                aliases.append((old, gid, "merged_into"))
                changed.update({old, gid})
        for k in members:
            assignment[k] = gid
    return assignment, aliases, changed, next_seq
