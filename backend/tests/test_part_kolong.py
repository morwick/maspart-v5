"""Tool asisten `part_kolong` — DAFTAR PART KOLONG (undercarriage) per NOMOR RANGKA,
terkelompok per sistem (rem, setir, transmisi, gardan, kopel, suspensi, roda,
rangka, tangki/knalpot, dudukan).

Kasus nyata PF115691 (HOWO H3 4x4, 2026-08-12): 'cek part kolong <rangka>' tak
terjawab jalur lama — bom_dari_rangka memberi 618 baris DATAR tanpa pengelompokan,
cari_part_di_unit hanya melayani SATU istilah. Tool ini memakai KATEGORI RESMI
pohon unit (nama CN buatan pabrik) sebagai dasar pengelompokan.

Dua jebakan yang WAJIB tetap tertutup (keduanya nyata di unit itu):
  1. Nama EN kategori MENYESATKAN — '驾驶室后悬置' (dudukan KABIN) diterjemahkan
     EPC jadi 'Rear suspension of cab', dan 'ECU支架' jadi 'ECU mounting bracket'.
     Kalau EN ikut dipakai, keduanya masuk daftar kolong. CN harus menang.
  2. Kategori '外购…' (gardan/transmisi beli-jadi) hanya memberi NOMOR ASSEMBLY;
     isi dalamnya (kampas, hub, bearing, seal, as roda) tak ada di jalur ini →
     wajib ditandai + diarahkan ke part_aus_dari_rangka, JANGAN divonis 'tidak ada'.

Semua sumber di-mock (offline): test WAJIB mem-patch `ai.epc_bom.category_top`
dan `ai.epc_bom.category_open`, kalau tidak ia menembak EPC sungguhan.
"""
import copy

import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
STAF = {"username": "budi", "role": "user"}
PEMBELI = {"username": "roni", "role": "pembeli"}

FRAME = "PF115691"


def _kat(id_, plid, code, nama, nama_cn):
    return {"id": id_, "part_list_id": plid, "code": code,
            "nama": nama, "nama_cn": nama_cn}


# ── Kategori tingkat-atas unit (bentuk category_top) ─────────────────────────
KATEGORI = [
    _kat(1, 11, "A1", "Front suspension", "前悬架-FC"),
    _kat(2, 12, "A2", "Braking device/front axle", "前桥制动装置-FC"),
    _kat(3, 13, "A3", "Purchased drive axle assembly", "外购驱动桥总成-FH"),
    _kat(4, 14, "A4", "8JS85TE+QD40J gearbox", "外购变速器总成"),
    _kat(5, 15, "A5", "Transfer case suspension", "分动器悬置-FC"),
    _kat(6, 16, "A6", "Wheels and tires", "车轮和轮胎-FC"),
    _kat(7, 17, "A7", "Steering gear", "转向装置-FC"),          # gagal dibuka
    # ── JEBAKAN: nama EN-nya berbau kolong, nama CN-nya jelas BUKAN ──
    _kat(8, 18, "A8", "Rear suspension of cab", "驾驶室后悬置-FC"),
    _kat(9, 19, "A9", "ECU mounting bracket (Q23/two-point fixing)", "ECU支架-FC"),
    _kat(10, 20, "A10", "Air conditioning system", "空调系统-FC"),
]


def _p(pn, nama, nama_cn, qty=1):
    return {"pn": pn, "nama": nama, "nama_cn": nama_cn, "qty": qty}


# ── Isi tiap kategori (bentuk category_open) ─────────────────────────────────
ISI = {
    1: {"parts": [_p("FG9804523701", "Front left plate spring assembly", "前左钢板弹簧总成"),
                  _p("FG9604680003", "Cylindrical shock absorber", "筒式减振器", 2),
                  _p("ZQ150B1070", "Hex head bolt M10", "六角头螺栓M10", 2)],
        "sub_kategori": [_kat(101, 111, "S1", "Grease nipple assembly", "滑脂嘴总成")]},
    101: {"parts": [_p("AZ9003963010", "Grease nipple", "滑脂嘴")]},
    2: {"parts": [_p("FG9804364002", "Relay valve", "继动阀"),
                  _p("Q401B16", "Flat washer", "平垫圈", 4)]},
    3: {"parts": [_p("FZ711400000039", "Purchased drive axle assembly", "外购驱动桥总成")]},
    4: {"parts": [_p("FZ220100885010/1", "8JS85TE+QD40J (G35760) gearbox", "变速箱"),
                  _p("FZ220100885010_1_2", "2-axis assembly_virtual number",
                     "2 二轴总成_虚拟号")]},
    5: {"parts": [_p("FG9804263510", "Transfer case left mounting assembly", "分动箱左悬置总成")]},
    6: {"parts": [_p("FG9115610021", "7.00T-20 wheel assembly", "7.00T-20车轮总成", 6),
                  _p("FG9804618259", "9.00R20-16PR tire assembly", "9.00R20-16PR轮胎总成", 6)]},
}

