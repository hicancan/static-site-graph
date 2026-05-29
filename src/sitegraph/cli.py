from __future__ import annotations

import argparse
import json
from collections import Counter
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
from .crawl_sections import discover_sections_from_homepage
from .crawl_state import CrawlState
from .config import load_yaml
from .fetch import fetch_html
from .extract import (
    extract_detail_page,
    extract_list_items,
    extract_pagination_metadata,
    discover_next_url,
)
from .classify import same_domain
from .util import now_iso, stable_id, write_json, normalize_url


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
    fetch = state.fetch
    add_outcome = state.add_outcome
    add_edges = state.add_edges
    add_external = state.add_external
    add_attachment = state.add_attachment
    remove_records_from_source = state.remove_records_from_source

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
    queued_section_urls = {section['url'] for section in sections_out}
    extra_report_sections: list[dict] = []

    def queue_inline_section(link: dict, source_section: dict, source_url: str) -> None:
        url = normalize_url(link['url'], base_url)
        if url in queued_section_urls:
            return
        queued_section_urls.add(url)
        sections_out.append({
            'section_id': f'{site_id}_inline_section_{stable_id(url, source_url, length=12)}',
            'site_id': site_id,
            'name': link.get('label') or url,
            'url': url,
            'section_type': 'inline_section_link',
            'nav_path': ['inline', link.get('label') or url],
            'crawlable': True,
            'business_tags': ['inline_section'],
            'pagination': {'type': 'next_link', 'max_pages_safety': 500},
            'source': 'inline_link',
            'source_url': source_url,
        })

    def crawl_detail(url: str, section: dict, source_url: str, label: str | None = None, force_refresh: bool = False) -> None:
        url = normalize_url(url, base_url)
        if url in detail_records_by_url and not force_refresh:
            add_outcome(url, 'detail_article_page', 'crawled_detail_ok', source_url, label, section['section_id'])
            return
        if force_refresh:
            remove_records_from_source(url)
        res = fetch(url)
        if res.error or (res.status_code and res.status_code >= 400):
            err = {'url': url, 'status_code': res.status_code, 'error': res.error or f'HTTP {res.status_code}', 'section_id': section['section_id'], 'phase': 'detail'}
            manifest['errors'].append(err)
            add_outcome(url, 'detail_article_page', 'error', source_url, label, section['section_id'], res.status_code, err['error'])
            return
        page, atts, edges = extract_detail_page(res.text, url, base_url, site_id, section['section_id'])
        detail_records_by_url[url] = page
        add_edges(edges)
        add_outcome(url, 'detail_article_page', 'crawled_detail_ok', source_url, label, section['section_id'], res.status_code)
        for attachment in atts:
            add_attachment(attachment, url, section['section_id'])
        for inline_link in page.get('inline_links', []):
            if inline_link['target_type'] in {'external_link', 'redirect_link'}:
                add_external(inline_link, url, section)
            elif inline_link['target_type'] == 'section_list_page' and same_domain(inline_link['url'], base_url):
                queue_inline_section(inline_link, section, url)
                add_outcome(inline_link['url'], inline_link['target_type'], 'inline_link_recorded', url, inline_link.get('label'), section['section_id'])
            else:
                add_outcome(inline_link['url'], inline_link['target_type'], 'inline_link_recorded', url, inline_link.get('label'), section['section_id'])
        for image in page.get('inline_images', []):
            add_outcome(image['url'], 'static_asset', 'inline_image_recorded', url, image.get('alt'), section['section_id'])

    def record_section_content_page(url: str, section: dict, source_url: str, html: str, status_code: int | None = None) -> None:
        url = normalize_url(url, base_url)
        if url in detail_records_by_url:
            page = detail_records_by_url[url]
            for inline_link in page.get('inline_links', []):
                if inline_link['target_type'] in {'external_link', 'redirect_link'}:
                    add_external(inline_link, url, section)
                elif inline_link['target_type'] == 'section_list_page' and same_domain(inline_link['url'], base_url):
                    queue_inline_section(inline_link, section, url)
                    add_outcome(inline_link['url'], inline_link['target_type'], 'inline_link_recorded', url, inline_link.get('label'), section['section_id'])
                else:
                    add_outcome(inline_link['url'], inline_link['target_type'], 'inline_link_recorded', url, inline_link.get('label'), section['section_id'])
            for image in page.get('inline_images', []):
                add_outcome(image['url'], 'static_asset', 'inline_image_recorded', url, image.get('alt'), section['section_id'])
            return
        page, atts, edges = extract_detail_page(html, url, base_url, site_id, section['section_id'])
        if not (page.get('title') or page.get('content_text') or atts or page.get('inline_links') or page.get('inline_images')):
            return
        page['page_type'] = 'section_content_page'
        page['source_page_type'] = 'section_list_page'
        page['source_status_code'] = status_code
        detail_records_by_url[url] = page
        add_edges(edges)
        for attachment in atts:
            add_attachment(attachment, url, section['section_id'])
        for inline_link in page.get('inline_links', []):
            if inline_link['target_type'] in {'external_link', 'redirect_link'}:
                add_external(inline_link, url, section)
            elif inline_link['target_type'] == 'section_list_page' and same_domain(inline_link['url'], base_url):
                queue_inline_section(inline_link, section, url)
                add_outcome(inline_link['url'], inline_link['target_type'], 'inline_link_recorded', url, inline_link.get('label'), section['section_id'])
            else:
                add_outcome(inline_link['url'], inline_link['target_type'], 'inline_link_recorded', url, inline_link.get('label'), section['section_id'])
        for image in page.get('inline_images', []):
            add_outcome(image['url'], 'static_asset', 'inline_image_recorded', url, image.get('alt'), section['section_id'])

    def crawl_list_section(section: dict) -> None:
        section_id = section['section_id']
        next_url = normalize_url(section['url'], base_url)
        visited = set()
        page_index = 1
        known_pages_in_a_row = 0
        frontier_remaining = refresh_frontier if incremental else 0
        max_pages = int(section.get('pagination', {}).get('max_pages_safety', cfg.get('crawl_policy', {}).get('max_pages_safety', 20)))
        while next_url and next_url not in visited and page_index <= max_pages:
            visited.add(next_url)
            res = fetch(next_url)
            if res.error or (res.status_code and res.status_code >= 400):
                err = {'url': next_url, 'status_code': res.status_code, 'error': res.error or f'HTTP {res.status_code}', 'section_id': section_id, 'phase': 'list'}
                manifest['errors'].append(err)
                add_outcome(next_url, 'section_list_page', 'error', section.get('url'), section.get('name'), section_id, res.status_code, err['error'])
                break
            item_container_selector = section.get('item_container_selector') or cfg.get('selectors', {}).get('list', {}).get('item_container')
            items = extract_list_items(res.text, next_url, base_url, item_container_selector)
            item_counts = Counter(item['target_type'] for item in items)
            page_has_new = any(normalize_url(item['url'], base_url) not in initial_known_urls for item in items)
            remove_records_from_source(next_url)
            list_pages_by_url[next_url] = {
                'page_id': stable_id(site_id, next_url),
                'site_id': site_id,
                'section_id': section_id,
                'url': next_url,
                'page_type': 'section_list_page',
                'status': 'ok',
                'page_index': page_index,
                'item_count': len(items),
                'target_type_counts': dict(item_counts),
                'pagination': extract_pagination_metadata(res.text),
                'fetched_at': now_iso(),
            }
            add_outcome(next_url, 'section_list_page', 'crawled_list_ok', section.get('url'), section.get('name'), section_id, res.status_code)
            if not items:
                record_section_content_page(next_url, section, section.get('url'), res.text, res.status_code)
            for item in items:
                target_type = item['target_type']
                item_url = normalize_url(item['url'], base_url)
                edge_id = stable_id(next_url, item_url, item['title'], item.get('position', 0))
                edges_by_id[edge_id] = {
                    'edge_id': edge_id,
                    'from_url': next_url,
                    'to_url': item_url,
                    'anchor_text': item['title'],
                    'edge_type': 'list_item',
                    'target_type': target_type,
                    'same_domain': same_domain(item_url, base_url),
                }
                if target_type == 'detail_article_page':
                    old_page = detail_records_by_url.get(item_url)
                    title_changed = bool(old_page and item.get('title') and old_page.get('title') and item['title'] != old_page.get('title'))
                    refresh_recent = incremental and item_url in initial_known_urls and frontier_remaining > 0
                    if refresh_recent:
                        frontier_remaining -= 1
                    force_refresh = (
                        not incremental
                        or item_url not in initial_known_urls
                        or item_url not in detail_records_by_url
                        or title_changed
                        or refresh_recent
                    )
                    crawl_detail(item_url, section, next_url, item['title'], force_refresh=force_refresh)
                elif target_type == 'attachment_file':
                    add_attachment({
                        'attachment_id': stable_id(next_url, item_url, item['title']),
                        'parent_url': next_url,
                        'name': item['title'],
                        'url': item_url,
                        'extension': item_url.rsplit('.', 1)[-1].lower(),
                        'position': item.get('position', 0),
                    }, next_url, section_id)
                elif target_type in {'external_link', 'redirect_link'}:
                    add_external({'url': item_url, 'label': item['title'], 'target_type': target_type}, next_url, section)
                else:
                    add_outcome(item_url, target_type, f'{target_type}_recorded', next_url, item['title'], section_id)
            next_url = discover_next_url(res.text, next_url, base_url)
            if incremental:
                known_pages_in_a_row = 0 if page_has_new else known_pages_in_a_row + 1
                if known_pages_in_a_row >= known_page_stop:
                    break
            page_index += 1

    section_index = 0
    while section_index < len(sections_out):
        section = sections_out[section_index]
        section_index += 1
        crawl_list_section(section)

    direct_detail_section = {
        'section_id': f'{site_id}_direct_detail_links',
        'site_id': site_id,
        'name': 'Direct detail links',
        'url': base_url,
        'section_type': 'direct_detail_links',
        'nav_path': ['首页', '直接详情链接'],
        'crawlable': True,
        'business_tags': ['direct_detail'],
        'pagination': {'type': 'none'},
        'source': 'manifest_backfill',
    }
    direct_section_recorded = False
    seen_direct_details: set[str] = set()
    while True:
        direct_detail_urls = [
            (url, record)
            for url, record in list(manifest['url_outcomes'].items())
            if record.get('target_type') == 'detail_article_page'
            and record.get('outcome') not in {'crawled_detail_ok', 'error'}
            and url not in seen_direct_details
        ]
        if not direct_detail_urls:
            break
        if not direct_section_recorded:
            extra_report_sections.append(direct_detail_section)
            direct_section_recorded = True
        for url, record in direct_detail_urls:
            seen_direct_details.add(url)
            crawl_detail(url, direct_detail_section, (record.get('sources') or [base_url])[0], (record.get('labels') or [None])[0])
        while section_index < len(sections_out):
            section = sections_out[section_index]
            section_index += 1
            crawl_list_section(section)

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
