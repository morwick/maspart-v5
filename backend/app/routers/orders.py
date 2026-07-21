"""Router Pesanan internal: buat order, pesanan saya, detail, bukti bayar, admin status."""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel

from ..core.config import get_settings
from ..core.ratelimit import hit as _rl_hit, limit
from ..deps import get_current_user, require_admin, require_buyer_ready
from ..services import accurate, gudang, harga, notify, orders, part_index, payments, reservations, shipping
from ..services import supabase_client as sb

logger = logging.getLogger("maspart.orders")

# ── Idempotensi POST /orders (L5) ────────────────────────────────────────────
# Dobel-klik "Bayar" / retry jaringan dgn keranjang identik → satu order & satu
# VA, bukan dua. Uvicorn 1 worker → state proses-lokal cukup (pola ai_export).
_ORDER_IDEM_TTL = 90.0
_order_idem: dict[str, dict] = {}          # fp → {at, resp}
_order_locks: dict[str, tuple] = {}        # fp → (Lock, terakhir_dipakai)
_order_locks_guard = threading.Lock()


def _order_fp(username: str, body: "CreateOrderRequest") -> str:
    """Sidik jari checkout: user + item (PN,qty) + penerima + kurir. Sama = maksud
    pesan yang sama."""
    items = sorted((str(i.part_number or "").strip().upper(), int(i.qty or 0)) for i in body.items)
    key = json.dumps({
        "u": username, "items": items,
        "r": [body.recipient_name, body.recipient_phone, body.recipient_address, body.recipient_postal],
        "c": [body.courier, body.courier_service, body.payment_channel],
    }, sort_keys=True, default=str)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _order_lock(fp: str) -> threading.Lock:
    """Lock per-sidik-jari agar dua request kembar diserialkan (yang kedua
    menemukan hasil yang pertama, tak membuat order baru)."""
    now = time.monotonic()
    with _order_locks_guard:
        for k in [k for k, v in _order_idem.items() if now - v["at"] > _ORDER_IDEM_TTL]:
            _order_idem.pop(k, None)
        # Sapu lock lama agar tak menumpuk sepanjang uptime — termasuk lock YATIM
        # (checkout gagal → tak pernah punya entri cache). Yang sedang dipegang
        # thread lain JANGAN dibuang: penggantinya membuat dua request kembar
        # masuk bagian kritis bersamaan.
        for k, (lk, at) in list(_order_locks.items()):
            if k != fp and now - at > _ORDER_IDEM_TTL and not lk.locked():
                _order_locks.pop(k, None)
        lk, _at = _order_locks.get(fp) or (threading.Lock(), 0.0)
        _order_locks[fp] = (lk, now)
        return lk


def _order_idem_get(fp: str):
    ent = _order_idem.get(fp)
    if ent and time.monotonic() - ent["at"] < _ORDER_IDEM_TTL:
        return ent["resp"]
    return None


def _order_idem_put(fp: str, resp) -> None:
    _order_idem[fp] = {"at": time.monotonic(), "resp": resp}


def _idem_masih_berlaku(resp) -> bool:
    """Hasil lama boleh dipakai ulang hanya selama ordernya masih bisa dibayar.
    Pembeli yang MEMBATALKAN lalu checkout ulang keranjang yang sama dalam <TTL
    detik harus dapat order + VA baru, bukan order batal berikut VA matinya.
    Bila status tak bisa dibaca (DB sedang gagal), tetap pakai hasil lama — salah
    di sisi 'jangan buat order kedua' lebih aman daripada tagihan ganda."""
    try:
        cur = orders.get_order(str((resp or {}).get("order_code") or ""))
    except Exception:                 # pragma: no cover — DB down
        return True
    return not cur or cur.get("status") in _PAYABLE


router = APIRouter(prefix="/api", tags=["orders"])


