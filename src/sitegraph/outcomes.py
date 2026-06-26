from __future__ import annotations

MAX_OUTCOME_LABELS = 8
MAX_OUTCOME_SOURCES = 16
MAX_OUTCOME_SECTION_IDS = 16


def append_limited_unique(record: dict, key: str, value: str | None, limit: int) -> None:
    if not value:
        return
    items = record.setdefault(key, [])
    if not isinstance(items, list):
        raise TypeError(f"outcome record {key} must be a list")
    if value not in items:
        items.append(value)
    if len(items) > limit:
        del items[limit:]


def _dedupe_limited(values: object, limit: int) -> list:
    if not isinstance(values, list):
        return []
    out = []
    seen = set()
    for value in values:
        marker = value if isinstance(value, (str, int, float, bool, type(None))) else repr(value)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def compact_outcome_record(record: dict) -> dict:
    record["labels"] = _dedupe_limited(record.get("labels"), MAX_OUTCOME_LABELS)
    record["sources"] = _dedupe_limited(record.get("sources"), MAX_OUTCOME_SOURCES)
    record["section_ids"] = _dedupe_limited(record.get("section_ids"), MAX_OUTCOME_SECTION_IDS)
    return record


def compact_url_outcomes(url_outcomes: object) -> dict:
    if not isinstance(url_outcomes, dict):
        return {}
    compacted = {}
    for url, record in url_outcomes.items():
        if isinstance(record, dict):
            compacted[url] = compact_outcome_record(record)
    return compacted
