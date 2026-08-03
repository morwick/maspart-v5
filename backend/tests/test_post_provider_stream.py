"""_post_provider mode STREAM: `stream:true` + SSE dibaca potong demi potong,
tapi yang DIKEMBALIKAN tetap dict berbentuk PERSIS respons non-stream.

Bentuk itu kontrak keras: `_add_usage`, `choices[0].message`, `_finish_reason`,
dan seluruh loop chat() tidak boleh tahu jawabannya datang mengalir. Yang diuji:
  - content delta murni → dict identik bentuknya + usage chunk terakhir masuk;
  - tool_calls delta digabung per index (arguments dirangkai);
  - content lalu tool_calls → draf dibatalkan (on_delta(None));
  - putus di tengah aliran → reset + _AIGagalSementara (ladder mengulang);
  - reasoning_content (deepseek-v4-flash) diabaikan, bukan bagian jawaban.
"""
import json

import pytest
import requests as real_requests

from app.services import ai_assistant as A


def _sse(*chunks) -> list[str]:
    """Ubah daftar dict jadi baris SSE ala provider (ditutup [DONE])."""
    return [f"data: {json.dumps(c, ensure_ascii=False)}" for c in chunks] + ["", "data: [DONE]"]


def _delta(d: dict, finish=None) -> dict:
    return {"choices": [{"index": 0, "delta": d, "finish_reason": finish}]}


class _FakeStreamResp:
    """Respons `requests` bergaya stream. `lines` boleh memuat instance Exception
    → dilempar saat iterasi mencapainya (simulasi koneksi putus di tengah)."""

    def __init__(self, lines, status=200):
        self.status_code = status
        self._lines = list(lines)
        self.text = "\n".join(x for x in self._lines if isinstance(x, str))
        self.closed = False

    def iter_lines(self, decode_unicode=False):
        for x in self._lines:
            if isinstance(x, Exception):
                raise x
            yield x

    def json(self):
        return {"error": {"message": "boom"}}

    def close(self):
        self.closed = True


class _FakeRequests:
    RequestException = real_requests.RequestException

    def __init__(self, resp):
        self.resp = resp
        self.posts = []

    def post(self, url, headers=None, json=None, timeout=None, stream=False):
        self.posts.append({"url": url, "payload": json, "timeout": timeout,
                           "stream": stream})
        return self.resp


def _panggil(monkeypatch, lines, on_delta):
    fake = _FakeRequests(_FakeStreamResp(lines))
    monkeypatch.setattr(A, "requests", fake)
    data = A._post_provider("https://api.test", "k", "m", [{"role": "user", "content": "hai"}],
                            [], 6000, on_delta=on_delta)
    return data, fake


def test_content_delta_bentuk_hasil_sama_dengan_non_stream(monkeypatch):
    potongan: list = []
    data, fake = _panggil(monkeypatch, _sse(
        _delta({"role": "assistant", "content": "Ha"}),
        _delta({"content": "lo "}),
        _delta({"content": "dunia."}, finish="stop"),
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 7,
                                  "prompt_cache_hit_tokens": 90}},
    ), potongan.append)

    # Payload minta stream + usage (tanpa include_usage, biaya giliran jadi buta).
    assert fake.posts[0]["payload"]["stream"] is True
    assert fake.posts[0]["payload"]["stream_options"] == {"include_usage": True}
    assert fake.posts[0]["stream"] is True
    assert fake.posts[0]["timeout"] == A._STREAM_TIMEOUT      # (connect, antar-chunk)

    assert potongan == ["Ha", "lo ", "dunia."]                # urut, apa adanya
    msg = data["choices"][0]["message"]
    assert msg["content"] == "Halo dunia."
    assert msg["role"] == "assistant"
    assert "tool_calls" not in msg                            # tak ada = tak dikarang
    assert data["choices"][0]["finish_reason"] == "stop"
    assert A._finish_reason(data) == "stop"

    tot = {"calls": 0, "in": 0, "out": 0, "cache": 0}
    A._add_usage(tot, data)
    assert (tot["in"], tot["out"], tot["cache"]) == (100, 7, 90)


def test_tool_calls_delta_digabung_per_index(monkeypatch):
    potongan: list = []
    data, _ = _panggil(monkeypatch, _sse(
        _delta({"tool_calls": [
            {"index": 0, "id": "call_a", "type": "function",
             "function": {"name": "cari_part", "arguments": '{"q":'}},
            {"index": 1, "id": "call_b", "type": "function",
             "function": {"name": "detail_part", "arguments": ""}},
        ]}),
        _delta({"tool_calls": [
            {"index": 0, "function": {"arguments": '"rem"}'}},
            {"index": 1, "function": {"arguments": '{"pn":"X1"}'}},
        ]}),
        _delta({}, finish="tool_calls"),
    ), potongan.append)

    tcs = data["choices"][0]["message"]["tool_calls"]
    assert [t["id"] for t in tcs] == ["call_a", "call_b"]      # urut index
    assert tcs[0]["function"] == {"name": "cari_part", "arguments": '{"q":"rem"}'}
    assert json.loads(tcs[1]["function"]["arguments"]) == {"pn": "X1"}
    assert data["choices"][0]["finish_reason"] == "tool_calls"
    assert potongan == []                                     # ronde tool ≠ jawaban


