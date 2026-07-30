from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .model import SITE_PACKAGE_FILES, SITE_PACKAGE_FORMAT, SitePackage
from .util import now_iso, write_json, write_jsonl
from .package_contract import validate_schema_file


VOLATILE_OUTPUT_KEYS = {
    "created_at",
    "generated_at",
    "fetched_at",
    "recorded_at",
    "started_at",
    "finished_at",
}


def _artifact_ref(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "path": path.name,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _artifact_refs(root: Path) -> dict[str, dict[str, object]]:
    return {
        name: _artifact_ref(root / name)
        for name in SITE_PACKAGE_FILES
        if name != "manifest.json"
    }


def _package_id(
    site_id: str,
    artifacts: dict[str, dict[str, object]],
) -> str:
    identity = hashlib.sha256()
    identity.update(SITE_PACKAGE_FORMAT.encode())
    identity.update(b"\0")
    identity.update(site_id.encode())
    for name, artifact in artifacts.items():
        identity.update(b"\0")
        identity.update(name.encode())
        identity.update(b"\0")
        identity.update(str(artifact["bytes"]).encode())
        identity.update(b"\0")
        identity.update(str(artifact["sha256"]).encode())
    return identity.hexdigest()


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def validate_site_package(
    root: Path,
    *,
    expected_site_id: str | None = None,
) -> dict[str, int]:
    present = {path.name for path in root.iterdir()}
    expected = set(SITE_PACKAGE_FILES)
    missing = [name for name in SITE_PACKAGE_FILES if not (root / name).is_file()]
    if missing:
        raise ValueError("SitePackage missing: " + ", ".join(missing))
    extra = sorted(present - expected)
    if extra:
        raise ValueError("SitePackage has unexpected entries: " + ", ".join(extra))
    manifest = read_json(root / "manifest.json", None)
    site = read_json(root / "site.json", None)
    validate_schema_file("manifest.schema.json", manifest)
    validate_schema_file("site.schema.json", site)
    site_id = expected_site_id or site["site_id"]
    if manifest["site_id"] != site_id or site["site_id"] != site_id:
        raise ValueError(f"SitePackage site identity mismatch: {site_id}")
    if manifest["format"] != SITE_PACKAGE_FORMAT:
        raise ValueError(f"unsupported SitePackage format: {manifest['format']}")
    expected_artifacts = _artifact_refs(root)
    if manifest["artifacts"] != expected_artifacts:
        raise ValueError("SitePackage artifact size or hash mismatch")
    if manifest["package_id"] != _package_id(site_id, expected_artifacts):
        raise ValueError("SitePackage content identity mismatch")

    sections = read_json(root / "sections.json", None)
    if not isinstance(sections, list):
        raise ValueError("sections.json must be an array")
    for section in sections:
        validate_schema_file("section.schema.json", section)
        if section["site_id"] != site_id:
            raise ValueError("section site identity mismatch")
    _require_unique(sections, "section_id", "sections")

    nav = read_json(root / "nav_tree.json", None)
    if (
        not isinstance(nav, dict)
        or nav.get("site_id") != site_id
        or not isinstance(nav.get("nodes"), list)
    ):
        raise ValueError("nav_tree.json must contain a nodes array")
    for node in nav["nodes"]:
        validate_schema_file("nav_node.schema.json", node)
        if node["site_id"] != site_id:
            raise ValueError("navigation node site identity mismatch")
    _require_unique(nav["nodes"], "node_id", "navigation nodes")

    homepage = read_json(root / "homepage_modules.json", None)
    if (
        not isinstance(homepage, dict)
        or homepage.get("site_id") != site_id
        or not isinstance(homepage.get("modules"), list)
    ):
        raise ValueError("homepage_modules.json must contain a modules array")
    for module in homepage["modules"]:
        validate_schema_file("homepage_module.schema.json", module)
        if module["site_id"] != site_id:
            raise ValueError("homepage module site identity mismatch")
    _require_unique(homepage["modules"], "module_id", "homepage modules")

    list_pages = _validated_rows(root / "list_pages.jsonl", "page.schema.json")
    detail_pages = _validated_rows(
        root / "detail_pages.jsonl",
        "page.schema.json",
    )
    for page in [*list_pages, *detail_pages]:
        if page["site_id"] != site_id:
            raise ValueError("page site identity mismatch")
    _require_unique(list_pages, "page_id", "list pages")
    _require_unique(detail_pages, "page_id", "detail pages")

    attachments = _validated_rows(
        root / "attachments.jsonl",
        "attachment.schema.json",
    )
    external_links = _validated_rows(
        root / "external_links.jsonl",
        "external_link.schema.json",
    )
    edges = _validated_rows(root / "edges.jsonl", "edge.schema.json")
    _require_unique(attachments, "attachment_id", "attachments")
    _require_unique(external_links, "external_id", "external links")
    _require_unique(edges, "edge_id", "edges")

    counts = {
        "sections": len(sections),
        "nav_nodes": len(nav["nodes"]),
        "homepage_modules": len(homepage["modules"]),
        "list_pages": len(list_pages),
        "detail_pages": len(detail_pages),
        "empty_content": sum(
            not str(page.get("content_text") or "").strip()
            for page in detail_pages
        ),
        "unrecognized_page_type": sum(
            error.get("phase") == "classify"
            for error in manifest["errors"]
        ),
        "attachments": len(attachments),
        "external_links": len(external_links),
        "edges": len(edges),
    }
    if manifest["totals"] != counts:
        raise ValueError(
            f"SitePackage totals mismatch: expected {counts}, "
            f"found {manifest['totals']}"
        )
    return counts


def _validated_rows(path: Path, schema_name: str) -> list[dict]:
    rows = read_jsonl(path)
    for row in rows:
        validate_schema_file(schema_name, row)
    return rows


def _require_unique(
    rows: list[dict],
    key: str,
    description: str,
) -> None:
    values = [row.get(key) for row in rows]
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError(f"{description} require non-empty {key}")
    if len(values) != len(set(values)):
        raise ValueError(f"{description} contain duplicate {key}")


def without_volatile(value):
    if isinstance(value, dict):
        return {
            key: without_volatile(item)
            for key, item in value.items()
            if key not in VOLATILE_OUTPUT_KEYS
        }
    if isinstance(value, list):
        return [without_volatile(item) for item in value]
    return value


def canonical_record_list(records: list[dict]) -> list[dict]:
    normalized = [without_volatile(record) for record in records]
    return sorted(
        normalized,
        key=lambda record: json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def write_json_preserving_volatile(
    path: Path,
    payload: object,
    preserve_volatile: bool,
) -> None:
    if (
        preserve_volatile
        and path.exists()
        and without_volatile(read_json(path, None)) == without_volatile(payload)
    ):
        return
    write_json(path, payload)


def write_jsonl_preserving_volatile(
    path: Path,
    records: list[dict],
    preserve_volatile: bool,
) -> None:
    if (
        preserve_volatile
        and path.exists()
        and canonical_record_list(read_jsonl(path))
        == canonical_record_list(records)
    ):
        return
    write_jsonl(path, records)


def merge_incremental_sections(
    out_root: Path,
    sections: list[dict],
    incremental: bool,
) -> list[dict]:
    if not incremental:
        return sections
    sections_by_id = {
        section["section_id"]: section
        for section in read_json(out_root / "sections.json", [])
        if isinstance(section, dict) and section.get("section_id")
    }
    for section in sections:
        sections_by_id[section["section_id"]] = section
    return list(sections_by_id.values())


def _totals(package: SitePackage) -> dict[str, int]:
    details = list(package.detail_pages_by_url.values())
    edges = [
        edge
        for edge in package.edges_by_id.values()
        if edge.get("target_type")
        not in {"static_asset", "non_http_link", "template_placeholder_link"}
    ]
    package.edges_by_id = {edge["edge_id"]: edge for edge in edges}
    return {
        "sections": len(package.sections),
        "nav_nodes": len(package.nav_nodes),
        "homepage_modules": len(package.homepage_modules),
        "list_pages": len(package.list_pages_by_url),
        "detail_pages": len(details),
        "empty_content": sum(
            not str(page.get("content_text") or "").strip() for page in details
        ),
        "unrecognized_page_type": sum(
            error.get("phase") == "classify" for error in package.errors
        ),
        "attachments": len(package.attachments_by_id),
        "external_links": len(package.external_links_by_id),
        "edges": len(edges),
    }


def write_site_package(
    package: SitePackage,
    out_root: Path,
    *,
    incremental: bool,
) -> dict[str, int]:
    out_root.mkdir(parents=True, exist_ok=True)
    definition = package.definition
    package.sections = merge_incremental_sections(
        out_root,
        package.sections,
        incremental,
    )

    write_json_preserving_volatile(
        out_root / "site.json",
        {
            "site_id": definition.id,
            "name": definition.name,
            "base_url": definition.base_url,
            "domain": definition.domain,
        },
        incremental,
    )
    write_json_preserving_volatile(
        out_root / "nav_tree.json",
        {
            "site_id": definition.id,
            "generated_at": now_iso(),
            "nodes": package.nav_nodes,
        },
        incremental,
    )
    write_json_preserving_volatile(
        out_root / "homepage_modules.json",
        {
            "site_id": definition.id,
            "generated_at": now_iso(),
            "modules": package.homepage_modules,
        },
        incremental,
    )
    write_json_preserving_volatile(
        out_root / "sections.json",
        package.sections,
        incremental,
    )
    totals = _totals(package)
    for name, records in (
        ("list_pages.jsonl", list(package.list_pages_by_url.values())),
        ("detail_pages.jsonl", list(package.detail_pages_by_url.values())),
        ("attachments.jsonl", list(package.attachments_by_id.values())),
        ("external_links.jsonl", list(package.external_links_by_id.values())),
        ("edges.jsonl", list(package.edges_by_id.values())),
    ):
        write_jsonl_preserving_volatile(
            out_root / name,
            records,
            incremental,
        )

    artifacts = _artifact_refs(out_root)
    manifest = {
        "format": SITE_PACKAGE_FORMAT,
        "site_id": definition.id,
        "package_id": _package_id(definition.id, artifacts),
        "started_at": package.started_at,
        "finished_at": now_iso(),
        "artifacts": artifacts,
        "totals": totals,
        "errors": package.errors,
    }
    write_json_preserving_volatile(
        out_root / "manifest.json",
        manifest,
        incremental,
    )
    return validate_site_package(out_root, expected_site_id=definition.id)
