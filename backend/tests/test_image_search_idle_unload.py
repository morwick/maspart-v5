"""Model DINOv2 tak boleh menghuni RAM saat tak dipakai — dan startup tak boleh memuatnya.

Latar (diukur di container 2026-08-17, bukan di laptop). Backend duduk di 2,15 GB
RSS dari plafon 2,44 GiB; sisa ±280 MB. Dua kali dibunuh *cgroup* OOM (12 Agu, dan
17 Agu 02:19 UTC tepat setelah pembuatan Excel) — bukan OOM host, jadi plafon
containernya sendiri yang tertembus. Penghuni terbesarnya bukan cache (semua cache
sudah berpagar sejak `cache_util`), melainkan model DINOv2 ±350 MB yang di-preload
`main._warmup` SETIAP restart, padahal `search-image` cuma dipanggil 6× dalam 10
jam terakhir.

Yang dijaga di sini tiga lapis:
  1. perilaku pelepasan idle (`_maybe_unload` / `unload_model`);
  2. ⭐ pengunci kelas bug-nya: `main._warmup` TIDAK BOLEH memanggil `preload_model`
     lagi — diperiksa dari AST file aslinya, bukan dari stub;
  3. ⭐ setelan alokator di image tetap ada — kalau hilang dari Dockerfile,
     RSS menggelembung lagi tanpa satu baris kode pun berubah.
"""
import ast
import threading
import time
from pathlib import Path

import pytest

from app.services import image_search

_BACKEND = Path(__file__).resolve().parents[1]
_REPO = _BACKEND.parent


@pytest.fixture(autouse=True)
def _pulihkan_keadaan():
    """Cache tingkat-modul bertahan lintas tes di proses yang sama — dan urutan
    tes diacak (pytest-randomly). Kembalikan apa adanya setelah tiap tes."""
    asli = (image_search._model, image_search._preprocess,
            image_search._dipakai_terakhir, image_search._IDLE_UNLOAD_SEC)
    yield
    (image_search._model, image_search._preprocess,
     image_search._dipakai_terakhir, image_search._IDLE_UNLOAD_SEC) = asli


class _ModelPalsu:
    """Berdiri di tempat DINOv2 — tes ini soal SIKLUS HIDUP-nya, bukan inferensi."""


# ── 1. perilaku pelepasan idle ──────────────────────────────────────────
def test_dilepas_setelah_menganggur_melewati_ambang():
    sekarang = time.monotonic()
    image_search._model = _ModelPalsu()
    image_search._preprocess = object()
    image_search._dipakai_terakhir = sekarang - (image_search._IDLE_UNLOAD_SEC + 1)

    assert image_search._maybe_unload(sekarang) is True
    assert image_search.model_ready() is False
    assert image_search._preprocess is None


def test_yang_baru_dipakai_tidak_dilepas():
    """Pelepasan yang terlalu bersemangat = tiap pencarian foto membayar ulang
    pemuatan model ±15-20 detik di 1 vCPU."""
    sekarang = time.monotonic()
    image_search._model = _ModelPalsu()
    image_search._dipakai_terakhir = sekarang - 5

    assert image_search._maybe_unload(sekarang) is False
    assert image_search.model_ready() is True


def test_ambang_nol_mematikan_pelepasan():
    sekarang = time.monotonic()
    image_search._IDLE_UNLOAD_SEC = 0
    image_search._model = _ModelPalsu()
    image_search._dipakai_terakhir = sekarang - 99999

    assert image_search._maybe_unload(sekarang) is False
    assert image_search.model_ready() is True


def test_melepas_saat_tak_ada_model_aman():
    image_search._model = None
    assert image_search.unload_model() is False
    assert image_search._maybe_unload(time.monotonic()) is False


def test_kembalikan_ke_os_tak_pernah_melempar():
    """`malloc_trim` cuma ada di glibc. Di laptop dev (Windows/musl) fungsinya
    harus diam, bukan menggagalkan pelepasan model."""
    image_search._kembalikan_ke_os()


def test_status_model_melaporkan_keadaan_sebenarnya():
    image_search._model = None
    kosong = image_search.status_model()
    assert kosong["termuat"] is False and kosong["idle_detik"] is None
    assert kosong["lepas_setelah_detik"] == image_search._IDLE_UNLOAD_SEC

    image_search._model = _ModelPalsu()
    image_search._dipakai_terakhir = time.monotonic() - 120
    terisi = image_search.status_model()
    assert terisi["termuat"] is True
    assert 110 <= terisi["idle_detik"] <= 130


