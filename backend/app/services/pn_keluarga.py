# -*- coding: utf-8 -*-
"""KELUARGA PART NUMBER — persamaan yang DIBACA dari katalog, bukan ditebak dari pola.

**Latar (audit log produksi 45 hari, 824 giliran, 2026-09-01).** `pengganti_part`
adalah tool dengan kegagalan `:nf` TERBANYAK (33×), dan 37 giliran bertanya
"interchange/persamaan". Tool itu hanya membaca tabel SUPERSESSION resmi (SIMS
sasis + Weichai mesin). Bila keduanya kosong, asisten menjawab yakin **"tidak ada
persamaan"** — padahal katalog kita sendiri sering memuat part yang jelas-jelas
setara. Dua kasus produksi yang terbukti salah:

* `WG9725191800` (2026-08-29) dijawab "nihil"; katalog punya `WG9725191800/1` dan
  `WG9725191800/2` (Hebei Yili) — VARIAN PEMASOK dari part yang sama, stok 16.
* `WG9525523046/1` (2026-08-29) — user bahkan minta eksplisit "part lain yang
  ukurannya sama"; katalog punya `WG9525523046`, `/2` (Liangsha), `/5`.

Modul ini menutup celah itu TANPA melanggar larangan mengarang: setiap kandidat
di sini benar-benar ADA sebagai baris katalog, dan disajikan dengan derajat
keyakinan + alasannya, TERPISAH dari supersession resmi.

── TIGA KELUARGA (dan mengapa keandalannya beda) ──
Semua angka di bawah DIUKUR dari indeks katalog nyata (45.618 PN, 2026-09-01),
bukan perkiraan. Skrip pengukurnya ada di test_pn_keluarga.py.

1. VARIAN PEMASOK — akhiran `/N` pada PN dasar yang sama
   (`WG9725190122/1` Mann+Hummel vs `/2` Hebei Yili). 883 PN katalog punya
   keluarga varian begini; dari pasangan base↔/N yang keduanya bernama, 87,3%
   bernama sama. Ini kandidat interchange TERKUAT: nomor dasarnya identik,
   yang berbeda hanya pabrik pembuatnya. 12,7% sisanya bernama beda (mis.
   `WG1671820012` "Heater assembly" vs `/1` "Air conditioner assembly") → kami
   TETAP tampilkan tapi ditandai `nama_cocok: false` supaya asisten memperingatkan,
   bukan diam-diam menyamakan.

2. SUB-RAKITAN — akhiran `+NNN` (`WG9725522011+001` = lembar 1 pegas daun,
   `+002` = lembar 2). ⛔ Ini BUKAN interchange: mereka bagian BERBEDA dari satu
   rakitan. Dikembalikan terpisah justru supaya asisten tak salah menyodorkannya
   sebagai pengganti — kesalahan yang mudah terjadi karena nomornya mirip.

3. PREFIX SETARA — inti angka sama, awalan huruf beda (`WG9925520235` ↔
   `AZ9925520235`). Ini penomoran ulang Sinotruk yang nyata, TAPI hanya untuk
   pasangan awalan tertentu. Diukur per-pasangan (lihat `_PREFIX_SETARA`), dan
   ⛔⛔ keluarga `SP**` (SPDC/SPZC/SPLS/SPHG/SPYA/SPCY/SPZF) TERBUKTI 0% dari
   ~450 pasang: di sana angka itu nomor urut katalog pemasok, jadi
   `SPHG0000000024` = "Brake friction pad" sementara `SPDC0000000024` = "Output
   shaft end cover" — part yang sama sekali berbeda. Menyamakan mereka akan
   mengirim mekanik memasang part salah. Karena itu prefix-swap dipagari DUA
   lapis: allow-list pasangan + kecocokan NAMA part.

⚠️ Modul ini TIDAK pernah mengklaim sesuatu sebagai pengganti resmi pabrik. Yang
resmi tetap hanya SIMS/Weichai lewat `pengganti_part`.
"""
from __future__ import annotations

import re
import threading

from rapidfuzz import fuzz

from . import part_index

