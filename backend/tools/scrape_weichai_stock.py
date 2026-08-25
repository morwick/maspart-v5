# -*- coding: utf-8 -*-
"""
Panen STOK part dari portal reseller Weichai `weichai.tci-pnp.com/warehouse`
— SELURUH katalog yang TERSEDIA, sekali, ke file lokal supaya cek stok pemasok
jadi INSTAN & OFFLINE.

Latar: portal ini BUKAN JSON API — ia AdminLTE server-rendered (HTML). Data stok
harus di-scrape dari HTML. Diverifikasi live 2026-08-25 lewat HAR:
  • Login  : POST /sso/auth/login  (csrf_token + username + password + remember)
             csrf_token diambil dari field hidden di halaman login (kutip TUNGGAL);
             cookie `csrf_cookie` + `weichai_session` di-set otomatis.
  • Katalog: GET  /warehouse/p/1/catalogue?filter=1&availability=available
                  &branch[]=6&5&4&3&1 &order_by=latest&brand=0&type_goods=0&page=N
             25 kartu/hal. Kartu memuat qty AVAILABLE TOTAL per barcode (lintas
             cabang yang dipilih) — inilah "stok" yang dipanen di sini.
  • Detail : GET  /warehouse/p/1/catalogue/view?barcode=..&unit=..&brand=..
             memuat breakdown stok PER-CABANG (tabel "Order By Location").
             Hanya diambil bila --detail (mahal: 1 request per PN).

⛔ Portal ini TIDAK menampilkan HARGA untuk akun ini (semua Rp=0). Ini sumber
   STOK/ketersediaan SAJA. Harga jual tetap dari Accurate; modal dari SIMS.

⚠️ Cloudflare: request WAJIB pakai User-Agent browser, kalau tidak → 403.

Kredensial: data/weichai_stock_cred.json  {"username": "...", "password": "..."}
            (gitignored — jangan commit). Fallback env WEICHAI_PNP_USER/PASS.

Output: data/weichai_stock.json.gz
        {versi, diambil, total_api, halaman, cabang, lengkap, record:[
            {barcode, nama, brand, brand_id, unit_id, goods_id, qty, satuan}
        ]}

Sifat:
  • RESUMABLE — checkpoint per halaman ke <out>.part; ulangi perintah yang sama
    untuk melanjutkan setelah putus.
  • SOPAN — jeda antar halaman (default 1,5 dtk).
  • JUJUR — halaman yang gagal setelah retry DICATAT di 'halaman_gagal'; hasil
    menyandang 'lengkap: false'. ⛔ Jangan sajikan panen separuh sebagai lengkap.

Pemakaian:
    py backend/tools/scrape_weichai_stock.py                 # panen stok total
    py backend/tools/scrape_weichai_stock.py --detail        # + breakdown cabang
    py backend/tools/scrape_weichai_stock.py --delay 2.5     # lebih sopan
"""
from __future__ import annotations

import argparse
import gzip
import html
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

BASE = "https://weichai.tci-pnp.com"
LOGIN = BASE + "/sso/auth/login?redirect=" + \
    "https%3A%2F%2Fweichai.tci-pnp.com%2Fwarehouse%2Fwelcome"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Cabang yang dipilih (sama dgn HAR): 6,5,4,3,1. brand=0/type_goods=0 = semua.
BRANCHES = ["6", "5", "4", "3", "1"]
BRANCH_NAMES = {"1": "Bekasi", "3": "Morowali/SINOTRUK", "4": "Balikpapan",
                "5": "Shantui Jakarta", "6": "JKT GDG MM - WEICHAI"}

ROOT = Path(__file__).resolve().parents[2]          # repo root
DATA = ROOT / "data"
OUT = DATA / "weichai_stock.json.gz"
PART = DATA / "weichai_stock.json.part"
CRED = DATA / "weichai_stock_cred.json"


