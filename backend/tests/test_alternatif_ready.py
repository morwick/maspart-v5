"""Tool asisten `alternatif_ready` — ADMIN-ONLY.

Part habis → carikan PN pengganti resmi (SIMS sasis + Weichai mesin) yang stoknya
BENAR-BENAR siap kirim. 'Siap kirim' harus memakai definisi yang SAMA dengan checkout
(stok Accurate − reservasi aktif, hanya gudang yang boleh mengirim) — kalau tidak,
asisten menjanjikan barang yang ternyata tak bisa dibeli.
"""
from app.services import ai_assistant as A

ADMIN = {"username": "mas", "role": "admin"}
CABANG = {"username": "jkt", "role": "user"}
PEMBELI = {"username": "budi", "role": "pembeli"}

HABIS = "WG9725190070"       # PN yang ditanya (stok kosong)
BARU = "WG9725190071"        # penggantinya (ready)
LAMA = "WG9725190069"        # part lama yang digantikan (masih ada stok)


def _setup(monkeypatch, stok=None, reservasi=None, sims_res=None, shippable_off=()):
    stok = stok or {}
    monkeypatch.setattr(A.part_index, "gudang_breakdown", lambda pn: dict(stok.get(pn.upper(), {})))
    monkeypatch.setattr(A.gudang, "shippable",
                        lambda bd: {g: q for g, q in bd.items() if g not in shippable_off})
    monkeypatch.setattr(A.reservations, "reserved_map", lambda: dict(reservasi or {}))
    monkeypatch.setattr(A.sims, "get_part_equivalents",
                        lambda pn: dict(sims_res) if sims_res else {"found": False})
    # Weichai: DICEK & memang nihil. ⚠️ `{}` punya arti LAIN (tak terkonfigurasi /
    # exception = TIDAK dicek) — memakainya di sini dulu membuat test seolah
    # membuktikan "tidak ada pengganti" padahal tak satu sumber pun ditanya.
    monkeypatch.setattr(A.epc_weichai, "replace_part",
                        lambda pn, rangka="": {"found": False, "part_number": pn})
    monkeypatch.setattr(A.part_index, "search_exact_pns", lambda pns: [])


_SIMS = {"found": True, "digantikan_oleh": [{"pn": BARU, "nama": "Kampas Rem Baru"}],
         "menggantikan": [{"pn": LAMA, "nama": "Kampas Rem Lama"}]}


# ── Inti: pengganti yang ready ditemukan & disebut gudangnya ─────────────────
def test_pengganti_yang_ready_ditemukan(monkeypatch):
    _setup(monkeypatch,
           stok={HABIS: {}, BARU: {"01.Jakarta": 4}, LAMA: {}},
           sims_res=_SIMS)

    out = A._t_alternatif_ready({"part_number": HABIS}, ADMIN)

    assert out["found"] is True
    assert out["part_asli_siap_kirim"] == 0
    assert [a["pn"] for a in out["alternatif_siap_kirim"]] == [BARU]
    assert out["alternatif_siap_kirim"][0]["siap_kirim"] == 4
    assert out["alternatif_siap_kirim"][0]["gudang"] == [{"gudang": "01.Jakarta", "qty": 4}]
    assert [a["pn"] for a in out["alternatif_tanpa_stok"]] == [LAMA]


def test_part_lama_juga_dipakai_bila_masih_ada_stok(monkeypatch):
    """Arah 'menggantikan' (part lama) barang yang sama — membuangnya = membuang jualan."""
    _setup(monkeypatch, stok={HABIS: {}, BARU: {}, LAMA: {"04.Palembang": 2}}, sims_res=_SIMS)

    out = A._t_alternatif_ready({"part_number": HABIS}, ADMIN)

    assert [a["pn"] for a in out["alternatif_siap_kirim"]] == [LAMA]


def test_reservasi_dipotong_dari_stok_siap_kirim(monkeypatch):
    """Definisi ready HARUS sama dengan checkout: stok − reservasi aktif."""
    _setup(monkeypatch,
           stok={HABIS: {}, BARU: {"01.Jakarta": 3}, LAMA: {}},
           reservasi={(BARU, "01.Jakarta"): 3},         # semuanya sudah ditahan order lain
           sims_res=_SIMS)

    out = A._t_alternatif_ready({"part_number": HABIS}, ADMIN)

    assert out["alternatif_siap_kirim"] == []
    assert "TIDAK SATU PUN" in out["jawaban_wajib"]


def test_gudang_yang_tak_boleh_kirim_tidak_dihitung(monkeypatch):
    """Gudang dimatikan admin ('Bisa Kirim' off) tak boleh dijanjikan."""
    _setup(monkeypatch,
           stok={HABIS: {}, BARU: {"25.PT BJM": 9}, LAMA: {}},
           sims_res=_SIMS, shippable_off=("25.PT BJM",))

    out = A._t_alternatif_ready({"part_number": HABIS}, ADMIN)

    assert out["alternatif_siap_kirim"] == []


def test_saring_per_gudang(monkeypatch):
    _setup(monkeypatch,
           stok={HABIS: {}, BARU: {"01.Jakarta": 4, "04.Palembang": 1}, LAMA: {}},
           sims_res=_SIMS)

    out = A._t_alternatif_ready({"part_number": HABIS, "gudang": "palembang"}, ADMIN)

    assert out["alternatif_siap_kirim"][0]["gudang"] == [{"gudang": "04.Palembang", "qty": 1}]


def test_tanpa_data_pengganti_katakan_apa_adanya(monkeypatch):
    _setup(monkeypatch, stok={HABIS: {}}, sims_res={"found": False})

    out = A._t_alternatif_ready({"part_number": HABIS}, ADMIN)

    assert out["found"] is False
    assert "Tidak ada data persamaan/pengganti" in out["jawaban_wajib"]
    assert out["alternatif_siap_kirim"] == []


