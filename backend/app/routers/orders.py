"""Router Pesanan internal: buat order, pesanan saya, detail, bukti bayar, admin status."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel

from ..core.config import get_settings
from ..core.ratelimit import limit
from ..deps import get_current_user, require_admin, require_buyer_ready
from ..services import gudang, harga, orders, part_index, payments, reservations, shipping
from ..services import supabase_client as sb

router = APIRouter(prefix="/api", tags=["orders"])
_IMG_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "pdf": "application/pdf"}
_MAX_PROOF_BYTES = 10 * 1024 * 1024  # 10 MB


class OrderItemIn(BaseModel):
    part_number: str
    qty: int = 1
    name: str | None = None


class CreateOrderRequest(BaseModel):
    note: str | None = None
    items: list[OrderItemIn]
    courier: str | None = None
    courier_service: str | None = None
    shipping_cost: int = 0
    weight_grams: int = 0
    payment_method: str = "gateway"          # hanya 'gateway' (VA/QRIS)
    payment_channel: str | None = None      # 'qris' | 'va_bca' | ...
    recipient_name: str | None = None
    recipient_phone: str | None = None
    recipient_address: str | None = None
    recipient_postal: str | None = None


class StatusRequest(BaseModel):
    status: str


class WeightRequest(BaseModel):
    items: list[OrderItemIn]


@router.post("/shipping/weight")
def shipping_weight(body: WeightRequest, _user: dict = Depends(require_buyer_ready)):
    """Hitung total berat (gram) otomatis dari data berat part (fallback estimasi)."""
    default_each = get_settings().ship_default_item_grams
    pairs = [(i.part_number, i.qty) for i in body.items]
    grams = harga.total_weight_grams(pairs, default_each)
    return {"weight_grams": grams, "default_item_grams": default_each}


@router.get("/shipping/rates")
def shipping_rates(
    weight_grams: int = Query(1000, ge=100),
    value: int = Query(0, ge=0),
    dest_postal: str = Query("", description="Kode pos tujuan (penerima)"),
    user: dict = Depends(require_buyer_ready),
):
    # Asal kirim = kode pos gudang yang dipilih pembeli.
    loc = gudang.location(user.get("gudang"))
    origin_postal = (loc or {}).get("origin_postal", "")
    rates, err = shipping.get_rates(
        user["username"], weight_grams, value, dest_postal=dest_postal, origin_postal=origin_postal,
    )
    return {"rates": rates, "error": err, "available": shipping.available()}


@router.get("/payments/methods")
def payment_methods(_user: dict = Depends(require_buyer_ready)):
    """Metode pembayaran yang tersedia (channel asli dari gateway kalau aktif)."""
    if not payments.available():
        return {"gateway_available": False, "channels": []}
    channels, _err = payments.list_methods()
    return {"gateway_available": True, "channels": channels}


@router.post("/orders")
def create_order(body: CreateOrderRequest, user: dict = Depends(require_buyer_ready)):
    items = [i.model_dump() for i in body.items]
    # Berat dihitung server (otoritatif) dari data berat part — bukan dari klien.
    weight_grams = harga.total_weight_grams(
        [(i.part_number, i.qty) for i in body.items],
        get_settings().ship_default_item_grams,
    )
    method = (body.payment_method or "gateway").lower()
    if method != "gateway":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Hanya pembayaran online (VA/QRIS) yang tersedia.")
    if not payments.available():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pembayaran otomatis belum diaktifkan.")
    recipient = {
        "name": body.recipient_name,
        "phone": body.recipient_phone,
        "address": body.recipient_address,
        "postal": body.recipient_postal,
    }
    # Gudang yang dipilih pembeli (label penuh, mis. '01.Jakarta').
    blabel = gudang.buyer_label(user.get("gudang"))

    # Cek stok (Excel − reservasi aktif) + tentukan gudang pemenuh (termasuk
    # fallback ke lokasi terdekat). Order dirutekan ke cabang gudang pemenuh ini.
    names = part_index.gudang_names()
    resv = reservations.reserved_map()
    habis: list[str] = []
    kurang: list[str] = []
    fulfill_tally: dict[str, int] = {}
    res_entries: list[tuple[str, str, int]] = []
    stock_map: dict[tuple[str, str], int] = {}  # (PN, gudang) → stok Excel, untuk verifikasi pasca-reservasi
    for it in body.items:
        pn = (it.part_number or "").strip().upper()
        try:
            qty = max(1, int(it.qty or 1))
        except Exception:
            qty = 1
        scoped = gudang.scope_breakdown(
            part_index.gudang_breakdown(pn), user["username"], "pembeli", names, own=blabel,
        )
        if not scoped:
            habis.append(it.part_number)
            continue
        g = next(iter(scoped))  # gudang pemenuh
        stock = int(scoped[g])
        avail = stock - resv.get((pn, g), 0)
        if avail <= 0:
            habis.append(it.part_number)
            continue
        if avail < qty:
            kurang.append(f"{it.part_number} (sisa {avail})")
            continue
        fulfill_tally[g] = fulfill_tally.get(g, 0) + 1
        res_entries.append((pn, g, qty))
        stock_map[(pn, g)] = stock
    if habis:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Stok habis untuk: {', '.join(habis)}. Hapus dari keranjang untuk melanjutkan.",
        )
    if kurang:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Stok tidak mencukupi untuk: {', '.join(kurang)}. Kurangi jumlahnya.",
        )

    # Gudang pemenuh dominan → cabang pemiliknya → label order (tanpa prefix nomor).
    fulfill_label = max(fulfill_tally, key=fulfill_tally.get) if fulfill_tally else blabel
    branch_label = gudang.owning_branch_label(fulfill_label) or fulfill_label
    order_gudang = gudang.gudang_label(branch_label) if branch_label else ""

    # Ongkir dihitung ULANG di server (kurir+service+berat+asal/tujuan) — nilai
    # dari klien tidak dipercaya untuk mencegah manipulasi ongkir.
    server_ship = max(int(body.shipping_cost or 0), 0)
    if shipping.available():
        loc = gudang.location(user.get("gudang"))
        origin_postal = (loc or {}).get("origin_postal", "")
        rates, _rerr = shipping.get_rates(
            user["username"], weight_grams, 0,
            dest_postal=body.recipient_postal or "", origin_postal=origin_postal,
        )
        chosen = (body.courier or "").lower()
        svc = body.courier_service or ""
        match = next(
            (r for r in (rates or []) if (r.get("courier") or "").lower() == chosen
             and (r.get("service") or "") == svc),
            None,
        )
        if match:
            server_ship = int(match["price"])
        elif rates:
            # Kurir/layanan yang dipilih tidak ada di tarif resmi → minta cek ulang.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Ongkir tidak valid / tarif kurir berubah. Silakan cek ongkir ulang sebelum memesan.",
            )
        # rates kosong (gagal ambil tarif) → fallback ke nilai klien (tak bisa hitung).

    order, err = orders.create_order(
        user["username"],
        user.get("role", "user"),
        body.note or "",
        items,
        courier=body.courier or "",
        courier_service=body.courier_service or "",
        shipping_cost=server_ship,
        weight_grams=weight_grams,
        payment_method=method,
        recipient=recipient,
        gudang_label=order_gudang,
    )
    if err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)
    code = order["order_code"]

    # Reservasi stok ATOMIK (anti-oversell sejati) lewat RPC. Bila migrasi 014
    # belum dijalankan, fallback ke jalur lama (best-effort) + cek pasca-reservasi.
    if res_entries:
        entries_stock = [(pn, g, q, stock_map.get((pn, g), 0)) for pn, g, q in res_entries]
        res = reservations.reserve(code, entries_stock)
        if res is False:
            # Order lain memenangkan stok → batalkan order ini agar tak oversell.
            orders.set_status(code, "batal")
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Stok keburu habis saat memproses pesanan. Pesanan dibatalkan, silakan ulangi.",
            )
        if res is None:
            # RPC belum tersedia → jalur lama; reservasi GAGAL = hard error (jangan lanjut).
            if not reservations.add(code, res_entries):
                orders.set_status(code, "batal")
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Gagal mereservasi stok. Pesanan dibatalkan, silakan coba lagi.",
                )
            resv2 = reservations.reserved_map(force=True)
            oversold = sorted({pn for pn, g, _q in res_entries if resv2.get((pn, g), 0) > stock_map.get((pn, g), 0)})
            if oversold:
                reservations.release(code)
                orders.set_status(code, "batal")
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"Stok keburu habis untuk: {', '.join(oversold)}. Pesanan dibatalkan, silakan ulangi.",
                )

    # Pembayaran gateway: buat transaksi VA/QRIS lalu lampirkan ke order.
    if method == "gateway":
        pay, perr = payments.create_payment(
            order["order_code"],
            int(order["total"]),
            body.payment_channel or "qris",
            customer={"name": body.recipient_name or user["username"], "email": user.get("email", ""), "phone": body.recipient_phone or ""},
        )
        if perr:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Order dibuat tapi gagal buat pembayaran: {perr}")
        orders.attach_payment(order["order_code"], pay)
        order["payment"] = pay
    return order


@router.get("/orders")
def my_orders(user: dict = Depends(get_current_user)):
    # Sapu pesanan yang kedaluwarsa pembayarannya → auto-batal (lepas reservasi).
    return {"orders": orders.sweep_expired(orders.list_orders(username=user["username"]))}


@router.get("/orders/{code}")
def order_detail(code: str, user: dict = Depends(get_current_user)):
    is_admin = user.get("role") == "admin"
    o = orders.get_order(code, username=None if is_admin else user["username"])
    if not o:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pesanan tidak ditemukan.")
    return o


@router.post("/orders/{code}/confirm")
def confirm_order(code: str, user: dict = Depends(get_current_user)):
    """Pembeli konfirmasi barang sudah diterima → pesanan ditandai 'selesai'."""
    ok, err = orders.confirm_received(code, user["username"])
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err or "Gagal mengonfirmasi penerimaan.")
    return {"ok": True}


@router.post("/orders/{code}/cancel")
def cancel_order(code: str, user: dict = Depends(get_current_user)):
    """Pembeli membatalkan pesanan yang belum lunas."""
    ok, err = orders.cancel_by_buyer(code, user["username"])
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err or "Gagal membatalkan pesanan.")
    return {"ok": True}


@router.post("/orders/{code}/proof")
async def upload_proof(code: str, file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    # Pastikan order milik user (kecuali admin).
    is_admin = user.get("role") == "admin"
    o = orders.get_order(code, username=None if is_admin else user["username"])
    if not o:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pesanan tidak ditemukan.")
    # Ekstensi dari nama asli HANYA untuk menentukan tipe; nama file di-generate
    # server (cegah path traversal / overwrite). order_code diambil dari DB.
    raw_name = (file.filename or "bukti").strip()
    ext = raw_name.rsplit(".", 1)[-1].lower() if "." in raw_name else ""
    if ext not in _IMG_MIME:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bukti harus gambar/PDF.")
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File kosong.")
    if len(data) > _MAX_PROOF_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Ukuran file maksimal 10 MB.")
    safe_code = "".join(ch for ch in str(o["order_code"]) if ch.isalnum() or ch in "-_")
    safe_name = f"bukti-{secrets.token_hex(6)}.{ext}"
    path = f"order-proofs/{safe_code}/{safe_name}"
    ok, msg = sb.upload_storage_object(path, data, _IMG_MIME[ext], bucket=sb.PHOTO_BUCKET)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Upload gagal: {msg}")
    url = sb.photo_public_url(path)
    if not orders.set_proof(code, o["username"], url):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Gagal simpan bukti.")
    return {"ok": True, "url": url}


# ── Pembayaran gateway ──
@router.get("/orders/{code}/payment/status")
def payment_status(code: str, user: dict = Depends(get_current_user)):
    """Cek status pembayaran ke gateway; kalau lunas, tandai order diproses."""
    is_admin = user.get("role") == "admin"
    o = orders.get_order(code, username=None if is_admin else user["username"])
    if not o:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pesanan tidak ditemukan.")
    ref = o.get("payment_ref") or code
    res, err = payments.get_status(ref)
    if err:
        return {"status": o.get("status"), "paid": False, "error": err}
    paid = res["status"] == "paid"
    # Verifikasi nominal: jumlah yang dibayar harus sama dengan total tagihan
    # (dicek bila gateway menyertakan amount; VA/QRIS bernominal tetap aman).
    gw_amount = int(res.get("amount") or 0)
    if paid and gw_amount and gw_amount != int(o.get("total") or 0):
        return {
            "status": o.get("status"), "paid": False,
            "error": f"Nominal pembayaran (Rp{gw_amount:,}) tidak sama dengan total tagihan (Rp{int(o.get('total') or 0):,}). Hubungi admin.",
        }
    if paid and o.get("status") == "menunggu_pembayaran":
        orders.mark_paid(code, raw=res.get("raw"))
    return {"status": "diproses" if paid else o.get("status"), "paid": paid, "gateway_status": res["status"]}


@router.post("/payments/webhook", dependencies=[Depends(limit("webhook", 60, 60))])
async def payment_webhook(request: Request):
    """Callback dari gateway. Verifikasi key → konfirmasi ke server → tandai lunas. Publik (tanpa JWT)."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body callback bukan JSON.")
    data, err = payments.parse_webhook(dict(request.headers), payload)
    if err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)
    if data["status"] != "paid":
        return {"ok": True, "ignored": data["status"]}
    o = orders.find_by_payment(data.get("ref") or data.get("order_id") or "")
    if not o:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order tidak ditemukan untuk callback ini.")
    # Konfirmasi ulang ke server gateway (jangan percaya payload mentah).
    ref = o.get("payment_ref") or data.get("ref")
    chk, _e = payments.get_status(ref) if ref else (None, "no ref")
    if not chk or chk.get("status") != "paid":
        return {"ok": True, "ignored": "belum terkonfirmasi lunas di server"}
    # Pastikan konfirmasi ini benar untuk order yang sama (bukan transaksi lain).
    if chk.get("order_id") and chk.get("order_id") != o.get("order_code"):
        return {"ok": True, "ignored": "order_id tidak cocok"}
    # Verifikasi nominal (bila gateway menyertakannya) — tolak underpayment.
    gw_amount = int(chk.get("amount") or 0)
    if gw_amount and gw_amount != int(o.get("total") or 0):
        return {"ok": True, "ignored": f"nominal tidak cocok ({gw_amount} vs {o.get('total')})"}
    # Idempotent: hanya proses kalau masih menunggu_pembayaran.
    if o.get("status") == "menunggu_pembayaran":
        orders.mark_paid(o["order_code"], raw=data.get("raw"))
    return {"ok": True}


# ── Admin ──
@router.get("/admin/orders")
def admin_list(_admin: dict = Depends(require_admin)):
    return {"orders": orders.list_orders()}


@router.get("/admin/orders/{code}")
def admin_detail(code: str, _admin: dict = Depends(require_admin)):
    o = orders.get_order(code)
    if not o:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pesanan tidak ditemukan.")
    return o


@router.put("/admin/orders/{code}/status")
def admin_status(code: str, body: StatusRequest, _admin: dict = Depends(require_admin)):
    if not orders.set_status(code, body.status):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Status tidak valid / gagal.")
    return {"ok": True}
