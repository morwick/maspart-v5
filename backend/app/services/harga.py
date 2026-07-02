"""
Service: Harga — List Harga (dari harga.xlsx), kurs CNY→IDR, Cari & Batch
harga dari SIMS. Decoupled dari Streamlit.

Mirror app.py render_harga_tab (3 sub-tab) + sims_price_fetcher.
"""
from __future__ import annotations

import io
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from .supabase_client import download_storage_object
from . import sims

# ── List Harga (harga.xlsx) ──────────────────────────────────────────
_lock = threading.Lock()
_state: dict = {"df": None}


def refresh() -> pd.DataFrame:
    df = pd.DataFrame()
    b = download_storage_object("harga.xlsx")
    if b:
        try:
            raw = pd.read_excel(io.BytesIO(b), dtype=str)
            rename = {}
            berat_is_kg = False
            for c in raw.columns:
                cl = str(c).strip().lower()
                if cl in ("part number", "partnumber", "no part", "kode"):
                    rename[c] = "Part Number"
                elif cl in ("part name", "nama", "deskripsi"):
                    rename[c] = "Part Name"
                elif cl in ("harga", "price"):
                    rename[c] = "Harga"
                elif "berat" in cl or "weight" in cl:
                    rename[c] = "Berat"
                    if "kg" in cl:
                        berat_is_kg = True
            raw = raw.rename(columns=rename)
            if "Part Number" in raw.columns and "Harga" in raw.columns:
                if "Part Name" not in raw.columns:
                    raw["Part Name"] = ""
                cols = ["Part Number", "Part Name", "Harga"]
                if "Berat" in raw.columns:
                    cols.append("Berat")
                df = raw[cols].copy()
                df["Part Number"] = df["Part Number"].fillna("").astype(str).str.strip()
                df["Part Name"] = df["Part Name"].fillna("").astype(str)
                df = df[df["Part Number"].str.len() > 0].reset_index(drop=True)
                # Normalisasi berat → gram (int). Tanpa kolom Berat → 0 (pakai estimasi default).
                if "Berat" in df.columns:
                    df["Berat_g"] = df["Berat"].map(lambda v: _grams_from(v, berat_is_kg))
                    df = df.drop(columns=["Berat"])
                else:
                    df["Berat_g"] = 0
        except Exception:
            pass
    global _price_map, _weight_map
    _price_map = None
    _weight_map = None
    with _lock:
        _state["df"] = df
    return df


def _ensure() -> pd.DataFrame:
    if _state["df"] is None:
        refresh()
    return _state["df"]


_price_map: dict[str, int] | None = None


def price_for(pn: str) -> tuple[int, str]:
    """Return (harga_int, part_name) untuk part number dari harga.xlsx. (0, '') bila tak ada."""
    global _price_map
    df = _ensure()
    if _price_map is None:
        _price_map = {}
        _name_map: dict[str, str] = {}
        for _, row in df.iterrows():
            key = str(row["Part Number"]).strip().upper()
            if key:
                n = _num(row["Harga"])
                _price_map[key] = int(n) if n is not None else 0
                _name_map[key] = str(row.get("Part Name", "") or "")
        _state["_name_map"] = _name_map
    key = (pn or "").strip().upper()
    return _price_map.get(key, 0), (_state.get("_name_map", {}) or {}).get(key, "")


_weight_map: dict[str, int] | None = None


def weight_for(pn: str, allow_remote: bool = False) -> int:
    """Berat part (gram). Prioritas: kolom Berat manual di harga.xlsx → lalu berat
    resmi SIMS. `allow_remote=False` (default) HANYA baca cache SIMS (cepat — untuk
    daftar pencarian); `True` boleh login/fetch SIMS (alur pesanan/ongkir/detail)."""
    global _weight_map
    df = _ensure()
    if _weight_map is None:
        _weight_map = {}
        if "Berat_g" in df.columns:
            for _, row in df.iterrows():
                key = str(row["Part Number"]).strip().upper()
                if not key:
                    continue
                try:
                    g = int(row["Berat_g"])
                except Exception:
                    g = 0
                if g > 0:
                    _weight_map[key] = g
    g = _weight_map.get((pn or "").strip().upper(), 0)
    if g > 0:
        return g
    # Fallback: berat resmi pabrik dari SIMS (kg→gram), bila admin belum mengisi
    # kolom Berat di harga.xlsx. Membuka blokir pembelian & ongkir akurat tanpa
    # input manual. Non-fatal bila SIMS down → 0.
    try:
        return sims.get_part_weight_grams(pn) if allow_remote \
            else sims.get_part_weight_grams_cached(pn)
    except Exception:
        return 0


