"""AJARKAN LEWAT CHAT — admin mengajari asisten dari percakapan.

Alur yang dijaga di sini: "catat ya: …" → asisten menyusun DRAF → kartu
konfirmasi [Simpan][Perbaiki dulu][Batal] → tersimpan ke store Pengetahuan AI
yang sudah ada (add_dokumen + antre indexing), berlabel `asal="chat"`.

Yang paling gampang rusak & karena itu diuji ketat:

  • ⛔ Draf TIDAK menulis apa pun ke store — hanya kartu; user harus menyetujui.
  • Isi yang tersimpan = draf yang DITAHAN SERVER, bukan argumen giliran
    "Simpan". Balasan kartu adalah teks user biasa, jadi kalau isinya diambil
    dari argumen model, yang masuk store bisa beda dari yang disetujui user.
  • Jalur kartu `_tanya` MEMBUANG tulisan model → draf hanya sampai ke user
    lewat `_tanya_pengantar` yang di-prepend ke reply.
  • Kartu konfirmasi BEBAS pagar anti ping-pong (alur "Perbaiki dulu" butuh
    kartu kedua), tanpa melemahkan pagar itu untuk tanya_user.
  • Angka stok/harga & rujukan gantung ditolak: entri ini dibaca berbulan-bulan
    kemudian tanpa percakapannya.

Nol panggilan model: `_post_chat` di-stub (pola `test_tanya_user.py`); store
dialihkan ke tmp_path (pola `test_pengetahuan_store.py`); `pengetahuan_index.antre`
di-stub no-op supaya tak ada thread indexing yang jalan.
"""
from __future__ import annotations

import json

import pytest

from app.services import ai_assistant as ai
from app.services import knowledge_util, pengetahuan, pengetahuan_index, sinonim

ADMIN = {"username": "agus", "role": "admin"}
STAF = {"username": "budi", "role": "user"}          # dicentang ai_mengajar
POLOS = {"username": "polos", "role": "user"}        # tanpa centang
PEMBELI = {"username": "toko", "role": "pembeli"}

JUDUL = "Istilah lapangan cucuk per"
ISI = "Cucuk per adalah istilah lapangan untuk bushing spring depan truk Howo."
KK = ["cucuk per", "bushing", "spring"]


@pytest.fixture(autouse=True)
def bersih(tmp_path, monkeypatch):
    """Store ke tmp_path + semua state per-percakapan direset.

    `_HAY_CACHE`/`_LOAD_CACHE` dibersihkan karena beberapa test menulis isi
    berbeda pada path yang sama dalam hitungan milidetik (cache per-mtime bisa
    tak melihat perubahannya)."""
    monkeypatch.setattr(pengetahuan, "_dir", lambda: tmp_path)
    monkeypatch.setattr(sinonim, "entries", lambda: [])   # ekspansi tak mencemari skor
    antrean: list[str] = []
    monkeypatch.setattr(pengetahuan_index, "antre", lambda dok_id: antrean.append(dok_id))
    knowledge_util._LOAD_CACHE.clear()
    pengetahuan._HAY_CACHE.update(mtime=None, rows=None)
    ai._TANYA_TERAKHIR.clear()
    ai._AJAR_DRAF.clear()
    yield antrean
    knowledge_util._LOAD_CACHE.clear()
    pengetahuan._HAY_CACHE.update(mtime=None, rows=None)
    ai._TANYA_TERAKHIR.clear()
    ai._AJAR_DRAF.clear()


@pytest.fixture
def perms(monkeypatch):
    """Hanya `budi` yang dicentang 'ai_mengajar' (kind `asisten` fail-closed)."""
    def fake(kind, u, r):
        if kind == "asisten":
            return ["ai_mengajar"] if u == "budi" else []
        return ["col_stok", "col_harga"]
    monkeypatch.setattr("app.services.permissions.effective", fake)