def _after_paid(order_code: str) -> None:
    """Dipanggil SEKALI saat order transisi ke lunas (webhook/polling). Memicu
    pembuatan Penawaran Accurate otomatis di THREAD LATAR — best-effort, TAK
    PERNAH menggagalkan/menunda respons pembayaran. Satu implementasi dipakai
    bersama jalur rekonsiliasi latar (orders.after_paid)."""
    orders.after_paid(order_code)
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


class RatesRequest(BaseModel):
    weight_grams: int = 1000
    value: int = 0
    dest_postal: str = ""                     # kode pos tujuan (penerima)
    items: list[OrderItemIn] = []             # isi keranjang → tentukan gudang pemenuh (asal ongkir)


def _satu_gudang(username: str, items: list[dict]) -> tuple[str, str]:
    """(label gudang pemenuh TUNGGAL, pesan_error).

    ATURAN PEMILIK: satu pesanan hanya boleh dari SATU gudang. Barang dari dua gudang
    = dua paket, dua resi, dua ongkir — dan kurir tak bisa mengirim satu paket dari
    dua kota. Keranjang lintas gudang karena itu DITOLAK; pembeli memesan bergantian
    per gudang (dua transaksi)."""
    fmap = orders.fulfillment_map(username, items)
    gudangs = sorted(set(fmap.values()))
    if len(gudangs) > 1:
        nama = ", ".join(gudang.gudang_label(g) for g in gudangs)
        return "", (
            f"Keranjang berisi part dari {len(gudangs)} gudang ({nama}). Satu pesanan "
            "hanya bisa dari SATU gudang — pesan bergantian per gudang."
        )
    return (gudangs[0] if gudangs else ""), ""


def _origin_postal(username: str, items: list[dict]) -> tuple[str, str]:
    """(kode_pos_asal, pesan_error). Asal kirim = gudang PEMENUH item — gudang yang
    benar-benar mengirim barang, bukan gudang pilihan pembeli (fallback terdekat
    kerap memilih gudang lain). Gudang pemenuh tanpa kode pos → ERROR, JANGAN
    jatuh ke kode pos gudang pembeli: ongkir akan dihitung dari kota yang salah.
    Tanpa items (preview kosong) → pakai gudang pembeli seperti biasa."""
    if items:
        fl, err = _satu_gudang(username, items)
        if err:
            return "", err
        if fl:
            postal = gudang.origin_postal_for_label(fl)
            if not postal:
                return "", (
                    f"Ongkir dari gudang {gudang.gudang_label(fl)} belum bisa dihitung — "
                    "kode pos gudang itu belum diisi admin. Hubungi admin."
                )
            return postal, ""
    loc = gudang.location(sb.get_user_gudang(username))
    return (loc or {}).get("origin_postal", ""), ""


@router.post("/shipping/rates")
def shipping_rates(body: RatesRequest, user: dict = Depends(require_buyer_ready)):
    origin_postal, oerr = _origin_postal(
        user["username"], [i.model_dump() for i in body.items],
    )
    if oerr:
        return {"rates": [], "error": oerr, "available": shipping.available()}
    rates, err = shipping.get_rates(
        user["username"], max(body.weight_grams, 100), body.value,
        dest_postal=body.dest_postal, origin_postal=origin_postal,
    )
    return {"rates": rates, "error": err, "available": shipping.available()}


class CartGudangRequest(BaseModel):
    items: list[OrderItemIn] = []