# ── Pasangan awalan yang TERBUKTI menomori part yang sama ────────────────────
# Nilai = persen pasangan yang NAMANYA cocok, diukur dari katalog 45.618 PN
# (2026-09-01) oleh `ringkas_untuk_pengetahuan()` di modul ini — jadi angkanya
# bisa dihitung ulang kapan saja, bukan hafalan. Penyebut hanya pasangan yang
# KEDUA sisinya bernama (yang tak bernama tak membuktikan cocok maupun beda).
# Ambang masuk allow-list: >= 75%. Yang di bawah itu SENGAJA tidak didaftarkan,
# dan alasannya dicatat agar tak "diperbaiki" balik nanti:
#   EZ/WG 25,0% · WG/YG 22,2% · AZ/EZ 16,7% · GB/TPGB 50,0%
#   SPDC/SPZC/SPLS/SPHG/SPYA/SPCY/SPZF → 0,0% (nomor urut pemasok, bukan part).
_PREFIX_SETARA: dict[frozenset, float] = {
    frozenset({"AZ", "WG"}): 89.2,   # 955 pasang bernama — penomoran ulang sasis
    frozenset({"LG", "LZ"}): 86.7,   # 45
    frozenset({"YG", "YZ"}): 85.1,   # 67
    frozenset({"FG", "FZ"}): 79.5,   # 88 — HOMAN
    frozenset({"Q", "ZQ"}): 77.8,    # 117 — baut/mur standar GB
}

# Kecocokan nama minimum agar sebuah kandidat prefix-swap boleh disebut setara.
# 80 = ambang yang dipakai saat mengukur tabel di atas; menurunkannya akan
# meloloskan pasangan yang namanya sekadar mirip ("bracket" vs "bracket").
_AMBANG_NAMA = 80

# Ambang untuk sekadar MENANDAI apakah nama varian pemasok sejalan. Lebih longgar
# karena sebagian nama varian ditulis Mandarin sementara PN dasarnya Inggris
# ('WG9725193410' Oil bath air filter assembly vs '/4' 油浴式空滤器总成) — beda
# bahasa, part sama. Skor rendah di sini = "belum bisa dipastikan", bukan "beda".
_AMBANG_NAMA_VARIAN = 70

_MAKS_ANGGOTA = 12          # plafon per keluarga di hasil (hemat token prompt)

# Bentuk PN: DASAR [+SUB] [/VARIAN]. '-' TIDAK memotong (banyak PN katalog
# memang mengandungnya, mis. '200V10311-6082/1').
_BENTUK = re.compile(r"^(?P<dasar>.+?)(?:\+(?P<sub>\d+))?(?:/(?P<varian>\d+))?$")
_PREFIX = re.compile(r"^(?P<huruf>[A-Z]{1,4})(?P<angka>\d[\dA-Z]*)$")

_lock = threading.Lock()
# Indeks turunan katalog, dibangun ulang hanya bila indeks part di-rebuild.
# ⚠️ Sengaja dict biasa, BUKAN CacheTTL: isinya bukan cache berkunci yang tumbuh
# per-query (pelajaran 2026-08-12), melainkan SATU snapshot yang DIGANTI utuh
# tiap `part_index` rebuild — jadi jumlah entrinya terikat besar katalog dan tak
# pernah menumpuk. Ukurannya sekelas `part_index._PN_FLAT_CACHE` yang sudah ada.
_idx: dict = {"at": None, "dasar": {}, "induk": {}, "angka": {}, "nama": {}}


def urai(pn: str) -> dict:
    """PN → {dasar, sub, varian}. 'WG9725522011+001/1' →
    {'dasar': 'WG9725522011', 'sub': '001', 'varian': '1'}."""
    p = (pn or "").strip().upper()
    m = _BENTUK.match(p)
    if not m:
        return {"dasar": p, "sub": None, "varian": None}
    return {"dasar": m.group("dasar") or p, "sub": m.group("sub"),
            "varian": m.group("varian")}


def _batang(pn: str) -> str:
    """PN tanpa akhiran varian pemasok — pengelompok VARIAN PEMASOK.
    'WG9725522011+001/1' → 'WG9725522011+001' (sub-rakitan tetap dibedakan:
    lembar 1 dan lembar 2 memang part berbeda)."""
    u = urai(pn)
    return u["dasar"] + (f"+{u['sub']}" if u["sub"] else "")


