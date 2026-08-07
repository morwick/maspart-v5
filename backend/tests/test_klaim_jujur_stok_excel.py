"""KEJUJURAN KLAIM tool stok/harga/Excel (audit 2026-08-06).

Satu aturan yang diuji di semua handler di sini: **gagal mengecek ≠ tahu bahwa
tidak ada**. Sebelum perbaikan ini, kegagalan sumber data berubah jadi ANGKA &
VONIS yang terdengar pasti:

  • hitung_part: indeks Accurate kosong → total 'PASTI' + daftar "memang TIDAK
    punya harga di Accurate"; PN yang tak bisa di-resolve dipilih diam-diam lewat
    `norm_pn` polos (⛔ melanggar aturan pemilik: lookup WAJIB `index_key`);
  • cek_massal_part: exception / indeks per-gudang belum terisi → `stok_total: 0`
    disajikan sebagai fakta "barang habis";
  • _varian_pemasok: semua exception ditelan jadi None = "part ini sendirian",
    persis bug yang fitur itu tambal;
  • stok_gudang & excel_stok_gudang: jendela enrichment parsial → "Tidak ada part
    X berstok di gudang Y";
  • Permintaan Barang: kolom WAJIB 'Kts Jkt' diisi 0 karena gagal baca indeks —
    angka karangan masuk dokumen resmi ERP;
  • alternatif_ready: sumber gagal tapi ada kandidat → nol hedging;
  • cari_part: jaring terakhir SIMS dilewati diam-diam saat SIMS mati;
  • buat_penawaran: SATU kandidat pelanggan cocok-SEBAGIAN dipilih otomatis;
  • gambar_exploded: daftar balon 1 figure & maks 40 diklaim "SEMUA balon".

Semua akses data di-mock — nol jaringan, nol model.
"""
from __future__ import annotations

import pytest

from app.services import accurate, part_index, search_log, sims
from app.services import ai_assistant as ai

ADMIN = {"username": "mas", "role": "admin"}
STAF = {"username": "budi", "role": "user"}

# PN FIKTIF (⛔ jangan pakai PN nyata sebagai contoh karangan).
PN_A = "AZ9998887776"
PN_B = "AZ9998887771"
PN_C = "AZ9998887772"


# ══ 1. hitung_part — indeks kosong, norm_pn, varian ambigu (p6) ═════════════

def _hitung_dunia(monkeypatch, snap, *, index_key=None, family=None, full=None,
                  names=None):
    """Indeks Accurate tiruan untuk hitung_part. `index_key` default = PN dasar."""
    monkeypatch.setattr(accurate, "snapshot", lambda: dict(snap))
    monkeypatch.setattr(accurate, "index_key",
                        index_key or (lambda pn: (pn or "").upper().split("/")[0]))
    monkeypatch.setattr(accurate, "norm_pn", lambda pn: (pn or "").upper())
    monkeypatch.setattr(accurate, "family_summary", family or (lambda pn: None))
    monkeypatch.setattr(accurate, "stock_family",
                        full or (lambda pn: {"found": False, "varian": []}))
    monkeypatch.setattr(part_index, "rows_for_pns",
                        lambda pns: {p.upper(): {"part_name": (names or {}).get(p.upper(), "")}
                                     for p in pns})


def test_snapshot_kosong_bukan_vonis_tanpa_harga(monkeypatch):
    """Indeks belum siap → BATAL dengan pesan jujur; ⛔ bukan 'tak punya harga'."""
    _hitung_dunia(monkeypatch, {})
    out = ai._t_hitung_part({"items": [{"pn": PN_A, "qty": 2}],
                             "_grounded": {PN_A}}, ADMIN)
    assert out["found"] is False
    assert "belum siap" in out["error"].lower()
    assert "JANGAN menyimpulkan" in out["error"]
    # ⛔ tak ada angka & tak ada vonis yang lahir dari nol data
    assert "total_num" not in out and "items_tanpa_harga" not in out
    # telemetri: 'err' (belum sempat dicek), BUKAN 'nf' (tahu tak ada)
    assert ai._tool_fail_kind(out) == "err"


def test_tanpa_fallback_norm_pn(monkeypatch):
    """index_key '' = indeks MENOLAK memutuskan kartu → jangan diakali norm_pn.

    Aturan pemilik: lookup harga/stok WAJIB lewat index_key/rows_for_pns pemaaf.
    Dulu `snap.get(index_key(pn) or norm_pn(pn))` menyelundupkan kunci polos —
    harga kartu yang belum tentu benar ikut ke total."""
    _hitung_dunia(monkeypatch, {PN_A: {"harga": 500_000, "stok": 4}},
                  index_key=lambda pn: "")          # menolak memutuskan
    out = ai._t_hitung_part({"items": [{"pn": PN_A}], "_grounded": {PN_A}}, ADMIN)
    assert out["items"] == [] and out["total_num"] == 0
    assert out["items_tanpa_harga"] == [PN_A]       # jujur: harga tak terbaca


