"""
Memori sesi Asisten AI — ingatan singkat SERVER per percakapan.

Masalah yang dipecahkan: riwayat chat dikirim MENTAH oleh klien dan hasil tool
giliran sebelumnya dibuang 100% (`_sanitize_history` hanya menerima role
user/assistant). Akibatnya set `grounded` guard direset tiap giliran, dan
skenario ini rutin terjadi:

    giliran 1  user: "BOM rem unit RT108966"
               asisten: (bom_dari_rangka) → WG9100443050 …
    giliran 2  user: "harganya berapa?"
               asisten menyebut WG9100443050 → PN itu HANYA ada di EPC, tidak di
               katalog lokal → guard menilainya karangan → jawaban disanitasi
               jadi "⟨PN tak terverifikasi⟩".

Asisten menyangkal datanya sendiri. Modul ini menyimpan PN/angka/slot entitas
yang BERASAL DARI HASIL TOOL (bukan dari ucapan model, bukan dari riwayat klien)
supaya giliran berikutnya bisa meng-ground-nya kembali.

Sengaja IN-MEMORY, bukan tabel Supabase:
  • migrasi dijalankan manual → tabel baru = titik degradasi baru di jalur
    terpanas, plus round-trip DB tiap giliran;
  • restart container cukup mengembalikan perilaku ke SEBELUM fitur ini
    (degradasi anggun), bukan kegagalan;
  • manfaat DB yang sesungguhnya (resume lintas-perangkat) bukan tujuan fitur.

Pola stash-nya sama persis dengan `ai_sheet` (lock + TTL + kuota per-user +
pagar global), termasuk pelajaran dari sana: kuota per-user dulu, agar percakapan
user lain tidak menggusur ingatan user ini.
"""
from __future__ import annotations

import re
import threading
import time

# Percakapan dianggap "hangat" seharian kerja; lebih lama dari itu, konteks
# lapangan (unit yang sedang dikerjakan) hampir pasti sudah berganti.
_TTL_SEC = 12 * 3600.0
_MAX_PER_USER = 8      # tab/percakapan aktif per user (memo ≈ KB-an; 4 terbukti
                       # sempit — tab baru menggusur percakapan yang masih aktif)
_MAX_GLOBAL = 300      # pagar RAM (memo ≈ beberapa KB → < 2 MB total)
# Umur ANGKA/FAKTA yang masih layak di-ground ulang = satu siklus indeks Accurate
# (5 jam). Lebih tua dari itu stok/harga bisa sudah berubah — faktanya tetap
# ditampilkan (sbg 'fakta_lama', diberi label wajib-verifikasi oleh pemakai),
# tapi ANGKANYA tidak lagi meng-ground klaim stok/harga giliran baru.
_FRESH_SEC = 5 * 3600.0

# conversation_id datang dari KLIEN → perlakukan sebagai input tak tepercaya.
# Hanya bentuk UUID-ish yang diterima; selain itu memori sesi tidak aktif
# (perilaku turun ke mode lama, bukan error).
_CONV_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")

# Plafon isi memo. Semua kecil & bergulir: ingatan yang KEDALUWARSA lebih
# berbahaya daripada ingatan yang hilang — PN basi yang di-ground kembali
# membuat guard menerima jawaban yang seharusnya diverifikasi ulang.
_CAP_RANGKA = 2
_CAP_MESIN = 2
_CAP_WO = 3
_CAP_UNIT = 2          # nama MODEL/unit aktif (tanpa VIN) — dari argumen tool
_CAP_PN = 200          # BOM satu unit bisa ratusan baris; 200×~30B = 6 KB/sesi
_CAP_NUMS = 120
_CAP_FAKTA = 12

_lock = threading.Lock()
_stash: dict[str, dict] = {}


def _key(username: str, conv_id: str) -> str | None:
    """Kunci stash, atau None bila memori sesi tak boleh aktif."""
    u = (username or "").strip().lower()
    c = (conv_id or "").strip()
    if not u or not _CONV_RE.match(c):
        return None
    return f"{u}\n{c}"


def _purge_locked(now: float) -> None:
    for k in [k for k, v in _stash.items() if now - v["at"] > _TTL_SEC]:
        _stash.pop(k, None)


def _gabung(lama: list, baru, cap: int) -> list:
    """Gabung TERBARU-DULU dgn dedup, lalu potong ke `cap`."""
    out: list = []
    for x in list(baru or []) + list(lama or []):
        s = str(x or "").strip()
        if s and s not in out:
            out.append(s)
    return out[:cap]


