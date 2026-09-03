"""Tool asisten `foto_resmi_part`: foto RESMI SIMS sebuah PN → gambar inline.

Arahnya PN → foto (deterministik), BUKAN foto → PN: jalur "kirim foto ke asisten"
DIBUANG 2026-07-30 atas keputusan pemilik karena identifikasinya tak bisa dipercaya
(skor DINOv2 di rentang 40-60% tak membedakan benar/salah — part benar 44% vs part
salah 56%) dan tak bisa diperbaiki tanpa model vision, yang tak mungkin jalan di
server 1 vCPU / 1,2 GB RAM sisa. Pengenalan part dari foto tetap ada di menu
TERPISAH "Cari by Foto". Lihat juga test_image_search_akurasi.py.

Yang dijaga di sini:
  • foto resmi jadi gambar INLINE (lewat ai_export.stash_raw), maks 3 PN × 2 foto;
  • foto RAKSASA dilewati (RAM server 3,8 GB) TAPI bila SEMUA foto satu PN raksasa
    (nyata: WG9725550199 = 37 MB) masih ada satu percobaan longgar → PN tak pernah
    dilaporkan "tak punya foto" padahal punya;
  • PN tanpa foto dilaporkan apa adanya, tidak dikarang.
Tanpa jaringan & tanpa satu pun panggilan model.
"""
from __future__ import annotations

from app.services import ai_assistant as A

USER = {"username": "budi", "role": "pembeli"}


# ── Tool foto_resmi_part ────────────────────────────────────────────────────

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
    assert "keputusan kecocokan ADA DI USER" in out["catatan"]


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


# ── Pendaftaran tool ────────────────────────────────────────────────────────

def test_foto_hanya_saat_diminta():
    """Aturan pemilik 2026-07-30: foto resmi JANGAN auto tampil — tunggu user minta.

    Sejajar dengan aturan gambar exploded ('hanya muncul saat DIMINTA lewat tool ini').
    Dikunci di sini karena kalimatnya di deskripsi tool + prompt itulah satu-satunya
    yang menahan model menawarkan foto sendiri; kalau ada yang menghapusnya sambil
    merapikan teks, tak ada lagi yang menahannya.
    """
    spec = next(s["function"] for s in A._tool_specs(USER)
                if s["function"]["name"] == "foto_resmi_part")
    d = spec["description"]
    assert "HANYA saat user MEMINTA" in d
    assert "TIDAK auto-nempel" in d
    assert "JANGAN memanggil tool ini atas inisiatif sendiri" in d

    prompt = A._system_prompt({"username": "budi", "role": "pembeli"})
    assert "HANYA saat user MEMINTANYA" in prompt
    # Dipadatkan 2026-09-04 (anggaran prompt 60.000 char): larangan menawarkan
    # foto atas inisiatif sendiri tetap ada, kalimat 'tak pernah tampil
    # otomatis' dilebur ke sana.
    assert "jangan menawarkannya sendiri" in prompt


def test_tool_terdaftar_dan_gambar_inline():
    assert "foto_resmi_part" in A._DISPATCH
    assert "foto_resmi_part" in A._TOOLS_GAMBAR_INLINE
    nama = {s["function"]["name"] for s in A._tool_specs(USER)}
    assert "foto_resmi_part" in nama, "pembeli juga boleh melihat foto resmi part"
    assert "foto_resmi_part" in A._allowed_tool_names(USER)
