from __future__ import annotations

from .classify import same_domain
from .extract import extract_all_links
from .util import normalize_url, stable_id


def _nav_path(node: dict, by_id: dict[str, dict]) -> list[str]:
    labels = [node['label']]
    parent_id = node.get('parent_id')
    while parent_id and parent_id in by_id:
        parent = by_id[parent_id]
        labels.append(parent['label'])
        parent_id = parent.get('parent_id')
    return list(reversed(labels))


def _section_from_node(site_id: str, node: dict, nav_path: list[str]) -> dict:
    return {
        'section_id': f'{site_id}_nav_{stable_id(node["url"], *nav_path, length=12)}',
        'site_id': site_id,
        'name': nav_path[-1],
        'url': node['url'],
        'section_type': 'nav_section',
        'nav_path': nav_path,
        'crawlable': True,
        'business_tags': ['nav'],
        'pagination': {'type': 'next_link', 'max_pages_safety': 500},
        'source': 'homepage_nav',
    }


def _section_from_module(site_id: str, module: dict) -> dict | None:
    if not module.get('list_url'):
        return None
    return {
        'section_id': f'{site_id}_home_module_{stable_id(module["name"], module["list_url"], length=12)}',
        'site_id': site_id,
        'name': module['name'],
        'url': module['list_url'],
        'section_type': 'homepage_module',
        'nav_path': ['首页', module['name']],
        'crawlable': True,
        'business_tags': ['homepage_module'],
        'pagination': {'type': 'next_link', 'max_pages_safety': 500},
        'source': 'homepage_module',
        'homepage_url': module['url'],
        'container_selector': module.get('container_selector'),
    }


def discover_sections_from_homepage(
    cfg: dict,
    *,
    base_url: str,
    site_id: str,
    nav_nodes: list[dict],
    homepage_modules: list[dict],
    home_html: str,
) -> list[dict]:
    sections_by_url: dict[str, dict] = {}

    def add_section(section: dict) -> None:
        if section.get('crawlable', True) is False:
            return
        section = dict(section)
        section.setdefault('site_id', site_id)
        section['url'] = normalize_url(section['url'], base_url)
        section.setdefault('pagination', {'type': 'next_link', 'max_pages_safety': 500})
        if section.get('source') in {None, ''}:
            section['source'] = 'declared_section'
        existing = sections_by_url.get(section['url'])
        if not existing or existing.get('source') == 'homepage_nav':
            sections_by_url[section['url']] = section

    for section in cfg.get('sections', []):
        add_section(section)

    if cfg.get('crawl_policy', {}).get('auto_discover_sections_from_homepage', True):
        nodes_by_id = {node['node_id']: node for node in nav_nodes}
        for node in nav_nodes:
            if node['target_type'] == 'section_list_page':
                path = _nav_path(node, nodes_by_id)
                add_section(_section_from_node(site_id, node, path))
        for module in homepage_modules:
            section = _section_from_module(site_id, module)
            if section:
                add_section(section)
        if home_html:
            home_links, _ = extract_all_links(home_html, base_url, base_url)
            for link in home_links:
                if link['target_type'] == 'section_list_page' and same_domain(link['url'], base_url):
                    add_section({
                        'section_id': f'{site_id}_home_link_{stable_id(link["url"], link["label"], length=12)}',
                        'site_id': site_id,
                        'name': link['label'] or link['url'],
                        'url': link['url'],
                        'section_type': 'homepage_link_section',
                        'nav_path': ['首页', link['label'] or link['url']],
                        'crawlable': True,
                        'business_tags': ['homepage_link'],
                        'pagination': {'type': 'next_link', 'max_pages_safety': 500},
                        'source': 'homepage_nav',
                    })

    return list(sections_by_url.values())
