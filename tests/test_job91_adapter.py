from __future__ import annotations

from pathlib import Path

from sitegraph.crawl_job91 import crawl_job91_site


class FakeClient:
    def __init__(self, base_url: str, timeout: int) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def get_json(self, path, params=None):
        if path.endswith("getWzid"):
            return {"success": True, "code": 200, "result": "WZID"}
        if path.endswith("getXwlm"):
            return {
                "success": True,
                "code": 200,
                "result": [
                    {
                        "lmid": "root",
                        "lmmc": "就业信息",
                        "lmlx": "1",
                        "model": [{"lmid": "fair", "lmmc": "招聘会", "lmlx": "7", "model": []}],
                    }
                ],
            }
        if path.endswith("getLbsj"):
            return {
                "success": True,
                "code": 200,
                "result": [
                    {
                        "zphid": "1001",
                        "zphmc": "南京邮电大学春季招聘会",
                        "jbkssj": "2026-04-15",
                        "jbcd": "仙林校区体育馆",
                        "gljg": "南京邮电大学",
                    }
                ],
            }
        raise AssertionError(path)


class PagedFakeClient(FakeClient):
    def get_json(self, path, params=None):
        if not path.endswith("getLbsj"):
            return super().get_json(path, params)
        page = int((params or {}).get("page", 1))
        if page == 1:
            return {
                "success": True,
                "code": 200,
                "result": [
                    {"zphid": "1001", "zphmc": "第一页招聘会", "jbkssj": "2026-04-15", "jbcd": "仙林校区"}
                ],
            }
        if page == 2:
            return {
                "success": True,
                "code": 200,
                "result": [
                    {"zphid": "1002", "zphmc": "第二页招聘会", "jbkssj": "2026-04-16", "jbcd": "仙林校区"}
                ],
            }
        return {"success": True, "code": 200, "result": []}


def test_job91_adapter_writes_current_sitegraph_contract(tmp_path, monkeypatch):
    monkeypatch.setattr("sitegraph.crawl_job91.Job91ApiClient", FakeClient)
    cfg = {
        "site": {
            "id": "job91",
            "name": "就业信息网",
            "base_url": "https://njupt.91job.org.cn/",
            "domain": "njupt.91job.org.cn",
            "adapter": "job91_api",
        },
        "crawl_policy": {"attachment_policy": "metadata_only", "external_link_policy": "record_only"},
    }

    totals = crawl_job91_site(cfg, out_root=tmp_path / "index")

    assert totals["detail_pages"] == 1
    assert (tmp_path / "index" / "manifest.json").exists()
    assert (tmp_path / "index" / "coverage_report.json").exists()
    assert "南京邮电大学春季招聘会" in (tmp_path / "index" / "detail_pages.jsonl").read_text(encoding="utf-8")


def test_job91_adapter_crawls_api_pages_until_terminal_page(tmp_path, monkeypatch):
    monkeypatch.setattr("sitegraph.crawl_job91.Job91ApiClient", PagedFakeClient)
    cfg = {
        "site": {
            "id": "job91",
            "name": "就业信息网",
            "base_url": "https://njupt.91job.org.cn/",
            "domain": "njupt.91job.org.cn",
            "adapter": "job91_api",
            "audit_evidence_ref": "reports/audit_evidence.md",
        },
        "crawl_policy": {
            "attachment_policy": "metadata_only",
            "external_link_policy": "record_only",
            "job91_items_per_section": 1,
            "job91_max_pages_per_section": 5,
        },
    }

    totals = crawl_job91_site(cfg, out_root=tmp_path / "index")

    coverage = (tmp_path / "index" / "coverage_report.json").read_text(encoding="utf-8")
    assert totals["list_pages"] == 4
    assert totals["detail_pages"] == 2
    assert "第二页招聘会" in (tmp_path / "index" / "detail_pages.jsonl").read_text(encoding="utf-8")
    assert '"termination_reason": "empty_page"' in coverage
    assert '"coverage_status": "complete"' in coverage
