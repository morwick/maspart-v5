"""Diagnosa terpandu: wawancara gejala → penyebab berperingkat + bukti klaim.

Yang dijaga:
  • keluhan yang sudah menjawab pertanyaan TIDAK ditanyakan lagi;
  • kartu hanya SEKALI per (user, keluhan, sistem) — panggilan ulang tanpa
    jawaban lanjut dengan asumsi, bukan ping-pong;
  • jawaban kartu (format klien: "<pertanyaan> <opsi>\\n…") terparse dan
    mengubah peringkat;
  • bukti klaim ikut, tanpa nomor WO untuk akun tanpa gerbang garansi;
  • giliran chat berhenti di kartu (nol panggilan model kedua) dan giliran
    kedua menghasilkan jawaban — model di-stub, nol jaringan.
"""
from __future__ import annotations

import gzip
import json

import pytest

from app.services import ai_assistant as ai
from app.services import diagnosa_terpandu as dt
from app.services import knowledge_util, sinonim, warranty_kasus, warranty_profil

USER = {"username": "budi", "role": "pembeli"}
ADMIN = {"username": "agus", "role": "admin"}

_KLAIM = {
    "dibuat": "2026-01-01", "klaim": [
        {"no_wo": "WO1", "frame": "AA111111", "km": 15000, "status_code": "s-ro-status-js",
         "gejala": "Suspension rubber mount assembly is broke", "tindakan": "Replace rubber mount",
         "catatan": "", "jasa": [{"nama": "Replace rubber mount"}],
         "part": [{"pn": "AZ0000000001", "nama": "Suspension rubber mount assembly", "nama_cn": "",
                   "mode_gagal": "Cracking", "klaim_berulang": False},
                  {"pn": "AZ0000000002", "nama": "Rear plate spring bracket", "nama_cn": "",
                   "mode_gagal": "Break", "klaim_berulang": False}]},
        {"no_wo": "WO2", "frame": "AA111112", "km": 20000, "status_code": "s-ro-status-js",
         "gejala": "rubber mount cracked", "tindakan": "Replace", "catatan": "", "jasa": [],
         "part": [{"pn": "AZ0000000001", "nama": "Suspension rubber mount assembly", "nama_cn": "",
                   "mode_gagal": "Break", "klaim_berulang": False}]},
        {"no_wo": "WO3", "frame": "AA111113", "km": 9000, "status_code": "s-ro-status-zf",   # DIBATALKAN
         "gejala": "rubber mount", "tindakan": "", "catatan": "", "jasa": [],
         "part": [{"pn": "AZ0000000001", "nama": "Suspension rubber mount assembly", "nama_cn": "",
                   "mode_gagal": "Damage", "klaim_berulang": False}]},
        {"no_wo": "WO4", "frame": "AA111114", "km": 30000, "status_code": "s-ro-status-js",
         "gejala": "turbocharger oil leakage, low power", "tindakan": "Replace turbo",
         "catatan": "", "jasa": [],
         "part": [{"pn": "TB0000000001", "nama": "Turbocharger", "nama_cn": "",
                   "mode_gagal": "Oil Leakage", "klaim_berulang": False}]},
    ]}


@pytest.fixture(autouse=True)
def dataset(tmp_path, monkeypatch):
    """Dataset klaim sintetis kecil + kamus sinonim minimal; tanpa file nyata."""
    d = tmp_path / "warranty"
    d.mkdir()
    with gzip.open(d / "warranty_klaim.json.gz", "wt", encoding="utf-8") as f:
        json.dump(_KLAIM, f)

    class _S:
        data_path = tmp_path

    monkeypatch.setattr(warranty_kasus, "get_settings", lambda: _S())
    knowledge_util._LOAD_CACHE.clear()
    warranty_kasus._index_cache.clear()
    warranty_profil._cache.clear()
    monkeypatch.setattr(sinonim, "entries", lambda: [
        {"grup": "gejala:karet-per", "triggers": ["karet dudukan per", "karet per"],
         "keywords": ["rubber mount"]},
        {"grup": "gejala:turbo", "triggers": ["turbo"], "keywords": ["turbocharger"]},
    ])
    dt._reset()
    ai._TANYA_TERAKHIR.clear()
    yield
    dt._reset()
    ai._TANYA_TERAKHIR.clear()


# ── Profil klaim per komponen ───────────────────────────────────────────────

def test_profil_agregat_dan_diganti_bersama():
    r = warranty_profil.profil("karet dudukan per")
    assert r["found"]
    k = r["komponen"][0]
    assert k["nama"] == "suspension rubber mount assembly"
    assert k["klaim"] == 2, "klaim DIBATALKAN wajib dibuang"
    assert k["km_median"] == 17500
    assert [m["mode"] for m in k["mode_rusak"]] == ["Cracking", "Break"] or \
           {m["mode"] for m in k["mode_rusak"]} == {"Cracking", "Break"}
    assert k["diganti_bersama"][0]["nama"] == "rear plate spring bracket"
    assert k["diganti_bersama"][0]["pn"] == "AZ0000000002"
    assert k["diganti_bersama"][0]["persen"] == 50
    assert "no_wo" not in json.dumps(r) and "frame" not in json.dumps(r)


