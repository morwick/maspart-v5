# -*- coding: utf-8 -*-
"""KELUARGA PN (varian pemasok / sub-rakitan / awalan setara) — logika murni.

Katalog aslinya di-STUB: test ini menguji ATURANnya, bukan isi katalog produksi
(yang berubah tiap sinkron). Kasus uji disalin dari log produksi 45 hari
(2026-09-01) di mana asisten menjawab SALAH "tidak ada persamaan".
"""
import pytest

from app.services import pn_keluarga as K


# Potongan katalog yang meniru bentuk-bentuk nyata Sinotruk.
KATALOG = [
    # varian pemasok — kasus nyata WG9725191800 (dijawab 'nihil' di produksi)
    ("WG9725191800", "Air filter assembly (Clockwise standard)"),
    ("WG9725191800/1", "Air filter assembly"),
    ("WG9725191800/2", "Air filter assembly (Hebei Yili)"),
    # varian pemasok bernama Mandarin — beda bahasa, part sama
    ("WG9525523046", "Front plate spring assembly"),
    ("WG9525523046/5", "前钢板弹簧总成"),
    # sub-rakitan: lembar pegas daun ke-1 dan ke-2 = part BERBEDA
    ("WG9725522011+001/1", "Leaf spring assembly"),
    ("WG9725522011+002/1", "Leaf spring assembly"),
    # awalan setara AZ↔WG dengan nama cocok
    ("AZ9725551523", "Filter element"),
    ("WG9725551523/1", "Filter element (Long service life) (Mann-Hummel)"),
    # awalan setara AZ↔WG tapi salah satu TAK BERNAMA → tak bisa diverifikasi
    ("WG9925520235", "Balance shaft housing"),
    ("AZ9925520235", ""),
    # awalan setara secara statistik, tapi NAMA jelas beda → harus ditolak
    ("AZ9925520233", "Balance shaft bracket"),
    ("WG9925520233", "Ejector housing"),
    # jebakan SP**: angka sama, part sama sekali berbeda
    ("SPHG0000000024", "Brake friction pad 3502-01F-123"),
    ("SPDC0000000024", "Output shaft end cover AZ9722290054 (HW50)"),
    # PN tanpa keluarga apa pun
    ("200V06500-6694", "Water pump"),
]


@pytest.fixture(autouse=True)
def _katalog_stub(monkeypatch):
    """Ganti sumber katalog + kosongkan memo indeks turunan."""
    monkeypatch.setattr(K.part_index, "ensure_index", lambda: None)
    monkeypatch.setattr(K.part_index, "all_parts_min", lambda: list(KATALOG))
    monkeypatch.setattr(K.part_index, "_state", {"indexed_at": "stub"}, raising=False)
    monkeypatch.setattr(K.part_index, "rows_for_pns", lambda pns: {})
    K._idx.update({"at": None, "dasar": {}, "angka": {}, "nama": {}})
    yield
    K._idx.update({"at": None, "dasar": {}, "angka": {}, "nama": {}})


def _pns(hasil, kunci):
    return {r["part_number"] for r in hasil.get(kunci) or []}


# ── urai(): anatomi PN ───────────────────────────────────────────────────────

def test_urai_memisah_dasar_sub_dan_varian():
    assert K.urai("WG9725522011+001/1") == {
        "dasar": "WG9725522011", "sub": "001", "varian": "1"}


def test_urai_pn_polos_tanpa_akhiran():
    assert K.urai("Q1811645") == {"dasar": "Q1811645", "sub": None, "varian": None}


def test_urai_tidak_memotong_tanda_hubung_di_dalam_pn():
    # Banyak PN katalog memang mengandung '-' ('200V10311-6082/1'); memotongnya
    # akan melebur PN yang berbeda jadi satu keluarga.
    assert K.urai("200V10311-6082/1")["dasar"] == "200V10311-6082"


# ── VARIAN PEMASOK — kandidat interchange terkuat ────────────────────────────

