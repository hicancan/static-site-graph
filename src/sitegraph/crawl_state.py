from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

from .classify import classify_url
from .fetch import FetchResult, fetch_html, fetch_redirect_location
from .util import normalize_url, now_iso, stable_id


def external_category(label: str, url: str, cfg: dict, section: dict | None = None) -> str:
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


def outcome_priority(outcome: str) -> int:
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


@dataclass
class CrawlState:
    cfg: dict
    base_url: str
    timeout: int
    incremental: bool
    manifest: dict
    initial_known_urls: set[str]
    detail_records_by_url: dict[str, dict]
    attachments_by_id: dict[str, dict]
    external_links_by_id: dict[str, dict]
    edges_by_id: dict[str, dict]
    fetch_html_fn: Callable[..., FetchResult] = fetch_html
    fetch_redirect_location_fn: Callable[..., object] = fetch_redirect_location
    fetch_cache: dict[str, FetchResult] = field(default_factory=dict)

    def fetch(self, url: str) -> FetchResult:
        url = normalize_url(url, self.base_url)
        if url not in self.fetch_cache:
            res = self.fetch_html_fn(url, timeout=self.timeout)
            if res.error and 'timed out' in res.error.lower() and self.timeout < 60:
                res = self.fetch_html_fn(url, timeout=60)
            retry_count = 0
            while res.status_code is not None and 500 <= res.status_code < 600 and retry_count < 3:
                retry_count += 1
                res = self.fetch_html_fn(url, timeout=max(self.timeout, 60))
            self.fetch_cache[url] = res
        return self.fetch_cache[url]

    def add_outcome(
        self,
        url: str,
        target_type: str | None = None,
        outcome: str = 'recorded',
        source_url: str | None = None,
        label: str | None = None,
        section_id: str | None = None,
        status_code: int | None = None,
        error: str | None = None,
    ) -> None:
        url = normalize_url(url, self.base_url)
        target_type = target_type or classify_url(url, self.base_url)
        if not url or target_type == 'non_http_link':
            return
        record = self.manifest['url_outcomes'].setdefault(url, {
            'url': url,
            'target_type': target_type,
            'outcome': outcome,
            'labels': [],
            'sources': [],
            'section_ids': [],
        })
        record['target_type'] = target_type
        if outcome_priority(outcome) >= outcome_priority(record.get('outcome', '')):
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

    def add_edges(self, edges: list[dict]) -> None:
        for edge in edges:
            self.edges_by_id[edge['edge_id']] = edge

    def add_external(self, link: dict, source_url: str, section: dict | None = None) -> None:
        if not link.get('url'):
            return
        link_url = normalize_url(link['url'], self.base_url)
        resolved_url = None
        redirect_status_code = None
        redirect_error = None
        category_url = link_url
        if link.get('target_type') == 'redirect_link':
            redirect = self.fetch_redirect_location_fn(link_url, timeout=self.timeout)
            redirect_status_code = redirect.status_code
            redirect_error = redirect.error
            if redirect.location:
                resolved_url = normalize_url(redirect.location, self.base_url)
                category_url = resolved_url
        category = external_category(link.get('label') or '', category_url, self.cfg, section)
        ext_id = stable_id(link_url, resolved_url, link.get('label'), category)
        self.external_links_by_id[ext_id] = {
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
        section_id = section.get('section_id') if section else None
        self.add_outcome(link_url, link.get('target_type'), f'{category}_recorded', source_url, link.get('label'), section_id, redirect_status_code, redirect_error)
        if resolved_url:
            self.add_outcome(resolved_url, classify_url(resolved_url, self.base_url), f'{category}_recorded', link_url, link.get('label'), section_id)

    def add_attachment(self, attachment: dict, source_url: str, section_id: str | None = None) -> None:
        self.attachments_by_id[attachment['attachment_id']] = attachment
        self.add_outcome(attachment['url'], 'attachment_file', 'attachment_metadata_only', source_url, attachment.get('name'), section_id)

    def backfill_external_records_from_known_details(self) -> None:
        if not self.incremental:
            return
        for page in list(self.detail_records_by_url.values()):
            source_url = page.get('url')
            if not source_url:
                continue
            section = {'section_id': page.get('section_id'), 'business_tags': []}
            for link in page.get('inline_links') or []:
                if link.get('target_type') in {'external_link', 'redirect_link'}:
                    self.add_external(link, source_url, section)

    def remove_records_from_source(self, source_url: str) -> None:
        source_url = normalize_url(source_url, self.base_url)
        for attachment_id, attachment in list(self.attachments_by_id.items()):
            if normalize_url(attachment.get('parent_url', ''), self.base_url) == source_url:
                self.attachments_by_id.pop(attachment_id, None)
        for external_id, external in list(self.external_links_by_id.items()):
            if normalize_url(external.get('source_url', ''), self.base_url) == source_url:
                self.external_links_by_id.pop(external_id, None)
        for edge_id, edge in list(self.edges_by_id.items()):
            if normalize_url(edge.get('from_url', ''), self.base_url) == source_url:
                self.edges_by_id.pop(edge_id, None)
