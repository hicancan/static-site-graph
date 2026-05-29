from __future__ import annotations

import argparse
import json
from pathlib import Path

from .crawl_output import (
    CrawlOutputPackage,
    finalize_crawl_output,
    read_json,
    read_jsonl,
    write_homepage_outputs,
    write_site_metadata,
)
from .crawl_homepage import crawl_homepage
from .crawl_pages import CrawlPageRunner
from .crawl_sections import discover_sections_from_homepage
from .crawl_state import CrawlState
from .config import load_yaml
from .fetch import fetch_html
from .extract import extract_nav_tree_from_homepage
from .util import now_iso, write_json, normalize_url


def validate_config(args: argparse.Namespace) -> None:
    cfg = load_yaml(args.config)
    for key in ['site']:
        if key not in cfg:
            raise SystemExit(f'missing required key: {key}')
    site = cfg['site']
    for key in ['id', 'name', 'base_url', 'domain', 'adapter']:
        if key not in site:
            raise SystemExit(f'missing site.{key}')
    print(f"OK config: {site['id']} {site['base_url']}")


def _dedupe_records(records: list[dict], *keys: str) -> list[dict]:
    seen = set()
    out = []
    for record in records:
        key = tuple(record.get(item) for item in keys)
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    return out


def crawl_site(args: argparse.Namespace) -> None:
    cfg = load_yaml(args.config)
    site = cfg['site']
    base_url = normalize_url(site['base_url'])
    site_id = site['id']
    timeout = int(cfg.get('crawl_policy', {}).get('timeout_seconds', 20))
    out_root = Path(args.out or f'data/sites/{site_id}/index')
    incremental = bool(getattr(args, 'incremental', False)) and out_root.exists()
    known_page_stop = max(1, int(getattr(args, 'incremental_known_page_stop', 1)))
    refresh_frontier = max(0, int(getattr(args, 'incremental_refresh_frontier', 3)))
    if args.dry_run:
        print(json.dumps({
            'dry_run': True,
            'site_id': site_id,
            'base_url': base_url,
            'sections': len(cfg.get('sections', [])),
            'incremental': bool(getattr(args, 'incremental', False)),
        }, ensure_ascii=False, indent=2))
        return

    out_root.mkdir(parents=True, exist_ok=True)
    old_manifest = read_json(out_root / 'manifest.json', {}) if incremental else {}

    write_site_metadata(out_root, site=site, site_id=site_id, base_url=base_url, incremental=incremental)

    manifest = {
        'site_id': site_id,
        'generated_at': now_iso(),
        'totals': {},
        'outcomes': {},
        'errors': [],
        'quality': {},
        'url_outcomes': dict(old_manifest.get('url_outcomes', {})) if incremental else {},
    }
    initial_known_urls = set(manifest['url_outcomes'])
    detail_records_by_url: dict[str, dict] = {
        normalize_url(record['url'], base_url): record
        for record in read_jsonl(out_root / 'detail_pages.jsonl')
    } if incremental else {}
    attachments_by_id: dict[str, dict] = {
        record['attachment_id']: record
        for record in read_jsonl(out_root / 'attachments.jsonl')
    } if incremental else {}
    external_links_by_id: dict[str, dict] = {
        record['external_id']: record
        for record in read_jsonl(out_root / 'external_links.jsonl')
    } if incremental else {}
    edges_by_id: dict[str, dict] = {
        record['edge_id']: record
        for record in read_jsonl(out_root / 'edges.jsonl')
    } if incremental else {}
    list_pages_by_url: dict[str, dict] = {
        normalize_url(record['url'], base_url): record
        for record in read_jsonl(out_root / 'list_pages.jsonl')
    } if incremental else {}

    state = CrawlState(
        cfg=cfg,
        base_url=base_url,
        timeout=timeout,
        incremental=incremental,
        manifest=manifest,
        initial_known_urls=initial_known_urls,
        detail_records_by_url=detail_records_by_url,
        attachments_by_id=attachments_by_id,
        external_links_by_id=external_links_by_id,
        edges_by_id=edges_by_id,
        fetch_html_fn=fetch_html,
    )

    state.backfill_external_records_from_known_details()

    homepage = crawl_homepage(cfg, base_url=base_url, site_id=site_id, state=state)
    home_html = homepage.home_html
    nav_nodes = homepage.nav_nodes
    homepage_modules = homepage.homepage_modules

    write_homepage_outputs(
        out_root,
        site_id=site_id,
        nav_nodes=nav_nodes,
        homepage_modules=homepage_modules,
        incremental=incremental,
    )

    sections_out = discover_sections_from_homepage(
        cfg,
        base_url=base_url,
        site_id=site_id,
        nav_nodes=nav_nodes,
        homepage_modules=homepage_modules,
        home_html=home_html,
    )
    page_runner = CrawlPageRunner(
        cfg=cfg,
        site_id=site_id,
        base_url=base_url,
        state=state,
        sections=sections_out,
        list_pages_by_url=list_pages_by_url,
        known_page_stop=known_page_stop,
        refresh_frontier=refresh_frontier,
    )
    extra_report_sections = page_runner.run()

    totals = finalize_crawl_output(CrawlOutputPackage(
        cfg=cfg,
        out_root=out_root,
        incremental=incremental,
        manifest=manifest,
        nav_nodes=nav_nodes,
        homepage_modules=homepage_modules,
        sections=sections_out + extra_report_sections,
        list_pages_by_url=list_pages_by_url,
        detail_records_by_url=detail_records_by_url,
        attachments_by_id=attachments_by_id,
        external_links_by_id=external_links_by_id,
        edges_by_id=edges_by_id,
    ))
    print(json.dumps(totals, ensure_ascii=False, indent=2))


def discover_homepage(args: argparse.Namespace) -> None:
    cfg = load_yaml(args.config)
    site = cfg['site']
    base_url = normalize_url(site['base_url'])
    res = fetch_html(base_url)
    if res.error or (res.status_code and res.status_code >= 400):
        raise SystemExit(res.error or f'HTTP {res.status_code}')
    nodes = extract_nav_tree_from_homepage(res.text, base_url, base_url, site['id'])
    out = Path(args.out or f'data/sites/{site["id"]}/index/nav_tree.json')
    write_json(out, {'site_id': site['id'], 'generated_at': now_iso(), 'nodes': nodes})
    print(f'wrote {out} nodes={len(nodes)}')


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog='sitegraph')
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('validate-config')
    p.add_argument('config')
    p.set_defaults(func=validate_config)
    p = sub.add_parser('crawl-site')
    p.add_argument('config')
    p.add_argument('--out')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--incremental', action='store_true', help='reuse an existing output package and crawl only front matter/new URLs')
    p.add_argument('--incremental-known-page-stop', type=int, default=1, help='stop a section after this many consecutive already-known list pages')
    p.add_argument('--incremental-refresh-frontier', type=int, default=3, help='refresh this many already-known detail pages per section')
    p.set_defaults(func=crawl_site)
    p = sub.add_parser('discover-homepage')
    p.add_argument('config')
    p.add_argument('--out')
    p.set_defaults(func=discover_homepage)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main()