# Indeks stok/harga lokal (bentuk part_index.rows_for_pns)
_LOKAL = {
    "FG9804523701": {"part_number": "FG9804523701", "part_name": "PER DEPAN KIRI H3",
                     "stok": "3", "harga": "Rp 2.500.000", "gudang": {"01.Jakarta": 3}},
    "FG9604680003": {"part_number": "FG9604680003", "part_name": "SHOCK ABSORBER H3",
                     "stok": "8", "harga": "Rp 450.000", "gudang": {"02.Pekanbaru": 8}},
}


@pytest.fixture
def dunia(monkeypatch):
    state = {"gagal": {7}}

    def _category_top(rangka):
        return {"found": True, "frame_number": FRAME, "order_no": "CYAH23080002",
                "root_id": 812036, "jumlah": len(KATEGORI),
                "kategori": copy.deepcopy(KATEGORI)}

    def _category_open(rangka, node_id, part_list_id=None, code=None):
        if node_id in state["gagal"]:
            return {"found": False, "frame_number": FRAME, "_err": "network"}
        isi = ISI.get(node_id) or {}
        return {"found": True, "frame_number": FRAME, "root_id": 812036,
                "sub_kategori": copy.deepcopy(isi.get("sub_kategori") or []),
                "parts": copy.deepcopy(isi.get("parts") or [])}

    monkeypatch.setattr(ai.epc_bom, "category_top", _category_top)
    monkeypatch.setattr(ai.epc_bom, "category_open", _category_open)
    monkeypatch.setattr(ai.part_index, "rows_for_pns",
                        lambda pns: {p: dict(_LOKAL[p]) for p in pns if p in _LOKAL})
    return state


def _baris(r, pn):
    for blok in r["part_per_sistem"].values():
        for p in blok["parts"]:
            if p["part_number"] == pn:
                return p
    return None


def _pns(r):
    return [p["part_number"] for blok in r["part_per_sistem"].values()
            for p in blok["parts"]]


# ── klasifikasi kategori → sistem (fungsi murni, tanpa jaringan) ─────────────
@pytest.mark.parametrize("nama_cn,harap", [
    ("前悬架-FC", "suspensi"),
    ("后悬架-FC", "suspensi"),
    ("减振器系统-FC", "suspensi"),
    ("前桥制动装置-FC", "rem"),          # ⚠️ '桥' ada, tapi REM yang menang
    ("底盘制动管束-FC", "rem"),
    ("驾驶室制动装置-FC", "rem"),        # rem sisi kabin tetap kolong (kran/total pump)
    ("转向直拉杆总成-FC", "setir"),
    ("外购驱动桥总成-FH", "gardan"),
    ("外购前驱桥总成-FH", "gardan"),     # '前驱桥' ≠ '驱动桥' — dua kata kunci terpisah
    ("分动器悬置-FC", "transmisi"),      # ⚠️ '悬置' ada, tapi TRANSMISI yang menang
    ("外购变速器总成", "transmisi"),
    ("全驱控制电器-FC", "transmisi"),
    ("主传动轴-FC", "kopel"),
    ("车轮和轮胎-FC", "roda"),
    ("备胎架及附件-FC", "roda"),
    ("车架总成-FC", "rangka"),
    ("前牵引装置-FC", "rangka"),
    ("燃油箱滤清器模块-FC", "bbm_knalpot"),
    ("排气系统-FC", "bbm_knalpot"),
    ("发动机悬置系统-FC", "dudukan"),
])
def test_klasifikasi_kategori_kolong(nama_cn, harap):
    assert ai.epc_bom.klasifikasi_sistem("", nama_cn) == harap


