"""Tool `tanya_user`: asisten BERTANYA balik dengan pilihan (kartu di klien).

Permintaan pemilik 2026-07-30: asisten boleh bertanya saat bingung / butuh
kepastian, gaya kartu pilihan seperti Claude. Yang dijaga di sini:

  • Giliran-bertanya BERHENTI di tool itu — tak ada panggilan model lagi (jadi
    giliran bertanya lebih MURAH dari giliran menjawab). Ini juga alasan kenapa
    `reply` harus sudah berisi pertanyaan + opsi sebagai TEKS: klien lama/mobile
    yang belum dirilis tetap berguna, dan log/umpan balik tetap bermakna.
  • Kartu cacat DITOLAK dengan pesan yang menyuruh model lanjut bekerja + sebut
    asumsinya — bukan bertanya setengah jadi.
  • Bertanya tak boleh jadi cara kabur dari kerja: maks SATU kartu per giliran,
    dan tak boleh bertanya dua giliran berturut-turut (pagar anti ping-pong).

Nol panggilan model: `_post_chat` di-stub (pola `test_chat_guard_loop.py`).
"""
from __future__ import annotations

import json

import pytest

from app.services import ai_assistant as ai

USER = {"username": "budi", "role": "pembeli"}
ADMIN = {"username": "agus", "role": "admin"}
KARTU = [{"teks": "Kampas rem posisi mana?", "opsi": ["Depan", "Belakang"]}]


@pytest.fixture(autouse=True)
def bersih():
    ai._TANYA_TERAKHIR.clear()
    yield
    ai._TANYA_TERAKHIR.clear()


def _stub_tool_call(monkeypatch, args: dict, nama: str = "tanya_user"):
    """Model memanggil `nama` sekali per GILIRAN; panggilan berikutnya di giliran
    yang sama menulis jawaban biasa.

    `n` = panggilan dalam giliran ini (test yang menjalankan >1 giliran WAJIB
    me-reset `calls["n"] = 0` di antaranya — tanpa itu giliran kedua tak pernah
    memanggil tool dan test-nya menguji hal yang salah).
    `total` = seluruh panggilan model, tak pernah direset."""
    calls = {"n": 0, "total": 0}

    def fake(messages, tools, max_tokens=6000):
        calls["n"] += 1
        calls["total"] += 1
        if calls["n"] == 1:
            return {"choices": [{"message": {"content": "", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": nama, "arguments": json.dumps(args)},
            }]}, "finish_reason": "tool_calls"}]}
        return {"choices": [{"message": {"content": "Baik, saya lanjut dengan asumsi depan."},
                             "finish_reason": "stop"}]}

    monkeypatch.setattr(ai, "_post_chat", fake)
    return calls


# ── Validasi kartu ──────────────────────────────────────────────────────────

def test_kartu_normal():
    out = ai._t_tanya_user({"pertanyaan": KARTU}, USER)
    assert out["_tanya"] == KARTU


def test_string_tunggal_dimaafkan():
    """Model kadang mengirim string, bukan list of dict — jangan langsung gagal."""
    out = ai._t_tanya_user({"pertanyaan": "Posisi mana?"}, USER)
    # tanpa opsi → kartu tak sah → error yang menyuruh lanjut kerja
    assert "error" in out and "asumsi" in out["error"].lower()


def test_opsi_kurang_dari_dua_ditolak():
    out = ai._t_tanya_user({"pertanyaan": [{"teks": "Mana?", "opsi": ["Depan"]}]}, USER)
    assert "error" in out
    assert "2 opsi" in out["error"]
    assert "lanjutkan bekerja" in out["error"].lower()


def test_opsi_duplikat_dan_lainnya_dibuang():
    """'Lainnya'/'Lewati' disediakan UI — kalau model ikut mengarang, jadi dobel."""
    out = ai._t_tanya_user({"pertanyaan": [{
        "teks": "Mana?", "opsi": ["Depan", "depan", "Lainnya", "Lewati", "Belakang"]}]}, USER)
    assert out["_tanya"][0]["opsi"] == ["Depan", "Belakang"]


