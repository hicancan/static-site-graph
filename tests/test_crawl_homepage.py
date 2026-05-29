from __future__ import annotations

from sitegraph.crawl_homepage import configured_homepage_modules


def test_configured_homepage_modules_normalize_and_filter_entries() -> None:
    modules = configured_homepage_modules(
        {
            'homepage_modules': [
                {'name': '通知公告', 'list_url': '/notice/list.htm', 'container_selector': '.notice'},
                {'name': '缺少地址'},
                {'url': '/missing-name/list.htm'},
            ]
        },
        'https://demo.example.edu/',
        'demo',
    )

    assert len(modules) == 1
    assert modules[0]['site_id'] == 'demo'
    assert modules[0]['name'] == '通知公告'
    assert modules[0]['url'] == 'https://demo.example.edu/'
    assert modules[0]['list_url'] == 'https://demo.example.edu/notice/list.htm'
    assert modules[0]['container_selector'] == '.notice'
    assert modules[0]['source'] == 'config'
