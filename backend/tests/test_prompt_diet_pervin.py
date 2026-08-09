"""Diet prompt: tool & aturan per-VIN hanya ditawarkan saat rangka relevan.

Kenapa ada (ukuran produksi 2026-08-09, 1.117 giliran):
 • blok TETAP tiap panggilan API = 77 spec tool (24.691 tok) + system prompt
   (17.569 tok) ≈ 44.400 token, DIBAYAR SETIAP RONDE tool;
 • giliran 8 ronde nyata memakai 480.084 token masuk — mayoritas beban tetap;
 • 67% waktu giliran Agustus habis di MODEL (3.829 dtk) vs tool (1.854 dtk).
Maka memangkas prompt = memangkas latensi, bukan sekadar biaya.

⚠️ Sifat yang WAJIB dijaga tes ini:
 1. Gerbang hanya menyembunyikan MENU, bukan izin eksekusi.
 2. Aturan & alat bergerak BERSAMA (mustahil aturan per-VIN tanpa tool-nya).
 3. Deteksi rangka PEMURAH — salah-sembunyi jauh lebih mahal dari salah-tampil.
 4. System prompt tetap byte-identik (prompt-cache DeepSeek).
"""
import json

import pytest

from app.services import ai_assistant as A

U = {"username": "mas", "role": "admin"}


def _nama(specs):
    return {s["function"]["name"] for s in specs}


def _wajib_rangka(specs):
    return {s["function"]["name"] for s in specs
            if "rangka" in ((s["function"].get("parameters") or {}).get("required") or [])}


def _pesan(teks, role="user"):
    return [{"role": role, "content": teks}]


# ── deteksi konteks rangka: PEMURAH ─────────────────────────────────────────
@pytest.mark.parametrize("teks", [
    "kampas rem untuk SJ346500",                 # frame 8-char
    "LZZ1ELSF1RJ371264 carikan bushing",         # VIN 17-char
    "cek nomor rangka unit ini dong",            # kata 'rangka'
    "berapa VIN yang terdaftar?",                # kata 'vin'
    "part aus untuk truk saya",                  # 'truk saya' — belum sebut VIN
    "filter untuk unit ini",                     # 'unit ini'
    "chassis number-nya berapa",                 # 'chassis'
])
def test_konteks_rangka_terdeteksi(teks):
    assert A._konteks_rangka(_pesan(teks)) is True


@pytest.mark.parametrize("teks", [
    "harga oli hidrolik berapa?",
    "stok filter solar di Jakarta",
    "apa itu wearing part?",
    "1 tambah 1",
])
def test_tanpa_konteks_rangka(teks):
    assert A._konteks_rangka(_pesan(teks)) is False


def test_rangka_disebut_ASISTEN_saja_tetap_terdeteksi():
    """Rangka kerap hanya ada di hasil tool / jawaban asisten sebelumnya —
    membaca pesan user saja akan menyembunyikan alat di tengah percakapan."""
    h = [{"role": "user", "content": "yang belakang gimana?"},
         {"role": "assistant", "content": "Untuk unit SJ346500 part belakangnya…"}]
    assert A._konteks_rangka(h) is True


def test_history_none_tidak_menggerbangi_apa_pun():
    """Default lama harus utuh — pemanggil yang tak tahu gerbang ini (mis.
    _allowed_tool_names) tak boleh kehilangan tool apa pun."""
    assert A._konteks_rangka(None) is True
    assert _wajib_rangka(A._saring_pervin(A._tool_specs(U), None))


# ── gerbang spec ────────────────────────────────────────────────────────────
def test_tool_wajib_rangka_disembunyikan_tanpa_konteks():
    penuh = A._saring_pervin(A._tool_specs(U), _pesan("kampas rem SJ346500"))
    kurus = A._saring_pervin(A._tool_specs(U), _pesan("harga oli hidrolik"))
    hilang = _nama(penuh) - _nama(kurus)
    assert hilang == _wajib_rangka(penuh), "yang hilang harus PERSIS tool wajib-rangka"
    assert hilang, "gerbang tak melakukan apa-apa — spec berubah?"
    # tool inti TIDAK boleh ikut hilang
    for inti in ("cari_part", "detail_part", "cek_massal_part", "pengganti_part"):
        assert inti in _nama(kurus)


def test_tak_ada_tool_TANPA_rangka_yang_ikut_hilang():
    kurus = A._saring_pervin(A._tool_specs(U), _pesan("harga oli"))
    for s in kurus:
        req = (s["function"].get("parameters") or {}).get("required") or []
        assert "rangka" not in req


