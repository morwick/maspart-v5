"""
Service SIMS — reuse modul `sims_fetcher.py` milik project Streamlit untuk
mengambil URL gambar part dari SIMS (cache di images/image_links.json,
fallback login RSA bila cache miss).

Tidak menulis ulang logika SIMS: kita import modul root apa adanya, lalu
arahkan path cache-nya ke folder `images/` di root project (karena backend
berjalan dari folder backend/).
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..core.config import get_settings

# Root project = parent dari folder backend/ (sibling: data/, images/).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
# Modul SIMS/compare yang di-reuse ada di backend/shared/ (di-import top-level).
_SHARED_DIR = Path(__file__).resolve().parents[2] / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

_images_dir = _PROJECT_ROOT / "images"

_SIMS_OK = False
try:
    import sims_fetcher as _sf  # type: ignore

    # Arahkan path cache JSON ke root project (absolut).
    _sf.IMAGES_JSON = _images_dir / "image_links.json"
    _sf.PART_INFO_JSON = _images_dir / "part_info.json"
    _SIMS_OK = True
except Exception as _e:  # pragma: no cover
    _sf = None
    _SIMS_IMPORT_ERR = str(_e)

# Harga (CNY) dari SIMS — modul terpisah, cache JSON sendiri.
_PRICE_OK = False
try:
    import sims_price_fetcher as _spf  # type: ignore

    _spf.PRICE_CACHE_FILE = _images_dir / "part_price_cache.json"
    _PRICE_OK = True
except Exception:  # pragma: no cover
    _spf = None


def available() -> bool:
    return _SIMS_OK


def get_images(part_number: str, force_refresh: bool = False) -> list[str]:
    """
    Return list URL gambar SIMS untuk part_number (bisa kosong).
    Non-fatal: error apa pun → list kosong.
    """
    pn = (part_number or "").strip()
    if not _SIMS_OK or not pn:
        return []
    try:
        urls, _err = _sf.get_sims_images(pn, force_refresh=force_refresh)
        return list(urls or [])
    except Exception:
        return []


def get_part_info(part_number: str, force_refresh: bool = False) -> dict:
    """Return info part dari SIMS (mis. {'partName': ..., 'roughWeightKg': ...})
    atau {} bila gagal. `force_refresh` melewati cache part_info.json."""
    pn = (part_number or "").strip()
    if not _SIMS_OK or not pn:
        return {}
    try:
        info, _err = _sf.get_sims_part_info(pn, force_refresh=force_refresh)
        return info or {}
    except Exception:
        return {}


def get_part_weight_grams(part_number: str) -> int:
    """Berat KIRIM (gram) part dari SIMS — pakai berat KOTOR (rough) bila ada,
    jatuh ke berat bersih. 0 bila tak tersedia. Hasil ikut ter-cache di
    part_info.json (lewat get_part_info)."""
    info = get_part_info(part_number)
    # Cache lama (sebelum field berat ditambah) tak punya kunci ini → segarkan sekali.
    if info and "roughWeightKg" not in info:
        info = get_part_info(part_number, force_refresh=True)
    if not info:
        return 0
    kg = info.get("roughWeightKg") or info.get("netWeightKg")
    try:
        g = int(round(float(kg) * 1000)) if kg else 0
    except Exception:
        g = 0
    return g if g > 0 else 0


def _part_info_cached(part_number: str) -> dict:
    """Baca part_info.json TANPA jaringan (cache-only). {} bila miss / cache lama."""
    if not _SIMS_OK:
        return {}
    key = (part_number or "").strip().upper()
    if not key:
        return {}
    try:
        return _sf._load_part_info_json().get(key) or {}
    except Exception:
        return {}


def get_part_weight_grams_cached(part_number: str) -> int:
    """Seperti get_part_weight_grams tapi HANYA dari cache (tanpa login/fetch) →
    0 bila belum ter-cache. Dipakai di jalur cepat (daftar pencarian) agar tak
    memicu fetch SIMS per baris."""
    info = _part_info_cached(part_number)
    if not info or "roughWeightKg" not in info:
        return 0
    kg = info.get("roughWeightKg") or info.get("netWeightKg")
    try:
        g = int(round(float(kg) * 1000)) if kg else 0
    except Exception:
        g = 0
    return g if g > 0 else 0


# Pembagi berat volumetrik kurir domestik Indonesia (JNE/J&T/SiCepat/POS):
# (p × l × t dalam cm) / 6000 = kg. Kurir menagih yang LEBIH BESAR antara berat
# asli dan berat volumetrik — barang besar-ringan (filter, kaca spion) jauh lebih
# mahal dari beratnya.
VOLUMETRIC_DIVISOR = 6000


def _vol_grams(info: dict) -> int:
    """Berat volumetrik (gram) dari dimensi SIMS. 0 bila dimensi tak lengkap."""
    try:
        l = float(info.get("lengthCm") or 0)
        w = float(info.get("widthCm") or 0)
        h = float(info.get("heightCm") or 0)
    except (TypeError, ValueError):
        return 0
    if l <= 0 or w <= 0 or h <= 0:
        return 0
    return int(round(l * w * h / VOLUMETRIC_DIVISOR * 1000))


def get_part_volumetric_grams(part_number: str) -> int:
    """Berat VOLUMETRIK (gram) dari dimensi resmi SIMS — boleh login/fetch. 0 bila
    dimensi tak ada. Dipakai untuk ongkir: kurir menagih max(berat asli, volumetrik)."""
    info = get_part_info(part_number)
    if info and "lengthCm" not in info:
        info = get_part_info(part_number, force_refresh=True)
    return _vol_grams(info or {})


def get_part_volumetric_grams_cached(part_number: str) -> int:
    """Seperti di atas tapi HANYA dari cache (tanpa jaringan). 0 bila belum ter-cache."""
    return _vol_grams(_part_info_cached(part_number))


def get_part_spec(part_number: str) -> dict:
    """Spesifikasi fisik resmi SIMS untuk ditampilkan: berat (kg), dimensi (cm),
    satuan, kemasan minimum, merek. {} bila tak ada data."""
    info = get_part_info(part_number)
    if info and "roughWeightKg" not in info:
        info = get_part_info(part_number, force_refresh=True)
    if not info:
        return {}
    out: dict = {}
    net, rough = info.get("netWeightKg"), info.get("roughWeightKg")
    if net is not None:
        out["berat_bersih_kg"] = net
    if rough is not None:
        out["berat_kirim_kg"] = rough  # berat kotor — dipakai untuk ongkir
    l, w, h = info.get("lengthCm"), info.get("widthCm"), info.get("heightCm")
    if l and w and h:
        out["dimensi_cm"] = f"{l} x {w} x {h}"
    if info.get("partUnit"):
        out["satuan"] = info["partUnit"]
    if info.get("minPackNum"):
        out["kemasan_minimum"] = info["minPackNum"]
    if info.get("brandName"):
        out["merek"] = info["brandName"]
    return out


def get_part_equivalents(part_number: str) -> dict:
    """Persamaan/pengganti part resmi Sinotruk (tabel partEquivalentQuery SIMS).
    Klasifikasi arah relatif terhadap PN yang ditanya:
      - `digantikan_oleh` = PN PENGGANTI (part baru) bila PN ditanya = part LAMA.
      - `menggantikan`    = PN LAMA yang digantikan bila PN ditanya = part BARU.
    Return {found, part_number, digantikan_oleh:[{pn,nama}], menggantikan:[{pn,nama}]}.
    Non-fatal: {} bila SIMS tak tersedia / kosong."""
    pn = (part_number or "").strip()
    if not _SIMS_OK or not pn:
        return {}
    try:
        recs = _sf.fetch_part_equivalents(pn)
    except Exception:
        return {}
    if not recs:
        return {"found": False, "part_number": pn}

    def _norm(x: str) -> str:
        return "".join((x or "").upper().split())

    q = _norm(pn)
    diganti: list[dict] = []   # PN pengganti (part baru)
    lama: list[dict] = []      # PN lama yang digantikan
    seen: set[tuple[str, str]] = set()
    for r in recs:
        pre_pn = (r.get("preSpGoodsNo") or "").strip()
        aft_pn = (r.get("afterSpGoodsNo") or "").strip()
        pre_nm = " ".join((r.get("preGoodsName") or "").split())
        aft_nm = " ".join((r.get("afterGoodsName") or "").split())
        pre_n, aft_n = _norm(pre_pn), _norm(aft_pn)
        # PN ditanya muncul di kolom SEBELUM → penggantinya = kolom SESUDAH.
        if q and (q in pre_n or pre_n in q) and aft_pn:
            key = ("d", aft_n)
            if key not in seen:
                seen.add(key); diganti.append({"pn": aft_pn, "nama": aft_nm or None})
        # PN ditanya muncul di kolom SESUDAH → yang digantikannya = kolom SEBELUM.
        elif q and (q in aft_n or aft_n in q) and pre_pn:
            key = ("m", pre_n)
            if key not in seen:
                seen.add(key); lama.append({"pn": pre_pn, "nama": pre_nm or None})
    return {"found": bool(diganti or lama), "part_number": pn,
            "digantikan_oleh": diganti, "menggantikan": lama}


# ── INDEKS PERSAMAAN/PENGGANTI (partEquivalentQuery penuh, ~17rb baris) ──────
# Tabel penggantian jarang berubah & tak besar → ditarik SEKALI (paginasi) ke
# indeks in-memory, dibagi ke semua fitur (mis. cari_part menyisipkan persamaan
# TANPA panggilan live per-part). Refresh terjadwal di latar (TTL 12 jam).
import re as _re
import threading as _threading
import time as _time

_EQUIV_TTL = 12 * 3600
_equiv_lock = _threading.Lock()
_equiv_index: dict = {"ts": 0.0, "by_pn": {}}   # {norm_pn: {digantikan_oleh:[{pn,nama}], menggantikan:[{pn,nama}]}}
_equiv_sched_started = False

# ── PENCARIAN MASTER by NAMA (pageDealer partName LIKE, ±670rb part) ─────────
# Jaring terakhir cari_part saat katalog lokal nihil. Memo in-memory kecil TTL
# 1 jam (BUKAN part_info.json — file itu keyed-PN utk berat/ongkir).
_master_name_memo: dict[str, tuple] = {}   # name.lower() -> (ts, rows)
_MASTER_NAME_TTL = 3600


def search_master_by_name(name: str, limit: int = 8) -> list:
    """Cari master part SIMS by NAMA (EN). Ringkas [{partCode,partName,brandName}];
    [] bila kosong/gagal. Hasil di-memo 1 jam per kata kunci."""
    key = str(name or "").strip().lower()
    if not key or not _SIMS_OK:
        return []
    hit = _master_name_memo.get(key)
    if hit and _time.time() - hit[0] < _MASTER_NAME_TTL:
        return hit[1][:limit]
    rows = _sf.fetch_part_info_by_name(key, page_size=limit)
    if len(_master_name_memo) > 256:
        _master_name_memo.clear()
    _master_name_memo[key] = (_time.time(), rows)
    return rows


def _eq_norm(pn: str) -> str:
    return "".join((pn or "").upper().split())


def _eq_base(pn: str) -> str:
    """Buang suffix varian ('+001/1', '/1') agar PN katalog tanpa suffix tetap cocok."""
    return _re.sub(r"(\+\d+)?(/\d+)?$", "", _eq_norm(pn))


def refresh_equivalents(force: bool = False) -> int:
    """Bangun/segarkan indeks penggantian penuh (paginasi partEquivalentQuery).
    Hormati TTL kecuali force. Return jumlah PN ter-indeks. Non-fatal."""
    if not _SIMS_OK:
        return 0
    with _equiv_lock:
        if not force and _equiv_index["by_pn"] and _time.time() - _equiv_index["ts"] < _EQUIV_TTL:
            return len(_equiv_index["by_pn"])
    page_size = 500
    try:
        recs, total = _sf.fetch_equivalents_page(1, page_size)
        all_recs = list(recs)
        pages = (total + page_size - 1) // page_size if total else 1
        for p in range(2, pages + 1):
            more, _t = _sf.fetch_equivalents_page(p, page_size)
            if not more:
                break
            all_recs.extend(more)
    except Exception:
        return len(_equiv_index["by_pn"])
    if not all_recs:
        return len(_equiv_index["by_pn"])

    idx: dict = {}

    def _reg(key_pn: str, direction: str, other_pn: str, other_nm: str) -> None:
        if not key_pn or not other_pn:
            return
        on = _eq_norm(other_pn)
        for k in {_eq_norm(key_pn), _eq_base(key_pn)}:
            if not k:
                continue
            e = idx.setdefault(k, {"digantikan_oleh": [], "menggantikan": []})
            lst = e[direction]
            if not any(_eq_norm(x["pn"]) == on for x in lst):
                lst.append({"pn": other_pn, "nama": other_nm or None})

    for r in all_recs:
        pre = (r.get("preSpGoodsNo") or "").strip()
        aft = (r.get("afterSpGoodsNo") or "").strip()
        pre_nm = " ".join((r.get("preGoodsName") or "").split())
        aft_nm = " ".join((r.get("afterGoodsName") or "").split())
        _reg(pre, "digantikan_oleh", aft, aft_nm)   # PN lama → penggantinya
        _reg(aft, "menggantikan", pre, pre_nm)      # PN baru → yang digantikan
    with _equiv_lock:
        _equiv_index["by_pn"] = idx
        _equiv_index["ts"] = _time.time()
    return len(idx)


def equivalents_for(part_number: str) -> dict:
    """{digantikan_oleh:[{pn,nama}], menggantikan:[{pn,nama}]} dari INDEKS (instan,
    tanpa jaringan). {} bila tak ada / indeks belum siap."""
    idx = _equiv_index["by_pn"]
    if not idx or not part_number:
        return {}
    return idx.get(_eq_norm(part_number)) or idx.get(_eq_base(part_number)) or {}


def equivalents_count() -> int:
    return len(_equiv_index["by_pn"])


def start_equivalents_refresh() -> bool:
    """Thread daemon: bangun indeks penggantian sekali di awal, lalu segarkan tiap
    _EQUIV_TTL. Idempoten. Tarikan pertama jalan segera (indeks hangat sejak awal)."""
    global _equiv_sched_started
    if not _SIMS_OK:
        return False
    with _equiv_lock:
        if _equiv_sched_started:
            return False
        _equiv_sched_started = True

    def _loop():
        while True:
            try:
                n = refresh_equivalents(force=True)
                print(f"[sims] indeks persamaan OK ({n} PN); berikutnya {_EQUIV_TTL // 3600} jam lagi")
            except Exception as e:  # pragma: no cover
                print(f"[sims] refresh indeks persamaan gagal: {e}")
            _time.sleep(_EQUIV_TTL)

    _threading.Thread(target=_loop, daemon=True, name="sims-equiv-refresh").start()
    return True


def price_available() -> bool:
    return _PRICE_OK


def get_price(part_number: str, force_refresh: bool = False) -> tuple[float | None, str | None]:
    """
    Harga part (CNY) dari SIMS, dengan fallback PN tanpa suffix '/<digit>'.
    Return (harga_cny_atau_None, info_error_atau_None). Field error ke-2 juga
    dipakai sebagai catatan 'via PN lain' (mirror batch_harga_engine._fetch_one).
    """
    import re

    pn = (part_number or "").strip()
    if not _PRICE_OK or not pn:
        return None, "price fetcher tidak tersedia" if not _PRICE_OK else None
    try:
        price, err = _spf.get_sims_part_price(pn, force_refresh=force_refresh)
        if price is None and re.search(r"/\d+$", pn):
            fallback = re.sub(r"/\d+$", "", pn)
            price2, _err2 = _spf.get_sims_part_price(fallback, force_refresh=force_refresh)
            if price2 is not None:
                return price2, f"(via {fallback})"
        return price, err
    except Exception as e:
        return None, str(e)
