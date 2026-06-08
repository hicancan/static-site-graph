from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .util import now_iso, write_json, write_jsonl


VOLATILE_OUTPUT_KEYS = {'created_at', 'generated_at', 'fetched_at', 'recorded_at'}


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def without_volatile(value):
    if isinstance(value, dict):
        return {key: without_volatile(item) for key, item in value.items() if key not in VOLATILE_OUTPUT_KEYS}
    if isinstance(value, list):
        return [without_volatile(item) for item in value]
    return value


def canonical_record_list(records: list[dict]) -> list[dict]:
    normalized = [without_volatile(record) for record in records]
    return sorted(normalized, key=lambda record: json.dumps(record, ensure_ascii=False, sort_keys=True))


def write_json_preserving_volatile(path: Path, payload: object, preserve_volatile: bool) -> None:
    if preserve_volatile and path.exists() and without_volatile(read_json(path, None)) == without_volatile(payload):
        return
    write_json(path, payload)


def write_jsonl_preserving_volatile(path: Path, records: list[dict], preserve_volatile: bool) -> None:
    if preserve_volatile and path.exists() and canonical_record_list(read_jsonl(path)) == canonical_record_list(records):
        return
    write_jsonl(path, records)


def write_site_metadata(out_root: Path, *, site: dict, site_id: str, base_url: str, incremental: bool) -> None:
    write_json_preserving_volatile(out_root / 'site.json', {
        'site_id': site_id,
        'name': site['name'],
        'base_url': base_url,
        'domain': site['domain'],
        'adapter': site['adapter'],
        'created_at': now_iso(),
        'notes': site.get('notes', ''),
    }, incremental)


def write_homepage_outputs(
    out_root: Path,
    *,
    site_id: str,
    nav_nodes: list[dict],
    homepage_modules: list[dict],
    incremental: bool,
) -> None:
    write_json_preserving_volatile(
        out_root / 'nav_tree.json',
        {'site_id': site_id, 'generated_at': now_iso(), 'nodes': nav_nodes},
        incremental,
    )
    write_json_preserving_volatile(
        out_root / 'homepage_modules.json',
        {'site_id': site_id, 'generated_at': now_iso(), 'modules': homepage_modules},
        incremental,
    )


@dataclass
class CrawlOutputPackage:
    cfg: dict
    out_root: Path
    incremental: bool
    manifest: dict
    nav_nodes: list[dict]
    homepage_modules: list[dict]
    sections: list[dict]
    list_pages_by_url: dict[str, dict]
    detail_records_by_url: dict[str, dict]
    attachments_by_id: dict[str, dict]
    external_links_by_id: dict[str, dict]
    edges_by_id: dict[str, dict]


def merge_incremental_sections(out_root: Path, sections: list[dict], incremental: bool) -> list[dict]:
    if not incremental:
        return sections
    sections_by_id = {
        section['section_id']: section
        for section in read_json(out_root / 'sections.json', [])
        if isinstance(section, dict) and section.get('section_id')
    }
    for section in sections:
        sections_by_id[section['section_id']] = section
    return list(sections_by_id.values())


def _is_graph_passive_edge(edge: dict) -> bool:
    return edge.get('target_type') in {'static_asset', 'non_http_link', 'template_placeholder_link'}


def _backfill_inline_image_outcomes(package: CrawlOutputPackage) -> None:
    for page in package.detail_records_by_url.values():
        page_url = page.get('url')
        section_id = page.get('section_id')
        for image in page.get('inline_images') or []:
            url = image.get('url')
            if not url:
                continue
            record = package.manifest['url_outcomes'].setdefault(url, {
                'url': url,
                'target_type': 'static_asset',
                'outcome': 'inline_image_recorded',
                'labels': [],
                'sources': [],
                'section_ids': [],
            })
            record['target_type'] = 'static_asset'
            if record.get('outcome') != 'crawled_detail_ok':
                record['outcome'] = 'inline_image_recorded'
            label = image.get('alt')
            if label and label not in record['labels'][:8]:
                record['labels'].append(label)
            if page_url and page_url not in record['sources'][:8]:
                record['sources'].append(page_url)
            if section_id and section_id not in record['section_ids']:
                record['section_ids'].append(section_id)


def finalize_crawl_output(package: CrawlOutputPackage) -> dict:
    sections_for_output = merge_incremental_sections(package.out_root, package.sections, package.incremental)
    package.edges_by_id = {
        edge_id: edge
        for edge_id, edge in package.edges_by_id.items()
        if not _is_graph_passive_edge(edge)
    }
    _backfill_inline_image_outcomes(package)
    write_json_preserving_volatile(package.out_root / 'sections.json', sections_for_output, package.incremental)
    write_jsonl_preserving_volatile(package.out_root / 'list_pages.jsonl', list(package.list_pages_by_url.values()), package.incremental)
    write_jsonl_preserving_volatile(package.out_root / 'detail_pages.jsonl', list(package.detail_records_by_url.values()), package.incremental)
    write_jsonl_preserving_volatile(package.out_root / 'attachments.jsonl', list(package.attachments_by_id.values()), package.incremental)
    write_jsonl_preserving_volatile(package.out_root / 'external_links.jsonl', list(package.external_links_by_id.values()), package.incremental)
    write_jsonl_preserving_volatile(package.out_root / 'edges.jsonl', list(package.edges_by_id.values()), package.incremental)

    outcome_counts = Counter(item['outcome'] for item in package.manifest['url_outcomes'].values())
    package.manifest['totals'] = {
        'sections': len(sections_for_output),
        'nav_nodes': len(package.nav_nodes),
        'homepage_modules': len(package.homepage_modules),
        'list_pages': len(package.list_pages_by_url),
        'detail_pages': len(package.detail_records_by_url),
        'low_content_detail_pages': sum(
            1 for page in package.detail_records_by_url.values() if page.get('content_status') == 'low_content'
        ),
        'attachments': len(package.attachments_by_id),
        'external_links': len(package.external_links_by_id),
        'edges': len(package.edges_by_id),
        'url_outcomes': len(package.manifest['url_outcomes']),
    }
    package.manifest['outcomes'] = dict(sorted(outcome_counts.items()))
    package.manifest['quality'] = {
        'all_discovered_urls_have_outcomes': True,
        'errors': len(package.manifest['errors']),
        'attachment_policy': package.cfg.get('crawl_policy', {}).get('attachment_policy', 'metadata_only'),
        'external_link_policy': package.cfg.get('crawl_policy', {}).get('external_link_policy', 'record_only'),
    }
    write_json_preserving_volatile(package.out_root / 'manifest.json', package.manifest, package.incremental)
    return package.manifest['totals']
