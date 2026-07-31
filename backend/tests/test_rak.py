"""
Rak & Kartu Stok — lokasi fisik part per gudang (services/rak + /api/rak).

Yang dijaga di sini: kanonisasi PN pemaaf-suffix (rak yang diisi lewat satu
jalur wajib ketemu lewat jalur lain), pagar tulis `users.gudang_kelola`, pembeli
tak melihat apa pun, foto lama benar-benar dihapus saat diganti, dan fitur tetap
DORMAN (bukan meledak) selama migrasi 027/028 belum dijalankan.

⛔ Semua Supabase/Storage/jaringan di-mock — tak ada satu pun request nyata.
"""
import io

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import ai_assistant as ai
from app.services import accurate, permissions, rak

ADMIN = {"username": "admin", "role": "admin"}
STAF = {"username": "beni", "role": "user"}
PEMBELI = {"username": "toko", "role": "pembeli"}

JKT = "01.Jakarta"
BPN = "03.Balikpapan"


# ── Perkakas mock ───────────────────────────────────────────────────────────
class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else []
        self.text = text

    def json(self):
        return self._payload


class FakeDB:
    """Pengganti `requests` untuk tabel rak_gudang: menyimpan baris di memori dan
    meniru semantik PostgREST yang dipakai service (in./eq., upsert merge)."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.panggilan = []

    # -- util
    @staticmethod
    def _in_values(raw: str) -> list[str]:
        return [v.strip().strip('"') for v in raw[len("in.("):-1].split(",")]

    def _cocok(self, row, params):
        pk = params.get("pn_key", "")
        if pk.startswith("in.(") and row["pn_key"] not in self._in_values(pk):
            return False
        g = params.get("gudang", "")
        if g.startswith("eq.") and row["gudang"] != g[3:]:
            return False
        return True

    def get(self, url, headers=None, params=None, timeout=None):
        self.panggilan.append(("get", params))
        return FakeResp(200, [dict(r) for r in self.rows if self._cocok(r, params or {})])

    def post(self, url, headers=None, params=None, json=None, timeout=None):
        self.panggilan.append(("post", json))
        for r in self.rows:
            if r["pn_key"] == json["pn_key"] and r["gudang"] == json["gudang"]:
                r.update({k: v for k, v in json.items()})   # merge-duplicates
                return FakeResp(201)
        self.rows.append(dict(json))
        return FakeResp(201)

    def patch(self, url, headers=None, params=None, json=None, timeout=None):
        self.panggilan.append(("patch", json))
        for r in self.rows:
            if self._cocok(r, params or {}):
                r.update(json)
        return FakeResp(204)

    def delete(self, url, headers=None, params=None, json=None, timeout=None):
        self.panggilan.append(("delete", params))
        self.rows = [r for r in self.rows if not self._cocok(r, params or {})]
        return FakeResp(204)


@pytest.fixture
def db(monkeypatch):
    """Tabel rak_gudang palsu + Supabase dianggap terkonfigurasi."""
    fake = FakeDB()
    monkeypatch.setattr(rak, "requests", fake)
    monkeypatch.setattr(rak, "get_settings",
                        lambda: type("S", (), {"supabase_configured": True,
                                               "supabase_table": "users"})())
    rak.invalidate()
    yield fake
    rak.invalidate()


@pytest.fixture
def dibuang(monkeypatch):
    """Rekam objek Storage yang dihapus (foto lama)."""
    jejak = []
    monkeypatch.setattr(rak, "delete_storage_object", lambda bucket, path: jejak.append(path) or True)
    return jejak


def _klien(user):
    from app import deps
    app.dependency_overrides[deps.get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture(autouse=True)
def _bersihkan_override():
    yield
    app.dependency_overrides.clear()
    rak.invalidate()


@pytest.fixture
def label_dikenal(monkeypatch):
    """Daftar gudang sah tanpa menyentuh indeks Accurate nyata."""
    from app.routers import rak as rak_router
    monkeypatch.setattr(rak_router.part_index, "gudang_names", lambda: [JKT, BPN])
    monkeypatch.setattr(rak_router.gudang_config, "coords_map", lambda: {JKT: (0, 0), BPN: (0, 0)})


# ── 1. Kanonisasi PN (pemaaf-suffix) ────────────────────────────────────────
def test_pn_key_ikut_indeks_accurate(monkeypatch):
    """Katalog menulis 'WG9525160004/2', Accurate menyimpan PN dasar. Kalau
    kuncinya mentah, rak yang sudah diisi tak pernah ketemu lagi."""
    monkeypatch.setattr(accurate, "snapshot", lambda: {"WG9525160004": {}})
    monkeypatch.setitem(accurate._index_cache, "by_pn", {"WG9525160004": {}})
    assert rak.pn_key("WG9525160004/2") == "WG9525160004"
    assert rak.pn_key("wg 9525160004") == "WG9525160004"


def test_pn_key_indeks_dingin_pakai_pn_dasar(monkeypatch):
    """Jebakan: indeks belum dimuat → index_key balik ke kunci APA ADANYA
    ('WG95251600042'). Baris yang tersimpan begitu jadi yatim begitu indeks
    hangat, jadi saat dingin kita paksa bentuk PN DASAR yang deterministik."""
    monkeypatch.setattr(accurate, "snapshot", lambda: {})
    assert rak.pn_key("WG9525160004/2") == "WG9525160004"


def test_kunci_baca_menutup_dua_bentuk(monkeypatch):
    monkeypatch.setattr(accurate, "snapshot", lambda: {})
    kunci = rak._kunci_baca("WG9525160004/2")
    assert "WG95251600042" in kunci and "WG9525160004" in kunci


# ── 2. Simpan & baca (roundtrip) ────────────────────────────────────────────
def test_upsert_lalu_baca_pn_bersuffix(db, monkeypatch):
    monkeypatch.setattr(accurate, "snapshot", lambda: {})
    ok, msg = rak.upsert("WG9525160004", JKT, "A-12", "dus atas", "beni")
    assert ok, msg
    # Dicari dengan PN ber-suffix varian → tetap ketemu.
    data = rak.get_for_pn("WG9525160004/2")
    assert data[JKT]["rak"] == "A-12" and data[JKT]["catatan"] == "dus atas"
    assert data[JKT]["updated_by"] == "beni" and data[JKT]["updated_at"]


def test_upsert_ulang_tidak_menghapus_foto(db, monkeypatch):
    """Staf membetulkan kode rak → foto kartu stok tak boleh ikut hilang."""
    monkeypatch.setattr(accurate, "snapshot", lambda: {})
    rak.upsert("PN1", JKT, "A-1", username="beni")
    db.rows[0].update({"foto_url": "http://x/f.jpg", "foto_path": "rak-kartu/01Jakarta/f.jpg"})
    rak.upsert("PN1", JKT, "A-2", username="beni")
    assert db.rows[0]["rak"] == "A-2"
    assert db.rows[0]["foto_url"] == "http://x/f.jpg"


def test_upsert_menolak_rak_kosong(db):
    ok, msg = rak.upsert("PN1", JKT, "  ", username="beni")
    assert not ok and "rak" in msg.lower()


def test_lookup_terbalik_per_gudang(db, monkeypatch):
    monkeypatch.setattr(accurate, "snapshot", lambda: {})
    rak.upsert("PN1", JKT, "A-1", username="beni")
    rak.upsert("PN2", BPN, "B-9", username="beni")
    items = rak.get_for_gudang(JKT)
    assert [i["gudang"] for i in items] == [JKT]


# ── 3. Foto kartu stok ──────────────────────────────────────────────────────
def test_ganti_foto_menghapus_objek_lama(db, dibuang, monkeypatch):
    """Tanpa pembersihan ini tiap penggantian meninggalkan file yatim permanen."""
    monkeypatch.setattr(accurate, "snapshot", lambda: {})
    rak.upsert("PN1", JKT, "A-1", username="beni")
    rak.set_foto("PN1", JKT, "http://x/lama.jpg", "rak-kartu/01Jakarta/lama.jpg", "beni")
    assert dibuang == []                                   # belum ada yang lama
    rak.set_foto("PN1", JKT, "http://x/baru.jpg", "rak-kartu/01Jakarta/baru.jpg", "beni")
    assert dibuang == ["rak-kartu/01Jakarta/lama.jpg"]
    assert db.rows[0]["foto_url"] == "http://x/baru.jpg"


def test_foto_tanpa_baris_rak_ditolak(db, monkeypatch):
    monkeypatch.setattr(accurate, "snapshot", lambda: {})
    ok, msg = rak.set_foto("PNX", JKT, "http://x/a.jpg", "p/a.jpg", "beni")
    assert not ok and "rak" in msg.lower()


def test_hapus_baris_ikut_hapus_foto(db, dibuang, monkeypatch):
    monkeypatch.setattr(accurate, "snapshot", lambda: {})
    rak.upsert("PN1", JKT, "A-1", username="beni")
    rak.set_foto("PN1", JKT, "http://x/a.jpg", "rak-kartu/01Jakarta/a.jpg", "beni")
    ok, _ = rak.hapus("PN1", JKT)
    assert ok and dibuang == ["rak-kartu/01Jakarta/a.jpg"] and db.rows == []


# ── 4. Gerbang tulis (users.gudang_kelola) ──────────────────────────────────
def _pasang_kelola(monkeypatch, peta):
    monkeypatch.setattr(rak, "gudang_kelola_for", lambda u: list(peta.get(u, [])))


def test_boleh_tulis(monkeypatch):
    _pasang_kelola(monkeypatch, {"beni": [JKT]})
    assert rak.boleh_tulis(ADMIN, BPN) is True             # admin lolos di mana pun
    assert rak.boleh_tulis(STAF, JKT) is True              # gudang sendiri
    assert rak.boleh_tulis(STAF, BPN) is False             # gudang orang lain
    assert rak.boleh_tulis({"username": "polos", "role": "user"}, JKT) is False
    assert rak.boleh_tulis(PEMBELI, JKT) is False          # tak pernah, walau diisi


def test_boleh_tulis_fail_closed(monkeypatch):
    """Supabase ngadat tak boleh berubah jadi 'boleh tulis semua gudang'."""
    def _meledak(_u):
        raise RuntimeError("supabase down")
    monkeypatch.setattr(rak, "gudang_kelola_for", _meledak)
    assert rak.boleh_tulis(STAF, JKT) is False


def test_gudang_kelola_kolom_belum_ada_fitur_dorman(monkeypatch):
    """Migrasi 027 belum jalan → [] tanpa exception (login & izin tetap jalan)."""
    class Kolom42703:
        @staticmethod
        def get(url, headers=None, params=None, timeout=None):
            return FakeResp(400, {}, 'column users.gudang_kelola does not exist (42703)')

    monkeypatch.setattr(rak, "requests", Kolom42703)
    monkeypatch.setattr(rak, "get_settings",
                        lambda: type("S", (), {"supabase_configured": True,
                                               "supabase_table": "users"})())
    rak.invalidate()
    assert rak.gudang_kelola_for("beni") == []


def test_gudang_kelola_di_cache_dan_bisa_dibatalkan(monkeypatch):
    hit = {"n": 0}

    class Sekali:
        @staticmethod
        def get(url, headers=None, params=None, timeout=None):
            hit["n"] += 1
            return FakeResp(200, [{"gudang_kelola": "01.Jakarta, 06.B80 H1"}])

    monkeypatch.setattr(rak, "requests", Sekali)
    monkeypatch.setattr(rak, "get_settings",
                        lambda: type("S", (), {"supabase_configured": True,
                                               "supabase_table": "users"})())
    rak.invalidate()
    assert rak.gudang_kelola_for("beni") == ["01.Jakarta", "06.B80 H1"]
    rak.gudang_kelola_for("beni")
    assert hit["n"] == 1                                   # panggilan kedua dari cache
    rak.invalidate("beni")
    rak.gudang_kelola_for("beni")
    assert hit["n"] == 2                                   # admin ubah → langsung berlaku


# ── 5. Impor massal ─────────────────────────────────────────────────────────
def _excel(baris: list[dict]) -> bytes:
    import pandas as pd
    bio = io.BytesIO()
    pd.DataFrame(baris).to_excel(bio, index=False)
    return bio.getvalue()


def test_parse_import_deteksi_header_bebas(monkeypatch):
    monkeypatch.setattr(accurate, "snapshot", lambda: {})
    data = _excel([{"No Part": "PN1", "Lokasi Rak": "A-12", "Keterangan": "dus atas"},
                   {"No Part": "PN2", "Lokasi Rak": "B-03", "Keterangan": ""}])
    valid, dilewati = rak.parse_import(data, "rak.xlsx")
    assert [v["pn"] for v in valid] == ["PN1", "PN2"]
    assert valid[0]["rak"] == "A-12" and valid[0]["catatan"] == "dus atas"
    assert dilewati == []


def test_parse_import_baris_tanpa_rak_dilaporkan(monkeypatch):
    """Diam-diam melewati baris kosong membuat staf mengira raknya tersimpan."""
    monkeypatch.setattr(accurate, "snapshot", lambda: {})
    data = _excel([{"Part Number": "PN1", "Rak": "A-1"},
                   {"Part Number": "PN2", "Rak": ""}])
    valid, dilewati = rak.parse_import(data, "rak.xlsx")
    assert [v["pn"] for v in valid] == ["PN1"]
    assert dilewati == [{"pn": "PN2", "alasan": "kode rak kosong"}]


def test_parse_import_tanpa_kolom_rak(monkeypatch):
    monkeypatch.setattr(accurate, "snapshot", lambda: {})
    data = _excel([{"Part Number": "PN1", "Qty": "3"}])
    valid, dilewati = rak.parse_import(data, "rak.xlsx")
    assert valid == [] and "RAK" in dilewati[0]["alasan"]


# ── 6. Endpoint /api/rak ────────────────────────────────────────────────────
def test_pembeli_403_di_semua_endpoint(db):
    c = _klien(PEMBELI)
    assert c.get("/api/rak/part/PN1").status_code == 403
    assert c.get(f"/api/rak/gudang/{JKT}").status_code == 403
    assert c.put(f"/api/rak/part/PN1/{JKT}", json={"rak": "A-1"}).status_code == 403
    assert c.delete(f"/api/rak/part/PN1/{JKT}").status_code == 403
    assert c.delete(f"/api/rak/part/PN1/{JKT}/foto").status_code == 403
    assert c.post("/api/rak/import", data={"gudang": JKT},
                  files={"file": ("a.xlsx", b"x")}).status_code == 403


def test_staf_boleh_melihat_walau_tak_mengelola(db, monkeypatch):
    """MELIHAT terbuka untuk semua staf internal — yang mengambil barang di rak
    belum tentu yang berhak mengubah datanya."""
    monkeypatch.setattr(accurate, "snapshot", lambda: {})
    _pasang_kelola(monkeypatch, {})
    rak.upsert("PN1", JKT, "A-12", username="admin")
    r = _klien(STAF).get("/api/rak/part/PN1")
    assert r.status_code == 200
    assert r.json()["rak"][JKT]["rak"] == "A-12"


def test_tulis_gudang_lain_403(db, monkeypatch, label_dikenal):
    _pasang_kelola(monkeypatch, {"beni": [JKT]})
    c = _klien(STAF)
    assert c.put(f"/api/rak/part/PN1/{BPN}", json={"rak": "B-1"}).status_code == 403
    r = c.put(f"/api/rak/part/PN1/{JKT}", json={"rak": "A-1"})
    assert r.status_code == 200 and r.json()["rak"]["rak"] == "A-1"


def test_tulis_gudang_karangan_ditolak(db, monkeypatch, label_dikenal):
    _pasang_kelola(monkeypatch, {"admin": []})
    r = _klien(ADMIN).put("/api/rak/part/PN1/99.Ngawur", json={"rak": "A-1"})
    assert r.status_code == 400 and "dikenal" in r.json()["detail"]


def test_impor_massal(db, monkeypatch, label_dikenal):
    monkeypatch.setattr(accurate, "snapshot", lambda: {})
    _pasang_kelola(monkeypatch, {"beni": [JKT]})
    data = _excel([{"Part Number": "PN1", "Rak": "A-1"},
                   {"Part Number": "PN2", "Rak": ""}])
    r = _klien(STAF).post("/api/rak/import", data={"gudang": JKT},
                          files={"file": ("rak.xlsx", data)})
    assert r.status_code == 200
    body = r.json()
    assert body["tersimpan"] == 1 and body["dilewati"][0]["pn"] == "PN2"
    assert rak.get_for_pn("PN1")[JKT]["rak"] == "A-1"


def test_unggah_foto_bukan_gambar_ditolak(db, monkeypatch, label_dikenal):
    _pasang_kelola(monkeypatch, {"beni": [JKT]})
    r = _klien(STAF).post(f"/api/rak/part/PN1/{JKT}/foto",
                          files={"file": ("kartu.pdf", b"%PDF-1.4")})
    # PDF sengaja DITOLAK di sini (beda dari bukti transfer): kartu stok tampil
    # sebagai thumbnail + lightbox di UI.
    assert r.status_code == 400


# ── Kompresi foto kartu (server-side, titik penegakan semua klien) ──────────
def _gambar_besar(fmt="JPEG", w=3000, h=2000, quality=95) -> bytes:
    """Tiruan foto HP: besar, bergradien halus (JPEG q95 ala kamera).
    ⚠️ Jangan pakai pola piksel teratur utk klaim UKURAN — PNG menyusutkannya
    ekstrem sementara JPEG membencinya, kebalikan dari foto sungguhan."""
    from PIL import Image
    img = Image.new("RGB", (w, h))
    px = img.load()
    for x in range(w):
        for y in range(0, h, 4):
            v = (x * 255 // w, ((x + y) // 12) % 255, y * 255 // h)
            px[x, y] = v
            if y + 1 < h:
                px[x, y + 1] = v
    buf = io.BytesIO()
    img.save(buf, format=fmt, **({"quality": quality} if fmt == "JPEG" else {}))
    return buf.getvalue()


def _siap_unggah(db, monkeypatch):
    """Baris rak PN1 siap + tangkap objek yang diunggah ke Storage."""
    from app.routers import rak as rak_router
    _pasang_kelola(monkeypatch, {"beni": [JKT]})
    monkeypatch.setattr(accurate, "snapshot", lambda: {})
    rak.upsert("PN1", JKT, "A-1", username="beni")
    tangkapan = {}

    def fake_upload(path, data, content_type, bucket=None):
        tangkapan.update({"path": path, "data": data, "ct": content_type})
        return True, "ok"

    monkeypatch.setattr(rak_router.sb, "upload_storage_object", fake_upload)
    monkeypatch.setattr(rak_router.sb, "photo_public_url", lambda p: f"http://x/{p}")
    return tangkapan


def test_unggah_foto_hp_dikompres(db, monkeypatch, label_dikenal):
    """Foto HP (JPEG besar) tak boleh mendarat mentah di bucket: sisi terpanjang
    dipangkas ke 1600 px, hasilnya jauh lebih kecil dari aslinya."""
    from PIL import Image
    tangkapan = _siap_unggah(db, monkeypatch)
    raw = _gambar_besar("JPEG")
    r = _klien(STAF).post(f"/api/rak/part/PN1/{JKT}/foto",
                          files={"file": ("kartu.jpg", raw)})
    assert r.status_code == 200
    assert tangkapan["ct"] == "image/jpeg"
    assert len(tangkapan["data"]) < len(raw)
    img = Image.open(io.BytesIO(tangkapan["data"]))
    assert max(img.size) <= 1600
    assert r.json()["foto_url"].endswith(".jpg")


def test_unggah_png_dikonversi_jpeg(db, monkeypatch, label_dikenal):
    """PNG masuk → JPEG keluar (format seragam; EXIF/alpha ikut hilang). Ukuran
    TIDAK di-assert di sini: PNG sintetis menyusut tak realistis."""
    from PIL import Image
    tangkapan = _siap_unggah(db, monkeypatch)
    r = _klien(STAF).post(f"/api/rak/part/PN1/{JKT}/foto",
                          files={"file": ("kartu.png", _gambar_besar("PNG"))})
    assert r.status_code == 200
    assert tangkapan["path"].endswith(".jpg")
    img = Image.open(io.BytesIO(tangkapan["data"]))
    assert img.format == "JPEG" and max(img.size) <= 1600


def test_unggah_bytes_palsu_berjudul_jpg_ditolak(db, monkeypatch, label_dikenal):
    """Dulu bytes apa pun berekstensi .jpg lolos ke bucket; decode Pillow kini
    sekaligus VALIDASI isi — bukan-gambar ditolak 400, bukan tersimpan rusak."""
    tangkapan = _siap_unggah(db, monkeypatch)
    r = _klien(STAF).post(f"/api/rak/part/PN1/{JKT}/foto",
                          files={"file": ("kartu.jpg", b"bukan gambar sama sekali")})
    assert r.status_code == 400
    assert "gambar" in r.json()["detail"].lower()
    assert not tangkapan                               # tak ada yang terunggah


def test_jpg_mungil_tak_dipaksa_membesar(db, monkeypatch, label_dikenal):
    """JPEG kecil yang sudah teroptimasi bisa MEMBESAR bila di-encode ulang —
    jaring di _kompres_kartu menyimpan bytes asli untuk kasus itu."""
    from PIL import Image
    tangkapan = _siap_unggah(db, monkeypatch)
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (200, 30, 30)).save(buf, format="JPEG", quality=30)
    raw = buf.getvalue()
    r = _klien(STAF).post(f"/api/rak/part/PN1/{JKT}/foto",
                          files={"file": ("kartu.jpg", raw)})
    assert r.status_code == 200
    assert len(tangkapan["data"]) <= len(raw)


def test_alias_pn_bergaris_miring(db, monkeypatch):
    """Uvicorn men-decode %2F jadi '/' sebelum route dicocokkan, jadi PN ber-
    suffix varian butuh alias dengan PN di posisi terakhir."""
    monkeypatch.setattr(accurate, "snapshot", lambda: {})
    rak.upsert("WG9525160004", JKT, "A-12", username="admin")
    c = _klien(STAF)
    # Bentuk lama tak pernah cocok: '/part/WG.../2' terbaca sebagai dua segmen.
    assert c.get("/api/rak/part/WG9525160004/2").status_code != 200
    r = c.get("/api/rak/part-of/WG9525160004/2")
    assert r.status_code == 200 and r.json()["rak"][JKT]["rak"] == "A-12"


# ── 7. Injeksi ke Asisten AI ────────────────────────────────────────────────
@pytest.fixture
def ai_detail(monkeypatch):
    """detail_part yang bisa dijalankan tanpa jaringan: katalog 1 baris + stok
    Accurate + semua pengaya (SIMS/taksonomi/tautan) dimatikan."""
    monkeypatch.setattr(ai.part_index, "search_part_number", lambda pn: [
        {"part_number": "PN1", "part_name": "Filter Oli", "file": "HOWO"}])
    monkeypatch.setattr(ai.part_index, "suggest_pns", lambda pn: [])
    monkeypatch.setattr(ai.accurate, "available", lambda: True)
    monkeypatch.setattr(ai.accurate, "stock_full", lambda pn: {
        "available_to_sell": 4, "unit": "PCS", "price": 100000, "name": "Filter Oli",
        "per_gudang": [{"gudang": JKT, "qty": 4}]})
    monkeypatch.setattr(ai.sims, "available", lambda: False)
    monkeypatch.setattr(ai.sims, "get_part_spec", lambda pn: {})
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {})
    monkeypatch.setattr(ai.part_taxonomy, "ringkas", lambda pn: "")
    monkeypatch.setattr(ai.knowledge_links, "entitas", lambda **kw: {})
    return monkeypatch


def test_detail_part_menyertakan_rak(ai_detail, monkeypatch):
    monkeypatch.setattr(ai.rak, "get_for_pn", lambda pn: {
        JKT: {"rak": "A-12", "catatan": "dus atas"}})
    r = ai._t_detail_part({"part_number": "PN1"}, ADMIN)
    assert r["rak_gudang"] == {JKT: "A-12 (catatan: dus atas)"}


def test_detail_part_diluar_katalog_menyertakan_rak(ai_detail, monkeypatch):
    """Jalur FALLBACK (barang stok lokal/aftermarket) punya penyusun
    stok_per_gudang sendiri — gampang terlewat saat menyisipkan rak."""
    monkeypatch.setattr(ai.part_index, "search_part_number", lambda pn: [])
    monkeypatch.setattr(ai.rak, "get_for_pn", lambda pn: {JKT: {"rak": "C-03"}})
    r = ai._t_detail_part({"part_number": "PNX"}, ADMIN)
    assert r["found"] and r["rak_gudang"] == {JKT: "C-03"}


def test_detail_part_pembeli_tak_menerima_rak(ai_detail, monkeypatch):
    monkeypatch.setattr(ai.rak, "get_for_pn", lambda pn: {JKT: {"rak": "A-12"}})
    r = ai._t_detail_part({"part_number": "PN1"}, PEMBELI)
    assert "rak_gudang" not in r and "stok_per_gudang" not in r


def test_stok_gudang_menyertakan_rak(monkeypatch):
    """Daftar 'apa yang ready di gudang X' adalah tempat rak paling berguna —
    petanya ditarik SEKALI, bukan sekali per baris."""
    monkeypatch.setattr(ai, "_resolve_gudang", lambda g: JKT)
    monkeypatch.setattr(ai.accurate, "items_matching", lambda terms, limit=400: [
        {"pn": "PN1", "name": "Filter Oli", "price": 100000}])
    monkeypatch.setattr(ai.accurate, "gudang_breakdown", lambda pn: {JKT: 4})
    monkeypatch.setattr(ai.accurate, "gudang_enriched_count", lambda: 1)
    tarik = {"n": 0}

    def _peta(label, q="", limit=300):
        tarik["n"] += 1
        return [{"pn_key": "PN1", "rak": "A-12"}]
    monkeypatch.setattr(ai.rak, "get_for_gudang", _peta)
    monkeypatch.setattr(ai.rak, "pn_key", lambda pn: pn.upper())
    r = ai._t_stok_gudang({"kata_kunci": "filter oli", "gudang": "jakarta"}, ADMIN)
    assert r["ditampilkan"][0]["rak"] == "A-12"
    assert tarik["n"] == 1


def test_hide_gudang_for_buyer_membuang_rak():
    hasil = ai._hide_gudang_for_buyer(
        {"stok_per_gudang": {JKT: 4}, "rak_gudang": {JKT: "A-12"}}, PEMBELI)
    assert hasil == {}


def test_rak_gagal_dibaca_tidak_menjatuhkan_detail(ai_detail, monkeypatch):
    """Rak cuma pelengkap — DB-nya ngadat tak boleh menghapus info stok/harga."""
    def _meledak(pn):
        raise RuntimeError("db down")
    monkeypatch.setattr(ai.rak, "get_for_pn", _meledak)
    r = ai._t_detail_part({"part_number": "PN1"}, ADMIN)
    assert r["found"] and "rak_gudang" not in r and r["stok_per_gudang"]


# ── 8. Payload izin ─────────────────────────────────────────────────────────
def test_permissions_memuat_gudang_kelola(monkeypatch):
    monkeypatch.setattr(permissions.rak, "gudang_kelola_for", lambda u: [JKT])
    p = permissions.all_effective("beni", "user")
    assert p["gudang_kelola"] == [JKT]
    # Menu baru otomatis muncul di Menu Control karena daftar inilah sumbernya.
    assert permissions.MENU_TABS["rak"] == "Rak & Kartu Stok"
    assert "rak" in permissions.effective("menu", "siapa_saja", "admin")


def test_permissions_pembeli_tanpa_gudang_kelola(monkeypatch):
    def _jangan_dipanggil(_u):
        raise AssertionError("pembeli tak boleh sampai membaca gudang_kelola")
    monkeypatch.setattr(permissions.rak, "gudang_kelola_for", _jangan_dipanggil)
    assert permissions.all_effective("toko", "pembeli")["gudang_kelola"] == []