class IndeksDingin(RuntimeError):
    """Indeks katalog belum dibangun — kita belum bisa menjawab, bukan 'tak ada'."""


def _bangun() -> dict:
    """Bangun indeks turunan sekali per build `part_index` (murah: satu lintasan).

    ⛔ SENGAJA TIDAK memanggil `part_index.ensure_index()`. Modul ini dipakai
    sebagai PELENGKAP di dalam `pengganti_part`; memicu build indeks penuh
    (±20 detik + unduh dataset) sebagai efek samping pertanyaan supersession
    akan membuat satu giliran chat menggantung tanpa alasan yang terlihat user.
    Indeks selalu sudah hangat di produksi (hampir semua tool part memakainya);
    kalau memang dingin, jawaban jujurnya 'belum bisa diperiksa'."""
    at = part_index._state.get("indexed_at")
    if at is None:
        raise IndeksDingin
    with _lock:
        if _idx["at"] == at:
            return _idx
    dasar: dict[str, list[str]] = {}
    induk: dict[str, list[str]] = {}
    angka: dict[str, list[str]] = {}
    nama: dict[str, str] = {}
    for pn, nm in part_index.all_parts_min():
        up = (pn or "").upper()
        if not up:
            continue
        nama[up] = " ".join((nm or "").split())
        u = urai(up)
        dasar.setdefault(_batang(up), []).append(up)
        induk.setdefault(u["dasar"], []).append(up)
        m = _PREFIX.match(re.sub(r"[^A-Z0-9]", "", u["dasar"]))
        if m:
            angka.setdefault(m.group("angka"), []).append(up)
    with _lock:
        _idx.update({"at": at, "dasar": dasar, "induk": induk, "angka": angka,
                     "nama": nama})
    return _idx


def _mirip(a: str, b: str) -> int:
    """Kemiripan nama part 0-100. Nama kosong di salah satu sisi → 0 (=TAK
    TERVERIFIKASI), sengaja bukan 100: tanpa nama kita tak punya bukti apa pun."""
    if not a or not b:
        return 0
    return int(fuzz.token_set_ratio(a.upper(), b.upper()))


def _baris(pn: str, nama: dict, **extra) -> dict:
    return {"part_number": pn, "nama": nama.get(pn) or None, **extra}