def _draf(user=ADMIN, cid="c1", judul=JUDUL, isi=ISI, kata_kunci=None, **extra):
    return ai._t_ajarkan_pengetahuan(
        {"aksi": "draf", "judul": judul, "isi": isi,
         "kata_kunci": KK if kata_kunci is None else kata_kunci,
         "_cid": cid, **extra}, user)


def _seed_chunk(**kw):
    """Satu chunk terindeks di store (pola _chunk test_pengetahuan_store)."""
    d = {
        "id": "lama#0001", "dok_id": "lama", "judul": "Kamus istilah bengkel",
        "judul_id": "Kamus istilah bengkel", "kata_kunci": [], "ringkasan": "",
        "teks": "", "tabel": [], "gambar_ref": [], "sumber": "teks-admin",
        "halaman": 0, "tipe": "teks", "untuk_pembeli": False, "dicari": True,
        "kode": [],
    }
    d.update(kw)
    pengetahuan._save_chunks([d])
    knowledge_util._LOAD_CACHE.clear()
    pengetahuan._HAY_CACHE.update(mtime=None, rows=None)


# ── stub model ──────────────────────────────────────────────────────────────

def _stub(monkeypatch, balasan="Baik, sudah saya kerjakan."):
    """Model memanggil SATU tool di panggilan pertama tiap giliran (bila
    `cur['call']` diisi), lalu menulis jawaban biasa.

    `cur['n']` WAJIB direset antar-giliran (lewat `_giliran`) — tanpa itu giliran
    kedua tak pernah memanggil tool dan test-nya menguji hal yang salah."""
    cur = {"call": None, "n": 0, "total": 0, "balasan": balasan}

    def fake(messages, tools, max_tokens=6000):
        cur["n"] += 1
        cur["total"] += 1
        if cur["n"] == 1 and cur["call"]:
            nama, args = cur["call"]
            return {"choices": [{"message": {"content": "", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": nama, "arguments": json.dumps(args)},
            }]}, "finish_reason": "tool_calls"}]}
        return {"choices": [{"message": {"content": cur["balasan"]},
                             "finish_reason": "stop"}]}

    monkeypatch.setattr(ai, "_post_chat", fake)
    return cur


def _giliran(cur, nama: str = "", args: dict | None = None):
    cur["call"] = (nama, args or {}) if nama else None
    cur["n"] = 0


# ── 1. draf tampil di jawaban, store BELUM tersentuh ────────────────────────

def test_draf_tampil_di_reply_dan_store_masih_kosong(monkeypatch):
    cur = _stub(monkeypatch)
    _giliran(cur, "ajarkan_pengetahuan",
             {"aksi": "draf", "judul": JUDUL, "isi": ISI, "kata_kunci": KK})
    out = ai.chat(ADMIN, [{"role": "user", "content": f"catat ya: {ISI}"}],
                  conversation_id="c1")
    assert JUDUL in out["reply"] and ISI in out["reply"], "draf WAJIB terlihat user"
    assert out["pertanyaan"][0]["opsi"] == ["Simpan", "Perbaiki dulu", "Batal"]
    assert "Simpan pengetahuan ini?" in out["reply"]      # kartu juga sbg teks
    assert pengetahuan.load_dokumen() == [], "draf TIDAK boleh menulis ke store"
    assert cur["n"] == 1, "giliran berhenti di kartu (hemat satu panggilan model)"


def test_draf_tersimpan_di_server_bukan_di_model():
    out = _draf()
    assert out["_tanya_bebas_pagar"] is True
    draf = ai._AJAR_DRAF["agus|c1"]
    assert draf["judul"] == JUDUL and draf["isi"] == ISI


# ── 2. Simpan memakai draf SERVER ───────────────────────────────────────────

