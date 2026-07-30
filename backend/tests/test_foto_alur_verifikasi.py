"""Alur FOTO asisten: saring kandidat ke BOM unit + verifikasi visual oleh user.

Latar (uji foto lapangan 2026-07-30, galeri 37.910 embedding, VIN LZZ5DMSDXRT108820):
Cari-by-Foto menaruh part yang BENAR (WG9725550198, konektor solar) di peringkat #58
dengan skor 0,441, sementara peringkat 1 diisi part asing (peredam kejut) berskor
0,555. Jadi top-6 global melewatkan jawaban benar DAN menyajikan yang salah dengan
skor lebih tinggi — sementara prompt lama ("ragu bila <50%") meloloskannya sebagai
"dugaan utama". Disaring ke 3.966 baris BOM unit, part yang benar naik ke #2.

Yang dijaga di sini:
  • `frame_dari_teks`: rangka/VIN dipungut dari pesan chat (yang TERAKHIR menang).
  • `search_by_image(restrict_pns=...)`: hasil disaring ke PN BOM; jendela kandidat
    diperlebar (peringkat #58 tak akan terjangkau oleh jendela top_k*5).
  • suffix varian PN ('…004/2') dianggap part yang SAMA saat mencocokkan.
  • `_photo_note`: TIDAK memakai skor sebagai ambang keyakinan; mewajibkan
    foto_resmi_part + pertanyaan penyempit; minta rangka bila belum ada.
  • `foto_resmi_part`: foto resmi SIMS jadi gambar inline; foto raksasa DILEWATI
    (RAM server 3,8 GB); PN tanpa foto dilaporkan apa adanya, tidak dikarang.
Tanpa jaringan & tanpa satu pun panggilan model.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.routers import ai as ai_router
from app.services import ai_assistant as A
from app.services import epc_bom, image_search

USER = {"username": "budi", "role": "pembeli"}


# ── 1. Rangka dipungut dari teks percakapan ──────────────────────────────────

@pytest.mark.parametrize("teks, harap", [
    ("cari part ini untuk unit LZZ5DMSDXRT108820", "RT108820"),
    ("unitnya RT108820 ya", "RT108820"),
    ("frame no: lzz5dmsdxrt108820.", "RT108820"),
    ("part ini apa?", ""),
    ("12345678901234567", ""),            # 17 digit polos — bukan VIN
    ("VIN LZZ1BLSG7SJ346500 lalu ganti ke LZZ5DMSDXRT108820", "RT108820"),
])
def test_frame_dari_teks(teks, harap):
    assert epc_bom.frame_dari_teks(teks) == harap


# ── 2. Penyaringan kandidat ke BOM unit ─────────────────────────────────────

def test_search_by_image_disaring_ke_pn_bom(monkeypatch):
    """Kandidat berskor lebih tinggi tapi BUKAN part unit itu harus tersingkir."""
    dipanggil = {}

    def fake_fetch(vec, dist, count):
        dipanggil["count"] = count
        return [
            {"part_number": "AZ1642440086", "sims_url": "http://x/1", "similarity": 0.555},
            {"part_number": "WG9725550198", "sims_url": "http://x/2", "similarity": 0.441},
            {"part_number": "AZ9731430050", "sims_url": "http://x/3", "similarity": 0.420},
        ]

    monkeypatch.setattr(image_search, "_fetch_candidates", fake_fetch)
    monkeypatch.setattr(image_search, "compute_embedding", lambda b: [1.0, 0.0])
    monkeypatch.setattr(image_search, "local_index_available", lambda: True)
    monkeypatch.setattr(image_search._part_index, "name_for", lambda pn: "")

    bom = image_search.pn_keys("WG9725550198") | image_search.pn_keys("WG9725550206")
    out = image_search.search_by_image(b"foto", top_k=6, threshold=0.30, restrict_pns=bom)

    assert [c["part_number"] for c in out] == ["WG9725550198"]
    assert out[0]["di_bom_unit"] is True
    # Jendela harus jauh lebih lebar dari top_k*_AGG_FETCH_MULT (=30): part benar
    # bisa duduk di peringkat puluhan pada galeri penuh.
    assert dipanggil["count"] >= image_search._RESTRICT_FETCH_MIN


def test_search_by_image_tanpa_restrict_tak_berubah(monkeypatch):
    """Tanpa rangka, perilaku lama dipertahankan (jendela & hasil apa adanya)."""
    dipanggil = {}

    def fake_fetch(vec, dist, count):
        dipanggil["count"] = count
        return [{"part_number": "AZ1642440086", "sims_url": "http://x/1", "similarity": 0.555}]

    monkeypatch.setattr(image_search, "_fetch_candidates", fake_fetch)
    monkeypatch.setattr(image_search, "compute_embedding", lambda b: [1.0, 0.0])
    monkeypatch.setattr(image_search, "local_index_available", lambda: True)
    monkeypatch.setattr(image_search._part_index, "name_for", lambda pn: "")

    out = image_search.search_by_image(b"foto", top_k=6, threshold=0.30)
    assert [c["part_number"] for c in out] == ["AZ1642440086"]
    assert dipanggil["count"] < image_search._RESTRICT_FETCH_MIN


def test_sisa_global_tetap_disertakan(monkeypatch):
    """Loading List EPC DATAR: part di dalam assembly tak tercatat di sana, jadi
    kandidat di luar BOM tak boleh hilang total — hanya ditandai."""
    def fake_fetch(vec, dist, count):
        return [
            {"part_number": "AZ1642440086", "sims_url": "http://x/1", "similarity": 0.555},
            {"part_number": "WG9725550198", "sims_url": "http://x/2", "similarity": 0.441},
            {"part_number": "AZ9731430050", "sims_url": "http://x/3", "similarity": 0.420},
        ]

    monkeypatch.setattr(image_search, "_fetch_candidates", fake_fetch)
    monkeypatch.setattr(image_search, "compute_embedding", lambda b: [1.0, 0.0])
    monkeypatch.setattr(image_search, "local_index_available", lambda: True)
    monkeypatch.setattr(image_search._part_index, "name_for", lambda pn: "")

    out = image_search.search_by_image(
        b"foto", top_k=6, threshold=0.30,
        restrict_pns=image_search.pn_keys("WG9725550198"), sisa_global=2)
    # yang di BOM tetap DULU walau skornya lebih rendah
    assert [c["part_number"] for c in out] == ["WG9725550198", "AZ1642440086", "AZ9731430050"]
    assert [c["di_bom_unit"] for c in out] == [True, False, False]


def test_pn_keys_suffix_varian_dianggap_sama():
    """Katalog pakai suffix varian, BOM pakai PN dasar — part fisiknya sama."""
    bom = image_search.pn_keys("WG9725550300/1")
    assert image_search.pn_keys("WG9725550300") & bom
    assert image_search.pn_keys("WG9725550300/1") & bom
    assert not (image_search.pn_keys("WG9725550301") & bom)


# ── 3. Router: rangka → kumpulan kunci PN BOM ───────────────────────────────

def test_unit_dari_riwayat_ambil_bom(monkeypatch):
    monkeypatch.setattr(ai_router.epc_bom, "loading_list",
                        lambda f: {"found": True, "parts": [{"pn": "WG9725550198"},
                                                            {"pn": "WG9725550300/1"}]})
    unit = ai_router._unit_dari_riwayat(
        [{"role": "user", "content": "cari part ini untuk unit LZZ5DMSDXRT108820"}])
    assert unit["frame"] == "RT108820"
    assert unit["n_part_bom"] == 2
    assert image_search.pn_keys("WG9725550198") & unit["pn_keys"]


def test_unit_dari_riwayat_tanpa_rangka_tak_tembak_epc(monkeypatch):
    def jangan(_f):
        raise AssertionError("EPC tak boleh dipanggil bila user tak menyebut rangka")
    monkeypatch.setattr(ai_router.epc_bom, "loading_list", jangan)
    unit = ai_router._unit_dari_riwayat([{"role": "user", "content": "part ini apa?"}])
    assert unit == {"frame": "", "n_part_bom": 0, "pn_keys": set()}


def test_unit_dari_riwayat_epc_gagal_tetap_jalan(monkeypatch):
    """EPC error (mis. internal.server.error) → fitur foto tetap hidup, tanpa saringan."""
    def meledak(_f):
        raise RuntimeError("EPC down")
    monkeypatch.setattr(ai_router.epc_bom, "loading_list", meledak)
    unit = ai_router._unit_dari_riwayat(
        [{"role": "user", "content": "unit LZZ5DMSDXRT108820"}])
    assert unit["frame"] == "RT108820" and unit["pn_keys"] == set()


# ── 4. Catatan foto: skor bukan bukti ───────────────────────────────────────

KANDIDAT = [
    {"part_number": "AZ9725478050", "part_name": "Bracket", "similarity": 0.479},
    {"part_number": "WG9725550198", "part_name": "Pipe joint", "similarity": 0.441},
]


def test_photo_note_disaring_bom_wajib_verifikasi():
    note = A._photo_note(KANDIDAT, {"frame": "RT108820", "n_part_bom": 1160})
    assert "RT108820" in note and "1160" in note
    assert "foto_resmi_part" in note
    # ⛔ ambang lama yang meloloskan jawaban salah tak boleh kembali
    assert "<50%" not in note
    assert "SKOR KEMIRIPAN BUKAN BUKTI" in note
    # sudah ada rangka → jangan minta rangka lagi
    assert "MINTA NOMOR RANGKA" not in note


def test_photo_note_tanpa_rangka_minta_rangka():
    note = A._photo_note(KANDIDAT, None)
    assert "MINTA NOMOR RANGKA" in note
    assert "foto_resmi_part" in note


def test_photo_note_saringan_kosong_diberi_tahu():
    note = A._photo_note(KANDIDAT, {"frame": "RT108820", "n_part_bom": 1160,
                                    "saringan_kosong": True})
    assert "BELUM terbukti milik unit itu" in note


def test_photo_note_pisahkan_kandidat_luar_bom():
    kand = [dict(KANDIDAT[1], di_bom_unit=True),
            dict(KANDIDAT[0], di_bom_unit=False)]
    note = A._photo_note(kand, {"frame": "RT108820", "n_part_bom": 1160})
    assert "DI LUAR daftar BOM unit" in note
    # alasan kenapa tak boleh langsung dibuang harus ikut, bukan cuma labelnya
    assert "Loading List" in note and "assembly" in note
    assert note.index("WG9725550198") < note.index("DI LUAR daftar BOM unit")


def test_photo_note_nol_kandidat_tanya_penyempit():
    note = A._photo_note([], {"frame": "RT108820", "n_part_bom": 1160})
    assert "JANGAN mengarang PN" in note
    assert "pertanyaan penyempit" in note
    assert "crop" in note


# ── 5. Tool foto_resmi_part ─────────────────────────────────────────────────

def test_foto_resmi_part_gambar_inline(monkeypatch):
    monkeypatch.setattr(A.sims, "available", lambda: True)
    monkeypatch.setattr(A.sims, "get_images",
                        lambda pn: [f"http://sims/{pn}-1.jpg", f"http://sims/{pn}-2.jpg",
                                    f"http://sims/{pn}-3.jpg"])
    monkeypatch.setattr(A, "_foto_sims_unduh", lambda u, m=0: b"JPEGBYTES")
    monkeypatch.setattr(A, "_foto_kecilkan", lambda d: d)

    out = A._t_foto_resmi_part({"part_number": "WG9725550198, WG9725550206"}, USER)
    assert out["found"] is True
    # 2 PN × maks 2 foto (URL ke-3 diabaikan)
    assert out["jumlah"] == 4
    assert {g["pn"] for g in out["gambar"]} == {"WG9725550198", "WG9725550206"}
    assert all(g["image_id"] for g in out["gambar"])
    # keputusan kecocokan harus dilempar ke user, bukan diklaim model
    assert "MEMBANDINGKAN" in out["catatan"]


def test_foto_resmi_part_tanpa_foto_tidak_dikarang(monkeypatch):
    monkeypatch.setattr(A.sims, "available", lambda: True)
    monkeypatch.setattr(A.sims, "get_images", lambda pn: [])
    out = A._t_foto_resmi_part({"part_number": ["EZ9325470004"]}, USER)
    assert out["found"] is False
    assert out["pn_tanpa_foto"] == ["EZ9325470004"]
    assert "jangan mengarang" in out["catatan"].lower()


def test_foto_resmi_part_batasi_3_pn(monkeypatch):
    monkeypatch.setattr(A.sims, "available", lambda: True)
    monkeypatch.setattr(A.sims, "get_images", lambda pn: ["http://sims/x.jpg"])
    monkeypatch.setattr(A, "_foto_sims_unduh", lambda u, m=0: b"X")
    monkeypatch.setattr(A, "_foto_kecilkan", lambda d: d)
    out = A._t_foto_resmi_part(
        {"part_number": ["A11111111", "B22222222", "C33333333", "D44444444"],
         "maks_per_part": 1}, USER)
    assert [g["pn"] for g in out["gambar"]] == ["A11111111", "B22222222", "C33333333"]


def test_foto_resmi_part_pn_kosong():
    assert A._t_foto_resmi_part({"part_number": ""}, USER)["found"] is False


def test_foto_raksasa_dilewati(monkeypatch):
    """Foto SIMS 37 MB pernah nyata — RAM server 3,8 GB, jangan ditelan."""
    class Resp:
        status_code = 200
        headers = {"Content-Length": str(40 * 1024 * 1024)}

        def iter_content(self, n):  # pragma: no cover — tak boleh sampai sini
            raise AssertionError("body tak boleh dibaca bila Content-Length kebesaran")

    monkeypatch.setattr(A.requests, "get", lambda *a, **k: Resp())
    assert A._foto_sims_unduh("http://sims/besar.jpg") is None


def test_semua_foto_raksasa_masih_dapat_satu(monkeypatch):
    """PN yang SEMUA fotonya raksasa tak boleh dilaporkan 'tak punya foto'."""
    monkeypatch.setattr(A.sims, "available", lambda: True)
    monkeypatch.setattr(A.sims, "get_images",
                        lambda pn: ["http://sims/besar1.jpg", "http://sims/besar2.jpg"])
    dicoba: list[int] = []

    def unduh(url, maks_byte=A._FOTO_RESMI_MAKS_BYTE):
        dicoba.append(maks_byte)
        return b"BIG" if maks_byte >= A._FOTO_RESMI_MAKS_BYTE_LONGGAR else None

    monkeypatch.setattr(A, "_foto_sims_unduh", unduh)
    monkeypatch.setattr(A, "_foto_kecilkan", lambda d: d)
    out = A._t_foto_resmi_part({"part_number": "WG9725550199"}, USER)
    assert out["found"] is True and out["jumlah"] == 1
    # pagar longgar hanya SEKALI, dan hanya setelah pagar normal gagal
    assert dicoba.count(A._FOTO_RESMI_MAKS_BYTE_LONGGAR) == 1


def test_pagar_longgar_tak_bocor_ke_pn_berikutnya(monkeypatch):
    """Batas 1 foto pada percobaan longgar tak boleh mengurangi kuota PN lain."""
    monkeypatch.setattr(A.sims, "available", lambda: True)
    monkeypatch.setattr(A.sims, "get_images", lambda pn: [f"http://s/{pn}-1", f"http://s/{pn}-2"])

    def unduh(url, maks_byte=A._FOTO_RESMI_MAKS_BYTE):
        # PN pertama: hanya lolos pada pagar longgar. PN kedua: normal.
        if "AAAAAAAA" in url:
            return b"BIG" if maks_byte >= A._FOTO_RESMI_MAKS_BYTE_LONGGAR else None
        return b"OK"

    monkeypatch.setattr(A, "_foto_sims_unduh", unduh)
    monkeypatch.setattr(A, "_foto_kecilkan", lambda d: d)
    out = A._t_foto_resmi_part({"part_number": ["AAAAAAAA", "BBBBBBBB"]}, USER)
    per_pn = {}
    for g in out["gambar"]:
        per_pn[g["pn"]] = per_pn.get(g["pn"], 0) + 1
    assert per_pn == {"AAAAAAAA": 1, "BBBBBBBB": 2}


def test_foto_potong_bila_content_length_bohong(monkeypatch):
    class Resp:
        status_code = 200
        headers = {}

        def iter_content(self, n):
            for _ in range(200):
                yield b"x" * (256 * 1024)

    monkeypatch.setattr(A.requests, "get", lambda *a, **k: Resp())
    assert A._foto_sims_unduh("http://sims/bohong.jpg") is None


# ── 6. Pendaftaran tool ─────────────────────────────────────────────────────

def test_tool_terdaftar_dan_gambar_inline():
    assert "foto_resmi_part" in A._DISPATCH
    assert "foto_resmi_part" in A._TOOLS_GAMBAR_INLINE
    nama = {s["function"]["name"] for s in A._tool_specs(USER)}
    assert "foto_resmi_part" in nama, "pembeli juga harus bisa verifikasi foto"
    assert "foto_resmi_part" in A._allowed_tool_names(USER)
