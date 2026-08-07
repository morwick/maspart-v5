"""Tiga pinggiran router yang membuat model salah paham hasil tool (p7).

  (3) _tool_fail_kind: error INFRA tanpa kata 'jaringan' divonis 'nf' — model
      diberi tahu "data memang tidak ada" padahal tak ada yang pernah dicek.
      Kasus produksinya paling telanjang di jalur Accurate: hasilnya
      {"tersedia": False, "pesan": "Sesi Accurate kadaluarsa …"} — TANPA key
      `error` sama sekali, jadi klasifikasi lama tak punya apa pun untuk dibaca.

  (4) _trim_old_tool_messages: stub ronde lama kosong total. Grounding tetap
      menerima PN-nya, jadi model boleh menyebut PN yang pasangan unitnya sudah
      ia lupakan — persis kombinasi yang melahirkan jawaban percaya diri & salah.

 (12) _LOOKUP_GAGAL_NOTE: nota disuntik SEKALI per giliran tanpa menyebut tool
      mana yang gagal. Dengan 5 tool dipanggil, model tak punya cara tahu.
"""
import json

import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}


# ── (3) err vs nf ────────────────────────────────────────────────────
@pytest.mark.parametrize("result,kind", [
    # INFRA yang dulu salah divonis 'nf' — semua string di bawah ADA di kode.
    ({"tersedia": False, "pesan": "Sesi Accurate kadaluarsa — perlu diperbarui admin."}, "err"),
    ({"tersedia": False, "pesan": "Integrasi Accurate belum aktif (sesi belum diatur)."}, "err"),
    ({"tersedia": False, "pesan": "Accurate tak dapat diakses: boom"}, "err"),
    ({"found": False, "error": "SIMS tidak merespons — coba lagi sebentar lagi."}, "err"),
    ({"found": False, "error": "Accurate belum terkonfigurasi/aktif."}, "err"),
    ({"found": False, "error": "Indeks stok per-gudang sedang disiapkan — coba lagi beberapa menit."}, "err"),
    ({"found": False, "error": "Accurate bermasalah saat mencari 'X'"}, "err"),
    ({"found": False, "error": "Gagal mengambil katalog unit dari EPC. Coba lagi."}, "err"),
    # ⛔ not-found JUJUR tak boleh ikut terseret jadi 'err'.
    ({"found": False}, "nf"),
    ({"found": False, "error": "BOM unit ini tidak ditemukan di EPC (cek nomor rangka)."}, "nf"),
    ({"found": False, "error": "Tidak ada data persamaan/pengganti untuk PN ini "
                               "(sudah dicek SIMS Sinotruk & EPC Weichai)."}, "nf"),
    ({"ditemukan": False, "pesan": "PN ini tidak ada di data Accurate. Cek ejaan PN, "
                                   "atau coba detail_part / cari per NAMA via cari_part."}, "nf"),
    ({"found": False, "error": "Kategori 'xyz' tak dikenal."}, "nf"),
    # sukses & rem tak berubah.
    ({"found": True}, ""),
    ({"found": False, "dibatasi": True}, "brake"),
])
def test_fail_kind_infra_vs_nihil(result, kind):
    assert ai._tool_fail_kind(result) == kind
    assert ai._tool_failed(result) is bool(kind)


@pytest.mark.parametrize("kind", ["err", "nf", "brake"])
def test_err_kind_eksplisit_menang(kind):
    """Builder tool boleh menyatakan jenisnya sendiri — lebih kuat dari tebakan
    atas prosa, dan tak menuntut kata kunci tertentu di kalimat error."""
    assert ai._tool_fail_kind({"found": True, "_err_kind": kind}) == kind


def test_err_kind_ngawur_diabaikan():
    assert ai._tool_fail_kind({"_err_kind": "rusak-berat", "found": False}) == "nf"


def test_stok_accurate_sesi_mati_dilaporkan_err(monkeypatch):
    """End-to-end handler: sesi Accurate mati = GAGAL CEK, bukan 'stok tak ada'."""
    monkeypatch.setattr(ai.accurate, "available", lambda: True)

    def _mati(pn):
        raise ai.accurate.AccurateSessionExpired("sesi habis")

    monkeypatch.setattr(ai.accurate, "stock_full", _mati)
    out = ai._t_stok_accurate({"part_number": "ZZ9100520065"}, ADMIN)
    assert out["tersedia"] is False
    assert ai._tool_fail_kind(out) == "err"       # dulu: "nf"


# ── (4) stub ronde lama membawa intisari ─────────────────────────────
_HASIL = {
    "found": True, "rangka": "RT110061",
    "hasil": [{"pn": "ZZ9001234567", "nama": "Brake shoe", "stok": 4},
              {"pn": "ZZ9001234568", "nama": "Brake drum", "stok": 0}],
}


def _pesan(content):
    return [{"role": "system", "content": "sys"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]},
            {"role": "tool", "tool_call_id": "a", "content": content}]


