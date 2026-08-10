#!/usr/bin/env python
"""Refresh token EPC Shantui (dipakai ADMIN, bukan asisten).

Token Shantui BER-TTL PENDEK / sesi-tunggal → perlu di-refresh berkala. Skrip ini
login ulang dan menulis token baru ke data/shantui_token.txt.

Kredensial dibaca dari ENV SHANTUI_USER / SHANTUI_PASS, atau data/shantui_cred.json
{"username": "...", "password": "..."}. ⛔ JANGAN commit kredensial.

Cara pakai:
  1. Otomatis penuh (butuh ddddocr terpasang — `pip install ddddocr`, onnxruntime sudah ada):
        python shantui_refresh.py
  2. Manual (tanpa ddddocr): skrip menyimpan gambar CAPTCHA ke data/shantui_captcha.png,
     Anda buka lalu jalankan lagi dengan kodenya:
        python shantui_refresh.py <KODE_CAPTCHA>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.services import epc_shantui as sh  # noqa: E402
from app.core.config import get_settings  # noqa: E402


def main() -> int:
    user, pw = sh._credentials()
    if not user or not pw:
        print("[shantui] ⛔ Kredensial tak ditemukan. Set ENV SHANTUI_USER/SHANTUI_PASS "
              "atau buat data/shantui_cred.json {\"username\":..,\"password\":..}.")
        return 2

    # Kode CAPTCHA diberikan manual di argv → langsung login (harus didahului fetch
    # captcha di run sebelumnya agar verifyId=user valid di server).
    if len(sys.argv) > 1:
        code = sys.argv[1].strip()
        tok = sh.login(code)
        print(f"[shantui] login manual: {'OK ' + tok[:18] + '…' if tok else 'GAGAL (kode salah/expired)'}")
        return 0 if tok else 1

    # Coba otomatis penuh (ddddocr).
    tok = sh.refresh_token()
    if tok:
        print(f"[shantui] refresh OTOMATIS OK → {tok[:18]}… (tersimpan)")
        return 0

    # ddddocr tak ada → simpan captcha utk dibaca manusia.
    import requests
    import urllib3
    urllib3.disable_warnings()
    img = requests.get(sh._BASE + "/verifyCode/login", params={"verifyId": user},
                       headers={"User-Agent": sh._UA}, timeout=20, verify=False).content
    out = get_settings().data_path / "shantui_captcha.png"
    out.write_bytes(img)
    print(f"[shantui] Auto gagal (ddddocr belum terpasang). CAPTCHA disimpan: {out}\n"
          f"          Buka gambar itu, lalu jalankan:  python shantui_refresh.py <KODE>")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
