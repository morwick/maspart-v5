"""
Buat Penawaran Penjualan Accurate OTOMATIS saat order lunas.

Dipicu dari router pembayaran tepat setelah `orders.mark_paid` sukses (transisi
menunggu_pembayaran → diproses, jadi sekali per order). Dijalankan di THREAD LATAR
agar webhook/polling tetap cepat, dan bersifat BEST-EFFORT: kegagalan Accurate
TAK PERNAH membatalkan pembayaran — order tetap lunas, hasilnya dicatat di order.

Kebijakan (disepakati pemilik):
  • Customer: DICOCOKKAN dari nama penerima (recipient_name). Tak ada / ambigu →
    di-SKIP (bukan error) — admin bisa buat manual lewat asisten.
  • PN tak ada di Accurate ATAU harga jual Rp 0 → SKIP seluruh penawaran (jangan
    buat sebagian). Order tetap lunas.

⛔ Sama seperti tool asisten: HANYA MEMBUAT penawaran; tak mengubah/menghapus
apa pun di Accurate. NOMOR = MASPART-NN (penomoran otomatis Accurate tak dipakai).
"""
from __future__ import annotations

import logging
import threading
import time

from . import accurate, orders

logger = logging.getLogger("maspart.penawaran")

# Penjaga idempoten DALAM-PROSES: cegah webhook + polling yang datang hampir
# bersamaan memicu DUA penawaran untuk order yang sama (cek kolom DB penawaran_*
# baru bekerja setelah migrasi dijalankan). Order_code kecil → set tak dibatasi ketat.
_lock = threading.Lock()
_seen: set[str] = set()


def _match_customer(name: str):
    """(customer|None, alasan_skip|None). Cocok persis menang; 1 hasil diterima;
    >1 tanpa cocok-persis = ambigu → skip (jangan menebak)."""
    cust = accurate.search_customers(name, limit=20)
    if not cust:
        return None, f"customer '{name}' tak ditemukan di Accurate"
    exact = [c for c in cust if (c.get("name") or "").strip().lower() == name.strip().lower()]
    if exact:
        return exact[0], None
    if len(cust) == 1:
        return cust[0], None
    return None, f"nama '{name}' ambigu ({len(cust)} customer cocok)"


def create_for_order(order: dict) -> dict:
    """Buat penawaran untuk SATU order lunas. TAK PERNAH raise.
    Return {status: created|skip|failed, number?, id?, customer?, note?}."""
    code = order.get("order_code") or ""
    if not accurate.available():
        return {"status": "skip", "note": "Accurate tak aktif"}

    name = (order.get("recipient_name") or order.get("username") or "").strip()
    if not name:
        return {"status": "skip", "note": "nama penerima kosong"}

    try:
        # Aksi non-interaktif → paksa sesi (abaikan cooldown backoff refresh latar).
        accurate.ensure_session_force()
    except accurate.AccurateError as e:
        return {"status": "failed", "note": f"login Accurate gagal: {str(e)[:150]}"}

    try:
        pel, cerr = _match_customer(name)
        if not pel:
            return {"status": "skip", "note": cerr}

        lines, missing, noprice = [], [], []
        for it in (order.get("items") or []):
            pn = str(it.get("part_number") or "").strip()
            try:
                qty = float(it.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0
            if not pn or qty <= 0:
                continue
            acc_it = accurate.item_for_quotation(pn)
            if not acc_it:
                missing.append(pn)
                continue
            price = float(acc_it.get("price") or 0)
            if price <= 0:
                noprice.append(acc_it.get("pn") or pn)
                continue
            lines.append({"item_id": acc_it["id"], "name": acc_it["name"], "qty": qty,
                          "unit_price": price, "unit_id": acc_it["unit_id"]})

        if missing or noprice:
            bits = []
            if missing:
                bits.append(f"PN tak ada di Accurate: {', '.join(missing)}")
            if noprice:
                bits.append(f"tanpa harga jual: {', '.join(noprice)}")
            return {"status": "skip", "note": "; ".join(bits) + " — penawaran tak dibuat"}
        if not lines:
            return {"status": "skip", "note": "tak ada baris barang valid"}

        number = accurate.next_quotation_number()
        res = accurate.create_sales_quotation(
            number=number, customer_id=pel["id"], lines=lines,
            transdate=time.strftime("%d/%m/%Y"),
            description=f"Order {code} — otomatis (pembayaran lunas)")
        qid = res.get("id")
        if not qid:
            return {"status": "failed", "note": "penawaran tak mengembalikan id"}
        return {"status": "created", "number": res.get("number") or number,
                "id": qid, "customer": pel.get("name")}
    except Exception as e:  # jaring pengaman terakhir — jangan pernah bocor ke pemanggil
        logger.exception("buat penawaran otomatis gagal (order %s)", code)
        return {"status": "failed", "note": str(e)[:200]}


def create_for_order_bg(order_code: str) -> None:
    """Jalankan pembuatan penawaran di THREAD LATAR (non-blocking, best-effort).
    Idempoten: penjaga dalam-proses + cek nomor penawaran yang sudah ada di DB."""
    if not order_code:
        return
    with _lock:
        if order_code in _seen:
            return
        _seen.add(order_code)

    def _run() -> None:
        try:
            o = orders.get_order(order_code)   # scope admin (semua field + items)
            if not o:
                return
            if (o.get("penawaran_number") or "").strip():
                return  # sudah pernah dibuat (mis. setelah restart proses)
            res = create_for_order(o)
            orders.set_penawaran_result(order_code, res)
            logger.info("penawaran order %s → %s (%s)", order_code,
                        res.get("status"), res.get("number") or res.get("note"))
        except Exception:
            logger.exception("thread penawaran gagal (order %s)", order_code)

    threading.Thread(target=_run, name=f"penawaran-{order_code}"[:60], daemon=True).start()
