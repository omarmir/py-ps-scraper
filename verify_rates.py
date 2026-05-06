#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "beautifulsoup4>=4.12",
#   "lxml>=5.0",
#   "python-dateutil>=2.9",
# ]
# ///

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from scrape_rates import (
    AGREEMENT_DIR,
    COMBINED_PATH,
    INDEX_URL,
    build_combined,
    canonical_row_key,
    discover_agreement_links,
    fetch_html,
    parse_soup,
    scrape_agreement,
)


def load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def verify_agreement_outputs() -> list[dict]:
    agreement_records: list[dict] = []
    links = discover_agreement_links(parse_soup(fetch_html(INDEX_URL)))

    for agreement_name, url in links:
        slug = url.rsplit("/", 1)[-1].split(".")[0]
        expected = scrape_agreement(agreement_name, url)
        path = AGREEMENT_DIR / f"{slug}.json"
        actual = load_json(path)
        if actual != expected:
            raise ValueError(f"Agreement output mismatch for {slug}")
        agreement_records.extend(actual)
        print(f"verified agreement {slug}: {len(actual)} rows")

    return agreement_records


def verify_combined_output(agreement_records: list[dict]) -> None:
    combined = load_json(COMBINED_PATH)
    rebuilt = build_combined(agreement_records)
    if combined != rebuilt:
        raise ValueError("Combined output does not match canonical merged rebuild")

    key_counts = Counter(canonical_row_key(row) for row in combined)
    duplicates = [key for key, count in key_counts.items() if count > 1]
    if duplicates:
        raise ValueError(f"Combined output has duplicate canonical keys: {duplicates}")

    for row in combined:
        if "sources" not in row or not row["sources"]:
            raise ValueError(f"Combined row missing sources: {row['grpLvl']}")

    print(f"verified combined: {len(combined)} rows")


def main() -> None:
    agreement_records = verify_agreement_outputs()
    verify_combined_output(agreement_records)
    print("verification complete")


if __name__ == "__main__":
    main()
