from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .classify import same_domain
from .crawl_state import CrawlState
from .extract import (
    discover_next_url,
    extract_detail_page,
    extract_list_items,
    extract_pagination_metadata,
)
from .coverage import record_pagination_evidence
from .util import normalize_url, now_iso, stable_id


@dataclass
class CrawlPageRunner:
    cfg: dict
    site_id: str
    base_url: str
    state: CrawlState
    sections: list[dict]
    list_pages_by_url: dict[str, dict]
    known_page_stop: int
    refresh_frontier: int
    queued_section_urls: set[str] = field(init=False)
    extra_report_sections: list[dict] = field(default_factory=list)
    seen_direct_details: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.queued_section_urls = {section['url'] for section in self.sections}

    def run(self) -> list[dict]:
        section_index = self._crawl_pending_sections(0)
        self._crawl_direct_detail_backfill(section_index)
        return self.extra_report_sections

    def _crawl_pending_sections(self, section_index: int) -> int:
        while section_index < len(self.sections):
            section = self.sections[section_index]
            section_index += 1
            self.crawl_list_section(section)
        return section_index

    def queue_inline_section(self, link: dict, source_section: dict, source_url: str) -> None:
        if self.cfg.get('crawl_policy', {}).get('follow_inline_section_links', True) is not True:
            return
        url = normalize_url(link['url'], self.base_url)
        if url in self.queued_section_urls:
            return
        self.queued_section_urls.add(url)
        self.sections.append({
            'section_id': f'{self.site_id}_inline_section_{stable_id(url, source_url, length=12)}',
            'site_id': self.site_id,
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

    def _record_inline_artifacts(self, page: dict, url: str, section: dict) -> None:
        for inline_link in page.get('inline_links', []):
            if inline_link['target_type'] in {'external_link', 'redirect_link'}:
                self.state.add_external(inline_link, url, section)
            elif inline_link['target_type'] == 'section_list_page' and same_domain(inline_link['url'], self.base_url):
                self.queue_inline_section(inline_link, section, url)
                self.state.add_outcome(
                    inline_link['url'],
                    inline_link['target_type'],
                    'inline_link_recorded',
                    url,
                    inline_link.get('label'),
                    section['section_id'],
                )
            else:
                self.state.add_outcome(
                    inline_link['url'],
                    inline_link['target_type'],
                    'inline_link_recorded',
                    url,
                    inline_link.get('label'),
                    section['section_id'],
                )
        for image in page.get('inline_images', []):
            self.state.add_outcome(image['url'], 'static_asset', 'inline_image_recorded', url, image.get('alt'), section['section_id'])

    def crawl_detail(
        self,
        url: str,
        section: dict,
        source_url: str,
        label: str | None = None,
        force_refresh: bool = False,
    ) -> None:
        url = normalize_url(url, self.base_url)
        if url in self.state.detail_records_by_url and not force_refresh:
            self.state.add_outcome(url, 'detail_article_page', 'crawled_detail_ok', source_url, label, section['section_id'])
            return
        if force_refresh:
            self.state.remove_records_from_source(url)
        res = self.state.fetch(url)
        if res.error or (res.status_code and res.status_code >= 400):
            err = {
                'url': url,
                'status_code': res.status_code,
                'error': res.error or f'HTTP {res.status_code}',
                'section_id': section['section_id'],
                'phase': 'detail',
            }
            self.state.manifest['errors'].append(err)
            self.state.add_outcome(url, 'detail_article_page', 'error', source_url, label, section['section_id'], res.status_code, err['error'])
            return
        page, atts, edges = extract_detail_page(res.text, url, self.base_url, self.site_id, section['section_id'])
        self.state.detail_records_by_url[url] = page
        self.state.add_edges(edges)
        self.state.add_outcome(url, 'detail_article_page', 'crawled_detail_ok', source_url, label, section['section_id'], res.status_code)
        for attachment in atts:
            self.state.add_attachment(attachment, url, section['section_id'])
        self._record_inline_artifacts(page, url, section)

    def record_section_content_page(
        self,
        url: str,
        section: dict,
        source_url: str,
        html: str,
        status_code: int | None = None,
    ) -> None:
        url = normalize_url(url, self.base_url)
        if url in self.state.detail_records_by_url:
            self._record_inline_artifacts(self.state.detail_records_by_url[url], url, section)
            return
        page, atts, edges = extract_detail_page(html, url, self.base_url, self.site_id, section['section_id'])
        if not (page.get('title') or page.get('content_text') or atts or page.get('inline_links') or page.get('inline_images')):
            return
        page['page_type'] = 'section_content_page'
        page['source_page_type'] = 'section_list_page'
        page['source_status_code'] = status_code
        self.state.detail_records_by_url[url] = page
        self.state.add_edges(edges)
        for attachment in atts:
            self.state.add_attachment(attachment, url, section['section_id'])
        self._record_inline_artifacts(page, url, section)

    def crawl_list_section(self, section: dict) -> None:
        section_id = section['section_id']
        next_url = normalize_url(section['url'], self.base_url)
        visited = set()
        page_index = 1
        pages_crawled = 0
        known_pages_in_a_row = 0
        frontier_remaining = self.refresh_frontier if self.state.incremental else 0
        max_pages = int(section.get('pagination', {}).get('max_pages_safety', self.cfg.get('crawl_policy', {}).get('max_pages_safety', 20)))
        last_url: str | None = None
        termination_reason = 'empty_start_url'
        terminal_verified = False
        while next_url and next_url not in visited and page_index <= max_pages:
            current_url = next_url
            last_url = current_url
            visited.add(next_url)
            res = self.state.fetch(next_url)
            if res.error or (res.status_code and res.status_code >= 400):
                err = {
                    'url': next_url,
                    'status_code': res.status_code,
                    'error': res.error or f'HTTP {res.status_code}',
                    'section_id': section_id,
                    'phase': 'list',
                }
                self.state.manifest['errors'].append(err)
                self.state.add_outcome(next_url, 'section_list_page', 'error', section.get('url'), section.get('name'), section_id, res.status_code, err['error'])
                termination_reason = 'fetch_error'
                break
            item_container_selector = section.get('item_container_selector') or self.cfg.get('selectors', {}).get('list', {}).get('item_container')
            items = extract_list_items(res.text, next_url, self.base_url, item_container_selector)
            item_counts = Counter(item['target_type'] for item in items)
            page_has_new = any(normalize_url(item['url'], self.base_url) not in self.state.initial_known_urls for item in items)
            self.state.remove_records_from_source(next_url)
            self.list_pages_by_url[next_url] = {
                'page_id': stable_id(self.site_id, next_url),
                'site_id': self.site_id,
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
            pages_crawled += 1
            self.state.add_outcome(next_url, 'section_list_page', 'crawled_list_ok', section.get('url'), section.get('name'), section_id, res.status_code)
            if not items:
                self.record_section_content_page(next_url, section, section.get('url'), res.text, res.status_code)
            for item in items:
                frontier_remaining = self._record_list_item(
                    item,
                    section,
                    next_url,
                    section_id,
                    frontier_remaining,
                )
            next_url = discover_next_url(res.text, next_url, self.base_url)
            if not next_url:
                termination_reason = 'no_next_page'
                terminal_verified = True
            if self.state.incremental:
                known_pages_in_a_row = 0 if page_has_new else known_pages_in_a_row + 1
                if known_pages_in_a_row >= self.known_page_stop:
                    termination_reason = 'incremental_known_page_stop'
                    terminal_verified = True
                    break
            page_index += 1
        else:
            if next_url and next_url in visited:
                termination_reason = 'pagination_cycle'
            elif next_url and page_index > max_pages:
                termination_reason = 'safety_cap'
            elif not next_url:
                termination_reason = 'no_next_page'
                terminal_verified = True

        if termination_reason == 'safety_cap' and not self.state.incremental:
            err = {
                'url': last_url,
                'next_url': next_url,
                'section_id': section_id,
                'phase': 'pagination',
                'error': f'pagination reached max_pages_safety={max_pages} before terminal page',
            }
            self.state.manifest['errors'].append(err)

        if not self.state.incremental:
            record_pagination_evidence(self.state.manifest, {
                'section_id': section_id,
                'section_name': section.get('name'),
                'section_url': section.get('url'),
                'last_url': last_url,
                'next_url': next_url,
                'pages_crawled': pages_crawled,
                'max_pages_safety': max_pages,
                'termination_reason': termination_reason,
                'terminal_verified': terminal_verified,
            })

    def _record_list_item(
        self,
        item: dict,
        section: dict,
        next_url: str,
        section_id: str,
        frontier_remaining: int,
    ) -> int:
        target_type = item['target_type']
        item_url = normalize_url(item['url'], self.base_url)
        edge_id = stable_id(next_url, item_url, item['title'], item.get('position', 0))
        self.state.edges_by_id[edge_id] = {
            'edge_id': edge_id,
            'from_url': next_url,
            'to_url': item_url,
            'anchor_text': item['title'],
            'edge_type': 'list_item',
            'target_type': target_type,
            'same_domain': same_domain(item_url, self.base_url),
        }
        if target_type == 'detail_article_page':
            old_page = self.state.detail_records_by_url.get(item_url)
            title_changed = bool(old_page and item.get('title') and old_page.get('title') and item['title'] != old_page.get('title'))
            refresh_recent = self.state.incremental and item_url in self.state.initial_known_urls and frontier_remaining > 0
            if refresh_recent:
                frontier_remaining -= 1
            force_refresh = (
                not self.state.incremental
                or item_url not in self.state.initial_known_urls
                or item_url not in self.state.detail_records_by_url
                or title_changed
                or refresh_recent
            )
            self.crawl_detail(item_url, section, next_url, item['title'], force_refresh=force_refresh)
        elif target_type == 'attachment_file':
            self.state.add_attachment({
                'attachment_id': stable_id(next_url, item_url, item['title']),
                'parent_url': next_url,
                'name': item['title'],
                'url': item_url,
                'extension': item_url.rsplit('.', 1)[-1].lower(),
                'position': item.get('position', 0),
            }, next_url, section_id)
        elif target_type in {'external_link', 'redirect_link'}:
            self.state.add_external({'url': item_url, 'label': item['title'], 'target_type': target_type}, next_url, section)
        else:
            self.state.add_outcome(item_url, target_type, f'{target_type}_recorded', next_url, item['title'], section_id)
        return frontier_remaining

    def _crawl_direct_detail_backfill(self, section_index: int) -> None:
        if self.cfg.get('crawl_policy', {}).get('direct_detail_backfill', True) is not True:
            return
        direct_detail_section = {
            'section_id': f'{self.site_id}_direct_detail_links',
            'site_id': self.site_id,
            'name': 'Direct detail links',
            'url': self.base_url,
            'section_type': 'direct_detail_links',
            'nav_path': ['首页', '直接详情链接'],
            'crawlable': True,
            'business_tags': ['direct_detail'],
            'pagination': {'type': 'none'},
            'source': 'manifest_backfill',
        }
        direct_section_recorded = False
        while True:
            direct_detail_urls = [
                (url, record)
                for url, record in list(self.state.manifest['url_outcomes'].items())
                if record.get('target_type') == 'detail_article_page'
                and record.get('outcome') not in {'crawled_detail_ok', 'error'}
                and url not in self.seen_direct_details
            ]
            if not direct_detail_urls:
                break
            if not direct_section_recorded:
                self.extra_report_sections.append(direct_detail_section)
                direct_section_recorded = True
            for url, record in direct_detail_urls:
                self.seen_direct_details.add(url)
                self.crawl_detail(
                    url,
                    direct_detail_section,
                    (record.get('sources') or [self.base_url])[0],
                    (record.get('labels') or [None])[0],
                )
            section_index = self._crawl_pending_sections(section_index)
