"""Cache berkunci dengan TTL + plafon entri — pengganti langsung `dict` polos.

**Latar (2026-08-12).** Backend produksi diukur 1,66 GB dari jatah 2,44 GB hanya
23 menit setelah container hidup, lalu merangkak ke 2,31 GB (94,6%) sepanjang
hari; `anon` 1,76 GB (objek Python, bukan page cache yang bisa dilepas kernel).
Sebabnya: ada ~20 dict cache tingkat-modul berkunci frame/PN/nodeId yang
memeriksa TTL **saat dibaca** tapi tak pernah **menghapus** entri kedaluwarsa —
jadi hanya tumbuh. Beberapa (mis. `epc_bom._atlas_raw_cache`) malah berkunci
kombinatorial `"frame|MOD1,MOD2"`.

Akibatnya bukan cuma risiko OOM. `vin_ocr._SISA_MIN_MB` menolak membaca foto
saat sisa jatah container < 420 MB — di puncak 2,31 GB sisanya ±330 MB, jadi
fitur OCR foto rangka & kode kesalahan **mematikan dirinya sendiri** menjelang
sore, makin sering seiring container berumur.

Pola TTL+plafon di sini menyalin `p4_tools_diagnosa._unit_probe_cache` yang
sudah ada dan terbukti; kelas ini hanya membuatnya bisa dipakai ulang supaya
tiap cache baru tak perlu menulis ulang eviksinya sendiri (dan lupa lagi).

⚠️ Nilai yang disimpan **tidak ditafsirkan**. Pemanggil yang menaruh stempel
waktunya sendiri di dalam nilai (`{"at": ...}` / `{"ts": ...}`) tetap bekerja
apa adanya — kelas ini memakai jamnya SENDIRI untuk membuang, jadi konversi
dari dict polos tak mengubah logika pemanggil sebaris pun.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterator

_KOSONG = object()          # sentinel: beda dari nilai sah `None`

_daftar: list["CacheTTL"] = []
_daftar_lock = threading.Lock()


class CacheTTL:
    """dict berkunci yang MEMBUANG isinya sendiri (kedaluwarsa + plafon entri).

    Dipakai sebagai pengganti langsung `dict`: `.get()`, `c[k] = v`, `k in c`,
    `.pop()`, `.setdefault()`, `len()`, iterasi kunci.

    `ttl=None` → tanpa kedaluwarsa, hanya plafon entri. Dipakai untuk cache yang
    memang SENGAJA abadi selama proses hidup (lihat `epc._cache`, yang alasan
    'tanpa TTL'-nya ditulis eksplisit di sana) — di situ yang kita tambahkan
    cuma pagar jumlahnya.

    Urutan pembuangan = tertua-masuk-duluan, memanfaatkan urutan sisip `dict`
    bawaan Python (menulis ulang kunci yang sudah ada memindahkannya ke belakang),
    jadi eviksi O(1) tanpa memindai seluruh isi.
    """

    __slots__ = ("nama", "_ttl", "_maks", "_d", "_lock", "_dibuang")

    def __init__(self, nama: str, ttl: float | None, maks: int) -> None:
        if maks < 1:
            raise ValueError("maks minimal 1")
        self.nama = nama
        self._ttl = ttl
        self._maks = maks
        self._d: dict[Any, list] = {}      # kunci -> [kapan_masuk, nilai]
        self._lock = threading.RLock()
        self._dibuang = 0                  # penghitung eviksi (sinyal plafon kesempitan)
        with _daftar_lock:
            _daftar.append(self)

    # ── internal ────────────────────────────────────────────────────────
    def _kedaluwarsa(self, ent: list, sekarang: float) -> bool:
        return self._ttl is not None and (sekarang - ent[0]) >= self._ttl

    def _pangkas(self, sekarang: float) -> None:
        """Buang yang kedaluwarsa lalu yang tertua bila masih di atas plafon.

        Dipanggil saat TULIS saja — membaca tak perlu membayar biayanya, dan
        cache yang tak pernah ditulis lagi memang tak tumbuh."""
        if self._ttl is not None:
            for k in list(self._d):
                if not self._kedaluwarsa(self._d[k], sekarang):
                    break              # urut umur → sisanya pasti masih segar
                del self._d[k]
        while len(self._d) > self._maks:
            del self._d[next(iter(self._d))]
            self._dibuang += 1

    # ── antarmuka ala dict ──────────────────────────────────────────────
    def get(self, kunci: Any, bawaan: Any = None) -> Any:
        with self._lock:
            ent = self._d.get(kunci)
            if ent is None or self._kedaluwarsa(ent, time.monotonic()):
                return bawaan
            return ent[1]

    def __getitem__(self, kunci: Any) -> Any:
        nilai = self.get(kunci, _KOSONG)
        if nilai is _KOSONG:
            raise KeyError(kunci)
        return nilai

    def __setitem__(self, kunci: Any, nilai: Any) -> None:
        sekarang = time.monotonic()
        with self._lock:
            # hapus dulu supaya kunci lama pindah ke BELAKANG (= termuda)
            self._d.pop(kunci, None)
            self._d[kunci] = [sekarang, nilai]
            self._pangkas(sekarang)

    def __contains__(self, kunci: Any) -> bool:
        return self.get(kunci, _KOSONG) is not _KOSONG

    def __len__(self) -> int:
        with self._lock:
            return len(self._d)

    def __iter__(self) -> Iterator:
        with self._lock:
            return iter(list(self._d))

    def pop(self, kunci: Any, bawaan: Any = _KOSONG) -> Any:
        with self._lock:
            ent = self._d.pop(kunci, None)
        if ent is not None and not self._kedaluwarsa(ent, time.monotonic()):
            return ent[1]
        if bawaan is _KOSONG:
            raise KeyError(kunci)
        return bawaan

    def setdefault(self, kunci: Any, bawaan: Any) -> Any:
        """Seperti `dict.setdefault`. Nilai yang dikembalikan adalah objek yang
        TERSIMPAN, jadi pemanggil yang memutasinya (mis. `_cat_index`) tetap
        bekerja — hanya stempel waktunya yang tak ikut disegarkan."""
        with self._lock:
            ent = self._d.get(kunci)
            if ent is not None and not self._kedaluwarsa(ent, time.monotonic()):
                return ent[1]
            self[kunci] = bawaan
            return bawaan

    def clear(self) -> None:
        with self._lock:
            self._d.clear()

    # ── pelaporan (dipakai /api/admin/monitoring/memori) ─────────────────
    def info(self) -> dict:
        with self._lock:
            return {"nama": self.nama, "entri": len(self._d), "maks": self._maks,
                    "ttl": self._ttl, "dibuang": self._dibuang}


def daftar() -> list[CacheTTL]:
    with _daftar_lock:
        return list(_daftar)


def ringkasan() -> list[dict]:
    """Isi tiap cache terdaftar, terbanyak dulu. Entri yang sudah kedaluwarsa
    tapi belum sempat dipangkas ikut terhitung — itu memang yang menempati RAM."""
    return sorted((c.info() for c in daftar()), key=lambda x: -x["entri"])


def kosongkan_semua() -> None:
    """⚠️ Untuk TES saja — cache tingkat-modul bertahan lintas tes di proses yang
    sama, dan itu sumber tes yang lulus/gagal tergantung urutan."""
    for c in daftar():
        c.clear()
