from sitegraph.outcomes import (
    MAX_OUTCOME_SOURCES,
    append_limited_unique,
    compact_url_outcomes,
)


def test_append_limited_unique_checks_the_whole_existing_list():
    record = {"sources": [f"https://example.edu/{index}" for index in range(MAX_OUTCOME_SOURCES)]}
    duplicate = record["sources"][-1]

    append_limited_unique(record, "sources", duplicate, MAX_OUTCOME_SOURCES)

    assert record["sources"].count(duplicate) == 1
    assert len(record["sources"]) == MAX_OUTCOME_SOURCES


def test_append_limited_unique_caps_new_values():
    record = {"sources": [f"https://example.edu/{index}" for index in range(MAX_OUTCOME_SOURCES)]}

    append_limited_unique(record, "sources", "https://example.edu/new", MAX_OUTCOME_SOURCES)

    assert len(record["sources"]) == MAX_OUTCOME_SOURCES
    assert "https://example.edu/new" not in record["sources"]


def test_compact_url_outcomes_dedupes_and_caps_sources():
    duplicated_sources = ["https://example.edu/a", "https://example.edu/b"] * 20
    outcomes = {
        "https://example.edu/page.htm": {
            "url": "https://example.edu/page.htm",
            "target_type": "detail_article_page",
            "outcome": "crawled_detail_ok",
            "labels": ["A", "A"],
            "sources": duplicated_sources,
            "section_ids": ["section", "section"],
        }
    }

    compacted = compact_url_outcomes(outcomes)
    record = compacted["https://example.edu/page.htm"]

    assert record["labels"] == ["A"]
    assert record["sources"] == ["https://example.edu/a", "https://example.edu/b"]
    assert record["section_ids"] == ["section"]
