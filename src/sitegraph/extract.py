from __future__ import annotations

import re
from bs4 import BeautifulSoup
from bs4.element import Tag
from urllib.parse import urlparse
from .classify import classify_url, extension, ATTACHMENT_EXTENSIONS, same_domain
from .util import clean_text, normalize_url, stable_id

LIST_CONTAINER_SELECTORS = [
    '.news_list.list2',
    '.news_list2.list2',
    '.col_news_con .news_list',
    '.col_news_list .news_list',
    '.col_news_con .news_list2',
    '.col_news_list .news_list2',
]

SKIP_LINK_TEXT = {
    '更多',
    '更多 +',
    '显示更多',
    '首页',
    '上页',
    '上一页',
    '下页',
    '下一页',
    '下一页>>',
    '尾页',
    '末页',
}

MODULE_LABELS = [
    '新闻资讯',
    '新闻动态',
    '通知公告',
    '学工要闻',
    '政策法规',
    '下载专区',
    '双创项目',
    '竞赛成果',
    '教务快讯',
    '教改动态',
    '八面来风',
    '综合信息服务',
    '本科教学工程',
    '校内链接',
    '校外链接',
]

MODULE_CONTAINER_SELECTORS = [
    'div[class*="post-"]',
    '.links-wrap',
    '.ml',
    '.mr',
]


def soup_from_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or '', 'html.parser')


def _normalize_href(href: str | None, page_url: str) -> str:
    if not href:
        return ''
    return normalize_url(href, page_url)


def _link_label(a: Tag) -> str:
    return clean_text(a.get('title') or a.get_text(' ', strip=True))


def _iter_scoped_anchors(soup: BeautifulSoup, container_selector: str | None = None, fallback_all: bool = True) -> list[Tag]:
    if container_selector:
        containers = soup.select(container_selector)
        if containers:
            anchors: list[Tag] = []
            for container in containers:
                anchors.extend(container.find_all('a'))
            return anchors
        if not fallback_all:
            return []
    for selector in LIST_CONTAINER_SELECTORS:
        containers = soup.select(selector)
        if containers:
            anchors = []
            for container in containers:
                anchors.extend(container.find_all('a'))
            return anchors
    return soup.find_all('a') if fallback_all else []


def _is_skippable_link(url: str, label: str, base_url: str) -> bool:
    if not url or classify_url(url, base_url) == 'non_http_link':
        return True
    if not label:
        return True
    if label in SKIP_LINK_TEXT:
        return True
    if re.fullmatch(r'\d+', label):
        return True
    return False


def extract_all_links(html: str, page_url: str, base_url: str, container_selector: str | None = None) -> tuple[list[dict], list[dict]]:
    soup = soup_from_html(html)
    links: list[dict] = []
    edges: list[dict] = []
    for idx, a in enumerate(_iter_scoped_anchors(soup, container_selector)):
        url = _normalize_href(a.get('href'), page_url)
        if not url:
            continue
        label = _link_label(a)
        kind = classify_url(url, base_url)
        if kind == 'non_http_link':
            continue
        if not label and kind == 'static_asset':
            continue
        links.append({'url': url, 'label': label, 'target_type': kind, 'position': idx})
        edges.append({
            'edge_id': stable_id(page_url, url, label, idx),
            'from_url': page_url,
            'to_url': url,
            'anchor_text': label,
            'edge_type': 'link',
            'target_type': kind,
            'same_domain': same_domain(url, base_url),
        })
    return links, edges


def _nav_class_path(a: Tag) -> tuple[str | None, int]:
    parent = a.find_parent(['li', 'div'])
    classes = []
    while parent:
        classes.extend(parent.get('class') or [])
        if parent.name == 'li':
            break
        parent = parent.find_parent(['li', 'div'])
    for cls in classes:
        if re.fullmatch(r'i\d+(?:-\d+)*', cls):
            return cls, cls.count('-') + 1
    return None, 1


