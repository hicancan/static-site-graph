from __future__ import annotations

from dataclasses import dataclass

from .crawl_state import CrawlState
from .extract import (
    extract_all_links,
    extract_homepage_modules,
    extract_nav_tree_from_homepage,
)
from .util import normalize_url, stable_id


@dataclass(frozen=True)
class HomepageCrawlResult:
    home_html: str
    nav_nodes: list[dict]
    homepage_modules: list[dict]


def configured_homepage_modules(cfg: dict, base_url: str, site_id: str) -> list[dict]:
    modules = []
    for idx, item in enumerate(cfg.get('homepage_modules', [])):
        name = item.get('name')
        list_url = item.get('list_url') or item.get('url')
        if not name or not list_url:
            continue
        list_url = normalize_url(list_url, base_url)
        modules.append({
            'module_id': item.get('module_id') or f'{site_id}_home_module_{stable_id(name, list_url, length=12)}',
            'site_id': site_id,
            'name': name,
            'url': normalize_url(item.get('homepage_url') or base_url, base_url),
            'list_url': list_url,
            'container_selector': item.get('container_selector'),
            'link_count': item.get('link_count'),
            'position': item.get('position', idx),
            'source': item.get('source', 'config'),
        })
    return modules


def crawl_homepage(cfg: dict, *, base_url: str, site_id: str, state: CrawlState) -> HomepageCrawlResult:
    home_res = state.fetch(base_url)
    if home_res.error or (home_res.status_code and home_res.status_code >= 400):
        state.add_error(
            phase="homepage",
            url=base_url,
            status_code=home_res.status_code,
            message=home_res.error or f"HTTP {home_res.status_code}",
        )
        return HomepageCrawlResult(home_html='', nav_nodes=[], homepage_modules=[])

    home_html = home_res.text
    nav_nodes = extract_nav_tree_from_homepage(home_html, base_url, base_url, site_id)
    homepage_cfg = cfg.get('selectors', {}).get('homepage', {})
    extracted_modules = extract_homepage_modules(
        home_html,
        base_url,
        base_url,
        site_id,
        module_labels=homepage_cfg.get('module_labels'),
        container_selectors=homepage_cfg.get('module_container_selectors'),
    )
    homepage_modules = []
    seen_modules = set()
    for module in configured_homepage_modules(cfg, base_url, site_id) + extracted_modules:
        key = (module.get('name'), module.get('list_url'))
        if key in seen_modules:
            continue
        seen_modules.add(key)
        homepage_modules.append(module)

    state.remove_records_from_source(base_url)
    home_links, home_edges = extract_all_links(home_html, base_url, base_url)
    state.add_edges(home_edges)
    for link in home_links:
        if link['target_type'] in {'external_link', 'redirect_link'}:
            state.add_external(link, base_url)
        elif link['target_type'] == 'attachment_file':
            state.add_attachment({
                'attachment_id': stable_id(base_url, link['url'], link['label']),
                'parent_url': base_url,
                'name': link['label'] or link['url'].rsplit('/', 1)[-1],
                'url': link['url'],
                'extension': link['url'].rsplit('.', 1)[-1].lower(),
                'position': link.get('position', 0),
            })
        elif link["target_type"] == "detail_article_page":
            state.add_detail_candidate(
                link["url"],
                source_url=base_url,
                label=link.get("label"),
            )

    return HomepageCrawlResult(
        home_html=home_html,
        nav_nodes=nav_nodes,
        homepage_modules=homepage_modules,
    )
