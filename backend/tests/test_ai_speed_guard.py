"""Test percepatan & guard EPC-first asisten (DeepSeek DI-MOCK, tanpa network):
- prefetch EPC latar saat user menyebut rangka;
- guard EPC-FIRST: rangka disebut + jawaban ber-PN tanpa tool ber-rangka → dikoreksi;
- batch >1 tool dieksekusi paralel dengan urutan hasil tetap deterministik;
- render knowledge block memuat fakta baru (DTC/filter Shantui/gearbox)."""
import threading
import time

import pytest

from app.services import ai_assistant as ai
from app.services import ai_knowledge

USER = {"username": "tester", "role": "user"}

# Simpan implementasi ASLI sebelum fixture menonaktifkannya di modul ai.
_PREFETCH_ASLI = ai._prefetch_epc_rangka


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    # Prefetch di-nolkan default (test guard tak boleh menyentuh EPC nyata);
    # test prefetch memasang kembali targetnya sendiri.
    monkeypatch.setattr(ai, "_prefetch_epc_rangka", lambda history: None)


def _stub_model(monkeypatch, seq):
    """_post_chat palsu: seq = list of str (tanpa tool) atau dict respons penuh."""
    calls = {"n": 0}

    def fake(messages, tools):
        c = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        if isinstance(c, str):
            return {"choices": [{"message": {"content": c}, "finish_reason": "stop"}]}
        return c

    monkeypatch.setattr(ai, "_post_chat", fake)
    return calls


# ── Prefetch EPC ─────────────────────────────────────────────────────────────
def test_prefetch_dipicu_rangka_di_pesan_terakhir(monkeypatch):
    hit = {"lookup": [], "ll": []}
    done = threading.Event()
    monkeypatch.setattr(ai.epc, "lookup", lambda r: hit["lookup"].append(r))

    def _ll(r):
        hit["ll"].append(r)
        done.set()
        return {}

    monkeypatch.setattr(ai.epc_bom, "loading_list", _ll)
    _PREFETCH_ASLI([{"role": "user", "content": "cek kampas rem RT108966 dong"}])
    assert done.wait(timeout=3), "thread prefetch tidak jalan"
    assert hit["lookup"] == ["RT108966"] and hit["ll"] == ["RT108966"]


def test_prefetch_tanpa_rangka_tidak_jalan(monkeypatch):
    called = []
    monkeypatch.setattr(ai.epc, "lookup", lambda r: called.append(r))
    _PREFETCH_ASLI([{"role": "user", "content": "stok filter oli berapa?"}])
    time.sleep(0.15)
    assert called == []


# ── Guard EPC-first ──────────────────────────────────────────────────────────
def test_epc_first_koreksi_saat_pn_tanpa_tool_rangka(monkeypatch):
    # PN grounded via katalog (riwayat asisten) TAPI user kini menyebut RANGKA →
    # wajib dikoreksi sekali agar cek EPC; model membandel → jawaban tetap keluar.
    monkeypatch.setattr(ai.part_index, "search_exact_pns",
                        lambda pns: [{"part_number": "WG9100443050"}])
    calls = _stub_model(monkeypatch, ["Kampas remnya WG9100443050."])
    history = [
        {"role": "user", "content": "kampas rem howo?"},
        {"role": "assistant", "content": "Perkiraan per-model: WG9100443050."},
        {"role": "user", "content": "unit saya rangka RT108966, kampas remnya itu kan?"},
    ]
    out = ai.chat(USER, history)
    assert calls["n"] == 2  # 1 jawaban + 1 koreksi EPC-first (sekali saja)
    assert "WG9100443050" in out["reply"]


