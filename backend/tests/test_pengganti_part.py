"""Supersession/pengganti part: SIMS partEquivalentQuery (Sinotruk sasis) +
EPC Weichai (mesin) digabung di tool pengganti_part. Jaringan di-mock.
"""
import pytest

from app.services import epc_weichai, sims, ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}


@pytest.fixture(autouse=True)
def _sims_status_jual_offline(monkeypatch):
    """pengganti_part kini menyertakan status jual RESMI SIMS (`isSale`) — satu
    HTTP LIVE per PN saat cache dingin (login RSA + partInfo). ⛔ Test tidak
    boleh menyentuh jaringan, jadi seluruh modul ini dijalankan dengan helper
    itu di-nol-kan; test yang memang mengujinya me-monkeypatch ulang sendiri."""
    monkeypatch.setattr(ai.sims, "status_jual", lambda pn: None)


# ── sims.get_part_equivalents: klasifikasi ARAH (lama→pengganti) ─────────────
def test_sims_equivalents_arah(monkeypatch):
    recs = [
        # OLD1 sbg SEBELUM → penggantinya NEW1
        {"preSpGoodsNo": "OLD1+001/1", "preGoodsName": "Old One",
         "afterSpGoodsNo": "NEW1+121/1", "afterGoodsName": "New One"},
        # OLD1 sbg SESUDAH → ia menggantikan OLDX
        {"preSpGoodsNo": "OLDX", "preGoodsName": "X",
         "afterSpGoodsNo": "OLD1+001/1", "afterGoodsName": "Old One"},
    ]

    class FakeSF:
        @staticmethod
        def fetch_part_equivalents(pn, page_size=50):
            return recs

    monkeypatch.setattr(sims, "_SIMS_OK", True)
    monkeypatch.setattr(sims, "_sf", FakeSF)
    r = sims.get_part_equivalents("OLD1+001/1")
    assert r["found"]
    assert [x["pn"] for x in r["digantikan_oleh"]] == ["NEW1+121/1"]
    assert [x["pn"] for x in r["menggantikan"]] == ["OLDX"]


def test_sims_equivalents_kosong(monkeypatch):
    monkeypatch.setattr(sims, "_SIMS_OK", True)
    monkeypatch.setattr(sims, "_sf", type("F", (), {"fetch_part_equivalents": staticmethod(lambda pn, page_size=50: [])}))
    assert sims.get_part_equivalents("ZZ")["found"] is False


# ── tool _t_pengganti_part: gabung SIMS + Weichai, silang stok lokal ─────────
def test_pengganti_gabung_dua_sumber(monkeypatch):
    monkeypatch.setattr(ai.sims, "get_part_equivalents", lambda pn: {
        "found": True, "digantikan_oleh": [{"pn": "NEW1", "nama": "New One"}], "menggantikan": []})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {
        "found": True, "digantikan_oleh": [{"pn": "NEWENG", "tanggal": "2024", "tipe": "searah"}],
        "menggantikan": []})
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [
        {"part_number": "NEW1", "part_name": "New One Local", "stok": "5", "harga": "Rp 10"}])
    r = ai._t_pengganti_part({"part_number": "OLD1"}, ADMIN)
    assert r["found"]
    pns = [x["part_number"] for x in r["digantikan_oleh"]]
    assert "NEW1" in pns and "NEWENG" in pns          # kedua sumber tergabung
    new1 = next(x for x in r["digantikan_oleh"] if x["part_number"] == "NEW1")
    assert new1["sumber"] == "SIMS" and new1["stok_total"] == "5"   # silang stok lokal
    eng = next(x for x in r["digantikan_oleh"] if x["part_number"] == "NEWENG")
    assert eng["sumber"] == "Weichai" and eng.get("catatan")        # tak ada di katalog lokal
    assert set(r["sumber"]) == {"SIMS", "Weichai"}


def test_pengganti_dedup(monkeypatch):
    # PN pengganti sama muncul di SIMS & Weichai → hanya sekali.
    monkeypatch.setattr(ai.sims, "get_part_equivalents", lambda pn: {
        "found": True, "digantikan_oleh": [{"pn": "SAME", "nama": "S"}], "menggantikan": []})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {
        "found": True, "digantikan_oleh": [{"pn": "SAME"}], "menggantikan": []})
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])
    r = ai._t_pengganti_part({"part_number": "OLD"}, ADMIN)
    assert [x["part_number"] for x in r["digantikan_oleh"]] == ["SAME"]