@router.post("/cart/gudang")
def cart_gudang(body: CartGudangRequest, user: dict = Depends(require_buyer_ready)):
    """Keadaan TERKINI tiap item keranjang menurut server: gudang pengirim, harga,
    berat, dan apakah masih bisa dibeli.

    Keranjang disimpan di browser LENGKAP DENGAN HARGANYA saat part dimasukkan —
    harga/stok bisa sudah berubah (mis. part hilang dari Accurate) sehingga pembeli
    melihat harga basi lalu ditolak saat checkout. Halaman keranjang menyegarkan
    dirinya dari sini, jadi yang dilihat = yang ditagih.
    `multi` = keranjang terpecah ke >1 gudang → akan jadi >1 paket."""
    raw = [i.model_dump() for i in body.items]
    fmap = orders.fulfillment_map(user["username"], raw)

    items: list[dict] = []
    for it in body.items:
        pn = (it.part_number or "").strip().upper()
        if not pn:
            continue
        price, _nm = harga.price_for_buyer(pn)
        berat = harga.weight_for(pn, allow_remote=True)
        g = fmap.get(pn, "")
        alasan = ""
        if price <= 0:
            alasan = "harga belum tersedia"
        elif berat <= 0:
            alasan = "berat belum ditetapkan"
        elif not g:
            alasan = "stok habis"
        items.append({
            "part_number": pn,
            "gudang": gudang.gudang_label(g) if g else "",
            "harga": price,
            "harga_display": f"Rp {price:,}".replace(",", ".") if price > 0 else "",
            "berat": berat,
            "bisa_dibeli": not alasan,
            "alasan": alasan,
        })

    tally: dict[str, int] = {}
    for pn, g in fmap.items():
        if any(i["part_number"] == pn and i["bisa_dibeli"] for i in items):
            tally[g] = tally.get(g, 0) + 1
    utama = max(tally, key=tally.get) if tally else ""
    return {
        "items": items,
        "utama": gudang.gudang_label(utama) if utama else "",
        "multi": len(tally) > 1,     # keranjang terpecah → lebih dari satu paket
    }


@router.get("/payments/methods")
def payment_methods(_user: dict = Depends(require_buyer_ready)):
    """Metode pembayaran yang tersedia (channel asli dari gateway kalau aktif)."""
    if not payments.available():
        return {"gateway_available": False, "channels": []}
    channels, _err = payments.list_methods()
    return {"gateway_available": True, "channels": channels}


@router.post("/orders")
def create_order(body: CreateOrderRequest, user: dict = Depends(require_buyer_ready)):
    """L5 idempoten: dobel-klik/retry dgn keranjang identik → satu order & satu
    VA. Lock per-sidik-jari menserialkan request kembar; yang kedua menemukan
    hasil yang pertama alih-alih membuat order + reservasi + transaksi baru."""
    fp = _order_fp(user["username"], body)
    with _order_lock(fp):
        cached = _order_idem_get(fp)
        if cached is not None and _idem_masih_berlaku(cached):
            return cached
        order = _create_order_impl(body, user)
        _order_idem_put(fp, order)
        return order