def test_epc_first_tak_menyala_bila_tool_rangka_dicoba(monkeypatch):
    seq = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "a", "function": {"name": "bom_dari_rangka",
                                     "arguments": '{"rangka":"RT108966","kata_kunci":"kampas rem"}'}},
        ]}, "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "Kampas rem unit ini: WG9100443050."},
                      "finish_reason": "stop"}]},
    ]
    calls = _stub_model(monkeypatch, seq)
    monkeypatch.setattr(ai, "_run_tool", lambda name, args, user, sheet_id="": {
        "found": True, "parts": [{"part_number": "WG9100443050"}]})
    out = ai.chat(USER, [{"role": "user", "content": "kampas rem RT108966?"}])
    assert calls["n"] == 2  # ronde tool + jawaban final; TANPA koreksi tambahan
    assert "WG9100443050" in out["reply"]


def test_epc_first_tak_menyala_tanpa_rangka(monkeypatch):
    monkeypatch.setattr(ai.part_index, "search_exact_pns",
                        lambda pns: [{"part_number": "WG9100443050"}])
    calls = _stub_model(monkeypatch, ["Stok WG9100443050 ada 5."])
    history = [
        {"role": "user", "content": "cek WG9100443050"},
        {"role": "assistant", "content": "WG9100443050 stok 5."},
        {"role": "user", "content": "harganya?"},
    ]
    ai.chat(USER, history)
    assert calls["n"] == 1  # tanpa koreksi apa pun


def test_epc_first_jawaban_tanpa_pn_lolos(monkeypatch):
    calls = _stub_model(monkeypatch, ["Baik, saya cek dulu ya. Untuk unit rangka itu "
                                      "mohon konfirmasi bagian mana yang dimaksud."])
    ai.chat(USER, [{"role": "user", "content": "RT108966 remnya"}])
    assert calls["n"] == 1  # tak ada PN → guard tak menyala


# ── Eksekusi batch tool paralel: urutan hasil deterministik ─────────────────
def test_batch_tool_paralel_urutan_terjaga(monkeypatch):
    seq = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "t1", "function": {"name": "detail_part", "arguments": '{"part_number":"AAA111222"}'}},
            {"id": "t2", "function": {"name": "detail_part", "arguments": '{"part_number":"BBB333444"}'}},
            {"id": "t3", "function": {"name": "detail_part", "arguments": '{"part_number":"CCC555666"}'}},
        ]}, "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "AAA111222, BBB333444, CCC555666 tersedia."},
                      "finish_reason": "stop"}]},
    ]
    _stub_model(monkeypatch, seq)

    def slow_tool(name, args, user, sheet_id=""):
        # Yang pertama paling lambat — urutan hasil tetap harus sesuai panggilan.
        pn = args.get("part_number")
        time.sleep({"AAA111222": 0.2, "BBB333444": 0.1}.get(pn, 0.0))
        return {"found": True, "part_number": pn}

    monkeypatch.setattr(ai, "_run_tool", slow_tool)
    t0 = time.monotonic()
    out = ai.chat(USER, [{"role": "user", "content": "cek AAA111222 BBB333444 CCC555666"}])
    elapsed = time.monotonic() - t0
    assert out["tools_used"] == ["detail_part", "detail_part", "detail_part"]
    assert "AAA111222" in out["reply"]
    assert elapsed < 1.5  # paralel: jauh di bawah jumlah total sleep berurutan


# ── Knowledge block: fakta baru ikut dirender ────────────────────────────────
def test_render_knowledge_fakta_baru():
    d = {
        "prefix_pn": [], "sub_prefix_pn": [], "gudang": [],
        "fault_codes": {"jumlah": 2276},
        "filter_shantui_units": ["SD22", "SE215W（WP6H)"],
        "gearbox_repairkit": [{"model": "HW19709XST", "tipe": "9-speed", "jumlah_unit": 3}],
        "cakupan": {"unit_katalog_bom": 40, "pn_unik_bom": 18098},
    }
    blok = ai_knowledge._render(d, with_gudang=True)
    assert "2276 entri" in blok and "cari_kode_kesalahan" in blok
    assert "SD22" in blok and "cari_filter_shantui" in blok
    assert "HW19709XST (9-speed)" in blok and "repair_kit_transmisi" in blok