def test_pengganti_kosong(monkeypatch):
    """Dua sumber resmi nihil. Kalimatnya kini menyebut SUPERSESSION RESMI, bukan
    'persamaan' — sengaja: 'tak ada supersession' adalah fakta yang bisa kita
    buktikan, sedangkan 'tak ada persamaan' adalah klaim yang lebih luas dan
    TERBUKTI sering salah (varian pemasok /1 /2 ada di katalog kita sendiri)."""
    monkeypatch.setattr(ai.sims, "get_part_equivalents", lambda pn: {"found": False})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {"found": False})
    r = ai._t_pengganti_part({"part_number": "XX"}, ADMIN)
    assert not r["found"]
    assert "supersession resmi" in r["error"].lower()
    assert "tidak ada data persamaan" not in r["error"].lower()


def test_pengganti_butuh_pn():
    assert "error" in ai._t_pengganti_part({}, ADMIN)


# ── INDEKS persamaan penuh (paginasi) + lookup instan ───────────────────────
def test_equivalents_index(monkeypatch):
    page1 = [
        {"preSpGoodsNo": "OLD1+001/1", "preGoodsName": "Old", "afterSpGoodsNo": "NEW1", "afterGoodsName": "New"},
        {"preSpGoodsNo": "OLD2", "preGoodsName": "O2", "afterSpGoodsNo": "NEW2", "afterGoodsName": "N2"},
    ]

    def fake_page(page, page_size=500):
        return (page1, 2) if page == 1 else ([], 2)

    monkeypatch.setattr(sims, "_SIMS_OK", True)
    monkeypatch.setattr(sims, "_sf", type("F", (), {"fetch_equivalents_page": staticmethod(fake_page)}))
    monkeypatch.setattr(sims, "_equiv_index", {"ts": 0.0, "by_pn": {}})
    n = sims.refresh_equivalents(force=True)
    assert n > 0
    # exact + base ('OLD1' tanpa suffix) sama-sama cocok
    assert [x["pn"] for x in sims.equivalents_for("OLD1+001/1")["digantikan_oleh"]] == ["NEW1"]
    assert [x["pn"] for x in sims.equivalents_for("OLD1")["digantikan_oleh"]] == ["NEW1"]
    # arah balik
    assert [x["pn"] for x in sims.equivalents_for("NEW1")["menggantikan"]] == ["OLD1+001/1"]
    assert sims.equivalents_for("TAKADA") == {}


# ── fix produksi 2026-07-17: tool pakai INDEKS hangat, bukan query live ─────
def test_pengganti_pakai_indeks_bukan_live(monkeypatch):
    # indeks siap → query live TIDAK boleh dipanggil (rentan timeout = gagal 45%)
    monkeypatch.setattr(ai.sims, "equivalents_count", lambda: 17000)
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {
        "digantikan_oleh": [{"pn": "NEWIDX", "nama": "Baru"}], "menggantikan": []})

    def _boom(pn):
        raise AssertionError("query live tidak boleh dipanggil saat indeks siap")

    monkeypatch.setattr(ai.sims, "get_part_equivalents", _boom)
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {"found": False})
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    r = ai._t_pengganti_part({"part_number": "OLD1"}, ADMIN)
    assert r["found"]
    assert [x["part_number"] for x in r["digantikan_oleh"]] == ["NEWIDX"]


def test_pengganti_kosong_tetap_beri_info_pn(monkeypatch):
    # tak ada supersession TAPI PN dikenal katalog → sertakan info (bukan buntu).
    monkeypatch.setattr(ai.sims, "equivalents_count", lambda: 17000)
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {"found": False})
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {
        "WG9100443050": {"part_name": "Brake Disc", "stok": "3", "harga": "Rp 1.000"}})
    r = ai._t_pengganti_part({"part_number": "WG9100443050"}, ADMIN)
    assert r["found"] is False
    assert r["info_part_ditanya"]["stok_total"] == "3"
    assert "aktif" in r["info_part_ditanya"]["catatan"]