def test_profil_lewat_pn():
    r = warranty_profil.profil("AZ0000000002")
    assert r["found"] and r["komponen"][0]["nama"] == "rear plate spring bracket"


def test_profil_tak_ada_jujur():
    r = warranty_profil.profil("busi pijar")
    assert r["found"] is False
    assert "BELUM PERNAH diklaim" in r["catatan"]


# ── Mesin wawancara ─────────────────────────────────────────────────────────

def test_keluhan_jelas_langsung_hasil_tanpa_kartu():
    """Keluhan sudah menjawab semua pertanyaan sistem → tak ada kartu."""
    r = dt.jalankan("kolong bunyi duk kalau jalan jelek, muatan penuh", username="u")
    assert r["tahap"] == "hasil" and "_tanya" not in r
    assert r["sistem"] == "suspensi_sasis"
    assert r["hasil"][0]["penyebab"].startswith("Karet dudukan suspensi")
    bukti = r["hasil"][0]["part_terkait"][0]
    assert bukti["klaim"] == 2 and "rear plate spring bracket" in bukti["diganti_bersama"]
    assert r["pertanyaan_tak_terjawab"] == []


def test_keluhan_kabur_menampilkan_kartu_lalu_jawaban_mengubah_peringkat():
    r1 = dt.jalankan("mesin ngempos", username="u")
    assert r1["tahap"] == "tanya" and r1["sistem"] == "mesin"
    assert 1 <= len(r1["_tanya"]) <= 3
    assert r1["_tanya_bebas_pagar"] is True
    teks = "\n".join(f"{q['teks']} {q['opsi'][0]}" for q in r1["_tanya"])   # format klien
    # jawaban: lampu MIL + asap hitam → pasokan udara/turbo naik, dan bukti turbo ikut
    jawab = "Ada lampu peringatan yang menyala di panel? Tidak ada lampu\nAsap knalpotnya bagaimana? Asap hitam\nKapan gejalanya paling terasa? Saat beban / tanjakan"
    r2 = dt.jalankan("mesin ngempos", jawaban=jawab, username="u")
    assert r2["tahap"] == "hasil"
    assert r2["jawaban_dipakai"] == {"lampu": "Tidak ada lampu", "asap": "Asap hitam",
                                     "kapan": "Saat beban / tanjakan"}
    assert r2["hasil"][0]["penyebab"].startswith("Pasokan udara kurang")
    turbo = [p for p in r2["hasil"][0]["part_terkait"] if p["nama"] == "turbocharger"]
    assert turbo and turbo[0]["klaim"] == 1 and turbo[0]["mode_rusak"] == ["Oil Leakage"]
    assert list(r2.keys())[-1] == "catatan"
    assert teks  # format klien terbentuk (dipakai di test giliran)


def test_kartu_hanya_sekali_per_keluhan():
    r1 = dt.jalankan("mesin ngempos", username="u")
    assert r1["tahap"] == "tanya"
    r2 = dt.jalankan("mesin ngempos", username="u")          # model lupa meneruskan jawaban
    assert r2["tahap"] == "hasil", "tak boleh bertanya ulang → lanjut dengan asumsi"
    assert r2["pertanyaan_tak_terjawab"]
    r3 = dt.jalankan("mesin ngempos", username="lain")       # user lain tetap ditanya
    assert r3["tahap"] == "tanya"


def test_lewati_langsung_hasil():
    r = dt.jalankan("mesin ngempos", jawaban="(lewati) Lanjutkan dengan asumsi terbaik", username="u")
    assert r["tahap"] == "hasil" and r["pertanyaan_tak_terjawab"]


def test_sistem_tak_dikenal_pakai_pemilih_lalu_tanya_sistem():
    r1 = dt.jalankan("unit bermasalah", username="u")
    assert r1["tahap"] == "tanya" and r1["sistem"] is None
    assert r1["_tanya"][0]["teks"] == dt._PILIH_SISTEM_TEKS
    r2 = dt.jalankan("unit bermasalah", jawaban="Gejalanya paling terasa di bagian mana? Rem / angin", username="u")
    assert r2["tahap"] == "tanya" and r2["sistem"] == "angin_rem"
    r3 = dt.jalankan("unit bermasalah", jawaban="Apa yang terjadi saat mengerem? Rem kurang pakem / blong", username="u")
    assert r3["tahap"] == "hasil" and r3["hasil"][0]["penyebab"].startswith("Kampas / tromol")


def test_selalu_ada_pembanding():
    r = dt.jalankan("stir berat", username="u")
    assert r["tahap"] == "hasil"
    assert len(r["hasil"]) >= 3
    assert r["hasil"][0]["cocok_keluhan"] is True and r["hasil"][-1]["cocok_keluhan"] is False


