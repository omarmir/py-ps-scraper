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
import re
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dateutil import parser

from group import RateAgreement, RateGroup, RateSteps


BASE_URL = "https://www.canada.ca"
INDEX_URL = (
    "https://www.canada.ca/en/treasury-board-secretariat/topics/pay/"
    "collective-agreements.html"
)
USER_AGENT = "Mozilla/5.0"
DATA_DIR = Path("data")
AGREEMENT_DIR = DATA_DIR / "agreements"
COMBINED_PATH = DATA_DIR / "combined" / "payscales.json"

RATES_OF_PAY_RE = re.compile(r"rates of pay", re.IGNORECASE)
CONTINUED_RE = re.compile(
    r"\s*(?::\s*continuation|[-–]\s*continued|\(continued\)|\(continuation\)|continuation)\s*$",
    re.IGNORECASE,
)
STEPS_SUFFIX_RE = re.compile(r"\s*\(steps?[^)]*\)\s*$", re.IGNORECASE)
SIGNING_DATE_RE = re.compile(
    r"signed(?:\s+\w+)*\s+on\s+([A-Z][a-z]+ \d{1,2}, \d{4})", re.IGNORECASE
)
PAY_TYPE_PATTERNS = [
    ("basic hourly rates of pay (in dollars)", "basic_hourly"),
    ("hourly rates of pay (in dollars)", "hourly"),
    ("weekly rates of pay (in dollars)", "weekly"),
    ("daily rates of pay (in dollars)", "daily"),
    ("annual rates of pay (in dollars)", "annual"),
    ("basic hourly rates of pay", "basic_hourly"),
    ("hourly rates of pay", "hourly"),
    ("weekly rates of pay", "weekly"),
    ("daily rates of pay", "daily"),
    ("annual rates of pay", "annual"),
]


