from __future__ import annotations

import re
from urllib.parse import urlparse

ATTACHMENT_EXTENSIONS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.zip', '.rar'}
STATIC_EXTENSIONS = {'.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.webp', '.mp4', '.mp3'}
WEBPLUS_MAIN_ALIAS_RE = re.compile(r'^/(?:_s\d+(?:/_t\d+)?|_t\d+)/main\.(?:htm|psp)$')


def extension(url: str) -> str:
    path = urlparse(url).path.lower()
    if '.' not in path:
        return ''
    return '.' + path.rsplit('.', 1)[-1]


def same_domain(url: str, base_url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme.lower() not in {'http', 'https'}:
        return False
    host = parsed.netloc.lower()
    base = urlparse(base_url).netloc.lower()
    return host == base or host.endswith('.' + base)


def classify_url(url: str, base_url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme.lower() not in {'http', 'https'}:
        return 'non_http_link'
    if '{' in parsed.path or '}' in parsed.path:
        return 'template_placeholder_link'
    ext = extension(url)
    if ext in ATTACHMENT_EXTENSIONS:
        return 'attachment_file'
    if ext in STATIC_EXTENSIONS:
        return 'static_asset'
    if not same_domain(url, base_url):
        return 'external_link'
    path = parsed.path.lower()
    if 'showoriginalimg.html' in path or '/fckeditor.html' in path:
        return 'editor_helper_page'
    if path.rstrip('/') == '/_redirect':
        return 'redirect_link'
    if path.endswith('/list.htm') or path.endswith('/list.psp') or ('/list' in path and path.endswith(('.htm', '.psp'))):
        return 'section_list_page'
    if path.endswith('/page.htm') or path.endswith('/page.psp'):
        return 'detail_article_page'
    if WEBPLUS_MAIN_ALIAS_RE.fullmatch(path) or path.rstrip('/') == '/en' or re.fullmatch(r'/[a-z0-9_-]+/', path):
        return 'site_entry_alias'
    if path in {'/', '', '/main.htm', '/main.psp'}:
        return 'homepage'
    return 'same_domain_page_unknown'
