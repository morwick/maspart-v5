"""ASISTEN: gambar_exploded TANPA nomor rangka (2026-08-04, perintah pemilik).

Dulu tool ini menolak mentah ("Sebutkan nomor rangka/VIN") padahal jalur
lintas-model (`exploded_view`) sudah dipakai halaman detail part & Batch
Download — user chat saja yang tak kebagian.

Yang dijaga:
  • tanpa rangka TIDAK ditolak lagi; gambar tetap disiapkan inline (image_id);
  • peringatan LINTAS-MODEL wajib ikut (gambar bukan milik unit tertentu);
  • cache exploded_view yang dipakai (dibagi dgn Batch Download), dan ⛔ TIDAK
    mengambil gerbang batch — chat tak boleh membuat Batch Download ditolak;
  • multi-PN tanpa rangka dipotong TERBUKA di 2 PN (dingin bisa ±94 dtk/PN);
  • jalur per-VIN yang lama tidak berubah.
Zero-network: exploded_view & stash di-mock.
"""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}

_FIGURE = {
    "found": True, "part_number": "AZ9725520683", "svg": "fig-123.svg",
    "balon": 7, "figure_pn": "WG9000", "figure_nama": "Rear suspension",
    "nama_item": "Rubber support", "jumlah_item": 24,
    "sumber_model": "ZZ3257V404JF1", "jumlah_model_pemakai": 312,
    "catatan": "lintas-model", "png": b"PNG",
}


@pytest.fixture
def patched(monkeypatch):
    jejak = {"build": 0, "gerbang": 0}

    def _fig(pn, **kw):
        jejak["build"] += 1
        return dict(_FIGURE, part_number=pn.upper())

    def _gerbang(fn, *a, **kw):          # tak boleh dipakai jalur chat
        jejak["gerbang"] += 1
        return fn(*a, **kw)

    monkeypatch.setattr(ai.exploded_view, "figure_untuk_pn", _fig)
    monkeypatch.setattr(ai.exploded_view, "bangun_dengan_gerbang", _gerbang)
    monkeypatch.setattr(ai.ai_export, "stash_builder",
                        lambda judul, builder, ext="xlsx": ("img1", "exploded.png"))
    return jejak


def test_tanpa_rangka_tetap_jalan(patched):
    r = ai._t_gambar_exploded({"pn": "AZ9725520683"}, ADMIN)
    assert r["found"] and r["tanpa_rangka"] is True
    assert r["gambar"][0]["image_id"] == "img1" and r["gambar"][0]["balon"] == 7
    assert r["sumber_model"] == "ZZ3257V404JF1"
    # peringatan lintas-model + arahan per-VIN wajib ada di catatan utk model
    assert "lintas-model" in r["catatan"] and "nomor rangka" in r["catatan"]


def test_tidak_mengambil_gerbang_batch(patched):
    """Gerbang = milik Batch Download. Satu PN dari chat memegangnya = user lain
    yang menekan Batch Download ditolak 'sedang sibuk'."""
    ai._t_gambar_exploded({"pn": "AZ9725520683"}, ADMIN)
    assert patched["gerbang"] == 0 and patched["build"] == 1


def test_balon_diminta_menimpa_balon_pn(patched):
    r = ai._t_gambar_exploded({"pn": "AZ9725520683", "balon": 3}, ADMIN)
    assert r["gambar"][0]["balon"] == 3


def test_figure_tak_ada_disarankan_per_vin(monkeypatch, patched):
    monkeypatch.setattr(ai.exploded_view, "figure_untuk_pn",
                        lambda pn, **kw: {"found": False, "part_number": pn,
                                          "alasan": "Figure tidak ditemukan."})
    r = ai._t_gambar_exploded({"pn": "XX999"}, ADMIN)
    assert r["found"] is False
    assert "Figure tidak ditemukan." in r["error"] and "nomor rangka" in r["saran"]


def test_epc_error_dijawab_jujur(monkeypatch, patched):
    def _boom(pn, **kw):
        raise RuntimeError("EPC mati")
    monkeypatch.setattr(ai.exploded_view, "figure_untuk_pn", _boom)
    r = ai._t_gambar_exploded({"pn": "AZ9725520683"}, ADMIN)
    assert r["found"] is False and "Coba lagi" in r["error"]


def test_pn_kosong_ditolak(patched):
    assert "Part Number" in ai._t_gambar_exploded({"pn": ""}, ADMIN)["error"]


def test_multi_pn_tanpa_rangka_dipotong_terbuka(patched):
    r = ai._t_gambar_exploded({"pn": ["AZ1", "AZ2", "AZ3", "AZ4"]}, ADMIN)
    assert r["pns"] == ["AZ1", "AZ2"]                 # hanya 2 diproses
    assert r["pn_belum_diproses"] == ["AZ3", "AZ4"]   # sisanya DILAPORKAN, tak diam2
    assert "BELUM diproses" in r["catatan"] and patched["build"] == 2


def test_dengan_rangka_tetap_jalur_per_vin(monkeypatch, patched):
    """Rangka disebut → jalur lama (per-VIN), bukan lintas-model."""
    monkeypatch.setattr(ai, "_exploded_via_reverse",
                        lambda rangka, pn, balon: {"found": True, "per_vin": True})
    r = ai._t_gambar_exploded({"rangka": "RT108966", "pn": "AZ9725520683"}, ADMIN)
    assert r.get("per_vin") is True and patched["build"] == 0


def test_spec_rangka_tak_wajib_lagi():
    spec = next(s for s in ai._tool_specs(ADMIN)
                if s["function"]["name"] == "gambar_exploded")
    assert spec["function"]["parameters"]["required"] == ["pn"]
