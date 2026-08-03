"""chat(on_delta=…) — DRAF jawaban mengalir ke klien selagi model menulis.

Cermin `test_stream_progress.py`, tapi untuk kanal TEKS (bukan label langkah).
Kontrak yang dijaga:
  - potongan tiba URUT dan gabungannya == `reply` final (giliran tanpa guard);
  - blok [PIKIR] tak pernah ikut mengalir;
  - guard menyala → `on_delta(None)` (reset) dipancarkan SEBELUM jawaban final,
    dan `reply` di return tetap satu-satunya kebenaran;
  - ⚠️ stub LAMA `lambda messages, tools, max_tokens=6000` (dipakai belasan modul
    test) harus tetap jalan — kwarg on_delta wajib KONDISIONAL;
  - ttft_ms (waktu potongan pertama tiba di klien) ikut tercatat ke ai_chat_log.
DeepSeek DI-MOCK sepenuhnya — tak ada panggilan model asli.
"""
import pytest

from app.services import ai_assistant as ai

USER = {"username": "tester", "role": "user"}
TOOL_CALL = {"choices": [{"message": {"content": "", "tool_calls": [
    {"id": "a", "function": {"name": "cari_part", "arguments": "{}"}}]},
    "finish_reason": "tool_calls"}]}


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "sys uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai, "_prefetch_epc_rangka", lambda h: None)
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])
    monkeypatch.setattr(ai, "_run_tool",
                        lambda n, a, u, sheet_id="": {"found": True, "hasil": []})


def _stub(monkeypatch, seq, potong=12):
    """_post_chat palsu. Bila dipanggil DENGAN on_delta, isi jawaban dikirim
    potong demi potong lebih dulu (meniru SSE provider), lalu dict-nya
    dikembalikan seperti respons non-stream."""
    seq = list(seq)
    calls = {"n": 0, "stream": []}

    def fake(messages, tools, max_tokens=6000, on_delta=None):
        data = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        calls["stream"].append(on_delta is not None)
        if on_delta is not None:
            isi = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            for i in range(0, len(isi), potong):
                on_delta(isi[i:i + potong])
        return data

    monkeypatch.setattr(ai, "_post_chat", fake)
    return calls


def _jawab(teks, finish="stop"):
    return {"choices": [{"message": {"content": teks}, "finish_reason": finish}]}


# ── jalur lurus: draf == jawaban ────────────────────────────────────────────
def test_delta_urut_dan_gabungannya_sama_dengan_reply(monkeypatch):
    """Giliran tanpa guard: apa yang dibaca user selagi menunggu = jawaban akhir."""
    JAWAB = ("Kampas rem depan tersedia di gudang Surabaya. "
             "Silakan sebutkan nomor rangka bila ingin dipastikan per unit.")
    _stub(monkeypatch, [TOOL_CALL, _jawab(f"[PIKIR]nalar internal[/PIKIR]{JAWAB}")])

    potongan: list = []
    out = ai.chat(USER, [{"role": "user", "content": "kampas rem ada?"}],
                  on_delta=potongan.append)

    assert out["reply"] == JAWAB
    assert None not in potongan                    # tak ada guard → tak ada reset
    assert "".join(potongan) == JAWAB              # urut & utuh
    assert len(potongan) > 1                       # benar-benar mengalir
    assert "PIKIR" not in "".join(potongan)        # nalar tak pernah bocor


def test_ronde_perencanaan_tidak_ikut_di_stream(monkeypatch):
    """Panggilan ronde-0 (hampir selalu balas tool_calls) tak perlu di-stream —
    hanya panggilan yang KEMUNGKINAN menulis jawaban yang dialirkan."""
    calls = _stub(monkeypatch, [TOOL_CALL, _jawab("Ini jawabannya.")])
    ai.chat(USER, [{"role": "user", "content": "cari part"}], on_delta=lambda p: None)
    assert calls["stream"] == [False, True]


def test_tanpa_on_delta_tak_ada_yang_dialirkan(monkeypatch):
    calls = _stub(monkeypatch, [TOOL_CALL, _jawab("Ini jawabannya.")])
    out = ai.chat(USER, [{"role": "user", "content": "cari part"}])
    assert out["reply"] == "Ini jawabannya."
    assert calls["stream"] == [False, False]


