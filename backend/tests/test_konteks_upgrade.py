# -*- coding: utf-8 -*-
"""Upgrade paham-konteks 2026-08 — DeepSeek DI-MOCK (tanpa network).

Yang dikunci di sini (rombakan p9/ai_session/p8):
  • slot PN: PN murni-angka (Weichai) ikut; VIN/frame/WO TIDAK ikut; fallback memo;
  • slot WO: user-first (tabel asisten tak lagi jadi 'klaim aktif');
  • memori sesi: slot unit, umur angka/fakta (basi ≠ grounded), touch-on-read;
  • guard angka: jumlah baris & bentuk desimal di-ground; banner tak 'tercuci'
    lewat riwayat;
  • guard EPC-FIRST: PN ketikan user & PN yang sudah terverifikasi EPC di memo
    tidak memicu koreksi palsu;
  • _DeltaGate: blok [PIKIR] SUSULAN di tengah aliran tak bocor ke layar;
  • pemangkasan pesan user terakhir: putus di batas kata + penanda eksplisit.
"""
from __future__ import annotations

import pytest

from app.services import ai_assistant as ai
from app.services import ai_session

USER = {"username": "tester", "role": "user"}
CONV = "konteks-uji-1234"

PN_EPC = "WG9100443050"


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai, "_prefetch_epc_rangka", lambda h: None)
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    ai_session._reset()
    yield
    ai_session._reset()


def _stub_model(monkeypatch, content):
    seq = [content] if isinstance(content, str) else list(content)
    calls = {"n": 0, "messages": None}

    def fake(messages, tools, max_tokens=6000):
        calls["messages"] = [dict(m) for m in messages]
        c = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return {"choices": [{"message": {"content": c}, "finish_reason": "stop"}]}

    monkeypatch.setattr(ai, "_post_chat", fake)
    return calls


# ── Slot PN: numerik masuk, VIN/frame/WO keluar ─────────────────────────────

def test_recent_pn_numerik_weichai_masuk():
    hist = [{"role": "assistant", "content": "Filter oli mesin: 612630010054."},
            {"role": "user", "content": "harganya?"}]
    assert "612630010054" in ai._recent_part_numbers(hist)


def test_recent_pn_vin_frame_wo_tak_ikut():
    hist = [{"role": "user",
             "content": "unit RT108966 VIN LZZ1BLSG1234R6789 klaim RIDZ0052607121 "
                        "pakai WG9925520270"}]
    pns = ai._recent_part_numbers(hist)
    assert "WG9925520270" in pns
    assert "RT108966" not in pns
    assert "LZZ1BLSG1234R6789" not in pns
    assert "RIDZ0052607121" not in pns


def test_recent_pn_potongan_digit_tak_dobel():
    hist = [{"role": "assistant", "content": "Gasket WG9725190712 tersedia."}]
    pns = ai._recent_part_numbers(hist)
    assert "WG9725190712" in pns
    assert "9725190712" not in pns


def test_recent_pn_fallback_memo_saat_riwayat_kosong():
    ai_session.merge(USER["username"], CONV, pn={PN_EPC: "bom_dari_rangka"})
    memo = ai_session.get(USER["username"], CONV)
    blk = ai._active_context_block(
        [{"role": "user", "content": "harganya?"}], memo)
    assert PN_EPC in blk


# ── Slot WO: user-first ─────────────────────────────────────────────────────

def test_recent_wo_user_menang_atas_tabel_asisten():
    hist = [
        {"role": "assistant",
         "content": "Tabel klaim: RIDZ0052607001, RIDZ0052607002, RIDZ0052607003."},
        {"role": "user", "content": "cek klaim RIDZ0052609999"},
    ]
    assert ai._recent_wo(hist) == ["RIDZ0052609999"]


def test_recent_wo_user_only_tanpa_fallback():
    hist = [{"role": "assistant", "content": "Klaim RIDZ0052607121 selesai."}]
    assert ai._recent_wo(hist, user_only=True) == []
    assert ai._recent_wo(hist) == ["RIDZ0052607121"]   # fallback tampilan tetap


# ── Deret nomor dokumen berlabel ────────────────────────────────────────────

def test_deret_dokumen_berlabel_semua_dibuang(monkeypatch):
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda toks: [])
    out = ai._rangka_candidates("CEK SO AB123456, CD654321 DAN EF111222 UNIT SJ346500")
    assert "SJ346500" in out
    assert "AB123456" not in out and "CD654321" not in out and "EF111222" not in out


# ── Memori sesi: unit, umur, touch-on-read ──────────────────────────────────

