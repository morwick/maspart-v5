"""BATCH: satu panggilan tool, banyak item — aturan pindah dari PROSA ke SKEMA.

Kenapa ada (audit 1.189 giliran produksi, 17 Jul–16 Agu 2026):
 • 44% panggilan tool (1.394/3.196) = nama tool SAMA diulang dalam SATU giliran;
 • giliran ber-ulang: 2,80 ronde & 43,9 dtk — tanpa ulang: 1,09 ronde & 19,8 dtk;
 • tiap ronde membayar ulang blok tetap ±43.832 token (system prompt + spec).

⭐ Eksperimen alami di data sendiri: tool yang MENDEKLARASIKAN array praktis tak
pernah diulang (cek_massal_part 9 ulang/70 pakai, kategori_massal_part 0,
spek_massal_rangka 0), sedangkan yang hanya MELARANG lewat prosa diulang ratusan
kali — detail_part sudah memuat larangan huruf besar ber-⛔ dan tetap jadi
pelanggar nomor 1 (251 pengulangan). Kesimpulan: prosa tidak bekerja, skema
bekerja.

⚠️ Sifat yang WAJIB dijaga tes ini:
 1. KOMPATIBEL MUNDUR MUTLAK — string tunggal tetap menempuh jalur LAMA persis.
 2. Array TIDAK PERNAH menyembunyikan kegagalan: satu item rusak ≠ sisanya batal.
 3. Bendera operasi TULIS (perlu_konfirmasi) TERANGKAT ke tingkat atas.
 4. Plafon per tool ditegakkan, dan yang terpotong DILAPORKAN (bukan diam).
 5. Bentuk hasil tetap terbaca lapisan hilir (_salvage_rows/_fakta_from_tool).
"""
import json

import pytest

from app.services import ai_assistant as A

ADMIN = {"username": "mas", "role": "admin"}


def _spec(nama, sheet_id=""):
    for s in A._tool_specs(ADMIN, sheet_id):
        if s["function"]["name"] == nama:
            return s["function"]
    raise AssertionError(f"tool {nama} tak ada di spec")


def _tipe(nama, param, sheet_id=""):
    p = (_spec(nama, sheet_id).get("parameters") or {}).get("properties") or {}
    return (p.get(param) or {}).get("type")


# ── 1. SKEMA benar-benar mendeklarasikan array ──────────────────────────────
# Ini inti perubahan: kalau deklarasinya hilang, model kembali memanggil berulang
# dan seluruh perbaikan batal DIAM-DIAM (tak ada tes lain yang menangkapnya).
@pytest.mark.parametrize("nama,param", [
    ("detail_part", "part_number"),
    ("cari_part", "query"),
    ("cari_part_di_unit", "rangka"),
    ("cari_part_di_unit", "kata_kunci"),
    ("pengganti_part", "part_number"),
    ("unit_dari_part", "part_number"),
    ("cek_kendaraan", "rangka"),
    ("bom_dari_rangka", "rangka"),
    ("part_aus_dari_rangka", "rangka"),
    ("part_dari_mesin", "no_mesin"),
    ("uraikan_assembly", "assembly"),
    ("gambar_exploded", "pn"),
    ("cek_populasi", "query"),
    ("lihat_unit_armada", "unit"),
    ("masukkan_unit_fleet", "unit"),
])
def test_parameter_menerima_array_DAN_string(nama, param):
    t = _tipe(nama, param)
    assert isinstance(t, list) and "array" in t and "string" in t, (
        f"{nama}.{param} bertipe {t!r} — harus menerima array DAN string "
        "(string demi kompatibilitas mundur)")


def test_ganti_nama_unit_pakai_pasangan_bukan_dua_array_sejajar():
    """Unit ↔ nama barunya harus TERIKAT dalam satu objek. Dua array sejajar
    (cjh[] + nama_baru[]) bisa salah-pasang diam-diam bila panjangnya beda —
    dan salah-pasang di operasi TULIS berarti unit orang lain ikut berganti nama."""
    p = (_spec("ganti_nama_unit").get("parameters") or {}).get("properties") or {}
    assert p["daftar"]["type"] == "array"
    item = p["daftar"]["items"]
    assert item["type"] == "object"
    assert set(item["required"]) == {"cjh", "nama_baru"}
    assert p["cjh"]["type"] == "string", "jalur satu-unit tetap string"


