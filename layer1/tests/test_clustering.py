"""Clustering and stable golden-ID tests (architecture §7). Synthetic keys only."""
import numpy as np

from layer1.identity import clustering


def comps(n, edges):
    a = np.array([e[0] for e in edges], dtype=np.int64)
    b = np.array([e[1] for e in edges], dtype=np.int64)
    return clustering.connected_components(n, a, b).tolist()


def test_connected_components():
    labels = comps(6, [(0, 1), (1, 2), (4, 5)])
    assert labels[0] == labels[1] == labels[2]
    assert labels[4] == labels[5] != labels[0]
    assert labels[3] not in (labels[0], labels[4])


def edge(a, b, p, removable=True):
    return {"key_l": a, "key_r": b, "prob": p, "removable": removable}


def test_conflicting_national_ids_split_at_weakest_link():
    keys = ["A", "B", "C"]
    edges = [edge("A", "B", 0.99), edge("B", "C", 0.96)]
    kept, demoted, labels, bad = clustering.enforce_cluster_limits(keys, edges, {"A": "h1", "C": "h2"})
    assert demoted == [edge("B", "C", 0.96)] and not bad
    assert labels[0] == labels[1] != labels[2]


def test_oversized_cluster_is_cut_back():
    keys = [f"K{i}" for i in range(12)]
    edges = [edge(keys[i], keys[i + 1], 0.99) for i in range(11)]
    edges[5] = edge(keys[5], keys[6], 0.951)
    kept, demoted, labels, bad = clustering.enforce_cluster_limits(keys, edges, {}, max_size=10)
    assert demoted == [edge(keys[5], keys[6], 0.951)] and not bad


def test_deterministic_links_are_never_cut():
    keys = ["A", "B"]
    kept, demoted, labels, bad = clustering.enforce_cluster_limits(
        keys, [edge("A", "B", 1.0, removable=False)], {"A": "h1", "B": "h2"})
    assert not demoted and bad          # flagged for a person, not broken automatically


def test_new_ids_on_first_run():
    assignment, aliases, changed, nxt = clustering.assign_golden_ids({0: ["A", "B"], 1: ["C"]}, {}, {}, 1)
    assert assignment == {"A": "GC00000001", "B": "GC00000001", "C": "GC00000002"}
    assert aliases == [] and changed == set() and nxt == 3


def test_ids_are_stable_across_runs():
    prev = {"A": "GC00000007", "B": "GC00000007", "C": "GC00000009"}
    assignment, aliases, changed, _ = clustering.assign_golden_ids({0: ["A", "B"], 1: ["C"]}, prev, {}, 10)
    assert assignment == prev and not aliases and not changed


def test_merge_keeps_oldest_id():
    prev = {"A": "GC00000005", "B": "GC00000002"}
    created = {"GC00000005": 5, "GC00000002": 2}
    assignment, aliases, changed, _ = clustering.assign_golden_ids({0: ["A", "B"]}, prev, created, 10)
    assert set(assignment.values()) == {"GC00000002"}
    assert aliases == [("GC00000005", "GC00000002", "merged_into")]
    assert changed == {"GC00000005", "GC00000002"}


def test_split_largest_part_keeps_id():
    prev = {"A": "GC00000003", "B": "GC00000003", "C": "GC00000003"}
    assignment, aliases, changed, nxt = clustering.assign_golden_ids({0: ["A", "B"], 1: ["C"]}, prev, {}, 10)
    assert assignment["A"] == assignment["B"] == "GC00000003"
    assert assignment["C"] == "GC00000010" and nxt == 11
    assert ("GC00000010", "GC00000003", "split_from") in aliases
    assert {"GC00000003", "GC00000010"} <= changed
