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

    def fake(messages, tools, max_tokens=6000):
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


def test_epc_first_kebal_pn_dari_tool_klaim(monkeypatch):
    # Kasus nyata 2026-07-23: tabel riwayat klaim memuat kolom Frame (RT108970 dst)
    # → "cek <no WO>" berikutnya memanggil detail_klaim, jawabannya ber-PN, dan guard
    # EPC-FIRST memaksa ekskursi EPC 6 ronde sia-sia. PN hasil tool garansi/klaim =
    # data resmi per-unit → guard TIDAK boleh menyala.
    seq = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "a", "function": {"name": "detail_klaim",
                                     "arguments": '{"no_wo":"RIDZ0052607125"}'}},
        ]}, "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content":
                      "Part yang diklaim: WG9100443050 (timing gear)."},
                      "finish_reason": "stop"}]},
    ]
    calls = _stub_model(monkeypatch, seq)
    monkeypatch.setattr(ai, "_run_tool", lambda name, args, user, sheet_id="": {
        "found": True, "no_wo": "RIDZ0052607125", "frame": "RT108970",
        "parts": [{"part_number": "WG9100443050", "nama": "timing gear"}]})
    history = [
        {"role": "user", "content": "riwayat klaim garansi bulan ini"},
        {"role": "assistant",
         "content": "RIDZ0052607125 | Frame RT108970 | Timing gear rusak"},
        {"role": "user", "content": "cek RIDZ0052607125"},
    ]
    out = ai.chat(USER, history)
    assert calls["n"] == 2  # ronde tool + jawaban final; TANPA koreksi EPC-first
    assert "WG9100443050" in out["reply"]
    assert "belum sempat diverifikasi" not in out["reply"]  # tanpa disclaimer terminal


# ── Guard DTC-first (bukti log 2026-07-16: SPN 520243 FMI 21 dijawab 'tidak
#    ditemukan' TANPA memanggil tool, padahal datanya ADA) ────────────────────
def test_dtc_tokens_deteksi():
    assert ai._dtc_tokens("cek kesalahan SPN 520243 FMI 21") == {"520243"}
    assert ai._dtc_tokens("apa arti P0100F7?") == {"P0100F7"}
    assert "B1117" in ai._dtc_tokens("kode error ABS B1117 muncul")
    assert ai._dtc_tokens("stok filter oli howo?") == set()


def test_dtc_first_koreksi_saat_jawab_tanpa_tool(monkeypatch):
    # Meniru log nyata: setelah satu 'tidak ditemukan' yang sah, model malas —
    # menjawab SPN berikutnya dari ingatan tanpa tool → wajib dikoreksi sekali.
    calls = _stub_model(monkeypatch, [
        "SPN 520243 FMI 21 — Tidak ditemukan di database. Coba cek ulang.",
        "SPN 520243 FMI 21 = P0088 (tekanan rail melebihi batas maksimum).",
    ])
    history = [
        {"role": "user", "content": "SPN 524045 FMI 5"},
        {"role": "assistant", "content": "SPN 524045 FMI 5 tidak terdaftar di database."},
        {"role": "user", "content": "cek kesalahan SPN 520243 FMI 21"},
    ]
    out = ai.chat(USER, history)
    assert calls["n"] == 2  # 1 jawaban malas + 1 ronde koreksi paksa
    assert "P0088" in out["reply"]


def test_dtc_first_tak_menyala_bila_tool_dipanggil(monkeypatch):
    seq = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "d1", "function": {"name": "cari_kode_kesalahan",
                                      "arguments": '{"spn":520243,"fmi":21}'}},
        ]}, "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "SPN 520243 FMI 21 = P0088 (rail terlalu tinggi)."},
                      "finish_reason": "stop"}]},
    ]
    calls = _stub_model(monkeypatch, seq)
    monkeypatch.setattr(ai, "_run_tool", lambda name, args, user, sheet_id="": {
        "jumlah_cocok": 1, "hasil": [{"kode": "P0088", "spn": 520243, "fmi": 21}]})
    out = ai.chat(USER, [{"role": "user", "content": "cek kesalahan SPN 520243 FMI 21"}])
    assert calls["n"] == 2  # ronde tool + jawaban final; TANPA koreksi tambahan
    assert "P0088" in out["reply"]


def test_dtc_first_tak_menyala_tanpa_pola_dtc(monkeypatch):
    calls = _stub_model(monkeypatch, ["Filter oli HOWO tersedia beberapa pilihan."])
    ai.chat(USER, [{"role": "user", "content": "filter oli howo ada?"}])
    assert calls["n"] == 1


def test_dtc_first_sekali_saja_bila_membandel(monkeypatch):
    # Model tetap tak memanggil tool setelah dikoreksi → jawaban tetap keluar
    # (tak ada loop tak berujung).
    calls = _stub_model(monkeypatch, [
        "SPN 520243 FMI 21 tidak ditemukan.",
        "SPN 520243 FMI 21 tetap tidak saya temukan.",
    ])
    out = ai.chat(USER, [{"role": "user", "content": "cek SPN 520243 FMI 21"}])
    assert calls["n"] == 2
    assert "520243" in out["reply"]


# ── Eksekusi batch tool paralel: urutan hasil deterministik ─────────────────
def test_batch_tool_paralel_urutan_terjaga(monkeypatch):
    # Test ini mengukur WALL-CLOCK — semua jalur network wajib distub. Tanpa ini,
    # laptop ber-.env Supabase live memuat izin dingin (~2 dtk round-trip) di
    # dalam chat() dan menembus anggaran 1,5 dtk (flaky lama, akar bukan CPU).
    monkeypatch.setattr("app.services.permissions.effective",
                        lambda kind, u, r: ["col_stok", "col_harga"])
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