# ── cari_part menyisipkan 'pengganti' dari indeks ───────────────────────────
def test_cari_part_sisip_pengganti(monkeypatch):
    ROW = {"part_number": "OLDPN", "part_name": "Brake Pedal", "file": "NX360", "path": "Sinotruk/NX360"}
    monkeypatch.setattr(ai.part_index, "search_part_name", lambda t: [ROW])
    monkeypatch.setattr(ai.part_index, "search_part_number", lambda t: [])
    monkeypatch.setattr(ai.part_index, "correct_typos", lambda t: (t, []))
    monkeypatch.setattr(ai.part_index, "suggest_names", lambda q, limit=6: [])
    monkeypatch.setattr(ai.part_index, "assembly_context", lambda pn, f="": {})
    monkeypatch.setattr(ai, "_expand_query", lambda q: (["brake pedal"], []))
    monkeypatch.setattr(ai.sims, "equivalents_for",
                        lambda pn: {"digantikan_oleh": [{"pn": "NEWPN", "nama": "New Pedal"}],
                                    "menggantikan": []} if pn == "OLDPN" else {})
    res = ai._t_cari_part({"query": "brake pedal"}, ADMIN)
    it = next(x for x in res["hasil"] if x["part_number"] == "OLDPN")
    assert it.get("pengganti") == [{"pn": "NEWPN", "nama": "New Pedal"}]
    assert "info_pengganti" in res


# ═══════════════════════════════════════════════════════════════════════
#  "sudah dicek, memang tak ada"  vs  "GAGAL mengecek"
# ═══════════════════════════════════════════════════════════════════════
# Dulu keduanya menghasilkan kalimat yang PERSIS SAMA — "(dicek SIMS Sinotruk &
# EPC Weichai)" — diklaim tanpa syarat walau sumbernya gagal diperiksa. Untuk
# pertanyaan supersession itu berbahaya: mekanik dijawab yakin "tidak ada
# pengganti", lalu memesan part yang sebenarnya sudah disupersede.

@pytest.fixture
def _no_katalog(monkeypatch):
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})


def _hitung_miss(monkeypatch):
    """Ganti search_log.record_miss dgn penghitung — 'Pencarian Nihil' & loop
    belajar sinonim hanya boleh belajar dari BUKTI."""
    n = {"c": 0}
    monkeypatch.setattr(ai.search_log, "record_miss",
                        lambda q, mode="", source="": n.__setitem__("c", n["c"] + 1))
    return n


def test_sims_gagal_tidak_mengklaim_sudah_dicek(monkeypatch, _no_katalog):
    # Indeks dingin → jalur live; `{}` = tak terkonfigurasi/exception = TIDAK dicek.
    monkeypatch.setattr(ai.sims, "equivalents_count", lambda: 0)
    monkeypatch.setattr(ai.sims, "get_part_equivalents", lambda pn: {})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {"found": False})
    n = _hitung_miss(monkeypatch)

    r = ai._t_pengganti_part({"part_number": "OLD1"}, ADMIN)

    assert r["found"] is False
    assert r["sumber_dicek"]["sims"] == "gagal"
    assert r["sumber_dicek"]["weichai"] == "ok"
    assert r["sumber_gagal"] == ["sims"]
    assert "sudah dicek SIMS" not in r["error"]          # klaim palsu itu HILANG
    assert "BUKAN pernyataan" in r["error"]
    # Telemetri: 'err' (gagal cek), BUKAN 'nf' (data memang tak ada).
    assert ai._tool_fail_kind(r) == "err"
    assert n["c"] == 0                                   # tak mencemari Pencarian Nihil


def test_weichai_tanpa_sesi_dianggap_belum_dicek(monkeypatch, _no_katalog):
    monkeypatch.setattr(ai.sims, "equivalents_count", lambda: 17000)
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {
        "found": False, "reason": "no_session", "message": "Sesi EPC Weichai belum aktif."})
    n = _hitung_miss(monkeypatch)

    r = ai._t_pengganti_part({"part_number": "1007167053"}, ADMIN)

    assert r["sumber_dicek"] == {"sims": "ok", "weichai": "tanpa_sesi"}
    assert ai._tool_fail_kind(r) == "err"
    assert n["c"] == 0


def test_weichai_exception_dianggap_gagal(monkeypatch, _no_katalog):
    monkeypatch.setattr(ai.sims, "equivalents_count", lambda: 17000)
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {})

    def _boom(pn, rangka):
        raise RuntimeError("jaringan putus")

    monkeypatch.setattr(ai.epc_weichai, "replace_part", _boom)
    r = ai._t_pengganti_part({"part_number": "X"}, ADMIN)
    assert r["sumber_dicek"]["weichai"] == "gagal"
    assert ai._tool_fail_kind(r) == "err"


