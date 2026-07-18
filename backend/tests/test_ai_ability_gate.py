"""Menu Control tab "Asisten AI" — kemampuan elevated per akun (kind `asisten`).

Kemampuan yang dulu HARDCODED admin-only (atau admin+'mas') kini bisa DIBERIKAN
per akun: ai_harga_sims, ai_populasi, ai_penawaran, ai_stok_admin,
ai_pesanan_bermasalah. Semantik: centang = MEMBERI (OR dengan gerbang role lama,
tak pernah mencabut dari admin/'mas'); tanpa baris = staf TIDAK punya
(fail-CLOSED — beda sengaja dari boleh_harga/boleh_stok yang fail-open);
pembeli tak pernah bisa diberi.

Pola: tests/test_stok_gate_asisten.py (panggil fungsi langsung, monkeypatch
app.services.permissions.effective).
"""
import pytest

from app.services import ai_assistant as ai
from app.services import permissions

ADMIN = {"username": "admin", "role": "admin"}
MAS = {"username": "mas", "role": "user"}            # SEE_ALL — back-compat
BUDI = {"username": "budi", "role": "user"}          # grant ai_penawaran
SARI = {"username": "sari", "role": "user"}          # grant ai_stok_admin
POLOS = {"username": "polos", "role": "user"}        # tanpa baris apa pun
PEMBELI = {"username": "toko", "role": "pembeli"}

_GRANTS = {"budi": ["ai_penawaran"], "sari": ["ai_stok_admin"]}


@pytest.fixture
def perms(monkeypatch):
    """Kolom ON untuk semua (agar _GATED_STOK tak ikut campur); grant asisten
    hanya untuk budi & sari sesuai _GRANTS."""
    def fake(kind, u, r):
        if kind == "asisten":
            return _GRANTS.get(u, [])
        return ["col_stok", "col_harga"]
    monkeypatch.setattr("app.services.permissions.effective", fake)


# ── boleh_ai (gerbang tunggal, fail-closed) ──────────────────────────────────
def test_boleh_ai_per_peran(perms):
    assert permissions.boleh_ai(ADMIN, "ai_penawaran") is True
    assert permissions.boleh_ai(BUDI, "ai_penawaran") is True
    assert permissions.boleh_ai(BUDI, "ai_stok_admin") is False   # grant per-key
    assert permissions.boleh_ai(POLOS, "ai_penawaran") is False   # tanpa baris
    assert permissions.boleh_ai(PEMBELI, "ai_penawaran") is False


def test_boleh_ai_pembeli_hard_block(monkeypatch):
    """Salah-konfigurasi (pembeli diberi baris) tetap TIDAK membuka kemampuan."""
    monkeypatch.setattr("app.services.permissions.effective",
                        lambda k, u, r: ["ai_penawaran"])
    assert permissions.boleh_ai(PEMBELI, "ai_penawaran") is False


def test_boleh_ai_fail_closed(monkeypatch):
    def boom(kind, u, r):
        raise RuntimeError("supabase down")
    monkeypatch.setattr("app.services.permissions.effective", boom)
    assert permissions.boleh_ai(POLOS, "ai_penawaran") is False
    assert permissions.boleh_ai(ADMIN, "ai_penawaran") is True    # admin tak lewat effective


# ── Back-compat: admin & 'mas' tak berubah ───────────────────────────────────
def test_mas_tetap_punya_sims_dan_populasi(perms):
    """'mas' (SEE_ALL) TIDAK punya baris grant — akses lamanya wajib bertahan."""
    assert ai._can_sims(MAS) is True
    assert ai._can_populasi(MAS) is True
    assert ai._can_sims(POLOS) is False
    assert ai._can_populasi(POLOS) is False


def test_grant_memberi_sims_dan_populasi_terpisah(monkeypatch):
    monkeypatch.setattr("app.services.permissions.effective",
                        lambda k, u, r: ["ai_harga_sims"] if k == "asisten" else [])
    assert ai._can_sims(BUDI) is True
    assert ai._can_populasi(BUDI) is False    # key terpisah, bukan alias lagi


# ── Spec ditawarkan / disembunyikan per grant ────────────────────────────────
def _nama_tools(user, sheet_id=""):
    return {f["function"]["name"] for f in ai._tool_specs(user, sheet_id)}