def test_pn_yang_ditanya_tak_pernah_jadi_alternatif_dirinya_sendiri(monkeypatch):
    _setup(monkeypatch, stok={HABIS: {"01.Jakarta": 2}},
           sims_res={"found": True, "digantikan_oleh": [{"pn": HABIS, "nama": "diri sendiri"}]})

    out = A._t_alternatif_ready({"part_number": HABIS}, ADMIN)

    assert out["alternatif_siap_kirim"] == []
    assert out["part_asli_siap_kirim"] == 2


def test_sims_error_tidak_menggagalkan_tool(monkeypatch):
    _setup(monkeypatch, stok={HABIS: {"01.Jakarta": 1}})
    monkeypatch.setattr(A.sims, "get_part_equivalents",
                        lambda pn: (_ for _ in ()).throw(RuntimeError("SIMS down")))

    out = A._t_alternatif_ready({"part_number": HABIS}, ADMIN)

    assert out["found"] is False            # tak ada kandidat, tapi tak meledak
    assert out["part_asli_siap_kirim"] == 1


# ── Gagal MENGECEK ≠ tahu bahwa TIDAK ADA (audit klaim 2026-08-04) ───────────
# Temuan risiko tertinggi: sesi Weichai belum aktif / SIMS down dijawab
# "Tidak ada pengganti … dan stok aslinya juga kosong" — dua vonis negatif dari
# nol pengecekan, dan admin menolak penjualan yang sebenarnya bisa jalan.

def test_sumber_gagal_bukan_vonis_tidak_ada(monkeypatch):
    _setup(monkeypatch, stok={HABIS: {}})
    monkeypatch.setattr(A.sims, "get_part_equivalents",
                        lambda pn: (_ for _ in ()).throw(RuntimeError("SIMS down")))

    out = A._t_alternatif_ready({"part_number": HABIS}, ADMIN)

    assert out["found"] is False
    assert out["_cek_tak_lengkap"] is True          # → _tool_fail_kind 'err', bukan 'nf'
    assert out["sumber_gagal"] == ["sims"]
    assert out["sumber_dicek"] == {"sims": "gagal", "weichai": "ok"}
    jw = out["jawaban_wajib"]
    assert "BELUM bisa memastikan" in jw and "BUKAN pernyataan" in jw
    assert "Tidak ada data persamaan" not in jw


def test_weichai_tanpa_sesi_ditandai_terpisah(monkeypatch):
    _setup(monkeypatch, stok={HABIS: {}})
    monkeypatch.setattr(A.epc_weichai, "replace_part",
                        lambda pn, rangka="": {"found": False, "reason": "no_session"})

    out = A._t_alternatif_ready({"part_number": HABIS}, ADMIN)

    assert out["sumber_dicek"]["weichai"] == "tanpa_sesi"
    assert out["_cek_tak_lengkap"] is True
    # sumber yang BERHASIL dicek tetap disebut — jawaban jujur, bukan gelap total
    assert "SIMS Sinotruk" in out["jawaban_wajib"]


def test_weichai_kosong_dianggap_tak_dicek(monkeypatch):
    """`{}` = tak terkonfigurasi/exception, BUKAN 'dicek & nihil'."""
    _setup(monkeypatch, stok={HABIS: {}})
    monkeypatch.setattr(A.epc_weichai, "replace_part", lambda pn, rangka="": {})

    out = A._t_alternatif_ready({"part_number": HABIS}, ADMIN)

    assert out["sumber_dicek"]["weichai"] == "gagal" and out["_cek_tak_lengkap"] is True


def test_semua_sumber_ok_baru_boleh_vonis(monkeypatch):
    """Dua sumber benar-benar dicek & nihil → vonis 'tidak ada' SAH."""
    _setup(monkeypatch, stok={HABIS: {}})

    out = A._t_alternatif_ready({"part_number": HABIS}, ADMIN)

    assert out["sumber_dicek"] == {"sims": "ok", "weichai": "ok"}
    assert "_cek_tak_lengkap" not in out
    assert "Tidak ada data persamaan/pengganti" in out["jawaban_wajib"]


def test_kandidat_ketemu_status_sumber_tetap_dilaporkan(monkeypatch):
    """Meski ada kandidat, status per-sumber tetap ikut: satu sumber gagal berarti
    daftar alternatifnya BELUM tentu lengkap."""
    _setup(monkeypatch, stok={HABIS: {}, BARU: {"01.Jakarta": 3}}, sims_res=_SIMS)
    monkeypatch.setattr(A.epc_weichai, "replace_part", lambda pn, rangka="": {})

    out = A._t_alternatif_ready({"part_number": HABIS}, ADMIN)

    assert out["found"] is True
    assert out["sumber_dicek"]["weichai"] == "gagal"


# ── Peran: ADMIN-ONLY ────────────────────────────────────────────────────────
def test_non_admin_ditolak(monkeypatch):
    _setup(monkeypatch, stok={HABIS: {}}, sims_res=_SIMS)

    for u in (PEMBELI, CABANG):
        assert A._t_alternatif_ready({"part_number": HABIS}, u).get("denied") is True
        assert A._run_tool("alternatif_ready", {"part_number": HABIS}, u).get("denied") is True


def test_hanya_admin_ditawari_toolnya():
    assert "alternatif_ready" in {s["function"]["name"] for s in A._tool_specs(ADMIN)}
    for u in (PEMBELI, CABANG):
        assert "alternatif_ready" not in {s["function"]["name"] for s in A._tool_specs(u)}
