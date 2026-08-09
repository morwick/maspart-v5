"""Pastikan package `app` bisa diimpor saat pytest dijalankan dari mana pun.

    cd backend && python -m pytest tests/ -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _jangan_tulis_observabilitas_prod(request, monkeypatch):
    """Test yang memanggil ai_assistant.chat() TIDAK boleh menulis ke Supabase
    PRODUKSI (kejadian nyata 2026-07-08: 150 baris 'tester' mengotori halaman
    Observabilitas AI). log_turn DAN log_turn_async di-no-op untuk SEMUA test —
    kecuali modul test_ai_chat_log* yang memang menguji fungsi itu (mereka mock
    requests sendiri). Pengecualiannya berbasis PREFIKS: dulu nama persis, sehingga
    test_ai_chat_log_session_id ikut di-no-op dan seluruh assertion-nya menguji
    stub, bukan kode nyata.

    log_turn_async WAJIB ikut: chat() memakai versi asinkron itu, jadi tanpa
    no-op-nya thread daemon akan tetap menembak Supabase produksi diam-diam
    (dan gagalnya pun tak terlihat karena ditelan)."""
    if request.module.__name__.startswith("test_ai_chat_log"):
        return
    try:
        from app.services import ai_chat_log
        monkeypatch.setattr(ai_chat_log, "log_turn", lambda **kw: True)
        monkeypatch.setattr(ai_chat_log, "log_turn_async", lambda **kw: None)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_memo_tangga_chat_log():
    """log_turn mengingat tingkat tangga payload yang terakhir diterima server
    (variabel modul, umur proses). Antar-test ingatan itu BOCOR: test yang
    servernya menolak kolom baru meninggalkan memo di tingkat rendah, lalu test
    berikutnya mendapati payload 'kurang kolom' — dan dengan pytest-randomly,
    gagalnya berpindah-pindah. Nolkan sebelum & sesudah tiap test."""
    try:
        from app.services import ai_chat_log
        ai_chat_log._tier_memo = 0
    except Exception:
        yield
        return
    yield
    ai_chat_log._tier_memo = 0


@pytest.fixture(autouse=True)
def _bersihkan_cache_izin():
    """permissions menyimpan hasil perms_load di cache modul ber-TTL 30 dtk.
    Antar-test cache itu BOCOR: test yang me-monkeypatch perms_load justru
    membaca centang milik test sebelumnya. Buang sebelum & sesudah tiap test."""
    try:
        from app.services import permissions
    except Exception:
        yield
        return
    permissions.invalidate_cache()
    yield
    permissions.invalidate_cache()


@pytest.fixture(autouse=True)
def _jangan_baca_feedback_prod(request, monkeypatch):
    """Umpan balik 👍/👎 kini ikut ditambang ai_belajar.mine_chat_logs. Tanpa
    penjaga ini, test biasa MEMBACA tabel ai_feedback PRODUKSI lewat jaringan:
    lambat, dan hasilnya bergantung data nyata — `test_gap_kelompok_minimal_3`
    langsung gagal ketika kebetulan ada 👎 sungguhan di sana. Di-no-op kecuali
    modul yang memang mengujinya (mereka mock sendiri)."""
    if request.module.__name__.startswith("test_ai_feedback") or \
            request.module.__name__ == "test_ai_belajar_feedback":
        return
    try:
        from app.services import ai_feedback
        monkeypatch.setattr(ai_feedback, "list_feedback",
                            lambda rating=None, only_open=False, limit=200: [])
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _jangan_bangun_indeks_epc_nyata(monkeypatch):
    """Indeks item EPC per-unit: warm_items_index menembak RATUSAN panggilan EPC
    nyata di thread latar & menulis cache disk — tak boleh terjadi dari test.
    items_index_ready dipaksa False agar handler tak terpengaruh file cache yang
    kebetulan ada di data/ (test yang mengujinya me-mock sendiri, menimpa ini)."""
    try:
        from app.services import epc_bom
        monkeypatch.setattr(epc_bom, "warm_items_index", lambda r: None)
        monkeypatch.setattr(epc_bom, "items_index_ready", lambda r: False)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _jangan_cari_master_sims_nyata(request, monkeypatch):
    """search_master_by_name menembak pageDealer NYATA (login token + GET) — sejak
    2026-07-23 query NAMA 0-hasil di cari_part memicunya sbg jaring terakhir; test
    biasa tak boleh menyentuh jaringan. Di-no-op kecuali modul pengujinya (ia mock
    sendiri). Memo per-nama juga dibersihkan agar tak bocor antar-test."""
    try:
        from app.services import sims
        sims._master_name_memo.clear()
        if request.module.__name__ != "test_sims_master_fallback":
            monkeypatch.setattr(sims, "search_master_by_name",
                                lambda name, limit=8: [])
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _jangan_status_jual_sims_nyata(request, monkeypatch):
    """sims.status_jual (baru 2026-08-07) bisa menembak partInfo NYATA (login +
    GET per PN, bahkan force_refresh utk entri cache lama tanpa 'raw') — dan kini
    dipanggil detail_part & pengganti_part. Test biasa tak boleh menyentuh
    jaringan → di-no-op (None = status TAK DIKETAHUI, field absen). Test yang
    memang mengujinya cukup monkeypatch ulang di badan test-nya (menang atas
    fixture ini) — pola yang sama dgn _jangan_cari_master_sims_nyata."""
    try:
        from app.services import sims
        # test_kejujuran_klaim_epc menguji logika status_jual ASLI (get_part_info
        # sudah ia mock sendiri) → dikecualikan, pola _jangan_cari_master_sims_nyata.
        if request.module.__name__ != "test_kejujuran_klaim_epc":
            monkeypatch.setattr(sims, "status_jual", lambda pn: None)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _jangan_bridge_weichai_nyata(request, monkeypatch):
    """`fast_moving.build()` (2026-08-07) menambal lubang part MESIN dari EPC
    Weichai — itu panggilan JARINGAN via bridge SSO. Setiap test yang memanggil
    build() jadi ikut menembak Weichai sungguhan: lambat, rapuh, dan hasilnya
    menyusup ke dataset uji (2 test fast_moving sempat gagal karenanya).
    Dimatikan untuk semua test; modul pengujinya sendiri menyalakan ulang —
    pola yang sama dgn _jangan_cari_master_sims_nyata."""
    try:
        from app.services import fast_moving
        if request.module.__name__ != "test_fast_moving_weichai":
            monkeypatch.setattr(fast_moving, "_WC_AKTIF", False)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _jangan_pakai_tabel_replace_nyata(request, monkeypatch):
    """`pengganti_part` (2026-08-08) menambahkan sumber TABEL OFFLINE Weichai dari
    data/weichai_replace.json.gz (58rb record). Bila file itu ada di mesin yang
    menjalankan test, tiap test pengganti_part ikut memuat & mengindeksnya —
    lambat, dan hasil data NYATA menyusup ke skenario uji yang PN-nya sintetis
    (satu tabrakan PN saja = assertion yang gagal berpindah-pindah).
    Dimatikan untuk semua test; modul pengujinya sendiri menyalakan ulang —
    pola yang sama dgn _jangan_bridge_weichai_nyata."""
    try:
        from app.services import weichai_replace
        if request.module.__name__ != "test_weichai_replace":
            monkeypatch.setattr(weichai_replace, "available", lambda: False)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _bersihkan_idempotensi_order():
    """Cache idempotensi POST /orders (L5) proses-lokal → bersihkan antar-test
    agar hasil order satu test tak bocor ke test lain yang sidik jarinya sama."""
    try:
        from app.routers import orders as _o
        _o._order_idem.clear()
        _o._order_locks.clear()
    except Exception:
        pass
    yield
    try:
        from app.routers import orders as _o
        _o._order_idem.clear()
        _o._order_locks.clear()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _indeks_accurate_segar(monkeypatch):
    """Guard umur indeks (accurate.index_too_old_for_checkout) memblokir checkout
    bila ts=0 (default env test) — dianggap basi. Beri ts SEGAR agar test checkout
    yang tak menguji integritas indeks tetap jalan; test khusus H3 meng-override
    ts-nya sendiri (monkeypatch.setitem menang di scope test)."""
    try:
        import time
        from app.services import accurate
        monkeypatch.setitem(accurate._index_cache, "ts", time.time())
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _pemutus_arus_epc_bersih():
    """Keadaan pemutus arus EPC adalah state MODUL — kalau satu test membuatnya
    terputus (mis. mensimulasi jaringan mati), test berikutnya akan menerima
    {'_err':'network'} tanpa menyentuh stub-nya sama sekali dan gagal dengan
    sebab yang sama sekali tak berhubungan. Bersihkan sebelum & sesudah."""
    try:
        from app.services import epc_bom
        epc_bom.circuit_reset()
        yield
        epc_bom.circuit_reset()
    except Exception:
        yield