def test_spec_penawaran_per_grant(perms):
    assert "buat_penawaran" in _nama_tools(BUDI)
    assert "sheet_jadi_penawaran" in _nama_tools(BUDI, sheet_id="sid")
    assert "buat_penawaran" not in _nama_tools(SARI)
    assert "buat_penawaran" not in _nama_tools(POLOS)
    assert "buat_penawaran" in _nama_tools(ADMIN)


def test_spec_stok_admin_per_grant(perms):
    for t in ("stok_tertahan", "alternatif_ready"):
        assert t in _nama_tools(SARI)
        assert t not in _nama_tools(BUDI)
        assert t not in _nama_tools(POLOS)
        assert t in _nama_tools(ADMIN)
    # pesanan_bermasalah key TERPISAH dari ai_stok_admin (blok p2 dipecah).
    assert "pesanan_bermasalah" not in _nama_tools(SARI)
    assert "pesanan_bermasalah" in _nama_tools(ADMIN)


def test_grant_tak_menembus_gerbang_kolom_stok(monkeypatch):
    """SARI diberi ai_stok_admin tapi col_stok DIMATIKAN → stok_tertahan tetap
    tak ditawarkan (_GATED_STOK menyaring; grant asisten bukan pintu belakang)."""
    def fake(kind, u, r):
        if kind == "asisten":
            return ["ai_stok_admin"]
        return ["col_harga"]                     # col_stok OFF
    monkeypatch.setattr("app.services.permissions.effective", fake)
    nama = _nama_tools(SARI)
    assert "stok_tertahan" not in nama
    assert "alternatif_ready" not in nama


# ── Eksekusi: allow-list & handler denied tanpa grant ────────────────────────
def test_eksekusi_ditolak_tanpa_grant(perms):
    assert "buat_penawaran" not in ai._allowed_tool_names(POLOS)
    assert ai._run_tool("buat_penawaran", {"pelanggan": "x"}, POLOS)["denied"] is True
    assert ai._t_stok_tertahan({}, POLOS)["denied"] is True
    assert ai._t_pesanan_bermasalah({}, POLOS)["denied"] is True
    assert ai._t_alternatif_ready({"part_number": "X"}, POLOS)["denied"] is True
    assert ai._t_buat_penawaran({}, POLOS)["denied"] is True
    assert ai._t_sheet_jadi_penawaran({}, POLOS)["denied"] is True


def test_allow_list_terbuka_sesuai_grant(perms):
    assert "buat_penawaran" in ai._allowed_tool_names(BUDI)
    assert "stok_tertahan" in ai._allowed_tool_names(SARI)
    assert "stok_tertahan" not in ai._allowed_tool_names(BUDI)


def test_gerbang_helper_per_grant(perms):
    assert ai._can_penawaran(BUDI) is True and ai._can_penawaran(SARI) is False
    assert ai._can_stok_admin(SARI) is True and ai._can_stok_admin(BUDI) is False
    assert ai._can_pesanan_bermasalah(POLOS) is False
    assert ai._can_pesanan_bermasalah(ADMIN) is True


# ── effective() dengan flag grant_off (tanpa mock effective) ─────────────────
def test_effective_grant_off_default_kosong(monkeypatch):
    monkeypatch.setattr("app.services.permissions.perms_load", lambda pt: {})
    assert permissions.effective("asisten", "polos", "user") == []
    # Admin dapat SEMUA (grant_off = izin, beda dari default_off 'sesi' = pembatasan).
    assert permissions.effective("asisten", "admin", "admin") == list(permissions.ASISTEN_KEYS)
    assert permissions.effective("sesi", "admin", "admin") == []
    # Regresi kind lama: tanpa baris → semua menu tetap aktif.
    assert permissions.effective("menu", "polos", "user") == list(permissions.MENU_TABS)


def test_effective_grant_dibaca_dari_baris(monkeypatch):
    monkeypatch.setattr("app.services.permissions.perms_load",
                        lambda pt: {"budi": ["ai_penawaran"]})
    assert permissions.effective("asisten", "budi", "user") == ["ai_penawaran"]
    assert permissions.effective("asisten", "lain", "user") == []


# ── overview kind asisten ────────────────────────────────────────────────────
def test_overview_asisten(monkeypatch):
    monkeypatch.setattr("app.services.permissions.perms_load", lambda pt: {})
    monkeypatch.setattr("app.services.permissions.list_users",
                        lambda: [{"username": "budi", "role": "user"}])
    ov = permissions.overview("asisten")
    assert ov["default"] == []                    # baris default mulai kosong
    assert set(ov["all_keys"]) == set(permissions.ASISTEN_KEYS)
    assert permissions.is_valid_kind("asisten")
