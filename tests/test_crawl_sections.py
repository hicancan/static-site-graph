from __future__ import annotations

from sitegraph.crawl_sections import discover_sections_from_homepage


def test_discover_sections_preserves_config_and_promotes_homepage_modules() -> None:
    base_url = 'https://demo.example.edu/'
    cfg = {
        'sections': [
            {
                'section_id': 'configured_notice',
                'name': 'Configured notices',
                'url': 'https://demo.example.edu/news/list.htm',
                'source': 'declared_section',
                'crawlable': True,
            }
        ],
        'crawl_policy': {'auto_discover_sections_from_homepage': True},
    }
    nav_nodes = [
        {
            'node_id': 'root',
            'label': '首页',
            'url': base_url,
            'target_type': 'homepage',
        },
        {
            'node_id': 'notice',
            'parent_id': 'root',
            'label': '通知公告',
            'url': 'https://demo.example.edu/news/list.htm',
            'target_type': 'section_list_page',
        },
        {
            'node_id': 'teaching',
            'parent_id': 'root',
            'label': '教学动态',
            'url': 'https://demo.example.edu/teaching/list.htm',
            'target_type': 'section_list_page',
        },
    ]
    homepage_modules = [
        {
            'module_id': 'teaching_module',
            'name': '教学动态',
            'url': base_url,
            'list_url': 'https://demo.example.edu/teaching/list.htm',
            'container_selector': '.teaching',
        }
    ]
    home_html = '''
    <html><body>
      <a href="/extra/list.htm">更多栏目</a>
      <a href="https://external.example.edu/list.htm">外部栏目</a>
    </body></html>
    '''

    sections = discover_sections_from_homepage(
        cfg,
        base_url=base_url,
        site_id='demo',
        nav_nodes=nav_nodes,
        homepage_modules=homepage_modules,
        home_html=home_html,
    )
    by_url = {section['url']: section for section in sections}

    assert by_url['https://demo.example.edu/news/list.htm']['section_id'] == 'configured_notice'
    assert by_url['https://demo.example.edu/news/list.htm']['source'] == 'declared_section'
    assert by_url['https://demo.example.edu/teaching/list.htm']['source'] == 'homepage_module'
    assert by_url['https://demo.example.edu/teaching/list.htm']['container_selector'] == '.teaching'
    assert by_url['https://demo.example.edu/extra/list.htm']['source'] == 'homepage_nav'
    assert 'https://external.example.edu/list.htm' not in by_url