# ── 2. _as_items: normalisasi bentuk lama & baru ────────────────────────────
def test_as_items_menerima_string_maupun_array():
    assert A._as_items("RT108966") == ["RT108966"]
    assert A._as_items(["A1234", "B5678"]) == ["A1234", "B5678"]
    assert A._as_items("A1234; B5678") == ["A1234", "B5678"]
    assert A._as_items("A1234\nB5678") == ["A1234", "B5678"]


def test_as_items_TIDAK_memecah_spasi():
    """Nama part lapangan berisi spasi — memecahnya membuat 'kampas rem depan'
    jadi tiga pencarian omong kosong."""
    assert A._as_items("kampas rem depan") == ["kampas rem depan"]


def test_as_items_dedup_dan_plafon():
    assert A._as_items(["A1", "A1", "B2"], min_len=2) == ["A1", "B2"]
    assert len(A._as_items([f"PN{i:04d}" for i in range(50)], maks=10)) == 10


def test_as_items_upper_dan_min_len():
    assert A._as_items(["rt108966"], upper=True) == ["RT108966"]
    assert A._as_items(["ab", "RT108966"], min_len=4) == ["RT108966"]


# ── 3. KOMPATIBEL MUNDUR: string tunggal = jalur LAMA, tak tersentuh ────────
def test_string_tunggal_tidak_lewat_batch():
    dipanggil = []

    def fn(args, user):
        dipanggil.append(args.get("rangka"))
        return {"found": True}

    args = {"rangka": "RT108966"}
    assert A._batch_wrap(fn, args, ADMIN, "rangka") is None, \
        "satu item harus mengembalikan None supaya pemanggil menempuh jalur lama"
    assert dipanggil == [], "handler tak boleh dipanggil oleh _batch_wrap"


def test_array_menyusut_jadi_satu_DIRAPIKAN_jadi_string():
    """⛔ Regresi yang mahal: jalur lama langsung .strip() — bila array menyusut
    jadi ≤1 item, list mentah sampai ke sana dan meledakkan giliran user."""
    args = {"rangka": ["ab", "RT108966"]}          # 'ab' dibuang (min_len=4)
    assert A._batch_wrap(lambda a, u: {}, args, ADMIN, "rangka", min_len=4) is None
    assert args["rangka"] == "RT108966", "harus jadi STRING, bukan list"

    kosong = {"rangka": ["ab", "cd"]}
    assert A._batch_wrap(lambda a, u: {}, kosong, ADMIN, "rangka", min_len=4) is None
    assert kosong["rangka"] == "", "semua tersaring → string kosong, bukan list"


def test_alias_list_ikut_dirapikan():
    args = {"pn": ["ab", "WG9725550199"]}
    assert A._batch_wrap(lambda a, u: {}, args, ADMIN, "part_number",
                         min_len=4, alias=("pn",)) is None
    assert args["part_number"] == "WG9725550199"
    assert not isinstance(args["pn"], list)


# ── 4. Fan-out: tiap item dijalankan, hasil digabung ────────────────────────
def test_array_menjalankan_handler_untuk_TIAP_item():
    dipanggil = []

    def fn(args, user):
        dipanggil.append(args["rangka"])
        return {"rows": [{"pn": f"PN-{args['rangka']}"}]}

    out = A._batch_wrap(fn, {"rangka": ["AAAA1111", "BBBB2222", "CCCC3333"]},
                        ADMIN, "rangka")
    assert sorted(dipanggil) == ["AAAA1111", "BBBB2222", "CCCC3333"]
    assert out["jumlah_item"] == 3
    assert len(out["rows"]) == 3
    assert {r["rangka"] for r in out["rows"]} == set(dipanggil)