def test_dua_sumber_sehat_dan_nihil_tetap_nf(monkeypatch, _no_katalog):
    """Kasus SAH: keduanya benar-benar dicek & memang tak ada → 'nf', dan
    klaim 'sudah dicek' kini JUJUR."""
    monkeypatch.setattr(ai.sims, "equivalents_count", lambda: 17000)
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {
        "found": False, "part_number": "X",
        "message": "Tidak ada data pengganti untuk PN 'X' di EPC Weichai"})
    n = _hitung_miss(monkeypatch)

    r = ai._t_pengganti_part({"part_number": "X"}, ADMIN)

    assert r["sumber_dicek"] == {"sims": "ok", "weichai": "ok"}
    assert "_cek_tak_lengkap" not in r
    assert "sudah dicek SIMS Sinotruk & EPC Weichai" in r["error"]
    assert ai._tool_fail_kind(r) == "nf"
    assert n["c"] == 1                                   # bukti sah → boleh dicatat


def test_ketemu_tapi_satu_sumber_gagal_diberi_catatan_cakupan(monkeypatch, _no_katalog):
    """SIMS menemukan pengganti, Weichai gagal → hasil berguna TAPI daftarnya
    belum tentu lengkap. Katakan, jangan diam-diam."""
    monkeypatch.setattr(ai.sims, "equivalents_count", lambda: 17000)
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {
        "digantikan_oleh": [{"pn": "NEW1", "nama": "Baru"}], "menggantikan": []})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {
        "found": False, "reason": "gagal", "message": "Gagal menghubungi EPC Weichai"})

    r = ai._t_pengganti_part({"part_number": "OLD1"}, ADMIN)

    assert r["found"] is True
    assert r["sumber_dicek"]["weichai"] == "gagal"
    assert "BELUM LENGKAP" in r["catatan_cakupan"]
    assert ai._tool_fail_kind(r) == ""                   # ketemu = bukan kegagalan


# ── epc_weichai.replace_part: error jaringan ≠ "tidak ada data" ──────────────
def test_weichai_error_halaman_pertama_bukan_tidak_ada_data(monkeypatch):
    monkeypatch.setattr(epc_weichai, "_ensure_token", lambda rangka="": "TOKEN")
    monkeypatch.setattr(epc_weichai, "_get",
                        lambda url, params, token: {"_err": "network"})

    r = epc_weichai.replace_part("1007167053")

    assert r["found"] is False
    assert r["reason"] == "gagal"
    assert "Tidak ada data pengganti" not in r["message"]
    assert "BUKAN pernyataan" in r["message"]


def test_weichai_kosong_asli_tetap_bilang_tidak_ada(monkeypatch):
    monkeypatch.setattr(epc_weichai, "_ensure_token", lambda rangka="": "TOKEN")
    monkeypatch.setattr(epc_weichai, "_get",
                        lambda url, params, token: {"data": {"list": [], "total": 0}})

    r = epc_weichai.replace_part("WG9100443050")

    assert r["found"] is False
    assert "reason" not in r
    assert "Tidak ada data pengganti" in r["message"]


# ── KELUARGA KATALOG: 'tak ada supersession' ≠ 'tak ada persamaan' ───────────
# Regresi kasus produksi 2026-08-29: WG9725191800 dijawab "tidak ada persamaan"
# padahal katalog memuat varian pemasok /1 dan /2.

def _keluarga_stub(monkeypatch, **isi):
    dasar = {"tersedia": True, "part_number": "OLD1", "ada_di_katalog": True,
             "nama": "Air filter assembly", "varian_pemasok": [], "sub_rakitan": [],
             "rakitan_induk": [], "prefix_setara": [], "prefix_perlu_dicek": [],
             "prefix_ditolak": [], "jumlah": 0, "catatan": "BUKAN supersession resmi"}
    dasar.update(isi)
    dasar["jumlah"] = sum(len(dasar[k]) for k in
                          ("varian_pemasok", "sub_rakitan", "rakitan_induk",
                           "prefix_setara", "prefix_perlu_dicek"))
    monkeypatch.setattr(ai.pn_keluarga, "keluarga", lambda pn: dasar)


def _dua_sumber_nihil(monkeypatch):
    monkeypatch.setattr(ai.sims, "get_part_equivalents", lambda pn: {"found": False})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {"found": False})


