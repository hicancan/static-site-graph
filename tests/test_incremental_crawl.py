from __future__ import annotations

import argparse
import json
from pathlib import Path

from sitegraph import cli
from sitegraph.fetch import FetchResult


CONTENT = 'This body is long enough for the crawler to treat it as normal article content. ' * 5


def detail_html(title: str, date: str = '2026-05-02') -> str:
    return f'''
    <html>
      <body>
        <h1 class="arti_title">{title}</h1>
        <div>发布者：admin 发布时间：{date} 浏览次数：1</div>
        <div class="wp_articlecontent">
          {CONTENT}
          <a href="https://external.example.edu/system">External system</a>
        </div>
      </body>
    </html>
    '''


def list_html(items: list[tuple[str, str, str]], next_href: str | None = None) -> str:
    links = ''.join(
        f'<li><span>{date}</span><a href="{href}">{title}</a></li>'
        for title, href, date in items
    )
    next_link = f'<a href="{next_href}">下一页</a>' if next_href else ''
    return f'<html><body><div class="news_list list2"><ul>{links}</ul></div>{next_link}</body></html>'


def write_config(path: Path) -> None:
    path.write_text(
        '''
site:
  id: demo
  name: Demo
  base_url: https://demo.example.edu/
  domain: demo.example.edu
  adapter: demo
crawl_policy:
  timeout_seconds: 1
  max_pages_safety: 10
  auto_discover_sections_from_homepage: false
  external_link_policy: record_only
  attachment_policy: metadata_only
selectors:
  list:
    item_container: ".news_list.list2"
sections:
  - section_id: demo_notice
    site_id: demo
    name: Notices
    url: https://demo.example.edu/1/list.htm
    section_type: list
    nav_path: [Notices]
    crawlable: true
    business_tags: [notice]
    pagination:
      type: next_link
      max_pages_safety: 10
    item_container_selector: ".news_list.list2"
''',
        encoding='utf-8',
    )


def run_crawl(config: Path, out: Path, *, incremental: bool) -> None:
    cli.crawl_site(
        argparse.Namespace(
            config=str(config),
            out=str(out),
            dry_run=False,
            incremental=incremental,
            incremental_known_page_stop=1,
            incremental_refresh_frontier=0,
        )
    )


def test_incremental_crawl_fetches_new_details_and_preserves_noop_files(tmp_path, monkeypatch):
    config = tmp_path / 'site.yaml'
    out = tmp_path / 'index'
    write_config(config)

    pages = {
        'https://demo.example.edu/': '<html><body>home</body></html>',
        'https://demo.example.edu/1/list.htm': list_html(
            [('Old notice A', '/2026/0502/c1a2/page.htm', '2026-05-02')],
            '/1/list2.htm',
        ),
        'https://demo.example.edu/1/list2.htm': list_html(
            [('Old notice B', '/2026/0501/c1a1/page.htm', '2026-05-01')],
        ),
        'https://demo.example.edu/2026/0502/c1a2/page.htm': detail_html('Old notice A'),
        'https://demo.example.edu/2026/0501/c1a1/page.htm': detail_html('Old notice B', '2026-05-01'),
    }
    counts: dict[str, int] = {}

    def fake_fetch(url: str, timeout: int = 20, verify: bool = True) -> FetchResult:
        counts[url] = counts.get(url, 0) + 1
        return FetchResult(url=url, final_url=url, status_code=200, text=pages[url])

    monkeypatch.setattr(cli, 'fetch_html', fake_fetch)

    run_crawl(config, out, incremental=False)
    assert counts['https://demo.example.edu/2026/0502/c1a2/page.htm'] == 1
    assert counts['https://demo.example.edu/2026/0501/c1a1/page.htm'] == 1

    sections_path = out / 'sections.json'
    sections = json.loads(sections_path.read_text(encoding='utf-8'))
    sections.append({
        'section_id': 'demo_old_section',
        'site_id': 'demo',
        'name': 'Old archived section',
        'url': 'https://demo.example.edu/old/list.htm',
        'section_type': 'list',
        'nav_path': ['Old archived section'],
        'crawlable': True,
        'business_tags': ['notice'],
        'pagination': {'type': 'next_link'},
    })
    sections_path.write_text(json.dumps(sections, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'external_links.jsonl').write_text('', encoding='utf-8')

    pages['https://demo.example.edu/1/list.htm'] = list_html(
        [
            ('New notice C', '/2026/0503/c1a3/page.htm', '2026-05-03'),
            ('Old notice A', '/2026/0502/c1a2/page.htm', '2026-05-02'),
        ],
        '/1/list2.htm',
    )
    pages['https://demo.example.edu/2026/0503/c1a3/page.htm'] = detail_html('New notice C', '2026-05-03')
    counts.clear()

    run_crawl(config, out, incremental=True)
    assert counts['https://demo.example.edu/2026/0503/c1a3/page.htm'] == 1
    assert 'https://demo.example.edu/2026/0502/c1a2/page.htm' not in counts
    assert (out / 'detail_pages.jsonl').read_text(encoding='utf-8').count('New notice C') == 1
    assert 'demo_old_section' in (out / 'sections.json').read_text(encoding='utf-8')
    assert 'https://external.example.edu/system' in (out / 'external_links.jsonl').read_text(encoding='utf-8')

    snapshot = {path.name: path.read_text(encoding='utf-8') for path in out.iterdir() if path.is_file()}
    counts.clear()
    run_crawl(config, out, incremental=True)

    assert 'https://demo.example.edu/2026/0503/c1a3/page.htm' not in counts
    assert {path.name: path.read_text(encoding='utf-8') for path in out.iterdir() if path.is_file()} == snapshot
