"""Guard klaim file Excel palsu + eksekusi buat_excel bocor saat ronde habis.

Kasus nyata (probe 2026-07-09, 'buatkan data semua lampu howo ada stok, bentuk
excel'): model menghabiskan seluruh ronde tool untuk mengumpulkan data, panggilan
buat_excel-nya bocor sebagai teks di ronde 9 (jatah habis → tool dimatikan) dan
DIBUANG, lalu jawaban akhir MENGKLAIM 'File Excel sudah siap di bawah 👇' padahal
kartu unduh tidak pernah dibuat. DeepSeek DI-MOCK — tanpa network.
"""
import pytest

from app.services import ai_assistant as ai

USER = {"username": "tester", "role": "user"}


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())


def _stub_model(monkeypatch, seq):
    calls = {"n": 0, "last_messages": None}

    def fake(messages, tools, max_tokens=6000):
        calls["last_messages"] = messages
        c = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return {"choices": [{"message": {"content": c}, "finish_reason": "stop"}]}

    monkeypatch.setattr(ai, "_post_chat", fake)
    return calls


def test_klaim_excel_tanpa_kartu_dikoreksi(monkeypatch):
    calls = _stub_model(monkeypatch, [
        "Berikut datanya. File Excel sudah siap di bawah 👇",
        "Berikut datanya. Mau saya buatkan file Excel-nya?",
    ])
    out = ai.chat(USER, [{"role": "user", "content": "buatkan data lampu bentuk excel"}])
    assert calls["n"] == 2                       # 1 klaim palsu + 1 koreksi
    assert "sudah siap" not in out["reply"]
    assert "Mau saya buatkan" in out["reply"]
    # pesan koreksi terkirim ke model
    assert any("MENGKLAIM file Excel" in (m.get("content") or "")
               for m in calls["last_messages"] if m.get("role") == "user")


def test_tawaran_excel_bukan_klaim_tidak_dikoreksi(monkeypatch):
    calls = _stub_model(monkeypatch, ["Ini datanya. Mau saya buatkan file Excel-nya?"])
    out = ai.chat(USER, [{"role": "user", "content": "data lampu howo dong"}])
    assert calls["n"] == 1                       # tanpa retry
    assert out["reply"].startswith("Ini datanya.")


def test_buat_excel_bocor_saat_ronde_habis_tetap_dieksekusi(monkeypatch):
    # Jatah ronde tool langsung habis → dulu panggilan bocor dibuang begitu saja.
    monkeypatch.setattr(ai, "_MAX_TOOL_ROUNDS", 0)
    ran = {}

    def fake_run_tool(name, args, user, sheet_id=""):
        ran["name"] = name
        return {"found": True, "export_id": "x1", "filename": "data.xlsx",
                "judul": "Data Lampu", "jumlah_baris": 2}

    monkeypatch.setattr(ai, "_run_tool", fake_run_tool)
    leaked = ('<tool_calls><invoke name="buat_excel">'
              '<parameter name="judul">Data Lampu</parameter>'
              '</invoke>')
    calls = _stub_model(monkeypatch, [leaked, "File Excel **Data Lampu** siap diunduh di bawah."])
    out = ai.chat(USER, [{"role": "user", "content": "excel data lampu dong"}])
    assert ran.get("name") == "buat_excel"       # leaked call tetap dijalankan
    assert "buat_excel" in out["tools_used"]
    assert out["excel_exports"] and out["excel_exports"][0]["id"] == "x1"
    assert "siap diunduh" in out["reply"]        # klaim kini SAH (kartu ada)
    assert calls["n"] == 2


def test_tool_lain_bocor_saat_ronde_habis_tetap_dibuang(monkeypatch):
    # Hanya buat_excel yang diistimewakan; tool data lain saat ronde habis tetap
    # tidak dijalankan (paksa jawaban final), sesuai desain lama.
    monkeypatch.setattr(ai, "_MAX_TOOL_ROUNDS", 0)
    ran = {}
    monkeypatch.setattr(ai, "_run_tool",
                        lambda name, args, user, sheet_id="": ran.setdefault("name", name) or {})
    leaked = ('<invoke name="cari_part"><parameter name="query">lampu</parameter></invoke>'
              " Sebentar ya.")
    _stub_model(monkeypatch, [leaked, "Data lampu tidak saya temukan."])
    out = ai.chat(USER, [{"role": "user", "content": "cari lampu"}])
    assert "name" not in ran
    assert "cari_part" not in out["tools_used"]