# ── ⛔ gerbang menu ≠ gerbang izin ───────────────────────────────────────────
def test_eksekusi_tool_per_vin_TETAP_diizinkan_tanpa_konteks():
    """Sifat paling penting: model bisa saja memanggil tool per-VIN dari ingatan
    giliran sebelumnya. Menyembunyikannya dari menu boleh; MENOLAKNYA tidak."""
    izin = A._allowed_tool_names(U)
    for t in ("cek_kendaraan", "bom_dari_rangka", "part_aus_dari_rangka",
              "cari_part_di_unit", "filter_unit", "uraikan_assembly"):
        assert t in izin, f"{t} harus tetap BOLEH dieksekusi"


# ── aturan & alat bergerak bersama ──────────────────────────────────────────
def test_blok_pervin_ikut_gerbang_yang_sama():
    assert A._pervin_block(_pesan("harga oli")) == ""
    blok = A._pervin_block(_pesan("kampas rem SJ346500"))
    assert "part_aus_dari_rangka" in blok
    assert "ROUTING TOOL EPC PER-VIN" in blok or "ROUTING" in blok


def test_aturan_pervin_tak_lagi_di_prompt_statik():
    """Kalau aturan ini bocor balik ke prompt statik, dietnya batal diam-diam.

    ⚠️ Yang diuji hanya blok ROUTING yang dipindah. Nama tool per-VIN masih SAH
    muncul di prompt statik lewat aturan EPC-FIRST — aturan itu justru mengurus
    kasus TANPA rangka ('sebut part+model tanpa VIN → minta VIN dulu'), jadi ia
    memang harus selalu ada.
    """
    sp = A._system_prompt(U)
    for penanda in ("ROUTING TOOL EPC PER-VIN", "ATURAN KERAS",
                    "BANDING DUA RANGKA", "KATALOG BERGAMBAR",
                    "FILTER MENYELURUH"):
        assert penanda not in sp, f"'{penanda}' bocor balik ke prompt statik"


def test_isi_yang_dipindah_utuh_di_blok_dinamis():
    """Dipindah ≠ hilang: tiap penanda harus muncul di blok per-VIN."""
    blok = A._pervin_block(_pesan("kampas rem SJ346500"))
    for penanda in ("ROUTING TOOL EPC PER-VIN", "ATURAN KERAS",
                    "BANDING DUA RANGKA", "KATALOG BERGAMBAR",
                    "FILTER MENYELURUH", "part_aus_dari_rangka",
                    "uraikan_assembly", "filter_unit"):
        assert penanda in blok, f"'{penanda}' hilang saat pemindahan"


def test_aturan_TANPA_rangka_tetap_di_prompt_statik():
    """Justru saat rangka TIDAK ada, model wajib tahu harus MEMINTA rangka —
    aturan itu haram ikut pindah ke blok yang cuma muncul saat rangka ada."""
    sp = A._system_prompt(U)
    assert "AKURASI PER-UNIT" in sp
    assert "perkiraan per-model" in sp


# ── prompt-cache: prefix statik harus byte-identik ──────────────────────────
def test_system_prompt_byte_identik_antar_panggilan():
    a = A._system_prompt(U)
    b = A._system_prompt({"username": "lain", "role": "admin"})
    assert a == b, "system prompt harus bebas identitas & stabil (prompt-cache)"


# ── pagar agar prompt tak menggemuk diam-diam lagi ──────────────────────────
def test_plafon_ukuran_prompt():
    """Pagar regresi. Angka diambil dari ukuran SESUDAH diet + kelonggaran ~8%;
    menaikkannya harus keputusan sadar, bukan efek samping menambah tool."""
    sp = A._system_prompt(U)
    specs = A._saring_pervin(A._tool_specs(U), _pesan("kampas rem SJ346500"))
    js = json.dumps(specs, ensure_ascii=False)
    assert len(sp) <= 60_000, f"system prompt membengkak: {len(sp):,} char"
    assert len(js) <= 105_000, f"spec tool membengkak: {len(js):,} char"


def test_diet_benar_benar_menghemat():
    def tok(h):
        return (len(A._system_prompt(U))
                + len(json.dumps(A._saring_pervin(A._tool_specs(U), h), ensure_ascii=False))
                + len(A._pervin_block(h))) / 3.5
    hemat = tok(_pesan("kampas rem SJ346500")) - tok(_pesan("harga oli hidrolik"))
    assert hemat > 4_000, f"hemat cuma {hemat:,.0f} token — gerbang tak efektif"
