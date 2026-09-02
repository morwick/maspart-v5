"""Pecahan latensi giliran chat → ai_chat_log (migrations/029).

`latency_ms` saja hanya bilang giliran itu lambat; yang menentukan tindakan
adalah lambat DI MANA — menunggu MODEL menulis, atau menunggu TOOL eksternal
(EPC/SIMS/Accurate). Karena itu tiap panggilan model & tiap blok eksekusi tool
distopwatch, lalu dijumlah ke model_ms/tools_ms. ttft_ms disiapkan untuk jalur
streaming (0 selama giliran belum di-stream).

Seperti kolom token: skema lama (migrasi 029 belum jalan) TIDAK boleh membuat
log hilang — baris diulang tanpa kolom fase.
"""
import time

from app.services import ai_assistant as A
from app.services import ai_chat_log as L

# conftest me-no-op L.log_turn untuk modul yang namanya bukan test_ai_chat_log*
# (jaga-jaga agar test tak menulis ke Supabase produksi). Test tangga di bawah
# memang harus menguji fungsi ASLINYA — ditangkap di sini, saat impor modul,
# jauh sebelum fixture autouse itu berjalan. requests.post tetap di-mock.
_LOG_TURN_ASLI = L.log_turn

ADMIN = {"username": "mas", "role": "admin"}


def _hermetik(monkeypatch):
    monkeypatch.setattr(A, "_system_prompt", lambda user: "sys")
    monkeypatch.setattr(A, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(A, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(A, "_prefetch_epc_rangka", lambda h: None)


# ── chat(): waktu MODEL terukur & ikut tercatat ─────────────────────────────
def test_model_ms_terukur_dan_tercatat(monkeypatch):
    """Satu panggilan model yang sengaja lambat → model_ms mencerminkannya."""
    _hermetik(monkeypatch)

    def lambat(messages, tools, max_tokens=6000):
        time.sleep(0.03)
        return {"choices": [{"message": {"content": "Halo."},
                             "finish_reason": "stop"}]}

    monkeypatch.setattr(A, "_post_chat", lambat)
    logged: dict = {}
    monkeypatch.setattr(A.ai_chat_log, "log_turn_async",
                        lambda **kw: logged.update(kw) or True)

    A.chat(ADMIN, [{"role": "user", "content": "halo"}])

    assert logged["model_ms"] >= 10          # longgar: jam OS kasar di Windows
    assert logged["tools_ms"] == 0           # giliran ini tak memanggil tool
    assert logged["ttft_ms"] == 0            # belum ada jalur streaming
    # Pecahan tak boleh melebihi total giliran (nilai bohong ketahuan di sini).
    assert logged["model_ms"] <= logged["latency_ms"]


def test_tools_ms_terukur_dari_blok_eksekusi_tool(monkeypatch):
    """Ronde tool → wall-clock eksekusi tool masuk tools_ms, terpisah dari model_ms."""
    _hermetik(monkeypatch)
    responses = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "t1", "function": {"name": "cari_part", "arguments": "{}"}}]},
            "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "Ini jawabannya."},
                      "finish_reason": "stop"}]},
    ]
    monkeypatch.setattr(A, "_post_chat",
                        lambda messages, tools, max_tokens=6000: responses.pop(0))

    def tool_lambat(n, a, u, s=""):
        time.sleep(0.03)
        return {"found": True, "hasil": []}

    monkeypatch.setattr(A, "_run_tool", tool_lambat)
    logged: dict = {}
    monkeypatch.setattr(A.ai_chat_log, "log_turn_async",
                        lambda **kw: logged.update(kw) or True)

    A.chat(ADMIN, [{"role": "user", "content": "cek stok kampas rem"}])

    assert logged["tools_ms"] >= 10
    assert logged["model_ms"] >= 0
    assert logged["tools_ms"] <= logged["latency_ms"]


def test_kunci_fase_selalu_ada(monkeypatch):
    """Giliran paling sepele pun mengirim ketiga kunci — panel & agregasi
    mengandalkannya ADA, bukan kadang-kadang."""
    _hermetik(monkeypatch)
    monkeypatch.setattr(A, "_post_chat", lambda messages, tools, max_tokens=6000: {
        "choices": [{"message": {"content": "Halo."}, "finish_reason": "stop"}]})
    logged: dict = {}
    monkeypatch.setattr(A.ai_chat_log, "log_turn_async",
                        lambda **kw: logged.update(kw) or True)

    A.chat(ADMIN, [{"role": "user", "content": "halo"}])

    for k in ("model_ms", "tools_ms", "ttft_ms"):
        assert k in logged and isinstance(logged[k], int) and logged[k] >= 0