def extract_nav_tree_from_homepage(html: str, page_url: str, base_url: str, site_id: str) -> list[dict]:
    soup = soup_from_html(html)
    anchors = soup.select('a.menu-link, a.sub-link, .wp_nav a, .nav a, .menu a')
    if not anchors:
        anchors = soup.find_all('a')
    nodes: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    by_path: dict[str, str] = {}
    stack: dict[int, str] = {}
    for idx, a in enumerate(anchors):
        label = _link_label(a)
        url = _normalize_href(a.get('href'), page_url)
        target_type = classify_url(url, base_url)
        if _is_skippable_link(url, label, base_url):
            continue
        class_path, depth = _nav_class_path(a)
        key = (label, url, depth)
        if key in seen:
            continue
        seen.add(key)
        parent_id = None
        if class_path and '-' in class_path:
            parent_class_path = class_path.rsplit('-', 1)[0]
            parent_id = by_path.get(parent_class_path)
        elif depth > 1:
            parent_id = stack.get(depth - 1)
        node_id = stable_id(site_id, label, url, idx)
        if class_path:
            by_path[class_path] = node_id
        stack[depth] = node_id
        for stale_depth in [item for item in stack if item > depth]:
            stack.pop(stale_depth, None)
        nav_path = [label]
        nodes.append({
            'node_id': node_id,
            'site_id': site_id,
            'label': label,
            'url': url,
            'nav_path': nav_path,
            'depth': depth,
            'target_type': target_type,
            'same_domain': same_domain(url, base_url),
            'parent_id': parent_id,
            'position': idx,
        })
    return nodes


def extract_homepage_modules(
    html: str,
    page_url: str,
    base_url: str,
    site_id: str,
    module_labels: list[str] | None = None,
    container_selectors: list[str] | None = None,
) -> list[dict]:
    soup = soup_from_html(html)
    labels = module_labels or MODULE_LABELS
    selectors = container_selectors or MODULE_CONTAINER_SELECTORS
    containers = []
    seen_container_ids = set()
    for selector in selectors:
        for container in soup.select(selector):
            ident = id(container)
            if ident not in seen_container_ids:
                seen_container_ids.add(ident)
                containers.append(container)
    modules: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for idx, container in enumerate(containers):
        text = clean_text(container.get_text(' ', strip=True))
        name = next((label for label in labels if label in text), '')
        if not name:
            continue
        links, _ = extract_all_links(str(container), page_url, base_url)
        list_urls = [link for link in links if link['target_type'] == 'section_list_page']
        primary_list = next((link for link in list_urls if link['label'] in {'更多 +', '显示更多'}), None)
        if not primary_list and list_urls:
            primary_list = list_urls[0]
        key = (name, primary_list['url'] if primary_list else '', container.get('class', [''])[0] if container.get('class') else '')
        if key in seen:
            continue
        seen.add(key)
        modules.append({
            'module_id': stable_id(site_id, name, idx),
            'site_id': site_id,
            'name': name,
            'url': page_url,
            'list_url': primary_list['url'] if primary_list else None,
            'container_selector': _container_selector(container),
            'link_count': len([link for link in links if link['label'] not in SKIP_LINK_TEXT]),
            'position': idx,
        })
    return modules


def _container_selector(container: Tag) -> str | None:
    classes = container.get('class') or []
    if classes:
        return ''.join(f'.{cls}' for cls in classes[:3])
    if container.get('id'):
        return f"#{container['id']}"
    return None


def extract_list_items(html: str, page_url: str, base_url: str, container_selector: str | None = None) -> list[dict]:
    soup = soup_from_html(html)
    items: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for idx, a in enumerate(_iter_scoped_anchors(soup, container_selector, fallback_all=False)):
        url = _normalize_href(a.get('href'), page_url)
        label = _link_label(a)
        target_type = classify_url(url, base_url)
        if _is_skippable_link(url, label, base_url):
            continue
        if target_type == 'static_asset':
            continue
        key = (url, label)
        if key in seen:
            continue
        seen.add(key)
        text_context = clean_text(a.parent.get_text(' ', strip=True) if a.parent else label)
        date = None
        m = re.search(r'(20\d{2}[-./年]\d{1,2}[-./月]\d{1,2}|\d{2}[-./]\d{2})', text_context)
        if m:
            date = m.group(1)
        items.append({
            'item_id': stable_id(page_url, url, label, idx),
            'title': label,
            'url': url,
            'target_type': target_type,
            'date_text': date,
            'position': idx,
            'context_text': text_context[:500],
        })
    return items


def discover_next_url(html: str, page_url: str, base_url: str) -> str | None:
    soup = soup_from_html(html)
    for a in soup.find_all('a'):
        text = clean_text(a.get_text(' ', strip=True))
        if '下一页' in text:
            href = a.get('href')
            if href:
                url = _normalize_href(href, page_url)
                if classify_url(url, base_url) == 'section_list_page':
                    return url
    return None


