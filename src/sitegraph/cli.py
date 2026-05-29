from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .crawl_state import CrawlState
from .config import load_yaml
from .fetch import fetch_html
from .extract import (
    extract_all_links,
    extract_detail_page,
    extract_homepage_modules,
    extract_list_items,
    extract_nav_tree_from_homepage,
    extract_pagination_metadata,
    discover_next_url,
)
from .classify import same_domain
from .util import now_iso, stable_id, write_json, write_jsonl, normalize_url


VOLATILE_OUTPUT_KEYS = {'created_at', 'generated_at', 'fetched_at', 'recorded_at'}


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _without_volatile(value):
    if isinstance(value, dict):
        return {key: _without_volatile(item) for key, item in value.items() if key not in VOLATILE_OUTPUT_KEYS}
    if isinstance(value, list):
        return [_without_volatile(item) for item in value]
    return value


def _canonical_record_list(records: list[dict]) -> list[dict]:
    normalized = [_without_volatile(record) for record in records]
    return sorted(normalized, key=lambda record: json.dumps(record, ensure_ascii=False, sort_keys=True))


def _write_json_preserving_volatile(path: Path, payload: object, preserve_volatile: bool) -> None:
    if preserve_volatile and path.exists() and _without_volatile(_read_json(path, None)) == _without_volatile(payload):
        return
    write_json(path, payload)


def _write_jsonl_preserving_volatile(path: Path, records: list[dict], preserve_volatile: bool) -> None:
    if preserve_volatile and path.exists() and _canonical_record_list(_read_jsonl(path)) == _canonical_record_list(records):
        return
    write_jsonl(path, records)


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


def _nav_path(node: dict, by_id: dict[str, dict]) -> list[str]:
    labels = [node['label']]
    parent_id = node.get('parent_id')
    while parent_id and parent_id in by_id:
        parent = by_id[parent_id]
        labels.append(parent['label'])
        parent_id = parent.get('parent_id')
    return list(reversed(labels))


def _section_from_node(site_id: str, node: dict, nav_path: list[str]) -> dict:
    return {
        'section_id': f'{site_id}_nav_{stable_id(node["url"], *nav_path, length=12)}',
        'site_id': site_id,
        'name': nav_path[-1],
        'url': node['url'],
        'section_type': 'nav_section',
        'nav_path': nav_path,
        'crawlable': True,
        'business_tags': ['nav'],
        'pagination': {'type': 'next_link', 'max_pages_safety': 500},
        'source': 'homepage_nav',
    }


def _section_from_module(site_id: str, module: dict) -> dict | None:
    if not module.get('list_url'):
        return None
    return {
        'section_id': f'{site_id}_home_module_{stable_id(module["name"], module["list_url"], length=12)}',
        'site_id': site_id,
        'name': module['name'],
        'url': module['list_url'],
        'section_type': 'homepage_module',
        'nav_path': ['首页', module['name']],
        'crawlable': True,
        'business_tags': ['homepage_module'],
        'pagination': {'type': 'next_link', 'max_pages_safety': 500},
        'source': 'homepage_module',
        'homepage_url': module['url'],
        'container_selector': module.get('container_selector'),
    }


