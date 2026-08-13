"""Kasus serupa — keluhan lapangan → part yang NYATA dipasang (dataset klaim).

Invarian yang dikunci di sini semuanya lahir dari cacat yang BENAR-BENAR
terjadi saat membangun tool ini (2026-08-13), bukan skenario karangan:

1. Klaim DIBATALKAN tak boleh jadi dasar bukti — 18% baris part berasal dari
   klaim yang justru ditolak.
2. ⛔ REGRESI TERUKUR DUA KALI: ambang kelayakan pernah dihitung dari skor
   BERBOBOT, sehingga kueri PN polos menyusut 359 → 82 kasus dan km median
   melenceng 16.642 → 2.032. Ambang WAJIB memakai massa IDF polos; bobot
   gejala hanya untuk MENGURUTKAN.
3. Keyword kamus multi-kata wajib cocok UTUH — 'air conditioning compressor'
   tak boleh menyeret 'Air dryer' (komponen REM) lewat token 'air' sendirian.
4. Kata KATEGORI polos ('suspensi') hanya dijembatani UMBRELLA_KATEGORI;
   tanpa itu 'dudukan karet suspensi' menaruh dudukan MESIN di peringkat 1.
5. km dipakai MEDIAN, bukan rata-rata (satu unit ber-km ekstrem tak boleh
   menggeser saran perawatan).
6. Gerbang 'ai_garansi': pembeli TIDAK PERNAH (fail-closed).
"""
import gzip
import json

import pytest

from app.services import ai_assistant as ai
from app.services import knowledge_util, sinonim, warranty_kasus as wk

ADMIN = {"username": "admin", "role": "admin"}
PEMBELI = {"username": "toko1", "role": "pembeli"}


def _klaim(no, gejala, parts, *, status="s-ro-status-js", km=10000, total=100.0):
    return {
        "no_wo": no, "ro_id": "id" + no, "frame": "SJ00" + no[-2:],
        "model": "10031050", "tanggal": "2026-01-01", "tanggal_audit": "2026-01-02",
        "km": km, "gejala": gejala, "tindakan": "Replacing", "catatan": None,
        "status": "Selesai", "status_code": status, "durasi_jam": 1.0,
        "pelapor": "x", "mekanik": "y", "bengkel": "MAS",
        "part": parts, "jasa": [], "oli": [], "tambahan": [],
        "biaya": {"total_cny": total, "mata_uang": "CNY"},
    }


def _part(pn, nama, *, mode="Break", qty=1, harga=10.0):
    return {"pn": pn, "nama": nama, "nama_cn": None, "qty": qty,
            "harga_cny": harga, "total_cny": harga * qty, "jenis": "ganti",
            "mode_gagal_kode": "048", "mode_gagal": mode,
            "penanggung_jawab": "QX", "supplier_part_lama": None,
            "jenis_garansi": None, "klaim_berulang": False}