def test_memuat_menyetel_stempel_dan_memulai_satu_pemantau(monkeypatch):
    """`_load_model` wajib menyetel stempel pakai — tanpa itu stempel tetap 0.0
    dan pemantau melepas model yang BARU SAJA dimuat."""
    dipanggil: list[int] = []
    monkeypatch.setattr(image_search, "_mulai_pemantau_locked",
                        lambda: dipanggil.append(1))
    monkeypatch.setattr(image_search, "_TORCH_OK", True)

    palsu = _ModelPalsu()

    class _Hub:
        @staticmethod
        def set_dir(_):
            pass

        @staticmethod
        def load(*_a, **_k):
            palsu.eval = lambda: None
            return palsu

    monkeypatch.setattr(image_search, "torch", type("T", (), {"hub": _Hub}))
    monkeypatch.setattr(image_search, "transforms",
                        type("Tf", (), {"Compose": staticmethod(lambda x: "pre"),
                                        "Resize": staticmethod(lambda *a, **k: None),
                                        "CenterCrop": staticmethod(lambda *a: None),
                                        "ToTensor": staticmethod(lambda: None),
                                        "Normalize": staticmethod(lambda **k: None),
                                        "InterpolationMode": type("I", (), {"BICUBIC": 1})}))
    image_search._model = None
    image_search._dipakai_terakhir = 0.0

    model, pre = image_search._load_model()
    assert model is palsu and pre == "pre"
    assert image_search._dipakai_terakhir > 0.0
    assert dipanggil == [1]

    # Panggilan kedua memakai model yang sama & tak menambah pemantau.
    image_search._load_model()
    assert dipanggil == [1]


def test_pemantau_hanya_satu_thread():
    image_search._pemantau_mulai = False
    try:
        image_search._mulai_pemantau_locked()
        image_search._mulai_pemantau_locked()
        assert sum(1 for t in threading.enumerate() if t.name == "dinov2-idle") == 1
    finally:
        image_search._pemantau_mulai = True   # thread daemon, biarkan hidup


# ── 2. ⭐ pengunci: startup tak boleh memuat model ───────────────────────
def test_warmup_startup_tidak_memuat_model_dinov2():
    """Diperiksa dari AST `main.py` ASLI. Tes berbasis stub tidak akan menangkap
    ini — preload-nya ada di startup produksi, bukan di jalur yang di-stub."""
    pohon = ast.parse((_BACKEND / "app" / "main.py").read_text(encoding="utf-8"))
    warmup = next(n for n in ast.walk(pohon)
                  if isinstance(n, ast.FunctionDef) and n.name == "_warmup")
    dipanggil = {n.func.attr for n in ast.walk(warmup)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}

    assert "preload_model" not in dipanggil, (
        "main._warmup memuat DINOv2 saat startup lagi — ±350 MB dibayar tiap "
        "restart walau fitur foto tak dipakai; itu penyebab cgroup OOM 17 Agu."
    )
    # Matriks galeri (113 MB, dipakai TIAP pencarian) memang tetap di-preload.
    assert "preload_local_index" in dipanggil


# ── 3. ⭐ pengunci: setelan alokator ada di image ────────────────────────
@pytest.mark.parametrize("kunci,nilai", [
    ("MALLOC_ARENA_MAX", "2"),
    ("MALLOC_TRIM_THRESHOLD_", "131072"),
    ("OMP_NUM_THREADS", "1"),
])
def test_dockerfile_menyetel_alokator(kunci, nilai):
    """Setelan ini WAJIB env image: glibc & OpenMP membacanya saat proses START,
    jadi tak bisa dipindah ke `os.environ` di Python.

    ⛔ Yang DIBANGUN deploy adalah `backend/Dockerfile` — `build.sh` memanggil
    `docker build ./backend` tanpa -f, dan `push.sh` hanya mengirim file itu."""
    teks = (_BACKEND / "Dockerfile").read_text(encoding="utf-8")
    assert f"{kunci}={nilai}" in teks


def test_dua_dockerfile_tidak_menyimpang_soal_alokator():
    """Salinan di deploy/coolify/ pernah tertinggal dari yang asli (libgl1 hanya
    ada di backend/Dockerfile). Kalau salinan itu masih disimpan, isinya harus
    sama — build manual dengan -f tak boleh berperilaku diam-diam beda."""
    salinan = _REPO / "deploy" / "coolify" / "backend.Dockerfile"
    if not salinan.exists():
        pytest.skip("salinan deploy/coolify sudah dihapus")
    teks = salinan.read_text(encoding="utf-8")
    for kunci in ("MALLOC_ARENA_MAX=2", "MALLOC_TRIM_THRESHOLD_=131072", "OMP_NUM_THREADS=1"):
        assert kunci in teks
