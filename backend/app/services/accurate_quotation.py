"""
Buat Penawaran Penjualan Accurate OTOMATIS saat order lunas.

Dipicu dari router pembayaran tepat setelah `orders.mark_paid` sukses (transisi
menunggu_pembayaran → diproses, jadi sekali per order). Dijalankan di THREAD LATAR
agar webhook/polling tetap cepat, dan bersifat BEST-EFFORT: kegagalan Accurate
TAK PERNAH membatalkan pembayaran — order tetap lunas, hasilnya dicatat di order.

Kebijakan (disepakati pemilik):
  • Customer: dari TAUTAN AKUN → pelanggan Accurate (`users.accurate_customer_id`,
    diatur admin di menu "Pelanggan Accurate"). Akun belum ditautkan → di-SKIP
    (bukan error) — admin bisa buat manual lewat asisten.
    ⛔ TIDAK LAGI mencocokkan `recipient_name`: itu teks bebas yang diketik
    pembeli di form pengiriman, jadi akun yang sama bisa menulis nama orang hari
    ini dan nama PT besok — penawaran kadang jadi, kadang di-skip diam-diam, dan
    berisiko menempel ke pelanggan yang SALAH bila namanya mirip.
  • PN tak ada di Accurate ATAU harga jual Rp 0 → SKIP seluruh penawaran (jangan
    buat sebagian). Order tetap lunas.

⛔ Sama seperti tool asisten: HANYA MEMBUAT penawaran; tak mengubah/menghapus
apa pun di Accurate. NOMOR = MASPART-NN (penomoran otomatis Accurate tak dipakai).
"""
from __future__ import annotations

import logging
import threading
import time

from . import accurate, customer_map, orders

logger = logging.getLogger("maspart.penawaran")

# Penjaga idempoten DALAM-PROSES: cegah webhook + polling yang datang hampir
# bersamaan memicu DUA penawaran untuk order yang sama (cek kolom DB penawaran_*
# baru bekerja setelah migrasi dijalankan). Order_code kecil → set tak dibatasi ketat.
_lock = threading.Lock()
_seen: set[str] = set()


def create_for_order(order: dict) -> dict:
    """Buat penawaran untuk SATU order lunas. TAK PERNAH raise.
    Return {status: created|skip|failed, number?, id?, customer?, note?}."""
    code = order.get("order_code") or ""
    if not accurate.available():
        return {"status": "skip", "note": "Accurate tak aktif"}

    # PELANGGAN diambil dari TAUTAN AKUN (admin menautkannya sekali di menu
    # "Pelanggan Accurate"), BUKAN dari `recipient_name`.
    # ⛔ recipient_name adalah teks BEBAS yang diketik pembeli di form pengiriman:
    # akun yang sama menulis nama orang hari ini dan nama PT besok, sehingga
    # penawaran kadang jadi & kadang di-skip diam-diam — dan bisa menempel ke
    # pelanggan SALAH bila namanya mirip. Aturan pemilik 2026-07-21.
    uname = (order.get("username") or "").strip()
    if not uname:
        return {"status": "skip", "note": "order tanpa username"}
    pel = customer_map.untuk(uname)
    if not pel:
        return {"status": "skip",
                "note": (f"akun '{uname}' belum ditautkan ke pelanggan Accurate — "
                         "tautkan di menu Admin → Pelanggan Accurate")}

    try:
        # Aksi non-interaktif → paksa sesi (abaikan cooldown backoff refresh latar).
        accurate.ensure_session_force()
    except accurate.AccurateError as e:
        return {"status": "failed", "note": f"login Accurate gagal: {str(e)[:150]}"}

    # ⚠️ MULAI SINI SESI ACCURATE SUDAH TERBUKA → apa pun hasilnya, LEPASKAN di
    # `finally`. Akun Accurate 1-SESI: selama MASPART memegangnya, admin tak bisa
    # login manual. Pemicunya adalah pembeli MEMBAYAR — bisa tengah malam, tanpa
    # siapa pun menunggu — jadi menahan kursi sampai idle-logout 2 menit murni
    # merugikan. Dulu jalur ini memang tak pernah logout sama sekali.
    try:
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
            # Harga baris = harga yang BENAR-BENAR DIBAYAR pembeli (order_items.price,
            # dari indeks saat order dibuat) — BUKAN harga live Accurate saat pelunasan.
            # Kalau admin sempat mengubah harga di Accurate di antara order dan bayar,
            # dokumen penawaran wajib tetap sama dengan uang yang masuk; kalau tidak,
            # invoice hasil proses penawaran akan menagih angka yang berbeda.
            try:
                paid = float(it.get("price") or 0)
            except (TypeError, ValueError):
                paid = 0
            live = float(acc_it.get("price") or 0)
            price = paid if paid > 0 else live
            if price <= 0:
                noprice.append(acc_it.get("pn") or pn)
                continue
            if paid > 0 and live > 0 and abs(paid - live) >= 1:
                logger.warning("harga %s berubah sejak order %s: dibayar %s, Accurate kini %s "
                               "— penawaran memakai harga DIBAYAR", pn, code, paid, live)
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
    finally:
        # ⛔ SENGAJA TANPA suppress_autologin(), beda dari jalur manual asisten:
        # di sana admin baru saja menekan tombol dan akan segera membuka Accurate,
        # jadi auto-login latar ditahan 10 menit. Di sini tak ada siapa-siapa yang
        # menunggu — menahan auto-login hanya akan menunda refresh indeks
        # terjadwal tanpa manfaat. Cukup lepaskan kursinya.
        try:
            accurate.logout()
        except Exception:   # pragma: no cover — logout() sendiri sudah best-effort
            logger.exception("logout Accurate setelah penawaran gagal (order %s)", code)


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
