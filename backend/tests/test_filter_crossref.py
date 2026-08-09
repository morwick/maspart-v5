"""Padanan AFTERMARKET filter (sumber PIHAK KETIGA) di pengganti_part.

Latar & batas yang HARUS dijaga (diukur saat riset 2026-08-08):
 • Cakupan sumbernya RENDAH (~8-17% PN filter kita) → `found: False` sering, dan
   itu BUKAN bukti "tak ada padanan aftermarket".
 • Sebagian PN membalas RATUSAN "padanan" lintas merek tak berhubungan (entri
   catch-all) → ditandai `generik` dan tak boleh direkomendasikan.
 • Sumbernya situs anonim tanpa lisensi data → ⛔ TIDAK boleh dicampur ke
   'digantikan_oleh' (supersession RESMI pabrik). Itu inti tes di bawah.

Catatan riset yang menyelamatkan dataset ini: domain fuelfilter-crossreference
dirender JS dan membalas HTTP 200 untuk merek+PN KARANGAN, dengan "padanan"
berupa template JS. Pengukuran pertama sempat melaporkan cakupan 100% karenanya.
"""
import pytest

from app.services import filter_crossref as fx
from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}

DATA = {
    "sumber": "situs-pihak-ketiga",
    "pn_dicek": 30,
    "data": {
        "1000424655": {"nama": "Filter Element", "jumlah": 3, "padanan": [
            {"merek": "HIFI FILTER", "pn": "SO15007"},
            {"merek": "FLEETGUARD", "pn": "LF16327"},
            {"merek": "DONALDSON", "pn": "R010014"}]},
        "080V05504-6096/1": {"nama": "Filter Element", "jumlah": 119, "generik": True,
                             "padanan": [{"merek": f"MERK{i}", "pn": f"P{i}"}
                                         for i in range(119)]},
    },
}


@pytest.fixture(autouse=True)
def _pakai_data(monkeypatch):
    d = dict(DATA)
    d["_idx"] = {fx._norm(k): {**v, "pn_oem": k} for k, v in DATA["data"].items()}
    monkeypatch.setattr(fx, "_load", lambda: d)


# ── lapisan service ─────────────────────────────────────────────────────────
def test_cari_pn_dikenal():
    r = fx.cari("1000424655")
    assert r["found"] is True
    assert [x["merek"] for x in r["padanan"]] == ["HIFI FILTER", "FLEETGUARD", "DONALDSON"]
    assert "PIHAK KETIGA" in r["peringatan"]


def test_pn_pemaaf_beda_penulisan():
    assert fx.cari("1000-424 655")["found"] is True


def test_tak_ada_di_sumber_bukan_bukti_tak_ada_padanan():
    r = fx.cari("PN-ASING-999")
    assert r["found"] is False
    assert r["alasan"] == "tak_ada_di_sumber"
    assert "BUKAN berarti tak ada" in r["pesan"]


def test_entri_generik_ditandai_sinyal_lemah():
    r = fx.cari("080V05504-6096/1")
    assert r["generik"] is True
    assert "SINYAL LEMAH" in r["peringatan"]
    assert "JANGAN direkomendasikan" in r["peringatan"]


def test_padanan_dipotong_dan_dilaporkan():
    r = fx.cari("080V05504-6096/1")
    assert len(r["padanan"]) == fx._MAKS_PADANAN
    assert r["terpotong"] == 119 - fx._MAKS_PADANAN
    assert r["jumlah"] == 119            # jumlah ASLI tetap jujur


def test_tanpa_data_bukan_vonis(monkeypatch):
    monkeypatch.setattr(fx, "_load", lambda: {})
    r = fx.cari("1000424655")
    assert r["found"] is False and r["alasan"] == "no_data"


def test_input_kosong():
    assert fx.cari("")["alasan"] == "input"


