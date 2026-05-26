from __future__ import annotations

import requests
from dataclasses import dataclass

DEFAULT_HEADERS = {
    'User-Agent': 'static-site-graph/0.1 (+https://github.com/hicancan/static-site-graph)'
}

@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int | None
    text: str
    error: str | None = None


def fetch_html(url: str, timeout: int = 20, verify: bool = True) -> FetchResult:
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout, verify=verify)
        resp.encoding = resp.apparent_encoding or resp.encoding
        return FetchResult(url=url, final_url=str(resp.url), status_code=resp.status_code, text=resp.text)
    except Exception as exc:
        return FetchResult(url=url, final_url=url, status_code=None, text='', error=str(exc))
