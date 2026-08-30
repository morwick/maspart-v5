"""KOALISI: N panggilan SKALAR paralel satu tool → SATU panggilan ber-array
(server-side), bukan ditolak rem.

Kenapa ada (audit ai_chat_log 2026-08-30, 2 hari pasca-deploy 28 Agu): user
'mas' kirim 133 PN → model memanggil harga_sims 115× satuan dalam SATU giliran
walau spec-nya sudah mendeklarasikan `part_number: ["string","array"]` + larangan
⛔ per-PN. Rem menolak 105 sisanya → 11 giliran "lanjut" ×10 PN, 2 Excel parsial,
±1,9 jt token, 40 menit. Larangan (prosa maupun skema) tak menjamin kepatuhan;
menolak hanya memindahkan kerja ke user. Jadi server MENGGABUNGKAN.

Sifat yang WAJIB dijaga:
 1. Hanya panggilan yang berbeda pada SATU argumen ber-array (skalar tiap
    panggilan) dan argumen lain identik yang dilebur — selain itu perilaku LAMA.
 2. Tool yang array-nya data terstruktur (buat_excel, tanya_user, …) tak dilebur.
 3. Tiap tool_call_id tetap mendapat balasan (API mewajibkannya): yang dilebur
    dapat stub pendek yang menunjuk hasil wakil & menegaskan SUDAH dicek.
 4. Rem TIDAK menyala untuk grup yang dilebur; telemetri mencatat 1 eksekusi.
"""
import json

import pytest

from app.services import ai_assistant as ai

USER = {"username": "tester", "role": "admin"}


def _spec(name, props):
    return {"type": "function", "function": {
        "name": name, "description": "uji",
        "parameters": {"type": "object", "properties": props}}}


SPECS = [
    _spec("harga_sims", {"part_number": {"type": ["string", "array"], "items": {"type": "string"}},
                         "konversi_idr": {"type": "boolean"}}),
    _spec("cek_massal_part", {"daftar_pn": {"type": "array", "items": {"type": "string"}}}),
    _spec("info_part", {"pn": {"type": "string"}}),                      # TANPA array
    _spec("buat_excel", {"kolom": {"type": "array", "items": {"type": "string"}},
                         "baris": {"type": "array", "items": {"type": "array"}}}),
    _spec("tanya_user", {"pertanyaan": {"type": "array", "items": {"type": "object"}}}),
]


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": SPECS)
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())


def _tc(i, name, args):
    return {"id": f"c{i}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _model(monkeypatch, calls, final="Selesai."):
    """Ronde 1 = tool_calls, ronde 2 = jawaban akhir; simpan messages ronde 2."""
    seq = [
        {"choices": [{"message": {"content": None, "tool_calls": calls},
                      "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": final}, "finish_reason": "stop"}]},
    ]
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


def _spy_tool(monkeypatch):
    log = []

    def run(name, args, u, sid=""):
        log.append((name, {k: v for k, v in args.items() if not k.startswith("_")}))
        pns = args.get("part_number") or args.get("daftar_pn") or args.get("pn") or ""
        pns = pns if isinstance(pns, list) else [pns]
        return {"found": True, "hasil": [{"part_number": p, "harga_cny": 1.5} for p in pns]}

    monkeypatch.setattr(ai, "_run_tool", run)
    return log


def _pesan_tool(rec):
    return [m for m in rec["messages"][1] if m.get("role") == "tool"]


# ── Unit: registri dari spec ────────────────────────────────────────────────

def test_registri_diturunkan_dari_spec():
    m = ai._param_array_map(SPECS)
    assert m["harga_sims"] == {"part_number"}
    assert m["cek_massal_part"] == {"daftar_pn"}
    assert "info_part" not in m                    # tak punya argumen array
    assert "buat_excel" not in m and "tanya_user" not in m   # _KOALISI_TOLAK


def test_registri_toleran_spec_cacat():
    assert ai._param_array_map([{"x": 1}, None, {"function": {"name": "a"}}]) == {}
    assert ai._param_array_map(None) == {}


# ── Unit: aturan peleburan ──────────────────────────────────────────────────

