"""Notifikasi Telegram — kirim pesan ke admin saat pesanan MASUK / LUNAS.

Pakai bot Telegram (token dari @BotFather) → gratis, resmi, tanpa risiko blokir.
Best-effort & di LATAR: kegagalan notif TIDAK boleh menghambat/menggagalkan
pembuatan order. Konfig via env: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
(chat/grup tujuan; boleh >1 dipisah koma).
"""
from __future__ import annotations

import logging
import threading

import requests

from ..core.config import get_settings

logger = logging.getLogger("maspart.notify")
_TIMEOUT = 10


def available() -> bool:
    return get_settings().telegram_configured


def _rp(n) -> str:
    try:
        return "Rp " + f"{int(n):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "-"


def _send(text: str) -> bool:
    """Kirim SATU pesan ke semua chat tujuan (best-effort). True bila ≥1 sukses."""
    s = get_settings()
    if not s.telegram_configured:
        return False
    url = f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage"
    ok = False
    for chat_id in [c.strip() for c in str(s.telegram_chat_id).split(",") if c.strip()]:
        try:
            r = requests.post(url, json={"chat_id": chat_id, "text": text,
                                         "disable_web_page_preview": True}, timeout=_TIMEOUT)
            if r.status_code == 200 and (r.json() or {}).get("ok"):
                ok = True
            else:
                logger.warning("[telegram] gagal kirim ke %s: %s %s", chat_id,
                               r.status_code, (r.text or "")[:160])
        except Exception as e:  # jaringan dll — jangan lempar
            logger.warning("[telegram] error kirim ke %s: %s", chat_id, e)
    return ok


def send_async(text: str) -> None:
    """Kirim di THREAD latar — pemanggil tak menunggu / tak terpengaruh kegagalan."""
    if not available():
        return
    threading.Thread(target=_send, args=(text,), daemon=True, name="tg-notify").start()


def _fmt_items(items: list[dict] | None) -> str:
    baris = []
    for it in (items or [])[:20]:
        qty = it.get("qty") or 1
        nama = it.get("name") or it.get("part_number") or "?"
        pn = it.get("part_number") or ""
        baris.append(f"  • {qty}× {nama} ({pn}) = {_rp(it.get('line_total'))}")
    sisa = len(items or []) - 20
    if sisa > 0:
        baris.append(f"  … +{sisa} item lagi")
    return "\n".join(baris) if baris else "  (tak ada rincian)"


def _msg_new_order(order: dict, items: list[dict] | None = None) -> str:
    o = order
    ship = o.get("shipping_cost") or 0
    kurir = " ".join(x for x in (o.get("courier"), o.get("courier_service")) if x) or "-"
    penerima = " · ".join(x for x in (o.get("recipient_name"), o.get("recipient_phone")) if x) or "-"
    text = (
        f"🛒 PESANAN BARU — {o.get('order_code','?')}\n"
        f"👤 Pembeli: {o.get('username','-')}\n"
        f"🏬 Gudang: {o.get('gudang') or '-'}\n"
        f"📦 Barang:\n{_fmt_items(items)}\n"
        f"💰 Total: {_rp(o.get('total'))}  (ongkir {_rp(ship)})\n"
        f"🚚 {kurir}\n"
        f"📍 {penerima}\n"
        f"⏳ Status: {o.get('status','-')}"
    )
    if o.get("note"):
        text += f"\n📝 Catatan: {o['note']}"
    return text


def _msg_paid(order: dict) -> str:
    return (
        f"✅ PEMBAYARAN LUNAS — {order.get('order_code','?')}\n"
        f"👤 {order.get('username','-')} · 💰 {_rp(order.get('total'))}\n"
        f"🏬 {order.get('gudang') or '-'} — siap diproses."
    )


def notify_new_order(order: dict, items: list[dict] | None = None) -> None:
    """Notif PESANAN MASUK ke admin (di latar)."""
    if available() and isinstance(order, dict):
        send_async(_msg_new_order(order, items))


def notify_paid(order: dict) -> None:
    """Notif PEMBAYARAN LUNAS ke admin (di latar)."""
    if available() and isinstance(order, dict):
        send_async(_msg_paid(order))