def _creds() -> tuple[str, str]:
    if CRED.exists():
        d = json.loads(CRED.read_text(encoding="utf-8"))
        return d["username"], d["password"]
    u, p = os.getenv("WEICHAI_PNP_USER"), os.getenv("WEICHAI_PNP_PASS")
    if u and p:
        return u, p
    sys.exit(f"⛔ Kredensial tak ditemukan. Buat {CRED} atau set "
             "WEICHAI_PNP_USER/WEICHAI_PNP_PASS")


def _unescape(s: str) -> str:
    # portal menyisipkan escape backslash (mis. 'SINOPEC\\ 150', 'GL\\_5')
    return re.sub(r"\\(.)", r"\1", html.unescape(s)).strip()


def login() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA,
                      "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                      "Accept-Language": "en-US,en;q=0.9"})
    lp = s.get(LOGIN, timeout=30)
    lp.raise_for_status()
    m = re.search(r"name='csrf_token' value='([0-9a-f]+)'", lp.text)
    if not m:
        sys.exit("⛔ csrf_token tak ditemukan di halaman login (layout berubah?)")
    u, p = _creds()
    r = s.post(LOGIN, timeout=30, data={
        "csrf_token": m.group(1), "username": u, "password": p, "remember": "1"})
    r.raise_for_status()
    # verifikasi sesi: katalog harus 200 & memuat 'Product Catalogue'
    chk = s.get(_cat_url(1), timeout=30)
    if "Product Catalogue" not in chk.text:
        sys.exit("⛔ Login gagal — kredensial salah atau sesi tak aktif.")
    return s


def _cat_url(page: int) -> str:
    br = "".join(f"&branch%5B%5D={b}" for b in BRANCHES)
    return (f"{BASE}/warehouse/p/1/catalogue?filter=1&availability=available"
            f"&order_by=latest{br}&brand=0&type_goods=0&page={page}")


CARD_SPLIT = 'class="col-sm-6 col-md-4 product-item"'


def parse_cards(htmltext: str) -> list[dict]:
    out = []
    for b in htmltext.split(CARD_SPLIT)[1:]:
        barcode = re.search(r'data-barcode="([^"]+)"', b)
        if not barcode:                    # kartu tanpa tombol add-to-cart → lewati
            continue
        goods = re.search(r'data-id-goods="(\d+)"', b)
        unit = re.search(r'data-id-unit="(\d+)"', b)
        brand_id = re.search(r'data-id-brand="(\d+)"', b)
        name = re.search(r'<h4 class="mt0 mb0" title="([^"]*)"', b)
        bp = re.search(r'text-muted">\s*([^<]*?)\s*-\s*[^<]+?\s*</p>', b)
        avail = re.search(r'AVAILABLE</p>\s*<h4[^>]*>\s*([\d.,]+)\s*([A-Za-z]+)', b)
        out.append({
            "barcode": barcode.group(1),
            "nama": _unescape(name.group(1)) if name else None,
            "brand": bp.group(1).strip() if bp else None,
            "brand_id": brand_id.group(1) if brand_id else None,
            "unit_id": unit.group(1) if unit else None,
            "goods_id": goods.group(1) if goods else None,
            "qty": _num(avail.group(1)) if avail else None,
            "satuan": avail.group(2) if avail else None,
        })
    return out


def _num(s: str) -> float | int:
    s = s.replace(".", "").replace(",", ".")
    f = float(s)
    return int(f) if f.is_integer() else f


def parse_branches(view_html: str) -> list[dict]:
    """Breakdown stok per-cabang dari halaman /view (tabel Order By Location)."""
    i = view_html.find("Order By Location")
    if i < 0:
        return []
    seg = view_html[i:i + 8000]
    rows = re.findall(r'<td class="bold">([^<]+)</td>\s*<td>\s*([\d.,]+)\s*([A-Za-z]+)',
                      seg)
    return [{"cabang": c.strip(), "qty": _num(q), "satuan": u} for c, q, u in rows]