def test_simpan_pakai_draf_server_dan_diantre(bersih):
    antrean = bersih
    _draf()
    # Model mengarang isi baru saat user menekan "Simpan" — WAJIB diabaikan.
    out = ai._t_ajarkan_pengetahuan(
        {"aksi": "simpan", "judul": "Judul karangan",
         "isi": "Isi karangan yang tak pernah dilihat user.", "_cid": "c1"}, ADMIN)
    assert out["tersimpan"] is True
    rows = pengetahuan.load_dokumen()
    assert len(rows) == 1
    d = rows[0]
    assert d["judul"] == JUDUL
    assert d["teks_admin"] == ISI, "isi tersimpan = draf server, bukan argumen model"
    assert d["oleh"] == "agus"
    assert d["asal"] == "chat"
    assert d["pakai_ai"] is False, "pengayaan LLM ulang = bakar saldo & memperlambat"
    assert d["untuk_pembeli"] is False
    assert d["tag"] == KK
    assert antrean == [d["id"]], "entri baru WAJIB diantre indexing"
    assert "agus|c1" not in ai._AJAR_DRAF, "draf dihapus setelah tersimpan"


def test_simpan_dua_kali_tak_membuat_entri_dobel():
    _draf()
    ai._t_ajarkan_pengetahuan({"aksi": "simpan", "_cid": "c1"}, ADMIN)
    ulang = ai._t_ajarkan_pengetahuan({"aksi": "simpan", "_cid": "c1"}, ADMIN)
    assert ulang.get("found") is False
    assert len(pengetahuan.load_dokumen()) == 1


def test_batal_membuang_draf_tanpa_menulis():
    _draf()
    out = ai._t_ajarkan_pengetahuan({"aksi": "batal", "_cid": "c1"}, ADMIN)
    assert out["dibatalkan"] is True
    assert "agus|c1" not in ai._AJAR_DRAF
    assert pengetahuan.load_dokumen() == []


# ── 3. Angka stok/harga ditolak, angka TEKNIS lolos ─────────────────────────

@pytest.mark.parametrize("isi", [
    "Stok minimal cucuk per di gudang 5 pcs.",
    "Harga cucuk per Rp 1.200.000 per pcs.",
    "Modalnya 350 CNY per batang.",
    "Sisa stoknya tinggal 2 batang.",
])
def test_angka_stok_harga_ditolak(isi):
    out = _draf(isi=isi)
    assert "error" in out and "STOK/HARGA" in out["error"]
    assert not ai._AJAR_DRAF, "draf haram tak boleh ikut tersimpan"


@pytest.mark.parametrize("isi", [
    "Torsi baut roda depan Howo 450 Nm, dikencangkan menyilang.",
    "Kapasitas oli gardan belakang 12 liter, ganti tiap 20000 km.",
    "Diameter bushing spring depan 32 mm.",
])
def test_angka_teknis_lolos(isi):
    out = _draf(isi=isi)
    assert out.get("found") is True, out.get("error")
    assert ai._AJAR_DRAF["agus|c1"]["isi"] == isi


# ── 4. Cek kembar ───────────────────────────────────────────────────────────

def test_kembar_menawarkan_perbarui_atau_baru():
    _seed_chunk(kata_kunci=["cucuk per", "bushing", "spring", "depan", "howo",
                            "istilah", "lapangan", "truk"],
                teks="Cucuk per = bushing spring depan.")
    out = _draf()
    kartu = out["_tanya"][0]
    assert kartu["opsi"] == ["Perbarui entri lama", "Simpan sebagai entri baru", "Batal"]
    assert "Kamus istilah bengkel" in out["_tanya_pengantar"]
    assert ai._AJAR_DRAF["agus|c1"]["mirip"]["dok_id"] == "lama"


def test_skor_di_bawah_ambang_tetap_kartu_normal():
    _seed_chunk(kata_kunci=["cucuk per", "bushing", "spring", "depan", "howo",
                            "istilah", "lapangan", "truk"],
                teks="Cucuk per = bushing spring depan.")
    out = _draf(judul="Interval ganti oli gardan",
                isi="Oli gardan truk diganti tiap 20000 km.",
                kata_kunci=["oli gardan"])
    assert out["_tanya"][0]["opsi"] == ["Simpan", "Perbaiki dulu", "Batal"]
    assert ai._AJAR_DRAF["agus|c1"]["mirip"] is None


