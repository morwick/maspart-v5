"""Tool asisten `cari_part_di_unit` — pencarian nama part DI DALAM satu unit lewat
EPC (match/part t=car), jalur utama saat user menyebut rangka + nama part.

Kenapa ada: 'kampas rem SJ346500' lewat bom_dari_rangka (Loading List) menghasilkan
0 part — kampas tersembunyi di dalam brake shoe assembly — padahal AZ450045000042
(depan) & AZ450045000024 (belakang) memang terpasang. Walk Atlas menemukannya tapi
18-22 dtk & hanya domain terpetakan. Pencarian EPC per-unit: ~1 dtk, seluruh katalog.
EPC hanya paham EN/CN → istilah lapangan diterjemahkan kamus sinonim dulu.
"""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
STAF = {"username": "budi", "role": "user"}
PEMBELI = {"username": "roni", "role": "pembeli"}


@pytest.fixture
def dunia(monkeypatch):
    dicari: dict = {}

    def _search(rangka, keywords):
        dicari["keywords"] = list(keywords)
        if any("friction" in k.lower() for k in keywords):
            return {"found": True, "frame_number": rangka.upper(), "order_no": "CYAJ24110049",
                    "hasil": [
                        {"pn": "AZ450045000042", "nama": "Brake friction plate", "kata_kunci": "brake friction plate"},
                        {"pn": "AZ450045000024", "nama": "Brake friction plate", "kata_kunci": "brake friction plate"},
                    ]}
        return {"found": False, "frame_number": rangka.upper(), "hasil": []}

    monkeypatch.setattr(ai.epc_bom, "search_in_unit", _search)
    monkeypatch.setattr(ai.epc_bom, "reverse_find_in_unit", lambda r, pn: {
        "found": True, "instances": [
            {"parent_pn": "AZ450045001161", "parent_nama": "Brake shoe assembly"},
            {"parent_pn": "AZ450045001160", "parent_nama": "Brake shoe assembly"},
        ]})
    monkeypatch.setattr(ai, "_expand_query",
                        lambda q: ([q, "brake friction plate", "brake lining"], ["kampas rem"])
                        if "kampas" in q.lower() else ([q], []))
    # rows_for_pns = silang inventori PEMAAF suffix varian EPC (lihat
    # test_pn_suffix_varian.py); di sini cukup indeks tiruan berisi 1 PN.
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {
        p: {"part_number": "AZ450045000042", "part_name": "Brake friction plate",
            "stok": "60", "harga": "Rp 112.000", "gudang": {"02.Pekanbaru": 60}}
        for p in pns if p.upper().startswith("AZ450045000042")
    })
    # Sisiran teliti (auto saat match nihil) — kembalikan nihil juga secara default
    # supaya cabang "jujur tidak ada" tercapai tanpa menyentuh jaringan EPC.
    monkeypatch.setattr(ai.epc_bom, "search_items_in_unit",
                        lambda r, k: {"found": False, "frame_number": r.upper(),
                                      "hasil": [], "incomplete": False})
    # Tangkap miss (JANGAN tulis ke data/search_misses.json produksi saat test).
    dicari["misses"] = []
    monkeypatch.setattr(ai.search_log, "record_miss",
                        lambda *a, **k: dicari["misses"].append(a))
    return dicari


def test_kampas_ketemu_lewat_terjemahan_sinonim(dunia):
    """Istilah lapangan 'kampas rem' diterjemahkan ke nama katalog EPC — EPC tak
    paham bahasa Indonesia."""
    r = ai._t_cari_part_di_unit({"rangka": "SJ346500", "kata_kunci": "kampas rem"}, ADMIN)
    assert r["found"] and r["jumlah_part"] == 2
    assert "brake friction plate" in dunia["keywords"]        # sinonim dipakai
    pns = [p["part_number"] for p in r["parts"]]
    assert pns == ["AZ450045000042", "AZ450045000024"]        # depan & belakang
    assert r["catatan_sinonim"] and "kampas rem" in r["catatan_sinonim"]