# ── integrasi pengganti_part: TERPISAH dari supersession resmi ──────────────
def _mock_pengganti(monkeypatch, resmi=True):
    monkeypatch.setattr(ai.sims, "status_jual", lambda pn: None)
    monkeypatch.setattr(ai.sims, "equivalents_count", lambda: 1)
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: (
        {"found": True, "digantikan_oleh": [{"pn": "NEW-RESMI", "nama": "Baru"}],
         "menggantikan": []} if resmi else {}))
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {"found": False})
    monkeypatch.setattr(ai.weichai_replace, "available", lambda: False)
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(ai.filter_crossref, "available", lambda: True)


def test_aftermarket_tidak_dicampur_ke_digantikan_oleh(monkeypatch):
    """Inti pagar: saran pihak ketiga TIDAK boleh tampak setara keputusan pabrik."""
    _mock_pengganti(monkeypatch)
    r = ai._t_pengganti_part({"part_number": "1000424655"}, ADMIN)
    resmi = [x["part_number"] for x in r["digantikan_oleh"]]
    assert resmi == ["NEW-RESMI"]
    assert "SO15007" not in str(resmi)
    am = r["padanan_aftermarket"]
    assert [x["merek"] for x in am["padanan"]][0] == "HIFI FILTER"


def test_aftermarket_muncul_juga_saat_supersession_resmi_nihil(monkeypatch):
    _mock_pengganti(monkeypatch, resmi=False)
    r = ai._t_pengganti_part({"part_number": "1000424655"}, ADMIN)
    assert r.get("padanan_aftermarket")
    assert r["found"] is False          # tetap jujur: supersession resmi nihil


def test_catatan_melarang_menyamakan_dengan_resmi(monkeypatch):
    _mock_pengganti(monkeypatch)
    r = ai._t_pengganti_part({"part_number": "1000424655"}, ADMIN)
    c = r["catatan"]
    assert "BUKAN supersession resmi" in c
    assert "JANGAN dicampur" in c
    assert "cakupan sumbernya rendah" in c.lower()


def test_pn_tanpa_padanan_tak_memunculkan_kunci(monkeypatch):
    _mock_pengganti(monkeypatch)
    r = ai._t_pengganti_part({"part_number": "PN-LAIN-123"}, ADMIN)
    assert "padanan_aftermarket" not in r


def test_sumber_mati_tak_menjatuhkan_tool(monkeypatch):
    _mock_pengganti(monkeypatch)

    def boom(pn):
        raise RuntimeError("gz rusak")
    monkeypatch.setattr(ai.filter_crossref, "cari", boom)
    r = ai._t_pengganti_part({"part_number": "1000424655"}, ADMIN)
    assert r["found"] is True and "padanan_aftermarket" not in r


# ── parser builder: JEBAKAN template JS ─────────────────────────────────────
def test_parser_membuang_template_js():
    """Regresi: tautan /convert/ di dalam <script> adalah template JS
    (' + results[i].brand + '), bukan padanan. Sempat membuat pengukuran
    cakupan melaporkan 100% padahal nihil."""
    import importlib.util
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "tools" / "build_filter_crossref.py"
    spec = importlib.util.spec_from_file_location("build_filter_crossref", src)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    html = ("""<a href="/convert/HIFI-FILTER/SO15007">HIFI</a>"""
            """<script>var x = '<a href="/convert/' + results[i].brand + '/'"""
            """ + results[i].model + '">';</script>""")

    class R:
        status_code = 200
        text = html
    m.requests = type("Q", (), {"get": staticmethod(lambda *a, **k: R())})
    pasang, status = m.ambil("WEICHAI-POWER", "1000424655")
    assert status == "ok"
    assert pasang == [{"merek": "HIFI FILTER", "pn": "SO15007"}]


def test_parser_404_berarti_tak_ada():
    import importlib.util
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "tools" / "build_filter_crossref.py"
    spec = importlib.util.spec_from_file_location("build_filter_crossref2", src)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    class R:
        status_code = 404
        text = ""
    m.requests = type("Q", (), {"get": staticmethod(lambda *a, **k: R())})
    pasang, status = m.ambil("SINOTRUK", "APAPUN")
    assert pasang is None and status == "404"
