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
_map: dict[str, int] | None = None
_started = False


def _file():
    return get_settings().data_path / "sims_weights.json"


def _load() -> dict[str, int]:
    global _map
    if _map is None:
        try:
            _map = {k.upper(): int(v) for k, v in json.loads(_file().read_text("utf-8")).items()}
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
    """Berat (gram) dari indeks persisten; 0 bila belum ada."""
    return int(_load().get((pn or "").strip().upper(), 0) or 0)


def put(pn: str, grams: int) -> None:
    key = (pn or "").strip().upper()
    if not key or grams <= 0:
        return
    with _lock:
        _load()[key] = int(grams)
        _save()


def fetch_and_store(pn: str) -> int:
    """Ambil berat SIMS LIVE (login/fetch) lalu simpan ke indeks. 0 bila tak ada."""
    try:
        g = sims.get_part_weight_grams(pn)
    except Exception:
        g = 0
    if g > 0:
        put(pn, g)
    return g


def _priced_pns() -> set[str]:
    """PN BERHARGA (Accurate price>0 ∪ harga.xlsx) — hanya ini kandidat etalase."""
    from . import accurate, part_index  # lazy: hindari import melingkar
    pns: set[str] = set()
    try:
        for it in accurate.all_items():
            if int(it.get("price") or 0) > 0:
                pns.add((it.get("pn") or "").strip().upper())
    except Exception:
        pass
    try:
        for pn, disp in part_index.harga_map().items():
            if int(re.sub(r"[^\d]", "", str(disp)) or 0) > 0:
                pns.add((pn or "").strip().upper())
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
        if get(pn) > 0:
            continue                     # sudah ada di indeks → lewati (murah)
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