def keluarga(pn: str) -> dict:
    """Keluarga katalog untuk satu PN. Kunci hasil:

    - `varian_pemasok`  — kandidat interchange TERKUAT (nomor dasar identik).
    - `sub_rakitan`     — bagian lain dari rakitan yang sama; ⛔ BUKAN pengganti.
    - `rakitan_induk`   — PN rakitan UTUHnya (tanpa +NNN), bila yang ditanya
      sebuah lembar/bagian. Sering inilah yang sebetulnya mau dipesan user.
    - `prefix_setara`   — penomoran ulang (AZ↔WG dll), sudah lolos cek nama.
    - `prefix_perlu_dicek` — pasangan awalan yang biasanya setara, tapi salah satu
      sisinya TAK BERNAMA di katalog kita sehingga tak bisa diverifikasi. Dipisah
      dari `prefix_ditolak` karena "tak bisa dicek" ≠ "terbukti beda": menolaknya
      diam-diam akan mengubur pasangan yang benar (kasus nyata `WG9925520235` ↔
      `AZ9925520235`, yang justru DIKONFIRMASI tabel resmi SIMS).
    - `prefix_ditolak`  — kandidat berinti-angka sama yang SENGAJA dibuang, plus
      alasannya. Ada supaya "tidak ada" bisa dibedakan dari "ada tapi terbukti
      part lain" — asisten boleh menjelaskannya, dan kita bisa mengauditnya.

    Selalu mengembalikan dict (tak pernah melempar); `tersedia: False` bila indeks
    katalog tak bisa dibaca — itu ⛔ BUKAN bukti keluarganya tak ada."""
    p = (pn or "").strip().upper()
    if not p:
        return {"tersedia": False, "alasan": "PN kosong"}
    try:
        idx = _bangun()
    except IndeksDingin:
        return {"tersedia": False,
                "alasan": ("indeks katalog belum dibangun — keluarga PN BELUM bisa "
                           "diperiksa, ⛔ BUKAN berarti tak ada")}
    except Exception:
        return {"tersedia": False,
                "alasan": ("indeks katalog gagal dibaca — keluarga PN BELUM bisa "
                           "diperiksa, ⛔ BUKAN berarti tak ada")}
    nama = idx["nama"]
    nm_p = nama.get(p, "")

    # 1) VARIAN PEMASOK — batang sama, akhiran /N berbeda (termasuk PN dasarnya).
    varian = []
    for k in sorted(idx["dasar"].get(_batang(p), [])):
        if k == p:
            continue
        s = _mirip(nm_p, nama.get(k, ""))
        varian.append(_baris(k, nama, kemiripan_nama=s,
                             nama_cocok=(s >= _AMBANG_NAMA_VARIAN)))

    # 2) SUB-RAKITAN (dasar sama, nomor +NNN berbeda) & RAKITAN INDUK (PN yang
    #    sama tanpa +NNN sama sekali = rakitan utuhnya). Keduanya dari satu
    #    indeks ber-kunci `dasar`, jadi O(1) — bukan memindai seluruh katalog.
    u = urai(p)
    sub, rakitan_induk = [], []
    if u["sub"]:
        for k in sorted(idx["induk"].get(u["dasar"], [])):
            ku = urai(k)
            if ku["sub"] == u["sub"]:
                continue
            if ku["sub"]:
                sub.append(_baris(k, nama, bagian=ku["sub"]))
            else:
                # Mekanik yang menanyakan "lembar 1" hampir selalu juga perlu
                # tahu nomor pegas UTUHNYA — di produksi asisten menyebutkan ini
                # hanya kalau kebetulan ingat dari giliran sebelumnya.
                rakitan_induk.append(_baris(k, nama))

    # 3) PREFIX SETARA — inti angka sama, awalan beda; DUA pagar (allow-list +
    #    nama). Yang gugur tetap dilaporkan agar penolakannya bisa dijelaskan.
    setara, perlu_dicek, ditolak = [], [], []
    m = _PREFIX.match(re.sub(r"[^A-Z0-9]", "", u["dasar"]))
    if m:
        huruf_p = m.group("huruf")
        for k in sorted(idx["angka"].get(m.group("angka"), [])):
            ku = urai(k)
            mk = _PREFIX.match(re.sub(r"[^A-Z0-9]", "", ku["dasar"]))
            if not mk or mk.group("huruf") == huruf_p:
                continue
            # Bagian rakitan WAJIB sama: 'WG…+001' hanya boleh disandingkan dgn
            # 'AZ…+001', bukan '+003'. Tanpa pagar ini dua lembar pegas yang
            # BERBEDA disamakan hanya karena namanya sama-sama "Leaf spring
            # assembly" — cek nama tak bisa membedakan lembar ke berapa.
            if ku["sub"] != u["sub"]:
                continue
            pasangan = frozenset({huruf_p, mk.group("huruf")})
            arah = f"{huruf_p}↔{mk.group('huruf')}"
            skor = _mirip(nm_p, nama.get(k, ""))
            # Nama hilang di salah satu sisi = TAK BISA DIVERIFIKASI. Ini kondisi
            # ketiga, bukan kegagalan: `_mirip` sengaja mengembalikan 0 untuk
            # nama kosong, jadi tanpa cabang ini pasangan sah yang kebetulan
            # tak bernama di katalog kita akan ditolak dengan alasan PALSU
            # ("namanya berbeda") — persis pola gagal-cek≠tidak-ada.
            tak_bernama = not nm_p or not nama.get(k)
            if pasangan not in _PREFIX_SETARA:
                ditolak.append(_baris(
                    k, nama, kemiripan_nama=skor,
                    alasan=(f"awalan {arah} TIDAK terbukti menomori part yang sama "
                            "(sering nomor urut pemasok) — angka yang sama di sini "
                            "KEBETULAN")))
            elif tak_bernama:
                perlu_dicek.append(_baris(
                    k, nama, kemiripan_nama=None,
                    keandalan_pasangan=f"{_PREFIX_SETARA[pasangan]}%",
                    alasan=(f"awalan {arah} biasanya setara, TAPI nama salah satu PN "
                            "tidak tercatat di katalog kita → belum bisa "
                            "diverifikasi. BUKAN berarti beda part.")))
            elif skor < _AMBANG_NAMA:
                ditolak.append(_baris(
                    k, nama, kemiripan_nama=skor,
                    alasan=(f"awalan {arah} biasanya setara, TAPI nama part-nya "
                            f"berbeda (kemiripan {skor}%) → kemungkinan besar "
                            "part lain")))
            else:
                setara.append(_baris(
                    k, nama, kemiripan_nama=skor,
                    keandalan_pasangan=f"{_PREFIX_SETARA[pasangan]}%",
                    alasan=(f"inti angka sama & awalan {arah} = penomoran ulang "
                            "Sinotruk; nama part cocok")))

    penuh = {"varian_pemasok": varian, "sub_rakitan": sub,
             "rakitan_induk": rakitan_induk, "prefix_setara": setara,
             "prefix_perlu_dicek": perlu_dicek, "prefix_ditolak": ditolak}
    out = {
        "tersedia": True,
        "part_number": p,
        "ada_di_katalog": p in nama,
        "nama": nm_p or None,
        **{k: v[:_MAKS_ANGGOTA] for k, v in penuh.items()},
    }
    # Daftar yang DIPOTONG wajib mengaku. Tanpa ini asisten menyajikan 12 varian
    # sebagai "semuanya", dan user menyimpulkan tak ada pilihan lain.
    _potong = {k: len(v) for k, v in penuh.items() if len(v) > _MAKS_ANGGOTA}
    if _potong:
        out["terpotong"] = {
            "jumlah_sebenarnya": _potong,
            "catatan": (f"Hanya {_MAKS_ANGGOTA} pertama yang ditampilkan per kelompok "
                        "— sebutkan ke user bahwa daftarnya lebih panjang."),
        }
    out["jumlah"] = (len(out["varian_pemasok"]) + len(out["sub_rakitan"])
                     + len(out["rakitan_induk"]) + len(out["prefix_setara"])
                     + len(out["prefix_perlu_dicek"]))
    _silang_stok(out)
    out["catatan"] = _CATATAN
    return out


