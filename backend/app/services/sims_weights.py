"""
Indeks BERAT resmi SIMS (gram) per Part Number, PERSISTEN di `/app/data`
(bertahan lintas redeploy — beda dari cache part_info.json di /app/images yang
ephemeral). Warmer latar mengisinya untuk part BERHARGA (kandidat etalase) →
SIMS jadi sumber berat UTAMA tanpa input manual harga.xlsx.

Dipakai oleh `harga.weight_for` (SIMS utama → fallback berat manual harga.xlsx).
Best-effort: bila SIMS mati / part tak ada → tetap 0, alur tak terganggu.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time

from ..core.config import get_settings
from . import sims

logger = logging.getLogger("maspart.simsweight")

_STARTUP_DELAY = 90         # detik setelah boot sebelum warm (jangan ganggu startup)
_INTERVAL = 6 * 3600        # ulang tiap 6 jam (tangkap part yang baru dihargai)
_THROTTLE = 0.8            # jeda antar fetch SIMS (detik) — gentle ke SIMS
_MAX_PER_CYCLE = 2000       # plafon fetch per siklus (jaga-jaga)

_lock = threading.Lock()
# {PN: {"g": berat asli (gram), "vol": berat volumetrik (gram)}}
# Format LAMA {PN: gram} tetap terbaca (diangkat jadi {"g": gram, "vol": 0}).
_map: dict[str, dict] | None = None
_started = False


def _file():
    return get_settings().data_path / "sims_weights.json"


def _norm(v) -> dict:
    """`vol` = None berarti BELUM pernah dicek dimensinya (mis. entri format lama)
    — beda dari 0 yang berarti SIMS memang tak punya dimensi untuk part itu.
    Tanpa pembedaan ini, entri lama tak akan pernah dilengkapi warmer."""
    if isinstance(v, dict):
        vol = v.get("vol")
        return {"g": int(v.get("g") or 0), "vol": None if vol is None else int(vol)}
    try:
        return {"g": int(v or 0), "vol": None}   # format lama: angka polos
    except (TypeError, ValueError):
        return {"g": 0, "vol": None}


def _load() -> dict[str, dict]:
    global _map
    if _map is None:
        try:
            raw = json.loads(_file().read_text("utf-8"))
            _map = {k.upper(): _norm(v) for k, v in raw.items()}
        except Exception:
            _map = {}
    return _map


def _save() -> None:
    try:
        f = _file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(_map, ensure_ascii=False), "utf-8")
    except Exception:
        logger.exception("[simsweight] gagal simpan indeks")


def get(pn: str) -> int:
    """Berat ASLI (gram) dari indeks persisten; 0 bila belum ada."""
    return _load().get((pn or "").strip().upper(), {}).get("g", 0)


def get_volumetric(pn: str) -> int:
    """Berat VOLUMETRIK (gram) dari dimensi SIMS; 0 bila dimensi tak ada/belum diindeks."""
    return _load().get((pn or "").strip().upper(), {}).get("vol") or 0


def dim_known(pn: str) -> bool:
    """Dimensi part ini SUDAH pernah dicek ke SIMS? (0 = dicek, memang tak ada)."""
    return _load().get((pn or "").strip().upper(), {}).get("vol") is not None


def put(pn: str, grams: int, vol_grams: int | None = None) -> None:
    key = (pn or "").strip().upper()
    if not key or grams <= 0:
        return
    with _lock:
        m = _load()
        lama = m.get(key, {})
        vol = lama.get("vol") if vol_grams is None else int(vol_grams)
        m[key] = {"g": int(grams), "vol": vol}
        _save()


def fetch_and_store(pn: str) -> int:
    """Ambil berat + dimensi SIMS LIVE (login/fetch) lalu simpan ke indeks.
    Return berat ASLI (gram); 0 bila tak ada."""
    try:
        g = sims.get_part_weight_grams(pn)
    except Exception:
        g = 0
    try:
        vol = sims.get_part_volumetric_grams_cached(pn)   # info sudah ter-cache oleh call di atas
    except Exception:
        vol = 0
    if g > 0:
        put(pn, g, vol)
    return g


def _priced_pns() -> set[str]:
    """PN BERHARGA di Accurate — hanya ini kandidat etalase (harga.xlsx TIDAK pernah
    dipakai sebagai harga jual, jadi part yang cuma berharga di sana tak dijual dan
    tak perlu dihangatkan berat/dimensinya)."""
    from . import accurate  # lazy: hindari import melingkar
    pns: set[str] = set()
    try:
        for it in accurate.all_items():
            if int(it.get("price") or 0) > 0:
                pns.add((it.get("pn") or "").strip().upper())
    except Exception:
        pass
    pns.discard("")
    return pns


def warm_once(max_fetch: int = _MAX_PER_CYCLE) -> dict:
    """Satu siklus: isi indeks berat SIMS untuk part BERHARGA yang belum ada."""
    if not getattr(sims, "_SIMS_OK", False):
        return {"skipped": "sims off"}
    pns = _priced_pns()
    fetched = ok = 0
    for pn in pns:
        if get(pn) > 0 and dim_known(pn):
            continue                     # berat & dimensi sudah lengkap → lewati (murah)
        if fetched >= max_fetch:
            break
        g = fetch_and_store(pn)          # fetch SIMS + simpan
        fetched += 1
        if g > 0:
            ok += 1
        time.sleep(_THROTTLE)
    logger.info("[simsweight] siklus: %d di-fetch, %d dapat berat (%d part berharga, %d di indeks)",
                fetched, ok, len(pns), len(_load()))
    return {"priced": len(pns), "fetched": fetched, "with_weight": ok, "index_size": len(_load())}


def _loop() -> None:
    time.sleep(_STARTUP_DELAY)
    while True:
        try:
            warm_once()
        except Exception:
            logger.exception("[simsweight] siklus warmer gagal")
        time.sleep(_INTERVAL)


def start() -> bool:
    """Mulai warmer latar (idempoten)."""
    global _started
    with _lock:
        if _started:
            return False
        _started = True
    threading.Thread(target=_loop, daemon=True, name="sims-weight-warmer").start()
    logger.info("[simsweight] warmer berat SIMS dimulai")
    return True
