"""
Service: Pesanan internal per cabang (keranjang → order → bukti bayar → status).
Harga diambil dari harga.xlsx; cabang & pemesan dari akun login. Supabase via
service_key. Tabel: orders, order_items.
"""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

import requests

from .supabase_client import _rest_url, _service_headers
from . import gudang, harga, reservations
from .gudang import coords_for_display as _coords_for_display, pic_for_display as _pic_for_display

STATUSES = ["menunggu_pembayaran", "menunggu_verifikasi", "diproses", "dikirim", "selesai", "batal"]

# State machine: transisi status yang diizinkan {dari: {ke,...}}. Status terminal
# (selesai, batal) tidak boleh berubah lagi. Mencegah lompatan ilegal (mis.
# selesai→menunggu_pembayaran, atau menghidupkan order batal).
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "menunggu_pembayaran": {"menunggu_verifikasi", "diproses", "batal"},
    "menunggu_verifikasi": {"diproses", "menunggu_pembayaran", "batal"},
    "diproses": {"dikirim", "selesai", "batal"},
    "dikirim": {"selesai", "batal"},
    "selesai": set(),
    "batal": set(),
}

PPN_RATE = 0.11  # PPN 11% (ditambahkan di atas subtotal)
_TIMEOUT = 15


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _gen_code() -> str:
    # 4 byte = 8 hex (≈4,3 miliar kombinasi) untuk menekan peluang tabrakan.
    return "PO-" + secrets.token_hex(4).upper()


def can_transition(current: str | None, new: str) -> bool:
    """True bila perpindahan status `current → new` diizinkan oleh state machine."""
    if new not in STATUSES:
        return False
    if current == new:
        return True  # idempotent (tidak mengubah apa pun)
    return new in _ALLOWED_TRANSITIONS.get(current or "", set())