def _silang_stok(out: dict) -> None:
    """Isi stok/harga lokal untuk tiap anggota keluarga — supaya asisten bisa
    langsung menyarankan yang READY, bukan sekadar menyebut nomor."""
    kunci = ("varian_pemasok", "sub_rakitan", "rakitan_induk", "prefix_setara",
             "prefix_perlu_dicek")
    semua = [r["part_number"] for k in kunci for r in out.get(k, [])]
    if not semua:
        return
    try:
        rows = part_index.rows_for_pns(semua)
    except Exception:
        return
    for k in kunci:
        for r in out.get(k, []):
            lr = rows.get(r["part_number"]) or {}
            if lr:
                r["stok_total"] = lr.get("stok")
                r["harga_lokal"] = lr.get("harga")


_CATATAN = (
    "KELUARGA PN dari KATALOG KITA SENDIRI — ⛔ BUKAN supersession resmi pabrik "
    "(itu hanya 'digantikan_oleh'/'menggantikan'). Cara memakai: "
    "(a) 'varian_pemasok' = PN dasar SAMA, beda pabrik pembuat (akhiran /1, /2) — "
    "ini kandidat interchange PALING KUAT, sebutkan sebagai 'varian pemasok dari "
    "part yang sama' dan dahulukan yang stoknya ada; bila 'nama_cocok': false, "
    "PERINGATKAN bahwa namanya berbeda sehingga perlu dicek fisik dulu. "
    "(b) 'sub_rakitan' = bagian LAIN dari satu rakitan (akhiran +001/+002, mis. "
    "lembar pegas daun ke-1 & ke-2) — ⛔ JANGAN sekali-kali menyodorkannya sebagai "
    "pengganti; sebutkan hanya bila membantu user melengkapi satu set. "
    "'rakitan_induk' = nomor rakitan UTUHnya — sebutkan, karena user yang minta "
    "satu lembar kerap sebenarnya butuh rakitan lengkapnya. "
    "(c) 'prefix_setara' = penomoran ulang Sinotruk (mis. WG…↔AZ…) yang sudah "
    "lolos cek nama part — sebutkan sebagai 'nomor setara di katalog', dan tetap "
    "sarankan konfirmasi bila part mahal/kritis. "
    "(d) 'prefix_perlu_dicek' = pasangan awalan yang biasanya setara tapi belum "
    "bisa diverifikasi (nama salah satu PN tak tercatat) — boleh disebut sebagai "
    "KANDIDAT yang perlu dikonfirmasi ke admin/pemasok, ⛔ jangan disajikan "
    "sebagai kepastian dan ⛔ jangan pula disembunyikan. "
    "(e) 'prefix_ditolak' = nomor bermiripan yang sengaja DIBUANG karena terbukti "
    "part lain — pakai untuk MENJELASKAN kalau user menyebut nomor itu, ⛔ jangan "
    "pernah menyarankannya. "
    "⛔ Keluarga KOSONG bukan bukti tak ada padanan di dunia nyata — hanya berarti "
    "katalog kita tak memuatnya."
)


