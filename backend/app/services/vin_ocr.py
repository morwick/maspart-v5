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
     `[A-Z]{2}\\d{6}` dan awalan 'L' (dua-duanya 100% unit armada);
  4. bila nilainya tak terdeteksi sama sekali, jalur di samping LABEL dibaca
     ulang sebagai satu baris (`_jalur_label`) — penyelamat plat beretsa.

Bentuk foto lapangan yang didukung:
  • nomor DIPAHAT di chassis — satu baris, kontras rendah, berdebu;
  • NAMEPLATE pabrik (CNHTC) — penuh teks lain (MODEL, ENGINE OUTPUT, RAW,
    GCW/GVW, MADE YEAR). Di sini label "CHASSIS NO." dipakai sebagai petunjuk
    (`_kandidat_label`) dan potongan tanpa cukup angka dibuang sebelum diadu
    (`_cukup_angka`) — tanpa itu satu plat menghasilkan 137 kandidat dan
    pencocokannya sendiri makan 21 detik;
  • PLAT BERETSA berlabel Mandarin (Wuyue/CNHTC) dan plat DUA PANEL
    (Inggris + Mandarin berdampingan) — ditambahkan 2026-08-11 dan menuntut
    tiga hal baru sekaligus, lihat `_jalur_label`, `_perbaiki_bentuk`, dan
    catatan urutan di `_tanpa_armada`/`_urut_kandidat`;
  • foto REBAH 90° / terbalik 180° — lazim karena plat dipotret sambil berdiri
    di samping unit. Ditangani tanpa memutar seluruh foto bila memungkinkan
    (`_jalur_label`), dengan putaran penuh sebagai cadangan (`_orientasi`).

Hasil ukur 6 foto lapangan × 8 versi (asli, 600px, blur, gelap, miring 8°,
diputar 90°/180°/270°) = 45 dari 48 benar, 0,4–3 detik untuk foto wajar.
Tiga sisanya = plat beretsa yang DIRUSAK berat (blur/miring) → semuanya pulang
"gagal" dengan jujur, bukan menebak. Sebelum perubahan 2026-08-11, tiga dari
enam foto itu tak terbaca sama sekali.

Kalibrasi ambang snap (simulasi 900 VIN dirusak 1–3 huruf + 300 versi rusak
berat, diulang 2026-08-11) = 0 kasus "nyangkut ke unit lain"; yang tak yakin
ditolak jadi keyakinan rendah, dan di situ USER yang mengoreksi. ⛔ Jangan
longgarkan `_SNAP_BATAS`/`_SNAP_MARGIN`/`_MAKS_KANDIDAT` tanpa mengulang
kalibrasi itu: VIN armada bertetangga rapat (unit sekali kirim beda 1–2 digit
di ekor), jadi ambang yang longgar = salah unit tanpa suara.

⚠️ Pelajaran yang paling mahal di sini BUKAN soal OCR: beberapa kali sebuah
perbaikan menaikkan satu foto sambil menjatuhkan foto lain diam-diam (lihat
catatan ⛔/⚠️ di `_MAKS_KANDIDAT`, `_urut_kandidat`, `_bentuk_kasar`,
`_JALUR_ANGGARAN`). Ukur SELURUH 48 kombinasi tiap kali menyentuh ambang atau
urutan — satu foto saja tidak membuktikan apa pun.

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
# Anggaran waktu per ORIENTASI: varian berikutnya tak dimulai lagi setelah lewat
# batas ini. (Bukan batas keras — varian yang sudah berjalan diselesaikan.)
_BATAS_DETIK = 9.0
# …dan varian "perbesar 2×" (yang termahal) tak dimulai lagi setelah batas ini.
_BATAS_2X = 5.0
# Orientasi kedua (foto rebah 90°) TAK dimulai lagi bila yang pertama sudah
# sepanjang ini — foto yang memang tak terbaca sebaiknya cepat mengaku daripada
# menahan user. Anggarannya sendiri dihitung ulang dari saat ia MULAI, bukan dari
# awal permintaan: pernah dicoba anggaran absolut, dan dua foto sulit yang tadinya
# terbaca benar (12–17 dtk) berubah jadi gagal karena kehabisan jatah di tengah
# jalan. Foto sulit lebih baik lambat daripada salah/gagal — yang normal tetap
# 0,4–3 detik karena semua ini hanya jalan saat sudah buntu.
_BATAS_MULAI_MIRING = 15.0

