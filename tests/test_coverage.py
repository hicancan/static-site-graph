from __future__ import annotations

import argparse
import json
from pathlib import Path

from sitegraph import cli
from sitegraph.fetch import FetchResult


def _write_config(path: Path) -> None:
    path.write_text(
        """
site:
  id: capdemo
  name: Cap Demo
  base_url: https://cap.example.edu/
  domain: cap.example.edu
  adapter: demo
  audit_evidence_ref: reports/audit_evidence.md
  audit_evidence_json_ref: reports/audit_evidence.json
crawl_policy:
  timeout_seconds: 1
  auto_discover_sections_from_homepage: false
  follow_inline_section_links: false
  max_pages_safety: 1
sections:
  - section_id: capdemo_notice
    site_id: capdemo
    name: Notices
    url: https://cap.example.edu/list.htm
    section_type: list
    nav_path: [Notices]
    crawlable: true
    business_tags: [notice]
    pagination:
      type: next_link
      max_pages_safety: 1
""",
        encoding="utf-8",
    )


def test_coverage_report_fails_when_pagination_hits_safety_cap(tmp_path, monkeypatch):
    config = tmp_path / "site.yaml"
    out = tmp_path / "index"
    _write_config(config)

    def fake_fetch(url: str, timeout: int = 20, verify: bool = True) -> FetchResult:
        if url == "https://cap.example.edu/":
            return FetchResult(url=url, final_url=url, status_code=200, text="<html><body>home</body></html>")
        html = """
        <html><body>
          <ul><li><a href="/2026/0702/c1a1/page.htm">Notice</a></li></ul>
          <a href="/list2.htm">下一页</a>
        </body></html>
        """
        return FetchResult(url=url, final_url=url, status_code=200, text=html)

    monkeypatch.setattr(cli, "fetch_html", fake_fetch)
    cli.crawl_site(
        argparse.Namespace(
            config=str(config),
            out=str(out),
            dry_run=False,
            incremental=False,
            incremental_known_page_stop=1,
            incremental_refresh_frontier=0,
        )
    )

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    coverage = json.loads((out / "coverage_report.json").read_text(encoding="utf-8"))
    assert manifest["coverage_status"] == "incomplete"
    assert manifest["pagination_terminal_verified"] is False
    assert coverage["pagination"]["safety_cap_hits"][0]["section_id"] == "capdemo_notice"
    assert any(error["phase"] == "pagination" for error in manifest["errors"])


def test_coverage_report_marks_complete_with_exclusions(tmp_path, monkeypatch):
    config = tmp_path / "site.yaml"
    out = tmp_path / "index"
    _write_config(config)
    text = config.read_text(encoding="utf-8")
    text = text.replace(
        "crawl_policy:\n  timeout_seconds: 1",
        """crawl_policy:
  timeout_seconds: 1
  coverage_exclusions:
    - scope: pagination
      section_id: capdemo_notice
      pattern: list2
      reason: Source blocks historical archive after the first page.
      evidence_url: https://cap.example.edu/list2.htm
      expiry: 2099-01-01
      owner_action: Recheck source archive and remove the exclusion when reachable.""",
    )
    config.write_text(text, encoding="utf-8")

    def fake_fetch(url: str, timeout: int = 20, verify: bool = True) -> FetchResult:
        if url == "https://cap.example.edu/":
            return FetchResult(url=url, final_url=url, status_code=200, text="<html><body>home</body></html>")
        html = """
        <html><body>
          <ul><li><a href="/2026/0702/c1a1/page.htm">Notice</a></li></ul>
          <a href="/list2.htm">下一页</a>
        </body></html>
        """
        return FetchResult(url=url, final_url=url, status_code=200, text=html)

    monkeypatch.setattr(cli, "fetch_html", fake_fetch)
    cli.crawl_site(
        argparse.Namespace(
            config=str(config),
            out=str(out),
            dry_run=False,
            incremental=False,
            incremental_known_page_stop=1,
            incremental_refresh_frontier=0,
        )
    )

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    coverage = json.loads((out / "coverage_report.json").read_text(encoding="utf-8"))
    assert manifest["coverage_status"] == "complete_with_exclusions"
    assert coverage["coverage_status"] == "complete_with_exclusions"
    assert coverage["evidence_source"] == "full_crawl"
    assert coverage["audit_evidence_json_ref"] == "reports/audit_evidence.json"
    assert coverage["urls"]["excluded_url_count"] == 1