def test_varian_ambigu_tak_dihitung_diam_diam(monkeypatch):
    """PN dasar dengan ≥2 kartu pemasok → masuk 'perlu_pilih_varian', bukan
    dipilihkan sistem (harga tiap kartu beda)."""
    _hitung_dunia(
        monkeypatch, {PN_B: {"harga": 100_000, "stok": 9}},
        index_key=lambda pn: "" if pn.upper() == PN_A else pn.upper(),
        family=lambda pn: {"n": 2} if pn.upper() == PN_A else None,
        full=lambda pn: {"varian": [
            {"pn": f"{PN_A}/SN", "available_to_sell": 1069.0, "price": 285_000},
            {"pn": f"{PN_A}/SH", "available_to_sell": 2.0, "price": 455_000}]})
    out = ai._t_hitung_part({"items": [{"pn": PN_A, "qty": 3}, {"pn": PN_B, "qty": 2}],
                             "_grounded": {PN_A, PN_B}}, ADMIN)
    assert [i["part_number"] for i in out["items"]] == [PN_B]
    assert out["total_num"] == 200_000              # ⛔ kartu ambigu TIDAK ikut total
    amb = out["perlu_pilih_varian"][0]
    assert amb["pn_diminta"] == PN_A and amb["qty"] == 3
    assert [v["kode"] for v in amb["varian"]] == [f"{PN_A}/SN", f"{PN_A}/SH"]
    assert "JANGAN memilihkan sendiri" in out["catatan"]
    assert "total SELURUH daftar" in out["catatan"]


# ══ 2. cek_massal_part — stok gagal ≠ 0 (p3) ════════════════════════════════

@pytest.fixture
def massal(monkeypatch):
    monkeypatch.setattr(ai, "_boleh_harga", lambda u: True)
    monkeypatch.setattr(ai, "_boleh_stok", lambda u: True)
    monkeypatch.setattr(ai.part_index, "rows_for_pns",
                        lambda pns: {PN_A: {"part_name": "Filter solar"},
                                     PN_B: {"part_name": "Baut"}})
    monkeypatch.setattr(ai.accurate, "snapshot", lambda: {})
    monkeypatch.setattr(ai.accurate, "index_key", lambda pn: pn)
    monkeypatch.setattr(ai.accurate, "family_summary", lambda pn: None)
    monkeypatch.setattr(ai.harga, "shipping_weight_for",
                        lambda pn, allow_remote=False: 0)
    return monkeypatch


def test_stok_exception_jadi_null_bukan_nol(massal):
    def _rincian(pn):
        if pn == PN_B:
            raise RuntimeError("indeks per-gudang rusak")
        return 11, "01.Jakarta: 11"

    massal.setattr(ai, "_rincian_gudang_str", _rincian)
    r = ai._t_cek_massal_part({"daftar_pn": f"{PN_A}, {PN_B}"}, ADMIN)
    baris = {it["pn"]: it for it in r["part"]}
    assert baris[PN_A]["stok_total"] == 11               # yang sukses tetap angka
    assert baris[PN_B]["stok_total"] is None             # ⛔ BUKAN 0
    assert "GAGAL dicek" in baris[PN_B]["catatan_pn"]
    assert r["stok_gagal_dicek"] == [PN_B]
    assert "JANGAN" in r["catatan"] and "0/habis/kosong" in r["catatan"]


def test_indeks_gudang_belum_siap_semua_null(massal):
    """gudang_enriched_count()==0 & tak satu pun PN punya rincian → semua nol di
    atas hanyalah 'belum terbaca'."""
    massal.setattr(ai, "_rincian_gudang_str", lambda pn: (0, ""))
    massal.setattr(ai.accurate, "gudang_enriched_count", lambda: 0)
    r = ai._t_cek_massal_part({"daftar_pn": f"{PN_A}, ZZTAKDIKENAL9"}, ADMIN)
    baris = {it["pn"]: it for it in r["part"]}
    assert baris[PN_A]["stok_total"] is None
    # PN tanpa nama katalog DAN stok tak terbaca → belum bisa divonis "tidak ada"
    assert r["belum_pasti"] == ["ZZTAKDIKENAL9"]
    assert r["tak_ada"] == 0
    assert "JANGAN memvonis" in r["catatan"]


def test_massal_nihil_total_karena_belum_pasti_telemetri_err(massal):
    """SEMUA PN masuk 'belum_pasti' (indeks per-gudang tumbang & namanya tak ada
    di katalog) → found=False. Tanpa penanda, telemetri mencatatnya 'nf' = lookup
    jujur nihil, padahal tak satu pun benar-benar dicek."""
    massal.setattr(ai, "_rincian_gudang_str", lambda pn: (0, ""))
    massal.setattr(ai.accurate, "gudang_enriched_count", lambda: 0)
    r = ai._t_cek_massal_part({"daftar_pn": "ZZTAKDIKENAL9, ZZTAKDIKENAL8"}, ADMIN)
    assert r["found"] is False and r["ketemu"] == 0
    assert len(r["belum_pasti"]) == 2
    assert r["_cek_tak_lengkap"] is True
    assert ai._tool_fail_kind(r) == "err"           # ⛔ bukan 'nf'


