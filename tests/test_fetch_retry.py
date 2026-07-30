from __future__ import annotations

from sitegraph.crawl_state import CrawlState
from sitegraph.fetch import FetchResult
from sitegraph.model import SiteDefinition, SitePackage


def make_state(fetch_html_fn):
    definition = SiteDefinition.from_config(
        {
            "site": {
                "id": "demo",
                "name": "Demo",
                "base_url": "https://demo.example.edu/",
                "domain": "demo.example.edu",
            }
        }
    )
    return CrawlState(
        cfg={},
        package=SitePackage(definition=definition, started_at="now"),
        timeout=1,
        incremental=False,
        initial_known_urls=set(),
        fetch_html_fn=fetch_html_fn,
    )


def test_fetch_retries_transient_transport_error() -> None:
    calls: list[int] = []

    def fetch_html(url: str, timeout: int = 20, verify: bool = True) -> FetchResult:
        calls.append(timeout)
        if len(calls) == 1:
            return FetchResult(url=url, final_url=url, status_code=None, text='', error='SSLEOFError')
        return FetchResult(url=url, final_url=url, status_code=200, text='<html>ok</html>')

    result = make_state(fetch_html).fetch('/notice/list.htm')

    assert result.status_code == 200
    assert result.error is None
    assert calls == [1, 60]


def test_fetch_retries_retryable_http_status() -> None:
    calls: list[int] = []

    def fetch_html(url: str, timeout: int = 20, verify: bool = True) -> FetchResult:
        calls.append(timeout)
        if len(calls) == 1:
            return FetchResult(url=url, final_url=url, status_code=429, text='')
        return FetchResult(url=url, final_url=url, status_code=200, text='<html>ok</html>')

    result = make_state(fetch_html).fetch('/notice/list.htm')

    assert result.status_code == 200
    assert calls == [1, 60]


def test_fetch_does_not_retry_non_retryable_http_status() -> None:
    calls: list[int] = []

    def fetch_html(url: str, timeout: int = 20, verify: bool = True) -> FetchResult:
        calls.append(timeout)
        return FetchResult(url=url, final_url=url, status_code=404, text='')

    result = make_state(fetch_html).fetch('/missing/page.htm')

    assert result.status_code == 404
    assert calls == [1]
