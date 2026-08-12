"""Service: baca KODE KESALAHAN (SPN/FMI) dari FOTO PANEL INSTRUMEN — OCR lokal.

Sopir/mekanik di lapangan memotret layar panel yang sedang menampilkan daftar
kesalahan (DM1 = sedang AKTIF, DM2 = tersimpan), klien mengunggahnya ke
`POST /api/ai/ocr-foto`, lalu MENGIRIM hasil bacanya sebagai pesan chat biasa
("Kode kesalahan dari panel: SPN 4203 FMI 12 …"). Model bahasa TIDAK pernah
melihat gambar — yang sampai ke asisten hanya TEKS, persis pola [[vin_ocr]].
Penjelasan kodenya tetap dikerjakan tool yang sudah ada (`cari_kode_kesalahan`
/`diagnosa`) beserta seluruh pagar kejujurannya; modul ini HANYA membaca angka.

Yang dibaca dari layar:
  • tabel berkolom "No. | SPN | FMI | DM1" — bentuk baku panel Sinotruk/SITRAK;
  • baris menyatu ("1 4203 12") maupun tulisan sebaris ("SPN4203/FMI12");
  • label ECU sumber di bawah tabel ("Engine", "ABS", "Gearbox", …) — dipakai
    sebagai PENENTU saat satu pasangan SPN/FMI terdaftar di beberapa ECU
    (mis. SPN 4203 FMI 12 ada di EMS mesin, sedang FMI 8 milik ZF_GearBox).

⚠️ Sama seperti nomor rangka, OCR-nya sendiri BUKAN penentu kelayakan: angka di
layar LCD berlatar gelap gampang tertukar (0↔O, 1↔I, 2↔Z, 5↔S, 8↔B). Lapisan
kedua di sini adalah STORE DTC (`dtc_codes`, 13.224 baris / 7.931 pasangan
SPN+FMI):
  1. bentuk J1939 diperiksa dulu — FMI 0..31, SPN wajar 1..524287 (19 bit);
  2. pasangan yang TERDAFTAR di store → keyakinan 'pasti' (bukti berdiri
     sendiri: hanya 6,6% dari kombinasi SPN×FMI yang mungkin itu benar-benar
     ada, jadi kecocokan bukan kebetulan);
  3. SPN dikenal tapi FMI-nya tidak → 'rendah', dan FMI yang memang terdaftar
     ikut disebut supaya user bisa membandingkan dengan layar;
  4. SPN sama sekali tak dikenal → 'rendah'. Di situ satu substitusi angka yang
     memang sering tertukar OCR dicoba (`_usul_koreksi`), TAPI hanya ditawarkan
     sebagai alternatif bila HANYA ADA SATU yang mendarat di store — tak pernah
     dipakai diam-diam menggantikan bacaan. Menukar kode kesalahan tanpa suara
     = mekanik membongkar komponen yang salah.

⚠️ Store TIDAK bisa menangkap satu kelas kesalahan: bacaan salah yang KEBETULAN
juga kode sah (foto silau → '12' terbaca '2', dan SPN 4203 FMI 2 memang ada).
Penjaganya cuma satu: foto panel dibaca DUA varian pra-proses dan angkanya harus
SEPAKAT sebelum boleh 'pasti' — kesalahan OCR antar-varian tidak berkorelasi.
Lihat `_sepakat`.

⛔ Jangan melonggarkan `_TOL_KOLOM`: pencocokan kolom itulah yang membuang angka
speedometer/odometer yang kebetulan ikut terbaca di foto yang sama (pada foto
uji pemilik, jarum kecepatan menyumbang '20' dan '40' — dua-duanya angka wajar
untuk sebuah FMI, dan hanya letak-nya yang membedakannya dari data tabel).

⚠️ Yang TIDAK dibaca modul ini: lampu indikator (ikon mesin kuning, rem tangan
merah, dst). Itu klasifikasi gambar, bukan OCR — di luar cakupan, dan menebaknya
dari warna/bentuk akan salah lebih sering daripada benar.

⛔ UKUR DI DALAM CONTAINER, JANGAN DI LAPTOP. OCR di image produksi membaca
BEDA dari laptop pada foto yang sama (sudah tercatat di `vin_ocr`, dan terbukti
lagi di sini): di container kepala 'No.' & 'SPN' menyatu jadi satu kotak. Dua
bug yang paling berbahaya di modul ini — nomor urut baris terbaca sebagai SPN,
dan '12' terbaca '2' — TIDAK muncul sama sekali di laptop (9/10 benar di sana),
dan baru ketahuan saat dijalankan di produksi. Bangku ujinya ada di scratchpad
sesi; salin ke container lalu `docker exec … python3 bench.py`.

Ukur DI CONTAINER pada DUA foto lapangan pemilik (2026-08-12, panel SITRAK —
satu bertabel 1 baris, satu 2 baris & layarnya berdebu/memantul) × 10 kondisi
(asli, 600 px, blur, gelap, silau, miring 8°, JPEG q25, diputar 90°/180°/270°):
masing-masing **8 terkirim otomatis & semuanya benar, 0 terkirim salah**, 1
minta konfirmasi user, 1 gagal. 0,8–7,4 detik. Kodenya mendarat tepat di store
(SPN 4203 FMI 12 → P0335 · EMS · "Tidak terdeteksi crankshaft sinyal").
Yang dihitung BUKAN "berapa yang terbaca benar" melainkan "berapa yang TERKIRIM
SENDIRI padahal salah" — itu satu-satunya angka yang merugikan user; dan sejak
keluhan 2026-08-12, "berapa BARIS yang hilang tanpa suara" ikut dihitung sama
beratnya (lihat `_ulang_baris` & `tak_lengkap`).

⚠️ Satu yang gagal: foto rebah 270° TANPA data EXIF. Di situ varian pertama
tak menemukan kata 'SPN' sama sekali sehingga modul mengalah lebih awal, dan
itu memang SENGAJA: modul ini berdiri di depan pembacaan nomor rangka pada
endpoint yang sama, jadi setiap varian tambahan yang dipakai untuk "berjaga-
jaga" dibayar oleh SEMUA foto nomor rangka (diukur: 2 dari 4 foto rangka uji
ikut membayar satu pembacaan penuh, 2–4 detik, demi satu kasus rebah). Foto
itu pulang "gagal" dengan jujur — tak pernah menebak kode. Kamera HP normal
menyertakan EXIF dan `_decode` sudah menegakkannya sebelum apa pun dimulai.

RAM: memakai mesin OCR yang SAMA dengan `vin_ocr` (RapidOCR ONNX) dan ikut
aturan proses anaknya — lihat catatan isolasi di modul itu; container backend
pernah terukur 94,6% penuh, jadi tak ada model kedua yang dimuat di sini.
"""
from __future__ import annotations

