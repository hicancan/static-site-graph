def test_import():
    import sitegraph.cli  # noqa


def test_webplus_list_psp_and_redirect_classification():
    from sitegraph.classify import classify_url

    base_url = 'https://demo.example.edu/'

    assert classify_url('https://demo.example.edu/_s24/_t3618/1160%20/list.psp', base_url) == 'section_list_page'
    assert classify_url('https://demo.example.edu/_redirect?siteId=1&articleId=2', base_url) == 'redirect_link'


def test_webplus_pagination_metadata_with_nav_words():
    from sitegraph.extract import extract_pagination_metadata

    html = '<div class="wp_paging">每页 8 记录 总共 55 记录 第一页 &lt;&lt;上一页 下一页&gt;&gt; 尾页 页码 1/7 跳转到</div>'

    assert extract_pagination_metadata(html) == {
        'raw_text': '每页 8 记录 总共 55 记录 第一页 <<上一页 下一页>> 尾页 页码 1/7',
        'page_size': 8,
        'total_records': 55,
        'current_page': 1,
        'total_pages': 7,
    }
