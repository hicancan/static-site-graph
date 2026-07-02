from __future__ import annotations

from sitegraph.classify import classify_url


def test_main_psp_is_homepage() -> None:
    assert classify_url("https://demo.example.edu/main.psp", "https://demo.example.edu/") == "homepage"


def test_webplus_page_alias_is_detail() -> None:
    assert classify_url("https://demo.example.edu/50/ae/c1415a20654/page.htm", "https://demo.example.edu/") == "detail_article_page"
    assert classify_url("https://demo.example.edu/_s103/_t326/0c/4d/c5262a68685/page.psp", "https://demo.example.edu/") == "detail_article_page"


def test_webplus_template_helpers_are_explicit() -> None:
    assert classify_url("https://demo.example.edu/_s142/main.psp", "https://demo.example.edu/") == "site_entry_alias"
    assert classify_url("https://demo.example.edu/_t24/main.htm", "https://demo.example.edu/") == "site_entry_alias"
    assert classify_url("https://demo.example.edu/20d/", "https://demo.example.edu/") == "site_entry_alias"
    assert (
        classify_url(
            "https://demo.example.edu/_ueditor/dialogs/showOriginalImg.html?img=/_upload/article/images/a/b/c_d.jpg",
            "https://demo.example.edu/",
        )
        == "editor_helper_page"
    )
    assert classify_url("https://demo.example.edu/4808/{站点URL}", "https://demo.example.edu/") == "template_placeholder_link"


def test_webplus_malformed_office_upload_is_attachment() -> None:
    assert (
        classify_url(
            "https://demo.example.edu/_upload/article/1/9f/f8/file.doc.x",
            "https://demo.example.edu/",
        )
        == "attachment_file"
    )
