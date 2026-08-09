"""Jadwal servis KM + kapasitas cairan truk Sinotruk (PDF RESMI cnhtcgroup.com).

Menutup celah lama audit log: "berapa liter oli yg dibutuhkan jika service
40.000" (dulu dijawab taksiran berlabel 'bukan data resmi'), kapasitas COOLANT
(sebelumnya nihil di SIMS, EPC, dan EOL AI), serta oli transmisi & gardan
(sebelumnya hanya klaim vendor).

Yang paling mudah salah & karena itu dikunci:
 • satu item bisa punya BEBERAPA kapasitas ber-konteks (gardan: 18 L tengah,
   14,5 L belakang) — mengambil angka pertama saja = overfill gardan belakang;
 • rentang/varian ('40-45Liter', '12/12.5L') tak boleh menyusut jadi satu angka;
 • KM yang bukan interval resmi tak boleh dikira-kira diam-diam;
 • cakupan HOWO 371HP wajib ikut supaya tak digeneralisasi ke NX/SITRAK.
"""
import pytest

from app.services import jadwal_servis_truk as js
from app.services import ai_assistant as ai

USER = {"username": "budi", "role": "user"}


# ── dataset nyata ───────────────────────────────────────────────────────────
def test_dataset_tersedia_dan_ber_cakupan():
    assert js.available()
    m = js.meta()
    assert "371" in (m.get("cakupan") or "")
    assert "cnhtcgroup.com" in (m.get("url") or "")
    assert len(m.get("interval_km") or []) == 12
    assert m["interval_km"][0] == 2000 and m["interval_km"][-1] == 90000


def test_kapasitas_gardan_bawa_DUA_angka_ber_konteks():
    """Regresi: versi pertama hanya mengambil 18 L dan MEMBUANG 14,5 L gardan
    belakang — mekanik yang mengikutinya akan overfill."""
    g = js.cairan("oli gardan")
    assert g, "kapasitas gardan tak ketemu"
    kap = g[0]["kapasitas"]
    nilai = {k.get("liter") for k in kap}
    assert nilai == {18.0, 14.5}
    konteks = " ".join((k.get("konteks") or "") for k in kap).lower()
    assert "middle" in konteks and "rear" in konteks
    assert g[0]["ganti_tiap_km"] == 60000


def test_coolant_rentang_tidak_menyusut():
    c = js.cairan("coolant")
    assert c
    kap = c[0]["kapasitas"][0]
    assert kap["liter_min"] == 40.0 and kap["liter_maks"] == 45.0
    assert "ASTM" in c[0]["spesifikasi"].upper()


def test_transmisi_varian_pto_tidak_menyusut():
    """'12/12.5L' = tanpa/dengan PTO — dua-duanya harus terbawa."""
    t = js.cairan("transmisi")
    assert t
    kap = t[0]["kapasitas"][0]
    assert kap["liter_min"] == 12.0 and kap["liter_maks"] == 12.5


def test_sinonim_indonesia():
    assert js.cairan("air radiator") == js.cairan("coolant")
    assert js.cairan("persneling") == js.cairan("transmisi")
    assert js.cairan("kemudi") == js.cairan("steering")


def test_cairan_tanpa_filter_kembalikan_semua():
    assert len(js.cairan()) >= 6


# ── pekerjaan per KM ────────────────────────────────────────────────────────
def test_km_interval_resmi_persis():
    r = js.pada_km(34000)
    assert r["found"] and r["interval_persis"] is True
    assert r["km_interval"] == 34000
    nama = " ".join(x["item"].lower() for x in r["pekerjaan"])
    assert "engine oil" in nama and "coolant" in nama       # 34.000 km = ganti coolant


def test_km_bukan_interval_tidak_dikira_kira_diam_diam():
    """40.000 km bukan interval resmi — wajib ditandai, bukan dijawab seolah pas."""
    r = js.pada_km(40000)
    assert r["found"] and r["interval_persis"] is False
    assert r["km_interval"] == 34000