def _create_order_impl(body: CreateOrderRequest, user: dict):
    # Harga & stok yang ditagih HARUS dari indeks Accurate yang masih bisa
    # dipertanggungjawabkan. Bila indeks terlalu tua (sesi Accurate rusak
    # berhari-hari, refresh terjadwal gagal beruntun), harga bisa basi & stok
    # oversell vs ERP — tolak checkout, jangan menjual buta.
    if accurate.index_too_old_for_checkout():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Stok & harga sedang disinkronkan ulang dari sistem gudang. Silakan "
            "coba lagi beberapa saat lagi.")
    items = [i.model_dump() for i in body.items]
    # SATU pesanan = SATU gudang (aturan pemilik). Dicek di awal, sebelum order/reservasi
    # dibuat — bukan cuma di UI, karena ongkirnya hanya bisa dihitung dari satu asal.
    _fl_cek, gerr = _satu_gudang(user["username"], items)
    if gerr:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, gerr)
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
    # Gudang yang dipilih pembeli (label penuh, mis. '01.Jakarta') — dari DB (lokasi
    # terpilih terkini), SAMA dgn etalase & halaman detail agar pemilihan gudang
    # konsisten (JWT bisa basi bila pembeli ganti lokasi setelah login).
    blabel = gudang.buyer_label(sb.get_user_gudang(user["username"]))

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
        # Tolak qty tak valid SECARA EKSPLISIT. Dulu router men-clamp ke 1
        # sedangkan create_order men-skip qty<1 → item qty-negatif direservasi
        # tapi tak masuk pesanan (stok hantu tertahan). Samakan: qty<1 = 400.
        try:
            qty = int(it.qty or 1)
        except Exception:
            qty = 0
        if qty < 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Jumlah tidak valid untuk {it.part_number or pn}.")
        # Reservasi dikurangkan SEBELUM scoping agar gudang pemenuh dipilih dengan
        # fallback gudang terdekat saat gudang sendiri habis — konsisten dgn
        # etalase (buyer_catalog) & halaman detail (parts._scope_gudang).
        # `shippable`: gudang yang dimatikan admin ('Bisa Kirim') tak boleh memenuhi.
        raw_bd = gudang.shippable(part_index.gudang_breakdown(pn))
        net_bd = {g: raw_bd.get(g, 0) - resv.get((pn, g), 0) for g in raw_bd}
        net_bd = {g: q for g, q in net_bd.items() if q > 0}
        scoped = gudang.scope_breakdown(net_bd, user["username"], "pembeli", names, own=blabel)
        if not scoped:
            habis.append(it.part_number)
            continue
        g = next(iter(scoped))       # gudang pemenuh (stok NET > 0)
        avail = int(scoped[g])       # sudah net (stok Excel − reservasi aktif)
        if avail < qty:
            kurang.append(f"{it.part_number} (sisa {avail})")
            continue
        fulfill_tally[g] = fulfill_tally.get(g, 0) + 1
        res_entries.append((pn, g, qty))
        stock_map[(pn, g)] = int(raw_bd.get(g, 0))   # RAW (Excel) utk cek oversell pasca-reservasi
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

    # Ongkir dihitung ULANG di server dari tarif resmi (kurir+service+berat+asal/
    # tujuan) — nilai `body.shipping_cost` dari klien TAK PERNAH dipercaya. Semua
    # order di sini = gateway (manual ditolak di atas), jadi bila ongkir tak bisa
    # dihitung dari tarif segar → TOLAK; ⛔ JANGAN jatuh ke nilai klien (celah
    # ongkir Rp 0 lewat kode pos ngawur / saat layanan tarif down).
    if not shipping.available():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Layanan ongkir sedang tidak tersedia — coba lagi sebentar lalu cek ongkir ulang.",
        )
    # Asal kirim = gudang PEMENUH (fulfill_label), bukan gudang pilihan pembeli.
    origin_postal = gudang.origin_postal_for_label(fulfill_label)
    if not origin_postal:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Ongkir dari gudang {gudang.gudang_label(fulfill_label)} belum bisa dihitung — "
            "kode pos gudang itu belum diisi admin. Hubungi admin.",
        )
    rates, _rerr = shipping.get_rates(
        user["username"], weight_grams, 0,
        dest_postal=body.recipient_postal or "", origin_postal=origin_postal,
    )
    if not rates:
        # Tujuan tak terselesaikan (kode pos salah) / gagal ambil tarif.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Ongkir tak bisa dihitung — periksa alamat & kode pos tujuan, lalu cek ongkir ulang.",
        )
    chosen = (body.courier or "").lower()
    svc = body.courier_service or ""
    match = next(
        (r for r in rates if (r.get("courier") or "").lower() == chosen
         and (r.get("service") or "") == svc),
        None,
    )
    if not match:
        # Kurir/layanan yang dipilih tak ada di tarif resmi → minta cek ulang.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Ongkir tidak valid / tarif kurir berubah. Silakan cek ongkir ulang sebelum memesan.",
        )
    server_ship = int(match["price"])

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
    # Gudang FISIK pengirim (mis. '06.B80 H1') — beda dari kolom `gudang` yang berisi
    # cabang pemroses. Dipakai agar pembeli & admin tahu barang berangkat dari mana.
    orders.set_fulfill_gudang(code, fulfill_label)

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
            # Order tanpa transaksi gateway TAK BISA dibayar (tak ada payment_url) DAN
            # tak pernah kedaluwarsa (is_expired butuh payment_expiry yang tak pernah
            # terisi) → akan nyangkut selamanya sambil menahan stok. Batalkan saja;
            # pembeli tinggal checkout ulang.
            reservations.release(code)
            orders.set_status(code, "batal")
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Gagal membuat pembayaran ({perr}). Pesanan dibatalkan, silakan ulangi checkout.",
            )
        orders.attach_payment(order["order_code"], pay)
        order["payment"] = pay
    # Notif PESANAN MASUK ke admin — HANYA di sini, setelah reservasi + pembayaran
    # sukses (bukan di dalam orders.create_order yang bisa berujung auto-batal).
    # notify_new_order mengirim di thread latar; get_order best-effort (bawa items).
    try:
        full = orders.get_order(code) or {}
        notify.notify_new_order(full or order, full.get("items"))
    except Exception:  # pragma: no cover — notif tak boleh menggagalkan order
        pass
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