# Ambang snap-ke-armada (lihat kalibrasi di docstring modul).
_SNAP_BATAS = 2.0      # jarak berbobot maksimum agar dianggap unit yang sama
_SNAP_MARGIN = 0.5     # selisih minimum ke kandidat armada KEDUA (anti-ambigu)
# Berapa banyak potongan yang diadu ke armada. ⛔ JANGAN dinaikkan: sempat dicoba
# 32 dengan alasan "indeks ekor sudah membuat perbandingan murah", dan hasilnya
# JUSTRU turun (45→44 benar, satu plat yang tadinya terbaca jadi gagal, satu foto
# melar jadi 45 detik). Sebabnya bukan kecepatan: `_snap` memilih kandidat
# TERDEKAT ke armada, jadi tiap potongan sampah tambahan adalah satu peluang lagi
# untuk berdiri lebih dekat daripada nomor yang benar — lalu ditolak aturan
# margin, dan jawabannya hilang sama sekali. Sempit = terjaga.
_MAKS_KANDIDAT = 16

# Huruf/angka yang sering tertukar saat OCR (dikelompokkan; kesalahan nyata dari
# foto uji: Z→7, J→1, 4→L, 0→O). Dipakai untuk MEMURAHKAN substitusi, bukan
# untuk menerima apa pun — bentuk VIN & check digit tetap diperiksa terpisah.
_GRUP_MIRIP = ("0OQD", "1ILT7J", "2Z7", "5S", "38B", "6G", "4A", "UV", "MHN",
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

# Angka + huruf yang sekelas dengannya (O/Q/D↔0, B↔8/3, A↔4, …) — dipakai
# `_cukup_angka` untuk menaksir "ini ekor nomor rangka atau kalimat biasa".
_MIRIP_ANGKA = set("0123456789") | {
    ch for g in _GRUP_MIRIP if any(c.isdigit() for c in g) for ch in g}

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
    """Bentuk VIN truk yang kita layani: 17 char, diawali 'L', tanpa I/O/Q, ekor
    2 huruf + 6 angka. Ketiganya berlaku di 1.335 dari 1.335 unit armada.

    Awalan 'L' = kode negara Tiongkok (ISO 3780) — dan seluruh merek yang dilayani
    (CNHTC/Sinotruk, Shantui, Weichai) buatan Tiongkok. Syarat kecil ini yang
    menahan kalimat plat yang KEBETULAN lolos check digit: foto terbalik membuat
    'Permissible axle load,axle 4 16000' terbaca 'XLEL0ADAXLE416000' — 17 char,
    ekornya 2 huruf + 6 angka, dan check digit-nya pun cocok (peluangnya 1/11),
    jadi tanpa aturan ini ia disodorkan sebagai nomor rangka dengan keyakinan
    TINGGI. ⚠️ Konsekuensinya unit non-Tiongkok tak akan pernah dibaca otomatis —
    disengaja; di situ user mengetik sendiri nomornya."""
    return (len(c) == 17 and c[0] == "L" and not (set(c) & set(_TERLARANG))
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


def _kotak_ocr(bgr) -> list[tuple[float, float, float, float, str]]:
    """Tiap kotak teks yang terbaca: (x0, y0, x1, y1, teks), urut hasil OCR.

    LETAK kotak ikut dibawa (bukan teksnya saja) karena `_jalur_label` perlu tahu
    di mana label "VIN"/"CHASSIS NO." berdiri untuk membaca ulang jalur nilainya.

    Dipagari satu lock: pipeline RapidOCR menyimpan state per-instance, dan dua
    unggahan foto bersamaan (endpoint berjalan di threadpool) tak boleh saling
    menimpa. Antre 1–2 detik jauh lebih murah daripada bacaan tercampur — dan
    lebih murah pula daripada memuat model kedua di container 2500m."""
    with _pakai_lock:
        res, _ = _mesin()(bgr)
    out = []
    for box, teks, _skor in (res or []):
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        out.append((min(xs), min(ys), max(xs), max(ys), str(teks)))
    return out


def kotak_datar(*teks: str, tinggi: float = 20.0, lebar: float = 12.0):
    """Kotak tiruan berjajar mendatar — dipakai test untuk mengganti `_kotak_ocr`
    tanpa perlu gambar sungguhan."""
    return [(0.0, i * tinggi * 2, len(t) * lebar, i * tinggi * 2 + tinggi, t)
            for i, t in enumerate(teks)]


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


# Label pada NAMEPLATE pabrik (CNHTC/Sinotruk) & dokumen: nomor rangka berdiri
# tepat sesudahnya. Di plat, OCR kadang menyatukannya ("CHASSISNOLZZ1…") dan
# kadang memisah jadi dua kotak ("CHASSISNO" lalu "LZZ1…") — dua-duanya ditangani.
_LABEL_RANGKA = ("CHASSISNO", "CHASSIS", "CHASIS", "VINNO", "FRAMENO",
                 "NORANGKA", "NOMORRANGKA", "RANGKA", "VIN")


def _kandidat_label(baris: list[str]) -> list[str]:
    """Kandidat yang BERLABEL — jauh lebih tepercaya daripada potongan acak.

    Penting di foto nameplate: di sana ada MODEL, GCW, MADE YEAR, dan sederet
    angka lain, sehingga tanpa label kandidat bisa berjumlah ratusan."""
    out: list[str] = []
    for i, s in enumerate(baris):
        for lab in _LABEL_RANGKA:
            p = s.find(lab)
            if p < 0:
                continue
            ekor = s[p + len(lab):]
            if len(ekor) >= 12:                       # nilainya menempel di label
                out.append(ekor[:17])
            elif i + 1 < len(baris) and len(baris[i + 1]) >= 12:
                out.append(baris[i + 1][:17])         # nilainya di kotak berikutnya
            break
    return out


# ── Baca ulang JALUR di samping label ─────────────────────────────────
# Pada plat yang nomornya DIPAHAT/DIETSA (bukan dicetak), nilai VIN sering berupa
# abu-abu muda di atas pelat perak: label "VIN" terbaca jelas, tapi nilainya tak
# terdeteksi sama sekali — pemindaian biasa pulang tangan kosong (foto plat Wuyue
# pemilik, 2026-08-11: 22 kotak terbaca, tak satu pun nomor rangkanya).
#
# Jalan keluarnya memakai dua sifat yang kebetulan berpasangan rapi:
#   • letak label sudah diketahui → jalurnya bisa dipotong tepat setinggi label;
#   • RapidOCR MELEWATI tahap deteksi bila lebar/tinggi gambar > 8 (config
#     `width_height_ratio`) → potongan jalur langsung masuk ke pengenal sebagai
#     SATU baris. Jadi kegagalan deteksi itu dilangkahi, bukan dilawan.
# Ditambah blackhat (morfologi) yang mengangkat huruf gelap dari latar terang,
# jalur tadi terbaca 'LZZXMVWL9PS0004B4' — meleset satu huruf, dan justru itulah
# yang sejak awal ditangani lapisan snap-ke-armada.
_JALUR_MAKS_LABEL = 2        # cukup 2 label per varian (sisanya = plat lain)
_JALUR_MAKS_VARIAN = 2       # cukup 2 varian per orientasi yang dibaca-ulang
_JALUR_MAKS_TERBALIK = 2     # jatah percobaan jalur versi terbalik 180°
_JALUR_ANGGARAN = 5.0        # detik per orientasi untuk seluruh baca-ulang jalur
_JALUR_KS = 25               # jendela blackhat (px) — selebar tinggi huruf plat


def _tepi_tinta(g) -> tuple[int, int] | None:
    """Kolom awal–akhir kelompok tinta TERPANJANG pada satu jalur.

    Jalur mentah membentang sampai tepi foto, jadi ekornya berisi baut, bayangan,
    dan kolom sebelah. Memotongnya penting: pada foto uji, jalur yang dipotong
    ketat terbaca meleset 1 huruf sementara jalur utuh meleset 4."""
    import cv2
    import numpy as np
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (_JALUR_KS, _JALUR_KS))
    bh = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, k)
    _, bw = cv2.threshold(bh, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    idx = np.flatnonzero((bw > 0).sum(axis=0) >= max(2, int(g.shape[0] * 0.10)))
    if idx.size == 0:
        return None
    jeda = max(1, int(g.shape[0] * 1.2))     # > 1 tinggi huruf = ganti kolom
    grup: list[tuple[int, int]] = []
    mulai = akhir = int(idx[0])
    for i in idx[1:]:
        if int(i) - akhir <= jeda:
            akhir = int(i)
        else:
            grup.append((mulai, akhir))
            mulai = akhir = int(i)
    grup.append((mulai, akhir))
    return max(grup, key=lambda g2: g2[1] - g2[0])


def _mirip_nomor(teks: list[str]) -> bool:
    """Ada bacaan yang setidaknya BERBENTUK nomor rangka (panjang + ekor angka)?
    Dipakai untuk memutuskan apakah jalur perlu dicoba ulang terbalik."""
    return any(len(bersih(t)) >= 12 and _cukup_angka(bersih(t)) for t in teks)


def _baca_jalur(g, ringkas: bool = False) -> list[str]:
    """Satu potongan jalur → beberapa bacaan (polos & blackhat, 1× & 2×).

    `ringkas=True` hanya menjalankan blackhat 1× — varian yang paling sering
    menang — dipakai untuk percobaan ulang yang harus murah."""
    import cv2
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (_JALUR_KS, _JALUR_KS))
    bh = 255 - cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, k)
    out: list[str] = []
    for v in ((bh,) if ringkas else (g, bh)):
        for s in ((1,) if ringkas else (1, 2)):
            x = v if s == 1 else cv2.resize(v, None, fx=s, fy=s,
                                            interpolation=cv2.INTER_CUBIC)
            try:
                out.extend(k2[4] for k2 in _kotak_ocr(cv2.cvtColor(x, cv2.COLOR_GRAY2BGR)))
            except Exception as e:
                logger.warning("vin_ocr: jalur label gagal dibaca: %s", e)
                return out
    return out