# ── Tangga payload: migrasi 029 belum jalan → log TETAP tercatat ────────────
def test_log_turn_fallback_tanpa_kolom_fase(monkeypatch):
    """PostgREST menolak (400) payload yang memuat model_ms → turun sampai lolos;
    barisnya tetap masuk, LENGKAP dengan kolom lama (guard_kinds dst).

    Sejak migrations/030 (diulang) dan 031 (telemetri per panggilan) tangganya
    bertambah dua anak, dan TIGA tingkat teratas sama-sama membawa model_ms —
    jadi ketiganya ditolak sebelum lolos."""
    payloads = []

    class _R:
        def __init__(self, code):
            self.status_code = code

    def fake_post(url, headers=None, json=None, timeout=None):
        payloads.append(json)
        return _R(400 if "model_ms" in json else 201)

    monkeypatch.setattr(L.requests, "post", fake_post)
    monkeypatch.setattr(L, "_rest_url", lambda t: "http://x/" + t)
    monkeypatch.setattr(L, "_service_headers", lambda *a, **k: {})

    ok = _LOG_TURN_ASLI(username="a", role="admin", question="q", tools_used=[],
                        rounds=1, latency_ms=10, guard_hit=False, tool_failed=False,
                        reply_len=5, outcome="ok", session_id="c-1",
                        guard_kinds=["dtc"], model_ms=800, tools_ms=200, ttft_ms=0)

    assert ok is True
    assert len(payloads) == 4
    assert payloads[0]["model_ms"] == 800 and payloads[0]["tools_ms"] == 200
    assert "pikir_chars" in payloads[0] and "diulang" in payloads[0]   # terkaya (031)
    assert "pikir_chars" not in payloads[1] and "diulang" in payloads[1]  # 030
    assert "diulang" not in payloads[2] and payloads[2]["model_ms"] == 800  # 029
    assert "model_ms" not in payloads[3]          # tingkat yang akhirnya lolos
    assert payloads[3]["guard_kinds"] == "dtc"    # kolom lama TIDAK ikut hilang
    assert payloads[3]["session_id"] == "c-1"


def test_log_turn_skema_baru_sekali_kirim(monkeypatch):
    """Migrasi 029 sudah jalan → cukup SATU POST, kolom fase ikut."""
    payloads = []

    class _R:
        status_code = 201

    monkeypatch.setattr(L.requests, "post",
                        lambda url, headers=None, json=None, timeout=None:
                        payloads.append(json) or _R())
    monkeypatch.setattr(L, "_rest_url", lambda t: "http://x/" + t)
    monkeypatch.setattr(L, "_service_headers", lambda *a, **k: {})

    ok = _LOG_TURN_ASLI(username="a", role="admin", question="q", tools_used=[],
                        rounds=0, latency_ms=9, guard_hit=False, tool_failed=False,
                        reply_len=5, outcome="ok", model_ms=7, tools_ms=1, ttft_ms=3)

    assert ok is True and len(payloads) == 1
    assert payloads[0]["model_ms"] == 7
    assert payloads[0]["tools_ms"] == 1
    assert payloads[0]["ttft_ms"] == 3


def test_memo_tangga_melompati_tingkat_yang_ditolak(monkeypatch):
    """Setelah sekali ditolak, giliran BERIKUTNYA tak mengulang tingkat yang sama:
    di server yang migrasinya tertinggal, itu POST sia-sia tiap giliran chat."""
    payloads = []

    class _R:
        def __init__(self, code):
            self.status_code = code

    def fake_post(url, headers=None, json=None, timeout=None):
        payloads.append(json)
        return _R(400 if "model_ms" in json else 201)

    monkeypatch.setattr(L.requests, "post", fake_post)
    monkeypatch.setattr(L, "_rest_url", lambda t: "http://x/" + t)
    monkeypatch.setattr(L, "_service_headers", lambda *a, **k: {})

    dasar = dict(username="a", role="admin", question="q", tools_used=[], rounds=0,
                 latency_ms=1, guard_hit=False, tool_failed=False, reply_len=1,
                 outcome="ok", model_ms=5)
    assert _LOG_TURN_ASLI(**dasar) is True
    # TIGA tingkat teratas membawa model_ms (031, 030, 029) → ketiganya ditolak.
    assert len(payloads) == 4
    payloads.clear()
    assert _LOG_TURN_ASLI(**dasar) is True
    assert len(payloads) == 1                     # langsung ke tingkat yang jalan
    assert "model_ms" not in payloads[0]


