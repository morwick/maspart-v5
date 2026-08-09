"""Sumber DTC komunitas SITRAK (CC BY 4.0) di store kanonik.

Latar: audit 2026-08-08 menemukan dataset github.com/STAS63-bit/sitrak-error-codes
(8.042 record) memuat 3.316 pasangan (SPN,FMI) yang TIDAK ada di seluruh koleksi
resmi kita — termasuk 3 kode yang BERULANG gagal dijawab di log produksi:
SPN 764 FMI 2 & SPN 744 FMI 5 (retarder Voith), SPN 520290 FMI 20 (Bosch).

Yang dikunci di sini adalah PAGAR-nya, bukan sekadar "datanya masuk":
 • sumber RESMI selalu menang — dataset komunitas hanya menambal lubang;
 • teks Rusia dibawa apa adanya di `deskripsi_ru` (⛔ builder TIDAK menerjemahkan
   lewat model — aturan pemilik: jangan bakar saldo), penerjemahan saat menjawab;
 • asisten WAJIB diberi tahu bahwa baris ini komunitas + atribusi megadata.pro.
"""
import pytest

from app.services import dtc_codes
from app.services import ai_assistant as ai


# ── isi store ───────────────────────────────────────────────────────────────
def test_sumber_sitrak_ada_di_store():
    assert dtc_codes.count("sitrak") > 0


def test_kode_yang_gagal_di_produksi_kini_terjawab():
    """Tiga kode nyata dari ai_chat_log yang dulu nihil."""
    for spn, fmi in ((764, 2), (744, 5), (520290, 20)):
        hit = dtc_codes.search_spn_fmi(spn, fmi, limit=3)
        assert hit, f"SPN {spn} FMI {fmi} masih nihil"
        cocok = [r for r in hit if r.get("spn") == spn and r.get("fmi") == fmi]
        assert cocok, f"SPN {spn} FMI {fmi} tak ada pasangan eksak"


def test_baris_sitrak_bawa_teks_rusia():
    baris = [r for r in dtc_codes.rows("sitrak") if r.get("deskripsi_ru")]
    assert baris, "tak ada baris sitrak ber-deskripsi_ru"
    # ~95% hanya Rusia → mayoritas 'deskripsi' (Indonesia/Inggris) kosong
    tanpa_id = sum(1 for r in dtc_codes.rows("sitrak") if not r.get("deskripsi"))
    assert tanpa_id > 0


def test_sumber_resmi_menang_atas_komunitas():
    """Pasangan yang sudah dipunyai sumber resmi TIDAK boleh diduplikasi sitrak."""
    resmi = {(r["spn"], r["fmi"]) for r in dtc_codes.rows()
             if r.get("sumber") != "sitrak"
             and r.get("spn") is not None and r.get("fmi") is not None}
    for r in dtc_codes.rows("sitrak"):
        assert (r["spn"], r["fmi"]) not in resmi, \
            f"pasangan {(r['spn'], r['fmi'])} diduplikasi sumber komunitas"


def test_setiap_baris_sitrak_ber_spn_dan_fmi():
    for r in dtc_codes.rows("sitrak"):
        assert r.get("spn") is not None and r.get("fmi") is not None


def test_skema_kanonik_punya_kolom_ru_untuk_semua_sumber():
    """Kolom baru harus ada di SEMUA baris (skema deterministik), bukan hanya sitrak."""
    for r in dtc_codes.rows()[:200]:
        assert "deskripsi_ru" in r


# ── pagar penyajian ────────────────────────────────────────────────────────
def test_tool_bawa_deskripsi_ru_dan_catatan_jujur(monkeypatch):
    monkeypatch.setattr(ai.sims_eol, "tanya", lambda *a, **k: None)
    r = ai._t_cari_kode_kesalahan({"spn": 764, "fmi": 2}, {"username": "x", "role": "user"})
    teks = str(r)
    assert "sitrak" in teks
    assert "deskripsi_ru" in teks
    catatan = r.get("catatan") or ""
    assert "KOMUNITAS" in catatan
    assert "megadata.pro" in catatan
    assert "BUKAN lembar diagnosa resmi" in catatan


def test_catatan_larang_karang_langkah_untuk_baris_komunitas(monkeypatch):
    monkeypatch.setattr(ai.sims_eol, "tanya", lambda *a, **k: None)
    r = ai._t_cari_kode_kesalahan({"spn": 744, "fmi": 5}, {"username": "x", "role": "user"})
    assert "JANGAN menyajikan langkah perbaikan seolah resmi" in (r.get("catatan") or "")


def test_kode_resmi_tetap_dijawab_sumber_resmi():
    """SPN 520264 FMI 11 punya kartu diagnosa resmi — jangan tergeser komunitas."""
    hit = dtc_codes.search_spn_fmi(520264, 11, limit=3)
    assert hit and hit[0].get("sumber") != "sitrak"
    assert hit[0].get("deskripsi")          # resmi = sudah Bahasa Indonesia


# ── normalisasi builder (tanpa jaringan) ───────────────────────────────────
def test_normalisasi_buang_baris_tanpa_spn():
    import importlib.util
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "tools" / "build_sitrak_dtc.py"
    spec = importlib.util.spec_from_file_location("build_sitrak_dtc", src)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    rows = m.normalisasi([
        {"spn": "764", "fmi": "2", "dtc": None, "system": "Voith", "description_ru": "A"},
        {"spn": None, "fmi": "3", "system": "X", "description_ru": "B"},   # tanpa SPN → buang
        {"spn": "764", "fmi": "2", "dtc": None, "system": "Voith", "description_ru": "A"},  # dedup
        {"spn": "100", "fmi": "1", "dtc": "p0100", "system": "Bosch",
         "description_en": "Oil pressure", "description_ru": "Давление"},
    ])
    assert [(r["spn"], r["fmi"]) for r in rows] == [(100, 1), (764, 2)]   # urut & dedup
    assert rows[0]["kode"] == "P0100"                                     # kode di-uppercase
    assert rows[0]["deskripsi_en"] == "Oil pressure"
