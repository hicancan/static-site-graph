from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

from .fetch import FetchResult, fetch_html, fetch_redirect_location
from .model import SitePackage
from .util import normalize_url, now_iso, stable_id


RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
MAX_FETCH_ATTEMPTS = 4


def external_category(
    label: str,
    url: str,
    cfg: dict,
    section: dict | None = None,
) -> str:
    link_cfg = cfg.get("link_classification", {})
    if label in set(link_cfg.get("external_system_labels", [])):
        return "external_system_link"
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()
    if any(domain in host for domain in link_cfg.get("external_policy_domains", [])):
        return "external_policy_link"
    if "/20" in path and path.endswith("/page.htm"):
        return "cross_domain_article_link"
    if section and "policy" in set(section.get("business_tags", [])):
        return "external_policy_link"
    return "external_link"


@dataclass
class CrawlState:
    cfg: dict
    package: SitePackage
    timeout: int
    incremental: bool
    initial_known_urls: set[str]
    fetch_html_fn: Callable[..., FetchResult] = fetch_html
    fetch_redirect_location_fn: Callable[..., object] = fetch_redirect_location
    fetch_cache: dict[str, FetchResult] = field(default_factory=dict)
    pending_details: dict[str, dict] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        return self.package.definition.base_url

    def fetch(self, url: str) -> FetchResult:
        url = normalize_url(url, self.base_url)
        if url not in self.fetch_cache:
            result = self.fetch_html_fn(url, timeout=self.timeout)
            attempt_count = 1
            while self._should_retry_fetch(result) and attempt_count < MAX_FETCH_ATTEMPTS:
                attempt_count += 1
                result = self.fetch_html_fn(url, timeout=max(self.timeout, 60))
            self.fetch_cache[url] = result
        return self.fetch_cache[url]

    @staticmethod
    def _should_retry_fetch(result: FetchResult) -> bool:
        if result.error:
            return True
        return result.status_code in RETRYABLE_STATUS_CODES

    def add_error(
        self,
        *,
        phase: str,
        url: str | None,
        message: str,
        status_code: int | None = None,
        section_id: str | None = None,
        **details,
    ) -> None:
        record = {
            "phase": phase,
            "url": url,
            "status_code": status_code,
            "section_id": section_id,
            "message": message,
            **details,
        }
        self.package.errors.append(
            {key: value for key, value in record.items() if value is not None}
        )

    def add_edges(self, edges: list[dict]) -> None:
        for edge in edges:
            self.package.edges_by_id[edge["edge_id"]] = edge

    def add_detail_candidate(
        self,
        url: str,
        *,
        source_url: str,
        label: str | None,
        section_id: str | None = None,
    ) -> None:
        url = normalize_url(url, self.base_url)
        if not url or url in self.package.detail_pages_by_url:
            return
        self.pending_details.setdefault(
            url,
            {
                "source_url": source_url,
                "label": label,
                "section_id": section_id,
            },
        )

    def add_external(
        self,
        link: dict,
        source_url: str,
        section: dict | None = None,
    ) -> None:
        if not link.get("url"):
            return
        link_url = normalize_url(link["url"], self.base_url)
        resolved_url = None
        redirect_status_code = None
        redirect_error = None
        category_url = link_url
        if link.get("target_type") == "redirect_link":
            redirect = self.fetch_redirect_location_fn(
                link_url,
                timeout=self.timeout,
            )
            redirect_status_code = redirect.status_code
            redirect_error = redirect.error
            if redirect.location:
                resolved_url = normalize_url(redirect.location, self.base_url)
                category_url = resolved_url
            if redirect_error or (
                redirect_status_code is not None and redirect_status_code >= 400
            ):
                self.add_error(
                    phase="redirect",
                    url=link_url,
                    status_code=redirect_status_code,
                    message=redirect_error or f"HTTP {redirect_status_code}",
                )
        category = external_category(
            link.get("label") or "",
            category_url,
            self.cfg,
            section,
        )
        external_id = stable_id(
            link_url,
            resolved_url,
            link.get("label"),
            category,
        )
        self.package.external_links_by_id[external_id] = {
            "external_id": external_id,
            "source_url": source_url,
            "source_section_id": section.get("section_id") if section else None,
            "label": link.get("label") or "",
            "url": resolved_url or link_url,
            "source_redirect_url": link_url if resolved_url else None,
            "redirect_status_code": redirect_status_code,
            "redirect_error": redirect_error,
            "category": category,
            "recorded_at": now_iso(),
        }

    def add_attachment(self, attachment: dict) -> None:
        self.package.attachments_by_id[attachment["attachment_id"]] = attachment

    def backfill_external_records_from_known_details(self) -> None:
        if not self.incremental:
            return
        for page in list(self.package.detail_pages_by_url.values()):
            source_url = page.get("url")
            if not source_url:
                continue
            section = {
                "section_id": page.get("section_id"),
                "business_tags": [],
            }
            for link in page.get("inline_links") or []:
                if link.get("target_type") in {"external_link", "redirect_link"}:
                    self.add_external(link, source_url, section)

    def remove_records_from_source(self, source_url: str) -> None:
        source_url = normalize_url(source_url, self.base_url)
        for attachment_id, attachment in list(
            self.package.attachments_by_id.items()
        ):
            if (
                normalize_url(attachment.get("parent_url", ""), self.base_url)
                == source_url
            ):
                self.package.attachments_by_id.pop(attachment_id, None)
        for external_id, external in list(
            self.package.external_links_by_id.items()
        ):
            if (
                normalize_url(external.get("source_url", ""), self.base_url)
                == source_url
            ):
                self.package.external_links_by_id.pop(external_id, None)
        for edge_id, edge in list(self.package.edges_by_id.items()):
            if normalize_url(edge.get("from_url", ""), self.base_url) == source_url:
                self.package.edges_by_id.pop(edge_id, None)