def test_content_lalu_tool_calls_membatalkan_draf(monkeypatch):
    """Model sempat menulis kalimat lalu ternyata memanggil tool → draf di layar
    user harus DIBUANG, dan teks sesudahnya tak boleh menyusul."""
    potongan: list = []
    data, _ = _panggil(monkeypatch, _sse(
        _delta({"content": "Sebentar, saya cek dulu."}),
        _delta({"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                "function": {"name": "cari_part", "arguments": "{}"}}]}),
        _delta({"content": "teks susulan"}, finish="tool_calls"),
    ), potongan.append)

    assert potongan == ["Sebentar, saya cek dulu.", None]     # None = reset
    # Isi teks TETAP lengkap di hasil (loop chat yang memutuskan nasibnya).
    assert data["choices"][0]["message"]["content"] == "Sebentar, saya cek dulu.teks susulan"
    assert data["choices"][0]["message"]["tool_calls"][0]["id"] == "c1"


def test_tool_calls_tanpa_index_pakai_urutan(monkeypatch):
    """Provider yang tak mengirim `index` tetap harus menghasilkan 2 tool call."""
    data, _ = _panggil(monkeypatch, _sse(
        _delta({"tool_calls": [
            {"id": "a", "function": {"name": "t1", "arguments": "{}"}},
            {"id": "b", "function": {"name": "t2", "arguments": "{}"}},
        ]}, finish="tool_calls"),
    ), lambda p: None)
    assert [t["id"] for t in data["choices"][0]["message"]["tool_calls"]] == ["a", "b"]


def test_id_dan_nama_berulang_tidak_digandakan(monkeypatch):
    """Provider yang mengulang id/name penuh tiap chunk tak boleh menghasilkan
    'call_1call_1' — tool_call_id seperti itu langsung ditolak API berikutnya."""
    data, _ = _panggil(monkeypatch, _sse(
        _delta({"tool_calls": [{"index": 0, "id": "call_1",
                                "function": {"name": "cari_part", "arguments": '{"q":'}}]}),
        _delta({"tool_calls": [{"index": 0, "id": "call_1",
                                "function": {"name": "cari_part", "arguments": '"rem"}'}}]},
               finish="tool_calls"),
    ), lambda p: None)
    tc = data["choices"][0]["message"]["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["function"]["name"] == "cari_part"
    assert tc["function"]["arguments"] == '{"q":"rem"}'


def test_putus_di_tengah_aliran_reset_lalu_gagal_sementara(monkeypatch):
    potongan: list = []
    fake = _FakeRequests(_FakeStreamResp(
        [f"data: {json.dumps(_delta({'content': 'Jawaban separuh'}))}",
         real_requests.ConnectionError("koneksi putus")]))
    monkeypatch.setattr(A, "requests", fake)

    with pytest.raises(A._AIGagalSementara):
        A._post_provider("https://api.test", "k", "m", [], [], 6000,
                         on_delta=potongan.append)
    assert potongan == ["Jawaban separuh", None]   # draf separuh dibuang


def test_reasoning_content_tidak_pernah_diteruskan(monkeypatch):
    """deepseek-v4-flash mengirim reasoning_content — itu nalar, bukan jawaban."""
    potongan: list = []
    data, _ = _panggil(monkeypatch, _sse(
        _delta({"reasoning_content": "user bertanya soal rem, saya harus…"}),
        _delta({"reasoning_content": " lanjut nalar"}),
        _delta({"content": "Pakai kampas rem tipe A."}, finish="stop"),
    ), potongan.append)

    assert potongan == ["Pakai kampas rem tipe A."]
    assert "nalar" not in data["choices"][0]["message"]["content"]


def test_baris_rusak_dan_bukan_data_dilewati(monkeypatch):
    """Komentar keep-alive & JSON rusak tak boleh menjatuhkan giliran."""
    potongan: list = []
    data, _ = _panggil(monkeypatch, [
        ": ping",
        "data: {bukan json}",
        f"data: {json.dumps(_delta({'content': 'Oke.'}, finish='stop'))}",
        "data: [DONE]",
    ], potongan.append)
    assert data["choices"][0]["message"]["content"] == "Oke."
    assert potongan == ["Oke."]


def test_callback_yang_melempar_tak_menjatuhkan_giliran(monkeypatch):
    """Klien putus di tengah jalan → on_delta melempar; jawaban tetap utuh."""
    def _boom(_p):
        raise RuntimeError("klien pergi")

    data, _ = _panggil(monkeypatch, _sse(
        _delta({"content": "Halo."}, finish="stop")), _boom)
    assert data["choices"][0]["message"]["content"] == "Halo."


def test_tanpa_on_delta_tetap_jalur_lama(monkeypatch):
    """Tanpa on_delta: TIDAK ada stream:true, `requests.post` tanpa stream, dan
    hasilnya r.json() apa adanya (perilaku sebelum fitur ini, persis)."""
    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "halo"}, "finish_reason": "stop"}]}

    calls: list = []

    class _Req:
        RequestException = real_requests.RequestException

        def post(self, url, headers=None, json=None, timeout=None):
            calls.append({"payload": json, "timeout": timeout})
            return _Resp()

    monkeypatch.setattr(A, "requests", _Req())
    data = A._post_provider("https://api.test", "k", "m", [], [], 6000)
    assert data["choices"][0]["message"]["content"] == "halo"
    assert "stream" not in calls[0]["payload"]
    assert calls[0]["timeout"] == A._TIMEOUT