def test_opsi_dipotong_empat_dan_pertanyaan_dipotong_tiga():
    q = {"teks": "Mana?", "opsi": ["a", "b", "c", "d", "e", "f"]}
    out = ai._t_tanya_user({"pertanyaan": [q, dict(q), dict(q), dict(q), dict(q)]}, USER)
    assert len(out["_tanya"]) == 3
    assert out["_tanya"][0]["opsi"] == ["a", "b", "c", "d"]


def test_teks_panjang_dipangkas():
    out = ai._t_tanya_user({"pertanyaan": [{"teks": "X" * 400,
                                           "opsi": ["Y" * 200, "Z"]}]}, USER)
    assert len(out["_tanya"][0]["teks"]) <= ai._TANYA_MAKS_TEKS
    assert len(out["_tanya"][0]["opsi"][0]) <= ai._TANYA_MAKS_OPSI_TEKS


def test_opsi_bentuk_dict_dimaafkan():
    out = ai._t_tanya_user({"pertanyaan": [{"teks": "Mana?", "opsi": [
        {"label": "Depan"}, {"label": "Belakang"}]}]}, USER)
    assert out["_tanya"][0]["opsi"] == ["Depan", "Belakang"]


def test_nama_field_alternatif():
    """Model kadang memakai 'question'/'options' (bahasa Inggris)."""
    out = ai._t_tanya_user({"questions": [{"question": "Mana?",
                                           "options": ["Depan", "Belakang"]}]}, USER)
    assert out["_tanya"][0]["opsi"] == ["Depan", "Belakang"]


# ── Teks jawaban (klien lama tetap berguna) ─────────────────────────────────

def test_teks_pertanyaan_memuat_opsi_bernomor():
    teks = ai._teks_pertanyaan(KARTU)
    assert "Kampas rem posisi mana?" in teks
    assert "1. Depan" in teks and "2. Belakang" in teks
    assert "balas langsung" in teks


# ── Alur giliran ────────────────────────────────────────────────────────────

def test_giliran_berhenti_tanpa_panggilan_model_lagi(monkeypatch):
    """Inti penghematan: bertanya = akhir giliran, model TIDAK dipanggil lagi."""
    calls = _stub_tool_call(monkeypatch, {"pertanyaan": KARTU})
    out = ai.chat(USER, [{"role": "user", "content": "kampas rem harga berapa?"}])
    assert calls["n"] == 1, "tak boleh ada _post_chat kedua setelah tanya_user"
    assert out["pertanyaan"] == KARTU
    assert "Kampas rem posisi mana?" in out["reply"]
    assert "1. Depan" in out["reply"]
    assert "tanya_user" in out["tools_used"]


def test_giliran_bertanya_dicatat_outcome_tanya(monkeypatch):
    _stub_tool_call(monkeypatch, {"pertanyaan": KARTU})
    dicatat = {}
    monkeypatch.setattr(ai.ai_chat_log, "log_turn_async",
                        lambda **kw: dicatat.update(kw))
    ai.chat(USER, [{"role": "user", "content": "kampas rem?"}])
    assert dicatat["outcome"] == "tanya"
    assert "tanya_user" in (dicatat["tools_used"] or [])


def test_kartu_cacat_tidak_menghentikan_giliran(monkeypatch):
    """Kartu ditolak → model lanjut menulis jawaban (giliran TIDAK berhenti)."""
    calls = _stub_tool_call(monkeypatch, {"pertanyaan": [{"teks": "Mana?", "opsi": ["satu"]}]})
    out = ai.chat(USER, [{"role": "user", "content": "kampas rem?"}])
    assert calls["n"] >= 2
    assert "pertanyaan" not in out
    assert out["reply"]