def test_sistem_dipaksa_lewat_argumen():
    r = dt.jalankan("bunyi", sistem="gardan", username="u")
    assert r["sistem"] == "gardan_penggerak"


def test_opsi_kartu_valid_untuk_klien():
    """Kontrak tanya_user: 2–4 opsi, teks ≤120, tanpa 'Lainnya'/'Lewati'."""
    for kode, s in dt._SISTEM.items():
        for q in s["tanya"]:
            assert 2 <= len(q["opsi"]) <= 4, (kode, q["id"])
            assert len(q["teks"]) <= 120
            assert all(o.lower() not in ai._TANYA_OPSI_TERLARANG for o in q["opsi"])
            for (qid, opsi) in {k for d in s["sebab"] for k in d["jawab"]}:
                pass
        # tiap bobot jawab merujuk opsi yang benar-benar ada
        opsi_sah = {(q["id"], o) for q in s["tanya"] for o in q["opsi"]}
        for d in s["sebab"]:
            for k in d["jawab"]:
                assert k in opsi_sah, (kode, d["id"], k)


# ── Gerbang & wiring asisten ───────────────────────────────────────────────

def test_tersedia_semua_peran_dan_wo_hanya_garansi():
    for u in (USER, ADMIN, {"username": "s", "role": "staff"}):
        names = [s["function"]["name"] for s in ai._tool_specs(u)]
        assert "diagnosa_terpandu" in names and "part_klaim_terkait" in names
    # keluhan yang juga cocok di teks klaim sintetis ('rubber mount' via sinonim)
    keluhan = "karet dudukan per pecah, kolong bunyi duk, muatan penuh"
    r_p = ai._run_tool("diagnosa_terpandu", {"keluhan": keluhan}, USER)
    r_a = ai._run_tool("diagnosa_terpandu", {"keluhan": keluhan}, ADMIN)
    assert r_p["tahap"] == "hasil" and r_a["tahap"] == "hasil"
    assert "kasus_contoh" not in (r_p.get("klaim_serupa") or {})
    assert "WO1" not in json.dumps(r_p)
    assert "kasus_contoh" in (r_a.get("klaim_serupa") or {})


def test_handler_toleran_alias_dan_jawaban_list():
    r = ai._t_diagnosa_terpandu({"gejala": "mesin ngempos", "jawaban": ["Asap hitam", "Tidak ada lampu"]}, USER)
    assert r["tahap"] == "hasil" and r["jawaban_dipakai"]["asap"] == "Asap hitam"
    r = ai._t_part_klaim_terkait({"pn": "AZ0000000001"}, USER)
    assert r["found"] and r["komponen"][0]["klaim"] == 2


def _stub(monkeypatch, args_per_giliran: list[dict]):
    """Giliran ke-i: model memanggil diagnosa_terpandu dengan args ke-i, lalu
    (bila giliran tak berhenti) menulis jawaban biasa."""
    st = {"giliran": -1, "n": 0, "total": 0}

    def fake(messages, tools, max_tokens=6000):
        st["n"] += 1
        st["total"] += 1
        if st["n"] == 1:
            return {"choices": [{"message": {"content": "", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "diagnosa_terpandu",
                             "arguments": json.dumps(args_per_giliran[st["giliran"]])}}]},
                "finish_reason": "tool_calls"}]}
        return {"choices": [{"message": {"content": "Penyebab teratas: karet dudukan suspensi."},
                             "finish_reason": "stop"}]}

    monkeypatch.setattr(ai, "_post_chat", fake)

    def giliran_baru():
        st["giliran"] += 1
        st["n"] = 0
    return st, giliran_baru


def test_dua_giliran_lewat_chat_loop(monkeypatch):
    st, baru = _stub(monkeypatch, [
        {"keluhan": "mesin ngempos"},
        {"keluhan": "mesin ngempos", "jawaban": "Asap knalpotnya bagaimana? Asap hitam"},
    ])
    conv = "conv-diag-1"
    baru()
    out1 = ai.chat(USER, [{"role": "user", "content": "mesin ngempos"}], conversation_id=conv)
    assert st["n"] == 1, "kartu = akhir giliran, model tak dipanggil lagi"
    assert out1["pertanyaan"] and "Asap" in json.dumps(out1["pertanyaan"], ensure_ascii=False)
    assert "mesin ngempos" in out1["reply"]
    assert "diagnosa_terpandu" in out1["tools_used"]

    baru()
    out2 = ai.chat(USER, [{"role": "user", "content": "mesin ngempos"},
                          {"role": "assistant", "content": out1["reply"]},
                          {"role": "user", "content": "Asap knalpotnya bagaimana? Asap hitam"}],
                   conversation_id=conv)
    assert "pertanyaan" not in out2, "giliran kedua harus MENJAWAB, bukan bertanya lagi"
    assert st["n"] >= 2 and out2["reply"]