@pytest.fixture
def dunia(tmp_path, monkeypatch):
    """Dataset sintetis di tmp_path + kamus sinonim dikontrol penuh."""
    klaim = []
    # 3 klaim SAH memuat PN-A, masing-masing km jauh berbeda (uji median).
    for i, km in enumerate([10_000, 20_000, 90_000], start=1):
        klaim.append(_klaim(f"WO{i:02}", "Suspension rubber mount is broke",
                            [_part("AZ-A", "Suspension rubber mount assembly")], km=km))
    # Klaim DIBATALKAN yang juga memuat PN-A — tak boleh ikut terhitung.
    klaim.append(_klaim("WO90", "Suspension rubber mount is broke",
                        [_part("AZ-A", "Suspension rubber mount assembly")],
                        status="s-ro-status-zf", km=1))
    # Kasus AC vs Air dryer (rem) — penguji aturan frasa utuh.
    klaim.append(_klaim("WO20", "AC not cool",
                        [_part("AZ-AC", "Air conditioning compressor")], km=30_000))
    klaim.append(_klaim("WO21", "air dryer is leakage",
                        [_part("AZ-DRY", "Air dryer")], km=40_000))
    # Dudukan MESIN — penguji kata kategori 'suspensi'.
    klaim.append(_klaim("WO30", "engine mount broken",
                        [_part("AZ-ENG", "Front rubber support of engine")], km=50_000))

    payload = {"dibuat": "2026-08-13 00:00 UTC", "total_klaim": len(klaim),
               "klaim": klaim, "kamus_mode_gagal": {}, "kamus_penanggung_jawab": {}}
    d = tmp_path / "warranty"
    d.mkdir()
    with gzip.open(d / "warranty_klaim.json.gz", "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    class _S:
        data_path = tmp_path

    monkeypatch.setattr(wk, "get_settings", lambda: _S())
    knowledge_util._LOAD_CACHE.clear()
    wk._index_cache.clear()
    # Kamus: 'ac tidak dingin' → frasa katalog multi-kata.
    monkeypatch.setattr(sinonim, "entries", lambda: [
        {"grup": "gejala:ac", "triggers": ["ac tidak dingin"],
         "keywords": ["air conditioning compressor"]},
    ])
    return tmp_path


# ── dasar bukti ──────────────────────────────────────────────────────
def test_klaim_dibatalkan_dibuang(dunia):
    r = wk.cari("AZ-A")
    assert r["found"] is True
    assert r["dari_total_kasus"] == 6            # 7 klaim − 1 dibatalkan
    pn = {p["pn"]: p for p in r["part_disarankan"]}
    assert pn["AZ-A"]["kali_dipasang"] == 3      # bukan 4


def test_kueri_pn_polos_ambil_semua_kasusnya(dunia):
    """⛔ REGRESI: ambang pernah memangkas himpunan PN jadi bias (359 → 82)."""
    r = wk.cari("AZ-A")
    assert r["jumlah_kasus_mirip"] == 3
    assert r["part_disarankan"][0]["pn"] == "AZ-A"


def test_km_median_bukan_rata_rata(dunia):
    """km 10rb/20rb/90rb → median 20rb (rata-rata 40rb akan menyesatkan)."""
    r = wk.cari("AZ-A")
    pn = {p["pn"]: p for p in r["part_disarankan"]}
    assert pn["AZ-A"]["km_median"] == 20_000


# ── presisi pencocokan ───────────────────────────────────────────────
def test_frasa_kamus_wajib_utuh_bukan_token_lepas(dunia):
    """'air conditioning compressor' tak boleh menyeret 'Air dryer' (REM)."""
    r = wk.cari("ac tidak dingin")
    pns = {p["pn"] for p in r["part_disarankan"]}
    assert "AZ-AC" in pns
    assert "AZ-DRY" not in pns


def test_kata_kategori_dijembatani(dunia):
    """'suspensi' (kata polos) → 'suspension'; dudukan MESIN tak boleh menang."""
    r = wk.cari("dudukan karet suspensi patah")
    assert r["found"] is True
    assert r["part_disarankan"][0]["pn"] == "AZ-A"


def test_istilah_asing_dijawab_jujur(dunia):
    r = wk.cari("kapal selam bocor")
    assert r["found"] is False
    assert "tak" in (r["catatan"] or "").lower()


# ── ketahanan & gerbang ──────────────────────────────────────────────
def test_dataset_kosong_bukan_error(tmp_path, monkeypatch):
    class _S:
        data_path = tmp_path
    monkeypatch.setattr(wk, "get_settings", lambda: _S())
    knowledge_util._LOAD_CACHE.clear()
    wk._index_cache.clear()
    r = wk.cari("aki soak")
    assert r["found"] is False and "error" not in r


def test_gejala_kosong_minta_diperjelas(dunia):
    assert wk.cari("  ")["found"] is False


def test_tool_admin_boleh(dunia, monkeypatch):
    monkeypatch.setattr(ai, "_boleh_ai", lambda user, key: False)
    r = ai._DISPATCH["kasus_serupa"]({"gejala": "AZ-A"}, ADMIN)
    assert r["found"] is True


def test_tool_pembeli_ditolak(dunia, monkeypatch):
    monkeypatch.setattr(ai, "_boleh_ai", lambda user, key: False)
    r = ai._DISPATCH["kasus_serupa"]({"gejala": "AZ-A"}, PEMBELI)
    assert "error" in r and not r.get("part_disarankan")


def test_spec_hanya_untuk_yang_berhak(monkeypatch):
    monkeypatch.setattr(ai, "_boleh_ai", lambda user, key: False)
    nama = lambda u: [s["function"]["name"] for s in ai._tool_specs(u)]
    assert "kasus_serupa" in nama(ADMIN)
    assert "kasus_serupa" not in nama(PEMBELI)


def test_pn_kasus_serupa_tetap_wajib_verifikasi_epc():
    """PN dari kasus unit LAIN belum terikat ke unit penanya → JANGAN
    dikecualikan dari guard EPC-FIRST."""
    assert "kasus_serupa" not in ai._KLAIM_TOOLS
