"""cari_part BROADEN dalam-unit — saat scope ke SATU unit, pencarian dibuat
forgiving: kata inti dicari sendiri-sendiri sehingga part yang di katalog bernama
RINGKAS (mis. 'HANDLE' untuk query 'handle pintu' / 'door handle') tetap ketemu.
Sekaligus memastikan search GLOBAL (tanpa unit) tetap presisi per-frasa (tak bocor).
part_index & _expand_query di-mock → tanpa data/network.
"""
import pytest

from app.services import ai_assistant as ai
from app.services import part_index

U = {"username": "mas", "role": "admin"}

HANDLE = {"part_number": "146-56-15200", "part_name": "HANDLE",
          "file": "SD16E _ CH", "path": "Shantui/SD16E _ CH"}
DOOR = {"part_number": "16Y-56E-02000", "part_name": "DOOR(L.H.)",
        "file": "SD16E _ CH", "path": "Shantui/SD16E _ CH"}
OTHER = {"part_number": "AZ1771210012", "part_name": "Right door assembly",
         "file": "NX360 6X4", "path": "Sinotruk/NX360 6X4"}


@pytest.fixture
def mock_index(monkeypatch):
    def sp_name(t):
        tl = (t or "").strip().lower()
        if tl == "handle":
            return [HANDLE]
        if tl == "door":
            return [DOOR, OTHER]   # OTHER = unit lain (harus tersaring saat scope SD16)
        return []                  # frasa penuh 'handle pintu'/'door handle' → 0
    monkeypatch.setattr(part_index, "search_part_name", sp_name)
    monkeypatch.setattr(part_index, "search_part_number", lambda t: [])
    monkeypatch.setattr(part_index, "correct_typos", lambda t: (t, []))
    monkeypatch.setattr(part_index, "suggest_names", lambda q, limit=6: [])
    # konteks grup: HANDLE segrup LOCK(L.H.) (handle pintu); sisanya tak ada cluster
    def fake_ctx(pn, file_simple=""):
        if pn == "146-56-15200":
            return {"induk": "LOCK(L.H.)", "anggota": ["LOCK CATCH", "LOCK BODY", "SET"]}
        return {}
    monkeypatch.setattr(part_index, "assembly_context", fake_ctx)
    # terms multi-kata (frasa) — TAK ada kata 'handle' tunggal → uji broaden
    monkeypatch.setattr(ai, "_expand_query", lambda q: (["handle pintu", "door handle"], ["pintu"]))


def _pns(res):
    return {it["part_number"] for it in (res.get("hasil") or [])}


def test_broaden_menemukan_part_bernama_ringkas(mock_index):
    res = ai._t_cari_part({"query": "handle pintu", "unit": "SD16"}, U)
    pns = _pns(res)
    assert "146-56-15200" in pns          # HANDLE ketemu via kata inti 'handle'
    assert "16Y-56E-02000" in pns          # DOOR ikut (kata 'door')
    assert "AZ1771210012" not in pns       # part unit LAIN tak bocor


def test_broaden_dihitung_relevan_kuat(mock_index):
    res = ai._t_cari_part({"query": "door handle", "unit": "SD16"}, U)
    assert "146-56-15200" in _pns(res)
    assert res["jumlah_relevan_kuat"] >= 1   # kata inti masuk skor relevansi


def test_nama_model_di_query_tetap_ketemu(mock_index):
    # model kadang menaruh nama unit di query juga — broaden abaikan token unit
    res = ai._t_cari_part({"query": "handle pintu SD16", "unit": "SD16"}, U)
    assert "146-56-15200" in _pns(res)


def test_global_tanpa_unit_tetap_presisi(mock_index):
    # tanpa unit: frasa penuh 0 → TIDAK broaden (presisi global terjaga, tak bocor)
    res = ai._t_cari_part({"query": "handle pintu"}, U)
    assert "146-56-15200" not in _pns(res)
    assert not (res.get("hasil") or [])


def test_grup_konteks_disambiguasi_handle_pintu(mock_index):
    # KONTEKS GRUP: HANDLE segrup LOCK(L.H.) → grup_induk + grup_isi (tetangga) →
    # asisten bisa menalar handle PINTU vs handle tuas.
    res = ai._t_cari_part({"query": "handle pintu", "unit": "SD16"}, U)
    byd = {it["part_number"]: it for it in res["hasil"]}
    assert byd["146-56-15200"].get("grup_induk") == "LOCK(L.H.)"
    assert "LOCK CATCH" in byd["146-56-15200"].get("grup_isi", [])
    assert "grup_induk" not in byd.get("16Y-56E-02000", {})  # DOOR tak punya cluster


def test_group_key_penomoran_shantui():
    gk = part_index._group_key
    assert gk("146-56-15200") == "146-56-15"    # cluster kunci pintu
    assert gk("16Y-56E-26400") == "16Y-56E-26"   # cluster lock assembly
    assert gk("ABC") is None                     # tak ada run angka
    assert gk("AZ12") is None                    # run < 4 digit

    ab = part_index._assembly_base
    assert ab("146-56-15200") == "146-56-15000"
    assert ab("16Y-56E-02000") is None           # sudah level assembly (…000)