def test_stub_lama_tanpa_parameter_on_delta_tetap_jalan(monkeypatch):
    """KONTRAK: belasan modul test lama memakai stub bertanda tangan persis ini.
    Kalau chat() mengirim on_delta tanpa syarat, semuanya pecah sekaligus."""
    def fake_lama(messages, tools, max_tokens=6000):
        return _jawab("Halo.")

    monkeypatch.setattr(ai, "_post_chat", fake_lama)
    out = ai.chat(USER, [{"role": "user", "content": "halo"}])
    assert out["reply"] == "Halo."


def test_callback_yang_melempar_tak_menjatuhkan_giliran(monkeypatch):
    """Klien menutup tab di tengah aliran → on_delta melempar; giliran tetap utuh."""
    _stub(monkeypatch, [TOOL_CALL, _jawab("Jawaban tetap selesai.")])

    def _boom(_p):
        raise RuntimeError("klien pergi")

    out = ai.chat(USER, [{"role": "user", "content": "cari part"}], on_delta=_boom)
    assert out["reply"] == "Jawaban tetap selesai."


# ── guard menyala → draf dibatalkan ─────────────────────────────────────────
def test_guard_memancarkan_reset_sebelum_jawaban_final(monkeypatch):
    """PN karangan sempat tampil beberapa detik → reset, lalu jawaban bersih."""
    _stub(monkeypatch, [
        TOOL_CALL,
        _jawab("Pakai part AZ9998887776 ya, itu yang cocok."),   # PN tak ter-ground
        _jawab("Maaf, PN-nya belum bisa saya pastikan."),
    ])
    potongan: list = []
    out = ai.chat(USER, [{"role": "user", "content": "tierod apa?"}],
                  on_delta=potongan.append)

    assert None in potongan                                  # reset terpancar
    assert potongan.index(None) < len(potongan) - 1          # sebelum jawaban akhir
    # Draf kotor mendahului reset; setelah reset hanya jawaban bersih.
    kotor = "".join(p for p in potongan[:potongan.index(None)] if p)
    bersih = "".join(p for p in potongan[potongan.index(None) + 1:] if p)
    assert "AZ9998887776" in kotor
    assert "AZ9998887776" not in bersih
    assert "AZ9998887776" not in out["reply"]
    assert out["reply"] == "Maaf, PN-nya belum bisa saya pastikan."


def test_reset_disertai_label_progress_supaya_klien_tak_diam(monkeypatch):
    """Setelah draf dihapus, layar tak boleh kosong tanpa penjelasan."""
    _stub(monkeypatch, [
        TOOL_CALL,
        _jawab("Pakai part AZ9998887776 ya."),
        _jawab("Belum bisa dipastikan."),
    ])
    label: list = []
    ai.chat(USER, [{"role": "user", "content": "tierod apa?"}],
            on_progress=label.append, on_delta=lambda p: None)
    assert ai._LBL_RAPI in label


def test_jawaban_kosong_memicu_reset_lalu_retry(monkeypatch):
    """Model berhenti di [PIKIR] (jawaban kosong) → draf dibuang, model menulis
    ulang. Yang sudah tampil di layar bukan jawaban, jadi tak boleh ditinggal."""
    _stub(monkeypatch, [
        TOOL_CALL,
        _jawab("[PIKIR]nalar panjang tanpa jawaban"),
        _jawab("Ini jawaban yang benar."),
    ])
    potongan: list = []
    out = ai.chat(USER, [{"role": "user", "content": "cari part"}],
                  on_delta=potongan.append)
    assert None in potongan
    assert out["reply"] == "Ini jawaban yang benar."
    assert "".join(p for p in potongan if p) == "Ini jawaban yang benar."


# ── observabilitas ──────────────────────────────────────────────────────────
def test_ttft_ms_tercatat_saat_di_stream(monkeypatch):
    _stub(monkeypatch, [TOOL_CALL, _jawab("Jawaban yang cukup panjang untuk mengalir.")])
    logged: dict = {}
    monkeypatch.setattr(ai.ai_chat_log, "log_turn_async",
                        lambda **kw: logged.update(kw) or True)

    ai.chat(USER, [{"role": "user", "content": "cari part"}], on_delta=lambda p: None)

    assert logged["ttft_ms"] > 0
    assert logged["ttft_ms"] <= logged["latency_ms"]   # mustahil lebih lambat dari giliran


