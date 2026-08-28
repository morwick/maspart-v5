"""harga_sims menerima BANYAK PN (audit ai_chat_log 2026-08-28: 133 PN butuh
12 giliran 'lanjut' karena tool cuma terima satu PN)."""
import pytest

from app.services import ai_assistant as ai, ai_export, harga, sims

MAS = {"username": "mas", "role": "user"}
STAF = {"username": "budi", "role": "user"}

_HARGA = {"WG9925520270": 123.5, "AZ100": 7.0}


@pytest.fixture
def dunia(monkeypatch):
    monkeypatch.setattr(ai, "_can_sims", lambda u: u["username"] in ("mas", "admin"))
    monkeypatch.setattr(harga, "get_rate", lambda force=False: (2200.0, None))
    monkeypatch.setattr(sims, "get_price",
                        lambda pn, force_refresh=False: (_HARGA.get(pn), None if pn in _HARGA else "Harga tidak ditemukan"))
    return monkeypatch


def test_array_banyak_pn_satu_panggilan(dunia):
    r = ai._t_harga_sims({"part_number": ["WG9925520270", "AZ100", "XXNOTEXIST"]}, MAS)
    assert r["found"] and r["jumlah"] == 3 and r["ketemu"] == 2
    assert r["mata_uang"] == "CNY"
    assert [h["part_number"] for h in r["hasil"]] == ["WG9925520270", "AZ100"]
    assert r["hasil"][0]["harga_cny"] == 123.5 and "harga_idr" not in r["hasil"][0]
    assert r["tidak_ditemukan"] == ["XXNOTEXIST"]
    assert "export_id" not in r          # ≤15 PN & tanpa excel → tabel biasa


def test_string_dipisah_spasi_dan_konversi_idr(dunia):
    r = ai._t_harga_sims({"part_number": "WG9925520270 AZ100", "konversi_idr": True}, MAS)
    assert r["mata_uang"] == "IDR" and r["kurs_cny_idr"] == 2200.0
    assert r["hasil"][0]["harga_idr"] == round(123.5 * 2200)


def test_excel_otomatis_di_atas_ambang_dan_saat_diminta(dunia):
    pns = [f"PN{i:06d}" for i in range(20)]
    r = ai._t_harga_sims({"part_number": pns + ["AZ100"]}, MAS)
    assert r["found"] and r["export_id"] and r["filename"].endswith(".xlsx")
    assert r["jumlah_baris"] == 21          # ketemu + nihil semua masuk file
    assert ai_export.generic_excel(r["export_id"])
    r2 = ai._t_harga_sims({"part_number": ["WG9925520270", "AZ100"], "excel": True}, MAS)
    assert r2["export_id"]
    assert "harga_sims" in ai._EXCEL_CARD_TOOLS if hasattr(ai, "_EXCEL_CARD_TOOLS") else True


def test_plafon_100_pn_jujur(dunia):
    pns = [f"PN{i:06d}" for i in range(105)]
    r = ai._t_harga_sims({"part_number": pns}, MAS)
    assert r["jumlah"] == 100 and "5 PN terakhir TIDAK dicek" in r["catatan"]


def test_skalar_tetap_bentuk_lama(dunia):
    r = ai._t_harga_sims({"part_number": "WG9925520270"}, MAS)
    assert r["part_number"] == "WG9925520270" and r["harga_cny"] == 123.5
    assert "hasil" not in r and "found" not in r
    # PN tanpa harga → found=False (telemetri 'nf', bukan sukses)
    r2 = ai._t_harga_sims({"part_number": "XXNOTEXIST"}, MAS)
    assert r2["found"] is False and r2["harga_cny"] is None


def test_staf_ditolak_juga_untuk_array(dunia):
    r = ai._t_harga_sims({"part_number": ["WG9925520270", "AZ100"]}, STAF)
    assert r.get("denied") and "hasil" not in r


def test_spec_terdaftar_array():
    spec = next(t for t in ai._tool_specs(MAS) if t["function"]["name"] == "harga_sims")
    p = spec["function"]["parameters"]["properties"]
    assert "array" in p["part_number"]["type"] and "excel" in p
