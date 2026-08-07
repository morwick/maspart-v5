"""Loop belajar sinonim otomatis (ai_sinonim_learn) + posisi/fuzzy asisten.

Semua file (usulan.json, sinonim.json, search_misses.json) dialihkan ke
tmp_path; LLM & part_index di-mock — tak ada panggilan jaringan.
"""
import pytest

from app.services import ai_assistant as ai
from app.services import ai_sinonim_learn as learn
from app.services import search_log, sinonim


# ── _parse_posisi: deterministik kanan/kiri/depan/belakang ───────────────────

def test_posisi_penanda_inggris():
    assert ai._parse_posisi("REAR VIEW MIRROR RH") == ["kanan"]
    assert ai._parse_posisi("MIRROR, LEFT") == ["kiri"]
    assert ai._parse_posisi("FRONT BRAKE DRUM") == ["depan"]
    assert ai._parse_posisi("REAR SPRING FRONT BRACKET") == ["depan", "belakang"]
    assert ai._parse_posisi("UPPER BUSHING") == ["atas"]


def test_posisi_tidak_salah_tangkap_substring():
    # RIGHT di dalam UPRIGHT / FR di dalam FRAME tidak boleh dihitung.
    assert ai._parse_posisi("UPRIGHT PANEL") == []
    assert ai._parse_posisi("FRAME ASSY") == []
    assert ai._parse_posisi("BRIGHT LAMP") == []


def test_posisi_fallback_nama_china():
    # Nama Inggris tanpa penanda → karakter China yang menentukan.
    assert ai._parse_posisi("MIRROR", "右后视镜") == ["kanan"]
    assert ai._parse_posisi(None, "左前灯") == ["kiri", "depan"]
    # Nama Inggris SUDAH memberi posisi → China tidak menimpa.
    assert ai._parse_posisi("MIRROR LH", "右后视镜") == ["kiri"]


# ── _bom_mungkin_maksud: saran fuzzy isi unit ────────────────────────────────

def test_fuzzy_saran_typo():
    parts = [{"pn": "WG1", "nama_cn": None}, {"pn": "WG2", "nama_cn": "空气滤芯"}]
    local = {"WG1": {"part_name": "REAR VIEW MIRROR RH"}}
    # 'miror' (typo) tidak match persis, tapi mirip MIRROR → disarankan.
    out = ai._bom_mungkin_maksud(parts, local, ["miror"])
    assert out and out[0]["part_number"] == "WG1"
    # Query pendek/berangka diabaikan → tidak ada saran ngawur.
    assert ai._bom_mungkin_maksud(parts, local, ["ab1"]) == []


# ── ai_sinonim_learn: generate → validasi → approve/reject ──────────────────

@pytest.fixture()
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(learn, "_file", lambda: tmp_path / "usulan.json")
    monkeypatch.setattr(sinonim, "_file", lambda: tmp_path / "sinonim.json")
    monkeypatch.setattr(search_log, "_path", lambda: tmp_path / "misses.json")
    search_log._state["data"] = None  # reset cache antar test
    return tmp_path


def test_fb_kandidat_dari_feedback_down(_env, monkeypatch):
    """P7 2026-07-23: pertanyaan 👎 pendek ala istilah = kandidat kamus; kalimat
    panjang / PN / yang sudah ter-cover kamus / duplikat miss DISARING."""
    monkeypatch.setattr(learn.ai_feedback, "list_feedback", lambda **kw: [
        {"question": "karet stabil howo?", "rating": "down", "resolved": False},
        {"question": "kenapa jawaban kamu tadi salah total soal harga part "
                     "yang saya tanyakan kemarin sore ya", "rating": "down"},   # panjang
        {"question": "WG9100443050", "rating": "down"},                          # PN
        {"question": "spion", "rating": "down"},                                 # dobel miss
    ])
    search_log.record_miss("spion", "nama", "asisten")
    out = learn._kandidat(10)
    qs = [m["query"] for m in out]
    assert "karet stabil howo" in qs                 # dipangkas '?' + lolos filter
    assert qs.count("spion") == 1                    # dedup vs miss
    fb = next(m for m in out if m["query"] == "karet stabil howo")
    assert fb["sumber"] == "feedback"
    assert "WG9100443050" not in qs
    assert not any(len(q.split()) > 6 for q in qs)


def test_fb_kandidat_feedback_error_aman(_env, monkeypatch):
    def boom(**kw):
        raise RuntimeError("supabase mati")

    monkeypatch.setattr(learn.ai_feedback, "list_feedback", boom)
    search_log.record_miss("spion", "nama", "asisten")
    assert [m["query"] for m in learn._kandidat(10)] == ["spion"]


def test_fb_kandidat_ikut_generate(_env, monkeypatch):
    """End-to-end: tanpa miss, kandidat feedback masuk pipeline usulan yang sama."""
    monkeypatch.setattr(learn.ai_feedback, "list_feedback", lambda **kw: [
        {"question": "karet stabil", "rating": "down", "resolved": False}])
    monkeypatch.setattr(learn, "_llm_usulan", lambda misses: [{
        "query": "karet stabil", "triggers": ["karet stabil"],
        "keywords": ["stabilizer bushing"], "grup": "poros",
        "confidence": 0.9, "alasan": "istilah lapangan",
    }])
    monkeypatch.setattr(learn.part_index, "search_part_name",
                        lambda k: [{"pn": "WG1"}])
    res = learn.generate(limit=10)
    assert res["dibuat"] == 1 and res["usulan"][0]["query"] == "karet stabil"