def test_memo_slot_unit_dari_argumen_tool(monkeypatch):
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [{"x": 1}])
    monkeypatch.setattr(
        ai, "_run_tool",
        lambda name, args, u, sid="": {"found": True,
                                       "hasil": [{"part_number": PN_EPC,
                                                  "nama": "brake pad"}]})
    seq = [
        {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "1", "type": "function",
             "function": {"name": "cari_part",
                          "arguments": '{"query": "kampas", "unit": "NX360"}'}}]},
            "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": f"Ketemu {PN_EPC}."},
                      "finish_reason": "stop"}]},
    ]
    it = iter(seq)
    last = {}

    def fake(messages, tools, max_tokens=6000):
        try:
            last["v"] = next(it)
        except StopIteration:
            pass
        return last["v"]

    monkeypatch.setattr(ai, "_post_chat", fake)
    ai.chat(USER, [{"role": "user", "content": "kampas nx360"}],
            conversation_id=CONV)
    memo = ai_session.get(USER["username"], CONV)
    assert memo is not None and memo["unit"] == ["NX360"]
    blk = ai._active_context_block([{"role": "user", "content": "yang depan?"}], memo)
    assert "NX360" in blk and "perkiraan per-model" in blk


def test_memo_angka_basi_tak_diground(monkeypatch):
    ai_session.merge(USER["username"], CONV, nums=["385000"],
                     fakta=["[RT108966] brake pad (harga 385000)"])
    # Tuakan SEMUA stempel waktu melewati ambang segar.
    with ai_session._lock:
        for e in ai_session._stash.values():
            for ent in e["nums"] + e["fakta"]:
                ent["at"] -= ai_session._FRESH_SEC + 60
    memo = ai_session.get(USER["username"], CONV)
    assert memo["nums"] == []
    assert memo["fakta"] == []
    assert memo["fakta_lama"] == ["[RT108966] brake pad (harga 385000)"]
    blk = ai._active_context_block([{"role": "user", "content": "brp?"}], memo)
    assert "CATATAN LAMA" in blk and "verifikasi" in blk


def test_memo_angka_segar_tetap_diground():
    ai_session.merge(USER["username"], CONV, nums=["385000"], fakta=["[x] y"])
    memo = ai_session.get(USER["username"], CONV)
    assert memo["nums"] == ["385000"]
    assert memo["fakta"] == ["[x] y"]
    assert memo["fakta_lama"] == []


def test_touch_on_read_menyegarkan_umur(monkeypatch):
    ai_session.merge("budi", "conv-touch-0001", pn=["AAA111"])
    with ai_session._lock:
        for e in ai_session._stash.values():
            e["at"] -= ai_session._TTL_SEC - 60          # 1 menit sebelum mati
    assert ai_session.get("budi", "conv-touch-0001") is not None
    with ai_session._lock:                               # dibaca = disegarkan
        e = next(iter(ai_session._stash.values()))
        import time as _t
        assert _t.monotonic() - e["at"] < 5


def test_merge_tanpa_tool_menyimpan_rangka_ketikan_user(monkeypatch):
    _stub_model(monkeypatch, "Siap, unit RT108966 dicatat.")
    ai.chat(USER, [{"role": "user", "content": "unit saya RT108966"}],
            conversation_id=CONV)
    memo = ai_session.get(USER["username"], CONV)
    assert memo is not None and "RT108966" in memo["rangka"]


# ── Guard angka: jumlah baris, desimal, anti-cuci ───────────────────────────

def test_extract_nums_bentuk_desimal():
    assert {"15000000", "1500000"} <= ai._extract_nums('"harga": 1500000.0')
    assert ai._extract_nums("stok 12, Rp 1.500.000") == {"12", "1500000"}


def test_row_count_diground(monkeypatch):
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [{"x": 1}])
    monkeypatch.setattr(
        ai, "_run_tool",
        lambda name, args, u, sid="": {"found": True,
                                       "hasil": [{"nama": f"item {i}"} for i in range(7)]})
    seq = [
        {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "1", "type": "function",
             "function": {"name": "cari_part", "arguments": '{"query": "lampu"}'}}]},
            "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "Ada 7 unit pilihan lampu."},
                      "finish_reason": "stop"}]},
    ]
    it = iter(seq)
    last = {}
    calls = {"n": 0}

    def fake(messages, tools, max_tokens=6000):
        calls["n"] += 1
        try:
            last["v"] = next(it)
        except StopIteration:
            pass
        return last["v"]

    monkeypatch.setattr(ai, "_post_chat", fake)
    out = ai.chat(USER, [{"role": "user", "content": "lampu ada berapa?"}])
    # '7 unit' = klaim stok menurut regex; len(hasil)==7 kini diground → lolos.
    assert calls["n"] == 2
    assert "tidak terverifikasi" not in out["reply"].lower()


