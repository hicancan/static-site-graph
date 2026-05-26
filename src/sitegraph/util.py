from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(*parts: object, length: int = 20) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:length]


def normalize_url(url: str, base_url: str | None = None) -> str:
    url = (url or '').strip()
    if not url:
        return ''
    parsed_initial = urlparse(url)
    if parsed_initial.scheme and parsed_initial.scheme.lower() not in {'http', 'https'}:
        return url
    if base_url:
        url = urljoin(base_url, url)
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if base_url and netloc:
        base = urlparse(base_url)
        if netloc == base.netloc.lower() and base.scheme:
            scheme = base.scheme.lower()
            netloc = base.netloc.lower()
    return urlunparse((scheme, netloc, parsed.path, '', parsed.query, ''))


def clean_text(value: str | None) -> str:
    return re.sub(r'\s+', ' ', value or '').strip()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        for item in records:
            f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + '\n')


def append_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        for item in records:
            f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + '\n')
