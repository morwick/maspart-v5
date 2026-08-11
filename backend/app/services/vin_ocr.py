"""Service: baca NOMOR RANGKA (VIN) dari FOTO — OCR lokal + cocokkan ke armada.

Dipakai saat asisten meminta nomor rangka: user di lapangan cukup memotret nomor
yang dipahat di chassis, klien mengunggahnya ke `POST /api/ai/ocr-rangka`, lalu
MENGIRIM hasil bacanya sebagai pesan chat biasa. Model bahasa TIDAK pernah
melihat gambar — yang sampai ke asisten hanya TEKS nomor rangka.

⚠️ Yang membuat fitur ini layak pakai BUKAN OCR-nya. Diukur pada foto lapangan
pemilik (2026-08-11, nomor dipahat di chassis, huruf tipis & berdebu), RapidOCR
mentah membaca `★L7Z1BLMJ4TJ465057` — satu huruf salah (Z→7); pada varian foto
lain ia salah di huruf yang berbeda-beda. Yang menyelamatkan adalah LAPISAN
KEDUA di bawah ini:

  1. beberapa varian pra-proses (kontras CLAHE / pertajam / perbesar) dibaca
     bergantian — tiap varian salah di tempat berbeda;
  2. hasil baca dicocokkan ke ±1.300 VIN ARMADA (populasi) dengan jarak
     Levenshtein BERBOBOT: substitusi antar huruf yang MEMANG sering tertukar
     OCR (0↔O, 2↔Z↔7, 5↔S, 8↔B, …) dihitung murah (0,35), sisanya 1,0;
  3. VIN di luar armada divalidasi lewat CHECK DIGIT VIN (ISO 3779/GB 16735 —
     terbukti cocok di 1.332 dari 1.335 unit armada, 99,8%) + bentuk ekor
     `[A-Z]{2}\\d{6}` (100% unit armada).

Hasil ukur (foto asli + 7 versi dirusak: 600px, blur, gelap, miring 8°, JPEG
mutu 25, crop ketat) = 8/8 benar, 0,6–2,3 detik. Kalibrasi ambang snap
(simulasi 900 VIN dirusak 1–3 huruf + 300 versi rusak berat) = 0 kasus
"nyangkut ke unit lain"; yang tak yakin ditolak jadi keyakinan rendah, dan
di situ USER yang mengoreksi. ⛔ Jangan longgarkan `_SNAP_BATAS`/`_SNAP_MARGIN`
tanpa mengulang kalibrasi itu: VIN armada bertetangga rapat (unit sekali kirim
beda 1–2 digit di ekor), jadi ambang yang longgar = salah unit tanpa suara.

RAM: model ONNX RapidOCR (PP-OCRv3 det+rec+cls, 13 MB di dalam wheel) dimuat
SEKALI & lazy — sama pola dgn ddddocr. Diukur: +160 MB RSS saat foto PERTAMA
dibaca (opencv + onnxruntime + model), lalu tetap; container backend dibatasi
2500m dan torch/DINOv2 sudah memakai ±1 GB, jadi masih lapang.
"""
from __future__ import annotations

import io
import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

# Foto HP 12 MP ≈ 3–5 MB; 12 MB memberi ruang tanpa mengundang unggahan raksasa.
MAX_BYTES = 12 * 1024 * 1024
# Sisi terpanjang foto sebelum OCR. RapidOCR menskalakan sendiri sebelum deteksi
# (sisi terpendek ±736 px), jadi foto 12 MP hanya menambah waktu & memori tanpa
# menambah akurasi — versi 600 px dari foto uji pun terbaca benar.
_MAKS_SISI = 1600
# Anggaran waktu: varian berikutnya tak dimulai lagi setelah lewat batas ini.
_BATAS_DETIK = 12.0

# Ambang snap-ke-armada (lihat kalibrasi di docstring modul).
_SNAP_BATAS = 2.0      # jarak berbobot maksimum agar dianggap unit yang sama
_SNAP_MARGIN = 0.5     # selisih minimum ke kandidat armada KEDUA (anti-ambigu)