def test_generate_validasi_keyword_dan_approve(_env, monkeypatch):
    search_log.record_miss("spion", "nama", "asisten")
    search_log.record_miss("spion", "nama", "asisten")

    monkeypatch.setattr(learn, "_llm_usulan", lambda misses: [{
        "query": "spion",
        "triggers": ["spion", "kaca spion"],
        "keywords": ["mirror", "unicorn horn"],   # satu valid, satu halusinasi
        "grup": "body", "confidence": 0.95, "alasan": "istilah umum",
    }, {
        "query": "bukan-dari-daftar",             # halusinasi query → dibuang
        "triggers": ["x"], "keywords": ["mirror"], "confidence": 1,
    }])
    # Katalog hanya mengenal 'mirror'.
    monkeypatch.setattr(learn.part_index, "search_part_name",
                        lambda k: [{"pn": "WG1"}] if "mirror" in k.lower() else [])

    res = learn.generate(limit=10)
    assert res["dibuat"] == 1
    u = res["usulan"][0]
    assert u["keywords"] == ["mirror"]            # halusinasi tervalidasi keluar
    assert u["keywords_dibuang"] == ["unicorn horn"]
    assert u["status"] == "pending"

    # Approve → masuk kamus + miss terhapus.
    learn.approve("spion")
    entri = sinonim.load()
    assert entri and entri[0]["keywords"] == ["mirror"]
    assert all(m["query"] != "spion" for m in search_log.top_misses())
    assert learn.list_usulan("approved")[0]["id"] == "spion"
    assert learn.list_usulan("pending") == []


def test_generate_auto_approve_dan_reject(_env, monkeypatch):
    search_log.record_miss("laher roda", "nama", "search")
    search_log.record_miss("istilah aneh", "nama", "search")

    monkeypatch.setattr(learn, "_llm_usulan", lambda misses: [
        {"query": "laher roda", "triggers": ["laher roda"],
         "keywords": ["wheel bearing"], "grup": "poros", "confidence": 0.9},
        {"query": "istilah aneh", "triggers": ["istilah aneh"],
         "keywords": ["hub"], "grup": "umum", "confidence": 0.4},  # conf rendah
    ])
    monkeypatch.setattr(learn.part_index, "search_part_name", lambda k: [{"pn": "X"}])

    res = learn.generate(limit=10, auto_approve=True)
    assert res["dibuat"] == 2
    assert res["auto_disetujui"] == 1             # hanya yang confidence tinggi
    assert {e["triggers"][0] for e in sinonim.load()} == {"laher roda"}

    learn.reject("istilah aneh")
    assert learn.list_usulan("rejected")[0]["id"] == "istilah aneh"
    # Ditolak → tidak diusulkan ulang oleh generate berikutnya.
    monkeypatch.setattr(learn, "_llm_usulan", lambda misses: (_ for _ in ()).throw(
        AssertionError("tidak boleh dipanggil — tak ada kandidat")))
    assert learn.generate(limit=10)["dibuat"] == 0


# ── Anti-generik: trigger LLM tak boleh membajak pencarian ──────────────────

def test_trigger_generik_dibuang_dan_ditandai(_env, monkeypatch):
    """Keyword sudah lama dipagari katalog; TRIGGER dulu masuk apa adanya —
    padahal trigger yang menentukan KAPAN keyword disuntikkan ke pencarian."""
    search_log.record_miss("karet stabil", "nama", "search")
    monkeypatch.setattr(learn, "_llm_usulan", lambda misses: [{
        "query": "karet stabil",
        # 'atas' (posisi) & 'harga part' (semua katanya generik) = pembajak;
        # 'per daun' campuran → justru bentuk trigger terbaik, harus LOLOS.
        "triggers": ["karet stabil", "atas", "harga part", "per daun", "ke"],
        "keywords": ["stabilizer bushing"], "grup": "poros", "confidence": 0.95,
    }])
    monkeypatch.setattr(learn.part_index, "search_part_name", lambda k: [{"pn": "X"}])

    u = learn.generate(limit=10)["usulan"][0]
    assert u["triggers"] == ["karet stabil", "per daun"]
    assert set(u["triggers_dibuang"]) == {"atas", "harga part", "ke"}
    learn.approve("karet stabil")
    assert sinonim.load()[0]["triggers"] == ["karet stabil", "per daun"]