def test_km_di_bawah_servis_pertama_jujur():
    r = js.pada_km(500)
    assert r["found"] is False and r["alasan"] == "di_bawah_interval_pertama"


def test_pekerjaan_tak_memuat_periksa_rutin():
    r = js.pada_km(34000)
    assert all(x["kode"] != "I" for x in r["pekerjaan"])
    assert r["jumlah_item_periksa_rutin"] > 10


def test_km_tak_sah():
    assert js.pada_km("bukan angka")["alasan"] == "input"


# ── tool asisten ────────────────────────────────────────────────────────────
def test_tool_km_dan_cairan(monkeypatch):
    r = ai._t_jadwal_servis_truk({"km": 40000}, USER)
    assert r["servis"]["km_interval"] == 34000
    assert "371" in r["cakupan"]


def test_tool_catatan_larang_generalisasi():
    r = ai._t_jadwal_servis_truk({"cairan": "coolant"}, USER)
    c = r["catatan"]
    assert "JANGAN disodorkan sebagai" in c and "SITRAK" in c
    assert "jadwal_perawatan" in c            # arahkan alat berat ke tool lain
    assert "konteks" in c                      # ingatkan gardan tengah vs belakang


def test_tool_cairan_tak_ketemu_dilaporkan():
    r = ai._t_jadwal_servis_truk({"cairan": "oli rem cakram"}, USER)
    assert r.get("cairan_tak_ketemu") == "oli rem cakram"


def test_tool_data_absen_bukan_vonis(monkeypatch):
    monkeypatch.setattr(ai.jadwal_servis_truk, "available", lambda: False)
    r = ai._t_jadwal_servis_truk({"km": 10000}, USER)
    assert r["_cek_tak_lengkap"] is True and "BUKAN bukti" in r["error"]


def test_tool_terdaftar():
    assert ai._DISPATCH["jadwal_servis_truk"] is ai._t_jadwal_servis_truk
    assert "jadwal_servis_truk" in [s["function"]["name"] for s in ai._tool_specs(USER)]


# ── parser builder ──────────────────────────────────────────────────────────
def _builder():
    import importlib.util
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "tools" / "build_jadwal_servis_truk.py"
    spec = importlib.util.spec_from_file_location("build_jadwal_servis_truk", src)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_parser_kapasitas_ganda_dan_rentang():
    m = _builder()
    item = [
        {"kategori": "AXLE", "item": "MCY13 Differential gear oil",
         "catatan": "GL-5 85W/90; 18L(Middle axle) & 14.5L(Rear axle); replace 60,000km"},
        {"kategori": "COOLING", "item": "Coolant", "catatan": "40-45Liter; ASTMD3306"},
        {"kategori": "TRANS", "item": "Transmission oil", "catatan": "GL-5 85W/90; 12/12.5L"},
        {"kategori": "X", "item": "Tanpa angka", "catatan": "cek visual"},
    ]
    out = m.cairan_dari(item)
    assert len(out) == 3                       # item tanpa angka dibuang
    gardan = out[0]["kapasitas"]
    assert [k["liter"] for k in gardan] == [18.0, 14.5]
    assert gardan[0]["konteks"] == "Middle axle"
    assert out[0]["ganti_tiap_km"] == 60000
    assert out[1]["kapasitas"][0] == {"liter_min": 40.0, "liter_maks": 45.0}
    assert out[2]["kapasitas"][0] == {"liter_min": 12.0, "liter_maks": 12.5}


def test_parser_tak_tertipu_spesifikasi_oli():
    """'GL-5 85W/90' tak boleh terbaca sebagai liter (angka wajib diikuti 'L')."""
    m = _builder()
    out = m.cairan_dari([{"kategori": "X", "item": "Y", "catatan": "GL-5 85W/90 saja"}])
    assert out == []