def _jalur_label(bgr, kotak) -> list[str]:
    """Bacaan ulang jalur di SAMPING tiap label rangka yang nilainya tak terbaca.

    Dua arah ditangani sekaligus, dan keduanya nyata di lapangan:

    • kotak label MENDATAR (foto tegak) → nilainya di kanan; sisi KIRI ikut
      dicoba karena foto yang terbalik 180° tetap terbaca isinya (RapidOCR
      memutar tiap kotak sendiri lewat model cls) sementara tata letaknya
      mencerminkan;
    • kotak label TEGAK (foto plat diambil sambil berdiri di samping unit, jadi
      rebah 90°) → nilainya di ATAS/BAWAH label, bukan di sampingnya. Kolomnya
      diputar dulu jadi jalur mendatar. Ini lebih murah daripada memutar SELURUH
      foto: teks tiap kotak sudah terbaca benar (RapidOCR memutar kotak yang
      tinggi > lebar), yang belum diketahui hanyalah tata letaknya."""
    import cv2
    H, W = bgr.shape[:2]
    g_full = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hasil: list[str] = []
    dipakai = 0
    terbalik_sisa = _JALUR_MAKS_TERBALIK
    for (x0, y0, x1, y1, teks) in kotak:
        if dipakai >= _JALUR_MAKS_LABEL:
            break
        c = bersih(teks)
        lab = next((l for l in _LABEL_RANGKA if l in c), None)
        if not lab:
            continue
        if len(c[c.find(lab) + len(lab):]) >= 12:
            continue                              # nilainya sudah menempel di label
        mendatar = (x1 - x0) >= (y1 - y0)
        t = int(y1 - y0 if mendatar else x1 - x0)  # tinggi huruf pada jalurnya
        if t < 8:
            continue
        dipakai += 1
        pad, sela = max(2, int(t * 0.08)), int(t * 0.15)
        if mendatar:
            a, b = max(0, int(y0 - pad)), min(H, int(y1 + pad))
            sisi = ((min(W, int(x1 + sela)), W), (0, max(0, int(x0 - sela))))
        else:
            a, b = max(0, int(x0 - pad)), min(W, int(x1 + pad))
            sisi = ((min(H, int(y1 + sela)), H), (0, max(0, int(y0 - sela))))
        for p, q in sisi:
            if q - p < t * 3:                     # terlalu sempit utk 17 karakter
                continue
            g = (g_full[a:b, p:q] if mendatar else
                 cv2.rotate(g_full[p:q, a:b], cv2.ROTATE_90_COUNTERCLOCKWISE))
            tepi = _tepi_tinta(g)
            if not tepi:
                continue
            g = g[:, max(0, tepi[0] - 4):min(g.shape[1], tepi[1] + 5)]
            if g.shape[1] < t * 3:
                continue
            baca = _baca_jalur(g)
            # Foto terbalik: model cls RapidOCR yang biasanya membalik teks sendiri
            # ragu pada jalur beretsa (ambangnya 0,9), jadi jalurnya dibalik manual.
            # Berjatah & ringkas — versi "selalu balik, semua varian" membuat satu
            # foto buram melar dari 17 jadi 31 detik demi kasus yang tak tertolong.
            if terbalik_sisa and not _mirip_nomor(baca):
                terbalik_sisa -= 1
                # Hanya bacaan yang BERBENTUK nomor yang diambil. Percobaan ini
                # dijalankan justru saat jalurnya sulit, jadi sebagian besar
                # hasilnya sampah; memasukkannya sebagai kandidat (berbobot 3,
                # lagi) pernah menendang nomor rangka yang benar keluar dari 16
                # besar — satu foto gelap yang tadinya terbaca jadi gagal.
                baca.extend(t for t in _baca_jalur(cv2.rotate(g, cv2.ROTATE_180),
                                                   ringkas=True) if _mirip_nomor([t]))
            hasil.extend(baca)
    return hasil