def test_perbarui_menimpa_entri_lama_dan_reindex(bersih):
    """`teks_admin` ADA di whitelist update_dokumen → cabang perbarui nyata
    (bukan sekadar arahan). ⛔ entri lama tak pernah dihapus otomatis."""
    antrean = bersih
    lama = pengetahuan.add_dokumen("Kamus istilah bengkel", teks_admin="isi lama",
                                   oleh="admin")
    _seed_chunk(dok_id=lama["id"], id=f"{lama['id']}#0001",
                kata_kunci=["cucuk per", "bushing", "spring", "depan", "howo",
                            "istilah", "lapangan", "truk"],
                teks="Cucuk per = bushing spring depan.")
    antrean.clear()
    _draf()
    out = ai._t_ajarkan_pengetahuan({"aksi": "perbarui", "_cid": "c1"}, ADMIN)
    assert out["diperbarui"] is True
    rows = pengetahuan.load_dokumen()
    assert len(rows) == 1, "entri lama DIPERBARUI, bukan ditambah/dihapus"
    assert rows[0]["teks_admin"] == ISI
    assert rows[0]["asal"] == "chat"
    assert rows[0]["status"] == "antre"
    assert antrean == [lama["id"]]


def test_perbarui_tanpa_kembar_dijawab_jujur():
    _draf()
    out = ai._t_ajarkan_pengetahuan({"aksi": "perbarui", "_cid": "c1"}, ADMIN)
    assert out["found"] is False
    assert "entri BARU" in out["catatan"]
    assert pengetahuan.load_dokumen() == []


# ── 5. Alur "Perbaiki dulu" (kartu kedua bebas pagar) ───────────────────────

def test_draf_kedua_menimpa_dan_kartunya_tetap_tampil(monkeypatch):
    cur = _stub(monkeypatch)
    _giliran(cur, "ajarkan_pengetahuan",
             {"aksi": "draf", "judul": JUDUL, "isi": ISI, "kata_kunci": KK})
    out1 = ai.chat(ADMIN, [{"role": "user", "content": "catat ya"}],
                   conversation_id="c1")
    assert "pertanyaan" in out1

    isi2 = "Cucuk per adalah bushing spring depan, bukan cross joint."
    _giliran(cur, "ajarkan_pengetahuan",
             {"aksi": "draf", "judul": JUDUL, "isi": isi2, "kata_kunci": KK})
    out2 = ai.chat(ADMIN, [{"role": "user", "content": "perbaiki dulu: bukan cross joint"}],
                   conversation_id="c1")
    assert "pertanyaan" in out2, "kartu konfirmasi tak boleh kena pagar ping-pong"
    assert isi2 in out2["reply"]
    assert ai._AJAR_DRAF["agus|c1"]["isi"] == isi2, "draf baru MENIMPA yang lama"
    assert not ai._TANYA_TERAKHIR, "kartu bebas-pagar tak boleh dicatat ke pagar"


def test_pagar_tanya_user_tetap_berlaku_setelah_kartu_ajar(monkeypatch):
    """Pembebasan pagar hanya untuk kartu konfirmasi — tanya_user tetap dijaga."""
    cur = _stub(monkeypatch)
    kartu = [{"teks": "Kampas rem posisi mana?", "opsi": ["Depan", "Belakang"]}]
    _giliran(cur, "ajarkan_pengetahuan",
             {"aksi": "draf", "judul": JUDUL, "isi": ISI, "kata_kunci": KK})
    ai.chat(ADMIN, [{"role": "user", "content": "catat ya"}], conversation_id="c1")

    _giliran(cur, "tanya_user", {"pertanyaan": kartu})
    assert "pertanyaan" in ai.chat(ADMIN, [{"role": "user", "content": "kampas rem?"}],
                                   conversation_id="c1")
    _giliran(cur, "tanya_user", {"pertanyaan": kartu})
    assert "pertanyaan" not in ai.chat(ADMIN, [{"role": "user", "content": "yang depan"}],
                                       conversation_id="c1")