def test_banner_angka_tak_tercuci_lewat_riwayat(monkeypatch):
    banner = ai._annotate_unverified_nums("Harga Rp 385.000.", ["385000"])
    hist = [
        {"role": "user", "content": "berapa harga kampas?"},
        {"role": "assistant", "content": banner},
        {"role": "user", "content": "jadi berapa?"},
    ]
    calls = _stub_model(monkeypatch, "Harganya Rp 385.000.")
    out = ai.chat(USER, hist)
    # Angka dari jawaban BER-BANNER tidak grounded → guard menyala lagi
    # (retry koreksi lalu banner lagi) — bukan lolos diam-diam.
    assert calls["n"] == 1 + ai._MAX_GUARD_RETRIES
    assert "tidak terverifikasi" in out["reply"].lower() \
        or "TIDAK terverifikasi" in out["reply"]


def test_angka_riwayat_tanpa_banner_tetap_sah(monkeypatch):
    hist = [
        {"role": "user", "content": "berapa harga kampas?"},
        {"role": "assistant", "content": "Harga Rp 385.000."},
        {"role": "user", "content": "jadi berapa?"},
    ]
    calls = _stub_model(monkeypatch, "Harganya Rp 385.000.")
    out = ai.chat(USER, hist)
    assert calls["n"] == 1
    assert "tidak terverifikasi" not in out["reply"].lower()


# ── Guard EPC-FIRST: pengecualian yang menghapus koreksi palsu ──────────────

def test_pn_ketikan_user_tak_memicu_epc_first(monkeypatch):
    calls = _stub_model(monkeypatch, "Stok WG9925520270 tersedia di gudang.")
    out = ai.chat(USER, [{"role": "user",
                          "content": "cek stok WG9925520270 buat RT108966"}])
    assert calls["n"] == 1                      # tanpa koreksi EPC-first
    assert "belum sempat diverifikasi" not in out["reply"]


def test_pn_terverifikasi_epc_di_memo_tak_memicu_epc_first(monkeypatch):
    ai_session.merge(USER["username"], CONV, rangka=["RT108966"],
                     pn={PN_EPC: "bom_dari_rangka"})
    calls = _stub_model(monkeypatch, f"Kampas remnya {PN_EPC}.")
    out = ai.chat(USER, [
        {"role": "user", "content": "BOM rem RT108966"},
        {"role": "assistant", "content": f"Kampas rem unit ini {PN_EPC}."},
        {"role": "user", "content": "sebutkan lagi part remnya"},
    ], conversation_id=CONV)
    assert calls["n"] == 1
    assert PN_EPC in out["reply"]


def test_pertanyaan_kecocokan_tetap_kena_epc_first(monkeypatch):
    """'PN cocok gak buat rangka X?' = inti aturan EPC-FIRST — PN ketikan user
    BUKAN pembebasan utk klaim kecocokan; guard wajib memaksa cek EPC sekali."""
    calls = _stub_model(monkeypatch, "Ya, WG9925520270 cocok untuk unit Anda.")
    ai.chat(USER, [{"role": "user",
                    "content": "WG9925520270 cocok gak buat rangka RT108966?"}])
    assert calls["n"] == 2                      # 1 koreksi EPC-first tersuntik


def test_memo_epc_tak_membebaskan_rangka_baru(monkeypatch):
    """PN terverifikasi EPC utk unit LAMA tidak membebaskan jawaban utk unit
    BARU: user menyebut rangka lain → pengecualian memo dimatikan."""
    ai_session.merge(USER["username"], CONV, rangka=["RT108966"],
                     pn={PN_EPC: "bom_dari_rangka"})
    calls = _stub_model(monkeypatch, f"Untuk unit itu pakai {PN_EPC}.")
    ai.chat(USER, [
        {"role": "user", "content": "BOM rem RT108966"},
        {"role": "assistant", "content": f"Kampas rem unit ini {PN_EPC}."},
        {"role": "user", "content": "kalau unit SJ346500 part remnya apa?"},
    ], conversation_id=CONV)
    assert calls["n"] == 2                      # guard EPC-first menyala lagi