def _gabung_ts(lama: list, baru, cap: int, now: float) -> list:
    """Gabung ber-STEMPEL WAKTU, terbaru-dulu. Entri = {'s': teks, 'at': waktu}.
    Yang disebut ULANG giliran ini naik ke depan dgn stempel BARU — data yang
    baru dikonfirmasi tool memang segar lagi."""
    out: list = []
    seen: set[str] = set()
    for x in list(baru or []):
        s = str(x or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append({"s": s, "at": now})
    for e in list(lama or []):
        s = (e.get("s") if isinstance(e, dict) else str(e or "")).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(e if isinstance(e, dict) else {"s": s, "at": now})
    return out[:cap]


def _pisah_umur(entries: list, now: float) -> tuple[list, list]:
    """(segar, lama) — teks polos, urutan terjaga. Entri tanpa stempel = segar."""
    segar: list = []
    lama: list = []
    for e in entries or []:
        if isinstance(e, dict):
            (segar if now - float(e.get("at") or now) <= _FRESH_SEC
             else lama).append(e.get("s") or "")
        else:
            segar.append(str(e))
    return [s for s in segar if s], [s for s in lama if s]


def get(username: str, conv_id: str) -> dict | None:
    """Memo percakapan ini (SALINAN — pemanggil tak bisa memutasi stash).
    None bila tak ada / kedaluwarsa / conversation_id tak sah."""
    k = _key(username, conv_id)
    if not k:
        return None
    now = time.monotonic()
    with _lock:
        e = _stash.get(k)
        if not e:
            return None
        if now - e["at"] > _TTL_SEC:
            _stash.pop(k, None)
            return None
        # Touch-on-read: percakapan yang DIBACA sedang aktif — tanpa ini, eviksi
        # kuota per-user bisa menggusur justru percakapan yang sedang dipakai
        # hanya karena giliran-giliran terakhirnya tak menulis memo baru.
        e["at"] = now
        nums_segar, _ = _pisah_umur(e.get("nums") or [], now)
        fakta_segar, fakta_lama = _pisah_umur(e.get("fakta") or [], now)
        return {
            "rangka": list(e["rangka"]), "mesin": list(e["mesin"]),
            "wo": list(e["wo"]), "unit": list(e.get("unit") or []),
            "pn": dict(e["pn"]),
            # nums = HANYA yang segar: angka stok/harga basi tak boleh
            # meng-ground klaim baru. Fakta basi tetap dikembalikan terpisah
            # (fakta_lama) supaya pemakai bisa menampilkannya BERLABEL.
            "nums": nums_segar, "fakta": fakta_segar, "fakta_lama": fakta_lama,
        }


def merge(username: str, conv_id: str, *, rangka=(), mesin=(), wo=(),
          unit=(), pn=None, nums=(), fakta=()) -> bool:
    """Tambahkan hasil satu giliran ke memo. Best-effort: False bila memori sesi
    tak aktif (conversation_id tak sah). `pn` boleh mapping {PN: label} atau
    iterable PN. Tidak melempar — pemanggil ada di jalur jawaban user."""
    k = _key(username, conv_id)
    if not k:
        return False
    if isinstance(pn, dict):
        pn_baru = {str(p).strip().upper(): str(v or "") for p, v in pn.items() if str(p).strip()}
    else:
        pn_baru = {str(p).strip().upper(): "" for p in (pn or []) if str(p).strip()}
    now = time.monotonic()
    with _lock:
        _purge_locked(now)
        e = _stash.get(k) or {"rangka": [], "mesin": [], "wo": [], "unit": [],
                              "pn": {}, "nums": [], "fakta": []}
        e["rangka"] = _gabung(e["rangka"], rangka, _CAP_RANGKA)
        e["mesin"] = _gabung(e["mesin"], mesin, _CAP_MESIN)
        e["wo"] = _gabung(e["wo"], wo, _CAP_WO)
        e["unit"] = _gabung(e.get("unit") or [], unit, _CAP_UNIT)
        # ANGKA & FAKTA ber-stempel waktu: umur menentukan apakah masih boleh
        # meng-ground / disajikan tanpa label 'lama' (lihat get()).
        e["nums"] = _gabung_ts(e.get("nums") or [], nums, _CAP_NUMS, now)
        e["fakta"] = _gabung_ts(e.get("fakta") or [], fakta, _CAP_FAKTA, now)
        # PN: LRU sederhana — yang BARU disebut naik ke depan, ekor terpotong.
        gabung_pn = dict(pn_baru)
        for p, v in e["pn"].items():
            if p not in gabung_pn:
                gabung_pn[p] = v
        e["pn"] = dict(list(gabung_pn.items())[:_CAP_PN])
        e["at"] = now
        e["user"] = (username or "").strip().lower()

        if k not in _stash:
            # Kuota per-user DULU (pelajaran ai_sheet: pagar global saja membuat
            # user lain menggusur ingatan user ini), baru pagar RAM global.
            milik = [x for x, v in _stash.items() if v.get("user") == e["user"]]
            while len(milik) >= _MAX_PER_USER:
                tertua = min(milik, key=lambda x: _stash[x]["at"])
                _stash.pop(tertua, None)
                milik.remove(tertua)
            while len(_stash) >= _MAX_GLOBAL:
                _stash.pop(min(_stash, key=lambda x: _stash[x]["at"]), None)
        _stash[k] = e
    return True


def clear(username: str, conv_id: str) -> bool:
    """Lupakan satu percakapan (mis. user menekan 'hapus obrolan')."""
    k = _key(username, conv_id)
    if not k:
        return False
    with _lock:
        return _stash.pop(k, None) is not None


def _reset() -> None:
    """Kosongkan seluruh stash — HANYA untuk test."""
    with _lock:
        _stash.clear()