def test_digest_json_membawa_pn_dan_unit():
    d = ai._digest_tool_content("cari_part_di_unit", json.dumps(_HASIL))
    assert "RT110061" in d and "ZZ9001234567" in d
    assert len(d) <= ai._TRIM_DIGEST_CAP


def test_digest_teks_bukan_json_jatuh_ke_pn():
    """Hasil terpotong _cap_tool_content / jalur tool bocor → masih ada PN."""
    d = ai._digest_tool_content("cari_part", "[HASIL TOOL cari_part] …ZZ9001234567… {rusak")
    assert "ZZ9001234567" in d


def test_digest_kosong_bila_tak_ada_apa_apa():
    assert ai._digest_tool_content("cari_part", "HASIL BESAR RONDE 1 " + "x" * 50) == ""


def test_digest_dipotong_di_batas_token_bukan_tengah_pn():
    """PN separuh ('…12345' dari '…1234567') = PN yang tak pernah ada, dan stub
    ini dibaca model — pemotongan wajib di batas pemisah."""
    besar = {"rangka": "RT110061",
             "hasil": [{"pn": f"ZZ90012345{i:02d}", "nama": "Komponen " + "x" * 60}
                       for i in range(3)]}
    d = ai._digest_tool_content("cari_part_di_unit", json.dumps(besar))
    assert len(d) <= ai._TRIM_DIGEST_CAP + 2          # + ' …'
    for tok in ai._extract_pns(d):
        assert tok in json.dumps(besar), f"token {tok} tak ada di sumbernya"


def test_stub_ronde_lama_menyisakan_pasangan_pn_unit():
    m = _pesan(json.dumps(_HASIL))
    idx = [{"i": 2, "round": 1, "name": "cari_part_di_unit"}]
    ai._trim_old_tool_messages(m, idx, cur_round=3, keep_last=2)
    isi = m[2]["content"]
    assert "diringkas" in isi and "cari_part_di_unit" in isi
    assert "RT110061" in isi and "ZZ9001234567" in isi
    assert m[2]["role"] == "tool" and m[2]["tool_call_id"] == "a"
    assert len(isi) < len(json.dumps(_HASIL)) + 220      # tetap jauh lebih pendek
    assert idx[0]["stubbed"] is True


def test_stub_deterministik():
    """Dua kali trim atas isi yang sama → stub identik (tak ada sumber acak)."""
    a, b = _pesan(json.dumps(_HASIL)), _pesan(json.dumps(_HASIL))
    for m in (a, b):
        ai._trim_old_tool_messages(m, [{"i": 2, "round": 1, "name": "x"}], cur_round=3)
    assert a[2]["content"] == b[2]["content"]


def test_digest_tak_pernah_melempar(monkeypatch):
    def _boom(name, args, result):
        raise RuntimeError("peringkas rusak")

    monkeypatch.setattr(ai, "_fakta_from_tool", _boom)
    assert isinstance(ai._digest_tool_content("cari_part", json.dumps(_HASIL)), str)


# ── (12) nota lookup gagal menyebut tool + argumen ───────────────────
def test_nota_menyebut_tool_dan_argumen():
    nota = ai._lookup_gagal_note([
        ("detail_part", {"part_number": "ZZ9001234567"}),
        ("cari_part_di_unit", {"rangka": "RT110061", "kata_kunci": "kampas rem"}),
    ])
    assert "detail_part(ZZ9001234567)" in nota
    # identitas + istilah pencarian: keduanya, karena sendiri-sendiri menyisakan tebakan.
    assert "cari_part_di_unit(RT110061, kampas rem)" in nota
    assert "DILARANG mengarang" in nota
    assert "Tool LAIN" in nota                            # tool sukses tetap boleh dipakai


def test_nota_tanpa_rincian_persis_seperti_dulu():
    """Pemanggil lama (p9 masih memakai konstanta) TIDAK berubah perilakunya."""
    assert ai._lookup_gagal_note() == ai._LOOKUP_GAGAL_NOTE
    assert "Ada tool yang GAGAL/tak menemukan data." in ai._LOOKUP_GAGAL_NOTE


def test_nota_dedupe_dan_argumen_kosong():
    nota = ai._lookup_gagal_note([("cari_part", {}), ("cari_part", {}),
                                  ("", {"query": "x"}), ("diagnosa", None)])
    assert nota.count("cari_part") == 1
    assert "diagnosa" in nota


def test_arg_kunci_memilih_yang_paling_menjelaskan():
    assert ai._lookup_gagal_arg({"excel": True, "part_number": "ZZ9001234567"}) == "ZZ9001234567"
    assert ai._lookup_gagal_arg({"daftar_rangka": ["RT110061", "RT110062"]}) == "RT110061, RT110062"
    assert ai._lookup_gagal_arg({"rangka": "RT110061", "part": "injector"}) == "RT110061, injector"
    assert ai._lookup_gagal_arg({"excel": True}) == ""
    assert len(ai._lookup_gagal_arg({"query": "x" * 200})) <= ai._LOOKUP_GAGAL_ARG_CAP