def test_recent_wo_tabel_baru_menang_atas_wo_user_lama():
    hist = [
        {"role": "user", "content": "cek klaim RIDZ0052600001"},
        {"role": "assistant", "content": "Status RIDZ0052600001: selesai."},
        {"role": "user", "content": "riwayat klaim bulan ini"},
        {"role": "assistant",
         "content": "Tabel: RIDZ0052607001, RIDZ0052607002, RIDZ0052607003."},
        {"role": "user", "content": "yang kedua detailnya?"},
    ]
    wo = ai._recent_wo(hist)
    assert wo[0] == "RIDZ0052607001"            # dari tabel BARU, bukan WO lama
    assert "RIDZ0052600001" not in wo


def test_row_count_tak_membebaskan_klaim_stok_pcs(monkeypatch):
    """len(list)==3 TIDAK membuat 'stok 3 pcs' karangan lolos — jumlah baris
    hanya membebaskan klaim hitungan ('3 unit'), bukan klaim stok keras."""
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [{"x": 1}])
    monkeypatch.setattr(
        ai, "_run_tool",
        lambda name, args, u, sid="": {"found": True,
                                       "hasil": [{"nama": "a"}, {"nama": "b"},
                                                 {"nama": "c"}]})
    seq = [
        {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "1", "type": "function",
             "function": {"name": "cari_part", "arguments": '{"query": "seal"}'}}]},
            "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "Stok 3 pcs tersedia."},
                      "finish_reason": "stop"}]},
    ]
    it = iter(seq)
    last = {}
    calls = {"n": 0}

    def fake(messages, tools, max_tokens=6000):
        calls["n"] += 1
        try:
            last["v"] = next(it)
        except StopIteration:
            pass
        return last["v"]

    monkeypatch.setattr(ai, "_post_chat", fake)
    out = ai.chat(USER, [{"role": "user", "content": "ada seal?"}])
    assert calls["n"] == 2 + ai._MAX_GUARD_RETRIES   # guard angka menyala
    assert "tidak terverifikasi" in out["reply"].lower()


def test_extract_pns_urut_sesuai_kemunculan():
    teks = 'baris: WG9925520270 lalu 612630010054 lalu AZ9100443051'
    assert ai._extract_pns_urut(teks) == [
        "WG9925520270", "612630010054", "AZ9100443051"]


def test_fallback_memo_pakai_kalimat_lunak_tanpa_ordinal():
    ai_session.merge(USER["username"], CONV, pn={PN_EPC: "bom_dari_rangka"})
    memo = ai_session.get(USER["username"], CONV)
    blk = ai._active_context_block(
        [{"role": "user", "content": "harganya?"}], memo)
    assert "MEMORI SESI" in blk and "ORDINAL" in blk
    assert "BARU ditampilkan" not in blk


# ── _DeltaGate: [PIKIR] susulan di tengah aliran ────────────────────────────

def _alirkan(potongan):
    g = ai._DeltaGate()
    keluar = []
    for p in potongan:
        out = g.feed(p)
        if out:
            keluar.append(out)
    keluar.append(g.close())
    return "".join(keluar)


def test_gate_pikir_tengah_satu_chunk():
    assert _alirkan(["Jawaban A. [PIKIR]nalar lagi[/PIKIR] Lanjutan B."]) == \
        "Jawaban A.  Lanjutan B."


def test_gate_pikir_tengah_tag_terbelah():
    hasil = _alirkan(["Jawaban A. [PIK", "IR]rahasia[/PIK", "IR] Lanjut B."])
    assert "rahasia" not in hasil
    assert hasil == "Jawaban A.  Lanjut B."


def test_gate_pikir_tengah_tak_tertutup_dibuang():
    hasil = _alirkan(["Jawaban A. [PIKIR]nalar tanpa penutup yang panjang"])
    assert hasil == "Jawaban A. "
    assert "nalar" not in hasil


def test_gate_pikir_pembuka_tetap_bekerja():
    assert _alirkan(["[PIKIR]x[/PIKIR]Jawaban."]) == "Jawaban."


# ── Pemangkasan pesan user terakhir ─────────────────────────────────────────

def test_sanitize_history_pesan_terakhir_putus_di_kata():
    panjang = ("PN " + "WG9925520270 ") * 500          # jauh > 4000 char
    out = ai._sanitize_history([{"role": "user", "content": panjang}])
    assert "TERPOTONG" in out[-1]["content"]
    # tidak putus di tengah token: char terakhir sebelum penanda bukan alnum terpotong
    inti = out[-1]["content"].split(" …(TERPOTONG")[0]
    assert inti.endswith("WG9925520270")


def test_sanitize_history_pesan_lama_penanda_lama():
    hist = ([{"role": "user", "content": "x" * 5000}]
            + [{"role": "user", "content": f"pesan {i}"} for i in range(20)])
    out = ai._sanitize_history(hist)
    assert all("TERPOTONG" not in m["content"] for m in out[:-1])