@pytest.mark.parametrize("nama,nama_cn", [
    # JEBAKAN NYATA: nama EN berbau kolong, CN-nya bukan.
    ("Rear suspension of cab", "驾驶室后悬置-FC"),
    ("Throttle control system (Xichai, mechanical throttle)", "驾驶室前悬置-FC"),
    ("ECU mounting bracket (Q23/two-point fixing)", "ECU支架-FC"),
    ("Air conditioning system", "空调系统-FC"),
    ("Light truck cab safety belt", "安全带总成-FC"),
    ("Bumper", "保险杠-FC"),
    ("Driver's cab harness", "驾驶室线束-FC"),
])
def test_kategori_bukan_kolong_ditolak(nama, nama_cn):
    """CN harus MENANG atas EN — kalau tidak, dudukan kabin & braket ECU ikut masuk."""
    assert ai.epc_bom.klasifikasi_sistem(nama, nama_cn) is None


def test_nama_en_dipakai_hanya_saat_cn_kosong():
    assert ai.epc_bom.klasifikasi_sistem("Front suspension", "") == "suspensi"
    assert ai.epc_bom.klasifikasi_sistem("Steering gear", "") == "setir"
    assert ai.epc_bom.klasifikasi_sistem("Sun visor", "") is None


@pytest.mark.parametrize("pn,nama,nama_cn,harap", [
    ("ZQ150B1070", "Hex head bolt M10", "六角头螺栓M10", True),
    ("Q401B16", "Flat washer", "平垫圈", True),
    ("QD10150A10F6", "M10 flange nut with teeth", "M10粗丝法兰面带齿螺母", True),
    ("Q4501244", "Half-round head rivet", "半圆头铆钉", True),
    ("WG9003171356", "Plastic fastening belt A5*280", "塑料紧固带A5*280", True),
    ("FG9804523701", "Front left plate spring assembly", "前左钢板弹簧总成", False),
    ("FG9604680003", "Cylindrical shock absorber", "筒式减振器", False),
    ("FG9604520001", "Plate spring pin", "板簧销", False),   # PEN PER ≠ pengencang
    ("FG9806540064", "Silencer clamp assembly", "消声器卡箍总成", False),
])
def test_penanda_pengencang(pn, nama, nama_cn, harap):
    assert ai.epc_bom.is_pengencang(pn, nama, nama_cn) is harap


# ── lapisan service: sistem_kolong ──────────────────────────────────────────
def test_sistem_kolong_hanya_membuka_kategori_kolong(dunia):
    d = ai.epc_bom.sistem_kolong(FRAME)
    assert d["found"] is True and d["frame_number"] == FRAME
    assert d["jumlah_kategori_unit"] == 10
    assert d["jumlah_kategori_kolong"] == 7      # 10 - 3 kategori non-kolong
    assert set(d["sistem"]) == {"rem", "transmisi", "gardan", "suspensi", "roda"}
    # kategori yang gagal dibuka DILAPORKAN, bukan diam-diam hilang
    assert d["kategori_gagal"] == ["Steering gear"] and d["incomplete"] is True


def test_sub_kategori_ikut_didrill(dunia):
    d = ai.epc_bom.sistem_kolong(FRAME)
    sus = d["sistem"]["suspensi"]["parts"]
    nip = next(r for r in sus if r["pn"] == "AZ9003963010")
    assert nip["dari_sub"] == "Grease nipple assembly"


def test_kategori_beli_jadi_dan_nomor_virtual_ditandai(dunia):
    d = ai.epc_bom.sistem_kolong(FRAME)
    gardan = d["sistem"]["gardan"]
    assert gardan["kategori_beli_jadi"] == ["Purchased drive axle assembly"]
    assert gardan["parts"][0]["beli_jadi"] is True
    virt = next(r for r in d["sistem"]["transmisi"]["parts"]
                if r["pn"] == "FZ220100885010_1_2")
    assert virt["virtual"] is True
    # suspensi bukan beli-jadi → kuncinya tak ada sama sekali
    assert "kategori_beli_jadi" not in d["sistem"]["suspensi"]


