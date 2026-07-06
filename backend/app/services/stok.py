"""
Service: Stok — daftar stok LIVE seluruh barang dari ERP Accurate.

Sumber data = ``accurate.all_items`` (indeks ber-cache TTL, auto-login). Modul ini
hanya menambah lapisan filter/urut/paginasi + format tampilan & Excel, sejajar
dengan pola ``harga`` (List Harga). Kolom tampilan mengikuti Daftar Harga:
Part Number, Part Name, Stok, Satuan.
"""
from __future__ import annotations

import io

import pandas as pd

from . import accurate


def _fmt_stok(v) -> str:
    """Angka stok → string ID (tanpa desimal bila bulat)."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "0"
    if n == int(n):
        return f"{int(n):,}".replace(",", ".")
    return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def stock_rows(q: str = "", sort: str = "pn", *, force: bool = False) -> list[dict]:
    """Barang Accurate ternormalisasi, terfilter & terurut (belum dipaginasi)."""
    items = accurate.all_items(force=force)
    term = (q or "").strip().upper()
    if term:
        items = [
            it for it in items
            if term in (it.get("pn") or "").upper()
            or term in (it.get("name") or "").upper()
            or term in (it.get("no") or "").upper()
        ]
    if sort in ("stok_asc", "stok_desc"):
        items = sorted(
            items,
            key=lambda it: it.get("available_to_sell") or 0,
            reverse=(sort == "stok_desc"),
        )
    elif sort == "name":
        items = sorted(items, key=lambda it: (it.get("name") or "").upper())
    else:  # "pn" (default)
        items = sorted(items, key=lambda it: (it.get("pn") or "").upper())
    return items


def total_count(*, force: bool = False) -> int:
    """Jumlah seluruh barang Accurate (tanpa filter)."""
    return len(accurate.all_items(force=force))


def display_rows(items: list[dict]) -> list[dict]:
    """Bentuk baris tampilan (kolom seperti Daftar Harga)."""
    return [
        {
            "Part Number": it.get("pn") or "",
            "Part Name": it.get("name") or "",
            "Stok": _fmt_stok(it.get("available_to_sell")),
            "Satuan": it.get("unit") or "",
        }
        for it in items
    ]


def to_excel_bytes(items: list[dict]) -> bytes:
    """Excel daftar stok (angka stok sebagai nilai numerik, bukan string)."""
    df = pd.DataFrame(
        [
            {
                "Part Number": it.get("pn") or "",
                "Part Name": it.get("name") or "",
                "Stok": it.get("available_to_sell") or 0,
                "Satuan": it.get("unit") or "",
            }
            for it in items
        ],
        columns=["Part Number", "Part Name", "Stok", "Satuan"],
    )
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()