def test_massal_ada_yang_ketemu_tak_ditandai_tak_lengkap(massal):
    """Sebagian ketemu → hasilnya SAH sebagai lookup; jangan tandai 'err' —
    peringatan per-baris & catatan sudah menanggung ketidak-pastiannya."""
    massal.setattr(ai, "_rincian_gudang_str",
                   lambda pn: (5, "01.Jakarta: 5") if pn == PN_A else (0, ""))
    massal.setattr(ai.accurate, "gudang_enriched_count", lambda: 0)
    r = ai._t_cek_massal_part({"daftar_pn": f"{PN_A}, ZZTAKDIKENAL9"}, ADMIN)
    assert r["found"] is True and r["ketemu"] == 1
    assert "_cek_tak_lengkap" not in r
    assert ai._tool_fail_kind(r) == ""


def test_indeks_hidup_maka_nol_tetap_nol(massal):
    """Bukti indeks hidup (satu PN punya stok) → 0 pada PN lain SAH, jangan
    di-hedge berlebihan."""
    massal.setattr(ai, "_rincian_gudang_str",
                   lambda pn: (11, "01.Jakarta: 11") if pn == PN_A else (0, ""))
    massal.setattr(ai.accurate, "gudang_enriched_count", lambda: 0)
    r = ai._t_cek_massal_part({"daftar_pn": f"{PN_A}, {PN_B}"}, ADMIN)
    baris = {it["pn"]: it for it in r["part"]}
    assert baris[PN_B]["stok_total"] == 0
    assert "stok_gagal_dicek" not in r and "belum_pasti" not in r


def test_kartu_pemasok_gagal_dicek_ditandai(massal):
    def _boom(pn):
        raise RuntimeError("indeks varian rusak")

    massal.setattr(ai, "_rincian_gudang_str", lambda pn: (5, "01.Jakarta: 5"))
    massal.setattr(ai.accurate, "family_summary", _boom)
    r = ai._t_cek_massal_part({"daftar_pn": PN_A}, ADMIN)
    assert r["varian_gagal_dicek"] == [PN_A]
    assert "cek kartu pemasok gagal" in r["part"][0]["catatan_pn"]
    assert "SATU kartu pemasok" in r["catatan"]


# ══ 3. _varian_pemasok — sentinel gagal_dicek (p3) ═══════════════════════════

def test_varian_pemasok_gagal_bukan_none(monkeypatch):
    """Gagal baca ≠ 'part ini sendirian' — itu justru bug produksi yang ditambal."""
    monkeypatch.setattr(ai, "_boleh_harga", lambda u: True)
    monkeypatch.setattr(ai, "_boleh_stok", lambda u: True)

    def _boom(pn):
        raise KeyError("by_base")

    monkeypatch.setattr(ai.accurate, "family_summary", _boom)
    assert ai._varian_pemasok(PN_A, ADMIN) == {"gagal_dicek": True}

    out = ai._sisip_varian_pemasok({"found": True, "stok_total": "2 Pc"}, PN_A, ADMIN)
    assert out["varian_pemasok_gagal_dicek"] is True
    assert "CEK KARTU PEMASOK GAGAL" in out["catatan_varian"]
    assert "SATU kartu pemasok" in out["catatan_varian"]
    assert "varian_pemasok" not in out           # tak ada angka karangan


def test_varian_pemasok_sendirian_tetap_none(monkeypatch):
    monkeypatch.setattr(ai, "_boleh_harga", lambda u: True)
    monkeypatch.setattr(ai, "_boleh_stok", lambda u: True)
    monkeypatch.setattr(ai.accurate, "family_summary", lambda pn: None)
    assert ai._varian_pemasok(PN_A, ADMIN) is None


# ══ 4. stok_gudang & excel_stok_gudang — progres enrichment (p3/p6) ═════════

_ITEMS = [{"pn": PN_A, "name": "Clutch driven disc", "price": 100_000.0}]
_ROWS = [{"part_number": PN_A, "part_name": "Clutch driven disc",
          "file": "HOWO.xlsx", "harga": "Rp 100"}]


@pytest.fixture
def gudang_dunia(monkeypatch):
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q], []))
    monkeypatch.setattr(ai, "_umbrella_keywords", lambda q: [])
    monkeypatch.setattr(ai.gudang_config, "coords_map",
                        lambda: {"01.Jakarta": (0, 0), "23.Medan": (0, 0)})
    monkeypatch.setattr(ai.accurate, "items_matching", lambda terms, limit=400: list(_ITEMS))
    monkeypatch.setattr(ai.part_index, "search_part_name", lambda t: list(_ROWS))
    monkeypatch.setattr(ai.part_index, "name_for", lambda pn: "")
    monkeypatch.setattr(ai.accurate, "gudang_breakdown",
                        lambda pn: {"01.Jakarta": 3.0} if pn == PN_A else {})
    return monkeypatch


def _snap(berstok: int, tanpa_stok: int = 0) -> dict:
    """Snapshot indeks Accurate tiruan: `berstok` entri stok>0 + `tanpa_stok`
    entri katalog ber-harga yang stoknya 0. Di produksi golongan kedua inilah
    MAYORITAS isi snapshot — dan enrichment per-gudang tak pernah menyentuhnya."""
    snap = {f"K{i}": {"stok": 3.0, "harga": 100_000} for i in range(berstok)}
    snap.update({f"Z{i}": {"stok": 0.0, "harga": 100_000} for i in range(tanpa_stok)})
    return snap