def _cukup_angka(c: str) -> bool:
    """Ekor nomor rangka = 2 huruf + 6 ANGKA (100% armada) → saringan murah yang
    membuang potongan teks biasa ('CHINANATIONALHEAV' → ekor 'ALHEAV') sebelum
    dibandingkan satu-satu.

    ⚠️ Yang dihitung ANGKA maupun HURUF YANG MIRIP ANGKA. Menghitung angka saja
    terlalu galak untuk plat beretsa: '…PS000ZBA' (tiga angka ekor terbaca huruf)
    dibuang di sini, padahal jaraknya ke unit yang benar cuma 1,7 — di bawah
    ambang snap. Bentuk ekor yang sesungguhnya tetap diperiksa `bentuk_vin_wajar`,
    saringan ini hanya memilih siapa yang layak diadu."""
    return sum(ch in _MIRIP_ANGKA for ch in c[-6:]) >= 4


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


# Indeks armada untuk MEMPERSEMPIT pembanding. Tanpa ini, foto NAMEPLATE (penuh
# teks: MODEL, GCW, MADE YEAR…) menghasilkan 137 kandidat × 1.335 VIN = 183 ribu
# perbandingan Levenshtein → 21 DETIK hanya untuk mencocokkan (diukur 2026-08-11).
#
# Kuncinya: 6 char terakhir tiap VIN armada SELALU angka. Kita indeks setiap
# PASANGAN angka bersebelahan di 6 angka itu. Dua kesalahan baca pun pasti masih
# menyisakan satu pasangan utuh (2 kesalahan memecah 6 posisi jadi maksimal 3
# penggal berisi 4 angka bersih — mustahil semuanya tunggal), jadi saringan ini
# tidak membuang kandidat yang sebetulnya masih terjangkau ambang snap.
_IDX: dict = {"kunci": None, "peta": {}}