def test_assembly_induk_disertakan(dunia):
    r = ai._t_cari_part_di_unit({"rangka": "SJ346500", "kata_kunci": "kampas rem"}, ADMIN)
    p0 = r["parts"][0]
    assert p0["di_dalam_assembly"] == "Brake shoe assembly"
    assert p0["assembly_pn"] == "AZ450045001161" and p0["jumlah_posisi"] == 2


def test_silang_inventori_stok_dan_harga_admin(dunia):
    r = ai._t_cari_part_di_unit({"rangka": "SJ346500", "kata_kunci": "kampas rem"}, ADMIN)
    p0, p1 = r["parts"]
    assert p0["ada_di_inventori"] is True and p0["stok_total"] == "60"
    assert p0["harga_lokal"] == "Rp 112.000"
    assert p1["ada_di_inventori"] is False                     # tak ada di indeks lokal


def test_harga_disembunyikan_dari_staf_biasa(dunia):
    """Aturan harga asisten: hanya admin & akun 'mas'."""
    r = ai._t_cari_part_di_unit({"rangka": "SJ346500", "kata_kunci": "kampas rem"}, STAF)
    assert "harga_lokal" not in r["parts"][0] and r["parts"][0]["stok_total"] == "60"


def test_pembeli_tak_lihat_rincian_gudang(dunia):
    r = ai._t_cari_part_di_unit({"rangka": "SJ346500", "kata_kunci": "kampas rem"}, PEMBELI)
    assert "stok_per_gudang" not in r["parts"][0]


def test_tidak_ada_hasil_jawab_jujur(dunia):
    r = ai._t_cari_part_di_unit({"rangka": "SJ346500", "kata_kunci": "sayap pesawat"}, ADMIN)
    assert r["found"] is False and "Tidak ada part" in r["error"]
    assert "JANGAN mengarang" in r["jawaban_wajib"]


def test_nihil_tanpa_sinonim_dicatat_ke_loop_belajar(dunia):
    """Jalur per-VIN (jalur utama) menyuplai loop belajar sinonim: istilah TAK
    dikenali kamus yang 0 hasil → tercatat sbg miss 'asisten_unit'."""
    ai._t_cari_part_di_unit({"rangka": "SJ346500", "kata_kunci": "sayap pesawat"}, ADMIN)
    assert ("sayap pesawat", "unit", "asisten_unit") in dunia["misses"]


def test_nihil_tapi_dikenali_sinonim_tak_dicatat(dunia, monkeypatch):
    """Istilah yang DIKENALI kamus tapi 0 hasil = data unit memang tak punya,
    BUKAN celah kamus → jangan cemari daftar miss."""
    # 'kampas rem' dikenali sinonim (matched_syn ada) tapi paksa hasil nihil.
    monkeypatch.setattr(ai.epc_bom, "search_in_unit",
                        lambda r, k: {"found": False, "frame_number": r.upper(), "hasil": []})
    r = ai._t_cari_part_di_unit({"rangka": "SJ346500", "kata_kunci": "kampas rem"}, ADMIN)
    assert r["found"] is False
    assert dunia["misses"] == []