# ── 6. Draf kedaluwarsa / tak ada ───────────────────────────────────────────

def test_simpan_tanpa_draf_dijawab_jujur():
    out = ai._t_ajarkan_pengetahuan({"aksi": "simpan", "judul": "X", "isi": "Y",
                                     "_cid": "c1"}, ADMIN)
    assert out["found"] is False
    assert "kedaluwarsa" in out["catatan"]
    assert pengetahuan.load_dokumen() == [], "store tak boleh tersentuh"


def test_draf_kedaluwarsa_dibuang():
    _draf()
    ai._AJAR_DRAF["agus|c1"]["t"] -= ai._AJAR_TTL + 1
    out = ai._t_ajarkan_pengetahuan({"aksi": "simpan", "_cid": "c1"}, ADMIN)
    assert out["found"] is False
    assert pengetahuan.load_dokumen() == []


def test_draf_terkunci_per_percakapan():
    _draf(cid="c1")
    out = ai._t_ajarkan_pengetahuan({"aksi": "simpan", "_cid": "c2"}, ADMIN)
    assert out["found"] is False, "draf percakapan lain tak boleh ikut tersimpan"
    assert pengetahuan.load_dokumen() == []


def test_conversation_id_tidak_pernah_dari_model(monkeypatch):
    """`_cid` disuntik server di _run_tool — argumen model diabaikan."""
    ditangkap = {}
    monkeypatch.setattr(ai, "_t_ajarkan_pengetahuan",
                        lambda a, u: ditangkap.update(a) or {"found": True})
    monkeypatch.setitem(ai._DISPATCH, "ajarkan_pengetahuan", ai._t_ajarkan_pengetahuan)
    ai._run_tool("ajarkan_pengetahuan",
                 {"aksi": "draf", "_cid": "conv-asli", "conversation_id": "punya-orang"},
                 ADMIN)
    assert ditangkap["_cid"] == "conv-asli"


# ── 7. Gerbang Menu Control ─────────────────────────────────────────────────

def test_gerbang_spec_dan_eksekusi(perms):
    assert ai._can_mengajar(ADMIN) is True
    assert ai._can_mengajar(STAF) is True
    assert ai._can_mengajar(POLOS) is False
    assert ai._can_mengajar(PEMBELI) is False
    for u, ada in ((ADMIN, True), (STAF, True), (POLOS, False), (PEMBELI, False)):
        nama = {s["function"]["name"] for s in ai._tool_specs(u)}
        assert ("ajarkan_pengetahuan" in nama) is ada
        assert ("ajarkan_pengetahuan" in ai._allowed_tool_names(u)) is ada


def test_eksekusi_ditolak_untuk_yang_tak_berhak(perms):
    out = ai._run_tool("ajarkan_pengetahuan",
                       {"aksi": "draf", "judul": JUDUL, "isi": ISI, "_cid": "c1"},
                       POLOS)
    assert out["denied"] is True
    assert pengetahuan.load_dokumen() == []
    # Lapis kedua: handler pun menolak walau dipanggil langsung.
    langsung = ai._t_ajarkan_pengetahuan({"aksi": "draf", "judul": JUDUL,
                                          "isi": ISI, "_cid": "c1"}, PEMBELI)
    assert langsung["denied"] is True


def test_key_terdaftar_di_menu_control():
    from app.services import permissions
    assert permissions.ASISTEN_KEYS.get("ai_mengajar")


# ── 8. Guard klaim-ajar ─────────────────────────────────────────────────────