def current_status(order_code: str, gudang: str | None = None) -> str | None:
    """Ambil status terkini sebuah order (untuk validasi transisi). None bila tak ada."""
    params = {"select": "status", "order_code": f"eq.{order_code}", "limit": "1"}
    if gudang:
        params["gudang"] = f"eq.{gudang}"
    try:
        resp = requests.get(_rest_url("orders"), headers={**_service_headers(), "Accept": "application/json"}, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        rows = resp.json() or []
        return rows[0].get("status") if rows else None
    except Exception:
        return None


def create_order(
    username: str,
    role: str,
    note: str,
    items: list[dict],
    courier: str = "",
    courier_service: str = "",
    shipping_cost: int = 0,
    weight_grams: int = 0,
    payment_method: str = "manual",
    recipient: dict | None = None,
    gudang_label: str | None = None,
) -> tuple[dict | None, str | None]:
    rows: list[dict] = []
    subtotal = 0
    no_price: list[str] = []
    no_weight: list[str] = []
    for it in items or []:
        pn = str(it.get("part_number", "")).strip().upper()
        try:
            qty = int(it.get("qty") or 1)
        except Exception:
            qty = 1
        if not pn or qty < 1:
            continue
        price, name = harga.price_for(pn)
        # Part tanpa harga (harga 0 / tidak ada di List Harga) tidak boleh dibeli.
        if price <= 0:
            no_price.append(pn)
            continue
        # Part tanpa berat (belum ditetapkan admin) juga tidak boleh dibeli.
        # allow_remote: fetch berat resmi SIMS bila kolom manual kosong.
        if harga.weight_for(pn, allow_remote=True) <= 0:
            no_weight.append(pn)
            continue
        lt = price * qty
        subtotal += lt
        rows.append({
            "part_number": pn,
            "name": name or str(it.get("name") or pn),
            "price": price,
            "qty": qty,
            "line_total": lt,
        })
    if no_price:
        return None, (
            f"Harga belum tersedia untuk: {', '.join(sorted(set(no_price)))}. "
            "Part ini belum bisa dibeli — hapus dari keranjang untuk melanjutkan."
        )
    if no_weight:
        return None, (
            f"Berat belum ditetapkan untuk: {', '.join(sorted(set(no_weight)))}. "
            "Part ini belum bisa dibeli — hapus dari keranjang untuk melanjutkan."
        )
    if not rows:
        return None, "Keranjang kosong / part tidak valid."

    if gudang_label is not None:
        gud_label = gudang_label
    else:
        g = gudang.gudang_for_user(username, role)
        gud_label = gudang.gudang_label(g) if g else ("Semua cabang" if role == "admin" else "")
    ship = max(int(shipping_cost or 0), 0)
    tax = (subtotal * 11 + 50) // 100  # PPN 11% exclusive, pembulatan integer (bukan float)
    total = subtotal + tax + ship
    rcp = recipient or {}
    try:
        # Insert order — retry beberapa kali bila order_code bentrok (unique violation).
        order = None
        code = ""
        last_err = ""
        for _attempt in range(5):
            code = _gen_code()
            resp = requests.post(
                _rest_url("orders"),
                headers=_service_headers("return=representation"),
                json={
                    "order_code": code,
                    "username": username,
                    "gudang": gud_label,
                    "note": note or None,
                    "subtotal": subtotal,
                    "tax": tax,
                    "shipping_cost": ship,
                    "total": total,
                    "courier": courier or None,
                    "courier_service": courier_service or None,
                    "weight_grams": int(weight_grams or 0),
                    "payment_method": payment_method or "manual",
                    "recipient_name": rcp.get("name") or None,
                    "recipient_phone": rcp.get("phone") or None,
                    "recipient_address": rcp.get("address") or None,
                    "recipient_postal": rcp.get("postal") or None,
                    "status": "menunggu_pembayaran",
                },
                timeout=_TIMEOUT,
            )
            if resp.status_code in (200, 201):
                order = (resp.json() or [{}])[0]
                break
            txt = (resp.text or "").lower()
            last_err = f"{resp.status_code} {resp.text[:160]}"
            # Tabrakan order_code → coba kode baru. 'tax' belum ada di skema lama →
            # ulangi tanpa kolom tax (resilient bila migrasi 014 belum dijalankan).
            if ("duplicate" in txt or "unique" in txt) and "order_code" in txt:
                continue
            if "tax" in txt and "column" in txt:
                # skema belum punya kolom tax: jangan gagal, lanjut tanpa kolom itu.
                resp = requests.post(
                    _rest_url("orders"),
                    headers=_service_headers("return=representation"),
                    json={
                        "order_code": code, "username": username, "gudang": gud_label,
                        "note": note or None, "subtotal": subtotal, "shipping_cost": ship,
                        "total": total, "courier": courier or None,
                        "courier_service": courier_service or None,
                        "weight_grams": int(weight_grams or 0),
                        "payment_method": payment_method or "manual",
                        "recipient_name": rcp.get("name") or None,
                        "recipient_phone": rcp.get("phone") or None,
                        "recipient_address": rcp.get("address") or None,
                        "recipient_postal": rcp.get("postal") or None,
                        "status": "menunggu_pembayaran",
                    },
                    timeout=_TIMEOUT,
                )
                if resp.status_code in (200, 201):
                    order = (resp.json() or [{}])[0]
                break
            break  # error lain → jangan retry
        if order is None:
            return None, f"Gagal buat order: {last_err}"
        oid = order.get("id")
        for r in rows:
            r["order_id"] = oid
        r2 = requests.post(
            _rest_url("order_items"),
            headers=_service_headers("return=minimal"),
            json=rows,
            timeout=_TIMEOUT,
        )
        if r2.status_code not in (200, 201, 204):
            # Kompensasi: hapus baris order yatim agar tidak ada order tanpa item.
            try:
                requests.delete(
                    _rest_url("orders"),
                    headers=_service_headers("return=minimal"),
                    params={"order_code": f"eq.{code}"},
                    timeout=_TIMEOUT,
                )
            except Exception:
                pass
            return None, f"Gagal simpan item: {r2.status_code}"
        return {"order_code": code, "total": total, "status": "menunggu_pembayaran",
                "payment_method": payment_method or "manual"}, None
    except Exception as e:
        return None, str(e)


def list_orders(username: str | None = None, gudang: str | None = None) -> list[dict]:
    params = {"select": "order_code,username,gudang,total,status,payment_method,payment_proof_url,payment_expiry,created_at", "order": "created_at.desc", "limit": "200"}
    if username:
        params["username"] = f"eq.{username}"
    if gudang:
        params["gudang"] = f"eq.{gudang}"
    try:
        resp = requests.get(_rest_url("orders"), headers={**_service_headers(), "Accept": "application/json"}, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json() or []
    except Exception:
        return []


def get_order(order_code: str, username: str | None = None, gudang: str | None = None) -> dict | None:
    params = {"select": "*", "order_code": f"eq.{order_code}", "limit": "1"}
    if username:
        params["username"] = f"eq.{username}"
    if gudang:
        params["gudang"] = f"eq.{gudang}"
    try:
        resp = requests.get(_rest_url("orders"), headers={**_service_headers(), "Accept": "application/json"}, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        rows = resp.json() or []
        if not rows:
            return None
        order = rows[0]
        ri = requests.get(
            _rest_url("order_items"),
            headers={**_service_headers(), "Accept": "application/json"},
            params={"select": "part_number,name,price,qty,line_total", "order_id": f"eq.{order['id']}"},
            timeout=_TIMEOUT,
        )
        order["items"] = ri.json() or [] if ri.status_code == 200 else []
        # Lampirkan koordinat gudang pengirim (untuk tampilan lokasi).
        # NB: parameter `gudang` menutupi modul, jadi pakai fungsi yang diimpor.
        c = _coords_for_display(order.get("gudang") or "")
        if c:
            order["gudang_lat"], order["gudang_lon"] = c[0], c[1]
        pic = _pic_for_display(order.get("gudang") or "")
        if pic:
            order["gudang_pic"] = pic
        # Auto-batalkan bila pembayaran sudah kedaluwarsa (VA/QRIS lewat batas).
        if is_expired(order) and expire_order(order_code):
            order["status"] = "batal"
        return order
    except Exception:
        return None


_PAID = {"diproses", "dikirim", "selesai"}  # pesanan dianggap "terjual" (sudah dibayar)


def sales_recap(gudang: str | None = None) -> dict:
    """Rekap penjualan dari harga jual (omzet = subtotal pesanan terjual).
    Bila `gudang` diberikan, rekap discoped ke cabang tsb saja."""
    try:
        params = {"select": "id,gudang,subtotal,total,status,created_at", "order": "created_at.desc", "limit": "2000"}
        if gudang:
            params["gudang"] = f"eq.{gudang}"
        ro = requests.get(
            _rest_url("orders"),
            headers={**_service_headers(), "Accept": "application/json"},
            params=params,
            timeout=_TIMEOUT,
        )
        ro.raise_for_status()
        orders_rows = ro.json() or []
        ri = requests.get(
            _rest_url("order_items"),
            headers={**_service_headers(), "Accept": "application/json"},
            params={"select": "order_id,part_number,name,qty,line_total", "limit": "10000"},
            timeout=_TIMEOUT,
        )
        items_rows = ri.json() or [] if ri.status_code == 200 else []
    except Exception:
        return {"summary": {}, "by_status": {}, "by_gudang": [], "by_month": [], "top_parts": []}

    def _i(v) -> int:
        try:
            return int(float(v or 0))
        except Exception:
            return 0

    paid_ids = {o["id"] for o in orders_rows if o.get("status") in _PAID}
    omzet = sum(_i(o.get("subtotal")) for o in orders_rows if o.get("status") in _PAID)

    by_status: dict[str, dict] = {}
    by_gudang: dict[str, dict] = {}
    by_month: dict[str, dict] = {}
    for o in orders_rows:
        st = o.get("status") or "?"
        s = by_status.setdefault(st, {"count": 0, "omzet": 0})
        s["count"] += 1
        s["omzet"] += _i(o.get("subtotal"))
        if o.get("status") in _PAID:
            g = (o.get("gudang") or "—")
            gg = by_gudang.setdefault(g, {"gudang": g, "count": 0, "omzet": 0})
            gg["count"] += 1
            gg["omzet"] += _i(o.get("subtotal"))
            mo = (o.get("created_at") or "")[:7]
            if mo:
                mm = by_month.setdefault(mo, {"month": mo, "count": 0, "omzet": 0})
                mm["count"] += 1
                mm["omzet"] += _i(o.get("subtotal"))

    parts: dict[str, dict] = {}
    items_sold = 0
    for it in items_rows:
        if it.get("order_id") not in paid_ids:
            continue
        pn = it.get("part_number") or "?"
        p = parts.setdefault(pn, {"part_number": pn, "name": it.get("name") or "", "qty": 0, "omzet": 0})
        p["qty"] += _i(it.get("qty"))
        p["omzet"] += _i(it.get("line_total"))
        items_sold += _i(it.get("qty"))

    top_parts = sorted(parts.values(), key=lambda x: x["omzet"], reverse=True)[:15]
    return {
        "summary": {
            "total_orders": len(orders_rows),
            "paid_orders": len(paid_ids),
            "omzet": omzet,
            "items_sold": items_sold,
        },
        "by_status": by_status,
        "by_gudang": sorted(by_gudang.values(), key=lambda x: x["omzet"], reverse=True),
        "by_month": sorted(by_month.values(), key=lambda x: x["month"]),
        "top_parts": top_parts,
    }


def order_owner_gudang(order_code: str) -> dict | None:
    """Ambil ringkas {order_code, username, gudang} untuk cek akses (chat)."""
    try:
        resp = requests.get(
            _rest_url("orders"),
            headers={**_service_headers(), "Accept": "application/json"},
            params={"select": "order_code,username,gudang", "order_code": f"eq.{order_code}", "limit": "1"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        rows = resp.json() or []
        return rows[0] if rows else None
    except Exception:
        return None


def _patch(order_code: str, data: dict, username: str | None = None, gudang: str | None = None) -> bool:
    params = {"order_code": f"eq.{order_code}"}
    if username:
        params["username"] = f"eq.{username}"
    if gudang:
        params["gudang"] = f"eq.{gudang}"
    data["updated_at"] = _now()
    try:
        resp = requests.patch(_rest_url("orders"), headers=_service_headers("return=minimal"), params=params, json=data, timeout=_TIMEOUT)
        return resp.status_code in (200, 204)
    except Exception:
        return False


def set_proof(order_code: str, username: str, url: str) -> bool:
    return _patch(order_code, {"payment_proof_url": url, "status": "menunggu_verifikasi"}, username=username)


def set_status(order_code: str, status: str) -> bool:
    if status not in STATUSES:
        return False
    # Tolak transisi ilegal (mis. selesai→menunggu_pembayaran, hidupkan batal).
    if not can_transition(current_status(order_code), status):
        return False
    ok = _patch(order_code, {"status": status})
    if ok and status == "batal":
        reservations.release(order_code)  # lepas stok yang direservasi
    return ok


def set_status_branch(order_code: str, gudang_label: str, status: str, tracking_no: str | None = None) -> bool:
    """Ubah status hanya untuk order milik cabang (gudang) tsb. Bisa sekaligus set resi."""
    if status not in STATUSES:
        return False
    # Validasi transisi pada order milik gudang ini (None bila bukan miliknya → ditolak).
    if not can_transition(current_status(order_code, gudang=gudang_label), status):
        return False
    data: dict = {"status": status}
    if tracking_no is not None:
        data["tracking_no"] = tracking_no
    ok = _patch(order_code, data, gudang=gudang_label)
    if ok and status == "batal":
        reservations.release(order_code)
    return ok


# ── Aksi pembeli (konfirmasi terima, batal) + kedaluwarsa pembayaran ──
def _epoch(s: str | None) -> float | None:
    """Parse timestamp ISO (mis. dari gateway) → epoch detik. None bila gagal."""
    if not s:
        return None
    try:
        t = s.strip()
        if t.endswith("Z"):
            t = t[:-1] + "+00:00"
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def is_expired(o: dict) -> bool:
    """True bila order masih menunggu pembayaran & sudah lewat batas waktu bayar."""
    if o.get("status") != "menunggu_pembayaran":
        return False
    e = _epoch(o.get("payment_expiry"))
    return e is not None and e < time.time()


def expire_order(order_code: str) -> bool:
    """Batalkan order kedaluwarsa + lepas reservasi stok. Aman: VA sudah ditutup gateway."""
    ok = _patch(order_code, {"status": "batal"})
    if ok:
        reservations.release(order_code)
    return ok


def sweep_expired(rows: list[dict]) -> list[dict]:
    """Untuk daftar order: auto-batalkan yang kedaluwarsa & perbarui status di tempat."""
    for o in rows or []:
        if is_expired(o) and expire_order(o.get("order_code") or ""):
            o["status"] = "batal"
    return rows


_BUYER_CANCELABLE = {"menunggu_pembayaran", "menunggu_verifikasi"}


def confirm_received(order_code: str, username: str) -> tuple[bool, str | None]:
    """Pembeli konfirmasi barang diterima: 'dikirim' → 'selesai'. Hanya pemilik order."""
    o = get_order(order_code, username=username)
    if not o:
        return False, "Pesanan tidak ditemukan."
    if o.get("status") != "dikirim":
        return False, "Hanya pesanan berstatus 'Dikirim' yang bisa dikonfirmasi diterima."
    if not _patch(order_code, {"status": "selesai"}, username=username):
        return False, "Gagal memperbarui status pesanan."
    return True, None


def cancel_by_buyer(order_code: str, username: str) -> tuple[bool, str | None]:
    """Pembeli batalkan pesanan yang BELUM lunas + lepas reservasi. Hanya pemilik order."""
    o = get_order(order_code, username=username)
    if not o:
        return False, "Pesanan tidak ditemukan."
    if o.get("status") not in _BUYER_CANCELABLE:
        return False, "Pesanan tidak bisa dibatalkan pada status saat ini. Hubungi admin/gudang."
    if not _patch(order_code, {"status": "batal"}, username=username):
        return False, "Gagal membatalkan pesanan."
    reservations.release(order_code)
    return True, None


# ── Pembayaran gateway (VA/QRIS) ──
def attach_payment(order_code: str, pay: dict) -> bool:
    """Simpan info pembayaran (ref, VA/QRIS/URL, expiry, channel) ke order."""
    return _patch(order_code, {
        "payment_method": "gateway",
        "payment_ref": pay.get("ref"),
        "payment_channel": pay.get("channel"),
        "payment_va": pay.get("va"),
        "payment_qr": pay.get("qr"),
        "payment_url": pay.get("url"),
        "payment_expiry": pay.get("expiry"),
        "payment_raw": pay.get("raw"),
    })


def find_by_payment(ref_or_code: str) -> dict | None:
    """Cari order via payment_ref; fallback ke order_code (untuk webhook)."""
    if not ref_or_code:
        return None
    for field in ("payment_ref", "order_code"):
        try:
            resp = requests.get(
                _rest_url("orders"),
                headers={**_service_headers(), "Accept": "application/json"},
                params={"select": "order_code,username,total,status,payment_ref", field: f"eq.{ref_or_code}", "limit": "1"},
                timeout=_TIMEOUT,
            )
            if resp.status_code == 200 and resp.json():
                return resp.json()[0]
        except Exception:
            continue
    return None


def mark_paid(order_code: str, raw: dict | None = None) -> bool:
    """Tandai lunas → status langsung 'diproses' (verifikasi otomatis)."""
    data = {"status": "diproses", "paid_at": _now()}
    if raw is not None:
        data["payment_raw"] = raw
    ok = _patch(order_code, data)
    if ok:
        # Order lunas: jadikan reservasi stok permanen agar tidak ikut kedaluwarsa.
        reservations.commit(order_code)
    return ok