def _progres(monkeypatch, n_enrich, berstok, tanpa_stok=0):
    """`berstok` = POPULASI yang di-enrich (pembanding rasio yang benar)."""
    monkeypatch.setattr(ai.accurate, "gudang_enriched_count", lambda: n_enrich)
    monkeypatch.setattr(ai.accurate, "snapshot",
                        lambda: _snap(berstok, tanpa_stok))


def test_snapshot_berstok_count_nilai_kotor(monkeypatch):
    """Pembanding populasi = entri stok>0 saja; nilai kotor (string/None/entri
    bukan dict dari indeks lama) tak boleh meledak atau ikut terhitung."""
    monkeypatch.setattr(accurate, "snapshot", lambda: {
        "A": {"stok": 3.0}, "B": {"stok": 0.0}, "C": {"stok": "5"},
        "D": {"stok": None}, "E": {"stok": "x"}, "F": {}, "G": None})
    assert accurate.snapshot_berstok_count() == 2        # hanya A & C


def test_progres_pembanding_hanya_barang_berstok(gudang_dunia):
    """⛔ REGRESI PRODUKSI: pembagi dulu `len(snapshot())` = SELURUH katalog
    ber-harga, padahal enrichment HANYA menarik barang berstok. Efeknya rasio
    nyaris selalu <90% → stok_gudang & excel_stok_gudang macet di 'belum bisa
    dipastikan' SELAMANYA. 900 dari 900 barang berstok = 100% SIAP, sekalipun
    katalognya 50.000 baris."""
    _progres(gudang_dunia, 900, 900, tanpa_stok=50_000)
    assert ai._enrich_progres() == (900, 900, 1.0)
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "medan"}, ADMIN)
    assert "Tidak ada part" in r["catatan"]              # vonis kosong BOLEH lagi
    assert "BELUM BISA DIPASTIKAN" not in r["catatan"]
    assert r["progres_indeks_gudang"] == {"ter_enrich": 900, "total": 900,
                                          "lengkap": True}


def test_progres_berstok_banyak_enrich_sedikit_tetap_ragu(gudang_dunia):
    """Kebalikannya: enrichment memang baru jalan sebagian → peringatan TETAP."""
    _progres(gudang_dunia, 90, 900, tanpa_stok=50_000)
    assert ai._enrich_progres()[2] == pytest.approx(0.1)
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "medan"}, ADMIN)
    assert "BELUM BISA DIPASTIKAN" in r["catatan"]
    assert "900 barang berstok" in r["catatan"]         # pembanding disebut jujur


def test_progres_katalog_besar_tanpa_stok_tak_macet(gudang_dunia):
    """Nol barang berstok (semua katalog stoknya 0) = tak ada yang PERLU
    di-enrich → 'siap', bukan macet."""
    _progres(gudang_dunia, 3, 0, tanpa_stok=20_000)
    assert ai._enrich_progres() == (3, 0, 1.0)
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "medan"}, ADMIN)
    assert "Tidak ada part" in r["catatan"]


def test_stok_gudang_parsial_tak_boleh_vonis_kosong(gudang_dunia):
    _progres(gudang_dunia, 50, 1000)                     # 5% — jauh di bawah ambang
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "medan"}, ADMIN)
    assert r["jumlah_part_ready"] == 0
    assert "BELUM BISA DIPASTIKAN" in r["catatan"]
    assert "Tidak ada part" not in r["catatan"]
    assert r["progres_indeks_gudang"] == {"ter_enrich": 50, "total": 1000,
                                          "lengkap": False}


def test_stok_gudang_lengkap_boleh_vonis_kosong(gudang_dunia):
    _progres(gudang_dunia, 990, 1000)                    # 99% — daftar mewakili
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "medan"}, ADMIN)
    assert "Tidak ada part" in r["catatan"]
    assert r["progres_indeks_gudang"]["lengkap"] is True


def test_stok_gudang_hasil_parsial_diberi_peringatan(gudang_dunia):
    _progres(gudang_dunia, 50, 1000)
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "jakarta"}, ADMIN)
    assert r["jumlah_part_ready"] == 1
    assert "BELUM tentu lengkap" in r["catatan"]


def test_stok_gudang_echo_filter_unit(gudang_dunia):
    _progres(gudang_dunia, 990, 1000)
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "jakarta",
                           "unit": "HOWO"}, ADMIN)
    assert r["unit_filter"] == "HOWO"
    assert "unit 'HOWO'" in r["catatan"]
    # tanpa filter unit, field-nya null (bukan hilang) agar konsisten cari_part
    assert ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "jakarta"},
                             ADMIN)["unit_filter"] is None


def test_excel_stok_gudang_parsial_tak_bikin_file(gudang_dunia):
    _progres(gudang_dunia, 50, 1000)
    gudang_dunia.setattr(ai.ai_export, "stash_export",
                         lambda *a, **k: pytest.fail("file tak boleh dibuat"))
    r = ai._t_excel_stok_gudang({"kata_kunci": "kopling", "gudang": "medan"}, ADMIN)
    assert r["found"] is False
    assert "BELUM bisa dipastikan" in r["error"]
    assert r["progres_indeks_gudang"]["ter_enrich"] == 50
    assert ai._tool_fail_kind(r) == "err"        # belum dicek ≠ tidak ada


