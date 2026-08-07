"""Cakupan penjaga istilah lapangan: TABEL tool (_TOOLS_ISTILAH) & definisi PN.

Guard-nya sendiri (p7 _paksa_istilah_kamus) sudah terbukti sejak 2026-07-22 —
yang bocor adalah dua hal di pinggirnya:

  1. TABEL tool. Hanya 4 tool yang dilindungi, padahal jalur pencarian per-VIN &
     per-armada yang lain (cek_massal_part_rangka, bom_dari_rangka, excel_*)
     sama-sama menerima "kata kunci" karangan model dan sama-sama meneruskannya
     lewat _expand_query. Di tool yang tak terdaftar, 'cucuk per' tetap bisa
     berubah jadi 'cross joint' tanpa seorang pun tahu.

  2. Definisi PN. Bail-out lama menyerah begitu kata kunci memuat SATU angka —
     dan kalimat lapangan hampir selalu memuat angka (kode unit 'NX400', gerak
     '6X4', ukuran '24 mm'). Jadi pagar mati justru di kalimat yang paling butuh.
     Kini pakai definisi PN yang sama dengan guard anti-halusinasi (_extract_pns).
"""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
Q = "cek cucuk per unit NX400 dong"

_ENTRIES = [
    {"grup": "suspensi", "triggers": ["cucuk per"], "keywords": ["spring pin"]},
    {"grup": "per", "triggers": ["per"], "keywords": ["leaf spring"]},
]


@pytest.fixture
def dunia(monkeypatch):
    monkeypatch.setattr(ai, "_load_sinonim_entries", lambda: _ENTRIES)
    tangkap: dict = {}

    def _pasang(nama: str):
        def fake(args, user):
            tangkap["args"] = dict(args)
            tangkap["nama"] = nama
            return {"found": True}
        monkeypatch.setitem(ai._DISPATCH, nama, fake)

    for n in ("cek_massal_part_rangka", "cek_massal_part_mesin",
              "banding_part_armada", "bom_dari_rangka", "excel_bom_rangka",
              "excel_stok_gudang", "cari_part_di_unit"):
        _pasang(n)
    return tangkap


# ── (1) tabel tool: field yang benar ikut dijaga ─────────────────────
@pytest.mark.parametrize("tool,field,args", [
    ("cek_massal_part_rangka", "part", {"daftar_rangka": ["RT110061"]}),
    ("cek_massal_part_mesin", "part", {"daftar_no_mesin": ["4P24B000713"]}),
    ("banding_part_armada", "part", {"customer": "PT ABC"}),
    ("bom_dari_rangka", "kata_kunci", {"rangka": "RT110061"}),
    ("excel_bom_rangka", "kata_kunci", {"rangka": "RT110061"}),
    ("excel_stok_gudang", "kata_kunci", {"gudang": "Jakarta"}),
])
def test_karangan_model_ditimpa_di_tool_baru(dunia, tool, field, args):
    r = ai._run_tool(tool, {**args, field: "cross joint", "_q_user": Q}, ADMIN)
    assert dunia["nama"] == tool
    assert dunia["args"][field] == "cucuk per"
    assert "spring pin" in r["catatan_istilah"]


def test_field_yang_dijaga_sesuai_nama_argumen_spec():
    """Tabel penjaga TIDAK boleh menyebut argumen yang tak ada di spec —
    kalau salah nama, guard-nya diam-diam tak pernah menyala."""
    specs = {s["function"]["name"]: s["function"] for s in ai._tool_specs(ADMIN)}
    for tool, fields in ai._TOOLS_ISTILAH.items():
        if tool not in specs:            # tool bergerbang peran → dilewati
            continue
        props = set((specs[tool].get("parameters") or {}).get("properties") or {})
        assert props & set(fields), f"{tool}: {fields} tak ada di spec {props}"


def test_tool_selaras_kamus_tetap_tak_disentuh(dunia):
    r = ai._run_tool("bom_dari_rangka",
                     {"rangka": "RT110061", "kata_kunci": "spring pin", "_q_user": Q},
                     ADMIN)
    assert dunia["args"]["kata_kunci"] == "spring pin"
    assert "catatan_istilah" not in r


# ── (2) bail-out PN: angka ≠ PN ──────────────────────────────────────
def test_kode_unit_ber_angka_tak_lagi_mematikan_penjaga(dunia):
    """'cross joint NX400' — dulu lolos bebas hanya karena ada digit."""
    r = ai._run_tool("bom_dari_rangka",
                     {"rangka": "RT110061", "kata_kunci": "cross joint NX400",
                      "_q_user": Q}, ADMIN)
    assert dunia["args"]["kata_kunci"] == "cucuk per"
    assert "MENGGANTINYA" in r["catatan_istilah"]


@pytest.mark.parametrize("kata", ["cross joint 6X4", "seal 24 mm", "filter 2 buah"])
def test_kata_lapangan_ber_angka_tetap_dijaga(dunia, kata):
    ai._run_tool("bom_dari_rangka",
                 {"rangka": "RT110061", "kata_kunci": kata, "_q_user": Q}, ADMIN)
    assert dunia["args"]["kata_kunci"] == "cucuk per"


@pytest.mark.parametrize("pn", ["ZZ9100520065", "ZZ9100520065 ZZ9100520066",
                                "612630010054"])
def test_pn_sungguhan_tetap_dibiarkan(dunia, pn):
    """PN alfanumerik (≥7 char) & PN numerik Weichai (≥9 digit) = permintaan
    eksplisit, bukan terjemahan karangan — jangan diganggu."""
    ai._run_tool("bom_dari_rangka",
                 {"rangka": "RT110061", "kata_kunci": pn, "_q_user": Q}, ADMIN)
    assert dunia["args"]["kata_kunci"] == pn


def test_array_kata_kunci_ditambahi_bukan_ditimpa(dunia):
    """Perilaku lama utk nilai ARRAY dipertahankan (istilah lain bisa sah)."""
    ai._run_tool("cari_part_di_unit",
                 {"rangka": "RT110061", "kata_kunci": ["cross joint"], "_q_user": Q},
                 ADMIN)
    assert dunia["args"]["kata_kunci"] == ["cross joint", "cucuk per"]
