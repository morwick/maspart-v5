"""Test maintenance_ref — jadwal perawatan berkala Shantui dari xlsx.

Fokus: (1) parse layout NYATA (baris meta, header interval dinamis 50/50h/100h,
baris SISTEM vs baris item), (2) filter per model/jam/kata-kunci, (3) sinonim
LINTAS-BAHASA (nama file campur Inggris/Spanyol/Mandarin) + batas kata
('hidrolik' tak nyangkut ke 'oli'), (4) baris catatan kaki diabaikan,
(5) file hilang → kosong tanpa error.
"""
import pytest
from openpyxl import Workbook

from app.services import maintenance_ref


def _write_book(path):
    wb = Workbook()
    # ── Sheet dozer: nama Spanyol + Mandarin, interval '50/250/500/1000' ──
    ws = wb.active
    ws.title = "SD_TEST"
    ws.append(["HISTORIAL DE MANTENIMIENTO PREVENTIVO"])
    ws.append(["UNIDAD:Bulldozer SD99 国二"])
    ws.append(["MARCA : SHANTUI"])
    ws.append(["MODELO : SD99国二"])
    ws.append([])
    ws.append(["PLAN DE MANTENIMIENTO PREVENTIVO 服务指南"])
    ws.append([" SERVICIOS 服务", "N/P SHANTUI", "N/P EQUIVALENTE",
               "N/P FLEETGUARD", "CANTIDAD", "Intervalo en horas 更换时间",
               "50", "250", "500", "1000"])
    ws.append(["MOTOR 发动机"])                                     # SISTEM
    ws.append(["Filtro de aceite de motor 机油滤", "OF-1", "", "", "1", "",
               "", "X", "X", "X"])                                 # 250/500/1000
    ws.append(["Filtro de Diesel 柴滤", "FF-1", "", "", "2", "",
               "", "", "X", "X"])                                  # 500/1000
    ws.append(["SISTEMA HIDRAULICO 液压系统"])                      # SISTEM
    ws.append(["Hydraulic fluids 液压油", "HY-1", "", "", "100L", "",
               "", "", "", "X"])                                   # 1000
    ws.append(["Note: valores de referencia 备注：仅作参照值"])       # catatan → skip

    # ── Sheet loader: nama Inggris, interval pakai '50h'/'100h' ──
    ws2 = wb.create_sheet("L_TEST")
    ws2.append(["HISTORIAL DE MANTENIMIENTO PREVENTIVO"])
    ws2.append(["UNIDAD:Loader L99-B5 国二"])
    ws2.append(["MARCA : SHANTUI"])
    ws2.append(["MODELO : L99-B5国二（潍柴）"])
    ws2.append([])
    ws2.append(["PLAN DE MANTENIMIENTO PREVENTIVO 服务指南"])
    ws2.append([" SERVICIOS 服务", "N/P SHANTUI/件号", "N/P EQUIVALENTE",
                "N/P FLEETGUARD", "CANTIDAD/数量", "Intervalo en horas 更换时间",
                "50h", "100h", "250", "500"])
    ws2.append(["MOTOR 发动机"])
    ws2.append(["Engine oil filter element 机油滤芯", "LOF-1", "", "", "1", "",
                "X", "", "X", "X"])                                # 50/250/500
    ws2.append(["FUEL FILTER 燃油滤清器", "LFF-1", "", "", "2", "",
                "", "", "", "X"])                                  # 500
    wb.save(path)


@pytest.fixture()
def fake_book(tmp_path, monkeypatch):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    _write_book(manuals / maintenance_ref._FILE_NAME)

    class _S:
        data_path = tmp_path

    monkeypatch.setattr(maintenance_ref, "get_settings", lambda: _S())
    monkeypatch.setattr(maintenance_ref, "_cache", {"mtime": None, "rows": []})
    return tmp_path


def test_available_dan_models(fake_book):
    assert maintenance_ref.available() is True
    # Kode model bersih (buang embel Mandarin & tanda kurung), urut sheet.
    assert maintenance_ref.models() == ["SD99", "L99-B5"]


def test_parse_item_dan_sistem(fake_book):
    rows = maintenance_ref.search("SD99")
    by_pn = {r["part_number"]: r for r in rows}
    assert set(by_pn) == {"OF-1", "FF-1", "HY-1"}
    assert by_pn["OF-1"]["sistem"].startswith("MOTOR")
    assert by_pn["OF-1"]["ganti_jam"] == [250, 500, 1000]
    assert by_pn["OF-1"]["qty"] == "1"
    assert "HIDRAULICO" in by_pn["HY-1"]["sistem"]
    assert by_pn["HY-1"]["ganti_jam"] == [1000]


def test_baris_catatan_diabaikan(fake_book):
    assert all(not r["nama"].lower().startswith("note")
               for r in maintenance_ref.search())


def test_filter_per_jam(fake_book):
    pn500 = {r["part_number"] for r in maintenance_ref.search("SD99", jam=500)}
    assert pn500 == {"OF-1", "FF-1"}
    pn250 = {r["part_number"] for r in maintenance_ref.search("SD99", jam=250)}
    assert pn250 == {"OF-1"}


def test_interval_header_bersuffix_h(fake_book):
    # Loader pakai '50h'/'100h' → harus terbaca sebagai jam 50/100.
    pn50 = {r["part_number"] for r in maintenance_ref.search("L99-B5", jam=50)}
    assert pn50 == {"LOF-1"}


def test_sinonim_lintas_bahasa(fake_book):
    # 'solar' (ID) → fuel/柴滤: cocokkan 'Filtro de Diesel 柴滤' & 'FUEL FILTER'.
    assert {r["part_number"] for r in maintenance_ref.search("SD99", query="solar")} == {"FF-1"}
    assert {r["part_number"] for r in maintenance_ref.search("L99-B5", query="solar")} == {"LFF-1"}
    # 'oli' (ID) → oil/aceite/机油: cocok item mesin, BUKAN item hidrolik.
    assert {r["part_number"] for r in maintenance_ref.search("SD99", query="oli")} == {"OF-1"}


def test_batas_kata_hidrolik_bukan_oli(fake_book):
    # 'hidrolik' MENGANDUNG substring 'oli' — tak boleh menyeret item oli mesin.
    res = maintenance_ref.search("SD99", query="hidrolik")
    assert {r["part_number"] for r in res} == {"HY-1"}


def test_file_hilang_kosong(tmp_path, monkeypatch):
    class _S:
        data_path = tmp_path  # tanpa subfolder manuals

    monkeypatch.setattr(maintenance_ref, "get_settings", lambda: _S())
    monkeypatch.setattr(maintenance_ref, "_cache", {"mtime": None, "rows": []})
    assert maintenance_ref.available() is False
    assert maintenance_ref.models() == []
    assert maintenance_ref.search("SD99") == []