def test_excel_stok_gudang_lengkap_pesan_lama(gudang_dunia):
    _progres(gudang_dunia, 990, 1000)
    r = ai._t_excel_stok_gudang({"kata_kunci": "kopling", "gudang": "medan"}, ADMIN)
    assert r["found"] is False and "Tidak ada part" in r["error"]


# ══ 5. Permintaan Barang — kolom WAJIB 'Kts Jkt' (p3) ═══════════════════════

_PR_ITEM = {"id": 17451, "no": "0001." + PN_A, "pn": PN_A, "name": "Filter",
            "unit_id": 100, "unit": "PCS", "price": 250_000}


@pytest.fixture
def pr_dunia(monkeypatch):
    dibuat: list[dict] = []
    monkeypatch.setattr(ai.accurate, "available", lambda: True)
    monkeypatch.setattr(ai.accurate, "ensure_session_force", lambda: {"dsi": "d"})
    monkeypatch.setattr(ai.accurate, "item_for_quotation",
                        lambda pn: dict(_PR_ITEM) if pn.upper() in (PN_A, PN_B) else None)
    monkeypatch.setattr(ai.accurate, "next_pr_number", lambda: "PERMINTAAN-09")
    monkeypatch.setattr(ai.accurate, "create_purchase_requisition",
                        lambda **kw: dibuat.append(kw) or {"id": 1, "number": kw["number"]})
    return dibuat, monkeypatch


def test_pr_kts_jkt_gagal_dibaca_membatalkan(pr_dunia):
    dibuat, monkeypatch = pr_dunia

    def _boom(pn):
        raise RuntimeError("indeks tumbang")

    monkeypatch.setattr(ai.accurate, "gudang_breakdown", _boom)
    r = ai._t_buat_permintaan_barang({"barang": [{"part_number": PN_A, "qty": 2}]}, ADMIN)
    assert r["found"] is False and not dibuat
    assert "GAGAL dibaca" in r["error"] and "DIBATALKAN" in r["error"]
    assert "pratinjau" not in r                      # ⛔ tak ada pratinjau berangka palsu
    assert ai._tool_fail_kind(r) == "err"            # gagal baca, bukan 'tak ada'


def test_pr_indeks_gudang_belum_siap_membatalkan(pr_dunia):
    dibuat, monkeypatch = pr_dunia
    monkeypatch.setattr(ai.accurate, "gudang_breakdown", lambda pn: {})
    monkeypatch.setattr(ai.accurate, "gudang_enriched_count", lambda: 0)
    r = ai._t_buat_permintaan_barang(
        {"barang": [{"part_number": PN_A}], "konfirmasi": True}, ADMIN)
    assert r["found"] is False and not dibuat        # ⛔ dokumen ERP tak dibuat
    assert "Kts Jkt" in r["error"]


def test_pr_nol_sah_bila_indeks_hidup(pr_dunia):
    """PN yang memang tak ada di Jakarta tetap 0 — asal indeksnya terbukti hidup
    (PN lain di dokumen yang sama punya rincian)."""
    dibuat, monkeypatch = pr_dunia
    monkeypatch.setattr(ai.accurate, "gudang_breakdown",
                        lambda pn: {"01.Jakarta": 28, "02.Pekanbaru": 5}
                        if pn == PN_A else {})
    monkeypatch.setattr(ai.accurate, "gudang_enriched_count", lambda: 0)
    r = ai._t_buat_permintaan_barang(
        {"barang": [{"part_number": PN_A}, {"part_number": PN_B}]}, ADMIN)
    assert r["pratinjau"] is True
    baris = {b["part_number"]: b for b in r["baris"]}
    assert baris[PN_A]["kts_jkt"] == 28 and baris[PN_B]["kts_jkt"] == 0


# ══ 6. alternatif_ready — kandidat ada TAPI sumber gagal (p3) ═══════════════

def test_alternatif_kandidat_ada_sumber_gagal_diberi_hedging(monkeypatch):
    monkeypatch.setattr(ai.part_index, "gudang_breakdown",
                        lambda pn: {"01.Jakarta": 3} if pn.upper() == PN_B else {})
    monkeypatch.setattr(ai.gudang, "shippable", lambda bd: dict(bd))
    monkeypatch.setattr(ai.reservations, "reserved_map", lambda: {})
    monkeypatch.setattr(ai.sims, "get_part_equivalents",
                        lambda pn: {"found": True,
                                    "digantikan_oleh": [{"pn": PN_B, "nama": "Filter baru"}]})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka="": {})
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])
    out = ai._t_alternatif_ready({"part_number": PN_A}, ADMIN)
    assert out["found"] is True
    assert out["sumber_gagal"] == ["weichai"]
    assert "BELUM" in out["catatan_sumber_gagal"]
    assert "JANGAN mengklaim 'sudah dicek SIMS & Weichai'" in out["catatan_sumber_gagal"]