@router.get("/orders/{code}/tracking")
def order_tracking(code: str, user: dict = Depends(get_current_user)):
    """Perjalanan paket dari kurir, untuk resi yang diisi admin cabang.

    ⛔ BUKAN pemesanan pengiriman: resi tetap dibuat gerai ekspedisi. Ini hanya
    membacakan manifest kurir supaya pembeli tak perlu menyalin nomornya ke
    situs kurir. Kegagalan dikembalikan sebagai `error` (200), BUKAN exception —
    halaman pesanan tetap tampil walau layanan lacak sedang mati.
    """
    if not _rl_hit(f"tracking:{user['username']}", 20, 60):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Terlalu sering melacak. Tunggu sebentar lalu coba lagi.")
    is_admin = user.get("role") == "admin"
    o = orders.get_order(code, username=None if is_admin else user["username"])
    if not o:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pesanan tidak ditemukan.")
    resi = (o.get("tracking_no") or "").strip()
    if not resi:
        return {"ada_resi": False, "error": "Nomor resi belum diisi admin."}
    hasil, err = shipping.track(resi, o.get("courier") or "")
    if err:
        return {"ada_resi": True, "resi": resi, "kurir": o.get("courier") or "", "error": err}
    return {"ada_resi": True, **hasil}


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
    # Order gateway TAK BOLEH terima bukti manual: set_proof memindahkan status ke
    # 'menunggu_verifikasi', dan pembayaran yang lunas setelah itu akan terabaikan
    # (webhook & polling hanya melunasi order yang belum diverifikasi manual).
    if (o.get("payment_method") or "") == "gateway":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Pesanan ini dibayar lewat pembayaran online — bukti transfer tidak diperlukan, "
            "pembayaran terverifikasi otomatis.",
        )
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
# Status yang masih boleh DILUNASI oleh konfirmasi gateway. 'menunggu_verifikasi'
# ikut (jaring pengaman): order gateway seharusnya tak pernah punya bukti manual,
# tapi kalau toh statusnya sempat berpindah ke sana, pembayaran yang benar-benar
# lunas jangan sampai terabaikan & ordernya nyangkut.
_PAYABLE = {"menunggu_pembayaran", "menunggu_verifikasi"}