def test_umbrella_hanya_saat_sinonim_tak_kena(monkeypatch):
    """Kata payung 'kopling' (tak kena trigger sinonim frasa) → keyword keluarga
    penuh dijaring; 'kampas kopling' (kena sinonim) TIDAK diperlebar umbrella."""
    dipakai = {}

    def _search(r, k):
        dipakai["kw"] = list(k)
        return {"found": False, "frame_number": r.upper(), "hasil": []}

    monkeypatch.setattr(ai.epc_bom, "search_in_unit", _search)
    monkeypatch.setattr(ai.epc_bom, "search_items_in_unit",
                        lambda r, k: {"found": False, "hasil": [], "incomplete": False})
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(ai.search_log, "record_miss", lambda *a, **k: None)
    monkeypatch.setattr(ai, "_umbrella_keywords",
                        lambda kata: ["clutch driven disc", "pressure plate"]
                        if "kopling" in kata.lower() else [])
    monkeypatch.setattr(ai, "_expand_query",
                        lambda q: ([q, "clutch driven disc"], ["kampas kopling"])
                        if "kampas" in q.lower() else ([q], []))

    ai._t_cari_part_di_unit({"rangka": "SJ346500", "kata_kunci": "kopling"}, ADMIN)
    assert "pressure plate" in dipakai["kw"]       # umbrella menjaring keluarga

    dipakai.clear()
    ai._t_cari_part_di_unit({"rangka": "SJ346500", "kata_kunci": "kampas kopling"}, ADMIN)
    assert "pressure plate" not in dipakai["kw"]    # sinonim kena → tak diperlebar


def test_epc_bermasalah_bukan_jawaban_kosong(dunia, monkeypatch):
    monkeypatch.setattr(ai.epc_bom, "search_in_unit",
                        lambda r, k: {"found": False, "_err": "network"})
    r = ai._t_cari_part_di_unit({"rangka": "SJ346500", "kata_kunci": "kampas rem"}, ADMIN)
    assert r["found"] is False and "jaringan" in r["error"].lower()


def test_multi_istilah_satu_panggilan(dunia):
    """Log produksi: model memanggil tool 3-4× per giliran dgn istilah berbeda →
    kini SEMUA istilah (array) dicari sekali jalan; hasil dilabeli per istilah &
    istilah nihil disebut eksplisit."""
    r = ai._t_cari_part_di_unit(
        {"rangka": "SJ346500", "kata_kunci": ["kampas rem", "sayap pesawat"]}, ADMIN)
    assert r["found"] and r["jumlah_part"] == 2
    # kedua istilah ikut dicari dalam SATU panggilan search
    assert "sayap pesawat" in dunia["keywords"]
    assert "brake friction plate" in dunia["keywords"]
    # hasil dilabeli istilah asalnya
    assert all(p["untuk_istilah"] == "kampas rem" for p in r["parts"])
    # istilah tanpa hasil disebut eksplisit + instruksi jujur
    assert r["istilah_tanpa_hasil"] == ["sayap pesawat"]
    assert "JANGAN mengarang" in r["catatan_istilah_nihil"]


def test_multi_istilah_string_pemisah_titik_koma(dunia):
    r = ai._t_cari_part_di_unit(
        {"rangka": "SJ346500", "kata_kunci": "kampas rem; sayap pesawat"}, ADMIN)
    assert r["found"] and r["istilah_tanpa_hasil"] == ["sayap pesawat"]
    assert "sayap pesawat" in dunia["keywords"]


def test_istilah_tunggal_tanpa_label_ekstra(dunia):
    """Back-compat: satu istilah = output persis seperti dulu (tanpa untuk_istilah/
    istilah_tanpa_hasil)."""
    r = ai._t_cari_part_di_unit({"rangka": "SJ346500", "kata_kunci": "kampas rem"}, ADMIN)
    assert "untuk_istilah" not in r["parts"][0]
    assert "istilah_tanpa_hasil" not in r and "catatan_istilah_nihil" not in r


def test_terdaftar_sebagai_tool_epc_per_vin():
    """Wajib masuk _EPC_VIN_PART_TOOLS: hasilnya PN per-VIN otoritatif (dipakai guard
    substitusi katalog-lokal) & dispatch."""
    assert ai._DISPATCH["cari_part_di_unit"] is ai._t_cari_part_di_unit
    assert "cari_part_di_unit" in ai._EPC_VIN_PART_TOOLS
    names = {s["function"]["name"] for s in ai._tool_specs(ADMIN)}
    assert "cari_part_di_unit" in names