def test_koalisi_hanya_satu_argumen_berbeda():
    arr = {"harga_sims": {"part_number"}}
    parsed = [("harga_sims", {"part_number": "A1"}),
              ("harga_sims", {"part_number": "A2"}),
              ("harga_sims", {"part_number": "A3", "konversi_idr": True}),   # argumen lain beda
              ("harga_sims", {"part_number": ["A4"]}),                       # list 1 elemen = skalar
              ("harga_sims", {"part_number": ["A5", "A6"]}),                 # sudah array → biarkan
              ("info_part", {"pn": "A7"})]
    baru, dilebur = ai._koalisi_panggilan(parsed, arr)
    assert baru[0] == ("harga_sims", {"part_number": ["A1", "A2", "A4"]})
    assert set(dilebur) == {1, 3}
    assert dilebur[1]["wakil"] == 0 and dilebur[1]["item"] == "A2" and dilebur[1]["jumlah"] == 3
    assert baru[2] == parsed[2] and baru[4] == parsed[4] and baru[5] == parsed[5]


def test_koalisi_tanpa_registri_atau_tunggal_tak_menyentuh():
    parsed = [("harga_sims", {"part_number": "A1"}), ("harga_sims", {"part_number": "A2"})]
    assert ai._koalisi_panggilan(parsed, {}) == (parsed, {})
    assert ai._koalisi_panggilan(parsed[:1], {"harga_sims": {"part_number"}}) == (parsed[:1], {})


# ── End-to-end di chat() ────────────────────────────────────────────────────

def test_115_panggilan_skalar_jadi_satu_eksekusi_tanpa_rem(monkeypatch):
    pns = [f"WG{i:010d}" for i in range(115)]
    rec = _model(monkeypatch, [_tc(i, "harga_sims", {"part_number": p}) for i, p in enumerate(pns)])
    log = _spy_tool(monkeypatch)
    out = ai.chat(USER, [{"role": "user", "content": "isikan harga sims: " + " ".join(pns)}])

    assert len(log) == 1                                   # SATU eksekusi nyata
    assert log[0][0] == "harga_sims" and log[0][1]["part_number"] == pns
    assert out["tools_used"] == ["harga_sims"]             # telemetri = eksekusi, bukan permintaan
    tools_msgs = _pesan_tool(rec)
    assert len(tools_msgs) == 115                          # tiap tool_call_id tetap dibalas
    stub = [m for m in tools_msgs if '"digabung":true' in m["content"]]
    assert len(stub) == 114
    assert "c0" in stub[0]["content"]                      # menunjuk hasil wakil
    assert all("⛔ BATAS" not in m["content"] for m in tools_msgs)   # rem tak menyala
    assert not any("PANGGILAN DITOLAK" in (m.get("content") or "")
                   for m in rec["messages"][1] if m.get("role") == "user")


def test_argumen_lain_berbeda_tidak_dilebur(monkeypatch):
    rec = _model(monkeypatch, [
        _tc(0, "harga_sims", {"part_number": "A1"}),
        _tc(1, "harga_sims", {"part_number": "A2"}),
        _tc(2, "harga_sims", {"part_number": "A3", "konversi_idr": True}),
    ])
    log = _spy_tool(monkeypatch)
    ai.chat(USER, [{"role": "user", "content": "harga sims A1 A2 A3"}])
    assert [a["part_number"] for _, a in log] == [["A1", "A2"], "A3"]
    assert len(_pesan_tool(rec)) == 3


def test_tool_tanpa_argumen_array_perilaku_lama(monkeypatch):
    rec = _model(monkeypatch, [_tc(i, "info_part", {"pn": f"A{i}"}) for i in range(3)])
    log = _spy_tool(monkeypatch)
    out = ai.chat(USER, [{"role": "user", "content": "info A0 A1 A2"}])
    assert len(log) == 3 and out["tools_used"] == ["info_part"] * 3
    assert not any('"digabung"' in m["content"] for m in _pesan_tool(rec))


def test_pesan_rem_menyebut_argumen_array_tool_itu(monkeypatch):
    """Rem masih bisa menyala (mis. tool tanpa registri) — nota harus menunjuk
    argumen array milik tool yang bersangkutan bila ada."""
    monkeypatch.setattr(ai, "_MAX_CALLS_PER_TOOL", 2)
    # konversi_idr bergantian → tak bisa dilebur → panggilan ke-3 kena rem
    rec = _model(monkeypatch, [
        _tc(0, "harga_sims", {"part_number": "A1", "konversi_idr": True}),
        _tc(1, "harga_sims", {"part_number": "A2", "konversi_idr": False}),
        _tc(2, "harga_sims", {"part_number": "A3", "excel": True}),
    ])
    _spy_tool(monkeypatch)
    ai.chat(USER, [{"role": "user", "content": "harga sims"}])
    rem = [m for m in _pesan_tool(rec) if "⛔ BATAS" in m["content"]]
    assert len(rem) == 1
    assert "'part_number'" in rem[0]["content"] and "harga_sims" in rem[0]["content"]