def test_hanya_satu_kartu_per_giliran(monkeypatch):
    """Dua tanya_user dalam satu ronde → hanya kartu PERTAMA yang dipakai."""
    def fake(messages, tools, max_tokens=6000):
        return {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "a", "type": "function", "function": {
                "name": "tanya_user",
                "arguments": json.dumps({"pertanyaan": KARTU})}},
            {"id": "b", "type": "function", "function": {
                "name": "tanya_user",
                "arguments": json.dumps({"pertanyaan": [
                    {"teks": "Unit mana?", "opsi": ["A", "B"]}]})}},
        ]}, "finish_reason": "tool_calls"}]}

    monkeypatch.setattr(ai, "_post_chat", fake)
    out = ai.chat(USER, [{"role": "user", "content": "kampas rem?"}])
    assert out["pertanyaan"] == KARTU
    assert "Unit mana?" not in out["reply"]


# ── Pagar anti ping-pong ────────────────────────────────────────────────────

def test_tak_boleh_bertanya_dua_giliran_berturut_turut(monkeypatch):
    calls = _stub_tool_call(monkeypatch, {"pertanyaan": KARTU})
    conv = "conv-1"
    out1 = ai.chat(USER, [{"role": "user", "content": "kampas rem?"}], conversation_id=conv)
    assert "pertanyaan" in out1

    n_awal = calls["total"]
    calls["n"] = 0                      # giliran BARU: model menawarkan kartu lagi
    out2 = ai.chat(USER, [{"role": "user", "content": "yang depan"}], conversation_id=conv)
    assert "pertanyaan" not in out2, "giliran kedua tak boleh bertanya lagi"
    assert calls["total"] > n_awal + 1, "giliran kedua HARUS lanjut menulis jawaban"
    assert out2["reply"]


def test_percakapan_lain_tak_ikut_kena_pagar(monkeypatch):
    """User buka 2 tab: tab kedua tak boleh kena pagar tab pertama."""
    calls = _stub_tool_call(monkeypatch, {"pertanyaan": KARTU})
    assert "pertanyaan" in ai.chat(USER, [{"role": "user", "content": "a"}],
                                   conversation_id="tab-1")
    calls["n"] = 0
    assert "pertanyaan" in ai.chat(USER, [{"role": "user", "content": "b"}],
                                   conversation_id="tab-2")


def test_tanpa_conversation_id_tetap_bisa_bertanya(monkeypatch):
    """Klien lama tanpa conversation_id: pagar dilewati, fitur tetap jalan."""
    calls = _stub_tool_call(monkeypatch, {"pertanyaan": KARTU})
    assert "pertanyaan" in ai.chat(USER, [{"role": "user", "content": "a"}])
    calls["n"] = 0
    assert "pertanyaan" in ai.chat(USER, [{"role": "user", "content": "b"}])


def test_kunci_pagar_memisahkan_user():
    k1 = ai._tanya_kunci({"username": "budi"}, "c1")
    k2 = ai._tanya_kunci({"username": "agus"}, "c1")
    assert k1 and k2 and k1 != k2
    assert ai._tanya_kunci({"username": "budi"}, "") == "", "tanpa conv id → pagar mati"


# ── Pendaftaran ─────────────────────────────────────────────────────────────

def test_tool_terdaftar_untuk_semua_peran():
    assert "tanya_user" in ai._DISPATCH
    for u in (USER, ADMIN):
        nama = {s["function"]["name"] for s in ai._tool_specs(u)}
        assert "tanya_user" in nama
        assert "tanya_user" in ai._allowed_tool_names(u)


def test_spec_melarang_opsi_lainnya_dan_bertanya_berulang():
    """Kalimat pengikat inilah satu-satunya penahan; jangan hilang saat dirapikan."""
    spec = next(s["function"] for s in ai._tool_specs(USER)
                if s["function"]["name"] == "tanya_user")
    d = spec["description"]
    assert "MENGAKHIRI giliranmu" in d
    assert "dua giliran" in d
    assert "Lainnya" in d
    prompt = ai._system_prompt(USER)
    assert "BERTANYA BUKAN PENGGANTI BEKERJA" in prompt
    assert "tanya_user" in prompt