def extract_pagination_metadata(html: str) -> dict:
    text = clean_text(soup_from_html(html).get_text(' ', strip=True))
    meta: dict[str, int | str | None] = {
        'raw_text': None,
        'page_size': None,
        'total_records': None,
        'current_page': None,
        'total_pages': None,
    }
    m = re.search(r'每页\s*(\d+)\s*记录\s*总共\s*(\d+)\s*记录.*?页码\s*(\d+)\s*/\s*(\d+)', text)
    if m:
        meta.update({
            'raw_text': m.group(0),
            'page_size': int(m.group(1)),
            'total_records': int(m.group(2)),
            'current_page': int(m.group(3)),
            'total_pages': int(m.group(4)),
        })
    return meta


def _extract_detail_content_node(soup: BeautifulSoup) -> tuple[Tag | BeautifulSoup, str]:
    for selector in ['.wp_articlecontent', '.article', '.entry', '.read', '.article_content', '.news_content', '.v_news_content', '#wp_content_w6_0', '.col_news_con', 'main']:
        node = soup.select_one(selector)
        if node:
            return node, selector
    return soup, 'document_fallback'


def extract_detail_page(html: str, page_url: str, base_url: str, site_id: str, section_id: str | None = None) -> tuple[dict, list[dict], list[dict]]:
    soup = soup_from_html(html)
    title = ''
    for selector in ['.arti_title', '.articleTitle', '.col_title', '.news_title', 'title', 'h1']:
        node = soup.select_one(selector)
        if node:
            title = clean_text(node.get_text(' ', strip=True))
            if title:
                break
    content_node, strategy = _extract_detail_content_node(soup)
    content = clean_text(content_node.get_text(' ', strip=True))
    if strategy == 'document_fallback':
        content = ''
    text_all = clean_text(soup.get_text(' ', strip=True))
    publisher = None
    published_at = None
    view_count = None
    m = re.search(r'发布者[:：]\s*([^\s]+)\s*发布时间[:：]\s*(20\d{2}[-./年]\d{1,2}[-./月]\d{1,2})\s*浏览次数[:：]\s*(\d+)', text_all)
    if m:
        publisher = m.group(1)
        published_at = m.group(2).replace('年', '-').replace('月', '-').replace('日', '')
        view_count = int(m.group(3))
    else:
        m = re.search(r'发布时间[:：]\s*(20\d{2}[-./]\d{1,2}[-./]\d{1,2})', text_all)
        if m:
            published_at = m.group(1)
    headings = [clean_text(node.get_text(' ', strip=True)) for node in content_node.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']) if clean_text(node.get_text(' ', strip=True))]
    links, edges = extract_all_links(str(content_node), page_url, base_url)
    attachments = []
    inline_links = []
    pos = 0
    for link in links:
        ext = extension(link['url']).lstrip('.')
        if ('.' + ext) in ATTACHMENT_EXTENSIONS:
            attachments.append({
                'attachment_id': stable_id(page_url, link['url'], link['label']),
                'parent_url': page_url,
                'name': link['label'] or link['url'].rsplit('/', 1)[-1],
                'url': link['url'],
                'extension': ext,
                'position': pos,
            })
            pos += 1
        elif link['target_type'] not in {'static_asset', 'non_http_link'}:
            inline_links.append(link)
    inline_images = []
    for idx, img in enumerate(content_node.find_all('img')):
        src = _normalize_href(img.get('src'), page_url)
        if not src or '_visitcount' in src:
            continue
        inline_images.append({
            'image_id': stable_id(page_url, src, idx),
            'parent_url': page_url,
            'url': src,
            'alt': clean_text(img.get('alt')),
            'position': idx,
        })
    content_status = 'normal_content' if len(content) >= 80 else 'low_content'
    page = {
        'page_id': stable_id(site_id, page_url),
        'site_id': site_id,
        'section_id': section_id,
        'url': page_url,
        'page_type': 'detail_article_page',
        'title': title,
        'publisher': publisher,
        'published_at': published_at,
        'view_count': view_count,
        'content_text': content,
        'content_hash': stable_id(content) if content else None,
        'status': 'ok' if title else 'low_evidence',
        'content_status': content_status,
        'extraction_strategy': strategy,
        'headings': headings,
        'inline_links': inline_links,
        'inline_images': inline_images,
        'attachment_count': len(attachments),
    }
    return page, attachments, edges