def _indeks(rows: list[tuple[str, dict]]) -> dict[str, list[int]]:
    kunci = (len(rows), rows[0][0] if rows else "", rows[-1][0] if rows else "")
    if _IDX["kunci"] == kunci:
        return _IDX["peta"]
    peta: dict[str, list[int]] = {}
    for i, (vin, _info) in enumerate(rows):
        ekor = vin[-6:]
        for j in range(len(ekor) - 1):
            peta.setdefault(ekor[j:j + 2], []).append(i)
    _IDX["kunci"], _IDX["peta"] = kunci, peta
    return peta


def _baris_kandidat(c: str, rows, peta) -> list[int]:
    """Nomor baris armada yang layak dibandingkan dengan kandidat `c`."""
    ekor = c[-6:]
    kena: set[int] = set()
    for j in range(len(ekor) - 1):
        kena.update(peta.get(ekor[j:j + 2], ()))
    return sorted(kena) if kena else []


def _urut_kandidat(kand: dict[str, int]) -> list[str]:
    """Kandidat yang layak diadu, terpenting dulu.

    ⚠️ BENTUK dulu, baru jumlah kemunculan. Foto plat menghasilkan ratusan
    potongan (462 pada plat CNHTC dua panel), dan potongan sampah gampang muncul
    dua kali juga — 'LZ5BYV96718MADB1N' (sambungan antar-kotak) mengungguli nomor
    rangka yang terbaca UTUH dan benar, lalu menendangnya keluar dari 16 besar
    sehingga unitnya tak pernah dikenali. Yang berbentuk VIN sah didahulukan;
    jumlah kemunculan tinggal penentu seri."""
    layak = [c for c in kand if len(c) >= 8 and _cukup_angka(c)]
    layak.sort(key=lambda c: (0 if _bentuk_kasar(c) else 1, -kand[c], -len(c), c))
    return layak[:_MAKS_KANDIDAT]


def _bentuk_kasar(c: str) -> bool:
    """Bentuk VIN untuk PERINGKAT saja — sengaja tanpa syarat awalan 'L'.

    Yang diadu ke armada boleh cacat justru pada huruf pertamanya: di foto gelap
    'LZZ5…' terbaca '1ZZ5…', dan memakai `bentuk_vin_wajar` di sini menendangnya
    keluar dari 16 besar sehingga unitnya tak pernah dikenali. Syarat 'L' tetap
    berlaku di tempat yang benar — saat menerima nomor TANPA konfirmasi armada."""
    return len(c) == 17 and bool(_EKOR_RE.search(c))


def _snap(kand: dict[str, int], rows: list[tuple[str, dict]]) -> dict | None:
    """Kandidat OCR → unit armada terdekat. None bila tak ada yang cukup dekat."""
    if not kand or not rows:
        return None
    peta = _indeks(rows)
    terbaik: tuple[float, float, str, str] | None = None   # (jarak, kedua, vin, kandidat)
    for c in _urut_kandidat(kand):
        # Potongan pendek (frame 8 char) membawa bukti jauh lebih sedikit daripada
        # VIN penuh — ekor unit satu pengiriman cuma beda 1 digit. Jadi untuk itu
        # ambangnya diperketat: praktis harus PERSIS (atau satu huruf sekelas).
        batas = _SNAP_BATAS if len(c) >= 12 else 1.0
        best, best_vin, kedua = 99.0, "", 99.0
        for i in _baris_kandidat(c, rows, peta):
            vin = rows[i][0]
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