def test_guard_klaim_ajar_memaksa_koreksi(monkeypatch):
    """Model mengaku mencatat TANPA tool → satu kali koreksi paksa + telemetri."""
    dicatat = {}
    monkeypatch.setattr(ai.ai_chat_log, "log_turn", lambda **kw: dicatat.update(kw))
    balasan = ["Siap, sudah saya catat pengetahuan itu ya.",
               "Maaf, saya belum menyimpannya."]
    n = {"i": 0}

    def fake(messages, tools, max_tokens=6000):
        i = min(n["i"], len(balasan) - 1)
        n["i"] += 1
        return {"choices": [{"message": {"content": balasan[i]},
                             "finish_reason": "stop"}]}

    monkeypatch.setattr(ai, "_post_chat", fake)
    out = ai.chat(ADMIN, [{"role": "user", "content": "catat ya: cucuk per itu bushing"}],
                  conversation_id="c1")
    assert n["i"] == 2, "klaim palsu WAJIB dikoreksi sekali"
    assert out["reply"] == balasan[1]
    assert "ajar" in dicatat["guard_kinds"]


def test_guard_tidak_menyala_setelah_tool_sukses(monkeypatch):
    """Tool sukses → klaim 'sudah saya catat' memang BENAR; jangan dikoreksi."""
    cur = _stub(monkeypatch, balasan="Sudah saya simpan ke pengetahuan, ya.")
    _draf()                                   # siapkan draf tertunda
    _giliran(cur, "ajarkan_pengetahuan", {"aksi": "simpan"})
    ai.chat(ADMIN, [{"role": "user", "content": "Simpan"}], conversation_id="c1")
    assert cur["total"] == 2, "tak boleh ada ronde koreksi tambahan"
    assert len(pengetahuan.load_dokumen()) == 1


def test_guard_tak_kena_klaim_lain(monkeypatch):
    """'sudah saya catat pesananmu' bukan klaim pengetahuan — jangan false positive."""
    n = {"i": 0}

    def fake(messages, tools, max_tokens=6000):
        n["i"] += 1
        return {"choices": [{"message": {"content": "Baik, sudah saya catat pesanannya."},
                             "finish_reason": "stop"}]}

    monkeypatch.setattr(ai, "_post_chat", fake)
    ai.chat(ADMIN, [{"role": "user", "content": "pesan 2 filter"}], conversation_id="c1")
    assert n["i"] == 1


# ── 9. Rujukan gantung & validasi lain ──────────────────────────────────────

@pytest.mark.parametrize("isi", [
    "Yang barusan itu salah, yang benar bushing spring depan.",
    "Part di atas dipakai untuk unit Howo.",
    "Seperti yang saya sebut tadi, itu bushing.",
])
def test_rujukan_gantung_ditolak(isi):
    out = _draf(isi=isi)
    assert "error" in out and "GANTUNG" in out["error"]
    assert not ai._AJAR_DRAF


def test_judul_atau_isi_kosong_ditolak():
    assert "error" in _draf(judul="")
    assert "error" in _draf(isi="   ")
    assert pengetahuan.load_dokumen() == []


def test_isi_terlalu_panjang_diarahkan_ke_menu_unggah():
    out = _draf(isi="a" * (ai._AJAR_MAKS_ISI + 1))
    assert "error" in out and "Pengetahuan AI" in out["error"]


# ── Pendaftaran & pengikat ──────────────────────────────────────────────────

def test_tool_terdaftar_dan_berlabel():
    assert ai._DISPATCH["ajarkan_pengetahuan"] is ai._t_ajarkan_pengetahuan
    assert ai._tool_label("ajarkan_pengetahuan") == "Menyusun draf pengetahuan"


def test_spec_dan_prompt_memuat_pengikat():
    spec = next(s["function"] for s in ai._tool_specs(ADMIN)
                if s["function"]["name"] == "ajarkan_pengetahuan")
    d = spec["description"]
    assert "catat" in d.lower()
    assert "BERDIRI SENDIRI" in d
    assert "aksi='simpan'" in d or "'simpan'" in d
    assert "tersimpan=true" in d
    prompt = ai._system_prompt(ADMIN)
    assert "ajarkan_pengetahuan" in prompt
