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


# ── _drop_unit_tokens: kode unit & nama gudang bukan PN karangan ─────────────

def test_kode_unit_dengan_klitik_tidak_disamarkan(monkeypatch):
    # Kasus nyata (probe 2026-07-09): model menulis 'unit NX360-mu' → token
    # 'NX360-MU' dianggap PN karangan & disamarkan. Klitik -mu/-nya di ujung
    # kode unit harus dikecualikan.
    monkeypatch.setitem(ai._UNIT_TOKEN_CACHE, "tokens", {"NX360HP", "NX360", "SITRAK"})
    monkeypatch.setitem(ai._UNIT_TOKEN_CACHE, "at", __import__("time").time())
    assert ai._drop_unit_tokens(["NX360-MU"]) == []
    assert ai._drop_unit_tokens(["SITRAK-NYA"]) == []
    # PN asli berujung '-LH' (basis BUKAN nama unit) tetap dianggap dugaan.
    assert ai._drop_unit_tokens(["AZ9998887776-LH"]) == ["AZ9998887776-LH"]


def test_nama_gudang_dikecualikan_dari_guard(monkeypatch):
    # Nama gudang kanonik ('01.Jakarta') kini ada di system prompt (ai_knowledge)
    # → model bisa menyebutnya tanpa tool; guard tak boleh menyamarkannya.
    from app.services import gudang_config
    monkeypatch.setattr(gudang_config, "coords_map",
                        lambda: {"01.Jakarta": (0, 0), "25. PT BJM": (0, 0)})
    monkeypatch.setitem(ai._UNIT_TOKEN_CACHE, "tokens", set())
    monkeypatch.setitem(ai._UNIT_TOKEN_CACHE, "at", 0.0)  # paksa rebuild cache
    toks = ai._extract_pns("Stok ada di 01.Jakarta")
    assert toks  # memang tertangkap regex mirip-PN…
    assert ai._drop_unit_tokens(sorted(toks)) == []  # …tapi dikecualikan guard


# ── P9.1: memori konteks lebih luas (3 pesan assistant ber-PN) ───────────────

def test_recent_part_numbers_agregat_lintas_pesan():
    hist = [
        {"role": "assistant", "content": "Rem: WG9100443050 dan AZ9100443051."},
        {"role": "user", "content": "kalau filternya?"},
        {"role": "assistant", "content": "Filter: VG1560080012."},
        {"role": "user", "content": "harga keduanya?"},
    ]
    pns = ai._recent_part_numbers(hist)
    # BUKAN cuma pesan terakhir — part dari jawaban 2 giliran lalu tetap teringat.
    assert "VG1560080012" in pns
    assert "WG9100443050" in pns and "AZ9100443051" in pns


def test_recent_part_numbers_batas_3_pesan_dan_cap():
    hist = [{"role": "assistant", "content": f"PN AZ160000{i:04d}"} for i in range(6)]
    pns = ai._recent_part_numbers(hist)
    assert len(pns) <= 12
    # hanya 3 pesan assistant TERAKHIR yang dipanen (recent-first)
    assert "AZ1600000005" in pns and "AZ1600000000" not in pns


# ── P9.2: _rangka_candidates buang PN katalog/kode unit yang mirip frame ─────

def test_rangka_candidates_buang_pn_katalog(monkeypatch):
    # 'AZ123456' cocok _FRAME_RE (2 huruf+6 angka) TAPI ternyata PN katalog → bukan rangka.
    monkeypatch.setattr(ai.part_index, "search_exact_pns",
                        lambda toks: [{"part_number": "AZ123456"}] if "AZ123456" in set(toks) else [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    # rangka asli SJ346500 tetap lolos; AZ123456 (PN) dibuang
    out = ai._rangka_candidates("CEK AZ123456 DAN SJ346500")
    assert "SJ346500" in out
    assert "AZ123456" not in out


def test_rangka_candidates_vin_tak_disaring(monkeypatch):
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda toks: [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    vin = "LZZ5EXSF0KX123456"      # 17 char VIN
    assert vin in ai._rangka_candidates("UNIT " + vin)