def test_baris_digabung_ke_KUNCI_yang_dikenal_lapisan_hilir():
    """_salvage_rows & _fakta_from_tool mencari list-of-dict di kunci tertentu.
    Kalau hasil batch memakai kunci asing, jaring penyelamat & memori sesi buta."""
    def fn(args, user):
        return {"rows": [{"pn": "X"}]}

    out = A._batch_wrap(fn, {"rangka": ["AAAA1111", "BBBB2222"]}, ADMIN, "rangka")
    assert A._salvage_rows(out), "hasil batch harus terbaca _salvage_rows"
    assert "rows" in out


def test_hasil_objek_tunggal_dikumpulkan_di_kunci_hasil():
    def fn(args, user):
        return {"found": True, "model": "ZZ" + args["rangka"]}

    out = A._batch_wrap(fn, {"rangka": ["AAAA1111", "BBBB2222"]}, ADMIN, "rangka")
    assert len(out["hasil"]) == 2
    assert out["hasil"][0]["rangka"] == "AAAA1111"
    assert A._salvage_rows(out), "bentuk objek pun harus terbaca _salvage_rows"


def test_satu_item_GAGAL_tidak_menjatuhkan_sisanya():
    """Inti perbaikannya: dulu panggilan ke-4 ditolak rem lalu model menyimpulkan
    'tidak ada' untuk SEMUA. Sekarang kegagalan bersifat per item & dilaporkan."""
    def fn(args, user):
        if args["rangka"] == "BBBB2222":
            raise RuntimeError("EPC ambruk")
        return {"rows": [{"pn": "X"}]}

    out = A._batch_wrap(fn, {"rangka": ["AAAA1111", "BBBB2222", "CCCC3333"]},
                        ADMIN, "rangka")
    assert out["item_gagal"] == ["BBBB2222"]
    assert len(out["rows"]) == 2, "dua item sukses tetap terjawab"


# ── 5. Plafon ditegakkan DAN dilaporkan (⛔ jangan diam) ────────────────────
def test_kelebihan_plafon_dilaporkan_bukan_dibuang_diam_diam():
    def fn(args, user):
        return {"rows": [{"pn": args["rangka"]}]}

    items = [f"AAAA{i:04d}" for i in range(8)]
    out = A._batch_wrap(fn, {"rangka": items}, ADMIN, "rangka", maks=5)
    assert out["jumlah_item"] == 5
    nota = out.get("catatan_batch") or ""
    assert "BELUM" in nota and "3" in nota, f"nota potongan tak jelas: {nota!r}"
    for sisa in items[5:]:
        assert sisa in nota, "item yang belum dicek harus DISEBUT"


def test_tool_berat_berplafon_ketat():
    """gambar_exploded dingin ±94 dtk/PN — array tak boleh jadi jalan pintas
    meledakkan kerja. Plafonnya dijaga di SPEC (yang dibaca model)."""
    d = ((_spec("gambar_exploded").get("parameters") or {})
         .get("properties") or {})["pn"]["description"]
    assert "maks 4" in d
    d2 = ((_spec("part_aus_dari_rangka").get("parameters") or {})
          .get("properties") or {})["rangka"]["description"]
    assert "maks 12" in d2


# ── 6. Operasi TULIS: bendera konfirmasi TERANGKAT ──────────────────────────
def test_perlu_konfirmasi_diangkat_ke_tingkat_atas():
    """⛔ Kasus rusak 2026-08-13: 3 unit = 6 panggilan (pratinjau+konfirmasi) →
    menabrak rem → asisten melaporkan 'ketiga unit BELUM dipindahkan' padahal
    user setuju. Kalau benderanya cuma ada DI DALAM tiap item, alur konfirmasi
    terlewat lagi dengan cara yang sama."""
    def fn(args, user):
        return {"perlu_konfirmasi": True, "pratinjau": {"unit": args["unit"]}}

    out = A._batch_wrap(fn, {"unit": ["AAAA1111", "BBBB2222", "CCCC3333"]},
                        ADMIN, "unit", paralel=False)
    assert out.get("perlu_konfirmasi") is True
    cat = out.get("catatan") or ""
    assert "konfirmasi" in cat.lower()
    assert "AAAA1111" in cat and "CCCC3333" in cat


