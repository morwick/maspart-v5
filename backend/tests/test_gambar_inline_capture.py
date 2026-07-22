"""Penguncian kanal GAMBAR INLINE (_TOOLS_GAMBAR_INLINE).

Latar: `cari_pengetahuan` sempat LOLOS dari _capture_meta karena logikanya
tersebar di dua cabang `elif` — gambarnya di-stash lalu hilang, padahal catatan
ke model menjanjikan "tampil OTOMATIS (inline)". Test ini mengunci daftar
tunggalnya supaya kelalaian yang sama tak terulang untuk tool bergambar
berikutnya.
"""
from app.services import ai_assistant as ai


def test_semua_nama_di_daftar_benar_benar_ada_di_dispatch():
    """Salah ketik nama = gambar hilang diam-diam, tanpa error apa pun."""
    for nama in ai._TOOLS_GAMBAR_INLINE:
        assert nama in ai._DISPATCH, f"'{nama}' tak ada di _DISPATCH (salah ketik?)"


def test_tool_pengetahuan_termasuk():
    assert "cari_pengetahuan" in ai._TOOLS_GAMBAR_INLINE


def test_tool_diagnosa_termasuk():
    """Fan-out pengetahuan_internal (18451fd) MENERUSKAN gambar hasil
    _t_cari_pengetahuan tanpa memanggil stash_raw sendiri — detektor sumber
    di bawah tak menangkapnya, jadi dikunci eksplisit di sini."""
    assert "diagnosa" in ai._TOOLS_GAMBAR_INLINE


def test_tool_detail_klaim_termasuk():
    """Foto klaim garansi SIMS (2026-07-22) tampil inline."""
    assert "detail_klaim" in ai._TOOLS_GAMBAR_INLINE


def test_tool_bergambar_lama_tidak_hilang_saat_refactor():
    """Regresi: pengangkatan keluar dari rantai elif tak boleh menjatuhkan
    satu pun tool yang sebelumnya sudah menampilkan gambar."""
    for nama in ("gambar_exploded", "gambar_exploded_mesin", "uraikan_mesin",
                 "uraikan_assembly", "part_aus_dari_rangka", "diagram_wiring",
                 "cari_manual"):
        assert nama in ai._TOOLS_GAMBAR_INLINE, nama


def test_setiap_tool_bergambar_terdaftar_di_daftar_inline():
    """Cegah kebalikannya: handler yang MENGISI `gambar` tapi lupa didaftarkan.

    Dideteksi dari sumber handler — kalau sebuah handler menulis key "gambar"
    ke hasilnya, ia wajib ada di _TOOLS_GAMBAR_INLINE.
    """
    import inspect
    lolos = []
    for nama, fn in ai._DISPATCH.items():
        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):
            continue
        mengisi = '"gambar"' in src and 'stash_raw' in src
        if mengisi and nama not in ai._TOOLS_GAMBAR_INLINE:
            lolos.append(nama)
    assert not lolos, f"handler mengisi 'gambar' tapi tak terdaftar inline: {lolos}"
