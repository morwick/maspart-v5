"""Guard anti-halusinasi PN (§3.5.5f) — logika murni, tanpa network.

Fungsi yang diuji: _extract_pns, _ungrounded_pns, _sanitize_ungrounded.
Bug di sini bisa MENYAMARKAN PN yang benar atau MELOLOSKAN PN karangan.
"""
from app.services import ai_assistant as ai


# ── _extract_pns: apa yang DIANGGAP PN ───────────────────────────────────────

def test_pn_alfanumerik_terdeteksi():
    txt = "Part AZ1623550001 dan WG2210040097 tersedia; gearbox HW19709XST201136."
    assert {"AZ1623550001", "WG2210040097", "HW19709XST201136"} <= ai._extract_pns(txt)


def test_pn_alfanumerik_juga_menghasilkan_bayangan_numerik():
    # Perilaku BY DESIGN: regex PN-numerik (Weichai, >=9 digit) ikut menangkap
    # ekor angka PN alfanumerik ('WG2210040097' -> juga '2210040097'). Aman
    # karena `grounded` dibangun dengan extractor yang SAMA (konsisten dua sisi)
    # — test ini mengunci asumsi itu agar perubahan regex yang memutusnya ketahuan.
    assert "2210040097" in ai._extract_pns("WG2210040097")


def test_pn_lowercase_dinormalkan_uppercase():
    assert "WG2210040097" in ai._extract_pns("stok wg2210040097 ada 5")


def test_pn_numerik_weichai_minimal_9_digit():
    # PN murni angka khas Weichai (>=9 digit) harus ikut terdeteksi.
    assert "612630010054" in ai._extract_pns("Piston ring set 612630010054")
    assert "1000076563" in ai._extract_pns("Piston 1000076563")


def test_harga_dan_angka_pendek_bukan_pn():
    # Harga berformat titik ribuan & angka pendek (qty/tahun) TIDAK boleh dianggap PN.
    txt = "Harga Rp 2.150.000, qty 12, tahun 2026, diskon 900.000"
    assert ai._extract_pns(txt) == set()


def test_kata_tanpa_angka_bukan_pn():
    assert ai._extract_pns("GEARBOX TRANSMISI ASSEMBLY driven disc") == set()


def test_token_pendek_bukan_pn():
    # < 7 karakter (mis. kode unit NX400) bukan PN.
    assert ai._extract_pns("unit NX400 dan P0645") == set()


def test_titik_strip_di_ujung_dibuang():
    assert ai._extract_pns("lihat AZ1623550001.") == {"AZ1623550001"}


# ── _ungrounded_pns: PN jawaban wajib bersumber dari data ────────────────────

def test_pn_grounded_lolos():
    # grounded dibangun dengan extractor yang sama seperti di chat() nyata.
    grounded = ai._extract_pns("hasil tool: WG2210040097 stok 5")
    assert ai._ungrounded_pns("Stok WG2210040097: 5 pcs", grounded) == []


def test_pn_karangan_tertangkap():
    grounded = ai._extract_pns("hasil tool: WG2210040097 stok 5")
    bad = ai._ungrounded_pns("Ada WG2210040097 dan AZ9998887776", grounded)
    assert "AZ9998887776" in bad
    assert not any("WG2210040097" == b for b in bad)


# ── _sanitize_ungrounded: jaring terakhir ────────────────────────────────────

def test_semua_pn_karangan_diganti_pesan_jujur():
    reply = "Berikut hasilnya:\n- AZ9998887776 stok 3\n- AZ9998887777 stok 2"
    bad = ai._ungrounded_pns(reply, grounded=set())   # seperti di chat() nyata
    out = ai._sanitize_ungrounded(reply, bad)
    assert out == ai._NOT_FOUND_REPLY  # tabel palsu TIDAK boleh ditampilkan


def test_sebagian_karangan_disamarkan_yang_asli_dipertahankan():
    reply = "WG2210040097 stok 5; alternatifnya AZ9998887776."
    out = ai._sanitize_ungrounded(reply, ["AZ9998887776"])
    assert "AZ9998887776" not in out
    assert "WG2210040097" in out          # PN nyata tetap tampil
    assert "tak terverifikasi" in out      # penanda samaran
    assert out.startswith("⚠️")            # peringatan di awal