# Huruf/angka yang sering tertukar saat OCR (dikelompokkan; kesalahan nyata dari
# foto uji: Z→7, J→1, 4→L, 0→O). Dipakai untuk MEMURAHKAN substitusi, bukan
# untuk menerima apa pun — bentuk VIN & check digit tetap diperiksa terpisah.
_GRUP_MIRIP = ("0OQD", "1ILT7J", "2Z7", "5S", "8B", "6G", "4A", "UV", "MHN",
               "CG", "XK", "YV", "EF", "9G")
_MIRIP: set[tuple[str, str]] = set()
for _g in _GRUP_MIRIP:
    for _a in _g:
        for _b in _g:
            if _a != _b:
                _MIRIP.add((_a, _b))

_SUB_MIRIP = 0.35
_SUB_LAIN = 1.0
_INDEL = 0.9

# Standar VIN: huruf I, O, Q TIDAK pernah dipakai (justru supaya tak tertukar
# dengan 1 dan 0) → kemunculannya = kesalahan OCR yang bisa dibetulkan langsung.
_TERLARANG = "IOQ"
_EKOR_RE = re.compile(r"[A-Z]{2}\d{6}$")     # 8 char terakhir = frame number EPC
_FRAME_RE = re.compile(r"^[A-Z]{2}\d{6}$")