def _configured_homepage_modules(cfg: dict, base_url: str, site_id: str) -> list[dict]:
    modules = []
    for idx, item in enumerate(cfg.get('homepage_modules', [])):
        name = item.get('name')
        list_url = item.get('list_url') or item.get('url')
        if not name or not list_url:
            continue
        list_url = normalize_url(list_url, base_url)
        modules.append({
            'module_id': item.get('module_id') or f'{site_id}_home_module_{stable_id(name, list_url, length=12)}',
            'site_id': site_id,
            'name': name,
            'url': normalize_url(item.get('homepage_url') or base_url, base_url),
            'list_url': list_url,
            'container_selector': item.get('container_selector'),
            'link_count': item.get('link_count'),
            'position': item.get('position', idx),
            'source': item.get('source', 'config'),
            'notes': item.get('notes'),
        })
    return modules


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
    old_manifest = _read_json(out_root / 'manifest.json', {}) if incremental else {}

    _write_json_preserving_volatile(out_root / 'site.json', {
        'site_id': site_id,
        'name': site['name'],
        'base_url': base_url,
        'domain': site['domain'],
        'adapter': site['adapter'],
        'created_at': now_iso(),
        'notes': site.get('notes', ''),
    }, incremental)

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
        for record in _read_jsonl(out_root / 'detail_pages.jsonl')
    } if incremental else {}
    attachments_by_id: dict[str, dict] = {
        record['attachment_id']: record
        for record in _read_jsonl(out_root / 'attachments.jsonl')
    } if incremental else {}
    external_links_by_id: dict[str, dict] = {
        record['external_id']: record
        for record in _read_jsonl(out_root / 'external_links.jsonl')
    } if incremental else {}
    edges_by_id: dict[str, dict] = {
        record['edge_id']: record
        for record in _read_jsonl(out_root / 'edges.jsonl')
    } if incremental else {}
    list_pages_by_url: dict[str, dict] = {
        normalize_url(record['url'], base_url): record
        for record in _read_jsonl(out_root / 'list_pages.jsonl')
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

    home_res = fetch(base_url)
    if home_res.error or (home_res.status_code and home_res.status_code >= 400):
        manifest['errors'].append({'url': base_url, 'status_code': home_res.status_code, 'error': home_res.error or f'HTTP {home_res.status_code}', 'phase': 'homepage'})
        add_outcome(base_url, 'homepage', 'error', status_code=home_res.status_code, error=home_res.error)
        home_html = ''
        nav_nodes = []
        homepage_modules = []
    else:
        add_outcome(base_url, 'homepage', 'crawled_homepage_ok', status_code=home_res.status_code)
        home_html = home_res.text
        nav_nodes = extract_nav_tree_from_homepage(home_html, base_url, base_url, site_id)
        homepage_cfg = cfg.get('selectors', {}).get('homepage', {})
        extracted_modules = extract_homepage_modules(
            home_html,
            base_url,
            base_url,
            site_id,
            module_labels=homepage_cfg.get('module_labels'),
            container_selectors=homepage_cfg.get('module_container_selectors'),
        )
        homepage_modules = []
        seen_modules = set()
        for module in _configured_homepage_modules(cfg, base_url, site_id) + extracted_modules:
            key = (module.get('name'), module.get('list_url'))
            if key in seen_modules:
                continue
            seen_modules.add(key)
            homepage_modules.append(module)
        remove_records_from_source(base_url)
        home_links, home_edges = extract_all_links(home_html, base_url, base_url)
        add_edges(home_edges)
        for link in home_links:
            if link['target_type'] in {'external_link', 'redirect_link'}:
                add_external(link, base_url)
            elif link['target_type'] == 'attachment_file':
                add_attachment({
                    'attachment_id': stable_id(base_url, link['url'], link['label']),
                    'parent_url': base_url,
                    'name': link['label'] or link['url'].rsplit('/', 1)[-1],
                    'url': link['url'],
                    'extension': link['url'].rsplit('.', 1)[-1].lower(),
                    'position': link.get('position', 0),
                }, base_url)
            else:
                add_outcome(link['url'], link['target_type'], 'homepage_link_recorded', base_url, link.get('label'))

    _write_json_preserving_volatile(out_root / 'nav_tree.json', {'site_id': site_id, 'generated_at': now_iso(), 'nodes': nav_nodes}, incremental)
    _write_json_preserving_volatile(out_root / 'homepage_modules.json', {'site_id': site_id, 'generated_at': now_iso(), 'modules': homepage_modules}, incremental)

    sections_by_url: dict[str, dict] = {}

    def add_section(section: dict) -> None:
        if section.get('crawlable', True) is False:
            return
        section = dict(section)
        section.setdefault('site_id', site_id)
        section['url'] = normalize_url(section['url'], base_url)
        section.setdefault('pagination', {'type': 'next_link', 'max_pages_safety': 500})
        existing = sections_by_url.get(section['url'])
        if not existing or existing.get('source') == 'homepage_nav':
            sections_by_url[section['url']] = section

    for section in cfg.get('sections', []):
        add_section(section)

    if cfg.get('crawl_policy', {}).get('auto_discover_sections_from_homepage', True):
        nodes_by_id = {node['node_id']: node for node in nav_nodes}
        for node in nav_nodes:
            if node['target_type'] == 'section_list_page':
                path = _nav_path(node, nodes_by_id)
                add_section(_section_from_node(site_id, node, path))
        for module in homepage_modules:
            section = _section_from_module(site_id, module)
            if section:
                add_section(section)
        if home_html:
            home_links, _ = extract_all_links(home_html, base_url, base_url)
            for link in home_links:
                if link['target_type'] == 'section_list_page' and same_domain(link['url'], base_url):
                    add_section({
                        'section_id': f'{site_id}_home_link_{stable_id(link["url"], link["label"], length=12)}',
                        'site_id': site_id,
                        'name': link['label'] or link['url'],
                        'url': link['url'],
                        'section_type': 'homepage_link_section',
                        'nav_path': ['首页', link['label'] or link['url']],
                        'crawlable': True,
                        'business_tags': ['homepage_link'],
                        'pagination': {'type': 'next_link', 'max_pages_safety': 500},
                        'source': 'homepage_link',
                    })

    sections_out = list(sections_by_url.values())
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

    sections_for_output = sections_out + extra_report_sections
    if incremental:
        sections_by_id = {
            section['section_id']: section
            for section in _read_json(out_root / 'sections.json', [])
            if isinstance(section, dict) and section.get('section_id')
        }
        for section in sections_for_output:
            sections_by_id[section['section_id']] = section
        sections_for_output = list(sections_by_id.values())
    _write_json_preserving_volatile(out_root / 'sections.json', sections_for_output, incremental)
    _write_jsonl_preserving_volatile(out_root / 'list_pages.jsonl', list(list_pages_by_url.values()), incremental)
    _write_jsonl_preserving_volatile(out_root / 'detail_pages.jsonl', list(detail_records_by_url.values()), incremental)
    _write_jsonl_preserving_volatile(out_root / 'attachments.jsonl', list(attachments_by_id.values()), incremental)
    _write_jsonl_preserving_volatile(out_root / 'external_links.jsonl', list(external_links_by_id.values()), incremental)
    _write_jsonl_preserving_volatile(out_root / 'edges.jsonl', list(edges_by_id.values()), incremental)

    outcome_counts = Counter(item['outcome'] for item in manifest['url_outcomes'].values())
    manifest['totals'] = {
        'sections': len(sections_for_output),
        'nav_nodes': len(nav_nodes),
        'homepage_modules': len(homepage_modules),
        'list_pages': len(list_pages_by_url),
        'detail_pages': len(detail_records_by_url),
        'low_content_detail_pages': sum(1 for page in detail_records_by_url.values() if page.get('content_status') == 'low_content'),
        'attachments': len(attachments_by_id),
        'external_links': len(external_links_by_id),
        'edges': len(edges_by_id),
        'url_outcomes': len(manifest['url_outcomes']),
    }
    manifest['outcomes'] = dict(sorted(outcome_counts.items()))
    manifest['quality'] = {
        'all_discovered_urls_have_outcomes': True,
        'errors': len(manifest['errors']),
        'attachment_policy': cfg.get('crawl_policy', {}).get('attachment_policy', 'metadata_only'),
        'external_link_policy': cfg.get('crawl_policy', {}).get('external_link_policy', 'record_only'),
    }
    _write_json_preserving_volatile(out_root / 'manifest.json', manifest, incremental)
    print(json.dumps(manifest['totals'], ensure_ascii=False, indent=2))


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
