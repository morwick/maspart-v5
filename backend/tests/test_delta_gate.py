"""_DeltaGate — penyaring [PIKIR] versi INKREMENTAL untuk streaming token.

`_strip_reasoning` boleh menunggu teks utuh; gerbang ini tidak — ia harus
memutuskan dari potongan pertama apakah aliran ini nalar internal atau jawaban.
Yang dijaga di sini:
  - blok [PIKIR] utuh dalam satu chunk → hanya jawabannya yang keluar;
  - tag "[/PIKIR]" TERBELAH antar chunk tak boleh bocor separuh ke layar;
  - jawaban tanpa [PIKIR] mengalir apa adanya (dikurangi ekor tertahan);
  - ekor 8 karakter baru keluar saat close();
  - markup pemanggilan tool yang bocor → sinyal supress (pemanggil kirim reset).
"""
from app.services import ai_assistant as A


def _alirkan(potongan: list[str]) -> tuple[str, bool]:
    """(teks yang sampai ke klien, pernah-supress?) untuk satu daftar chunk."""
    g = A._DeltaGate()
    keluar: list[str] = []
    supress = False
    for p in potongan:
        out = g.feed(p)
        if out is None:
            supress = True
            continue
        keluar.append(out)
    keluar.append(g.close())
    return "".join(keluar), supress


def test_pikir_utuh_satu_chunk():
    teks, supress = _alirkan(["[PIKIR]nalar rahasia[/PIKIR]Jawabannya A."])
    assert teks == "Jawabannya A."
    assert supress is False


def test_penutup_pikir_terbelah_antar_chunk():
    """Chunk SSE memotong di mana saja — '[/PIK' + 'IR]' tak boleh bocor."""
    teks, _ = _alirkan(["[PIKIR]nalar", " lanjut[/PIK", "IR]Jawaban B."])
    assert teks == "Jawaban B."
    assert "PIKIR" not in teks


def test_pembuka_pikir_terbelah_antar_chunk():
    """Potongan pertama masih ambigu ('[PI') → ditahan, bukan dialirkan."""
    g = A._DeltaGate()
    assert g.feed("[PI") == ""          # belum tahu ini nalar atau bukan
    assert g.feed("KIR]rahasia") == ""
    hasil = g.feed("[/PIKIR]Jawaban C.") + g.close()
    assert "rahasia" not in hasil
    assert hasil == "Jawaban C."


def test_pikir_case_insensitive():
    teks, _ = _alirkan(["[pikir]nalar[/pikir]Jawaban D."])
    assert teks == "Jawaban D."


def test_tanpa_pikir_mengalir_apa_adanya():
    teks, supress = _alirkan(["Halo, ", "ini jawaban panjang ", "tanpa nalar."])
    assert teks == "Halo, ini jawaban panjang tanpa nalar."
    assert supress is False


def test_ekor_ditahan_sampai_close():
    """8 karakter terakhir sengaja ditahan (tag bisa menyusul) → keluar di close."""
    g = A._DeltaGate()
    out = g.feed("1234567890ABCD")       # 14 char
    assert out == "123456"               # 8 char terakhir ditahan
    assert g.close() == "7890ABCD"


def test_jawaban_lebih_pendek_dari_ekor_baru_keluar_saat_close():
    g = A._DeltaGate()
    assert g.feed("Ya.") == ""           # 3 char ≤ ekor 8 → belum dialirkan
    assert g.close() == "Ya."


def test_markup_tool_bocor_disupress():
    """Model menulis pemanggilan tool sebagai TEKS → jangan tampilkan sepotong pun."""
    g = A._DeltaGate()
    keluar, supress = [], False
    for p in ["Sebentar ya, saya cek dulu di katalog...........",
              '<invoke name="cari_part">', '<parameter name="q">rem</parameter>']:
        out = g.feed(p)
        if out is None:
            supress = True
        elif out:
            keluar.append(out)
    assert supress is True
    assert g.close() == ""               # gerbang MATI — tak ada susulan
    assert "invoke" not in "".join(keluar)


def test_markup_tool_lengkap_beberapa_chunk_kemudian():
    """Tag baru lengkap ('>' menyusul) setelah sebagian teks terlanjur mengalir —
    gerbang tetap harus mendeteksinya lalu minta reset."""
    g = A._DeltaGate()
    supress = False
    for p in ['Oke. <invoke name="cari', '_part"', ">"]:
        if g.feed(p) is None:
            supress = True
    assert supress is True


def test_setelah_supress_feed_berikutnya_diam():
    g = A._DeltaGate()
    g.feed('<invoke name="x">' + "y" * 20)
    assert g.feed("teks susulan yang panjang sekali") == ""
    assert g.close() == ""


def test_gerbang_tak_pernah_terbuka_tak_membocorkan_nalar():
    """Aliran berhenti di tengah [PIKIR] (model terpotong) → close() kosong."""
    g = A._DeltaGate()
    g.feed("[PIKIR]nalar yang tak pernah ditutup")
    assert g.close() == ""


def test_spasi_awal_tidak_membuka_gerbang_prematur():
    teks, _ = _alirkan(["  \n", "[PIKIR]x[/PIKIR]Jawaban E."])
    assert teks.strip() == "Jawaban E."
