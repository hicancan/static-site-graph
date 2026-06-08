from __future__ import annotations

from sitegraph.crawl_pages import CrawlPageRunner


def test_inline_section_queue_can_be_disabled():
    runner = CrawlPageRunner(
        cfg={"crawl_policy": {"follow_inline_section_links": False}},
        site_id="demo",
        base_url="https://demo.example.edu/",
        state=None,  # type: ignore[arg-type]
        sections=[],
        list_pages_by_url={},
        known_page_stop=1,
        refresh_frontier=0,
    )

    runner.queue_inline_section(
        {"url": "https://demo.example.edu/extra/list.htm", "label": "Extra"},
        {"section_id": "source"},
        "https://demo.example.edu/1/page.htm",
    )

    assert runner.sections == []
