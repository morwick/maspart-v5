"""
Service: TABEL PENGGANTIAN part MESIN Weichai (offline) — data/weichai_replace.json.gz
dipanen oleh backend/tools/build_weichai_replace.py (58.100 record, endpoint
`replace/page` tanpa filter).

Kenapa offline: `pengganti_part` selama ini menanyakan EPC Weichai LIVE per-PN —
menambah detik ke giliran chat dan ikut tumbang bila sesi/token bermasalah.
Tabel yang sama, sekali dipanen, membuat lookup INSTAN dan tetap menjawab saat
Weichai sedang tak bisa dihubungi.

⚠️ SATU record bisa memuat BANYAK PN: `oldPartNumber`/`newPartNumber` berisi PN
dipisah koma untuk relasi GRUP (mis. 4 PN lama ↔ 4 PN baru). Indeks di bawah
memetakan SETIAP PN di kedua sisi, dan tetap membawa daftar pasangan utuhnya.

⛔ File ABSEN bukan berarti "tidak ada pengganti" — `available()` False, dan
pemanggil WAJIB jatuh ke sumber live, bukan memvonis nihil.
"""
from __future__ import annotations

import gzip
import json
import re
import threading

from ..core.config import get_settings

_CACHE: dict = {"mtime": None, "data": {}, "idx": {}}
_lock = threading.Lock()


def _path():
    return get_settings().data_path / "weichai_replace.json.gz"


def _norm(pn: str) -> str:
    """PN dibandingkan tanpa spasi/strip/garis miring — samakan dengan gaya
    pencocokan PN di modul lain (varian penulisan katalog vs EPC)."""
    return re.sub(r"[\s_\-/]", "", (pn or "")).upper()


def _pns(v) -> list[str]:
    """Pecah sel multi-PN ('A,B,C') → daftar PN bersih."""
    out: list[str] = []
    for p in re.split(r"[\s,;]+", str(v or "")):
        p = p.strip().upper()
        if p and p not in out:
            out.append(p)
    return out


def _load() -> dict:
    try:
        p = _path()
        mt = p.stat().st_mtime if p.exists() else None
    except Exception:
        mt = None
    if mt != _CACHE["mtime"]:
        with _lock:
            if mt != _CACHE["mtime"]:
                data: dict = {}
                if mt is not None:
                    try:
                        with gzip.open(_path(), "rt", encoding="utf-8") as f:
                            data = json.load(f) or {}
                    except Exception:
                        data = {}
                _CACHE.update(mtime=mt, data=data, idx=_bangun_indeks(data))
    return _CACHE["data"]


def _bangun_indeks(data: dict) -> dict:
    """{PN_norm: {'lama': [...], 'baru': [...]}} — 'lama' = record di mana PN ini
    berada di sisi LAMA (jadi ia DIGANTIKAN oleh sisi baru), 'baru' sebaliknya."""
    idx: dict[str, dict] = {}
    for rec in (data.get("record") or []):
        lama = _pns(rec.get("oldPartNumber"))
        baru = _pns(rec.get("newPartNumber"))
        if not lama and not baru:
            continue
        ringkas = {
            "lama": lama, "baru": baru,
            "tanggal": (rec.get("replacementDate") or "").strip(),
            "tipe": (rec.get("replaceType") or "").strip(),
            "grup": (rec.get("replaceGroup") or "").strip(),
        }
        for pn in lama:
            idx.setdefault(_norm(pn), {"lama": [], "baru": []})["lama"].append(ringkas)
        for pn in baru:
            idx.setdefault(_norm(pn), {"lama": [], "baru": []})["baru"].append(ringkas)
    return idx


def available() -> bool:
    return bool(_load().get("record"))


def status() -> dict:
    d = _load()
    return {"tersedia": bool(d.get("record")),
            "jumlah_record": len(d.get("record") or []),
            "jumlah_pn": len(_CACHE["idx"]),
            "total_api": d.get("total_api") or 0,
            "lengkap": bool(d.get("lengkap")),
            "diambil": d.get("diambil") or ""}


def cari(part_number: str) -> dict:
    """Pengganti untuk satu PN mesin. {found, digantikan_oleh, menggantikan, ...}.

    'digantikan_oleh' = PN yang MENGGANTIKAN pn ini (pn ada di sisi lama).
    'menggantikan'    = PN LAMA yang digantikan pn ini (pn ada di sisi baru).
    Dua arah dipisah karena arah penggantian menentukan saran ke pembeli."""
    _load()
    pn = (part_number or "").strip().upper()
    if not pn:
        return {"found": False, "reason": "input"}
    if not _CACHE["idx"]:
        return {"found": False, "reason": "no_data",
                "message": "Tabel penggantian Weichai offline belum tersedia di server."}
    e = _CACHE["idx"].get(_norm(pn))
    if not e:
        return {"found": False, "part_number": pn}

    def _sisi(recs: list[dict], ambil: str) -> list[dict]:
        keluar: list[dict] = []
        lihat: set[str] = set()
        for r in recs:
            for p in r[ambil]:
                if _norm(p) == _norm(pn) or p in lihat:
                    continue
                lihat.add(p)
                item = {"pn": p}
                if r["tanggal"]:
                    item["tanggal"] = r["tanggal"]
                if r["tipe"]:
                    item["tipe"] = r["tipe"]
                # Relasi GRUP (banyak↔banyak) harus terlihat: memilih satu PN dari
                # grup 4-ke-4 seolah pasangan tunggal itu menyesatkan pembeli.
                if len(r["lama"]) > 1 or len(r["baru"]) > 1:
                    item["grup"] = {"lama": r["lama"], "baru": r["baru"]}
                keluar.append(item)
        return keluar

    digantikan = _sisi(e["lama"], "baru")
    menggantikan = _sisi(e["baru"], "lama")
    return {"found": bool(digantikan or menggantikan), "part_number": pn,
            "digantikan_oleh": digantikan, "menggantikan": menggantikan,
            "jumlah_record": len(e["lama"]) + len(e["baru"]), "sumber": "tabel offline Weichai"}
