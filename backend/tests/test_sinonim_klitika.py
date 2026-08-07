"""Klitika Indonesia ('-nya', '-ku', '-mu') di sinonim.hit().

Kenapa ini penting jauh melampaui satu regex: `hit()` adalah satu-satunya
pencocok yang dipakai TIGA kanal sekaligus — Kamus Istilah (expand_query,
umbrella_keywords, subset prompt), Rute Maksud (maksud.cocok/block), dan penjaga
istilah deterministik (_paksa_istilah_kamus). Sebelum tambalan ini, ketiganya
MATI SEREMPAK pada bentuk paling alami di bengkel: user menyebut part sekali,
lalu seterusnya "filternya", "gambar teknisnya", "cucuk pernya".

Yang tetap dijaga: pagar KATA UTUH ('per' ⊄ 'persneling') dan trigger PENDEK
tak boleh berklitika ('ha'+'nya' = 'hanya' bukan istilah lapangan).
"""
from app.services import ai_assistant as ai
from app.services import maksud, sinonim

_ENTRIES = [
    {"grup": "filter", "triggers": ["filter oli"], "keywords": ["oil filter"]},
    {"grup": "suspensi", "triggers": ["cucuk per"], "keywords": ["spring pin"]},
]


# ── hit(): klitika diterima, pagar kata utuh tetap ───────────────────
def test_klitika_nya_ku_mu_dikenali():
    assert sinonim.hit("gambar teknis", "gambar teknisnya mana?")
    assert sinonim.hit("filter oli", "filter olinya sudah diganti belum")
    assert sinonim.hit("cucuk per", "cucuk pernya berapa harganya")
    assert sinonim.hit("gardan", "gardanku bunyi")
    assert sinonim.hit("kabin", "kabinmu bocor")


def test_tanpa_klitika_tetap_cocok():
    assert sinonim.hit("filter oli", "cek filter oli unit ini")
    assert sinonim.hit("per", "ganti per depan")


def test_bukan_substring_di_tengah_kata():
    """Pagar lama TIDAK boleh longgar gara-gara klitika."""
    assert not sinonim.hit("per", "persneling keras")
    assert not sinonim.hit("per", "pernyataan garansi")   # 'per'+'nya'+huruf lain
    assert not sinonim.hit("gambar teknis", "gambarkan teknisnya dong")
    assert not sinonim.hit("filter oli", "filter olinya2")


def test_trigger_pendek_tak_berklitika():
    """'hanya'/'punya'/'kamu'/'buku' = kata biasa, bukan istilah + klitika.
    Ambang 3 huruf yang menjaganya; trigger 3 huruf tetap dilayani."""
    assert not sinonim.hit("ha", "hanya itu saja")
    assert not sinonim.hit("pu", "punya unit berapa")
    assert not sinonim.hit("ka", "kamu cek dulu")
    assert not sinonim.hit("bu", "bukunya mana")
    assert sinonim.hit("per", "pernya patah")           # 3 huruf → boleh


def test_trigger_kosong_tak_cocok_apa_pun():
    assert not sinonim.hit("", "apa saja")
    assert not sinonim.hit(None, "apa saja")


# ── kanal 1: ekspansi kata kunci pencarian ───────────────────────────
def test_expand_query_ikut_sembuh():
    terms, matched = sinonim.expand_query("filter olinya habis?", lambda: _ENTRIES)
    assert matched == ["filter oli"]
    assert "oil filter" in terms


def test_umbrella_kategori_ikut_sembuh():
    kws = sinonim.umbrella_keywords("filternya apa saja", lambda: _ENTRIES)
    assert "oil filter" in kws


# ── kanal 2: Rute Maksud (frasa → tool) ──────────────────────────────
def test_rute_maksud_ikut_sembuh():
    ent = [{"frasa": ["gambar teknis"], "tool": "gambar_exploded", "catatan": ""}]
    assert maksud.block("gambar teknisnya mana?", lambda: ent)
    # ⛔ tetap tak cocok utk kata lain yang kebetulan berawalan sama
    assert maksud.block("gambarkan teknisnya", lambda: ent) == ""


# ── kanal 3: penjaga istilah deterministik ───────────────────────────
def test_penjaga_istilah_ikut_sembuh(monkeypatch):
    """Follow-up 'cucuk pernya' — dulu penjaga buta total (trigger tak cocok),
    jadi karangan model diteruskan apa adanya ke handler."""
    monkeypatch.setattr(ai, "_load_sinonim_entries", lambda: _ENTRIES)
    tangkap = {}

    def fake(args, user):
        tangkap["args"] = dict(args)
        return {"found": True}

    monkeypatch.setitem(ai._DISPATCH, "cari_part_di_unit", fake)
    r = ai._run_tool("cari_part_di_unit",
                     {"rangka": "RT110061", "kata_kunci": "cross joint",
                      "_q_user": "cucuk pernya ada stok?"},
                     {"username": "admin", "role": "admin"})
    assert tangkap["args"]["kata_kunci"] == "cucuk per"
    assert "spring pin" in r["catatan_istilah"]