def total_weight_grams(items: list[tuple[str, int]], default_each: int,
                       allow_remote: bool = True) -> int:
    """Total berat (gram) dari daftar (part_number, qty). Part tanpa berat → pakai
    estimasi `default_each` gram per item. Minimal `default_each`. `allow_remote`
    boleh fetch berat SIMS (dipakai utk ongkir; item sedikit)."""
    total = 0
    for pn, qty in items:
        try:
            q = max(1, int(qty or 1))
        except Exception:
            q = 1
        w = weight_for(pn, allow_remote=allow_remote)
        total += (w if w > 0 else default_each) * q
    return max(default_each, total) if items else default_each


def _num(v) -> float | None:
    try:
        s = re.sub(r"[^\d.]", "", str(v))
        return float(s) if s else None
    except Exception:
        return None


def _grams_from(v, is_kg: bool) -> int:
    """Konversi nilai berat dari sel Excel → gram (int). 0 bila kosong/invalid.
    Mode gram: ambil digit saja ("1.500" → 1500). Mode kg: parse desimal × 1000."""
    s = str(v or "").strip()
    if not s:
        return 0
    try:
        if is_kg:
            s2 = re.sub(r"[^\d.]", "", s.replace(",", "."))
            return int(round(float(s2) * 1000)) if s2 else 0
        s2 = re.sub(r"[^\d]", "", s)
        return int(s2) if s2 else 0
    except Exception:
        return 0


def fmt_rp(v) -> str:
    n = _num(v)
    if n is None:
        return str(v)
    return f"Rp {n:,.0f}".replace(",", ".")


def list_harga(q: str = "", sort: str = "pn") -> pd.DataFrame:
    df = _ensure()
    if df.empty:
        return df
    if q and q.strip():
        up = q.strip().upper()
        m = df["Part Number"].astype(str).str.upper().str.contains(up, na=False, regex=False) | \
            df["Part Name"].astype(str).str.upper().str.contains(up, na=False, regex=False)
        df = df[m]
    if sort in ("harga_asc", "harga_desc"):
        df = df.assign(_k=df["Harga"].map(_num)).sort_values(
            "_k", ascending=(sort == "harga_asc"), na_position="last"
        ).drop(columns="_k")
    elif sort == "name":
        df = df.sort_values("Part Name", key=lambda x: x.astype(str).str.upper())
    else:
        df = df.sort_values("Part Number")
    return df.reset_index(drop=True)


def total_count() -> int:
    return len(_ensure())


def display_frame(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "Part Number": df["Part Number"].to_numpy(),
        "Part Name": df["Part Name"].to_numpy(),
        "Harga (Rp)": df["Harga"].map(fmt_rp).to_numpy(),
    })


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


# ── Kurs CNY → IDR ───────────────────────────────────────────────────
_RATE_TTL = 1800
_FALLBACK_RATE = 2200.0
_rate: dict = {"value": None, "ts": 0.0}


def get_rate(force: bool = False) -> tuple[float, str | None]:
    now = time.time()
    if not force and _rate["value"] and (now - _rate["ts"]) < _RATE_TTL:
        return _rate["value"], None
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/CNY", timeout=8)
        r.raise_for_status()
        v = r.json().get("rates", {}).get("IDR")
        if v:
            _rate.update(value=float(v), ts=now)
            return float(v), None
        return _FALLBACK_RATE, "IDR tidak ada di response"
    except Exception as e:
        _rate.update(value=_FALLBACK_RATE, ts=now)
        return _FALLBACK_RATE, f"API kurs gagal ({e}), pakai fallback Rp {_FALLBACK_RATE:,.0f}/CNY"


# ── Cari & Batch harga dari SIMS ─────────────────────────────────────
def cari_harga(pn: str, force_refresh: bool = False) -> dict:
    rate, _err = get_rate()
    cny, note = sims.get_price(pn, force_refresh=force_refresh)
    idr = round(cny * rate) if cny is not None else None
    return {"pn": pn.strip(), "cny": cny, "idr": idr, "rate": rate, "note": note}


def batch_harga(part_numbers: list[str], max_workers: int = 5) -> dict:
    rate, _err = get_rate()
    results: dict[str, dict] = {}

    def _one(pn: str) -> dict:
        cny, note = sims.get_price(pn)
        idr = round(cny * rate) if cny is not None else None
        return {
            "pn": pn,
            "cny": cny,
            "idr": idr,
            "note": note,
            "status": "ok" if cny is not None else "not_found",
        }

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for r in ex.map(_one, part_numbers):
            results[r["pn"]] = r

    ordered = [results[pn] for pn in part_numbers if pn in results]
    found = sum(1 for r in ordered if r["status"] == "ok")
    return {"rate": rate, "count": len(ordered), "found": found, "results": ordered}


def batch_to_excel(rate: float, rows: list[dict]) -> bytes:
    df = pd.DataFrame([
        {
            "Part Number": r.get("pn", ""),
            "Harga SIMS (CNY)": r.get("cny") if r.get("cny") is not None else "",
            "Harga (IDR)": r.get("idr") if r.get("idr") is not None else "",
            "Keterangan": r.get("note") or ("" if r.get("status") == "ok" else "Tidak ditemukan"),
        }
        for r in rows
    ])
    return to_excel_bytes(df)