def ringkas_untuk_pengetahuan(min_pasang: int = 20) -> dict:
    """Statistik keluarga untuk blok pengetahuan prompt (dipanggil saat build
    ai_knowledge). Dihitung dari katalog nyata supaya angkanya ikut bergerak
    saat katalog tumbuh — bukan angka hafalan yang membusuk.

    `min_pasang` = jumlah pasangan BERNAMA minimum agar sebuah pasangan awalan
    layak dilaporkan; di bawah itu persentasenya terlalu ribut untuk dijadikan
    pengetahuan (satu-dua kebetulan bisa membuatnya 0% atau 100%)."""
    try:
        # ⚠️ BEDA dengan `keluarga()`: ini jalur BATCH (build ai_knowledge.json,
        # offline), bukan jalur permintaan chat — di sini membangun indeks memang
        # tugasnya. Tanpa ini builder yang jalan dari CLI selalu mendapati indeks
        # dingin dan diam-diam menghasilkan blok pengetahuan TANPA anatomi PN.
        part_index.ensure_index()
        idx = _bangun()
    except Exception:
        return {}          # statistik opsional: tak ada indeks = tak ada blok
    nama = idx["nama"]
    n_varian = sum(1 for k, v in idx["dasar"].items() if len(v) > 1)
    pasangan: dict[str, dict] = {}
    for anggota in idx["angka"].values():
        if len(anggota) < 2:
            continue
        pref: dict[str, str] = {}
        for k in anggota:
            mk = _PREFIX.match(re.sub(r"[^A-Z0-9]", "", urai(k)["dasar"]))
            if mk:
                pref.setdefault(mk.group("huruf"), k)
        hs = sorted(pref)
        for i in range(len(hs)):
            for j in range(i + 1, len(hs)):
                na, nb = nama.get(pref[hs[i]], ""), nama.get(pref[hs[j]], "")
                key = f"{hs[i]}/{hs[j]}"
                d = pasangan.setdefault(key, {"cocok": 0, "total": 0, "tanpa_nama": 0})
                # Pasangan yang salah satu sisinya TAK BERNAMA tidak masuk
                # penyebut: ia tak membuktikan cocok MAUPUN beda. Memasukkannya
                # menekan angka keandalan jadi jauh lebih rendah dari kenyataan
                # (AZ/WG 38% vs 89% sebenarnya) — dan angka itu ikut tercetak ke
                # blok pengetahuan asisten, jadi salahnya menular ke jawaban.
                if not na or not nb:
                    d["tanpa_nama"] += 1
                    continue
                d["total"] += 1
                if _mirip(na, nb) >= _AMBANG_NAMA:
                    d["cocok"] += 1
    setara = [
        {"pasangan": k, "jumlah": v["total"] + v["tanpa_nama"],
         "jumlah_bernama": v["total"],
         "persen_nama_cocok": round(v["cocok"] / v["total"] * 100, 1)}
        for k, v in pasangan.items()
        if v["total"] >= min_pasang and frozenset(k.split("/")) in _PREFIX_SETARA
    ]
    setara.sort(key=lambda r: -r["jumlah"])
    return {"pn_punya_varian_pemasok": n_varian, "prefix_setara": setara}
