"""Pagar WAKTU per eksekusi tool di chat(): tool yang menggantung dijawab
GAGAL-CEK (bukan 'tidak ada'), giliran tetap selesai.

Kenapa ada (audit ai_chat_log 2026-08-30): tak ada batas waktu di jalur tool
sama sekali — 9 Agu satu giliran menggantung 337 dtk saat jaringan EPC putus
(4 tool err beruntun, tiap panggilan HTTP retry sendiri); `_ex.map` di batch
pun tanpa timeout. Pagar stream (5,30) hanya melindungi panggilan MODEL.

Sifat yang dijaga:
 1. Lewat batas → hasil `_err_kind: "err"` + kalimat "BELUM DICEK" (bukan nf).
 2. Thread tool aslinya tak dibunuh; hasil yang datang belakangan masuk cache
    giliran → panggilan identik di ronde berikut langsung dapat hasilnya.
 3. Tool cepat tak berubah perilakunya; exception tool tetap diangkat.
"""
import json
import threading
import time

import pytest

from app.services import ai_assistant as ai

USER = {"username": "tester", "role": "admin"}


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [{"x": 1}])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai, "_TOOL_TIMEOUT_S", 0.2)
    monkeypatch.setattr(ai, "_TOOL_TIMEOUT_BERAT_S", 0.2)


def _tc(i, name, args):
    return {"id": f"c{i}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _model(monkeypatch, rounds, final="Selesai."):
    """rounds = daftar tool_calls per ronde; setelah itu jawaban akhir."""
    seq = [{"choices": [{"message": {"content": None, "tool_calls": calls},
                         "finish_reason": "tool_calls"}]} for calls in rounds]
    seq.append({"choices": [{"message": {"content": final}, "finish_reason": "stop"}]})
    it = iter(seq)
    rec = {"messages": []}

    def fake(messages, tools, max_tokens=6000):
        rec["messages"].append([dict(m) for m in messages])
        try:
            return next(it)
        except StopIteration:
            return seq[-1]

    monkeypatch.setattr(ai, "_post_chat", fake)
    return rec


def _tool_msgs(rec, ronde):
    return [m for m in rec["messages"][ronde] if m.get("role") == "tool"]


def test_tool_menggantung_dijawab_gagal_cek(monkeypatch):
    lepas = threading.Event()

    def lambat(name, args, u, sid=""):
        lepas.wait(3)
        return {"found": True, "hasil": [{"part_number": "WG1", "stok": 3}]}

    monkeypatch.setattr(ai, "_run_tool", lambat)
    rec = _model(monkeypatch, [[_tc(0, "detail_part", {"part_number": "WG1"})]])
    t0 = time.monotonic()
    out = ai.chat(USER, [{"role": "user", "content": "stok WG1"}])
    lepas.set()
    assert time.monotonic() - t0 < 2.5                      # tak menunggu 3 dtk
    m = _tool_msgs(rec, 1)[0]["content"]
    assert "BELUM DICEK" in m and "tidak ada" in m.lower()
    # nota lookup gagal disuntik (bukan nf, bukan brake) & telemetri = err
    assert any("gagal" in (x.get("content") or "").lower()
               for x in rec["messages"][1] if x.get("role") == "user")
    assert out["reply"]


def test_hasil_terlambat_masuk_cache_giliran(monkeypatch):
    """Ronde 1: tool lewat batas. Ronde 2: model memanggil ULANG argumen sama —
    hasil yang sudah datang di latar dipakai (cache), tool tak dijalankan lagi."""
    n = {"eksekusi": 0}

    def lambat(name, args, u, sid=""):
        n["eksekusi"] += 1
        time.sleep(0.5)
        return {"found": True, "hasil": [{"part_number": "WG1", "stok": 3}]}

    monkeypatch.setattr(ai, "_run_tool", lambat)
    seq = [[_tc(0, "detail_part", {"part_number": "WG1"})],
           [_tc(1, "detail_part", {"part_number": "WG1"})]]
    orig = ai._post_chat
    rec = _model(monkeypatch, seq)
    fake = ai._post_chat

    def fake_tunggu(messages, tools, max_tokens=6000):
        # beri waktu thread tool ronde 1 selesai sebelum ronde 2 diminta
        time.sleep(0.6)
        return fake(messages, tools, max_tokens)

    monkeypatch.setattr(ai, "_post_chat", fake_tunggu)
    ai.chat(USER, [{"role": "user", "content": "stok WG1"}])
    assert n["eksekusi"] == 1                              # tak dieksekusi ulang
    m2 = _tool_msgs(rec, 2)[-1]["content"]                 # balasan tool ronde 2
    assert "WG1" in m2 and "PANGGILAN IDENTIK" in m2       # dari cache giliran


def test_tool_cepat_dan_exception_tak_berubah(monkeypatch):
    monkeypatch.setattr(ai, "_run_tool",
                        lambda name, args, u, sid="": {"found": True, "hasil": [{"part_number": "WG2"}]})
    rec = _model(monkeypatch, [[_tc(0, "detail_part", {"part_number": "WG2"})]])
    ai.chat(USER, [{"role": "user", "content": "WG2"}])
    assert "WG2" in _tool_msgs(rec, 1)[0]["content"]

    def boom(name, args, u, sid=""):
        raise RuntimeError("meledak")

    monkeypatch.setattr(ai, "_run_tool", boom)
    _model(monkeypatch, [[_tc(0, "detail_part", {"part_number": "WG3"})]])
    # _run_tool sendiri yang meledak (bukan handler di dalamnya — itu sudah
    # ditangkap _run_tool) → diangkat ke pemanggil PERSIS seperti sebelum pagar
    # waktu ada; slot plafon dikembalikan.
    with pytest.raises(RuntimeError, match="meledak"):
        ai.chat(USER, [{"role": "user", "content": "WG3"}])