def test_varian_pemasok_ditemukan_kasus_produksi_wg9725191800():
    """Regresi kasus nyata 2026-08-29: dijawab 'tidak ada persamaan' padahal
    katalog punya dua varian pemasok."""
    h = K.keluarga("WG9725191800")
    assert _pns(h, "varian_pemasok") == {"WG9725191800/1", "WG9725191800/2"}


def test_varian_pemasok_dua_arah_dari_anggota_ke_dasar():
    h = K.keluarga("WG9725191800/2")
    assert _pns(h, "varian_pemasok") == {"WG9725191800", "WG9725191800/1"}


def test_varian_nama_beda_bahasa_tetap_tampil_tapi_ditandai():
    # Nama Mandarin vs Inggris = kemiripan 0, tapi nomor dasarnya identik →
    # TAMPILKAN (agar tak hilang) sambil menandai bahwa namanya tak cocok.
    h = K.keluarga("WG9525523046")
    baris = [r for r in h["varian_pemasok"] if r["part_number"] == "WG9525523046/5"]
    assert baris and baris[0]["nama_cocok"] is False


# ── SUB-RAKITAN — mirip nomornya, BUKAN pengganti ────────────────────────────

def test_sub_rakitan_tidak_pernah_masuk_varian_pemasok():
    """+001 dan +002 = lembar pegas ke-1 & ke-2. Melebur mereka ke varian
    pemasok akan membuat asisten menyarankan part yang salah."""
    h = K.keluarga("WG9725522011+001/1")
    assert _pns(h, "sub_rakitan") == {"WG9725522011+002/1"}
    assert _pns(h, "varian_pemasok") == set()


# ── AWALAN SETARA — dua pagar: allow-list + nama ─────────────────────────────

def test_prefix_setara_az_wg_dengan_nama_cocok():
    h = K.keluarga("AZ9725551523")
    assert _pns(h, "prefix_setara") == {"WG9725551523/1"}


def test_prefix_nama_berbeda_ditolak_bukan_disarankan():
    h = K.keluarga("AZ9925520233")          # Balance shaft bracket
    assert _pns(h, "prefix_setara") == set()
    assert "WG9925520233" in _pns(h, "prefix_ditolak")   # Ejector housing


def test_prefix_tanpa_nama_masuk_perlu_dicek_bukan_ditolak():
    """gagal-cek ≠ tidak-ada: AZ9925520235 tak bernama di katalog kita, tapi
    SIMS mengonfirmasi ia setara WG9925520235. Menolaknya diam-diam = jawaban
    salah; menyebutnya pasti = mengarang. Jalan ketiga: kandidat perlu cek."""
    h = K.keluarga("WG9925520235")
    assert _pns(h, "prefix_perlu_dicek") == {"AZ9925520235"}
    assert _pns(h, "prefix_ditolak") == set()
    assert _pns(h, "prefix_setara") == set()


def test_keluarga_sp_tidak_pernah_dianggap_setara():
    """⛔⛔ Pagar paling penting: di keluarga SP** angka = nomor urut pemasok.
    SPHG…024 kampas rem, SPDC…024 tutup poros. Menyetarakannya mengirim mekanik
    memasang part yang salah."""
    h = K.keluarga("SPHG0000000024")
    assert _pns(h, "prefix_setara") == set()
    assert _pns(h, "prefix_perlu_dicek") == set()
    assert "SPDC0000000024" in _pns(h, "prefix_ditolak")


def test_pasangan_sp_tetap_ditolak_walau_namanya_kebetulan_mirip(monkeypatch):
    """Allow-list harus menahan sendiri, tanpa bergantung pada cek nama — dua
    baut generik di awalan SP berbeda bisa saja bernama nyaris sama."""
    monkeypatch.setattr(K.part_index, "all_parts_min", lambda: [
        ("SPDC0000000046", "Bolt M10x30"), ("SPZC0000000046", "Bolt M10x30")])
    K._idx["at"] = None
    h = K.keluarga("SPDC0000000046")
    assert _pns(h, "prefix_setara") == set()