import logging
import re
import time

from . import dtc_codes, vin_ocr

logger = logging.getLogger(__name__)

MAX_BYTES = vin_ocr.MAX_BYTES

# Batas J1939: SPN 19 bit, FMI 5 bit. Store memang memuat 34 baris ber-SPN di
# atas batas itu (penomoran khusus pabrik) — maka nilai yang TERDAFTAR tetap
# diterima, dan batas ini hanya menyaring kode yang TAK dikenal.
_SPN_MAKS = 524287
_FMI_MAKS = 31

# Toleransi jarak ke tengah kolom, dalam kelipatan jarak antar-kolom SPN↔FMI.
# ⛔ Jangan dinaikkan mendekati 1,0: kolom bersebelahan akan saling mencaplok,
# dan angka di luar tabel (speedometer) mulai ikut tertarik masuk.
_TOL_KOLOM = 0.55

# Huruf yang sering terbaca menggantikan angka pada layar LCD panel. Dipakai
# dua arah: membetulkan token yang jelas-jelas angka (`_angka`), dan mengusulkan
# koreksi saat kode tak dikenal (`_usul_koreksi`).
_HURUF_KE_ANGKA = {"O": "0", "Q": "0", "D": "0", "U": "0",
                   "I": "1", "L": "1", "J": "1",
                   "Z": "2", "A": "4", "S": "5", "G": "6", "T": "7", "B": "8"}
# Pasangan angka yang saling tertukar (dipakai `_usul_koreksi` — arah angka↔angka).
_ANGKA_MIRIP = {"0": "68", "1": "7", "3": "89", "5": "6", "6": "058",
                "7": "1", "8": "36", "9": "3", "2": "7", "4": "1"}

_MAKS_KODE = 8            # baris kesalahan terbanyak yang dilaporkan sekali baca

# Label ECU sumber di bawah tabel → potongan nama `unit` di store DTC yang
# dianggap satu keluarga. Dipakai HANYA untuk mengurutkan kandidat penjelasan,
# tak pernah untuk menolak kode.
_ECU_LABEL: tuple[tuple[tuple[str, ...], str, tuple[str, ...]], ...] = (
    (("ENGINE", "EMS", "ECM", "EDC", "MESIN"), "Engine",
     ("EMS", "BOSCH", "WCWISE", "ECM")),
    (("ABS", "EBS"), "ABS/EBS", ("ABS", "EBS", "KNORR", "WABCO")),
    (("GEARBOX", "TRANSMISSION", "AMT", "TCU", "ZF"), "Gearbox/AMT",
     ("AMT", "TCU", "GEARBOX", "ZF")),
    (("RETARDER",), "Retarder", ("RETARDER",)),
    (("SCR", "DCU", "NOX", "DPF"), "SCR", ("SCR", "DCU", "NOX")),
    (("ECAS", "SUSPENSION"), "ECAS", ("ECAS",)),
    (("BCM", "BODY", "CBCU"), "Body/BCM", ("BCM", "CBCU")),
)

# Kata di layar yang membuktikan ini memang panel kode kesalahan — bukan plat
# nomor rangka atau foto lain. Tanpa salah satu pun, modul MENGALAH dan
# pemanggil melanjutkan ke pembacaan nomor rangka (`baca_foto`).
_BUKTI = ("SPN", "FMI", "DM1", "DM2", "DTC")


