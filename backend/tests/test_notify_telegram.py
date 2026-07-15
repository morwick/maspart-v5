"""Notifikasi Telegram: gate konfig + isi pesan pesanan masuk/lunas.
Tak menyentuh jaringan (hanya menguji pembentukan pesan & gate)."""
from app.services import notify
from app.core.config import get_settings


def test_available_gate(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "telegram_bot_token", "", raising=False)
    monkeypatch.setattr(s, "telegram_chat_id", "", raising=False)
    assert notify.available() is False
    monkeypatch.setattr(s, "telegram_bot_token", "T", raising=False)
    monkeypatch.setattr(s, "telegram_chat_id", "123", raising=False)
    assert notify.available() is True


def test_pesan_pesanan_masuk():
    order = {"order_code": "PO-ABC123", "username": "roni", "gudang": "01.Jakarta",
             "total": 158500, "shipping_cost": 12000, "courier": "jne",
             "courier_service": "REG", "recipient_name": "Budi", "recipient_phone": "0812",
             "status": "menunggu_pembayaran", "note": "kirim cepat"}
    items = [{"qty": 2, "name": "Filter Oli", "part_number": "WG1234", "line_total": 146500}]
    msg = notify._msg_new_order(order, items)
    assert "PESANAN BARU" in msg and "PO-ABC123" in msg
    assert "roni" in msg and "01.Jakarta" in msg
    assert "2× Filter Oli (WG1234)" in msg
    assert "Rp 158.500" in msg and "Rp 12.000" in msg      # total & ongkir
    assert "jne REG" in msg and "Budi" in msg
    assert "kirim cepat" in msg


def test_pesan_lunas():
    msg = notify._msg_paid({"order_code": "PO-XYZ", "username": "roni",
                            "total": 158500, "gudang": "01.Jakarta"})
    assert "LUNAS" in msg and "PO-XYZ" in msg and "Rp 158.500" in msg


def test_fmt_items_potong_banyak():
    items = [{"qty": 1, "name": f"P{i}", "part_number": f"PN{i}", "line_total": 1000}
             for i in range(25)]
    out = notify._fmt_items(items)
    assert "+5 item lagi" in out                            # 25 - 20 = 5


def test_notify_tak_kirim_bila_tak_konfig(monkeypatch):
    """Tanpa konfig → tak memanggil send (aman, tak error)."""
    monkeypatch.setattr(notify, "available", lambda: False)
    dipanggil = []
    monkeypatch.setattr(notify, "send_async", lambda t: dipanggil.append(t))
    notify.notify_new_order({"order_code": "X"}, [])
    notify.notify_paid({"order_code": "X"})
    assert dipanggil == []