def test_semua_trigger_generik_ditolak_otomatis(_env, monkeypatch):
    """Tak ada trigger yang selamat → entri mustahil berguna. Disimpan sbg
    'rejected' supaya istilah itu tak dibelikan panggilan LLM lagi besok."""
    search_log.record_miss("gambar unit", "nama", "search")
    monkeypatch.setattr(learn, "_llm_usulan", lambda misses: [{
        "query": "gambar unit", "triggers": ["gambar unit", "unit"],
        "keywords": ["frame"], "grup": "umum", "confidence": 0.99}])
    monkeypatch.setattr(learn.part_index, "search_part_name", lambda k: [{"pn": "X"}])

    res = learn.generate(limit=10, auto_approve=True)
    assert res["ditolak_generik"] == 1 and res["auto_disetujui"] == 0
    assert sinonim.load() == []                       # kamus TIDAK tersentuh
    assert learn.list_usulan("pending") == []
    assert learn.list_usulan("rejected")[0]["id"] == "gambar unit"
    # Tak diusulkan ulang → LLM tak dipanggil lagi.
    monkeypatch.setattr(learn, "_llm_usulan", lambda misses: (_ for _ in ()).throw(
        AssertionError("tidak boleh dipanggil — istilahnya sudah ditolak")))
    assert learn.generate(limit=10)["dibuat"] == 0


def test_approve_gerbang_terakhir_saat_admin_mengedit_trigger(_env, monkeypatch):
    """Admin boleh mengedit triggers saat approve → saringan diulang di _apply."""
    search_log.record_miss("karet stabil", "nama", "search")
    monkeypatch.setattr(learn, "_llm_usulan", lambda misses: [{
        "query": "karet stabil", "triggers": ["karet stabil"],
        "keywords": ["stabilizer bushing"], "grup": "poros", "confidence": 0.9}])
    monkeypatch.setattr(learn.part_index, "search_part_name", lambda k: [{"pn": "X"}])
    learn.generate(limit=10)

    with pytest.raises(RuntimeError, match="terlalu umum"):
        learn.approve("karet stabil", triggers=["atas", "yang"])
    assert sinonim.load() == []
    # Trigger campuran tetap lolos; yang generik dibuang & dicatat.
    u = learn.approve("karet stabil", triggers=["karet stabil", "atas"])
    assert u["triggers"] == ["karet stabil"] and u["triggers_dibuang"] == ["atas"]
    assert sinonim.load()[0]["triggers"] == ["karet stabil"]


def test_validate_triggers_tak_menyentuh_istilah_lapangan_asli():
    """Pagar kejut: istilah pendek yang MEMANG dipakai kamus kurasi ('per',
    'aki', 'sein', 'kem') tak boleh ikut tersapu — saringan ini cuma untuk
    pipeline otomatis, bukan koreksi kamus manusia."""
    asli = ["per", "aki", "sein", "kem", "abs", "per daun", "cucuk per"]
    layak, buang = learn._validate_triggers(asli)
    assert layak == asli and buang == []


def test_validate_triggers_membiarkan_frasa_gejala_dua_kata():
    """'asap' & 'hitam' sendiri-sendiri tak menunjuk sistem apa pun, tapi 'asap
    hitam' justru bentuk trigger paling berharga — dua kata sifat digabung sudah
    membatasi diri sendiri. Hanya frasa yang SEMUANYA kata isi-kalimat yang jatuh."""
    layak, buang = learn._validate_triggers(
        ["asap hitam", "setir berat", "rem blong", "harga part", "cek stok"])
    assert layak == ["asap hitam", "setir berat", "rem blong"]
    assert buang == ["harga part", "cek stok"]


# ── Kandidat: miss yang kamusnya SUDAH punya jawabannya ─────────────────────

def test_kandidat_lewati_miss_yang_sudah_dicover_kamus(_env):
    """Saringan sinonim.hit dulu hanya ada di jalur feedback; jalur miss
    tertinggal, jadi 'kaca spion pecah' tetap dibelikan panggilan LLM padahal
    trigger 'spion' sudah ada (hasilnya cuma entri kembar)."""
    sinonim.add("body", ["spion"], ["mirror"])
    search_log.record_miss("kaca spion pecah", "nama", "search")   # ter-cover
    search_log.record_miss("spionnya buram", "nama", "search")     # ter-cover (klitika)
    search_log.record_miss("karet stabil", "nama", "search")       # celah sungguhan
    with learn._lock:
        kandidat = learn._kandidat(10)
    assert [k["query"] for k in kandidat] == ["karet stabil"]


def test_cover_kamus_tak_kena_substring(_env):
    """Pra-saring cepat tak boleh mengubah arti hit(): 'per' bukan 'persneling'."""
    sinonim.add("suspensi", ["per"], ["spring"])
    search_log.record_miss("persneling keras", "nama", "search")
    with learn._lock:
        assert [k["query"] for k in learn._kandidat(10)] == ["persneling keras"]


def test_kandidat_skip_pn_dan_yang_sudah_di_kamus(_env, monkeypatch):
    sinonim.add("body", ["spion"], ["mirror"])
    search_log.record_miss("spion", "nama", "search")        # sudah di kamus
    search_log.record_miss("VG1560080276", "pn", "search")   # bentuk PN
    search_log.record_miss("karet stabil", "nama", "search")  # kandidat sah
    with learn._lock:
        kandidat = learn._kandidat(10)
    assert [k["query"] for k in kandidat] == ["karet stabil"]