def test_saring_sistem_tertentu(dunia):
    d = ai.epc_bom.sistem_kolong(FRAME, ["rem"])
    assert set(d["sistem"]) == {"rem"}
    assert d["jumlah_kategori_kolong"] == 1


# ── tool: bentuk jawaban & gerbang ──────────────────────────────────────────
def test_tool_mengelompokkan_dan_menyembunyikan_pengencang(dunia):
    r = ai._t_part_kolong({"rangka": FRAME}, ADMIN)
    assert r["found"] is True and r["frame_number"] == FRAME
    assert list(r["part_per_sistem"]) == ["rem", "transmisi", "gardan",
                                          "suspensi", "roda"]
    pns = _pns(r)
    assert "ZQ150B1070" not in pns and "Q401B16" not in pns      # baut disembunyikan
    assert "FG9804523701" in pns and "FG9804364002" in pns
    assert r["jumlah_pengencang"] == 2
    assert "DISEMBUNYIKAN" in r["catatan_pengencang"]
    assert r["jumlah_part_kolong"] == len(pns)


def test_plafon_baris_dibagi_adil_antar_sistem(dunia, monkeypatch):
    """Regresi PF115691: plafon baris chat dulu dibagi SIAPA-DULUAN, sehingga
    sistem kecil di urutan akhir ('dudukan', 4 part) tampil KOSONG karena jatah
    sudah habis di sistem besar — dan daftar kosong terbaca seperti 'tidak ada'."""
    ISI[2]["parts"] = ([_p("FG980436%04d" % i, "Brake pipe %d" % i, "制动管%d" % i)
                        for i in range(60)] + ISI[2]["parts"])
    try:
        monkeypatch.setattr(ai, "_KOLONG_CHAT_MAX_TOTAL", 30)
        r = ai._t_part_kolong({"rangka": FRAME}, ADMIN)
    finally:
        ISI[2]["parts"] = ISI[2]["parts"][60:]
    rem = r["part_per_sistem"]["rem"]
    assert rem["dipotong"] > 0 and len(rem["parts"]) < rem["jumlah_part"]
    # sistem kecil TETAP utuh — tak boleh dikorbankan demi sistem besar
    for kecil in ("gardan", "roda", "suspensi", "transmisi"):
        blok = r["part_per_sistem"][kecil]
        assert "dipotong" not in blok and len(blok["parts"]) == blok["jumlah_part"], kecil


def test_tool_sertakan_baut(dunia):
    r = ai._t_part_kolong({"rangka": FRAME, "sertakan_baut": True}, ADMIN)
    assert "ZQ150B1070" in _pns(r) and "catatan_pengencang" not in r


def test_tool_stok_harga_untuk_admin(dunia):
    r = ai._t_part_kolong({"rangka": FRAME}, ADMIN)
    p = _baris(r, "FG9804523701")
    assert p["ada_di_inventori"] is True and p["stok_total"] == "3"
    assert p["harga_lokal"] == "Rp 2.500.000"
    assert p["stok_per_gudang"] == {"01.Jakarta": 3}
    assert p["nama"] == "PER DEPAN KIRI H3"          # nama katalog lokal menang
    assert _baris(r, "FG9804364002")["ada_di_inventori"] is False


def test_harga_disembunyikan_dari_staf_tanpa_izin(dunia, monkeypatch):
    monkeypatch.setattr(ai, "_boleh_harga", lambda u: False)
    r = ai._t_part_kolong({"rangka": FRAME}, STAF)
    p = _baris(r, "FG9804523701")
    assert "harga_lokal" not in p and p["stok_total"] == "3"


def test_pembeli_tak_lihat_rincian_gudang(dunia):
    r = ai._t_part_kolong({"rangka": FRAME}, PEMBELI)
    p = _baris(r, "FG9804523701")
    assert "stok_per_gudang" not in p and p["stok_total"] == "3"


def test_nomor_virtual_ditandai_dan_tak_dicari_stoknya(dunia, monkeypatch):
    diminta: list = []
    asli = ai.part_index.rows_for_pns

    def _spy(pns):
        diminta.extend(pns)
        return asli(pns)

    monkeypatch.setattr(ai.part_index, "rows_for_pns", _spy)
    r = ai._t_part_kolong({"rangka": FRAME}, ADMIN)
    v = _baris(r, "FZ220100885010_1_2")
    assert v["virtual_bukan_part"] is True and "ada_di_inventori" not in v
    assert "FZ220100885010_1_2" not in diminta