# Bobot & tabel transliterasi check digit VIN (ISO 3779 / GB 16735-2019).
_TRANS = {c: i for i, c in enumerate("0123456789")}
_TRANS.update({"A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
               "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
               "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9})
_BOBOT = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)


def bersih(s: str) -> str:
    """Buang apa pun selain A–Z/0–9 (bintang ★, spasi, strip) + huruf besar."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def check_digit(vin: str) -> str:
    """Digit ke-9 yang SEHARUSNYA menurut standar VIN ('X' bila sisa 10)."""
    total = sum(_TRANS.get(c, 0) * _BOBOT[i] for i, c in enumerate(vin[:17]))
    sisa = total % 11
    return "X" if sisa == 10 else str(sisa)


def cd_cocok(vin: str) -> bool:
    """VIN 17 char dengan check digit yang benar? Peluang lolos acak 1/11, jadi
    ini bukti kuat untuk unit yang TIDAK ada di armada."""
    return len(vin) == 17 and vin[8] == check_digit(vin)


def bentuk_vin_wajar(c: str) -> bool:
    """Bentuk VIN truk yang kita layani: 17 char, tanpa I/O/Q, ekor 2 huruf + 6
    angka (100% dari 1.335 unit armada berbentuk begini)."""
    return (len(c) == 17 and not (set(c) & set(_TERLARANG))
            and bool(_EKOR_RE.search(c)) and c[:3].isalpha())


def frame_dari(vin: str) -> str:
    """Frame number EPC = 8 char terakhir (yang dipakai loading_list/Atlas)."""
    c = bersih(vin)
    return c[-8:] if len(c) >= 8 else c


def jarak(a: str, b: str, batas: float = 4.0) -> float:
    """Levenshtein BERBOBOT; berhenti lebih awal bila sudah melewati `batas`."""
    la, lb = len(a), len(b)
    if abs(la - lb) * _INDEL > batas:
        return batas + 1
    prev = [i * _INDEL for i in range(lb + 1)]
    for i in range(1, la + 1):
        ca = a[i - 1]
        cur = [i * _INDEL] + [0.0] * lb
        best = cur[0]
        for j in range(1, lb + 1):
            cb = b[j - 1]
            c = 0.0 if ca == cb else (_SUB_MIRIP if (ca, cb) in _MIRIP else _SUB_LAIN)
            cur[j] = min(prev[j - 1] + c, prev[j] + _INDEL, cur[j - 1] + _INDEL)
            if cur[j] < best:
                best = cur[j]
        if best > batas:                    # baris terbaik pun sudah lewat batas
            return batas + 1
        prev = cur
    return prev[lb]


# ── Armada: VIN yang KITA kenal (populasi) ────────────────────────────
# Cache 1 jam: populasi.xlsx diunduh dari Supabase Storage, jangan ditembak tiap
# unggahan foto. Best-effort — populasi mati = fitur tetap jalan, hanya kehilangan
# keyakinan "pasti" (jatuh ke validasi check digit).
_ARMADA: dict = {"at": 0.0, "rows": []}
_ARMADA_TTL = 3600.0
_lock = threading.Lock()


def armada() -> list[tuple[str, dict]]:
    """[(vin17, {model, jenis, tahun, customer})] dari populasi."""
    with _lock:
        if _ARMADA["rows"] and (time.monotonic() - _ARMADA["at"] < _ARMADA_TTL):
            return _ARMADA["rows"]
    rows: list[tuple[str, dict]] = []
    try:
        from . import populasi
        df = populasi._ensure()
        if df is not None and not getattr(df, "empty", True):
            cols = {str(c).strip().upper(): c for c in df.columns}
            c_vin = cols.get("NOMOR RANGKA")
            if c_vin:
                c_model, c_jenis = cols.get("MODEL"), cols.get("JENIS")
                c_tahun, c_cust = cols.get("TAHUN"), cols.get("CUSTOMER")

                def _s(r, c) -> str:
                    return " ".join(str(r.get(c) or "").split()) if c else ""

                for _, r in df.iterrows():
                    vin = bersih(str(r.get(c_vin) or ""))
                    if len(vin) != 17:
                        continue
                    rows.append((vin, {"model": _s(r, c_model).upper(),
                                       "jenis": _s(r, c_jenis).upper(),
                                       "tahun": _s(r, c_tahun)[:4],
                                       "customer": _s(r, c_cust)}))
    except Exception:                        # populasi absen/rusak → tanpa armada
        logger.info("vin_ocr: populasi tak tersedia (snap armada dilewati)")
        rows = []
    with _lock:
        _ARMADA["rows"], _ARMADA["at"] = rows, time.monotonic()
    return rows


# ── OCR ───────────────────────────────────────────────────────────────
_mesin_box: dict = {"ocr": None}
_mesin_lock = threading.Lock()      # pemuatan model (sekali)
_pakai_lock = threading.Lock()      # pemakaian model (satu foto pada satu waktu)


def _mesin():
    """RapidOCR (ONNX, CPU) — dimuat sekali, lazy. None bila paket tak terpasang
    (dev tanpa dependensi → fitur mati bersih, bukan 500 misterius)."""
    with _mesin_lock:
        if _mesin_box["ocr"] is None:
            from rapidocr_onnxruntime import RapidOCR   # lazy: ±15 MB model ONNX
            _mesin_box["ocr"] = RapidOCR()
        return _mesin_box["ocr"]


def _baris_ocr(bgr) -> list[str]:
    """Teks tiap kotak yang terbaca (urut hasil OCR).

    Dipagari satu lock: pipeline RapidOCR menyimpan state per-instance, dan dua
    unggahan foto bersamaan (endpoint berjalan di threadpool) tak boleh saling
    menimpa. Antre 1–2 detik jauh lebih murah daripada bacaan tercampur — dan
    lebih murah pula daripada memuat model kedua di container 2500m."""
    with _pakai_lock:
        res, _ = _mesin()(bgr)
    return [str(r[1]) for r in (res or [])]


def _decode(data: bytes):
    """bytes → ndarray BGR, sudah diputar sesuai EXIF & dibatasi `_MAKS_SISI`."""
    import numpy as np
    from PIL import Image, ImageOps
    try:
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im)          # foto HP portrait sering "rebah"
        im = im.convert("RGB")
    except Exception as e:
        raise ValueError(
            "Foto tidak terbaca (format tidak didukung). Kirim JPG/PNG — foto "
            f"HEIC dari iPhone perlu diubah dulu. [{type(e).__name__}]")
    sisi = max(im.size)
    if sisi > _MAKS_SISI:
        f = _MAKS_SISI / sisi
        im = im.resize((max(1, int(im.width * f)), max(1, int(im.height * f))),
                       Image.LANCZOS)
    return np.asarray(im)[:, :, ::-1].copy()      # RGB → BGR untuk cv2/RapidOCR


def _varian(bgr):
    """Varian pra-proses, URUT dari yang paling sering menang (hemat waktu: yang
    pertama sudah cukup untuk foto bagus, sisanya hanya jalan bila belum yakin).

    Diukur pada foto uji: `clahe` & `raw` benar 17 char, `clahe+up2` salah di
    huruf LAIN — justru itu gunanya, kesalahannya tidak berkorelasi."""
    import cv2

    def _clahe(x):
        lab = cv2.cvtColor(x, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(l)
        return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)

    c = _clahe(bgr)
    yield "clahe", c
    yield "raw", bgr
    yield "clahe+tajam", cv2.addWeighted(c, 1.6, cv2.GaussianBlur(c, (0, 0), 3), -0.6, 0)
    # Perbesar 2× menolong huruf pahatan yang tipis pada foto yang diambil agak jauh.
    yield "clahe+2x", cv2.resize(c, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)


def _kandidat(teks: str) -> list[str]:
    """Potongan yang mungkin nomor rangka dari satu string hasil OCR."""
    out: list[str] = []
    if len(teks) >= 17:
        out.extend(teks[i:i + 17] for i in range(len(teks) - 16))
    elif len(teks) >= 8:
        out.append(teks)
    return out


def _perbaiki_ejaan(c: str) -> str:
    """Huruf yang TIDAK pernah ada di VIN → angka pasangannya."""
    return c.replace("I", "1").replace("O", "0").replace("Q", "0")


def _perbaiki_cd(c: str) -> list[str]:
    """Tukar SATU huruf (dalam kelas kebingungan OCR) supaya check digit cocok.

    ⛔ Posisi ke-9 (check digit itu sendiri) sengaja TIDAK ikut ditukar: mengubah
    digit pemeriksa selalu 'berhasil' dan karenanya tak membuktikan apa pun."""
    out: list[str] = []
    for i, ch in enumerate(c):
        if i == 8:
            continue
        for g in _GRUP_MIRIP:
            if ch not in g:
                continue
            for alt in g:
                if alt == ch or alt in _TERLARANG:
                    continue
                kand = c[:i] + alt + c[i + 1:]
                if bentuk_vin_wajar(kand) and cd_cocok(kand) and kand not in out:
                    out.append(kand)
    return out


def _snap(kand: dict[str, int], rows: list[tuple[str, dict]]) -> dict | None:
    """Kandidat OCR → unit armada terdekat. None bila tak ada yang cukup dekat."""
    if not kand or not rows:
        return None
    terbaik: tuple[float, float, str, str] | None = None   # (jarak, kedua, vin, kandidat)
    for c in kand:
        if len(c) < 8:
            continue
        # Potongan pendek (frame 8 char) membawa bukti jauh lebih sedikit daripada
        # VIN penuh — ekor unit satu pengiriman cuma beda 1 digit. Jadi untuk itu
        # ambangnya diperketat: praktis harus PERSIS (atau satu huruf sekelas).
        batas = _SNAP_BATAS if len(c) >= 12 else 1.0
        best, best_vin, kedua = 99.0, "", 99.0
        for vin, _info in rows:
            target = vin if len(c) >= 12 else vin[-len(c):]
            d = jarak(c, target, batas=batas + 1.0)
            if d < best:
                best, kedua, best_vin = d, best, vin
            elif d < kedua:
                kedua = d
        if terbaik is None or best < terbaik[0]:
            terbaik = (best, kedua, best_vin, c)
    if terbaik is None or terbaik[0] > _SNAP_BATAS:
        return None
    # Bacaan PERSIS sama dengan satu unit tak perlu unggul selisih apa pun — ia
    # bukan tebakan. (Aturan margin di bawah sempat menolaknya: unit sekali
    # kirim bisa punya ekor yang cuma beda 7↔2, dua angka yang memang sekelas.)
    persis = terbaik[0] == 0 and terbaik[1] > 0
    if not persis and (terbaik[1] - terbaik[0]) < _SNAP_MARGIN:
        return None                                  # dua unit sama-sama dekat
    vin = terbaik[2]
    info = next((i for v, i in rows if v == vin), {})
    return {"rangka": vin, "keyakinan": "pasti", "unit": info, "cocok": terbaik[3]}


def _tanpa_armada(kand: dict[str, int]) -> dict | None:
    """Unit di LUAR populasi: bersandar pada bentuk VIN + check digit."""
    vin17 = sorted((c for c in kand if len(c) == 17),
                   key=lambda c: (-kand[c], c))
    for c in vin17:
        c2 = _perbaiki_ejaan(c)
        if not bentuk_vin_wajar(c2):
            continue
        if cd_cocok(c2):
            return {"rangka": c2, "keyakinan": "tinggi", "unit": None}
        perbaikan = _perbaiki_cd(c2)
        if len(perbaikan) == 1:              # hanya SATU pembetulan yang masuk akal
            return {"rangka": perbaikan[0], "keyakinan": "tinggi", "unit": None,
                    "alternatif": [c2]}
        if perbaikan:
            return {"rangka": c2, "keyakinan": "rendah", "unit": None,
                    "alternatif": perbaikan[:3]}
        return {"rangka": c2, "keyakinan": "rendah", "unit": None}
    # Hanya frame 8 char yang terlihat (banyak unit dipahat frame-nya saja).
    frame = [c for c in kand if _FRAME_RE.match(_perbaiki_ejaan(c))]
    if frame:
        frame.sort(key=lambda c: -kand[c])
        return {"rangka": _perbaiki_ejaan(frame[0]), "keyakinan": "rendah", "unit": None}
    return None


def baca_rangka(data: bytes, *, isolasi: bool = True) -> dict:
    """Foto → nomor rangka. TIDAK melempar untuk foto yang sekadar tak terbaca.

    → {ok, rangka, frame, keyakinan: pasti|tinggi|rendah|gagal, unit, alternatif,
       teks_terbaca, pesan, detik}

    `isolasi=True` (default) menjalankan OCR-nya di PROSES ANAK — lihat
    `_jalankan_anak`. Foto yang tak terbaca sebagai gambar tetap melempar
    ValueError seperti biasa (divalidasi di proses induk sebelum menyalakan anak).
    """
    _decode(data)                        # format rusak → ValueError, sebelum spawn
    if isolasi:
        sisa = _sisa_memori_mb()
        if sisa is not None and sisa < _SISA_MIN_MB:
            logger.warning("vin_ocr: jatah memori container tinggal %.0f MB — "
                           "pembacaan foto ditunda", sisa)
            return {"ok": False, "rangka": "", "frame": "", "keyakinan": "gagal",
                    "unit": None, "alternatif": [], "teks_terbaca": [], "detik": 0.0,
                    "pesan": "Server sedang penuh sesaat — coba kirim fotonya lagi "
                             "beberapa saat lagi, atau ketik nomor rangkanya."}
        hasil = _jalankan_anak(data, armada())
        if hasil is not None:
            return hasil
        logger.warning("vin_ocr: proses anak gagal — jatuh ke OCR dalam-proses")
    return _baca_lokal(data, armada())


def _baca_lokal(data: bytes, rows: list[tuple[str, dict]]) -> dict:
    """Pipeline sesungguhnya (dipakai proses anak, atau langsung bila isolasi mati)."""
    t0 = time.monotonic()
    hasil: dict = {"ok": False, "rangka": "", "frame": "", "keyakinan": "gagal",
                   "unit": None, "alternatif": [], "teks_terbaca": [],
                   "pesan": "", "detik": 0.0}
    bgr = _decode(data)                      # ValueError → 400 di router
    kand: dict[str, int] = {}
    terbaca: list[str] = []
    pilihan: dict | None = None

    for nama, im in _varian(bgr):
        if kand and (time.monotonic() - t0) > _BATAS_DETIK:
            logger.info("vin_ocr: anggaran waktu habis, berhenti di varian %s", nama)
            break
        try:
            baris = _baris_ocr(im)
        except Exception as e:               # satu varian gagal ≠ seluruhnya gagal
            logger.warning("vin_ocr: varian %s gagal: %s", nama, e)
            continue
        bersih_baris = [bersih(b) for b in baris]
        for b in baris:
            if b and b not in terbaca:
                terbaca.append(b)
        # Nomor rangka bisa terpecah jadi beberapa kotak (bintang di kiri-kanan,
        # atau pahatan yang renggang) → coba tiap kotak DAN gabungan semuanya.
        for s in bersih_baris + ["".join(bersih_baris)]:
            for c in _kandidat(s):
                kand[c] = kand.get(c, 0) + 1
        pilihan = _snap(kand, rows)
        if pilihan:                          # cocok satu unit armada → sudah cukup
            break

    if not pilihan:
        pilihan = _snap(kand, rows) or _tanpa_armada(kand)

    hasil["teks_terbaca"] = terbaca[:8]
    hasil["detik"] = round(time.monotonic() - t0, 2)
    if not pilihan:
        hasil["pesan"] = ("Nomor rangka tidak terbaca dari foto. Coba foto lebih "
                          "dekat, tegak lurus, dan pastikan seluruh 17 karakter "
                          "masuk dalam bingkai (lap dulu bila berdebu).")
        return hasil

    hasil.update({k: v for k, v in pilihan.items() if k in ("rangka", "keyakinan", "unit")})
    hasil["alternatif"] = pilihan.get("alternatif") or []
    hasil["frame"] = frame_dari(hasil["rangka"])
    hasil["ok"] = bool(hasil["rangka"])
    hasil["pesan"] = pesan(hasil)
    return hasil


# ── Isolasi proses ────────────────────────────────────────────────────
# Diukur di container produksi 2026-08-11: puncak pemakaian backend 2,31 GB dari
# batas 2,44 GB (94,6%) SEBELUM fitur ini ada. Memuat cv2+onnxruntime+model di
# proses server menambah ±120–160 MB yang TIDAK bisa dilepas lagi (mencoba
# membuang objek modelnya cuma mengembalikan 23 MB — sisanya adalah pustaka yang
# terlanjur di-import). Itu praktis menjamin OOM-kill suatu saat, dan OOM =
# asisten mati untuk semua orang.
#
# Maka OCR dijalankan di PROSES ANAK yang mati setelah selesai: memorinya kembali
# ke OS seutuhnya, dan puncaknya hanya ada selama 2–5 detik pembacaan. Ongkosnya
# ±1 detik (model dimuat ulang tiap foto) — murah untuk aksi yang sesekali.
# Armada dikirim lewat stdin, JADI anak TIDAK perlu menyentuh Supabase/populasi.
_BATAS_ANAK = 45.0
# Proses anak memuncak ±266 MB (diukur di image produksi, VPS 1 vCPU). Bila jatah
# container tinggal segitu, MEMBACA FOTO = mempertaruhkan seluruh asisten:
# OOM-killer cgroup akan memilih proses terbesar, yaitu server itu sendiri. Lebih
# jujur menolak sebentar daripada menjatuhkan semua orang.
_SISA_MIN_MB = 420.0


def _sisa_memori_mb() -> float | None:
    """Sisa jatah memori container (MB). None = tak diketahui (mis. laptop dev).

    Page cache (`file`) dipotong dari pemakaian karena bisa dilepas kernel kapan
    saja — menghitungnya sebagai 'terpakai' akan menolak foto tanpa alasan."""
    try:                                             # cgroup v2
        with open("/sys/fs/cgroup/memory.max") as f:
            teks = f.read().strip()
        if teks == "max":
            return None
        batas = float(teks)
        with open("/sys/fs/cgroup/memory.current") as f:
            dipakai = float(f.read().strip())
        cache = 0.0
        with open("/sys/fs/cgroup/memory.stat") as f:
            for baris in f:
                if baris.startswith("file "):
                    cache = float(baris.split()[1])
                    break
        return (batas - max(0.0, dipakai - cache)) / 1024 / 1024
    except Exception:
        pass
    try:                                             # cgroup v1
        with open("/sys/fs/cgroup/memory/memory.limit_in_bytes") as f:
            batas = float(f.read().strip())
        if batas > 1 << 50:                          # 'tanpa batas' → bukan container
            return None
        with open("/sys/fs/cgroup/memory/memory.usage_in_bytes") as f:
            dipakai = float(f.read().strip())
        return (batas - dipakai) / 1024 / 1024
    except Exception:
        return None


def _jalankan_anak(data: bytes, rows: list[tuple[str, dict]]) -> dict | None:
    """OCR di proses terpisah. None = gagal (pemanggil jatuh ke dalam-proses)."""
    import base64
    import json
    import subprocess
    import sys
    from pathlib import Path

    akar = str(Path(__file__).resolve().parents[2])      # …/backend (induk paket `app`)
    payload = json.dumps({"gambar": base64.b64encode(data).decode(),
                          "armada": [[v, i] for v, i in rows]})
    try:
        p = subprocess.run(
            [sys.executable, "-c", "from app.services.vin_ocr import _cli; _cli()"],
            input=payload.encode("utf-8"), capture_output=True,
            cwd=akar, timeout=_BATAS_ANAK)
    except Exception as e:                               # spawn/timeout gagal
        logger.warning("vin_ocr: proses anak tak jalan: %s", e)
        return None
    if p.returncode != 0:
        logger.warning("vin_ocr: proses anak keluar %s: %s", p.returncode,
                       (p.stderr or b"")[-400:].decode("utf-8", "replace"))
        return None
    try:
        return json.loads((p.stdout or b"").decode("utf-8") or "{}") or None
    except Exception:
        logger.warning("vin_ocr: keluaran proses anak tak terbaca")
        return None


def _cli() -> None:
    """Titik masuk proses anak: stdin JSON {gambar(base64), armada} → stdout JSON."""
    import base64
    import json
    import sys

    req = json.loads(sys.stdin.read() or "{}")
    rows = [(str(v), dict(i or {})) for v, i in (req.get("armada") or [])]
    hasil = _baca_lokal(base64.b64decode(req.get("gambar") or ""), rows)
    sys.stdout.write(json.dumps(hasil, ensure_ascii=False))


def pesan(hasil: dict) -> str:
    """Kalimat siap tampil untuk klien (web & mobile memakai yang sama)."""
    rangka, unit = hasil.get("rangka") or "", hasil.get("unit") or {}
    yakin = hasil.get("keyakinan")
    if yakin == "pasti":
        ket = " · ".join(x for x in (unit.get("jenis"), unit.get("model"),
                                     unit.get("tahun")) if x)
        return f"Nomor rangka: {rangka}" + (f" ({ket})" if ket else "")
    if yakin == "tinggi":
        return (f"Nomor rangka: {rangka} — unit ini belum ada di data populasi, "
                "periksa sekali lagi sebelum dipakai.")
    if yakin == "rendah":
        return (f"Saya baca: {rangka} — BELUM yakin. Perbaiki bila keliru, "
                "lalu kirim.")
    return hasil.get("pesan") or "Nomor rangka tidak terbaca."