def _perbaiki_bentuk(c: str) -> list[str]:
    """Satu ANGKA ekor yang terbaca sebagai huruf → coba sepuluh angka, terima
    hanya bila check digit menyisakan TEPAT SATU jawaban.

    Kasus nyata: jalur VIN plat Wuyue terbaca '…PS0004B4' — huruf B berdiri di
    posisi yang standar VIN wajibkan berupa angka, jadi kesalahannya PASTI di
    situ; check digit lalu menunjuk satu-satunya angka yang mungkin ('3').
    Peluang teks acak lolos 1/11 per posisi, karena itu pemanggil hanya
    memakainya untuk kandidat yang BERLABEL (lihat `_tanpa_armada`)."""
    if len(c) != 17:
        return []
    salah = [i for i in range(11, 17) if not c[i].isdigit()]
    if len(salah) != 1:                      # dua kesalahan = terlalu banyak tebakan
        return []
    i = salah[0]
    return [k for k in (c[:i] + d + c[i + 1:] for d in "0123456789")
            if bentuk_vin_wajar(k) and cd_cocok(k)]


def _cd_pasti(kand: dict[str, int], berlabel=()) -> dict | None:
    """Kandidat yang bentuk DAN check digit-nya sah — bukti kuat berdiri sendiri.

    Dipisah dari `_tanpa_armada` supaya bisa dipakai sebagai syarat BERHENTI di
    tengah pemindaian: begitu satu bacaan lolos check digit, varian & orientasi
    berikutnya tak perlu dijalankan lagi. Yang BERLABEL didahulukan — check digit
    bisa dilewati teks acak 1 kali dari 11, label tidak."""
    urut = sorted((c for c in kand if len(c) == 17),
                  key=lambda c: (0 if c in berlabel else 1, -kand[c], c))
    for c in urut:
        c2 = _perbaiki_ejaan(c)
        if bentuk_vin_wajar(c2) and cd_cocok(c2):
            return {"rangka": c2, "keyakinan": "tinggi", "unit": None}
    return None


def _tanpa_armada(kand: dict[str, int], berlabel=()) -> dict | None:
    """Unit di LUAR populasi: bersandar pada bentuk VIN + check digit.

    ⚠️ Urutannya SENGAJA berlapis, bukan "kandidat terbanyak menang". Foto plat
    memuat belasan potongan yang kebetulan berbentuk VIN — 'MANUFACTUREDATE 2024'
    terbaca 'FACTUREDATB900020', yang ekornya pun 2 huruf + 6 angka. Versi
    sebelumnya pulang di kandidat pertama yang BENTUKNYA sah, jadi satu potongan
    sampah yang terbaca dua kali sudah cukup untuk mengalahkan nomor rangka yang
    check digit-nya benar. Sekarang bukti terkuat dulu, jumlah kemunculan hanya
    penentu seri."""
    pasti = _cd_pasti(kand, berlabel)
    if pasti:
        return pasti
    vin17 = sorted((c for c in kand if len(c) == 17), key=lambda c: (-kand[c], c))
    for c in vin17:                          # satu huruf sekelas ditukar → cd cocok
        c2 = _perbaiki_ejaan(c)
        if not bentuk_vin_wajar(c2):
            continue
        perbaikan = _perbaiki_cd(c2)
        if len(perbaikan) == 1:
            return {"rangka": perbaikan[0], "keyakinan": "tinggi", "unit": None,
                    "alternatif": [c2]}
    for c in vin17:                          # ekor rusak, tapi BERLABEL & tunggal
        if c not in berlabel:
            continue
        perbaikan = _perbaiki_bentuk(_perbaiki_ejaan(c))
        if len(perbaikan) == 1:
            return {"rangka": perbaikan[0], "keyakinan": "tinggi", "unit": None,
                    "alternatif": [_perbaiki_ejaan(c)]}
    for c in vin17:                          # bentuk sah, check digit tidak
        c2 = _perbaiki_ejaan(c)
        if not bentuk_vin_wajar(c2):
            continue
        return {"rangka": c2, "keyakinan": "rendah", "unit": None,
                "alternatif": _perbaiki_cd(c2)[:3]}
    # Hanya frame 8 char yang terlihat (banyak unit dipahat frame-nya saja).
    # ⚠️ WAJIB berlabel: bentuk "2 huruf + 6 angka" terlalu umum di plat pabrik —
    # kode mesin 'MC13.48-50' terbaca 'MC134850' dan lolos begitu saja, lalu
    # disodorkan sebagai nomor rangka. Di luar armada, label adalah satu-satunya
    # yang membedakan angka nomor rangka dari angka lain di plat yang sama.
    frame = [c for c in kand
             if c in berlabel and _FRAME_RE.match(_perbaiki_ejaan(c))]
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


