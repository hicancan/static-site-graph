from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import requests

from .coverage import apply_coverage_to_manifest, build_coverage_report, record_pagination_evidence, write_coverage_report
from .crawl_output import write_site_metadata
from .fetch import DEFAULT_HEADERS
from .outcomes import (
    MAX_OUTCOME_LABELS,
    MAX_OUTCOME_SECTION_IDS,
    MAX_OUTCOME_SOURCES,
    append_limited_unique,
    compact_url_outcomes,
)
from .util import now_iso, stable_id, write_json, write_jsonl


JOB91_SCHOOL_CODE = "10293"
JOB91_SITE_KIND = "0"
JOB91_HOME_PATH = f"/sub-station/home/{JOB91_SCHOOL_CODE}"


@dataclass(frozen=True)
class Job91ApiClient:
    base_url: str
    timeout: int

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            response = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(f"job91 API request failed: {url}?{urlencode(params or {})}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("success") is not True or payload.get("code") != 200:
            raise RuntimeError(f"job91 API returned invalid payload for {url}: {json.dumps(payload, ensure_ascii=False)[:500]}")
        return payload


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _flatten_columns(columns: list[dict[str, Any]], parent_path: list[str] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in columns:
        name = _clean(item.get("lmmc"))
        path = [*(parent_path or []), name] if name else [*(parent_path or [])]
        children = item.get("model") if isinstance(item.get("model"), list) else []
        lmid = _clean(item.get("lmid"))
        if lmid:
            out.append({"lmid": lmid, "name": name or lmid, "nav_path": path, "type": _clean(item.get("lmlx"))})
        out.extend(_flatten_columns(children, path))
    return out


def _record_url(base_url: str, kind: str, record_id: str) -> str:
    return f"{base_url.rstrip('/')}/sub-station/{kind}/{quote(record_id, safe='')}"


def _news_record(base_url: str, site_id: str, section: dict[str, Any], item: dict[str, Any], position: int) -> dict[str, Any]:
    record_id = _clean(item.get("xwid")) or stable_id(section["section_id"], item.get("xwbt"), position)
    title = _clean(item.get("xwbt")) or "未命名就业信息"
    content_parts = [
        _clean(item.get("xwfbt")),
        _clean(item.get("xwnr")),
        _clean(item.get("xwbq")),
        _clean(item.get("flbq")),
        _clean(item.get("hdsj")),
        _clean(item.get("hddd")),
    ]
    return {
        "page_id": stable_id(site_id, "job91-news", record_id),
        "site_id": site_id,
        "section_id": section["section_id"],
        "url": _clean(item.get("tzljdz")) or _record_url(base_url, "news", record_id),
        "page_type": "detail_article_page",
        "title": title,
        "publisher": "南京邮电大学就业信息网",
        "published_at": _clean(item.get("fbsj")) or None,
        "view_count": None,
        "content_text": " ".join(part for part in content_parts if part),
        "content_hash": stable_id(json.dumps(item, ensure_ascii=False, sort_keys=True)),
        "status": "ok",
        "content_status": "normal_content" if any(content_parts) else "low_content",
        "extraction_strategy": "job91_api",
        "headings": [],
        "inline_links": [],
        "inline_images": [],
        "attachment_count": 0,
    }


def _fair_record(base_url: str, site_id: str, section: dict[str, Any], item: dict[str, Any], position: int) -> dict[str, Any]:
    record_id = _clean(item.get("zphid")) or stable_id(section["section_id"], item.get("zphmc"), position)
    title = _clean(item.get("zphmc")) or "未命名招聘会"
    place = _clean(item.get("jbcd"))
    organizer = _clean(item.get("gljg")) or "南京邮电大学"
    content = " ".join(part for part in [organizer, place] if part)
    return {
        "page_id": stable_id(site_id, "job91-fair", record_id),
        "site_id": site_id,
        "section_id": section["section_id"],
        "url": _record_url(base_url, "job-fair", record_id),
        "page_type": "detail_article_page",
        "title": title,
        "publisher": organizer,
        "published_at": _clean(item.get("jbkssj")) or None,
        "view_count": None,
        "content_text": content,
        "content_hash": stable_id(json.dumps(item, ensure_ascii=False, sort_keys=True)),
        "status": "ok",
        "content_status": "normal_content" if content else "low_content",
        "extraction_strategy": "job91_api",
        "headings": [],
        "inline_links": [],
        "inline_images": [],
        "attachment_count": 0,
    }


def _job91_item_key(item: dict[str, Any]) -> str:
    for key in ("xwid", "zphid", "zwid", "companyId", "id"):
        value = _clean(item.get(key))
        if value:
            return f"{key}:{value}"
    return stable_id(json.dumps(item, ensure_ascii=False, sort_keys=True))


def _fetch_job91_list_pages(
    client: Job91ApiClient,
    *,
    lmid: str,
    row: int,
    max_pages: int,
) -> tuple[list[tuple[int, list[dict[str, Any]]]], dict[str, Any]]:
    pages: list[tuple[int, list[dict[str, Any]]]] = []
    seen_page_keys: set[tuple[str, ...]] = set()
    termination_reason = "empty_first_page"
    terminal_verified = False
    next_page_after_terminal: int | None = None
    for page_index in range(1, max_pages + 1):
        params = {"lmid": lmid, "row": row}
        if page_index > 1:
            params["page"] = page_index
        payload = client.get_json("/web/wsjysc/wzsy/getLbsj", params)
        items = payload.get("result")
        if not isinstance(items, list):
            raise RuntimeError(f"job91 list result for {lmid} page {page_index} must be a list")
        item_keys = tuple(_job91_item_key(item) for item in items)
        if page_index > 1 and item_keys and item_keys in seen_page_keys:
            termination_reason = "api_duplicate_page"
            terminal_verified = True
            break
        if not items:
            termination_reason = "empty_page"
            terminal_verified = True
            break
        seen_page_keys.add(item_keys)
        pages.append((page_index, items))
        if len(items) < row:
            termination_reason = "short_page"
            terminal_verified = True
            break
    else:
        if pages:
            termination_reason = "safety_cap"
            next_page_after_terminal = max_pages + 1
        else:
            termination_reason = "empty_first_page"
            terminal_verified = True
    return pages, {
        "termination_reason": termination_reason,
        "terminal_verified": terminal_verified,
        "next_page": next_page_after_terminal,
    }


def crawl_job91_site(cfg: dict[str, Any], *, out_root: Path, dry_run: bool = False) -> dict[str, Any]:
    site = cfg["site"]
    site_id = site["id"]
    base_url = str(site["base_url"]).rstrip("/")
    timeout = int(cfg.get("crawl_policy", {}).get("timeout_seconds", 20))
    max_items = int(cfg.get("crawl_policy", {}).get("job91_items_per_section", 80))
    max_pages = int(cfg.get("crawl_policy", {}).get("job91_max_pages_per_section", cfg.get("crawl_policy", {}).get("max_pages_safety", 20)))
    client = Job91ApiClient(base_url=base_url, timeout=timeout)

    if dry_run:
        return {"dry_run": True, "site_id": site_id, "base_url": base_url, "adapter": site["adapter"]}

    out_root.mkdir(parents=True, exist_ok=True)
    write_site_metadata(out_root, site=site, site_id=site_id, base_url=base_url + "/", incremental=False)

    wzid_payload = client.get_json("/web/wsjysc/wzsy/getWzid", {"xxdm": JOB91_SCHOOL_CODE, "wzlx": JOB91_SITE_KIND})
    wzid = _clean(wzid_payload.get("result"))
    if not wzid:
        raise RuntimeError("job91 getWzid result is empty")
    columns_payload = client.get_json("/web/wsjysc/wzsy/getXwlm", {"wzid": wzid})
    raw_columns = columns_payload.get("result")
    if not isinstance(raw_columns, list) or not raw_columns:
        raise RuntimeError("job91 getXwlm returned no columns")
    columns = _flatten_columns(raw_columns)

    sections: list[dict[str, Any]] = []
    list_pages: list[dict[str, Any]] = []
    detail_pages_by_url: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    url_outcomes: dict[str, dict[str, Any]] = {}
    coverage_manifest: dict[str, Any] = {"coverage": {"pagination": []}, "errors": []}
    root_url = base_url + "/"
    home_url = base_url + JOB91_HOME_PATH
    url_outcomes[root_url] = {
        "url": root_url,
        "target_type": "homepage",
        "outcome": "crawled_homepage_ok",
        "labels": [],
        "sources": [],
        "section_ids": [],
        "status_code": 200,
    }
    url_outcomes[home_url] = {
        "url": home_url,
        "target_type": "homepage",
        "outcome": "crawled_homepage_ok",
        "labels": [],
        "sources": [],
        "section_ids": [],
        "status_code": 200,
    }

    for column in columns:
        lmid = column["lmid"]
        section_id = f"{site_id}_{stable_id(lmid, column['name'], length=12)}"
        section_url = f"{base_url}/sub-station/list/{quote(lmid, safe='')}"
        section = {
            "section_id": section_id,
            "site_id": site_id,
            "name": column["name"],
            "url": section_url,
            "section_type": "api_list",
            "nav_path": column["nav_path"],
            "crawlable": True,
            "business_tags": ["employment", "job91", "api"],
            "pagination": {"type": "api", "max_pages_safety": max_pages},
            "source": "job91_api",
            "api": {"endpoint": "/web/wsjysc/wzsy/getLbsj", "lmid": lmid, "row": max_items, "page_param": "page"},
        }
        sections.append(section)
        pages, pagination_result = _fetch_job91_list_pages(client, lmid=lmid, row=max_items, max_pages=max_pages)
        if pagination_result["termination_reason"] == "safety_cap":
            coverage_manifest["errors"].append({
                "section_id": section_id,
                "phase": "pagination",
                "error": f"job91 API reached max_pages_safety={max_pages} before terminal page",
                "next_page": pagination_result["next_page"],
            })
        record_pagination_evidence(coverage_manifest, {
            "section_id": section_id,
            "section_name": column["name"],
            "section_url": section_url,
            "last_url": f"{section_url}?page={pages[-1][0]}" if pages else section_url,
            "next_url": f"{section_url}?page={pagination_result['next_page']}" if pagination_result.get("next_page") else None,
            "pages_crawled": len(pages),
            "max_pages_safety": max_pages,
            "termination_reason": pagination_result["termination_reason"],
            "terminal_verified": pagination_result["terminal_verified"],
        })
        url_outcomes[section_url] = {
            "url": section_url,
            "target_type": "section_list_page",
            "outcome": "crawled_list_ok",
            "labels": [column["name"]],
            "sources": [home_url],
            "section_ids": [section_id],
            "status_code": 200,
        }
        for page_index, items in pages:
            list_url = section_url if page_index == 1 else f"{section_url}?page={page_index}"
            page_id = stable_id(site_id, list_url)
            list_pages.append({
                "page_id": page_id,
                "site_id": site_id,
                "section_id": section_id,
                "url": list_url,
                "page_type": "section_list_page",
                "status": "ok",
                "page_index": page_index,
                "item_count": len(items),
                "target_type_counts": {"detail_article_page": len(items)},
                "pagination": {
                    "raw_text": None,
                    "page_size": max_items,
                    "total_records": None,
                    "current_page": page_index,
                    "total_pages": None,
                    "terminal_verified": pagination_result["terminal_verified"],
                    "termination_reason": pagination_result["termination_reason"],
                },
                "fetched_at": now_iso(),
            })
            if page_index > 1:
                url_outcomes[list_url] = {
                    "url": list_url,
                    "target_type": "section_list_page",
                    "outcome": "crawled_list_ok",
                    "labels": [column["name"]],
                    "sources": [section_url],
                    "section_ids": [section_id],
                    "status_code": 200,
                }
            for position, item in enumerate(items):
                absolute_position = (page_index - 1) * max_items + position
                page = (
                    _fair_record(base_url, site_id, section, item, absolute_position)
                    if "zphid" in item or "zphmc" in item
                    else _news_record(base_url, site_id, section, item, absolute_position)
                )
                detail_pages_by_url.setdefault(page["url"], page)
                edges.append({
                    "edge_id": stable_id(list_url, page["url"], page["title"], absolute_position),
                    "from_url": list_url,
                    "to_url": page["url"],
                    "anchor_text": page["title"],
                    "edge_type": "api_list_item",
                    "target_type": "detail_article_page",
                    "same_domain": True,
                })
                outcome_record = url_outcomes.setdefault(page["url"], {
                    "url": page["url"],
                    "target_type": "detail_article_page",
                    "outcome": "crawled_detail_ok",
                    "labels": [],
                    "sources": [],
                    "section_ids": [],
                    "status_code": 200,
                })
                outcome_record["target_type"] = "detail_article_page"
                outcome_record["outcome"] = "crawled_detail_ok"
                append_limited_unique(outcome_record, "labels", page["title"], MAX_OUTCOME_LABELS)
                append_limited_unique(outcome_record, "sources", list_url, MAX_OUTCOME_SOURCES)
                append_limited_unique(outcome_record, "section_ids", section_id, MAX_OUTCOME_SECTION_IDS)

    nav_nodes = [
        {
            "node_id": stable_id(site_id, column["lmid"]),
            "site_id": site_id,
            "label": column["name"],
            "url": f"{base_url}/sub-station/list/{quote(column['lmid'], safe='')}",
            "nav_path": column["nav_path"],
            "depth": max(1, len(column["nav_path"])),
            "target_type": "section_list_page",
            "same_domain": True,
            "parent_id": None,
            "position": index,
        }
        for index, column in enumerate(columns)
    ]
    homepage_modules = [
        {
            "module_id": stable_id(site_id, column["lmid"], "module"),
            "site_id": site_id,
            "name": column["name"],
            "url": home_url,
            "list_url": f"{base_url}/sub-station/list/{quote(column['lmid'], safe='')}",
            "container_selector": None,
            "link_count": None,
            "position": index,
            "source": "job91_api",
        }
        for index, column in enumerate(columns)
    ]

    write_json(out_root / "nav_tree.json", {"site_id": site_id, "generated_at": now_iso(), "nodes": nav_nodes})
    write_json(out_root / "homepage_modules.json", {"site_id": site_id, "generated_at": now_iso(), "modules": homepage_modules})
    write_json(out_root / "sections.json", sections)
    write_jsonl(out_root / "list_pages.jsonl", list_pages)
    detail_pages = list(detail_pages_by_url.values())
    write_jsonl(out_root / "detail_pages.jsonl", detail_pages)
    write_jsonl(out_root / "attachments.jsonl", [])
    write_jsonl(out_root / "external_links.jsonl", [])
    write_jsonl(out_root / "edges.jsonl", edges)

    url_outcomes = compact_url_outcomes(url_outcomes)
    outcome_counts = Counter(record["outcome"] for record in url_outcomes.values())
    manifest = {
        "site_id": site_id,
        "generated_at": now_iso(),
        "totals": {
            "sections": len(sections),
            "nav_nodes": len(nav_nodes),
            "homepage_modules": len(homepage_modules),
            "list_pages": len(list_pages),
            "detail_pages": len(detail_pages),
            "low_content_detail_pages": sum(1 for page in detail_pages if page.get("content_status") == "low_content"),
            "attachments": 0,
            "external_links": 0,
            "edges": len(edges),
            "url_outcomes": len(url_outcomes),
        },
        "outcomes": dict(sorted(outcome_counts.items())),
        "errors": coverage_manifest["errors"],
        "quality": {
            "all_discovered_urls_have_outcomes": True,
            "errors": len(coverage_manifest["errors"]),
            "attachment_policy": cfg.get("crawl_policy", {}).get("attachment_policy", "metadata_only"),
            "external_link_policy": cfg.get("crawl_policy", {}).get("external_link_policy", "record_only"),
        },
        "url_outcomes": url_outcomes,
        "coverage": coverage_manifest["coverage"],
    }
    coverage_report = build_coverage_report(
        cfg=cfg,
        site_id=site_id,
        out_root=out_root,
        manifest=manifest,
        sections=sections,
        list_pages=list_pages,
        detail_pages=detail_pages,
        attachments=[],
        external_links=[],
        incremental=False,
    )
    apply_coverage_to_manifest(manifest, coverage_report)
    write_coverage_report(out_root, coverage_report, incremental=False)
    write_json(out_root / "manifest.json", manifest)
    return manifest["totals"]