def total_result(htmltext: str) -> int:
    m = re.search(r"Total result ([\d.,]+) items", htmltext)
    return int(m.group(1).replace(".", "").replace(",", "")) if m else 0


def main() -> None:
    try:                                             # konsol Windows cp1252 → utf-8
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=1.5, help="jeda antar halaman (dtk)")
    ap.add_argument("--detail", action="store_true",
                    help="ambil breakdown stok per-cabang (mahal: 1 req/PN)")
    ap.add_argument("--max-pages", type=int, default=0, help="0 = semua")
    args = ap.parse_args()

    # resume dari checkpoint bila ada
    done_pages: dict[int, list] = {}
    failed: list[int] = []
    if PART.exists():
        ck = json.loads(PART.read_text(encoding="utf-8"))
        done_pages = {int(k): v for k, v in ck.get("halaman", {}).items()}
        failed = ck.get("halaman_gagal", [])
        print(f"↻ lanjut dari checkpoint: {len(done_pages)} hal sudah ada")

    s = login()
    print("✓ login sukses")

    first = s.get(_cat_url(1), timeout=30).text
    total = total_result(first)
    n_pages = (total + 24) // 25
    if args.max_pages:
        n_pages = min(n_pages, args.max_pages)
    print(f"Total {total} item tersedia → {n_pages} halaman")

    for pg in range(1, n_pages + 1):
        if pg in done_pages:
            continue
        try:
            txt = first if pg == 1 else s.get(_cat_url(pg), timeout=30).text
            cards = parse_cards(txt)
            if not cards:
                raise ValueError("0 kartu ter-parse")
            done_pages[pg] = cards
            if pg in failed:
                failed.remove(pg)
            print(f"  hal {pg}/{n_pages}: {len(cards)} item")
        except Exception as e:                       # noqa: BLE001
            print(f"  ⚠️ hal {pg} GAGAL: {type(e).__name__} {e}")
            if pg not in failed:
                failed.append(pg)
        # checkpoint tiap halaman
        PART.write_text(json.dumps(
            {"halaman": {str(k): v for k, v in done_pages.items()},
             "halaman_gagal": failed}, ensure_ascii=False), encoding="utf-8")
        time.sleep(args.delay)

    records: list[dict] = []
    for pg in sorted(done_pages):
        records.extend(done_pages[pg])

    # opsional: breakdown per-cabang
    if args.detail:
        print(f"→ ambil breakdown cabang untuk {len(records)} PN…")
        for i, r in enumerate(records, 1):
            try:
                vu = (f"{BASE}/warehouse/p/1/catalogue/view?barcode={r['barcode']}"
                      f"&unit={r['unit_id']}&brand={r['brand_id']}")
                r["cabang"] = parse_branches(s.get(vu, timeout=30).text)
            except Exception as e:                   # noqa: BLE001
                r["cabang"] = None
                print(f"  ⚠️ detail {r['barcode']} gagal: {e}")
            if i % 50 == 0:
                print(f"    {i}/{len(records)}")
            time.sleep(args.delay)

    lengkap = not failed and len(records) >= total
    out = {
        "versi": 1,
        "diambil": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sumber": "weichai.tci-pnp.com/warehouse (scrape HTML)",
        "cabang": {b: BRANCH_NAMES[b] for b in BRANCHES},
        "total_api": total,
        "halaman": n_pages,
        "lengkap": lengkap,
        "halaman_gagal": sorted(failed),
        "catatan": "STOK saja — portal tak menampilkan harga (Rp=0 utk akun ini).",
        "record": records,
    }
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"\n✓ tulis {OUT}  ({len(records)} record, lengkap={lengkap})")
    if failed:
        print(f"⚠️ {len(failed)} halaman gagal: {sorted(failed)} — jalankan ulang "
              "perintah yang sama untuk melengkapi.")
    else:
        PART.unlink(missing_ok=True)                 # bersihkan checkpoint bila tuntas


if __name__ == "__main__":
    main()
