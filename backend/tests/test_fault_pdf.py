"""Lembar diagnosa PDF per-SPN/FMI (data/Fault) → kartu di chat.

Fokus: (1) index nama file (pola `SPN n FMI m.pdf` + varian ber-suffix),
(2) find eksak / per-SPN, (3) validasi nama anti-traversal, (4) handler
cari_kode_kesalahan melampirkan `pdf_diagnosa` (pasangan persis, fallback FMI
cap 4, pasangan di-luar-DB-teks tetap dapat kartu, tanpa-PDF → kosong),
(5) `_capture_meta` menyalurkan kartu ke kanal `excel_exports`.

Fixture pakai direktori sintetis (tmp_path) supaya deterministik.
"""
import pytest

from app.services import ai_assistant as ai
from app.services import fault_pdf

USER = {"username": "budi", "role": "user"}

_PDF = b"%PDF-1.4 uji"


@pytest.fixture()
def dunia(tmp_path, monkeypatch):
    d = tmp_path / "Fault"
    d.mkdir()
    for nama in ["SPN 3216 FMI 5.pdf", "SPN 2898 FMI 5.pdf", "SPN 2898 FMI 6.pdf",
                 "SPN 2898 FMI 7.pdf", "SPN 2898 FMI 15.pdf", "SPN 2898 FMI 16.pdf",
                 "SPN 1108 FMI 15.pdf", "SPN 1387 FMI 0 (E5_020E).pdf",
                 "SPN 1387 FMI 0 (E5_0211).pdf", "bukan-pola.pdf"]:
        (d / nama).write_bytes(_PDF)

    class _S:
        data_path = tmp_path

    monkeypatch.setattr(fault_pdf, "get_settings", lambda: _S())
    monkeypatch.setattr(fault_pdf, "_CACHE", {"mtime": None, "rows": []})
    return tmp_path


def test_index_dan_varian_suffix(dunia):
    assert fault_pdf.available() and fault_pdf.count() == 9  # bukan-pola diabaikan
    assert len(fault_pdf.find(1387, 0)) == 2                 # dua varian E5_*


def test_find_eksak_dan_per_spn(dunia):
    assert [r["file"] for r in fault_pdf.find(3216, 5)] == ["SPN 3216 FMI 5.pdf"]
    assert [r["fmi"] for r in fault_pdf.find(2898)] == [5, 6, 7, 15, 16]
    assert fault_pdf.find(999999) == [] and fault_pdf.find(None) == []


def test_pdf_bytes_validasi(dunia):
    assert fault_pdf.pdf_bytes("SPN 3216 FMI 5.pdf") == _PDF
    assert fault_pdf.pdf_bytes("../secrets.toml") is None
    assert fault_pdf.pdf_bytes("bukan-pola.pdf") is None  # tak terdaftar di index


def test_handler_kartu_pasangan_persis(dunia):
    r = ai._t_cari_kode_kesalahan({"spn": 3216, "fmi": 5}, USER)
    assert len(r["pdf_diagnosa"]) == 1
    c = r["pdf_diagnosa"][0]
    assert c["filename"] == "SPN 3216 FMI 5.pdf" and c["export_id"]
    assert "LEMBAR DIAGNOSA PDF" in r["catatan"]
    from app.services import ai_export
    data, fname = ai_export.generic_excel(c["export_id"])
    assert data == _PDF and fname == c["filename"]


def test_handler_fallback_fmi_cap4(dunia):
    # FMI 99 tak ada → PDF FMI lain utk SPN sama, maksimal 4 kartu.
    r = ai._t_cari_kode_kesalahan({"spn": 2898, "fmi": 99}, USER)
    assert len(r["pdf_diagnosa"]) == 4
    assert [c["fmi"] for c in r["pdf_diagnosa"]] == [5, 6, 7, 15]


def test_handler_di_luar_db_teks_tetap_dapat_kartu(dunia):
    # 1108/15 tak ada di tabel Bosch/EOL → kartu PDF tetap terlampir + catatan.
    r = ai._t_cari_kode_kesalahan({"spn": 1108, "fmi": 15}, USER)
    assert r["jumlah_cocok"] == 0 and len(r["pdf_diagnosa"]) == 1
    assert "lembar diagnosa" in r["catatan"].lower()


def test_handler_tanpa_pdf_tanpa_kartu(dunia):
    r = ai._t_cari_kode_kesalahan({"spn": 1551, "fmi": 5}, USER)
    assert r["pdf_diagnosa"] == []


def test_capture_meta_menyalurkan_ke_kartu(dunia, monkeypatch):
    # Lewat chat() stub: tool dipanggil → kartu masuk res.excel_exports.
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_allowed_tool_names",
                        lambda user, sheet_id="": {"cari_kode_kesalahan"})
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai, "_prefetch_epc_rangka", lambda history: None)
    seq = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "k1", "function": {"name": "cari_kode_kesalahan",
                                      "arguments": '{"spn":3216,"fmi":5}'}},
        ]}, "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "SPN 3216 FMI 5 = P2213, lembar diagnosa terlampir."},
                      "finish_reason": "stop"}]},
    ]
    calls = {"n": 0}

    def fake(messages, tools, max_tokens=6000):
        c = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return c

    monkeypatch.setattr(ai, "_post_chat", fake)
    out = ai.chat(USER, [{"role": "user", "content": "cek kesalahan SPN 3216 FMI 5"}])
    kartu = out["excel_exports"]
    assert len(kartu) == 1 and kartu[0]["filename"] == "SPN 3216 FMI 5.pdf"
    assert kartu[0]["judul"].startswith("Lembar diagnosa SPN 3216")