def test_ttft_ms_nol_bila_tak_di_stream(monkeypatch):
    _stub(monkeypatch, [TOOL_CALL, _jawab("Jawaban.")])
    logged: dict = {}
    monkeypatch.setattr(ai.ai_chat_log, "log_turn_async",
                        lambda **kw: logged.update(kw) or True)

    ai.chat(USER, [{"role": "user", "content": "cari part"}])
    assert logged["ttft_ms"] == 0


# ── endpoint /chat-stream: protokol SSE additive & OPT-IN ───────────────────
def _frames(body_kw: dict, fake_chat) -> list[dict]:
    """Jalankan endpoint /chat-stream dgn chat() palsu → daftar frame terurai."""
    import asyncio
    import json

    from app.routers import ai as ai_router
    from app.services import app_config

    asli_load = app_config.load
    app_config.load = lambda: {"version": {}, "config": {}}
    asli_chat = ai_router.ai_assistant.chat
    ai_router.ai_assistant.chat = fake_chat
    try:
        body = ai_router.AIChatRequest(
            messages=[{"role": "user", "content": "kampas rem ada?"}], **body_kw)
        resp = ai_router.ai_chat_stream(body, USER)

        async def _kumpul():
            out = []
            async for chunk in resp.body_iterator:
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8")
                for baris in chunk.strip().split("\n"):
                    if baris.startswith("data: "):
                        out.append(json.loads(baris[6:]))
            return out

        return asyncio.run(_kumpul())
    finally:
        app_config.load = asli_load
        ai_router.ai_assistant.chat = asli_chat


def _chat_palsu(tangkap: dict):
    """chat() palsu yang MENCATAT kwarg yang diterimanya lalu memancarkan
    beberapa potongan + reset (bila diminta streaming)."""
    def fake(user, history, sheet_id="", on_progress=None, conversation_id="",
             **kw):
        tangkap["kw"] = kw
        if on_progress:
            on_progress("Memproses pertanyaan…")
        cb = kw.get("on_delta")
        if cb:
            cb("Kampas ")
            cb(None)                 # guard menyala → draf dibuang
            cb("Jawaban bersih.")
        return {"reply": "Jawaban bersih.", "tools_used": ["cari_part"]}
    return fake


def test_endpoint_tanpa_opt_in_tak_mengirim_frame_delta():
    """Klien lama (web sebelum Fase 3 & APK 2.2.0) TIDAK boleh melihat frame baru."""
    tangkap: dict = {}
    frames = _frames({}, _chat_palsu(tangkap))
    assert "on_delta" not in tangkap["kw"]          # kwarg KONDISIONAL
    assert [f["type"] for f in frames] == ["progress", "done"]
    assert frames[-1]["result"]["reply"] == "Jawaban bersih."


def test_endpoint_stream_tokens_false_sama_dengan_tanpa_opt_in():
    tangkap: dict = {}
    frames = _frames({"stream_tokens": False}, _chat_palsu(tangkap))
    assert "on_delta" not in tangkap["kw"]
    assert not any(f["type"] in ("delta", "reset") for f in frames)


def test_endpoint_stream_tokens_true_kirim_delta_reset_lalu_done():
    tangkap: dict = {}
    frames = _frames({"stream_tokens": True}, _chat_palsu(tangkap))
    assert "on_delta" in tangkap["kw"]
    assert [f["type"] for f in frames] == ["progress", "delta", "reset", "delta", "done"]
    assert frames[1] == {"type": "delta", "text": "Kampas "}
    assert frames[2] == {"type": "reset"}           # tanpa field lain — cukup jelas
    # Bentuk frame `done` TIDAK berubah: klien lama membacanya persis sama.
    assert frames[-1] == {"type": "done",
                          "result": {"reply": "Jawaban bersih.",
                                     "tools_used": ["cari_part"]}}


def test_endpoint_error_tetap_satu_frame_error():
    def _gagal(user, history, **kw):
        raise RuntimeError("provider mati")

    frames = _frames({"stream_tokens": True}, _gagal)
    assert frames[-1]["type"] == "error"
    assert "provider mati" in frames[-1]["message"]