def _orientasi(bgr):
    """(tahap, gambar, jumlah varian) — MALAS: gambar miring baru diputar bila
    orientasi tegak pulang tangan kosong, jadi foto normal tak ikut membayar.

    Plat sering dipotret sambil berdiri di samping unit, jadi fotonya rebah 90°.
    Cukup satu putaran (searah jarum jam): bila aslinya berlawanan arah, hasilnya
    terbalik 180° — dan itu sudah dibereskan sendiri oleh model cls RapidOCR yang
    memutar tiap kotak teks. Yang tidak ikut terbalik adalah TATA LETAKNYA, maka
    `_jalur_label` membaca kedua sisi label."""
    import cv2
    yield "tegak", bgr, 4
    yield "miring", cv2.rotate(bgr, cv2.ROTATE_90_CLOCKWISE), 2


def _pindai(im0, rows, kand: dict[str, int], berlabel: set[str],
            terbaca: list[str], *, tahap: str, maks: int) -> dict | None:
    """Satu orientasi: varian pra-proses bergantian sampai ada jawaban yang layak.

    Berhenti begitu bacaan mendarat di satu unit armada ATAU lolos check digit —
    dua-duanya bukti yang berdiri sendiri, jadi varian sisanya cuma buang waktu."""
    mulai = time.monotonic()
    kotak = 0
    jalur_dipakai = 0
    waktu_jalur = 0.0            # baca-ulang jalur: anggaran sendiri, lihat di bawah
    for urut, (nama, im) in enumerate(_varian(im0)):
        if urut >= maks:
            break
        dipakai = time.monotonic() - mulai - waktu_jalur
        if kand and dipakai > _BATAS_DETIK:
            logger.info("vin_ocr: anggaran waktu habis, berhenti di %s/%s", tahap, nama)
            break
        # Perbesar 2× menolong pahatan tipis pada foto yang RENGGANG (satu baris
        # nomor), tapi pada NAMEPLATE penuh teks ia cuma melipatgandakan kerja
        # pengenalan: 18,9 dtk vs 4,7 dtk untuk hasil yang sama (diukur).
        #
        # Ia juga varian TERMAHAL, jadi tak dijalankan lagi kalau waktu sudah
        # banyak terpakai: pada foto buram (yang kotaknya sedikit, sehingga aturan
        # padat-teks tak menolongnya) ia sendirian membuat satu pembacaan melar
        # jadi 44 detik — di server 1 vCPU itu berarti kena batas proses anak dan
        # pulang "gagal" setelah menahan user satu menit.
        if nama.endswith("2x") and (kotak >= 6 or dipakai > _BATAS_2X):
            logger.info("vin_ocr: varian 2x dilewati (%d kotak, %.1f dtk terpakai)",
                        kotak, dipakai)
            continue
        try:
            kot = _kotak_ocr(im)
        except Exception as e:               # satu varian gagal ≠ seluruhnya gagal
            logger.warning("vin_ocr: varian %s gagal: %s", nama, e)
            continue
        kotak = max(kotak, len(kot))
        baris = [k[4] for k in kot]
        for b in baris:
            if b and b not in terbaca:
                terbaca.append(b)
        _kumpulkan(baris, kand, berlabel)
        pilihan = _snap(kand, rows) or _cd_pasti(kand, berlabel)
        if pilihan:
            return pilihan
        # Upaya terakhir varian ini: nilai di samping label dibaca ULANG sebagai
        # satu baris (lihat `_jalur_label`). Hanya dijalankan saat sudah buntu —
        # bacaan jalur bisa membawa potongan sampah, dan begitu jawaban yang sah
        # sudah ada, menambah kandidat cuma memperbesar peluang salah pilih.
        # ⚠️ Baca-ulang jalur punya anggaran SENDIRI dan waktunya tidak dihitung
        # ke tenggat varian. Sempat digabung, dan akibatnya fatal sekaligus halus:
        # strip memakan jatah yang dibutuhkan varian ke-3, padahal di plat dua
        # panel justru varian ke-3 itulah yang menemukan nomornya — hasilnya
        # berubah-ubah mengikuti beban mesin (foto yang sama: kadang terbaca,
        # kadang gagal).
        if (jalur_dipakai < _JALUR_MAKS_VARIAN and not nama.endswith("2x")
                and waktu_jalur < _JALUR_ANGGARAN):
            t_jalur = time.monotonic()
            teks = _jalur_label(im0, kot)
            waktu_jalur += time.monotonic() - t_jalur
            if teks:
                jalur_dipakai += 1
                for t in teks:
                    if t and t not in terbaca:
                        terbaca.append(t)
                _kumpulkan(teks, kand, berlabel, bobot=3, tandai=True)
                pilihan = _snap(kand, rows) or _cd_pasti(kand, berlabel)
                if pilihan:
                    return pilihan
    return None


