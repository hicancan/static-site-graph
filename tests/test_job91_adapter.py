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
    assert "南京邮电大学春季招聘会" in (tmp_path / "index" / "detail_pages.jsonl").read_text(encoding="utf-8")
