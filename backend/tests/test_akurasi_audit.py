"""Audit akurasi 2026-07-17 — kunci perilaku baru:

(1) detail_part TANGGA PEMAAF: eksak → basis↔varian ('WG…004' = '…004/2', part
    SAMA per aturan pemilik) → pemaaf pemisah — dan ⛔ ANTI false-positive:
    substring superstring BEDA basis TIDAK dianggap ketemu (jadi saran).
(2) pengganti_part: pengganti di luar katalog Excel tetap dapat stok/harga dari
    indeks Accurate (index_key pemaaf).
(3) Guard ANGKA diperluas: spesifikasi ber-satuan (kg/jam/…) & harga tanpa 'Rp'.
(4) Miss ber-found=False → _tool_failed → _LOOKUP_GAGAL_NOTE disuntik di chat().
"""
import pytest

from app.services import ai_assistant as ai

USER = {"username": "budi", "role": "user"}


def _row(pn, nama="Part Uji", file="UNIT A"):
    return {"part_number": pn, "part_name": nama, "file": file}


@pytest.fixture(autouse=True)
def _tanpa_jaringan(monkeypatch):
    monkeypatch.setattr(ai.accurate, "available", lambda: False)
    monkeypatch.setattr(ai.sims, "get_part_spec", lambda pn: {})
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {})


# ── (1) detail_part — tangga pemaaf ─────────────────────────────────────
def test_detail_eksak(monkeypatch):
    monkeypatch.setattr(ai.part_index, "search_part_number",
                        lambda q: [_row("WG9525160004")])
    r = ai._t_detail_part({"part_number": "WG9525160004"}, USER)
    assert r["found"] is True and "catatan_pn" not in r


def test_detail_basis_ke_varian(monkeypatch):
    # Katalog hanya menyimpan varian '/2' — query PN dasar WAJIB ketemu (part sama).
    monkeypatch.setattr(ai.part_index, "search_part_number",
                        lambda q: [_row("WG9525160004/2")] if "WG9525160004" in q.upper() else [])
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    r = ai._t_detail_part({"part_number": "WG9525160004"}, USER)
    assert r["found"] is True
    assert "catatan_pn" in r and "/2" in r["catatan_pn"]


def test_detail_substring_bukan_false_positive(monkeypatch):
    # Query parsial; kandidat substring BERBEDA basis → ⛔ jangan diaku ketemu.
    monkeypatch.setattr(ai.part_index, "search_part_number",
                        lambda q: [_row("WG9925530022"), _row("WG9925535001")])
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(ai.part_index, "smart_pn_search", lambda t: ([], None))
    monkeypatch.setattr(ai.part_index, "suggest_pns", lambda t: [])
    miss = []
    monkeypatch.setattr(ai.search_log, "record_miss",
                        lambda q, m, s: miss.append((q, s)))
    r = ai._t_detail_part({"part_number": "WG99255300"}, USER)
    assert r["found"] is False
    assert [s["part_number"] for s in r["saran_mungkin_maksud"]] == [
        "WG9925530022", "WG9925535001"]
    assert miss and miss[0][1] == "detail_part"


def test_detail_o_vs_nol(monkeypatch):
    monkeypatch.setattr(ai.part_index, "search_part_number", lambda q: [])
    monkeypatch.setattr(ai.part_index, "smart_pn_search", lambda t: ([], None))
    monkeypatch.setattr(ai.part_index, "suggest_pns", lambda t: [])
    monkeypatch.setattr(ai.part_index, "rows_for_pns",
                        lambda pns: ({"1000076563": _row("1000076563")}
                                     if "1000076563" in pns else {}))
    monkeypatch.setattr(ai.search_log, "record_miss", lambda *a: None)
    r = ai._t_detail_part({"part_number": "1O00076563"}, USER)
    assert r["found"] is True and "O vs angka 0" in r["catatan_pn"]


def test_detail_pengganti_terlampir(monkeypatch):
    monkeypatch.setattr(ai.part_index, "search_part_number",
                        lambda q: [_row("WG9100443050")])
    monkeypatch.setattr(ai.sims, "equivalents_for",
                        lambda pn: {"digantikan_oleh": [{"pn": "WG9100443051", "nama": "Kampas baru"}]})
    r = ai._t_detail_part({"part_number": "WG9100443050"}, USER)
    assert r["found"] and r["pengganti"][0]["pn"] == "WG9100443051"
    assert "info_pengganti" in r


# ── (2) pengganti_part — stok via Accurate utk pengganti di luar katalog ─
def test_pengganti_stok_dari_accurate(monkeypatch):
    monkeypatch.setattr(ai.sims, "get_part_equivalents",
                        lambda pn: {"digantikan_oleh": [{"pn": "1000076563", "nama": "Filter baru"}],
                                    "menggantikan": []})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, r: {})
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(ai.accurate, "available", lambda: True)
    monkeypatch.setattr(ai.accurate, "stock_full",
                        lambda pn: {"available_to_sell": 7.0, "unit": "PCS", "price": 250000,
                                    "name": "FILTER", "per_gudang": []})
    # pengganti_part kini juga menanyakan status jual RESMI SIMS (isSale) —
    # 1 HTTP live per PN. Di test WAJIB di-mock (tak ada jaringan nyata).
    monkeypatch.setattr(ai.sims, "status_jual", lambda pn: None)
    r = ai._t_pengganti_part({"part_number": "612600081334"}, USER)
    row = r["digantikan_oleh"][0]
    assert row["stok_total"].startswith("7") and row.get("harga_lokal")
    assert "catatan" not in row  # TIDAK lagi dicap 'belum ada di katalog'


# ── (3) guard angka diperluas ───────────────────────────────────────────
def test_claimed_nums_spesifikasi_dan_harga_polos():
    c = ai._claimed_nums("Berat 12,5 kg; interval 500 jam; harganya 1.500.000; "
                         "stok 3 pcs; Rp 250.000; panjang 45 cm")
    assert {"125", "500", "1500000", "3", "250000", "45"} <= c


# ── (4) miss found=False → note anti-karang disuntik di chat() ──────────
def test_lookup_gagal_note_untuk_cari_part_kosong(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_allowed_tool_names", lambda user, sheet_id="": {"cari_part"})
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai, "_prefetch_epc_rangka", lambda history: None)
    monkeypatch.setattr(ai, "_run_tool",
                        lambda name, args, user, sheet_id="": {"found": False,
                                                               "jumlah_part_unik": 0})
    seen = {"note": False}
    seq = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "cari_part",
                                      "arguments": '{"query":"zzz"}'}},
        ]}, "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "Part itu tidak ditemukan di katalog."},
                      "finish_reason": "stop"}]},
    ]
    calls = {"n": 0}

    def fake(messages, tools, max_tokens=6000):
        if any("CATATAN SISTEM" in (m.get("content") or "")
               for m in messages if m.get("role") == "user"):
            seen["note"] = True
        c = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return c

    monkeypatch.setattr(ai, "_post_chat", fake)
    out = ai.chat(USER, [{"role": "user", "content": "ada part zzz?"}])
    assert seen["note"], "_LOOKUP_GAGAL_NOTE tidak disuntik utk miss cari_part"
    assert "tidak ditemukan" in out["reply"]