@router.get("/orders/{code}/payment/status")
def payment_status(code: str, user: dict = Depends(get_current_user)):
    """Cek status pembayaran ke gateway; kalau lunas, tandai order diproses.

    L3: dibatasi 30 panggilan/menit per AKUN — tiap panggilan memicu get_status
    (HTTP ke Midtrans) + berpotensi mutasi status. ⛔ Sengaja BUKAN per IP:
    pembeli seluler berbagi satu IP publik lewat CGNAT operator, jadi batas
    per-IP akan menendang pembeli sah yang kebetulan satu jaringan. Web & mobile
    sama-sama polling tiap 8 dtk (7,5/menit) → masih longgar untuk beberapa
    pesanan terbuka sekaligus + tombol 'cek status' manual.
    """
    if not _rl_hit(f"paystatus:{user['username']}", 30, 60):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Terlalu sering mengecek status pembayaran. Tunggu sebentar lalu coba lagi.")
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
    _total = int(o.get("total") or 0)
    if paid and gw_amount and gw_amount != _total:
        # L2: uang MASUK tapi nominal beda → angkat ke radar admin (jangan hanya
        # tolak diam-diam; jalur reconcile sudah flag, samakan di sini).
        orders.flag_amount_mismatch(code, gw_amount, _total)
        return {
            "status": o.get("status"), "paid": False,
            "error": f"Nominal pembayaran (Rp{gw_amount:,}) tidak sama dengan total tagihan (Rp{_total:,}). Hubungi admin.",
        }
    if paid and not gw_amount:
        # L1: lunas tapi gateway tak menyertakan nominal → tak bisa verifikasi
        # underpay. Snap mengunci nominal saat pembuatan, jadi ini rare; log agar
        # tak lolos senyap.
        logger.warning("polling: gateway lunas TANPA nominal utk order %s — "
                       "verifikasi underpay dilewati", code)
    if paid and o.get("status") in _PAYABLE:
        if not orders.mark_paid(code, raw=res.get("raw")):
            # Gateway bilang LUNAS tapi status gagal disimpan (DB down). Jangan
            # laporkan 'diproses': UI berhenti polling & pembayaran seolah hilang.
            logger.error("polling: mark_paid GAGAL untuk order %s (lunas di gateway)", code)
            return {
                "status": o.get("status"), "paid": False, "gateway_status": res["status"],
                "error": "Pembayaran terdeteksi lunas, tapi status pesanan gagal diperbarui. "
                         "Muat ulang halaman ini sebentar lagi; bila tetap, hubungi admin.",
            }
        _after_paid(code)
    elif paid and o.get("status") == "batal":
        orders.flag_late_payment(code, int(gw_amount or o.get("total") or 0))
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
    _total = int(o.get("total") or 0)
    if gw_amount and gw_amount != _total:
        # L2: uang beda-nominal masuk → tandai agar admin tahu (dulu hanya
        # 'ignored' → uang bisa senyap dari radar).
        orders.flag_amount_mismatch(o["order_code"], gw_amount, _total)
        return {"ok": True, "ignored": f"nominal tidak cocok ({gw_amount} vs {_total})"}
    if not gw_amount:
        logger.warning("webhook: gateway lunas TANPA nominal utk order %s — "
                       "verifikasi underpay dilewati", o["order_code"])
    # Idempotent: hanya proses kalau order belum lunas.
    if o.get("status") in _PAYABLE:
        if not orders.mark_paid(o["order_code"], raw=data.get("raw")):
            # Gagal simpan (mis. Supabase sedang down). JANGAN balas 200: gateway
            # menganggap notifikasi sukses & tak pernah mengirim ulang, sedangkan
            # order tetap 'menunggu_pembayaran' → nanti auto-batal padahal uangnya
            # sudah masuk. Balas 5xx supaya Midtrans me-retry notifikasi ini.
            logger.error("webhook: mark_paid GAGAL untuk order %s (uang sudah masuk di gateway)",
                         o["order_code"])
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Gagal menandai pesanan lunas — kirim ulang notifikasi.",
            )
        _after_paid(o["order_code"])
        return {"ok": True}
    if o.get("status") == "batal":
        # Pembayaran MASUK untuk order yang sudah dibatalkan (mis. bayar tepat sebelum
        # batas 24 jam, order keburu auto-batal). Uang ada di gateway tapi tak ada yang
        # tahu → tandai order & log keras agar admin bisa refund. Jangan menghidupkan
        # order (stoknya sudah dilepas, bisa saja sudah terjual ke pembeli lain).
        orders.flag_late_payment(o["order_code"], gw_amount or int(o.get("total") or 0))
        return {"ok": True, "flagged": "dibayar setelah order batal — perlu refund"}
    return {"ok": True, "ignored": f"order sudah berstatus {o.get('status')}"}


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
