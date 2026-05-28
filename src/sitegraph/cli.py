from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from .config import load_yaml
from .fetch import fetch_html, fetch_redirect_location
from .extract import (
    extract_all_links,
    extract_detail_page,
    extract_homepage_modules,
    extract_list_items,
    extract_nav_tree_from_homepage,
    extract_pagination_metadata,
    discover_next_url,
)
from .classify import classify_url, same_domain
from .util import now_iso, stable_id, write_json, write_jsonl, normalize_url


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


def _external_category(label: str, url: str, cfg: dict, section: dict | None = None) -> str:
    link_cfg = cfg.get('link_classification', {})
    if label in set(link_cfg.get('external_system_labels', [])):
        return 'external_system_link'
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()
    if any(domain in host for domain in link_cfg.get('external_policy_domains', [])):
        return 'external_policy_link'
    if '/20' in path and path.endswith('/page.htm'):
        return 'cross_domain_article_link'
    if section and 'policy' in set(section.get('business_tags', [])):
        return 'external_policy_link'
    return 'external_link'


def _outcome_priority(outcome: str) -> int:
    if outcome == 'error':
        return 100
    if outcome.startswith('crawled_'):
        return 90
    if outcome == 'attachment_metadata_only':
        return 80
    if outcome.endswith('_recorded') and ('external' in outcome or 'cross_domain' in outcome):
        return 70
    if outcome == 'inline_image_recorded':
        return 60
    if outcome == 'inline_link_recorded':
        return 50
    if outcome == 'homepage_link_recorded':
        return 40
    return 30


