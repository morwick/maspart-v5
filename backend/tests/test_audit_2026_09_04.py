"""Audit asisten 2026-09-04 (442 giliran ai_chat_log, 21 hari) → 5 perbaikan:

1. Guard PN: token 'ANGKA-KATA' dari NAMA part ('4-CIRCUIT') bukan PN — 7/7
   jawaban 'sanitized' di log ternyata jawaban BENAR yang dirusak.
2. Nasihat ronde ke-4 (_ronde_note): giliran 7–8 ronde (97–219 dtk) semuanya
   pola "hasil ada tapi tak memuat kata yang diharapkan → tebak istilah lain".
3. Giliran yang MELEDAK tercatat outcome='error' (dulu lenyap dari ai_chat_log).
4. Jurnal crash tool persisten (data/logs/ai_tool_crash.jsonl): detail_part:err
   11× tak bisa direproduksi & traceback-nya hilang bersama container.
5. detail_part: langkah PENGAYAAN yang meledak tak menjatuhkan inti jawaban.

DeepSeek & tool DI-MOCK (tanpa jaringan).
"""
import json

import pytest

from app.services import ai_assistant as ai

USER = {"username": "tester", "role": "user"}
ADMIN = {"username": "t", "role": "admin"}


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai, "_prefetch_epc_rangka", lambda h: None)


# ── 1. guard PN vs potongan nama part ────────────────────────────────────────
def test_token_angka_kata_bukan_pn():
    assert ai._token_angka_kata("4-CIRCUIT")          # kasus nyata WG9000360417
    assert ai._token_angka_kata("2-PIECE")
    # PN nyata tetap dicurigai bila tak ter-ground
    assert not ai._token_angka_kata("AZ9998887776-LH")
    assert not ai._token_angka_kata("712W35701-0129")
    assert not ai._token_angka_kata("SPHG0000000024")
    assert not ai._token_angka_kata("2BBYLD-M5X6")    # huruf panjang, tapi ada digit di dalamnya
    assert ai._drop_unit_tokens(["4-CIRCUIT", "AZ9998887776"]) == ["AZ9998887776"]


def test_nama_part_angka_kata_tak_disamarkan(monkeypatch):
    """Model menulis nama katalog '4-circuit' (dump memuat '4 circuit' — tak
    byte-identik) → dulu disamarkan + peringatan di atas jawaban."""
    tool_result = {"found": True, "part_number": "WG9000360417",
                   "part_name": "C - APU (Silver pot / 4 circuit / 10-8.5)"}
    responses = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "t1", "function": {"name": "detail_part",
                                      "arguments": json.dumps({"part_number": "WG9000360417"})}}]},
            "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content":
            "**WG9000360417 — C - APU (Silver pot / 4-circuit / 10-8.5)** — unit pemroses udara."},
            "finish_reason": "stop"}]},
    ]
    calls = {"n": 0}

    def fake(messages, tools, max_tokens=6000):
        r = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return r

    monkeypatch.setattr(ai, "_post_chat", fake)
    monkeypatch.setattr(ai, "_run_tool", lambda n, a, u, sheet_id="": tool_result)
    out = ai.chat(USER, [{"role": "user", "content": "WG9000360417"}])
    assert calls["n"] == 2                       # tanpa retry koreksi
    assert "4-circuit" in out["reply"]
    assert "tak terverifikasi" not in out["reply"]


# ── 2. nasihat ronde ke-4 ────────────────────────────────────────────────────
def test_ronde_note_isi():
    n = ai._ronde_note(4, 8)
    assert "ronde tool ke-4" in n and "maksimal 8" in n
    assert "JANGAN terus menebak" in n and "nomor rangka" in n


def test_ronde_note_disuntik_sekali_setelah_ronde_ke_4(monkeypatch):
    """6 ronde tool berturut-turut: nota muncul di panggilan model ke-5 (setelah
    ronde ke-4 dieksekusi), TEPAT satu pesan, dan tidak menumpuk di ronde berikut."""
    terlihat: list[int] = []
    calls = {"n": 0}

    def fake(messages, tools, max_tokens=6000):
        calls["n"] += 1
        terlihat.append(sum(1 for m in messages
                            if m.get("role") == "user"
                            and "ronde tool ke-" in str(m.get("content") or "")))
        if calls["n"] <= 6:
            return {"choices": [{"message": {"content": "", "tool_calls": [
                {"id": f"t{calls['n']}",
                 "function": {"name": "cari_part",
                              "arguments": json.dumps({"query": f"kata{calls['n']}"})}}]},
                "finish_reason": "tool_calls"}]}
        return {"choices": [{"message": {"content":
            "Belum ketemu dengan istilah itu; sebutkan posisinya (depan/belakang) ya."},
            "finish_reason": "stop"}]}

    monkeypatch.setattr(ai, "_post_chat", fake)
    monkeypatch.setattr(ai, "_run_tool",
                        lambda n, a, u, sheet_id="": {"found": True, "parts": []})
    out = ai.chat(USER, [{"role": "user", "content": "bushing baja per depan"}])
    assert "sebutkan posisinya" in out["reply"]
    assert terlihat == [0, 0, 0, 0, 1, 1, 1], terlihat
    assert ai._RONDE_NUDGE == 4