def fetch_html(url: str) -> str:
    result = subprocess.run(
        ["curl", "-fsSL", "-A", USER_AGENT, url],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def parse_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def normalize_text(text: str) -> str:
    cleaned = text.replace("\xa0", " ")
    cleaned = (
        cleaned.replace("–", "-")
        .replace("—", "-")
        .replace("‑", "-")
        .replace("‐", "-")
        .replace("“", '"')
        .replace("”", '"')
        .replace("’", "'")
    )
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def discover_agreement_links(index_soup: BeautifulSoup) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()

    for anchor in index_soup.find("main").select("a[href]"):
        href = anchor["href"]
        if not href.endswith(".html"):
            continue
        if "/topics/pay/collective-agreements/" not in href:
            continue
        if "archived" in href:
            continue
        full_url = urljoin(BASE_URL, href)
        if full_url in seen:
            continue
        seen.add(full_url)
        links.append((normalize_text(anchor.get_text(" ", strip=True)), full_url))

    return links


def extract_signing_date(main_text: str) -> str | None:
    match = SIGNING_DATE_RE.search(main_text)
    if not match:
        return None
    return parser.parse(match.group(1), fuzzy=True).date().isoformat()


def canonical_caption(caption: str) -> str:
    base = normalize_text(caption)
    base = CONTINUED_RE.sub("", base)
    for pattern, _ in PAY_TYPE_PATTERNS:
        base = re.sub(re.escape(pattern), "", base, flags=re.IGNORECASE)
    base = STEPS_SUFFIX_RE.sub("", base)
    base = base.strip(" :-")
    return normalize_text(base)


def extract_pay_type(caption: str) -> str:
    normalized = normalize_text(caption).lower()
    for pattern, pay_type in PAY_TYPE_PATTERNS:
        if pattern in normalized:
            return pay_type
    return "unknown"


def parse_group_and_level(label: str) -> tuple[str, str, str]:
    label = normalize_text(label)
    match = re.match(r"^(?P<group>[A-Z]{1,10}(?:-[A-Z]{1,10})*)-(?P<level>[A-Z0-9/]+)$", label)
    if not match:
        match = re.match(r"^(?P<group>[A-Z]{1,10}(?:-[A-Z]{1,10})*) (?P<level>[A-Z0-9/]+)$", label)
    if not match:
        match = re.match(r"^(?P<group>[A-Z]{1,10}) - (?P<level>.+)$", label)

    if not match:
        return label, "", label

    group = match.group("group")
    level = normalize_text(match.group("level"))
    if level.isdigit():
        level = str(int(level))
    grp_lvl = f"{group}-{level}" if level else group
    return group, level, grp_lvl


def looks_like_classification_label(caption: str) -> bool:
    caption = normalize_text(caption)
    if "effective" in caption.lower():
        return False
    return bool(re.match(r"^[A-Z0-9][A-Za-z0-9 ()/\-]+$", caption)) and any(
        char.isdigit() for char in caption
    )


def parse_step_number(header_text: str, fallback: int) -> int:
    header_text = normalize_text(header_text)
    step_match = re.search(r"step\s*([0-9]+)", header_text, re.IGNORECASE)
    if step_match:
        return int(step_match.group(1))

    range_step_match = re.search(r"range\s*/?\s*step\s*([0-9]+)", header_text, re.IGNORECASE)
    if range_step_match:
        return int(range_step_match.group(1))

    if "range" in header_text.lower():
        return fallback

    return fallback


def parse_effective_date(label: str, signing_date: str | None) -> str | None:
    label = normalize_text(label).lstrip(">")
    if not re.search(r"\d{4}", label):
        if "signing" in label.lower() and signing_date:
            return signing_date
        return None

    try:
        return parser.parse(label, fuzzy=True).date().isoformat()
    except (ValueError, OverflowError):
        if "signing" in label.lower() and signing_date:
            return signing_date
        return None


def parse_amounts(cell_text: str) -> list[int | float]:
    cleaned = normalize_text(cell_text)
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    numbers = re.findall(r"\d[\d,]*(?:\.\d+)?", cleaned)
    parsed: list[int | float] = []
    for number in numbers:
        normalized = number.replace(",", "")
        if "." in normalized:
            parsed.append(float(normalized))
        else:
            parsed.append(int(normalized))
    return parsed


def merge_table_rows(existing: OrderedDict[str, list[RateSteps]], new: OrderedDict[str, list[RateSteps]]) -> None:
    for effective_date, steps in new.items():
        if effective_date not in existing:
            existing[effective_date] = steps
            continue

        by_signature = {(step.step, tuple(step.amount)): step for step in existing[effective_date]}
        for step in steps:
            signature = (step.step, tuple(step.amount))
            if signature not in by_signature:
                existing[effective_date].append(step)
        existing[effective_date].sort(key=lambda item: item.step)


def parse_rate_table_with_signing_date(table, signing_date: str | None) -> OrderedDict[str, list[RateSteps]]:
    rows = table.find_all("tr")
    if not rows:
        return OrderedDict()

    header_cells = rows[0].find_all(["th", "td"])
    if len(header_cells) < 2:
        return OrderedDict()

    step_numbers = [
        parse_step_number(cell.get_text(" ", strip=True), idx)
        for idx, cell in enumerate(header_cells[1:], start=1)
    ]

    parsed: OrderedDict[str, list[RateSteps]] = OrderedDict()
    for row in rows[1:]:
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue

        effective_date = parse_effective_date(cells[0].get_text(" ", strip=True), signing_date)
        if not effective_date:
            continue

        rate_steps: list[RateSteps] = []
        for step_number, amount_cell in zip(step_numbers, cells[1:]):
            amounts = parse_amounts(amount_cell.get_text(" ", strip=True))
            if not amounts:
                continue
            rate_steps.append(RateSteps(step_number, amounts))

        if rate_steps:
            parsed[effective_date] = rate_steps

    return parsed


def parse_symbol_date_map(table) -> OrderedDict[str, str]:
    symbol_dates: OrderedDict[str, str] = OrderedDict()

    for sibling in table.find_previous_siblings():
        if getattr(sibling, "name", None) != "ul":
            continue

        items = sibling.find_all("li")
        for item in items:
            text = normalize_text(item.get_text(" ", strip=True))
            symbol_match = re.match(r'^([A-Z$]+)\)\s*Effective\s+(.+)$', text, re.IGNORECASE)
            if not symbol_match:
                continue
            symbol = symbol_match.group(1)
            raw_date = symbol_match.group(2)
            raw_date = re.sub(r"table\s+\d+\s+note\s+\d+", "", raw_date, flags=re.IGNORECASE)
            raw_date = re.sub(r"-\s*wage adjustment", "", raw_date, flags=re.IGNORECASE)
            symbol_dates[symbol] = parser.parse(raw_date, fuzzy=True).date().isoformat()

        if symbol_dates:
            return symbol_dates

    return symbol_dates


def parse_ship_repair_west_matrix(table) -> list[dict]:
    rows = table.find_all("tr")
    if len(rows) < 2:
        return []

    header_cells = [normalize_text(cell.get_text(" ", strip=True)) for cell in rows[0].find_all(["th", "td"])]
    if len(header_cells) < 5:
        return []
    if header_cells[0].lower() != "pay group" or "sub-group and level" not in header_cells[1].lower():
        return []

    symbol_dates = parse_symbol_date_map(table)
    if not symbol_dates:
        return []

    data_columns = header_cells[3:]
    grouped_rows: OrderedDict[str, OrderedDict[str, list[RateSteps]]] = OrderedDict()

    for row in rows[1:]:
        cells = [normalize_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
        if len(cells) < len(header_cells) - 1:
            continue

        if len(cells) == len(header_cells):
            subgroup = cells[1]
            title = cells[2]
            amounts = cells[3:]
        else:
            subgroup = cells[0]
            title = cells[1]
            amounts = cells[2:]

        label = normalize_text(subgroup)
        if not label:
            continue

        grouped_rows.setdefault(label, OrderedDict())
        for column_symbol, amount_text in zip(data_columns, amounts):
            symbol = column_symbol.replace("$", "")
            effective_date = symbol_dates.get(symbol)
            parsed_amounts = parse_amounts(amount_text)
            if not effective_date or not parsed_amounts:
                continue
            grouped_rows[label].setdefault(effective_date, [])
            grouped_rows[label][effective_date].append(RateSteps(1, parsed_amounts))

    results: list[dict] = []
    for label, date_rows in grouped_rows.items():
        group, level, grp_lvl = parse_group_and_level(label)
        agreements = [
            RateAgreement(effective_date, sorted(rate_steps, key=lambda item: item.step))
            for effective_date, rate_steps in date_rows.items()
        ]
        record = RateGroup(group, level, agreements).to_dict()
        record["grpLvl"] = grp_lvl
        record["source"] = {
            "agreement": "Ship Repair (West)",
            "url": "https://www.canada.ca/en/treasury-board-secretariat/topics/pay/collective-agreements/srw.html",
        }
        results.append(record)

    results.sort(key=lambda item: item["grpLvl"])
    return results


def scrape_agreement(agreement_name: str, url: str) -> list[dict]:
    html = fetch_html(url)
    soup = parse_soup(html)
    main = soup.find("main")
    main_text = normalize_text(main.get_text(" ", strip=True))
    signing_date = extract_signing_date(main_text)

    grouped_tables: OrderedDict[tuple[str, str], OrderedDict[str, list[RateSteps]]] = OrderedDict()
    for table in main.find_all("table"):
        caption_tag = table.find("caption")
        if not caption_tag:
            continue

        caption = normalize_text(caption_tag.get_text(" ", strip=True))
        if caption.lower() == "conversion table":
            continue
        if re.match(r"^[A-Z$]\)\s*effective\b", caption, re.IGNORECASE):
            continue

        first_row = table.find("tr")
        first_row_text = normalize_text(first_row.get_text(" ", strip=True)) if first_row else ""
        is_rates_caption = bool(RATES_OF_PAY_RE.search(caption))
        is_coded_caption = "effective date" in first_row_text.lower() and looks_like_classification_label(caption)
        if not is_rates_caption and not is_coded_caption:
            continue

        label = canonical_caption(caption)
        pay_type = extract_pay_type(caption)
        if not label:
            continue

        if pay_type == "unknown":
            existing_pay_types = [existing_pay_type for existing_label, existing_pay_type in grouped_tables if existing_label == label]
            if len(existing_pay_types) == 1:
                pay_type = existing_pay_types[0]

        parsed_rows = parse_rate_table_with_signing_date(table, signing_date)
        if not parsed_rows:
            continue

        key = (label, pay_type)
        grouped_tables.setdefault(key, OrderedDict())
        merge_table_rows(grouped_tables[key], parsed_rows)

    results: list[dict] = []
    for (label, pay_type), date_rows in grouped_tables.items():
        group, level, grp_lvl = parse_group_and_level(label)
        if pay_type not in {"annual", "unknown"}:
            grp_lvl = f"{grp_lvl}@{pay_type}"
        agreements = [
            RateAgreement(
                effective_date,
                sorted(rate_steps, key=lambda item: item.step),
            )
            for effective_date, rate_steps in date_rows.items()
        ]
        record = RateGroup(group, level, agreements).to_dict()
        record["grpLvl"] = grp_lvl
        record["source"] = {
            "agreement": agreement_name,
            "url": url,
        }
        if pay_type != "unknown":
            record["payType"] = pay_type
        results.append(record)

    if not results:
        for table in main.find_all("table"):
            special_results = parse_ship_repair_west_matrix(table)
            if special_results:
                return special_results

    results.sort(key=lambda item: item["grpLvl"])
    return results


def write_json(path: Path, payload: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(payload), indent=2) + "\n")


def canonical_row_key(row: dict) -> tuple[str, str | None]:
    return row["grpLvl"], row.get("payType")


def agreement_signature(row: dict) -> tuple:
    return tuple(
        (
            agreement["effectiveDate"],
            tuple(
                (step["step"], tuple(step["amount"]))
                for step in agreement["rateStepsList"]
            ),
        )
        for agreement in row["rateAgreements"]
    )


def merge_sources(rows: list[dict]) -> dict:
    canonical = json.loads(json.dumps(rows[0]))
    source_entries = []
    seen_sources = set()

    for row in rows:
        source = row.get("source")
        if not source:
            continue
        source_key = tuple(sorted(source.items()))
        if source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        source_entries.append(source)

    canonical["sources"] = source_entries
    if len(source_entries) == 1:
        canonical["source"] = source_entries[0]
    else:
        canonical.pop("source", None)
    return canonical


def source_slug(row: dict) -> str:
    url = row["source"]["url"]
    return url.rsplit("/", 1)[-1].split(".")[0]


def build_combined(records: list[dict]) -> list[dict]:
    grouped: OrderedDict[tuple[str, str | None], list[dict]] = OrderedDict()
    for row in records:
        grouped.setdefault(canonical_row_key(row), []).append(row)

    combined: list[dict] = []
    for key, rows in grouped.items():
        by_signature: OrderedDict[tuple, list[dict]] = OrderedDict()
        for row in rows:
            by_signature.setdefault(agreement_signature(row), []).append(row)

        if len(by_signature) == 1:
            combined.append(merge_sources(rows))
            continue

        for signature_rows in by_signature.values():
            canonical = merge_sources(signature_rows)
            original_grp_lvl = canonical["grpLvl"]
            canonical["classification"] = original_grp_lvl
            canonical["grpLvl"] = f"{original_grp_lvl}#{source_slug(signature_rows[0])}"
            combined.append(canonical)

    combined.sort(key=lambda item: item["grpLvl"])
    return combined


def main() -> None:
    index_html = fetch_html(INDEX_URL)
    agreements = discover_agreement_links(parse_soup(index_html))

    combined_records: list[dict] = []
    AGREEMENT_DIR.mkdir(parents=True, exist_ok=True)
    COMBINED_PATH.parent.mkdir(parents=True, exist_ok=True)

    for agreement_name, url in agreements:
        slug = url.rsplit("/", 1)[-1].split(".")[0]
        records = scrape_agreement(agreement_name, url)
        write_json(AGREEMENT_DIR / f"{slug}.json", records)
        combined_records.extend(records)
        print(f"{slug}: {len(records)} classifications")

    combined = build_combined(combined_records)
    write_json(COMBINED_PATH, combined)
    print(f"combined: {len(combined)} classifications")


if __name__ == "__main__":
    main()