def crawl_site(args: argparse.Namespace) -> None:
    cfg = load_yaml(args.config)
    site = cfg['site']
    base_url = normalize_url(site['base_url'])
    site_id = site['id']
    timeout = int(cfg.get('crawl_policy', {}).get('timeout_seconds', 20))
    out_root = Path(args.out or f'data/sites/{site_id}/index')
    if args.dry_run:
        print(json.dumps({'dry_run': True, 'site_id': site_id, 'base_url': base_url, 'sections': len(cfg.get('sections', []))}, ensure_ascii=False, indent=2))
        return

    out_root.mkdir(parents=True, exist_ok=True)
    write_json(out_root / 'site.json', {
        'site_id': site_id,
        'name': site['name'],
        'base_url': base_url,
        'domain': site['domain'],
        'adapter': site['adapter'],
        'created_at': now_iso(),
        'notes': site.get('notes', ''),
    })

    manifest = {
        'site_id': site_id,
        'generated_at': now_iso(),
        'totals': {},
        'outcomes': {},
        'errors': [],
        'quality': {},
        'url_outcomes': {},
    }
    fetch_cache = {}
    detail_records_by_url: dict[str, dict] = {}
    attachments_by_id: dict[str, dict] = {}
    external_links_by_id: dict[str, dict] = {}
    edges_by_id: dict[str, dict] = {}
    list_pages: list[dict] = []

    def fetch(url: str):
        url = normalize_url(url, base_url)
        if url not in fetch_cache:
            res = fetch_html(url, timeout=timeout)
            if res.error and 'timed out' in res.error.lower() and timeout < 60:
                res = fetch_html(url, timeout=60)
            retry_count = 0
            while res.status_code is not None and 500 <= res.status_code < 600 and retry_count < 3:
                retry_count += 1
                res = fetch_html(url, timeout=max(timeout, 60))
            fetch_cache[url] = res
        return fetch_cache[url]

    def add_outcome(url: str, target_type: str | None = None, outcome: str = 'recorded', source_url: str | None = None, label: str | None = None, section_id: str | None = None, status_code: int | None = None, error: str | None = None) -> None:
        url = normalize_url(url, base_url)
        target_type = target_type or classify_url(url, base_url)
        if not url or target_type == 'non_http_link':
            return
        record = manifest['url_outcomes'].setdefault(url, {
            'url': url,
            'target_type': target_type,
            'outcome': outcome,
            'labels': [],
            'sources': [],
            'section_ids': [],
        })
        record['target_type'] = target_type
        if _outcome_priority(outcome) >= _outcome_priority(record.get('outcome', '')):
            record['outcome'] = outcome
        if label and label not in record['labels'][:8]:
            record['labels'].append(label)
        if source_url and source_url not in record['sources'][:8]:
            record['sources'].append(source_url)
        if section_id and section_id not in record['section_ids']:
            record['section_ids'].append(section_id)
        if status_code is not None:
            record['status_code'] = status_code
        if error:
            record['error'] = error

    def add_edges(edges: list[dict]) -> None:
        for edge in edges:
            edges_by_id[edge['edge_id']] = edge

    def add_external(link: dict, source_url: str, section: dict | None = None) -> None:
        if not link.get('url'):
            return
        link_url = normalize_url(link['url'], base_url)
        resolved_url = None
        redirect_status_code = None
        redirect_error = None
        category_url = link_url
        if link.get('target_type') == 'redirect_link':
            redirect = fetch_redirect_location(link_url, timeout=timeout)
            redirect_status_code = redirect.status_code
            redirect_error = redirect.error
            if redirect.location:
                resolved_url = normalize_url(redirect.location, base_url)
                category_url = resolved_url
        category = _external_category(link.get('label') or '', category_url, cfg, section)
        ext_id = stable_id(link_url, resolved_url, link.get('label'), category)
        external_links_by_id[ext_id] = {
            'external_id': ext_id,
            'source_url': source_url,
            'source_section_id': section.get('section_id') if section else None,
            'label': link.get('label') or '',
            'url': resolved_url or link_url,
            'source_redirect_url': link_url if resolved_url else None,
            'redirect_status_code': redirect_status_code,
            'redirect_error': redirect_error,
            'category': category,
            'recorded_at': now_iso(),
        }
        add_outcome(link_url, link.get('target_type'), f'{category}_recorded', source_url, link.get('label'), section.get('section_id') if section else None, redirect_status_code, redirect_error)
        if resolved_url:
            add_outcome(resolved_url, classify_url(resolved_url, base_url), f'{category}_recorded', link_url, link.get('label'), section.get('section_id') if section else None)

    def add_attachment(attachment: dict, source_url: str, section_id: str | None = None) -> None:
        attachments_by_id[attachment['attachment_id']] = attachment
        add_outcome(attachment['url'], 'attachment_file', 'attachment_metadata_only', source_url, attachment.get('name'), section_id)

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

    write_json(out_root / 'nav_tree.json', {'site_id': site_id, 'generated_at': now_iso(), 'nodes': nav_nodes})
    write_json(out_root / 'homepage_modules.json', {'site_id': site_id, 'generated_at': now_iso(), 'modules': homepage_modules})

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

    def crawl_detail(url: str, section: dict, source_url: str, label: str | None = None) -> None:
        url = normalize_url(url, base_url)
        if url in detail_records_by_url:
            add_outcome(url, 'detail_article_page', 'crawled_detail_ok', source_url, label, section['section_id'])
            return
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

    section_index = 0
    while section_index < len(sections_out):
        section = sections_out[section_index]
        section_index += 1
        section_id = section['section_id']
        next_url = normalize_url(section['url'], base_url)
        visited = set()
        page_index = 1
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
            list_pages.append({
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
            })
            add_outcome(next_url, 'section_list_page', 'crawled_list_ok', section.get('url'), section.get('name'), section_id, res.status_code)
            if not items:
                record_section_content_page(next_url, section, section.get('url'), res.text, res.status_code)
            for item in items:
                target_type = item['target_type']
                edge_id = stable_id(next_url, item['url'], item['title'], item.get('position', 0))
                edges_by_id[edge_id] = {
                    'edge_id': edge_id,
                    'from_url': next_url,
                    'to_url': item['url'],
                    'anchor_text': item['title'],
                    'edge_type': 'list_item',
                    'target_type': target_type,
                    'same_domain': same_domain(item['url'], base_url),
                }
                if target_type == 'detail_article_page':
                    crawl_detail(item['url'], section, next_url, item['title'])
                elif target_type == 'attachment_file':
                    add_attachment({
                        'attachment_id': stable_id(next_url, item['url'], item['title']),
                        'parent_url': next_url,
                        'name': item['title'],
                        'url': item['url'],
                        'extension': item['url'].rsplit('.', 1)[-1].lower(),
                        'position': item.get('position', 0),
                    }, next_url, section_id)
                elif target_type in {'external_link', 'redirect_link'}:
                    add_external({'url': item['url'], 'label': item['title'], 'target_type': target_type}, next_url, section)
                else:
                    add_outcome(item['url'], target_type, f'{target_type}_recorded', next_url, item['title'], section_id)
            next_url = discover_next_url(res.text, next_url, base_url)
            page_index += 1

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
            section_id = section['section_id']
            next_url = normalize_url(section['url'], base_url)
            visited = set()
            page_index = 1
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
                list_pages.append({
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
                })
                add_outcome(next_url, 'section_list_page', 'crawled_list_ok', section.get('url'), section.get('name'), section_id, res.status_code)
                if not items:
                    record_section_content_page(next_url, section, section.get('url'), res.text, res.status_code)
                for item in items:
                    target_type = item['target_type']
                    edge_id = stable_id(next_url, item['url'], item['title'], item.get('position', 0))
                    edges_by_id[edge_id] = {
                        'edge_id': edge_id,
                        'from_url': next_url,
                        'to_url': item['url'],
                        'anchor_text': item['title'],
                        'edge_type': 'list_item',
                        'target_type': target_type,
                        'same_domain': same_domain(item['url'], base_url),
                    }
                    if target_type == 'detail_article_page':
                        crawl_detail(item['url'], section, next_url, item['title'])
                    elif target_type == 'attachment_file':
                        add_attachment({
                            'attachment_id': stable_id(next_url, item['url'], item['title']),
                            'parent_url': next_url,
                            'name': item['title'],
                            'url': item['url'],
                            'extension': item['url'].rsplit('.', 1)[-1].lower(),
                            'position': item.get('position', 0),
                        }, next_url, section_id)
                    elif target_type in {'external_link', 'redirect_link'}:
                        add_external({'url': item['url'], 'label': item['title'], 'target_type': target_type}, next_url, section)
                    else:
                        add_outcome(item['url'], target_type, f'{target_type}_recorded', next_url, item['title'], section_id)
                next_url = discover_next_url(res.text, next_url, base_url)
                page_index += 1

    sections_for_output = sections_out + extra_report_sections
    write_json(out_root / 'sections.json', sections_for_output)
    write_jsonl(out_root / 'list_pages.jsonl', list_pages)
    write_jsonl(out_root / 'detail_pages.jsonl', list(detail_records_by_url.values()))
    write_jsonl(out_root / 'attachments.jsonl', list(attachments_by_id.values()))
    write_jsonl(out_root / 'external_links.jsonl', list(external_links_by_id.values()))
    write_jsonl(out_root / 'edges.jsonl', list(edges_by_id.values()))

    outcome_counts = Counter(item['outcome'] for item in manifest['url_outcomes'].values())
    manifest['totals'] = {
        'sections': len(sections_for_output),
        'nav_nodes': len(nav_nodes),
        'homepage_modules': len(homepage_modules),
        'list_pages': len(list_pages),
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
    write_json(out_root / 'manifest.json', manifest)
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
    p.set_defaults(func=crawl_site)
    p = sub.add_parser('discover-homepage')
    p.add_argument('config')
    p.add_argument('--out')
    p.set_defaults(func=discover_homepage)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main()
