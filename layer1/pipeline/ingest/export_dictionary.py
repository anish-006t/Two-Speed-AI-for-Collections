"""Export DATA_DICTIONARY.xlsx to the CSVs CLAUDE.md points to (docs/data_dictionary/data_dictionary_{tables,columns,relationships}.csv).

Usage:  python -m layer1.pipeline.ingest.export_dictionary
"""
import pandas as pd

from layer1.pipeline import config

SHEETS = {"Tables": "tables", "Columns": "columns", "Relationships": "relationships"}


def main():
    book = pd.read_excel(config.RAW / "DATA_DICTIONARY.xlsx", sheet_name=None, dtype=str)
    for sheet, name in SHEETS.items():
        df = book[sheet].dropna(how="all")
        out = config.ROOT / "docs" / "data_dictionary" / f"data_dictionary_{name}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)
        print(f"{sheet}: {len(df):,} rows -> {out.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