# ── Kejujuran hasil ──────────────────────────────────────────────────────────

def test_pn_tanpa_keluarga_mengembalikan_daftar_kosong_bukan_error():
    h = K.keluarga("200V06500-6694")
    assert h["tersedia"] is True and h["jumlah"] == 0


def test_indeks_gagal_dibaca_tidak_menyamar_jadi_tidak_ada(monkeypatch):
    def _meledak():
        raise RuntimeError("indeks mati")
    monkeypatch.setattr(K, "_bangun", _meledak)
    h = K.keluarga("WG9725191800")
    assert h["tersedia"] is False
    assert "BUKAN" in h["alasan"]          # eksplisit: bukan bukti ketiadaan


def test_pn_kosong_ditolak_dengan_sopan():
    assert K.keluarga("  ")["tersedia"] is False


def test_hasil_selalu_membawa_catatan_cara_pakai():
    # Tanpa catatan, model gampang menyamakan keluarga katalog dengan
    # supersession resmi — persis pembauran yang ingin dicegah.
    h = K.keluarga("WG9725191800")
    assert "BUKAN supersession resmi" in h["catatan"]


# ── Statistik untuk blok pengetahuan ─────────────────────────────────────────

def test_ringkasan_pengetahuan_tidak_menghitung_pasangan_tanpa_nama():
    """Pasangan yang salah satu sisinya tak bernama tak membuktikan cocok
    MAUPUN beda. Memasukkannya ke penyebut dulu menekan keandalan AZ/WG dari
    89% jadi 38% — dan angka itu ikut tercetak ke prompt asisten."""
    r = K.ringkas_untuk_pengetahuan(min_pasang=1)
    az = [x for x in r["prefix_setara"] if x["pasangan"] == "AZ/WG"]
    assert az, "pasangan AZ/WG harus terhitung"
    assert az[0]["jumlah_bernama"] < az[0]["jumlah"]     # ada pasangan tak bernama
    # 3 pasang AZ/WG bernama di stub: 1523 cocok, 0233 beda, 0235 tak bernama.
    assert az[0]["persen_nama_cocok"] == 50.0


def test_rakitan_induk_disebut_saat_yang_ditanya_hanya_satu_lembar(monkeypatch):
    """User yang minta 'pegas lembar 1' kerap sebenarnya butuh rakitan utuhnya.
    Nomor induk itu ADA di katalog — menyembunyikannya membuat jawaban benar
    tapi tidak berguna."""
    monkeypatch.setattr(K.part_index, "all_parts_min", lambda: [
        ("WG9725522011+001/1", "Leaf spring assembly"),
        ("WG9725522011+002/1", "Leaf spring assembly"),
        ("WG9725522011/1", "Front plate spring assembly"),
    ])
    K._idx["at"] = None
    h = K.keluarga("WG9725522011+001/1")
    assert _pns(h, "rakitan_induk") == {"WG9725522011/1"}
    assert _pns(h, "sub_rakitan") == {"WG9725522011+002/1"}


def test_prefix_setara_tidak_menyandingkan_lembar_rakitan_yang_berbeda(monkeypatch):
    """'WG…+001' vs 'AZ…+003' = lembar pegas BERBEDA, tapi namanya sama-sama
    'Leaf spring assembly' → cek nama LOLOS dan menyamakan part yang salah.
    Hanya kesamaan nomor bagian (+NNN) yang bisa memagarinya."""
    monkeypatch.setattr(K.part_index, "all_parts_min", lambda: [
        ("WG9725522011+001/1", "Leaf spring assembly"),
        ("AZ9725522011+003/1", "Leaf spring assembly"),
        ("AZ9725522011+001/1", "Leaf spring assembly"),
    ])
    K._idx["at"] = None
    h = K.keluarga("WG9725522011+001/1")
    assert _pns(h, "prefix_setara") == {"AZ9725522011+001/1"}