def test_alternatif_semua_sumber_ok_tanpa_hedging(monkeypatch):
    monkeypatch.setattr(ai.part_index, "gudang_breakdown",
                        lambda pn: {"01.Jakarta": 3} if pn.upper() == PN_B else {})
    monkeypatch.setattr(ai.gudang, "shippable", lambda bd: dict(bd))
    monkeypatch.setattr(ai.reservations, "reserved_map", lambda: {})
    monkeypatch.setattr(ai.sims, "get_part_equivalents",
                        lambda pn: {"found": True,
                                    "digantikan_oleh": [{"pn": PN_B, "nama": "Filter baru"}]})
    monkeypatch.setattr(ai.epc_weichai, "replace_part",
                        lambda pn, rangka="": {"found": False, "part_number": pn})
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])
    out = ai._t_alternatif_ready({"part_number": PN_A}, ADMIN)
    assert out["found"] is True and "catatan_sumber_gagal" not in out


# ══ 7. cari_part — jaring terakhir SIMS tak sempat dicek (p3) ═══════════════

@pytest.fixture(autouse=True)
def _miss_ke_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(search_log, "_path", lambda: tmp_path / "misses.json")
    search_log._state["data"] = None


def test_cari_part_sims_mati_bukan_vonis_tak_ada(monkeypatch):
    monkeypatch.setattr(sims, "available", lambda: False)
    r = ai._t_cari_part({"query": "zzqx spoiler nihil"}, ADMIN)
    assert r["found"] is False and r["jumlah_part_unik"] == 0
    assert r["sims_tak_dicek"] is True
    assert "JARING TERAKHIR" in r["catatan"]
    assert "JANGAN memvonis" in r["catatan"]


def test_cari_part_sims_hidup_nihil_tetap_vonis_biasa(monkeypatch):
    monkeypatch.setattr(sims, "available", lambda: True)
    monkeypatch.setattr(sims, "get_part_info", lambda pn, force_refresh=False: {})
    r = ai._t_cari_part({"query": "zzqx spoiler nihil"}, ADMIN)
    assert r["found"] is False
    assert "sims_tak_dicek" not in r            # benar-benar dicek & nihil


def test_cari_part_sims_meledak_dianggap_tak_dicek(monkeypatch):
    monkeypatch.setattr(sims, "available", lambda: True)
    monkeypatch.setattr(sims, "get_part_info", lambda pn, force_refresh=False: {})

    def _boom(name, limit=8):
        raise RuntimeError("SIMS down")

    monkeypatch.setattr(sims, "search_master_by_name", _boom)
    r = ai._t_cari_part({"query": "zzqx spoiler nihil"}, ADMIN)
    assert r["sims_tak_dicek"] is True          # exception ≠ 'sudah dicek, nihil'


def test_cari_part_sims_mati_telemetri_err_bukan_nf(monkeypatch):
    """Telemetri: giliran SIMS-mati harus terhitung 'err' (jaring terakhir tak
    ditarik), BUKAN 'nf' (lookup jujur nihil) — kalau tidak, statistik memberi
    kesan part-part itu memang tak ada di data."""
    monkeypatch.setattr(sims, "available", lambda: False)
    r = ai._t_cari_part({"query": "zzqx spoiler nihil"}, ADMIN)
    assert r["_cek_tak_lengkap"] is True
    assert ai._tool_fail_kind(r) == "err"


def test_cari_part_sims_mati_tak_mencatat_celah_kamus(monkeypatch):
    """'Pencarian Nihil' = kandidat istilah lapangan yang belum dikenali kamus.
    Nihil karena SIMS mati bukan bukti apa pun tentang kamus → jangan dicatat."""
    dicatat: list = []
    monkeypatch.setattr(search_log, "record_miss",
                        lambda q, jenis, sumber: dicatat.append(q))
    monkeypatch.setattr(sims, "available", lambda: False)
    ai._t_cari_part({"query": "zzqx spoiler nihil"}, ADMIN)
    assert dicatat == []

    # Pembanding: SIMS HIDUP & benar-benar nihil → tetap dicatat (kanal kamus
    # tak boleh ikut mati) dan telemetrinya 'nf' apa adanya.
    monkeypatch.setattr(sims, "available", lambda: True)
    monkeypatch.setattr(sims, "get_part_info", lambda pn, force_refresh=False: {})
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q], []))
    r = ai._t_cari_part({"query": "zzqx spoiler nihil"}, ADMIN)
    assert dicatat == ["zzqx spoiler nihil"]
    assert ai._tool_fail_kind(r) == "nf"


# ══ 8. buat_penawaran — pelanggan cocok SEBAGIAN (p3) ══════════════════════

