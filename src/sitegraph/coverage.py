from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from .util import now_iso, write_json

COVERAGE_REPORT_VERSION = "sitegraph-coverage-v1"
ALLOWED_COVERAGE_STATUSES = {
    "complete",
    "complete_with_exclusions",
    "blocked_by_source",
    "incomplete",
}
ALLOWED_EVIDENCE_SOURCES = {"full_crawl", "incremental_crawl", "backfill"}
ALLOWED_SECTION_SOURCES = {
    "declared_section",
    "homepage_nav",
    "homepage_module",
    "inline_section_link",
    "api_category",
    "archive_section",
}


def audit_evidence_json_ref(cfg: dict[str, Any]) -> str | None:
    policy = cfg.get("crawl_policy", {})
    value = policy.get("audit_evidence_json_ref") or cfg.get("site", {}).get("audit_evidence_json_ref")
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def audit_evidence_ref(cfg: dict[str, Any]) -> str | None:
    policy = cfg.get("crawl_policy", {})
    value = policy.get("audit_evidence_ref") or cfg.get("site", {}).get("audit_evidence_ref")
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def ensure_coverage_state(manifest: dict[str, Any]) -> dict[str, Any]:
    coverage = manifest.setdefault("coverage", {})
    coverage.setdefault("pagination", [])
    coverage.setdefault("unknown_urls", [])
    coverage.setdefault("exclusions", [])
    return coverage