# ── p8: perbaikan prompt ────────────────────────────────────────────────────

def test_prompt_nomor_aturan_tak_dobel(monkeypatch):
    monkeypatch.undo()
    teks = ai._system_prompt({"username": "t", "role": "admin"})
    assert "\n14. DUA JENIS HARGA" in teks
    assert "\n12. DUA JENIS HARGA" not in teks


def test_prompt_banding_pakai_cek_massal(monkeypatch):
    monkeypatch.undo()
    teks = ai._system_prompt({"username": "t", "role": "user"})
    assert "BANDING 2+ PN" in teks
    assert "panggil cek_massal_part" in teks


def test_prompt_pikir_plafon_menang(monkeypatch):
    monkeypatch.undo()
    teks = ai._system_prompt({"username": "t", "role": "user"})
    assert "plafon ±150 kata" in teks and "TETAP MENANG" in teks


# ── Konteks aktif: rangka memo vs riwayat ───────────────────────────────────

def test_rangka_memo_pakai_kalimat_lunak():
    ai_session.merge(USER["username"], CONV, rangka=["RT108966"])
    memo = ai_session.get(USER["username"], CONV)
    blk = ai._active_context_block(
        [{"role": "user", "content": "kalau filternya?"}], memo)
    assert "memori sesi" in blk
    assert "beralih ke unit/model lain" in blk


def test_rangka_riwayat_pakai_kalimat_tegas():
    blk = ai._active_context_block(
        [{"role": "user", "content": "part rem RT108966"}], None)
    assert "Nomor rangka AKTIF" in blk


def test_caveat_ganti_model(monkeypatch):
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: {"NX360"})
    ai_session.merge(USER["username"], CONV, rangka=["RT108966"])
    memo = ai_session.get(USER["username"], CONV)
    blk = ai._active_context_block(
        [{"role": "user", "content": "kalau nx360 gimana?"}], memo)
    assert "menyebut model unit" in blk and "NX360" in blk


def test_detail_part_membawa_status_jual_sims(monkeypatch):
    """detail_part kini membawa status jual RESMI SIMS (isSale) — sumber sah
    satu-satunya utk klaim dijual/tidaknya part (pengganti klaim marketability
    EPC yang terbukti terbalik)."""
    row = {"part_number": "AZ9998887776", "part_name": "TEST PART",
           "file": "NX360 6X4 (LZZ1TEST)"}
    monkeypatch.setattr(ai, "_detail_lookup_pemaaf", lambda pn: ([row], "", []))
    monkeypatch.setattr(ai.accurate, "available", lambda: False)
    monkeypatch.setattr(ai.sims, "get_part_spec", lambda pn: {})
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {})
    monkeypatch.setattr(ai.sims, "status_jual",
                        lambda pn: {"dijual": True, "label": "dijual di SIMS",
                                    "pra_turun_rak": None,
                                    "sumber": "SIMS partInfo (isSale)"})
    r = ai._t_detail_part({"part_number": "AZ9998887776"},
                          {"username": "t", "role": "user"})
    assert r["found"] is True
    assert r["status_jual_sims"]["dijual"] is True


def test_detail_part_status_jual_gagal_field_absen(monkeypatch):
    """SIMS mati/PN tak dikenal → field ABSEN (tak diketahui), BUKAN 'tidak dijual'."""
    row = {"part_number": "AZ9998887776", "part_name": "TEST PART",
           "file": "NX360 6X4 (LZZ1TEST)"}
    monkeypatch.setattr(ai, "_detail_lookup_pemaaf", lambda pn: ([row], "", []))
    monkeypatch.setattr(ai.accurate, "available", lambda: False)
    monkeypatch.setattr(ai.sims, "get_part_spec", lambda pn: {})
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {})
    monkeypatch.setattr(ai.sims, "status_jual", lambda pn: None)
    r = ai._t_detail_part({"part_number": "AZ9998887776"},
                          {"username": "t", "role": "user"})
    assert r["found"] is True
    assert "status_jual_sims" not in r


def test_harga_sims_hanya_utk_akun_sims(monkeypatch):
    hist = [{"role": "assistant", "content": "Ketemu WG9925520270."},
            {"role": "user", "content": "harganya?"}]
    monkeypatch.setattr(ai, "_can_sims", lambda u: False)
    blk = ai._active_context_block(hist, None, {"username": "t", "role": "user"})
    assert "harga_sims" not in blk
    monkeypatch.setattr(ai, "_can_sims", lambda u: True)
    blk2 = ai._active_context_block(hist, None, {"username": "t", "role": "admin"})
    assert "harga_sims" in blk2