@pytest.fixture
def penawaran(monkeypatch):
    dibuat = {"sq": 0}
    monkeypatch.setattr(ai.accurate, "available", lambda: True)
    monkeypatch.setattr(ai.accurate, "ensure_session_force", lambda: {"dsi": "d"})
    monkeypatch.setattr(ai.accurate, "item_for_quotation",
                        lambda pn: {"id": 1, "no": "x", "pn": pn.upper(), "name": "Filter",
                                    "unit_id": 100, "unit": "Pc", "price": 250_000})
    monkeypatch.setattr(ai.accurate, "index_key", lambda pn: pn.upper())
    monkeypatch.setattr(ai.accurate, "family_summary", lambda pn: None)
    monkeypatch.setattr(ai.accurate, "next_quotation_number", lambda: "MASPART-01")
    monkeypatch.setattr(ai.accurate, "sales_quotation_pdf", lambda qid, layout_id=50: b"%PDF")
    monkeypatch.setattr(ai.accurate, "logout", lambda: True)
    monkeypatch.setattr(ai.accurate, "suppress_autologin", lambda *a, **k: None)

    def _create(**kw):
        dibuat["sq"] += 1
        dibuat.update(kw)
        return {"id": 9, "number": kw["number"], "total": 250_000}

    monkeypatch.setattr(ai.accurate, "create_sales_quotation", _create)
    return dibuat, monkeypatch


_BARANG = [{"part_number": PN_A, "qty": 1}]


def test_pelanggan_cocok_sebagian_tunggal_wajib_konfirmasi(penawaran):
    """Accurate mencocokkan SUBSTRING: 'cio' → 'ARGCIO'. Satu kandidat pun bukan
    izin memilihkan — dokumen resmi salah alamat susah ditarik kembali."""
    dibuat, monkeypatch = penawaran
    monkeypatch.setattr(ai.accurate, "search_customers",
                        lambda q, limit=20: [{"id": 7, "no": "001-9", "name": "ARGCIO MANDIRI"}])
    r = ai._t_buat_penawaran({"pelanggan": "cio", "barang": _BARANG}, ADMIN)
    assert r["found"] is False and r["perlu_klarifikasi"] is True
    assert dibuat["sq"] == 0                       # ⛔ tak ada dokumen terbuat
    assert r["kandidat"] == [{"nama": "ARGCIO MANDIRI", "kode": "001-9"}]
    assert "maksudnya 'ARGCIO MANDIRI'?" in r["pesan"]


def test_pelanggan_nama_sama_beda_bentuk_badan_usaha_tetap_jalan(penawaran):
    """'anugerah' vs 'CV ANUGERAH' = nama yang SAMA (bentuk badan usaha), bukan
    tebakan — jangan menambah pertanyaan yang tak perlu ke admin."""
    dibuat, monkeypatch = penawaran
    monkeypatch.setattr(ai.accurate, "search_customers",
                        lambda q, limit=20: [{"id": 2, "no": "001", "name": "CV ANUGERAH"}])
    r = ai._t_buat_penawaran({"pelanggan": "anugerah", "barang": _BARANG}, ADMIN)
    assert r["found"] is True and dibuat["sq"] == 1
    assert r["pelanggan"] == "CV ANUGERAH"


def test_pelanggan_nama_kembar_persis_tetap_ditanya(penawaran):
    dibuat, monkeypatch = penawaran
    monkeypatch.setattr(ai.accurate, "search_customers",
                        lambda q, limit=20: [{"id": 2, "no": "001", "name": "CV ANUGERAH"},
                                             {"id": 3, "no": "002", "name": "CV ANUGERAH"}])
    r = ai._t_buat_penawaran({"pelanggan": "CV ANUGERAH", "barang": _BARANG}, ADMIN)
    assert r["perlu_klarifikasi"] is True and dibuat["sq"] == 0
    assert len(r["kandidat"]) == 2


def test_pelanggan_bentuk_badan_usaha_beda_wajib_konfirmasi(penawaran):
    """'PT ANUGERAH' vs 'CV ANUGERAH' = DUA BADAN USAHA BERBEDA. _pel_kanonik
    membuang bentuknya sehingga keduanya kembar, dan penawaran RESMI bisa terbit
    atas nama entitas yang salah — begitu user MENYEBUT bentuknya, bentuk itu
    wajib ikut cocok."""
    dibuat, monkeypatch = penawaran
    monkeypatch.setattr(ai.accurate, "search_customers",
                        lambda q, limit=20: [{"id": 2, "no": "001", "name": "CV ANUGERAH"}])
    r = ai._t_buat_penawaran({"pelanggan": "PT ANUGERAH", "barang": _BARANG}, ADMIN)
    assert r["found"] is False and r["perlu_klarifikasi"] is True
    assert dibuat["sq"] == 0                       # ⛔ tak ada dokumen terbuat
    assert r["kandidat"] == [{"nama": "CV ANUGERAH", "kode": "001"}]
    # Alasannya disebut jelas — "cocok sebagian" saja terbaca seperti salah ketik
    assert r["beda_badan_usaha"] is True
    assert "BENTUK BADAN USAHA BERBEDA" in r["pesan"]


def test_pelanggan_toko_bukan_cv(penawaran):
    dibuat, monkeypatch = penawaran
    monkeypatch.setattr(ai.accurate, "search_customers",
                        lambda q, limit=20: [{"id": 2, "no": "001", "name": "CV ANUGERAH"}])
    r = ai._t_buat_penawaran({"pelanggan": "TOKO ANUGERAH", "barang": _BARANG}, ADMIN)
    assert r["perlu_klarifikasi"] is True and dibuat["sq"] == 0