def _kumpulkan(baris: list[str], kand: dict[str, int], berlabel: set[str],
               bobot: int = 1, tandai: bool = False) -> None:
    """Teks hasil baca → kandidat nomor rangka (+ tandai yang BERLABEL).

    `tandai=True` untuk bacaan JALUR: labelnya sudah dipotong keluar dari gambar,
    jadi tak ada lagi kata "VIN" di teksnya — padahal justru bacaan itulah yang
    paling terikat ke label."""
    bersih_baris = [bersih(b) for b in baris]
    # Nomor rangka bisa terpecah jadi beberapa kotak (bintang di kiri-kanan, atau
    # pahatan yang renggang) → coba tiap kotak DAN gabungan semuanya.
    #
    # Tiap kandidat langsung dibetulkan ejaannya (I/O/Q → 1/0/0; huruf itu TIDAK
    # pernah ada di VIN). Bukan sekadar kosmetik: plat beretsa terbaca
    # '…PSOOOAB4A', dan selama O belum jadi 0 potongan itu gugur di `_cukup_angka`
    # (angka di ekornya cuma satu) — jadi tak pernah sempat diadu ke armada,
    # padahal jaraknya cuma 0,7. ⚠️ Pembetulan dilakukan pada KANDIDAT, bukan pada
    # barisnya, supaya kata "VIN"/"CHASSIS" tetap utuh untuk `_kandidat_label`.
    for s in bersih_baris + ["".join(bersih_baris)]:
        for c in map(_perbaiki_ejaan, _kandidat(s)):
            kand[c] = kand.get(c, 0) + bobot
            if tandai:
                berlabel.add(c)
    # Yang berdiri di belakang label "CHASSIS NO." diberi bobot lebih: di foto
    # NAMEPLATE, potongan acak berjumlah ratusan dan beberapa di antaranya
    # (MODEL, GCW, MADE YEAR) juga berbentuk huruf+angka.
    for c in map(_perbaiki_ejaan, _kandidat_label(bersih_baris)):
        kand[c] = kand.get(c, 0) + 3
        berlabel.add(c)


def _baca_lokal(data: bytes, rows: list[tuple[str, dict]]) -> dict:
    """Pipeline sesungguhnya (dipakai proses anak, atau langsung bila isolasi mati)."""
    t0 = time.monotonic()
    hasil: dict = {"ok": False, "rangka": "", "frame": "", "keyakinan": "gagal",
                   "unit": None, "alternatif": [], "teks_terbaca": [],
                   "pesan": "", "detik": 0.0}
    bgr = _decode(data)                      # ValueError → 400 di router
    kand: dict[str, int] = {}
    berlabel: set[str] = set()
    terbaca: list[str] = []
    pilihan: dict | None = None

    for urut, (tahap, im0, maks) in enumerate(_orientasi(bgr)):
        # Orientasi kedua hanya bila yang pertama tak makan waktu terlalu lama.
        if urut and (time.monotonic() - t0) > _BATAS_MULAI_MIRING:
            logger.info("vin_ocr: waktu tersisa tak cukup untuk orientasi %s", tahap)
            break
        pilihan = _pindai(im0, rows, kand, berlabel, terbaca,
                          tahap=tahap, maks=maks)
        if pilihan:
            break
        logger.info("vin_ocr: orientasi %s belum menghasilkan nomor rangka", tahap)

    if not pilihan:
        pilihan = _snap(kand, rows) or _tanpa_armada(kand, berlabel)

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
#
# Batas 60 dtk = jaring pengaman, bukan target: foto normal selesai 0,4–3 dtk.
# Yang paling lama adalah foto yang harus menempuh SEMUA jalan (dua orientasi ×
# varian × baca-ulang jalur) — 18 dtk sebelum orientasi kedua boleh mulai, plus
# anggarannya sendiri. Angka ini harus tetap di atas penjumlahan itu, kalau tidak
# foto sulit dibunuh di tengah jalan dan hasilnya jadi "gagal" yang menyesatkan.
_BATAS_ANAK = 60.0
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
