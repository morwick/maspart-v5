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


def test_kandidat_skip_pn_dan_yang_sudah_di_kamus(_env, monkeypatch):
    sinonim.add("body", ["spion"], ["mirror"])
    search_log.record_miss("spion", "nama", "search")        # sudah di kamus
    search_log.record_miss("VG1560080276", "pn", "search")   # bentuk PN
    search_log.record_miss("karet stabil", "nama", "search")  # kandidat sah
    with learn._lock:
        kandidat = learn._kandidat(10)
    assert [k["query"] for k in kandidat] == ["karet stabil"]
