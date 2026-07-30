from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .util import normalize_url


SITE_PACKAGE_FORMAT = "static-site-package-v3"
SITE_PACKAGE_FILES = (
    "site.json",
    "nav_tree.json",
    "homepage_modules.json",
    "sections.json",
    "list_pages.jsonl",
    "detail_pages.jsonl",
    "attachments.jsonl",
    "external_links.jsonl",
    "edges.jsonl",
    "manifest.json",
)


@dataclass(frozen=True)
class SiteDefinition:
    id: str
    name: str
    base_url: str
    domain: str
    config: dict[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "SiteDefinition":
        site = config.get("site")
        if not isinstance(site, dict):
            raise ValueError("missing required object: site")
        missing = [
            name
            for name in ("id", "name", "base_url", "domain")
            if not str(site.get(name) or "").strip()
        ]
        if missing:
            raise ValueError(
                "missing required site fields: " + ", ".join(missing)
            )
        base_url = normalize_url(str(site["base_url"]))
        return cls(
            id=str(site["id"]).strip(),
            name=str(site["name"]).strip(),
            base_url=base_url,
            domain=str(site["domain"]).strip().lower(),
            config=config,
        )


@dataclass
class SitePackage:
    definition: SiteDefinition
    started_at: str
    nav_nodes: list[dict[str, Any]] = field(default_factory=list)
    homepage_modules: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    list_pages_by_url: dict[str, dict[str, Any]] = field(default_factory=dict)
    detail_pages_by_url: dict[str, dict[str, Any]] = field(default_factory=dict)
    attachments_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    external_links_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