def test_memo_tak_mengunci_bila_tingkat_ingatan_ikut_ditolak(monkeypatch):
    """Ingatan hanyalah titik MULAI, bukan kunci: kalau tingkat yang diingat pun
    ditolak (skema ternyata mundur lebih jauh), tangga tetap turun sampai baris
    berhasil tercatat — log tak boleh hilang gara-gara optimasi ini."""
    tolak = {"nilai": set()}
    payloads = []

    class _R:
        def __init__(self, code):
            self.status_code = code

    def fake_post(url, headers=None, json=None, timeout=None):
        payloads.append(json)
        return _R(400 if tolak["nilai"] & set(json.keys()) else 201)

    monkeypatch.setattr(L.requests, "post", fake_post)
    monkeypatch.setattr(L, "_rest_url", lambda t: "http://x/" + t)
    monkeypatch.setattr(L, "_service_headers", lambda *a, **k: {})

    dasar = dict(username="a", role="admin", question="q", tools_used=[], rounds=0,
                 latency_ms=1, guard_hit=False, tool_failed=False, reply_len=1,
                 outcome="ok", reply="halo", model_ms=5)
    tolak["nilai"] = {"model_ms"}
    _LOG_TURN_ASLI(**dasar)                       # memo → tingkat guard_kinds
    tolak["nilai"] = {"model_ms", "reply"}        # skema ternyata mundur lebih jauh
    payloads.clear()
    assert _LOG_TURN_ASLI(**dasar) is True
    assert "reply" not in payloads[-1]            # turun lagi sampai lolos
    assert payloads[-1]["outcome"] == "ok"


# ── summary(): rata-rata HANYA dari baris terukur ───────────────────────────
def test_summary_fase_abaikan_baris_pra_migrasi(monkeypatch):
    rows = [
        # 2 baris lama (pra-migrasi 029): 0 → tak boleh menyeret rata-rata turun.
        {"latency_ms": 1000, "outcome": "ok"},
        {"latency_ms": 1000, "outcome": "ok", "model_ms": 0, "tools_ms": 0},
        {"latency_ms": 20000, "outcome": "ok", "model_ms": 16000, "tools_ms": 3000},
        {"latency_ms": 10000, "outcome": "ok", "model_ms": 8000, "tools_ms": 1000},
    ]
    monkeypatch.setattr(L, "list_logs", lambda limit=1000: rows)

    f = L.summary()["fase"]

    assert f["giliran_terukur"] == 2
    assert f["rata2_model_ms"] == 12000
    assert f["rata2_tools_ms"] == 2000


def test_summary_tanpa_data_fase_tetap_utuh(monkeypatch):
    monkeypatch.setattr(L, "list_logs",
                        lambda limit=1000: [{"latency_ms": 5, "outcome": "ok"}])

    s = L.summary()

    assert s["fase"]["giliran_terukur"] == 0
    assert s["fase"]["rata2_model_ms"] == 0
    assert s["total"] == 1                        # metrik lama tak terganggu


# ── Tangga BACA ikut migrasi 030 (audit 2026-08-28: kolom diulang tak pernah diminta) ─
def test_list_logs_minta_diulang_dulu_lalu_jatuh_ke_full(monkeypatch):
    from app.services import ai_chat_log as cl
    dilihat = []

    class _R:
        def __init__(self, ok, data):
            self.ok, self._data = ok, data

        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError("400")

        def json(self):
            return self._data

    def get(url, headers=None, params=None, timeout=None):
        dilihat.append(params["select"])
        if ",diulang" in params["select"]:
            return _R(False, None)          # migrasi 030 (dan 031) belum dijalankan
        return _R(True, [{"id": 1}])

    monkeypatch.setattr(cl.requests, "get", get)
    assert cl.list_logs(5) == [{"id": 1}]
    # 031 (audit) dicoba dulu, lalu 030, lalu 029 (FULL) yang akhirnya lolos.
    assert dilihat[:3] == [cl._SELECT_AUDIT, cl._SELECT_DIULANG, cl._SELECT_FULL]


def test_summary_menghitung_diulang_bila_kolom_ada(monkeypatch):
    from app.services import ai_chat_log as cl
    rows = [{"latency_ms": 1, "diulang": True}, {"latency_ms": 1, "diulang": False},
            {"latency_ms": 1}]
    monkeypatch.setattr(cl, "list_logs", lambda limit=1000: rows)
    s = cl.summary()
    assert s["pertanyaan_diulang"] == 1 and s["pertanyaan_diulang_persen"] > 0