def _bersih(s: str) -> str:
    """Huruf/angka KAPITAL saja — 'No.' → 'NO', 'SPN:' → 'SPN'."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _angka(teks: str) -> str | None:
    """Token OCR → deretan angka, membetulkan huruf yang sering tertukar.

    None bila token itu bukan angka. Ambang 'berapa banyak huruf boleh
    dibetulkan' sengaja ketat: tanpa itu 'SOS' menjadi '505' dan 'DIODE'
    menjadi angka enam digit yang sah-sah saja bentuknya."""
    t = _bersih(teks)
    if not t or len(t) > 6:
        return None
    out: list[str] = []
    ganti = 0
    for ch in t:
        if ch.isdigit():
            out.append(ch)
        elif ch in _HURUF_KE_ANGKA:
            out.append(_HURUF_KE_ANGKA[ch])
            ganti += 1
        else:
            return None
    if ganti > 2 or ganti * 2 > len(t):
        return None
    return "".join(out)


def _cx(k) -> float:
    return (k[0] + k[2]) / 2


def _cy(k) -> float:
    return (k[1] + k[3]) / 2


def ada_bukti(kotak) -> bool:
    """Apakah foto ini memang layar kode kesalahan (ada kata SPN/FMI/DM1)."""
    for k in kotak:
        c = _bersih(k[4])
        if any(b in c for b in _BUKTI):
            return True
    return False


def _kolom(kotak) -> dict | None:
    """Letak kolom SPN & FMI + garis bawah header + jenis pesan (DM1/DM2).

    Kepala tabel kadang terbaca sebagai kotak terpisah ('SPN', 'FMI') dan kadang
    menyatu ('No.SPNFMIDM1') — untuk yang menyatu, letak kolom ditaksir dari
    posisi HURUFnya di dalam kotak. Taksiran itu kasar, tapi cukup: yang
    dibutuhkan hanya 'kolom mana yang lebih dekat', bukan koordinat tepat."""
    kol: dict = {"spn": None, "fmi": None, "no": None, "bawah": 0.0, "jenis": ""}
    for x0, y0, x1, y1, t in kotak:
        c = _bersih(t)
        if not c:
            continue
        for lab in ("DM1", "DM2"):
            if lab in c and not kol["jenis"]:
                kol["jenis"] = lab
        # Kolom "No." ikut dicatat: isinya nomor URUT baris, dan angka itu wajib
        # dibuang. Di produksi pernah terbaca sebagai SPN — lihat `_baca_baris`.
        for lab, kunci in (("SPN", "spn"), ("FMI", "fmi"), ("NO", "no")):
            i = c.find(lab)
            if i < 0 or kol[kunci] is not None:
                continue
            kol[kunci] = x0 + (x1 - x0) * ((i + len(lab) / 2) / len(c))
            if kunci != "no":
                kol["bawah"] = max(kol["bawah"], y1)
    if kol["spn"] is None or kol["fmi"] is None:
        return None
    if abs(kol["fmi"] - kol["spn"]) < 1.0:      # dua label di kotak yang sama persis
        return None
    return kol


# Jeda vertikal (kelipatan tinggi baris) yang dianggap "tabel sudah habis".
# ⚠️ Tanpa batas ini, SELURUH isi foto di bawah tabel ikut dihitung baris: pada
# foto 2 baris pemilik, angka speedometer/voltmeter di bagian bawah panel ('32',
# '20', '16') berdiri cukup dekat ke kolom SPN sehingga tampak seperti baris data
# yang tak lengkap — hasilnya bacaan yang sudah benar ikut diturunkan jadi ragu,
# dan jatah baca-ulang habis untuk angka yang bukan kode.
_JEDA_TABEL = 3.0


def _baris(kotak, y_min: float) -> list[list]:
    """Kotak di BAWAH header dikelompokkan jadi baris (pita y yang berdekatan),
    berhenti begitu ada jeda kosong selebar beberapa baris."""
    ks = sorted((k for k in kotak if _cy(k) > y_min), key=_cy)
    out: list[list] = []
    for k in ks:
        tinggi = max(k[3] - k[1], 12.0)
        if out and abs(_cy(k) - _cy(out[-1][0])) <= tinggi * 0.7:
            out[-1].append(k)
        else:
            out.append([k])
    hasil: list[list] = []
    bawah = y_min
    for grup in out:
        tinggi = max(max(k[3] - k[1] for k in grup), 12.0)
        if min(k[1] for k in grup) - bawah > tinggi * _JEDA_TABEL:
            break
        hasil.append(grup)
        bawah = max(k[3] for k in grup)
    return hasil


def _pecah_menyatu(baris: list, kol: dict) -> tuple[str, str] | None:
    """Baris yang terbaca sebagai SATU kotak ('1 4203 12' / '4203 12').

    Terjadi bila angka-angka tabel berdiri rapat: OCR menganggapnya satu baris
    teks. Kolomnya tak bisa dipakai lagi, jadi urutan kiri→kanan yang dipakai —
    aman karena bentuk tabelnya baku (No, SPN, FMI)."""
    if len(baris) != 1:
        return None
    tok = [x for x in re.split(r"[^0-9A-Za-z]+", baris[0][4]) if x]
    angka = [_angka(x) for x in tok]
    if any(a is None for a in angka):
        return None
    if len(angka) == 3:                 # No. | SPN | FMI
        return angka[1], angka[2]
    if len(angka) == 2:                 # SPN | FMI (kolom No. tak terbaca)
        return angka[0], angka[1]
    return None


def _baca_baris(baris: list, kol: dict) -> tuple[str, str] | None:
    """Satu baris tabel → (spn, fmi): tiap kolom mengambil angka TERDEKAT.

    ⚠️ Regresi produksi 2026-08-12 — dulu di sini diambil angka PERTAMA yang
    masuk toleransi, dan itu salah. Di container, OCR menyatukan kepala 'No.'
    dengan 'SPN' jadi satu kotak, sehingga taksiran letak kolom SPN bergeser
    ±12 px ke kiri; nomor URUT baris ('1', di kolom No.) lalu ikut masuk
    toleransi dan — karena berdiri lebih kiri — terambil duluan sebagai SPN.
    Hasilnya 'SPN 1 FMI 12' disodorkan dengan keyakinan tinggi. Angka '4203'
    yang benar cuma berjarak 14 px dari kolomnya, jadi memilih yang TERDEKAT
    (bukan yang pertama) menyelesaikannya, dan kolom 'No.' kini dibuang tegas.

    Dua penjaga sekaligus, sebab tata letak panel lain belum tentu sama:
    angka yang lebih dekat ke kolom 'No.' daripada ke 'SPN' dibuang, dan satu
    kotak tak boleh dipakai untuk dua kolom."""
    tol = abs(kol["fmi"] - kol["spn"]) * _TOL_KOLOM
    kand: list[tuple[float, str, int]] = []
    for i, k in enumerate(baris):
        a = _angka(k[4])
        if a is None:
            continue
        cx = _cx(k)
        if kol.get("no") is not None and abs(cx - kol["no"]) < abs(cx - kol["spn"]):
            continue                       # itu nomor urut baris, bukan SPN
        kand.append((cx, a, i))

    def _ambil(pusat: float, dipakai: set[int]) -> tuple[str, int] | None:
        sisa = [(abs(cx - pusat), a, i) for cx, a, i in kand if i not in dipakai]
        if not sisa:
            return None
        jarak, a, i = min(sisa)
        return (a, i) if jarak <= tol else None

    dipakai: set[int] = set()
    hit_spn = _ambil(kol["spn"], dipakai)
    if hit_spn:
        dipakai.add(hit_spn[1])
    hit_fmi = _ambil(kol["fmi"], dipakai)
    if hit_spn and hit_fmi:
        return hit_spn[0], hit_fmi[0]
    return _pecah_menyatu(baris, kol)


def _separuh(baris: list, kol: dict) -> bool:
    """Baris ini TAMPAK baris data (ada angka berdiri di kolom SPN/FMI) tapi tak
    menghasilkan pasangan lengkap. Dibedakan dari baris kosong/derau supaya
    hanya yang benar-benar kehilangan angka yang dibaca ulang — dan supaya
    kehilangan itu bisa DILAPORKAN, bukan hilang tanpa suara."""
    tol = abs(kol["fmi"] - kol["spn"]) * _TOL_KOLOM
    for k in baris:
        if _angka(k[4]) is None:
            continue
        cx = _cx(k)
        if kol.get("no") is not None and abs(cx - kol["no"]) < abs(cx - kol["spn"]):
            continue
        if min(abs(cx - kol["spn"]), abs(cx - kol["fmi"])) <= tol:
            return True
    return False


# Rasio lebar/tinggi pita saat dibaca ulang. ⛔ JANGAN diturunkan ke bawah 8:
# di situlah RapidOCR berhenti memperlakukan potongan sebagai gambar biasa, dan
# justru itu yang membuat angka tunggal ikut terbaca. Terukur di container pada
# foto 2 baris pemilik: rasio 7,8 → hanya '3597'; rasio 8,7/9,0/9,3 → '3597' DAN
# '4'. Selisihnya setipis itu, jadi pita dilebarkan sampai 9,0 dengan sengaja.
_RASIO_ULANG = 9.0
_MAKS_ULANG = 3           # baris yang boleh dibaca ulang per pembacaan
# Tinggi pita saat dibaca ulang. Pengenal RapidOCR menormalkan tiap baris ke 48
# px, jadi mengirim pita beresolusi penuh hanya menambah kerja: terukur di
# container, pita 702×78 memakan 3,0 detik (pernah 8,6 detik saat mesin sibuk)
# sedangkan versi 432×48 hanya 1,2 detik dengan bacaan yang SAMA.
# ⛔ Jangan pakai nilai di antaranya tanpa mengukur ulang: pada 96 px angka '4'
# justru hilang lagi — perilakunya tidak menaik rapi mengikuti resolusi.
_TINGGI_ULANG = 48


def _ulang_baris(im, kol: dict, baris: list) -> tuple[str, str] | None:
    """Baris yang cuma terbaca separuh → potong PITA-nya lalu baca ulang lebar.

    ⚠️ Ini menambal kegagalan DETEKSI, bukan pengenalan: angka FMI satu digit
    ('4') pada layar panel sering tak terdeteksi sama sekali — di foto 2 baris
    pemilik ia hilang di KEDUA varian pra-proses, sehingga seluruh barisnya
    lenyap dan asisten hanya menerima 1 dari 2 kode. Yang dibaca ulang cukup
    pita setinggi barisnya; kotak hasilnya dikembalikan ke koordinat ASLI supaya
    pencocokan kolom yang sama bisa dipakai lagi tanpa aturan baru."""
    import cv2
    tinggi_im, lebar_im = im.shape[:2]
    y0 = max(0, int(min(b[1] for b in baris)) - 10)
    y1 = min(tinggi_im, int(max(b[3] for b in baris)) + 10)
    tinggi = y1 - y0
    if tinggi < 10:
        return None
    lebar = int(tinggi * _RASIO_ULANG)
    kiri = min(kol["spn"], kol.get("no") if kol.get("no") is not None else kol["spn"])
    pusat = (kiri + kol["fmi"]) / 2
    xa = int(pusat - lebar / 2)
    xb = xa + lebar
    if xa < 0:
        xa, xb = 0, min(lebar_im, lebar)
    if xb > lebar_im:
        xb, xa = lebar_im, max(0, lebar_im - lebar)
    if (xb - xa) < tinggi * 8:          # foto terlalu sempit → tak ada gunanya
        return None
    pita = cv2.cvtColor(im[y0:y1, xa:xb], cv2.COLOR_BGR2GRAY)
    skala = 1.0
    if pita.shape[0] > _TINGGI_ULANG:
        skala = _TINGGI_ULANG / pita.shape[0]
        pita = cv2.resize(pita, (max(1, int(pita.shape[1] * skala)), _TINGGI_ULANG),
                          interpolation=cv2.INTER_AREA)
    try:
        kotak = vin_ocr._kotak_ocr(cv2.cvtColor(pita, cv2.COLOR_GRAY2BGR))
    except Exception as e:
        logger.warning("dtc_ocr: baca ulang pita gagal: %s", e)
        return None
    if not kotak:
        return None
    asli = [(x0 / skala + xa, yy0 / skala + y0, x1 / skala + xa, yy1 / skala + y0, t)
            for x0, yy0, x1, yy1, t in kotak]
    return _baca_baris(asli, kol)


# "SPN 4203 FMI 12", "SPN4203/FMI12", "SPN:4203 FMI:12" — dipakai bila tabel
# tak berkolom (sebagian panel & alat scanner menuliskannya sebaris).
_SEBARIS = re.compile(r"SPN[^0-9]{0,3}(\d{1,6})[^0-9]{0,8}FMI[^0-9]{0,3}(\d{1,2})")


def _sebaris(kotak) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k in kotak:
        out.extend(_SEBARIS.findall(_bersih(k[4])))
    return out


def _ecu(kotak) -> tuple[str, tuple[str, ...]]:
    """Label ECU sumber di layar → (label rapi, potongan nama unit di store)."""
    for k in kotak:
        c = _bersih(k[4])
        if not c or len(c) > 20:
            continue
        for kata, label, unit in _ECU_LABEL:
            if any(w in c for w in kata):
                return label, unit
    return "", ()


def _sah(spn: int, fmi: int, dikenal: bool) -> bool:
    if not 0 <= fmi <= _FMI_MAKS:
        return False
    return spn > 0 and (dikenal or spn <= _SPN_MAKS)


def _skor_baris(r: dict, unit_hint: tuple[str, ...]) -> tuple:
    """Urutan kandidat penjelasan: ECU yang cocok dulu, lalu sumber RESMI.

    `sitrak` ditaruh paling belakang dengan sengaja — dataset komunitas yang
    hanya memuat ARTI kode (lihat catatan sumber di `dtc_codes`)."""
    unit = (r.get("unit") or "").upper()
    cocok = 0 if (unit_hint and any(u in unit for u in unit_hint)) else 1
    resmi = 1 if r.get("sumber") == "sitrak" else 0
    isi = 0 if (r.get("deskripsi") or "").strip() else 1
    return (cocok, resmi, isi)


def _usul_koreksi(spn: int, fmi: int) -> list[str]:
    """Kode tak dikenal → usul SATU substitusi angka yang mendarat di store.

    Hanya dikembalikan bila TUNGGAL. Dua usulan berarti kita tak tahu yang mana
    — dan menebak kode kesalahan lebih buruk daripada mengaku tak yakin."""
    usul: list[str] = []
    teks = str(spn)
    for i, ch in enumerate(teks):
        for lain in _ANGKA_MIRIP.get(ch, ""):
            if i == 0 and lain == "0":
                continue
            cand = int(teks[:i] + lain + teks[i + 1:])
            if cand == spn:
                continue
            baris = dtc_codes.search_spn_fmi(cand, fmi, limit=1)
            # ⚠️ `search_spn_fmi` jatuh ke baris ber-SPN sama saat FMI tak cocok —
            # untuk usulan, hanya pasangan PERSIS yang boleh dihitung.
            if baris and baris[0].get("fmi") == fmi:
                sebut = f"SPN {cand} FMI {fmi}"
                if sebut not in usul:
                    usul.append(sebut)
    return usul if len(usul) == 1 else []


def _periksa(spn_s: str, fmi_s: str, unit_hint: tuple[str, ...]) -> dict | None:
    """(teks spn, teks fmi) → entri kode yang sudah diadu ke store DTC."""
    try:
        spn, fmi = int(spn_s), int(fmi_s)
    except (TypeError, ValueError):
        return None
    cocok = dtc_codes.search_spn_fmi(spn, fmi, limit=12)
    eksak = [r for r in cocok if r.get("fmi") == fmi]
    if not _sah(spn, fmi, bool(cocok)):
        return None
    ent: dict = {"spn": spn, "fmi": fmi, "dikenal": bool(eksak),
                 "kode": "", "arti": "", "unit": "", "sumber": "",
                 "fmi_terdaftar": [], "alternatif": []}
    if eksak:
        r = sorted(eksak, key=lambda x: _skor_baris(x, unit_hint))[0]
        ent.update({"kode": r.get("kode") or "", "arti": r.get("deskripsi") or "",
                    "unit": r.get("unit") or "", "sumber": r.get("sumber") or ""})
    elif cocok:                       # SPN dikenal, FMI ini tidak
        ent["fmi_terdaftar"] = dtc_codes.fmi_tersedia(spn)[:12]
    else:
        ent["alternatif"] = _usul_koreksi(spn, fmi)
    return ent


def _keyakinan(kode: list[dict], berkolom: bool) -> str:
    """Keyakinan seluruh bacaan = yang TERLEMAH di antara kodenya.

    Satu kode meragukan sudah cukup untuk membuat user perlu melihat sendiri;
    menyajikan '2 dari 3 pasti' hanya membuat yang ketiga ikut dipercaya —
    padahal justru yang ketiga itulah yang paling mungkin salah baca.

    'pasti' juga menuntut tabel BERKOLOM: pada bentuk sebaris tak ada bukti
    tata letak yang menguatkan, hanya kecocokan angka.

    ⚠️ 'SPN terdaftar tapi FMI-nya tidak' sengaja TIDAK lagi cukup untuk
    dikirim otomatis (dulu 'tinggi'). Insiden produksi 2026-08-12: nomor urut
    baris terbaca sebagai SPN 1 — dan SPN 1 KEBETULAN ada di store dengan FMI
    lain — sehingga bacaan yang sepenuhnya salah naik kelas jadi 'tinggi' dan
    terkirim sendiri. Bukti separuh (SPN cocok, FMI tidak) memang lemah: yang
    boleh terkirim tanpa dilihat user hanya pasangan UTUH yang terdaftar."""
    if not kode:
        return "gagal"
    tingkat = min(3 if k["dikenal"] else (2 if k["fmi_terdaftar"] else 1)
                  for k in kode)
    if tingkat == 3:
        return "pasti" if berkolom else "tinggi"
    return "rendah"


def _kosong() -> dict:
    return {"ok": False, "jenis": "dtc", "kode": [], "jenis_pesan": "", "ecu": "",
            "keyakinan": "gagal", "tak_lengkap": False, "teks_terbaca": [],
            "pesan": "", "detik": 0.0}


def _dari_kotak(kotak, im=None) -> dict | None:
    """Kotak OCR → hasil kode kesalahan. None = foto ini bukan layar kesalahan.

    Dipisah dari pembacaan gambar supaya bisa diuji dengan `vin_ocr.kotak_datar`
    tanpa perlu gambar sungguhan (pola yang sama dipakai test `vin_ocr`). `im`
    opsional: bila diberikan, baris yang cuma terbaca separuh dibaca ULANG dari
    gambarnya (`_ulang_baris`) — tanpa itu perilakunya persis seperti dulu."""
    if not ada_bukti(kotak):
        return None
    hasil = _kosong()
    label, unit_hint = _ecu(kotak)
    hasil["ecu"] = label
    kol = _kolom(kotak)
    pasangan: list[tuple[str, str]] = []
    tak_lengkap = False
    if kol:
        hasil["jenis_pesan"] = kol["jenis"]
        ulang = 0
        for baris in _baris(kotak, kol["bawah"]):
            hit = _baca_baris(baris, kol)
            if not hit and _separuh(baris, kol):
                if im is not None and ulang < _MAKS_ULANG:
                    ulang += 1
                    hit = _ulang_baris(im, kol, baris)
                # ⚠️ Baris data yang tetap tak lengkap TIDAK boleh lenyap tanpa
                # suara: keluhan pemilik 2026-08-12 — layar memuat 2 kode, yang
                # sampai ke asisten cuma 1, dan tak ada apa pun yang menandakan
                # ada yang hilang. Lebih baik user diminta melihat sendiri.
                if not hit:
                    tak_lengkap = True
            if hit:
                pasangan.append(hit)
    if not pasangan:                     # tabel tak berkolom → coba bentuk sebaris
        pasangan = _sebaris(kotak)
        kol = None
    kode: list[dict] = []
    for spn_s, fmi_s in pasangan[:_MAKS_KODE]:
        ent = _periksa(spn_s, fmi_s, unit_hint)
        if ent and not any(e["spn"] == ent["spn"] and e["fmi"] == ent["fmi"]
                           for e in kode):
            kode.append(ent)
    hasil["kode"] = kode
    hasil["tak_lengkap"] = tak_lengkap
    hasil["keyakinan"] = _keyakinan(kode, bool(kol))
    if tak_lengkap and hasil["keyakinan"] != "gagal":
        hasil["keyakinan"] = "rendah"
    hasil["ok"] = bool(kode)
    return hasil


_URUT_YAKIN = {"gagal": 0, "rendah": 1, "tinggi": 2, "pasti": 3}
# Anggaran waktu untuk PEMULIHAN foto berputar. Tak berlaku untuk pembacaan
# biasa (yang sudah selesai di pembacaan pertama) — hanya membatasi seberapa
# jauh kita mengejar foto yang sudah terbukti layar kesalahan tapi belum terurai.
_BATAS_PUTAR = 12.0


def _pilih(a: dict | None, b: dict | None) -> dict | None:
    """Hasil yang lebih kuat menang: keyakinan dulu, lalu jumlah kode."""
    if not b or not b.get("ok"):
        return a
    if not a or not a.get("ok"):
        return b
    kunci = lambda h: (_URUT_YAKIN.get(h["keyakinan"], 0), len(h["kode"]))  # noqa: E731
    return b if kunci(b) > kunci(a) else a


def _cukup(h: dict | None) -> bool:
    return bool(h and h.get("ok") and h["keyakinan"] in ("pasti", "tinggi"))


def _daftar(h: dict) -> list[tuple[int, int]]:
    return [(k["spn"], k["fmi"]) for k in h["kode"]]


def _sepakat(bacaan: list[dict]) -> dict | None:
    """Gabungkan bacaan antar-varian: BEDA angka = tak boleh terkirim sendiri.

    ⚠️ Ini satu-satunya penjaga untuk kesalahan yang TAK BISA ditangkap store:
    insiden produksi 2026-08-12 — pada foto yang disilaukan cahaya, angka '12'
    terbaca '2', dan SPN 4203 FMI 2 KEBETULAN juga kode sah ('Crankshaft sinyal
    terganggu', P0336). Store justru ikut membenarkannya, jadi hasil yang salah
    keluar dengan keyakinan 'pasti'. Yang membedakannya cuma ini: varian 'clahe'
    membaca FMI 2, varian 'raw' membaca FMI 12 — kesalahan OCR antar-varian
    memang tidak berkorelasi (pelajaran yang sama sudah terpakai di `vin_ocr`).

    Karena itu foto panel SELALU dibaca dua varian; yang cocok baru boleh
    'pasti'. Ongkosnya satu pembacaan tambahan (±1–2 detik) dan HANYA dibayar
    foto yang memang layar kode kesalahan. Bacaan yang berbeda tidak dibuang —
    ia disodorkan sebagai `alternatif` supaya user tinggal memilih.

    Varian yang pulang KOSONG bukan penyanggah (ia tak mengklaim apa pun),
    jadi tidak menurunkan keyakinan."""
    if not bacaan:
        return None
    terbaik: dict | None = None
    for h in bacaan:
        terbaik = _pilih(terbaik, h)
    if terbaik is None:
        return None
    # Label ECU & jenis pesan sering hanya tertangkap di SALAH SATU varian
    # ('Engine' terbaca di varian mentah tapi tidak di CLAHE, foto 2 baris
    # pemilik). Itu keterangan tambahan, bukan angka — tak ada gunanya dibuang
    # hanya karena varian pemenangnya kebetulan tak melihatnya.
    for kunci in ("ecu", "jenis_pesan"):
        if not terbaik.get(kunci):
            terbaik[kunci] = next((h[kunci] for h in bacaan if h.get(kunci)), "")
    if len(bacaan) < 2:
        return terbaik
    # ⚠️ Varian yang membaca LEBIH SEDIKIT baris bukan penyanggah — ia tak
    # membantah satu angka pun, cuma kehilangan satu baris (lazim: angka FMI
    # satu digit gampang tak terdeteksi). Yang menurunkan keyakinan hanyalah
    # PERTENTANGAN: pasangan yang tak ada sama sekali di bacaan terbaik. Tanpa
    # pembedaan ini, foto 2 baris yang terbaca sempurna pun diminta konfirmasi
    # hanya karena varian pembandingnya melihat satu baris saja (terukur: 2 dari
    # 10 kondisi).
    utama = set(_daftar(terbaik))
    beda = [h for h in bacaan if not set(_daftar(h)) <= utama]
    if not beda:
        return terbaik
    out = dict(terbaik)
    out["kode"] = [dict(k) for k in terbaik["kode"]]
    # Penurunan keyakinan tetap berlaku untuk SEMUA perbedaan (bacaan yang
    # goyah tetap bacaan yang goyah), tapi yang DITAMPILKAN sebagai kemungkinan
    # lain hanya pasangan yang benar-benar terdaftar — menawarkan angka sampah
    # sebagai pilihan cuma membuat user ragu pada bacaan yang sudah benar.
    usul = {f"SPN {k['spn']} FMI {k['fmi']}"
            for h in beda for k in h["kode"]
            if k["dikenal"] and (k["spn"], k["fmi"]) not in utama}
    for k in out["kode"]:
        sendiri = f"SPN {k['spn']} FMI {k['fmi']}"
        k["alternatif"] = sorted(usul - {sendiri})
    out["keyakinan"] = "rendah"
    out["beda_varian"] = True
    return out


def _balik(kotak, bentuk) -> list:
    """Kotak yang SAMA, dilihat sebagai foto terbalik 180°.

    Foto panel yang terpotret terbalik tetap terbaca ISInya — model cls RapidOCR
    memutar tiap kotak teks sendiri — yang tertukar hanya TATA LETAKnya, dan itu
    cukup untuk menggagalkan pencocokan kolom. Membalik koordinatnya GRATIS,
    jadi dicoba lebih dulu sebelum memutuskan membaca ulang gambar.

    Pada foto tegak, jalan ini tak menghasilkan apa-apa dengan sendirinya: baris
    data jatuh di ATAS kepala tabel setelah dibalik, dan `_baris` membuangnya."""
    t, l = float(bentuk[0]), float(bentuk[1])
    return [(l - x1, t - y1, l - x0, t - y0, s) for x0, y0, x1, y1, s in kotak]


def _baca_lokal(data: bytes) -> dict:
    """Pipeline sesungguhnya (proses anak, atau langsung bila isolasi mati).

    Dua varian pra-proses saja: layar panel adalah teks terang di latar gelap
    dengan kontras tinggi — 'clahe' dan 'raw' sudah cukup, dan varian perbesar
    2× hanya menambah detik tanpa menambah kebenaran (diukur pada foto uji:
    hasil identik, +3,4 detik).

    ⚠️ Varian KEDUA hanya dijalankan bila varian pertama sudah membuktikan foto
    ini memang layar kode kesalahan. Modul ini berdiri di depan pembacaan nomor
    rangka pada endpoint yang sama, jadi SETIAP foto rangka ikut melewatinya —
    membaca dua kali sebelum mengaku 'bukan urusan saya' berarti menghukum
    fitur tetangga dengan detik yang tak menghasilkan apa pun."""
    t0 = time.monotonic()
    bgr = vin_ocr._decode(data)                 # format rusak → ValueError
    terbaca: list[str] = []
    bacaan: list[dict] = []
    bukti = False
    for urut, (nama, im) in enumerate(vin_ocr._varian(bgr)):
        if urut >= 2 or (urut and not bukti):
            break
        kotak = vin_ocr._kotak_ocr(im)
        terbaca = [k[4] for k in kotak] or terbaca
        if not ada_bukti(kotak):
            continue
        bukti = True
        h = _dari_kotak(kotak, im)
        if not _cukup(h):                        # foto terbalik → tata letak saja
            h = _pilih(h, _dari_kotak(_balik(kotak, im.shape)))
        if h and h.get("ok"):
            bacaan.append(h)
    if not bukti:
        return {}                                # bukan layar kesalahan → jalur rangka
    hasil = _sepakat(bacaan)
    # Pemulihan putar hanya untuk foto yang BELUM terurai sama sekali. ⛔ Jangan
    # dijalankan pada bacaan yang sudah ada tapi ragu: bacaan ketiga dari gambar
    # yang sama akan menimpa keyakinan 'rendah' hasil `_sepakat` (ketahuan lewat
    # test kesepakatan varian) — persis pagar yang baru saja dipasang.
    if not (hasil and hasil.get("ok")):
        hasil = _pilih(hasil, _pulihkan_putar(bgr, t0))
    if hasil is None:
        hasil = _kosong()
    hasil["teks_terbaca"] = terbaca[:12]
    hasil["detik"] = round(time.monotonic() - t0, 2)
    hasil["pesan"] = pesan(hasil)
    return hasil


def _pulihkan_putar(bgr, t0: float) -> dict | None:
    """Foto REBAH 90°: baca ULANG gambar yang sudah diputar.

    Beda dari foto terbalik 180°, di sini membalik koordinat saja tidak cukup:
    pada foto rebah sebagian teks TAK TERDETEKSI sama sekali (diukur pada foto
    uji — angka FMI '12' hilang, sedang 'SPN'/'FMI'/'4203' tetap terbaca), jadi
    yang kurang memang harus dicari ulang pada gambar yang tegak.

    Hanya ditempuh setelah foto terbukti layar kode kesalahan, jadi ongkosnya
    tak pernah dibayar foto biasa."""
    import cv2
    hasil = None
    for rot in (cv2.ROTATE_90_COUNTERCLOCKWISE, cv2.ROTATE_90_CLOCKWISE):
        if time.monotonic() - t0 > _BATAS_PUTAR:
            logger.info("dtc_ocr: anggaran waktu pemulihan putar habis")
            break
        im = cv2.rotate(bgr, rot)
        kotak = vin_ocr._kotak_ocr(im)
        hasil = _pilih(hasil, _dari_kotak(kotak, im))
        if not _cukup(hasil):
            hasil = _pilih(hasil, _dari_kotak(_balik(kotak, im.shape)))
        if _cukup(hasil):
            break
    return hasil


def baca_dtc(data: bytes, *, isolasi: bool = True) -> dict:
    """Foto panel → kode kesalahan. {} bila foto ini bukan layar kode kesalahan.

    → {ok, jenis:'dtc', kode:[{spn, fmi, dikenal, kode, arti, unit, sumber}],
       jenis_pesan, ecu, keyakinan: pasti|tinggi|rendah|gagal, teks_terbaca,
       pesan, detik}"""
    hasil = baca_foto(data, isolasi=isolasi, juga_rangka=False)
    return hasil if hasil.get("jenis") == "dtc" else {}


def baca_foto(data: bytes, *, isolasi: bool = True, juga_rangka: bool = True) -> dict:
    """Satu foto, DUA kemungkinan: layar kode kesalahan atau nomor rangka.

    Kode kesalahan diuji DULU karena buktinya tak bermakna ganda (kata harfiah
    'SPN'/'FMI'/'DM1' di layar) dan ketahuan pada pembacaan pertama; sebaliknya
    memaksa foto panel lewat pipeline nomor rangka akan menempuh dua orientasi
    × empat varian sampai belasan detik hanya untuk pulang gagal.

    Keduanya berbagi SATU proses anak: yang mahal adalah memuat model OCR
    (±1 detik) dan itu hanya dibayar sekali."""
    vin_ocr._decode(data)                  # format rusak → ValueError, sebelum spawn
    if isolasi:
        sisa = vin_ocr._sisa_memori_mb()
        if sisa is not None and sisa < vin_ocr._SISA_MIN_MB:
            logger.warning("dtc_ocr: jatah memori container tinggal %.0f MB — "
                           "pembacaan foto ditunda", sisa)
            hasil = _kosong()
            hasil["pesan"] = ("Server sedang penuh sesaat — coba kirim fotonya "
                              "lagi beberapa saat lagi, atau ketik kodenya.")
            return hasil
        hasil = vin_ocr._jalankan_anak(
            data, vin_ocr.armada() if juga_rangka else [],
            entry="from app.services.dtc_ocr import _cli; _cli()")
        if hasil is not None:
            return hasil
        logger.warning("dtc_ocr: proses anak gagal — jatuh ke OCR dalam-proses")
    return _langsung(data, juga_rangka=juga_rangka)


def _langsung(data: bytes, *, juga_rangka: bool, rows=None) -> dict:
    """Urutan DTC → nomor rangka dalam satu proses (dipakai anak & fallback)."""
    hasil = _baca_lokal(data)
    if hasil:
        hasil["jenis"] = "dtc"
        return hasil
    if not juga_rangka:
        h = _kosong()
        h["pesan"] = ("Kode kesalahan tidak terbaca dari foto. Pastikan tabel "
                      "SPN/FMI di layar panel masuk penuh dalam bingkai dan "
                      "tidak memantul cahaya.")
        return h
    if rows is None:
        rows = vin_ocr.armada()
    hasil = vin_ocr._baca_lokal(data, rows)
    hasil["jenis"] = "rangka"
    return hasil


def _cli() -> None:
    """Titik masuk proses anak: stdin JSON {gambar(base64), armada} → stdout JSON."""
    import base64
    import json
    import sys

    req = json.loads(sys.stdin.read() or "{}")
    rows = [(str(v), dict(i or {})) for v, i in (req.get("armada") or [])]
    hasil = _langsung(base64.b64decode(req.get("gambar") or ""),
                      juga_rangka=bool(rows), rows=rows)
    sys.stdout.buffer.write(json.dumps(hasil, ensure_ascii=False).encode("utf-8"))


def _sebut(k: dict) -> str:
    return f"SPN {k['spn']} FMI {k['fmi']}"


def pesan(hasil: dict) -> str:
    """Kalimat siap tampil DAN siap dikirim ke chat (web & mobile memakai yang
    sama). Sengaja hanya memuat ANGKA + konteks layar, tanpa arti kodenya:
    penjelasan adalah pekerjaan tool `cari_kode_kesalahan`/`diagnosa` yang sudah
    membawa pagar sumber & bahasanya sendiri."""
    kode = hasil.get("kode") or []
    if not kode:
        return hasil.get("pesan") or "Kode kesalahan tidak terbaca dari foto."
    daftar = "; ".join(_sebut(k) for k in kode)
    ket = " · ".join(x for x in (hasil.get("jenis_pesan"), hasil.get("ecu")) if x)
    ekor = f" ({ket})" if ket else ""
    yakin = hasil.get("keyakinan")
    if yakin in ("pasti", "tinggi"):
        awal = "Kode kesalahan dari panel"
        tanya = " — tolong jelaskan penyebab dan langkah perbaikannya."
        return f"{awal}{ekor}: {daftar}{tanya}"
    # Sebabnya disebut apa adanya: user perlu tahu APA yang harus ia periksa di
    # layar — angka yang tak dikenal, atau angka yang terbaca mendua.
    if hasil.get("tak_lengkap"):
        sebab = ("ada baris lain di layar yang tak terbaca lengkap — periksa "
                 "apakah masih ada kode selain ini")
    elif hasil.get("beda_varian"):
        sebab = "dua kali pembacaan foto ini memberi angka berbeda"
    elif any(not k["dikenal"] for k in kode):
        sebab = "kode ini tidak ada di database kami"
    else:
        sebab = "bacaannya belum cukup meyakinkan"
    usul = [u for k in kode for u in k.get("alternatif") or []]
    saran = f" Kemungkinan lain: {usul[0]}." if len(usul) == 1 else ""
    return (f"Saya baca dari panel{ekor}: {daftar} — BELUM yakin, {sebab}.{saran} "
            "Perbaiki bila keliru, lalu kirim.")