def test_ganti_nama_unit_daftar_tidak_pernah_salah_pasang(monkeypatch):
    dipanggil = []

    def palsu(args, user):
        if isinstance(args.get("daftar"), (list, tuple)) and len(args["daftar"]) > 1:
            return asli(args, user)
        dipanggil.append((args.get("cjh"), args.get("nama_baru")))
        return {"perlu_konfirmasi": True}

    asli = A._t_ganti_nama_unit
    monkeypatch.setattr(A, "_t_ganti_nama_unit", palsu)
    monkeypatch.setattr(A, "_is_admin", lambda u: True)
    monkeypatch.setattr(A.telematics, "available", lambda: True)
    out = palsu({"daftar": [{"cjh": "AAAA1111", "nama_baru": "TRUK A"},
                            {"cjh": "BBBB2222", "nama_baru": "TRUK B"}]}, ADMIN)
    assert dipanggil == [("AAAA1111", "TRUK A"), ("BBBB2222", "TRUK B")]
    assert out.get("perlu_konfirmasi") is True


# ── 7. detail_part banyak PN → jalur massal yang sudah teruji ───────────────
def test_detail_part_banyak_pn_didelegasikan_ke_jalur_massal(monkeypatch):
    """Bukan fan-out per-PN: _t_cek_massal_part membaca indeks stok/harga SEKALI
    untuk seluruh daftar. Fan-out akan mengulang kerja indeks itu per PN."""
    lihat = {}

    def massal(args, user):
        lihat["daftar_pn"] = args.get("daftar_pn")
        return {"part": [{"pn": p} for p in args["daftar_pn"]]}

    monkeypatch.setattr(A, "_t_cek_massal_part", massal)
    out = A._t_detail_part({"part_number": ["WG9725550199", "AZ9003090022"]}, ADMIN)
    assert lihat["daftar_pn"] == ["WG9725550199", "AZ9003090022"]
    assert len(out["part"]) == 2


def test_detail_part_satu_pn_TIDAK_lewat_massal(monkeypatch):
    monkeypatch.setattr(A, "_t_cek_massal_part",
                        lambda a, u: pytest.fail("satu PN tak boleh ke jalur massal"))
    out = A._t_detail_part({"part_number": "   "}, ADMIN)
    assert out.get("error") == "part_number kosong"


# ── 8. Gerbang izin & pagar prompt tetap utuh ──────────────────────────────
def test_batch_tidak_mengubah_daftar_tool_yang_diizinkan():
    """Perubahan ini hanya menyentuh BENTUK parameter — tak satu pun tool boleh
    hilang dari menu maupun dari allow-list eksekusi."""
    izin = A._allowed_tool_names(ADMIN)
    for t in ("detail_part", "cek_massal_part", "cari_part", "cari_part_di_unit",
              "pengganti_part", "cek_kendaraan", "lihat_unit_armada",
              "masukkan_unit_fleet", "ganti_nama_unit", "cek_populasi"):
        assert t in izin


def test_spec_TIDAK_membengkak_setelah_menambah_array():
    """Array menambah deskripsi; larangan prosa yang terbukti GAGAL harus dibuang
    untuk membayarnya. Kalau angka ini jebol, prosa lamanya belum dihapus."""
    js = json.dumps(A._tool_specs(ADMIN, ""), ensure_ascii=False)
    assert len(js) <= 105_000, f"spec tool membengkak: {len(js):,} char"


def test_larangan_prosa_yang_terbukti_gagal_sudah_dibuang():
    """Ancaman 'akan DITOLAK sistem' di detail_part terbukti tak bekerja (251
    pengulangan dalam 30 hari). Menghidupkannya lagi = kembali ke pola yang
    sudah diukur gagal, sekaligus menambah token."""
    d = _spec("detail_part")["description"]
    assert "DITOLAK" not in d
    assert "berulang" not in d.lower()