def configured_exclusions(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    raw = cfg.get("crawl_policy", {}).get("coverage_exclusions", [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("crawl_policy.coverage_exclusions must be a list")
    exclusions: list[dict[str, Any]] = []
    today = date.today().isoformat()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("coverage exclusion entries must be objects")
        scope = str(item.get("scope") or "").strip()
        reason = str(item.get("reason") or "").strip()
        evidence_url = str(item.get("evidence_url") or "").strip()
        expiry = str(item.get("expiry") or "").strip()
        owner_action = str(item.get("owner_action") or "").strip()
        if not scope or not reason or not evidence_url or not expiry or not owner_action:
            raise ValueError(
                "coverage exclusion entries require scope, reason, evidence_url, expiry and owner_action"
            )
        if expiry < today:
            raise ValueError(f"coverage exclusion expired: {item!r}")
        normalized = dict(item)
        normalized["scope"] = scope
        normalized["reason"] = reason
        normalized["evidence_url"] = evidence_url
        normalized["expiry"] = expiry
        normalized["owner_action"] = owner_action
        exclusions.append(normalized)
    return exclusions


def _entry_matches_exclusion(entry: dict[str, Any], exclusion: dict[str, Any]) -> bool:
    if exclusion.get("scope") and exclusion["scope"] != "pagination":
        return False
    section_id = str(entry.get("section_id") or "")
    if exclusion.get("section_id") and exclusion["section_id"] != section_id:
        return False
    pattern = exclusion.get("pattern") or exclusion.get("url_pattern")
    if pattern:
        next_url = str(entry.get("next_url") or "")
        last_url = str(entry.get("last_url") or "")
        if not (re.search(str(pattern), next_url) or re.search(str(pattern), last_url)):
            return False
    return True


def _entry_has_exclusion(entry: dict[str, Any], exclusions: list[dict[str, Any]]) -> bool:
    return any(_entry_matches_exclusion(entry, exclusion) for exclusion in exclusions)


def record_pagination_evidence(manifest: dict[str, Any], evidence: dict[str, Any]) -> None:
    coverage = ensure_coverage_state(manifest)
    coverage["pagination"].append(evidence)


def _pagination_terminal_verified(entries: list[dict[str, Any]], exclusions: list[dict[str, Any]]) -> bool:
    if not entries:
        return True
    return all(entry.get("terminal_verified") is True or _entry_has_exclusion(entry, exclusions) for entry in entries)


def _safety_cap_hits(entries: list[dict[str, Any]], exclusions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "section_id": entry.get("section_id"),
            "section_name": entry.get("section_name"),
            "last_url": entry.get("last_url"),
            "next_url": entry.get("next_url"),
            "pages_crawled": entry.get("pages_crawled"),
            "max_pages_safety": entry.get("max_pages_safety"),
        }
        for entry in entries
        if entry.get("termination_reason") == "safety_cap"
        and not _entry_has_exclusion(entry, exclusions)
    ]


def _existing_evidence_source(out_root: Path) -> str | None:
    path = out_root / "coverage_report.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = str(payload.get("evidence_source") or "").strip()
    return value if value in ALLOWED_EVIDENCE_SOURCES else None


def _evidence_source(out_root: Path, incremental: bool) -> str:
    if not incremental:
        return "full_crawl"
    return _existing_evidence_source(out_root) or "incremental_crawl"


def _source_blocking_error(error: dict[str, Any]) -> bool:
    if error.get("source_blocked") is True or error.get("classification") == "blocked_by_source":
        return True
    status_code = error.get("status_code")
    try:
        status = int(status_code)
    except (TypeError, ValueError):
        return False
    return status in {404, 410, 429, 500, 502, 503, 504}


def build_coverage_report(
    *,
    cfg: dict[str, Any],
    site_id: str,
    out_root: Path,
    manifest: dict[str, Any],
    sections: list[dict[str, Any]],
    list_pages: list[dict[str, Any]],
    detail_pages: list[dict[str, Any]],
    attachments: list[dict[str, Any]],
    external_links: list[dict[str, Any]],
    incremental: bool,
) -> dict[str, Any]:
    coverage = ensure_coverage_state(manifest)
    pagination_entries = list(coverage.get("pagination") or [])
    exclusions = configured_exclusions(cfg) + list(coverage.get("exclusions") or [])
    safety_cap_hits = _safety_cap_hits(pagination_entries, exclusions)
    manifest_errors = list(manifest.get("errors") or [])
    unknown_urls = list(coverage.get("unknown_urls") or [])
    blocking_errors = [
        error
        for error in manifest_errors
        if not _entry_has_exclusion(
            {
                "section_id": error.get("section_id"),
                "last_url": error.get("url"),
                "next_url": error.get("next_url"),
                "termination_reason": "safety_cap" if error.get("phase") == "pagination" else error.get("phase"),
            },
            exclusions,
        )
    ]
    section_sources = Counter(str(section.get("source") or "unknown") for section in sections)
    section_types = Counter(str(section.get("section_type") or "unknown") for section in sections)
    audit_ref = audit_evidence_ref(cfg)
    audit_json_ref = audit_evidence_json_ref(cfg)
    evidence_source = _evidence_source(out_root, incremental)
    terminal_verified = _pagination_terminal_verified(pagination_entries, exclusions)
    coverage_status = "complete"
    incomplete_reasons: list[str] = []
    invalid_section_sources = sorted(
        source for source in section_sources if source not in ALLOWED_SECTION_SOURCES
    )
    if not audit_ref:
        coverage_status = "incomplete"
        incomplete_reasons.append("missing audit_evidence_ref")
    if not audit_json_ref:
        coverage_status = "incomplete"
        incomplete_reasons.append("missing audit_evidence_json_ref")
    if safety_cap_hits:
        coverage_status = "incomplete"
        incomplete_reasons.append("pagination safety cap hit before terminal page")
    if not terminal_verified:
        coverage_status = "incomplete"
        incomplete_reasons.append("pagination terminal page not verified")
    if blocking_errors:
        coverage_status = "incomplete"
        incomplete_reasons.append("crawl errors present")
    if unknown_urls:
        coverage_status = "incomplete"
        incomplete_reasons.append("unknown urls require classification or exclusion")
    if invalid_section_sources:
        coverage_status = "incomplete"
        incomplete_reasons.append(f"unknown section sources: {', '.join(invalid_section_sources)}")
    if coverage_status == "complete" and exclusions:
        coverage_status = "complete_with_exclusions"
    if incomplete_reasons == ["crawl errors present"] and all(
        _source_blocking_error(error) for error in blocking_errors
    ):
        coverage_status = "blocked_by_source"
        incomplete_reasons = ["source blocked during crawl; model contract did not fail"]

    return {
        "version": COVERAGE_REPORT_VERSION,
        "site_id": site_id,
        "generated_at": now_iso(),
        "crawl_mode": "incremental" if incremental else "full",
        "evidence_source": evidence_source,
        "coverage_status": coverage_status,
        "incomplete_reasons": incomplete_reasons,
        "audit_evidence_ref": audit_ref,
        "audit_evidence_json_ref": audit_json_ref,
        "sections": {
            "total": len(sections),
            "by_source": dict(sorted(section_sources.items())),
            "by_type": dict(sorted(section_types.items())),
        },
        "pages": {
            "list_pages": len(list_pages),
            "detail_pages": len(detail_pages),
        },
        "attachments": {
            "policy": cfg.get("crawl_policy", {}).get("attachment_policy", "metadata_only"),
            "count": len(attachments),
        },
        "external_links": {
            "policy": cfg.get("crawl_policy", {}).get("external_link_policy", "record_only"),
            "count": len(external_links),
        },
        "pagination": {
            "terminal_verified": terminal_verified,
            "sections_checked": len(pagination_entries),
            "safety_cap_hits": safety_cap_hits,
            "evidence": pagination_entries,
        },
        "urls": {
            "unknown_url_count": len(unknown_urls),
            "excluded_url_count": len(exclusions),
            "exclusions": exclusions,
        },
        "errors": blocking_errors,
        "output_root": str(out_root),
    }


def apply_coverage_to_manifest(manifest: dict[str, Any], report: dict[str, Any]) -> None:
    manifest["errors"] = list(report.get("errors") or [])
    manifest["coverage_status"] = report["coverage_status"]
    manifest["evidence_source"] = report["evidence_source"]
    manifest["audit_evidence_ref"] = report["audit_evidence_ref"]
    manifest["audit_evidence_json_ref"] = report["audit_evidence_json_ref"]
    manifest["pagination_terminal_verified"] = report["pagination"]["terminal_verified"]
    manifest["unknown_url_count"] = report["urls"]["unknown_url_count"]
    manifest["excluded_url_count"] = report["urls"]["excluded_url_count"]
    quality = manifest.setdefault("quality", {})
    quality.update(
        {
            "coverage_status": report["coverage_status"],
            "evidence_source": report["evidence_source"],
            "errors": len(manifest["errors"]),
            "audit_evidence_ref": report["audit_evidence_ref"],
            "audit_evidence_json_ref": report["audit_evidence_json_ref"],
            "pagination_terminal_verified": report["pagination"]["terminal_verified"],
            "unknown_url_count": report["urls"]["unknown_url_count"],
            "excluded_url_count": report["urls"]["excluded_url_count"],
        }
    )


def write_coverage_report(out_root: Path, report: dict[str, Any], incremental: bool) -> None:
    if incremental and (out_root / "coverage_report.json").exists():
        previous_payload = json.loads((out_root / "coverage_report.json").read_text(encoding="utf-8"))
        previous_payload["generated_at"] = "__volatile__"
        candidate = json.loads(json.dumps(report, ensure_ascii=False, sort_keys=True))
        candidate["generated_at"] = "__volatile__"
        if previous_payload == candidate:
            return
    write_json(out_root / "coverage_report.json", report)
