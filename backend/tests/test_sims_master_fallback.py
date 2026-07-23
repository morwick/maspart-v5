"""Master SIMS 670rb sbg jaring terakhir (P5 paket pintar 2026-07-23):
- fetch_part_info_by_name: pageDealer filter partName LIKE (terverifikasi live).
- sims.search_master_by_name: memo TTL 1 jam.
- cari_part: lokal+PN-fallback+stok_lokal nihil → cari NAMA di master (maks 2
  keyword EN), record_miss di-skip bila master kena.
- detail_part: PN miss lokal tapi SAHIH di master → anotasi 'master_sims'
  (found tetap False — jujur, nf).
Seluruh jaringan di-mock. conftest men-no-op search_master_by_name utk modul
LAIN; modul ini mock sendiri."""
import json

import pytest
import requests as real_requests

from app.services import ai_assistant as ai
from app.services import search_log, sims
from shared import sims_fetcher

ADMIN = {"role": "admin", "username": "t"}


@pytest.fixture(autouse=True)
def _misses_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(search_log, "_path", lambda: tmp_path / "misses.json")
    search_log._state["data"] = None


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise real_requests.HTTPError(str(self.status_code))


ROW = {"partCode": "wg1642110023", "partName": "Mirror assembly", "brandName": "HOWO"}


@pytest.mark.parametrize("body", [
    [ROW],                                      # list polos
    {"data": {"rows": [ROW]}},                  # data.rows
    {"records": [ROW]},                         # records
])
def test_fetch_by_name_parser_toleran(monkeypatch, body):
    monkeypatch.setattr(sims_fetcher, "_get_token", lambda: "Bearer tok")
    monkeypatch.setattr(sims_fetcher.requests, "get",
                        lambda *a, **k: _Resp(200, body))
    out = sims_fetcher.fetch_part_info_by_name("mirror")
    assert out == [{"partCode": "WG1642110023", "partName": "Mirror assembly",
                    "brandName": "HOWO"}]


def test_fetch_by_name_401_login_ulang(monkeypatch):
    seq = [_Resp(401, {}), _Resp(200, {"data": {"rows": [ROW]}})]
    monkeypatch.setattr(sims_fetcher, "_get_token", lambda: "Bearer tok")
    reset = {"n": 0}
    monkeypatch.setattr(sims_fetcher, "_reset_token",
                        lambda: reset.__setitem__("n", reset["n"] + 1))
    monkeypatch.setattr(sims_fetcher.requests, "get",
                        lambda *a, **k: seq.pop(0))
    out = sims_fetcher.fetch_part_info_by_name("mirror")
    assert len(out) == 1 and reset["n"] == 1


def test_fetch_by_name_gagal_jaringan_aman(monkeypatch):
    monkeypatch.setattr(sims_fetcher, "_get_token", lambda: "Bearer tok")

    def boom(*a, **k):
        raise real_requests.ConnectionError("mati")

    monkeypatch.setattr(sims_fetcher.requests, "get", boom)
    monkeypatch.setattr(sims_fetcher.time, "sleep", lambda s: None)
    assert sims_fetcher.fetch_part_info_by_name("mirror") == []


def test_fetch_by_name_query_pendek_ditolak(monkeypatch):
    hit = {"n": 0}
    monkeypatch.setattr(sims_fetcher.requests, "get",
                        lambda *a, **k: hit.__setitem__("n", hit["n"] + 1))
    assert sims_fetcher.fetch_part_info_by_name("ab") == []
    assert hit["n"] == 0


def test_search_master_memo(monkeypatch):
    hit = {"n": 0}

    def fetch(name, page_size=8):
        hit["n"] += 1
        return [ROW]

    monkeypatch.setattr(sims, "_SIMS_OK", True)
    monkeypatch.setattr(sims._sf, "fetch_part_info_by_name", fetch, raising=False)
    sims._master_name_memo.clear()
    assert sims.search_master_by_name("mirror") == [ROW]
    assert sims.search_master_by_name("MIRROR ") == [ROW]   # normalisasi + memo
    assert hit["n"] == 1
    # TTL lewat → fetch ulang
    ts, rows = sims._master_name_memo["mirror"]
    sims._master_name_memo["mirror"] = (ts - 7200, rows)
    sims.search_master_by_name("mirror")
    assert hit["n"] == 2


def test_cari_part_jaring_terakhir_master(monkeypatch):
    monkeypatch.setattr(sims, "available", lambda: True)
    monkeypatch.setattr(sims, "get_part_info", lambda pn, force_refresh=False: {})
    dicari = []

    def master(name, limit=8):
        dicari.append(name)
        return [ROW]

    monkeypatch.setattr(sims, "search_master_by_name", master)
    res = ai._t_cari_part({"query": "zzqx spoiler nihil"}, ADMIN)
    assert res["jumlah_part_unik"] == 0
    assert res["found"] is True
    assert res["hasil_master_sims"][0]["part_number"] == "WG1642110023"
    assert "master" in (res["catatan"] or "").lower()
    assert "JANGAN klaim" in res["catatan"]
    assert len(dicari) <= 2                     # maks 2 keyword
    # master kena → bukan celah kamus → miss TIDAK dicatat
    assert search_log.top_misses() == []


def test_cari_part_master_nihil_tetap_miss(monkeypatch):
    monkeypatch.setattr(sims, "available", lambda: True)
    monkeypatch.setattr(sims, "get_part_info", lambda pn, force_refresh=False: {})
    monkeypatch.setattr(sims, "search_master_by_name", lambda name, limit=8: [])
    res = ai._t_cari_part({"query": "zzqx spoiler nihil"}, ADMIN)
    assert res["found"] is False and res["hasil_master_sims"] == []
    assert [m["query"] for m in search_log.top_misses()] == ["zzqx spoiler nihil"]


def test_cari_part_pn_dikenal_sims_master_tak_dipanggil(monkeypatch):
    """hasil_sims (PN dikenal SIMS) sudah cukup → master by-nama tak disentuh."""
    monkeypatch.setattr(sims, "available", lambda: True)
    monkeypatch.setattr(sims, "get_part_info",
                        lambda pn, force_refresh=False:
                        {"partName": "Air compressor"} if pn == "1014167092" else {})
    dicari = []
    monkeypatch.setattr(sims, "search_master_by_name",
                        lambda name, limit=8: dicari.append(name) or [])
    res = ai._t_cari_part({"query": "1014167092"}, ADMIN)
    assert res["hasil_sims"] and dicari == []


def test_detail_part_pn_sahih_di_master(monkeypatch):
    monkeypatch.setattr(sims, "available", lambda: True)
    monkeypatch.setattr(sims, "get_part_info",
                        lambda pn, force_refresh=False: {"partName": "Timing gear",
                                                         "brandName": "SINOTRUK"})
    res = ai._t_detail_part({"part_number": "ZZ9999XCOBA"}, ADMIN)
    assert res["found"] is False                    # tetap jujur tidak dijual
    assert res["master_sims"]["part_name"] == "Timing gear"
    assert "master SIMS" in res["pesan"]


def test_detail_part_sims_error_aman(monkeypatch):
    monkeypatch.setattr(sims, "available", lambda: True)

    def boom(pn, force_refresh=False):
        raise RuntimeError("mati")

    monkeypatch.setattr(sims, "get_part_info", boom)
    res = ai._t_detail_part({"part_number": "ZZ9999XCOBA"}, ADMIN)
    assert res["found"] is False and "master_sims" not in res