# ── 3. giliran meledak → outcome 'error' ─────────────────────────────────────
def test_giliran_meledak_tercatat_outcome_error(monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(ai.ai_chat_log, "log_turn_async", lambda **kw: rows.append(kw))

    def meledak(*a, **k):
        raise RuntimeError("bum")

    monkeypatch.setattr(ai, "_chat_inti", meledak)
    with pytest.raises(RuntimeError):
        ai.chat(USER, [{"role": "user", "content": "brake crank shaft RT115383"}],
                conversation_id="c1")
    assert len(rows) == 1
    r = rows[0]
    assert r["outcome"] == "error" and r["session_id"] == "c1"
    assert r["username"] == "tester" and r["question"].startswith("brake crank")
    assert "RuntimeError: bum" in r["reply"] and r["tool_failed"] is True


def test_ai_not_configured_diangkat_tanpa_log(monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(ai.ai_chat_log, "log_turn_async", lambda **kw: rows.append(kw))

    def kosong(*a, **k):
        raise ai.AINotConfigured("kunci kosong")

    monkeypatch.setattr(ai, "_chat_inti", kosong)
    with pytest.raises(ai.AINotConfigured):
        ai.chat(USER, [{"role": "user", "content": "halo"}])
    assert rows == []


# ── 4. jurnal crash tool ─────────────────────────────────────────────────────
def test_crash_tool_masuk_jurnal(monkeypatch, tmp_path):
    jurnal = tmp_path / "logs" / "ai_tool_crash.jsonl"
    monkeypatch.setattr(ai, "_crash_jurnal_path", lambda: jurnal)

    def rusak(args, user):
        raise KeyError("unit")

    monkeypatch.setitem(ai._DISPATCH, "tool_uji_rusak", rusak)
    monkeypatch.setattr(ai, "_allowed_tool_names",
                        lambda user, sheet_id="": {"tool_uji_rusak"})
    res = ai._run_tool("tool_uji_rusak", {"part_number": "AZ9998887776"}, USER)
    assert "gangguan internal" in res["error"]
    row = json.loads(jurnal.read_text(encoding="utf-8").splitlines()[-1])
    assert row["tool"] == "tool_uji_rusak"
    assert row["exc"].startswith("KeyError")
    assert "AZ9998887776" in row["args"]
    assert "Traceback" in row["tb"] and "rusak" in row["tb"]


def test_jurnal_crash_dipangkas_saat_melewati_plafon(monkeypatch, tmp_path):
    jurnal = tmp_path / "logs" / "ai_tool_crash.jsonl"
    jurnal.parent.mkdir(parents=True)
    jurnal.write_text("".join(f'{{"n":{i}}}\n' for i in range(60000)), encoding="utf-8")
    besar = jurnal.stat().st_size
    assert besar > ai._CRASH_JURNAL_MAKS_BYTE
    monkeypatch.setattr(ai, "_crash_jurnal_path", lambda: jurnal)
    ai._catat_crash_tool("x", {"pn": "1"}, ValueError("v"))
    baris = jurnal.read_text(encoding="utf-8").splitlines()
    assert jurnal.stat().st_size < besar
    assert all(b.startswith("{") for b in baris)        # potongan baris tak tersisa
    assert json.loads(baris[-1])["tool"] == "x"


# ── 5. detail_part: pengayaan meledak ≠ tool gagal ──────────────────────────
def test_detail_part_pengayaan_meledak_tak_menjatuhkan(monkeypatch):
    monkeypatch.setattr(ai.part_index, "search_part_number", lambda pn: [{
        "part_number": "AZ9998887776", "part_name": "U-bolt", "stok": "5",
        "gudang": {"01.Jakarta": 5}, "harga": "Rp 100.000", "file": "NX400 6X4"}])
    monkeypatch.setattr(ai.accurate, "available", lambda: False)
    monkeypatch.setattr(ai.sims, "get_part_spec", lambda pn: {})
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {})
    dicatat: list[str] = []
    monkeypatch.setattr(ai, "_catat_crash_tool",
                        lambda name, args, exc: dicatat.append(name))

    def meledak(**kw):
        raise RuntimeError("indeks tautan rusak")

    monkeypatch.setattr(ai.knowledge_links, "entitas", meledak)
    res = ai._t_detail_part({"part_number": "AZ9998887776"}, ADMIN)
    assert res["found"] is True and res["part_number"] == "AZ9998887776"
    assert res["part_name"] == "U-bolt"
    assert dicatat == ["detail_part/tautan pengetahuan"]
    assert not ai._tool_fail_kind(res)
