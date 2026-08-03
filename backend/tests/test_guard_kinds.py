"""Telemetri guard: SEBAB mana yang menyala, bukan cuma boolean. DeepSeek di-mock.

Audit 847 giliran (15-31 Jul 2026) menemukan dua lubang:
 1. `guard_hit` hanya dinaikkan blok guard anti-karangan → tiga guard lain
    (klaim-Excel, DTC-FIRST, EPC-FIRST) tak terekam SAMA SEKALI. Padahal
    DTC-FIRST yang terbukti paling berharga: 14 jawaban kode kesalahan tanpa
    tool pada 16 Juli (uji silang: 5 dari 7 SALAH komponen), lalu nol kambuh.
 2. Yang terekam pun menggabung `pn` / `angka` (dugaan karangan) dengan
    `subst` (PN per-model menyalip EPC per-VIN — kerap false positive).
"""
import pytest

from app.services import ai_assistant as ai

USER = {"username": "tester", "role": "user"}


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())


@pytest.fixture
def tercatat(monkeypatch):
    """Tangkap argumen log_turn giliran terakhir."""
    box: dict = {}

    def fake(**kw):
        box.clear()
        box.update(kw)
        return True

    monkeypatch.setattr(ai.ai_chat_log, "log_turn_async", fake)
    return box


def _stub(monkeypatch, urutan):
    seq = list(urutan)
    n = {"i": 0}

    def fake(messages, tools, max_tokens=6000):
        m = seq[min(n["i"], len(seq) - 1)]
        n["i"] += 1
        return {"choices": [{"message": {"content": m}, "finish_reason": "stop"}]}

    monkeypatch.setattr(ai, "_post_chat", fake)
    return n


def test_guard_pn_karangan_tercatat_sebagai_pn(monkeypatch, tercatat):
    _stub(monkeypatch, ["Part yang cocok: AZ9998887776."])
    ai.chat(USER, [{"role": "user", "content": "cari tierod dong"}])
    assert "pn" in tercatat["guard_kinds"]
    assert tercatat["guard_hit"] is True


def test_guard_angka_karangan_tercatat_sebagai_angka(monkeypatch, tercatat):
    _stub(monkeypatch, ["Stok WG2210040097 saat ini 5 pcs."])
    ai.chat(USER, [{"role": "user", "content": "stok WG2210040097 berapa?"}])
    assert "angka" in tercatat["guard_kinds"]


def test_guard_dtc_first_kini_TERCATAT(monkeypatch, tercatat):
    """Inti perbaikan: guard yang menyetop jawaban kode kesalahan SALAH dulu
    sama sekali tak terlihat di telemetri — `guard_hit` tetap False."""
    # Model menjawab kode error tanpa pernah memanggil cari_kode_kesalahan.
    _stub(monkeypatch, ["SPN 110 FMI 17 artinya sensor suhu coolant."])
    ai.chat(USER, [{"role": "user", "content": "spn 110 fmi 17"}])
    assert "dtc" in tercatat["guard_kinds"]
    assert tercatat["guard_hit"] is True          # DULU False — itu bugnya


def test_giliran_bersih_tak_mencatat_guard(monkeypatch, tercatat):
    _stub(monkeypatch, ["Baik, ada yang bisa saya bantu soal part?"])
    ai.chat(USER, [{"role": "user", "content": "halo"}])
    assert tercatat["guard_kinds"] == []
    assert tercatat["guard_hit"] is False


def test_sebab_tak_duplikat_walau_guard_menyala_berkali(monkeypatch, tercatat):
    # Model membandel: PN karangan bertahan melewati semua retry.
    _stub(monkeypatch, ["Pakai AZ9998887776 ya."])
    ai.chat(USER, [{"role": "user", "content": "cari tierod"}])
    assert tercatat["guard_kinds"].count("pn") == 1


def test_ringkasan_admin_menghitung_sebab_guard(monkeypatch):
    """summary() memaparkan guard_sebab agar panel bisa memisahkan
    'karangan' dari 'substitusi' tanpa menebak."""
    from app.services import ai_chat_log
    monkeypatch.setattr(ai_chat_log, "list_logs", lambda limit=1000: [
        {"latency_ms": 10, "guard_hit": True, "guard_kinds": "dtc", "outcome": "ok"},
        {"latency_ms": 20, "guard_hit": True, "guard_kinds": "pn, angka", "outcome": "ok"},
        {"latency_ms": 30, "guard_hit": False, "guard_kinds": None, "outcome": "ok"},
    ])
    s = ai_chat_log.summary()
    assert s["guard_sebab"] == {"dtc": 1, "pn": 1, "angka": 1}
    assert s["guard_menyala"] == 2