def test_supersession_nihil_tapi_ada_varian_pemasok_dilarang_bilang_tak_ada(
        monkeypatch, _no_katalog):
    _dua_sumber_nihil(monkeypatch)
    _keluarga_stub(monkeypatch, varian_pemasok=[
        {"part_number": "OLD1/1", "nama": "Air filter assembly", "nama_cocok": True},
        {"part_number": "OLD1/2", "nama": "Air filter assembly (Hebei Yili)",
         "nama_cocok": True}])

    r = ai._t_pengganti_part({"part_number": "OLD1"}, ADMIN)

    assert r["found"] is False                     # supersession memang nihil
    kel = r["keluarga_katalog"]
    assert [x["part_number"] for x in kel["varian_pemasok"]] == ["OLD1/1", "OLD1/2"]
    # Pesan error WAJIB mencegah kesimpulan "tidak ada persamaan".
    assert "JANGAN menjawab 'tidak ada persamaan'" in r["error"]
    # …dan giliran ini BUKAN kegagalan: ada jawaban berguna. 'nf' akan mengotori
    # audit sekaligus memicu nota "lookup gagal" ke model.
    assert ai._tool_fail_kind(r) == ""


def test_keluarga_kosong_tidak_menambah_field_kosong(monkeypatch, _no_katalog):
    _dua_sumber_nihil(monkeypatch)
    _keluarga_stub(monkeypatch)
    r = ai._t_pengganti_part({"part_number": "OLD1"}, ADMIN)
    assert "keluarga_katalog" not in r
    assert "JANGAN menjawab" not in r["error"]
    assert ai._tool_fail_kind(r) == "nf"      # nihil sungguhan tetap 'nf'


def test_sub_rakitan_saja_tidak_memicu_klaim_ada_persamaan(monkeypatch, _no_katalog):
    """+001/+002 = lembar pegas berbeda. Mereka BUKAN persamaan, jadi tak boleh
    memicu peringatan 'ada keluarga' yang mendorong model menyebut pengganti."""
    _dua_sumber_nihil(monkeypatch)
    _keluarga_stub(monkeypatch, sub_rakitan=[
        {"part_number": "OLD1+002/1", "nama": "Leaf spring assembly", "bagian": "002"}])
    r = ai._t_pengganti_part({"part_number": "OLD1"}, ADMIN)
    assert "keluarga_katalog" in r                  # tetap dilaporkan (informatif)
    assert "JANGAN menjawab 'tidak ada persamaan'" not in r["error"]


def test_keluarga_gagal_dibaca_dinyatakan_bukan_disembunyikan(monkeypatch, _no_katalog):
    """Indeks katalog dingin = BELUM dicek. Diam di sini akan terbaca model
    sebagai 'tidak ada keluarga' — persis pola gagal-cek≠tidak-ada."""
    _dua_sumber_nihil(monkeypatch)
    monkeypatch.setattr(ai.pn_keluarga, "keluarga",
                        lambda pn: {"tersedia": False, "alasan": "indeks dingin"})
    r = ai._t_pengganti_part({"part_number": "OLD1"}, ADMIN)
    assert r["keluarga_katalog"]["dicek"] is False


def test_keluarga_meledak_tidak_menjatuhkan_tool(monkeypatch, _no_katalog):
    _dua_sumber_nihil(monkeypatch)
    def _boom(pn):
        raise RuntimeError("indeks rusak")
    monkeypatch.setattr(ai.pn_keluarga, "keluarga", _boom)
    r = ai._t_pengganti_part({"part_number": "OLD1"}, ADMIN)
    assert r["found"] is False and "keluarga_katalog" not in r


def test_keluarga_ikut_saat_supersession_resmi_ADA(monkeypatch):
    """PN bisa punya pengganti resmi DAN varian pemasok sekaligus; keduanya
    berguna dan tak boleh saling meniadakan."""
    monkeypatch.setattr(ai.sims, "get_part_equivalents", lambda pn: {
        "found": True, "digantikan_oleh": [{"pn": "NEW1", "nama": "New One"}],
        "menggantikan": []})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {"found": False})
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    _keluarga_stub(monkeypatch, varian_pemasok=[
        {"part_number": "OLD1/1", "nama": "Air filter assembly", "nama_cocok": True}])
    r = ai._t_pengganti_part({"part_number": "OLD1"}, ADMIN)
    assert r["found"] is True
    assert [x["part_number"] for x in r["digantikan_oleh"]] == ["NEW1"]
    assert r["keluarga_katalog"]["varian_pemasok"][0]["part_number"] == "OLD1/1"
    # Tiga tingkat kepastian tak boleh melebur jadi satu daftar.
    assert "OLD1/1" not in [x["part_number"] for x in r["digantikan_oleh"]]