def test_pelanggan_bentuk_sama_beda_tanda_baca_tetap_jalan(penawaran):
    """Pemaafan yang TERSISA: hanya tanda baca/spasi, bentuknya tetap sama."""
    dibuat, monkeypatch = penawaran
    monkeypatch.setattr(ai.accurate, "search_customers",
                        lambda q, limit=20: [{"id": 2, "no": "001", "name": "PT. ANUGERAH"}])
    r = ai._t_buat_penawaran({"pelanggan": "PT ANUGERAH", "barang": _BARANG}, ADMIN)
    assert r["found"] is True and dibuat["sq"] == 1
    assert r["pelanggan"] == "PT. ANUGERAH"


def test_pel_kanonik():
    assert ai._pel_kanonik("CV. Anugerah Jaya") == "anugerah jaya"
    assert ai._pel_kanonik("PT ARGCIO Mandiri Tbk") == "argcio mandiri"
    assert ai._pel_kanonik("cio") != ai._pel_kanonik("ARGCIO")      # ⛔ bukan kecocokan


def test_pel_sebut_bentuk():
    assert ai._pel_sebut_bentuk("PT ANUGERAH") is True
    assert ai._pel_sebut_bentuk("cv. anugerah jaya") is True
    assert ai._pel_sebut_bentuk("PT ARGCIO Mandiri Tbk") is True     # bentuk di akhir
    assert ai._pel_sebut_bentuk("anugerah") is False                 # kanonik boleh
    assert ai._pel_sebut_bentuk("") is False
    assert ai._pel_norm("PT. Anugerah") == ai._pel_norm("pt anugerah")
    assert ai._pel_norm("PT ANUGERAH") != ai._pel_norm("CV ANUGERAH")


# ══ 9. gambar_exploded — cakupan daftar balon (p6) ══════════════════════════

def test_daftar_balon_tak_mengklaim_semua(monkeypatch):
    """Daftar = figure PERTAMA & maks 40 baris. Klaim 'SEMUA balon di gambar'
    membuat model memvonis 'balon N tidak ada' untuk figure/baris yang memang
    tak pernah ikut."""
    mati = ai.get_settings().model_copy(update={"epc_exploded_reverse": False})
    monkeypatch.setattr(ai, "get_settings", lambda: mati)
    monkeypatch.setattr(ai.ai_export, "stash_builder",
                        lambda judul, spec, ext="png": ("img1", "gambar.png"))
    items = [{"balon": i, "pn": f"AZ99988877{i:02d}", "nama": f"Part {i}"}
             for i in range(1, 46)]
    monkeypatch.setattr(ai.epc_bom, "exploded_figures", lambda rangka, pn, kategori: {
        "found": True, "frame_number": "LZZTEST",
        "figures": [{"nama": "Rumah kopling", "svg": "a.svg", "balon": 3,
                     "kategori": kategori, "jumlah_item": len(items),
                     "items_ringkas": items},
                    {"nama": "Poros kopling", "svg": "b.svg", "balon": 8,
                     "kategori": kategori, "jumlah_item": 2,
                     "items_ringkas": [{"balon": 8, "pn": PN_C, "nama": "Poros"}]}]})
    out = ai._gambar_exploded_atlas_impl(
        {"rangka": "LZZTEST", "pn": PN_A, "kategori": "kopling"}, ADMIN)
    assert len(out["daftar_balon_gambar"]) == 40
    assert out["daftar_balon_figure"] == "Rumah kopling"
    assert out["daftar_balon_cakupan"] == {"ditampilkan": 40, "total_item_figure": 45,
                                           "jumlah_figure": 2}
    assert "SEMUA balon" not in out["catatan"]
    assert "figure 'Rumah kopling' saja" in out["catatan"]
    assert "40 dari 45 item" in out["catatan"]
    assert "JANGAN bilang balon itu tidak ada" in out["catatan"]


def test_daftar_balon_satu_figure_utuh_apa_adanya(monkeypatch):
    mati = ai.get_settings().model_copy(update={"epc_exploded_reverse": False})
    monkeypatch.setattr(ai, "get_settings", lambda: mati)
    monkeypatch.setattr(ai.ai_export, "stash_builder",
                        lambda judul, spec, ext="png": ("img1", "gambar.png"))
    monkeypatch.setattr(ai.epc_bom, "exploded_figures", lambda rangka, pn, kategori: {
        "found": True, "frame_number": "LZZTEST",
        "figures": [{"nama": "Rumah kopling", "svg": "a.svg", "balon": 3,
                     "kategori": kategori, "jumlah_item": 2,
                     "items_ringkas": [{"balon": 3, "pn": PN_A, "nama": "Rumah"},
                                       {"balon": 4, "pn": PN_C, "nama": "Baut"}]}]})
    out = ai._gambar_exploded_atlas_impl(
        {"rangka": "LZZTEST", "pn": PN_A, "kategori": "kopling"}, ADMIN)
    assert out["daftar_balon_cakupan"] == {"ditampilkan": 2, "total_item_figure": 2,
                                           "jumlah_figure": 1}
    assert "(2 item)" in out["catatan"] and "dipotong" not in out["catatan"]
