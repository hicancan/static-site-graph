from __future__ import annotations

from pathlib import Path
from typing import Callable

from .crawl_homepage import crawl_homepage
from .package import read_jsonl
from .crawl_pages import CrawlPageRunner
from .crawl_sections import discover_sections_from_homepage
from .crawl_state import CrawlState
from .fetch import FetchResult, fetch_html
from .model import SiteDefinition, SitePackage
from .util import normalize_url, now_iso


def crawl(
    definition: SiteDefinition,
    *,
    output_path: Path,
    incremental: bool = False,
    incremental_known_page_stop: int = 1,
    incremental_refresh_frontier: int = 3,
    fetch_html_fn: Callable[..., FetchResult] = fetch_html,
) -> SitePackage:
    incremental = incremental and output_path.exists()
    base_url = definition.base_url
    config = definition.config

    detail_pages_by_url = (
        {
            normalize_url(record["url"], base_url): record
            for record in read_jsonl(output_path / "detail_pages.jsonl")
        }
        if incremental
        else {}
    )
    attachments_by_id = (
        {
            record["attachment_id"]: record
            for record in read_jsonl(output_path / "attachments.jsonl")
        }
        if incremental
        else {}
    )
    external_links_by_id = (
        {
            record["external_id"]: record
            for record in read_jsonl(output_path / "external_links.jsonl")
        }
        if incremental
        else {}
    )
    edges_by_id = (
        {
            record["edge_id"]: record
            for record in read_jsonl(output_path / "edges.jsonl")
        }
        if incremental
        else {}
    )
    list_pages_by_url = (
        {
            normalize_url(record["url"], base_url): record
            for record in read_jsonl(output_path / "list_pages.jsonl")
        }
        if incremental
        else {}
    )
    package = SitePackage(
        definition=definition,
        started_at=now_iso(),
        list_pages_by_url=list_pages_by_url,
        detail_pages_by_url=detail_pages_by_url,
        attachments_by_id=attachments_by_id,
        external_links_by_id=external_links_by_id,
        edges_by_id=edges_by_id,
    )
    state = CrawlState(
        cfg=config,
        package=package,
        timeout=int(config.get("crawl_policy", {}).get("timeout_seconds", 20)),
        incremental=incremental,
        initial_known_urls=set(detail_pages_by_url) | set(list_pages_by_url),
        fetch_html_fn=fetch_html_fn,
    )
    state.backfill_external_records_from_known_details()

    homepage = crawl_homepage(
        config,
        base_url=base_url,
        site_id=definition.id,
        state=state,
    )
    package.nav_nodes = homepage.nav_nodes
    package.homepage_modules = homepage.homepage_modules
    package.sections = discover_sections_from_homepage(
        config,
        base_url=base_url,
        site_id=definition.id,
        nav_nodes=package.nav_nodes,
        homepage_modules=package.homepage_modules,
        home_html=homepage.home_html,
    )
    page_runner = CrawlPageRunner(
        cfg=config,
        site_id=definition.id,
        base_url=base_url,
        state=state,
        sections=package.sections,
        list_pages_by_url=package.list_pages_by_url,
        known_page_stop=max(1, incremental_known_page_stop),
        refresh_frontier=max(0, incremental_refresh_frontier),
    )
    package.sections.extend(page_runner.run())
    return package
