from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from sitegraph import cli
from sitegraph.fetch import FetchResult
from sitegraph.package import validate_site_package


def test_pagination_limit_is_a_visible_diagnostic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = tmp_path / "site.yaml"
    output = tmp_path / "package"
    config.write_text(
        """
site:
  id: demo
  name: Demo
  base_url: https://demo.example.edu/
  domain: demo.example.edu
crawl_policy:
  timeout_seconds: 1
  auto_discover_sections_from_homepage: false
selectors:
  list:
    item_container: "body"
sections:
  - section_id: demo_notice
    name: Notices
    url: https://demo.example.edu/list.htm
    crawlable: true
    pagination:
      type: next_link
      max_pages_safety: 1
""",
        encoding="utf-8",
    )

    def fake_fetch(
        url: str,
        timeout: int = 20,
        verify: bool = True,
    ) -> FetchResult:
        if url == "https://demo.example.edu/":
            html = "<html><body>home</body></html>"
        elif url.endswith("/page.htm"):
            html = (
                "<h1>Notice</h1>"
                "<main>This is a sufficiently useful notice body.</main>"
            )
        else:
            html = (
                '<html><body><a href="/2026/notice/page.htm">Notice</a>'
                '<a href="/list2.htm">下一页</a></body></html>'
            )
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            text=html,
        )

    monkeypatch.setattr(cli, "fetch_html", fake_fetch)
    cli.crawl_site(
        argparse.Namespace(
            config=str(config),
            out=str(output),
            dry_run=False,
            incremental=False,
            incremental_known_page_stop=1,
            incremental_refresh_frontier=0,
        )
    )

    manifest = json.loads(
        (output / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["format"] == "static-site-package-v3"
    assert len(manifest["package_id"]) == 64
    assert manifest["totals"]["detail_pages"] == 1
    assert any(error["phase"] == "pagination" for error in manifest["errors"])
    validate_site_package(output, expected_site_id="demo")
    with (output / "detail_pages.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(ValueError, match="artifact size or hash mismatch"):
        validate_site_package(output, expected_site_id="demo")