def test_peringatan_kategori_gagal_dan_catatan_beli_jadi(dunia):
    r = ai._t_part_kolong({"rangka": FRAME}, ADMIN)
    assert "Steering gear" in r["peringatan"] and "BELUM TENTU" in r["peringatan"]
    cat = r["catatan_assembly_beli_jadi"]
    assert "part_aus_dari_rangka" in cat and "BUKAN vonis" in cat
    # 'catatan' WAJIB kunci TERAKHIR (_cap_tool_content memotong bagian tengah)
    assert list(r)[-1] == "catatan"


def test_saring_sistem_lewat_tool_dan_sistem_tak_dikenal(dunia):
    r = ai._t_part_kolong({"rangka": FRAME, "sistem": "rem"}, ADMIN)
    assert list(r["part_per_sistem"]) == ["rem"]
    salah = ai._t_part_kolong({"rangka": FRAME, "sistem": ["kolongan"]}, ADMIN)
    assert "kolongan" in salah["error"] and "suspensi" in salah["error"]


def test_tanpa_rangka_dan_epc_mati(dunia, monkeypatch):
    assert "error" in ai._t_part_kolong({}, ADMIN)
    monkeypatch.setattr(ai.epc_bom, "category_top",
                        lambda r: {"found": False, "_err": "network"})
    r = ai._t_part_kolong({"rangka": FRAME}, ADMIN)
    assert r["found"] is False and "jaringan" in r["error"]
    monkeypatch.setattr(ai.epc_bom, "category_top",
                        lambda r: {"found": False, "_err": "token_expired"})
    assert ai._t_part_kolong({"rangka": FRAME}, ADMIN)["_token_issue"] is True


def test_semua_kategori_gagal_tak_boleh_jadi_vonis_tidak_ada(dunia):
    dunia["gagal"] = {1, 2, 3, 4, 5, 6, 7}
    r = ai._t_part_kolong({"rangka": FRAME}, ADMIN)
    assert r["found"] is False
    assert "BUKAN" in r["jawaban_wajib"] and "JANGAN mengarang" in r["jawaban_wajib"]


# ── excel ───────────────────────────────────────────────────────────────────
def test_excel_memuat_seluruh_baris_termasuk_pengencang(dunia):
    r = ai._t_part_kolong({"rangka": FRAME, "excel": True}, ADMIN)
    assert r["export_id"] and r["filename"].endswith(".xlsx")
    assert r["jumlah_baris"] == 12        # 10 part + 2 pengencang
    payload = ai.ai_export._stash[r["export_id"]]
    kolom = payload["kolom"]
    assert kolom[:4] == ["No", "Sistem", "Kategori EPC", "Part Number"]
    assert "Stok Total" in kolom and "Harga" in kolom and kolom[-1] == "Catatan"
    baut = [b for b in payload["baris"] if b[3] == "ZQ150B1070"]
    assert baut and baut[0][7] == "Pengencang"
    virt = [b for b in payload["baris"] if b[3] == "FZ220100885010_1_2"][0]
    assert "VIRTUAL" in virt[-1]
    assy = [b for b in payload["baris"] if b[3] == "FZ711400000039"][0]
    assert "beli-jadi" in assy[-1]
    # Sel stok & HARGA wajib ANGKA mentah supaya rumus Excel user jalan (aturan
    # pemilik 2026-07-20). rows_for_pns memberi 'Rp 2.500.000' terformat — kalau
    # tidak dinormalkan, export ini meledak/berisi teks.
    per = [b for b in payload["baris"] if b[3] == "FG9804523701"][0]
    assert per[kolom.index("Harga")] == 2500000
    assert per[kolom.index("Stok Total")] == 3


def test_excel_pembeli_tanpa_kolom_stok_harga(dunia):
    r = ai._t_part_kolong({"rangka": FRAME, "excel": True}, PEMBELI)
    kolom = ai.ai_export._stash[r["export_id"]]["kolom"]
    assert "Stok Total" not in kolom and "Harga" not in kolom
    assert r["kolom_stok"] is False and r["kolom_harga"] is False
