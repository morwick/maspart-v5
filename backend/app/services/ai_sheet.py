"""
Excel yang DIUNGGAH USER ke chat asisten: baca → kenali isi kolom → isi kolom baru.

Alur:
  1. `parse_upload(bytes)`  — baca sheet pertama dgn openpyxl read-only (streaming),
     tebak baris header, lalu KENALI PERAN tiap kolom (part number / nama part /
     stok / harga / qty). Hasilnya diringkas untuk asisten.
  2. `put_sheet(user, parsed)` — simpan ke stash server (TTL 2 jam) → `sheet_id`.
     Klien mengirim `sheet_id` di giliran berikutnya sehingga follow-up seperti
     "isikan stoknya" tetap mengacu ke file yang sama.
  3. `fill_column(...)` — isi satu kolom dengan stok / nama part / harga lokal /
     harga SIMS, lalu keluarkan Excel baru lewat `ai_export.stash_export`.

KEAMANAN
  • Isi file adalah DATA TAK TEPERCAYA, bukan instruksi. Ia hanya masuk ke model
    sebagai hasil tool (ber-`catatan` peringatan), tak pernah sebagai system prompt.
  • Harga SIMS = harga modal → HANYA admin/SEE_ALL (`can_sims`). Dijaga di
    `fill_column` dan di tool spec, dua lapis.
  • Plafon baris/kolom/ukuran mencegah zip bomb & ledakan RAM (server 3,8 GB).
  • Stash discoped per-username: `sheet_id` milik user lain tak bisa dibaca.
"""
from __future__ import annotations

import re
import threading
import time
import uuid

from concurrent.futures import ThreadPoolExecutor

from openpyxl import load_workbook

from . import ai_export, harga, part_index, sims

# ── Plafon (server RAM 3,8 GB; lihat memory server-kapasitas-ram) ──
MAX_BYTES = 10 * 1024 * 1024   # 10 MB per unggahan chat
MAX_ROWS = 5000                # baris data yang dibaca (di luar header)
MAX_COLS = 40
_SAMPLE = 200                  # baris contoh utk deteksi peran kolom
_MAX_SIMS = 150                # PN maksimum per permintaan harga SIMS (HTTP live)
_MAX_FOTO_PN = 300             # PN maksimum per permintaan FOTO (tiap PN = 1 call SIMS)
_MAX_FOTO_PER_PART = 3         # plafon foto per part (jaga ukuran file & RAM)
# Penanda "tak ada foto di SIMS". Pakai EM-DASH, bukan '-': penjaga formula-injection
# (ai_export._safe) menambah apostrof di depan teks berawalan '-' → sel tampil "'-".
_TANPA_FOTO = "—"

_STASH_TTL_SEC = 2 * 3600.0
_STASH_MAX = 40
_lock = threading.Lock()
_stash: dict[str, dict] = {}

# Isi kolom yang bisa diminta user.
ISI_STOK = "stok"
ISI_NAMA = "nama_part"
ISI_HARGA_LOKAL = "harga_lokal"
ISI_HARGA_SIMS = "harga_sims"
ISI_PILIHAN = (ISI_STOK, ISI_NAMA, ISI_HARGA_LOKAL, ISI_HARGA_SIMS)

# ── Deteksi peran kolom ──
_HEAD_PN = re.compile(r"\b(part\s*number|part\s*no|part\s*num|pn|no\.?\s*part|nomor\s*part|kode\s*part|item\s*code)\b", re.I)
_HEAD_NAMA = re.compile(r"\b(part\s*name|nama\s*part|nama\s*barang|deskripsi|description|item\s*name|keterangan\s*part)\b", re.I)
_HEAD_STOK = re.compile(r"\b(stok|stock|qty\s*ready|ready|sisa|on\s*hand)\b", re.I)
_HEAD_QTY = re.compile(r"\b(qty|quantity|jumlah|jml|pcs|order)\b", re.I)
_HEAD_HARGA = re.compile(r"\b(harga|price|rp|idr|cny|amount|nilai)\b", re.I)
# Kolom yang MENGELOMPOKKAN part (sistem/bagian unit) — konteks untuk memecah
# nama ambigu saat mengisi Part Number ('Hose clamp' di 'AIR INTAKE' vs 'COOLING').
_HEAD_KATEGORI = re.compile(
    r"\b(bagian|kategori|katagori|kelompok|kelas|golongan|grup|group|section|"
    r"sistem|system|assembly|assy|modul|module|posisi|lokasi)\b", re.I)

# PN Sinotruk/Weichai: huruf besar + angka, boleh . / + -, minimal 5 char.
_PN_RE = re.compile(r"^[A-Z0-9][A-Z0-9./+\-]{4,}$")


def _txt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _looks_pn(s: str) -> bool:
    s = s.strip().upper()
    if not _PN_RE.match(s):
        return False
    return any(c.isdigit() for c in s) and any(c.isalpha() for c in s)


def _header_row(rows: list[list]) -> int:
    """Indeks baris header = baris pertama dengan >=2 sel teks non-angka.
    Banyak Excel lapangan punya baris judul/logo di atas."""
    for i, r in enumerate(rows[:10]):
        teks = [c for c in r if _txt(c) and not _txt(c).replace(".", "").replace(",", "").isdigit()]
        if len(teks) >= 2:
            return i
    return 0


def _detect_roles(headers: list[str], cols: list[list[str]]) -> list[str]:
    """Peran tiap kolom. Isi kolom mengalahkan nama header: kolom yang isinya
    benar-benar PN katalog = part_number, walau headernya 'Kode' atau kosong."""
    n = len(headers)
    roles = ["lain"] * n

    # 1) Sinyal ISI: berapa persen sel yang berbentuk PN, dan berapa yang benar
    #    ada di katalog. Katalog dicek sekali untuk semua kolom (satu lookup).
    kandidat: dict[int, list[str]] = {}
    for i, vals in enumerate(cols):
        sample = [v for v in vals[:_SAMPLE] if v]
        if not sample:
            continue
        pn_like = [v for v in sample if _looks_pn(v)]
        if len(pn_like) >= max(2, int(0.6 * len(sample))):
            kandidat[i] = pn_like

    dikenal: dict[int, int] = {}
    if kandidat:
        semua = {v.upper() for vals in kandidat.values() for v in vals[:60]}
        try:
            ada = {(r.get("part_number") or "").upper() for r in part_index.search_exact_pns(semua)}
        except Exception:
            ada = set()
        for i, vals in kandidat.items():
            dikenal[i] = sum(1 for v in vals[:60] if v.upper() in ada)

    if dikenal:
        # Kolom PN = yang paling banyak cocok katalog; bila tak satu pun cocok,
        # pakai kandidat pertama (file bisa berisi PN aftermarket di luar katalog).
        best = max(dikenal, key=lambda k: dikenal[k])
        pn_col = best if dikenal[best] > 0 else min(kandidat)
        roles[pn_col] = "part_number"
    elif kandidat:
        roles[min(kandidat)] = "part_number"

    # 2) Sinyal HEADER untuk kolom sisanya.
    for i, h in enumerate(headers):
        if roles[i] != "lain":
            continue
        if _HEAD_PN.search(h) and "part_number" not in roles:
            roles[i] = "part_number"
        elif _HEAD_NAMA.search(h):
            roles[i] = "part_name"
        elif _HEAD_STOK.search(h):
            roles[i] = "stok"
        elif _HEAD_QTY.search(h):
            roles[i] = "qty"
        elif _HEAD_HARGA.search(h):
            roles[i] = "harga"
        elif _HEAD_KATEGORI.search(h):
            roles[i] = "kategori"
    return roles


def parse_upload(data: bytes, filename: str = "") -> dict:
    """Baca Excel unggahan → {ok, error?, ...}. Tidak menyentuh jaringan."""
    if not data:
        return {"ok": False, "error": "File kosong."}
    if len(data) > MAX_BYTES:
        return {"ok": False, "error": f"File terlalu besar (maksimum {MAX_BYTES // 1024 // 1024} MB)."}
    fl = (filename or "").lower()
    if not fl.endswith((".xlsx", ".xlsm")):
        return {"ok": False, "error": "Format harus .xlsx atau .xlsm (bukan .xls/.csv)."}

    import io
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        return {"ok": False, "error": "File bukan Excel yang valid / rusak."}
    try:
        ws = wb[wb.sheetnames[0]]
        raw: list[list] = []
        for r in ws.iter_rows(values_only=True):
            raw.append(list(r[:MAX_COLS]))
            if len(raw) > MAX_ROWS + 12:      # +12 = ruang baris judul sebelum header
                break
        sheet_names = list(wb.sheetnames)
    finally:
        wb.close()

    if not raw:
        return {"ok": False, "error": "Sheet kosong."}

    hi = _header_row(raw)
    headers = [_txt(c) for c in raw[hi]]
    while headers and not headers[-1]:
        headers.pop()
    if not headers:
        return {"ok": False, "error": "Baris header tidak ditemukan."}
    ncol = len(headers)
    headers = [h or f"Kolom {i + 1}" for i, h in enumerate(headers)]

    body = [[_txt(c) for c in (r[:ncol] + [None] * (ncol - len(r)))] for r in raw[hi + 1:]]
    body = [r for r in body if any(r)][:MAX_ROWS]
    if not body:
        return {"ok": False, "error": "Tidak ada baris data di bawah header."}

    cols = [[r[i] for r in body] for i in range(ncol)]
    roles = _detect_roles(headers, cols)

    pn_idx = roles.index("part_number") if "part_number" in roles else None
    dikenal = 0
    if pn_idx is not None:
        pns = [r[pn_idx].upper() for r in body if r[pn_idx]]
        try:
            ada = {(r.get("part_number") or "").upper() for r in part_index.search_exact_pns(set(pns))}
        except Exception:
            ada = set()
        dikenal = sum(1 for p in pns if p in ada)

    return {
        "ok": True,
        "filename": filename or "unggahan.xlsx",
        "sheet": sheet_names[0],
        "sheet_lain": sheet_names[1:],
        "jumlah_baris": len(body),
        "jumlah_kolom": ncol,
        "headers": headers,
        "roles": roles,
        "kolom_pn": headers[pn_idx] if pn_idx is not None else None,
        "pn_dikenal": dikenal,
        "contoh": body[:5],
        "_body": body,
        "terpotong": len(body) >= MAX_ROWS,
    }


def ringkas(parsed: dict) -> dict:
    """Ringkasan aman untuk model & UI (tanpa `_body` penuh). Dilengkapi sinyal
    KONTEKS agar model paham maksud file: berapa baris yang Part Number-nya masih
    KOSONG (kandidat diisi) & kolom pengelompokan (sistem/bagian) + nilainya."""
    headers, roles, body = parsed["headers"], parsed["roles"], parsed["_body"]
    kolom = [{"nama": h, "peran": p} for h, p in zip(headers, roles)]

    # Baris tanpa Part Number = yang biasanya user minta dilengkapi.
    baris_tanpa_pn = None
    if "part_number" in roles:
        pn_i = roles.index("part_number")
        baris_tanpa_pn = sum(
            1 for r in body
            if not (str(r[pn_i]).strip() if pn_i < len(r) and r[pn_i] is not None else "")
        )

    # Kolom kategori/bagian: bantu model mengerti STRUKTUR file (part dikelompokkan
    # per sistem) & jadi konteks pemecah ambigu saat mengisi PN.
    kategori = None
    if "kategori" in roles:
        k_i = roles.index("kategori")
        nilai: list[str] = []
        for r in body:
            v = (str(r[k_i]).strip() if k_i < len(r) and r[k_i] is not None else "")
            if v and v not in nilai:
                nilai.append(v)
            if len(nilai) >= 20:
                break
        kategori = {"kolom": headers[k_i], "contoh_nilai": nilai}

    out = {
        "filename": parsed["filename"],
        "sheet": parsed["sheet"],
        "sheet_lain": parsed["sheet_lain"],
        "jumlah_baris": parsed["jumlah_baris"],
        "jumlah_kolom": parsed["jumlah_kolom"],
        "kolom": kolom,
        "kolom_part_number": parsed["kolom_pn"],
        "part_number_dikenal_di_katalog": parsed["pn_dikenal"],
        "contoh_baris": parsed["contoh"],
        "terpotong": parsed["terpotong"],
    }
    if baris_tanpa_pn is not None:
        out["baris_tanpa_part_number"] = baris_tanpa_pn
    if kategori is not None:
        out["kolom_pengelompokan"] = kategori
    return out


# ── Stash per-user ──
def put_sheet(username: str, parsed: dict) -> str:
    now = time.monotonic()
    sid = uuid.uuid4().hex
    with _lock:
        for k in [k for k, v in _stash.items() if now - v["at"] > _STASH_TTL_SEC]:
            _stash.pop(k, None)
        while len(_stash) >= _STASH_MAX:
            _stash.pop(min(_stash, key=lambda k: _stash[k]["at"]), None)
        _stash[sid] = {"at": now, "user": (username or "").lower(), "data": parsed}
    return sid


def get_sheet(sheet_id: str, username: str) -> dict | None:
    """Ambil sheet MILIK user ini. sheet_id orang lain → None (bukan error beda,
    agar tak jadi oracle keberadaan file)."""
    if not sheet_id:
        return None
    with _lock:
        e = _stash.get(sheet_id)
        if not e:
            return None
        if time.monotonic() - e["at"] > _STASH_TTL_SEC:
            _stash.pop(sheet_id, None)
            return None
        if e["user"] != (username or "").lower():
            return None
        return e["data"]


def _cari_kolom(headers: list[str], nama: str) -> int | None:
    """Cocokkan nama kolom yang disebut user (case/spasi-insensitif, boleh
    sebagian). 'kolom D' / 'D' juga diterima sebagai huruf kolom Excel."""
    n = (nama or "").strip()
    if not n:
        return None
    low = [h.strip().lower() for h in headers]
    nl = n.lower()
    if nl in low:
        return low.index(nl)
    # huruf kolom Excel (A, B, C…)
    m = re.fullmatch(r"(?:kolom\s*)?([a-zA-Z])", n)
    if m:
        i = ord(m.group(1).upper()) - 65
        if 0 <= i < len(headers):
            return i
    for i, h in enumerate(low):
        if nl in h or h in nl:
            return i
    return None


def _fmt_rp(v) -> str:
    try:
        return "Rp " + f"{int(v):,}".replace(",", ".")
    except (TypeError, ValueError):
        return ""


def _cocok_gudang(minta: str, tersedia: list[str]) -> str | None:
    """Cocokkan nama gudang yang diminta user ke nama gudang KANONIK di indeks
    (case/spasi-insensitif; sama persis dulu, lalu substring)."""
    m = re.sub(r"\s+", " ", (minta or "").strip().lower())
    if not m:
        return None
    for g in tersedia:
        if m == g.strip().lower():
            return g
    for g in tersedia:
        gl = g.strip().lower()
        if m in gl or gl in m:
            return g
    return None


_MAX_GUDANG = 12   # plafon kolom gudang per permintaan (jaga lebar file)


def fill_column(
    sheet_id: str,
    user: dict,
    isi: str,
    kolom_tujuan: str = "",
    kolom_pn: str = "",
    can_sims: bool = False,
) -> dict:
    """Isi satu kolom pada sheet unggahan, lalu siapkan Excel hasil untuk diunduh.

    `kolom_tujuan` boleh kolom yang sudah ada (ditimpa) atau nama baru (ditambah
    di ujung). `can_sims` WAJIB True untuk isi='harga_sims' (harga modal)."""
    parsed = get_sheet(sheet_id, user.get("username", ""))
    if not parsed:
        return {"found": False, "error": "Belum ada file Excel yang diunggah di percakapan ini "
                                         "(atau sudah kedaluwarsa). Minta user unggah ulang."}
    if isi not in ISI_PILIHAN:
        return {"error": f"isi harus salah satu dari {list(ISI_PILIHAN)}"}
    if isi == ISI_HARGA_SIMS and not can_sims:
        return {
            "denied": True,
            "error": "Harga SIMS (harga modal) hanya untuk admin. Jangan menampilkan, "
                     "memperkirakan, atau menghitung harga SIMS untuk user ini.",
        }

    headers = list(parsed["headers"])
    body = [list(r) for r in parsed["_body"]]

    # Kolom PN: pakai yang disebut user, kalau tidak pakai hasil deteksi.
    pn_i = _cari_kolom(headers, kolom_pn) if kolom_pn else None
    if pn_i is None:
        pn_i = parsed["roles"].index("part_number") if "part_number" in parsed["roles"] else None
    if pn_i is None:
        return {"found": False,
                "error": "Kolom Part Number tidak terdeteksi. Minta user menyebut kolom mana "
                         "yang berisi Part Number."}

    label = {
        ISI_STOK: "Stok",
        ISI_NAMA: "Nama Part",
        ISI_HARGA_LOKAL: "Harga",
        ISI_HARGA_SIMS: "Harga SIMS (IDR)",
    }[isi]
    tgt = _cari_kolom(headers, kolom_tujuan) if kolom_tujuan else None
    if tgt is None:
        headers.append(kolom_tujuan.strip() or label)
        tgt = len(headers) - 1
        for r in body:
            r.append("")

    pns = [(r[pn_i] or "").strip().upper() for r in body]
    unik = [p for p in dict.fromkeys(pns) if p]

    terisi = 0
    catatan_sumber = ""
    if isi == ISI_HARGA_SIMS:
        if len(unik) > _MAX_SIMS:
            return {"found": False,
                    "error": f"Terlalu banyak Part Number ({len(unik)}) untuk harga SIMS live. "
                             f"Maksimum {_MAX_SIMS} per permintaan — minta user memecah filenya."}
        res = harga.batch_harga(unik)
        peta = {r["pn"]: r for r in res["results"]}
        for r, p in zip(body, pns):
            d = peta.get(p)
            r[tgt] = _fmt_rp(d["idr"]) if d and d.get("idr") is not None else ""
            terisi += 1 if r[tgt] else 0
        catatan_sumber = f"SIMS live (kurs CNY→IDR {res['rate']:.0f})"
    else:
        # Sumber MURAH: indeks part lokal (bukan panggilan per-PN ke Accurate).
        try:
            rows = part_index.search_exact_pns(set(unik))
        except Exception as e:
            return {"found": False, "error": f"gagal membaca indeks part: {e}"}
        peta: dict[str, dict] = {}
        for row in rows:
            peta.setdefault((row.get("part_number") or "").upper(), row)
        key = {ISI_STOK: "stok", ISI_NAMA: "part_name", ISI_HARGA_LOKAL: "harga"}[isi]
        for r, p in zip(body, pns):
            d = peta.get(p)
            v = "" if not d else _txt(d.get(key))
            r[tgt] = "" if v in ("N/A", "—") else v
            terisi += 1 if r[tgt] else 0
        catatan_sumber = "indeks part lokal (stok.xlsx / harga.xlsx / katalog)"

    judul = f"{parsed['filename'].rsplit('.', 1)[0]} + {label}"
    export_id, filename = ai_export.stash_export(judul, headers, body)
    return {
        "found": True,
        "export_id": export_id,
        "filename": filename,
        "judul": judul,
        "jumlah_baris": len(body),
        "kolom_diisi": headers[tgt],
        "kolom_part_number": headers[pn_i],
        "baris_terisi": terisi,
        "baris_kosong": len(body) - terisi,
        "sumber": catatan_sumber,
        "catatan": (
            "📎 Kartu unduh Excel muncul otomatis di bawah jawaban — beri tahu user singkat. "
            f"{terisi} dari {len(body)} baris terisi; sisanya PN tak ditemukan di sumber — "
            "sampaikan apa adanya, ⛔ JANGAN mengarang nilai untuk baris kosong."
        ),
    }


def fill_photos(sheet_id: str, user: dict, kolom_pn: str = "", jumlah: int = 2) -> dict:
    """Tempelkan FOTO RESMI SIMS ke Excel unggahan user: `jumlah` foto per part,
    di kolom baru di ujung kanan. Baris tanpa foto ditandai '-' (TIDAK ditebak).

    Foto dicari per PART NUMBER — bukan per nama. Pencarian nama di SIMS bersifat
    'mengandung kata' & mengembalikan part LAIN (nama 'Radiator' memunculkan pipa
    radiator), jadi mencocokkan foto lewat nama berisiko memasang foto part yang
    SALAH di dokumen penawaran.

    Di sini hanya URL foto yang diambil (ringan, ter-cache di SIMS). Unduh + tempel
    gambar dikerjakan SAAT KARTU DIUNDUH (ai_export builder 'sheet_foto'), karena
    foto SIMS bisa >10 MB per file — menahannya di RAM akan menggerus server."""
    parsed = get_sheet(sheet_id, user.get("username", ""))
    if not parsed:
        return {"found": False, "error": "Belum ada file Excel yang diunggah di percakapan ini "
                                         "(atau sudah kedaluwarsa). Minta user unggah ulang."}
    if not sims.available():
        return {"found": False, "error": "Layanan SIMS (sumber foto) sedang tidak tersedia."}
    try:
        n_foto = int(jumlah or 2)
    except (TypeError, ValueError):
        n_foto = 2
    n_foto = max(1, min(_MAX_FOTO_PER_PART, n_foto))

    headers = list(parsed["headers"])
    body = [list(r) for r in parsed["_body"]]

    pn_i = _cari_kolom(headers, kolom_pn) if kolom_pn else None
    if pn_i is None:
        pn_i = parsed["roles"].index("part_number") if "part_number" in parsed["roles"] else None
    if pn_i is None:
        return {"found": False,
                "error": "Kolom Part Number tidak terdeteksi. Foto SIMS hanya bisa dicari lewat "
                         "Part Number (pencarian lewat nama part memberi foto part yang salah). "
                         "Minta user menyebut kolom mana yang berisi Part Number."}

    pns = [(r[pn_i] or "").strip().upper() if pn_i < len(r) else "" for r in body]
    unik = [p for p in dict.fromkeys(pns) if p]
    if not unik:
        return {"found": False, "error": f"Kolom '{headers[pn_i]}' tidak berisi Part Number."}
    if len(unik) > _MAX_FOTO_PN:
        return {"found": False,
                "error": f"Terlalu banyak Part Number ({len(unik)}) untuk pencarian foto SIMS. "
                         f"Maksimum {_MAX_FOTO_PN} per permintaan — minta user memecah filenya."}

    # Ambil URL foto per PN (SIMS ber-cache; paralel terbatas agar tak membanjiri SIMS).
    peta: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for pn, urls in zip(unik, ex.map(sims.get_images, unik)):
            peta[pn] = list(urls or [])[:n_foto]

    # Kolom foto baru di ujung kanan; isi sel '-' untuk yang tak ada fotonya
    # (gambar ditempel saat unduh, jadi selnya sengaja dibiarkan kosong di sini).
    kol_foto = [len(headers) + i for i in range(n_foto)]
    for i in range(n_foto):
        headers.append(f"Foto {i + 1}" if n_foto > 1 else "Foto")
    foto_baris: list[list[str]] = []
    ada = 0
    for r, p in zip(body, pns):
        urls = peta.get(p) or []
        foto_baris.append(urls)
        for j, _c in enumerate(kol_foto):
            r.append("" if j < len(urls) else _TANPA_FOTO)
        if urls:
            ada += 1

    judul = f"{parsed['filename'].rsplit('.', 1)[0]} + Foto"
    export_id, filename = ai_export.stash_builder(judul, {
        "kind": "sheet_foto",
        "judul": judul,
        "kolom": headers,
        "baris": body,
        "foto": foto_baris,
        "kol_foto": kol_foto,
    })
    return {
        "found": True,
        "export_id": export_id,
        "filename": filename,
        "judul": judul,
        "jumlah_baris": len(body),
        "kolom_part_number": headers[pn_i],
        "foto_per_part": n_foto,
        "baris_berfoto": ada,
        "baris_tanpa_foto": len(body) - ada,
        "sumber": "foto resmi SIMS (dicari per Part Number)",
        "catatan": (
            "📎 Kartu unduh Excel muncul otomatis di bawah jawaban — beri tahu user singkat. "
            f"{ada} dari {len(body)} baris dapat foto; sisanya PN-nya memang TIDAK punya foto "
            f"di SIMS dan ditandai '{_TANPA_FOTO}'. ⛔ JANGAN menjanjikan foto untuk baris itu & "
            "JANGAN menyarankan mencocokkan foto lewat NAMA part — pencarian nama di SIMS "
            "mengembalikan part lain (foto bisa SALAH)."
        ),
    }


_ISI_LABEL = {
    ISI_STOK: "Stok",
    ISI_NAMA: "Nama Part",
    ISI_HARGA_LOKAL: "Harga",
    ISI_HARGA_SIMS: "Harga SIMS (IDR)",
}
_ISI_KEY = {ISI_STOK: "stok", ISI_NAMA: "part_name", ISI_HARGA_LOKAL: "harga"}


def fill_columns(
    sheet_id: str,
    user: dict,
    permintaan: list[dict],
    can_sims: bool = False,
    kolom_pn: str = "",
) -> dict:
    """Isi BEBERAPA kolom sekaligus ke SATU file (satu kartu unduh). `permintaan` =
    daftar spesifikasi kolom, tiap elemen menghasilkan SATU kolom:
      {"isi": "stok"|"nama_part"|"harga_lokal"|"harga_sims",
       "gudang": <opsional, khusus stok → kolom stok gudang itu>,
       "kolom_tujuan": <opsional nama/huruf kolom, default nama otomatis>}
    Semua kolom ditaruh dalam file yang sama sehingga user tak menerima banyak file.
    Data di-lookup sekali (indeks lokal + SIMS batch) lalu dipakai semua kolom."""
    parsed = get_sheet(sheet_id, user.get("username", ""))
    if not parsed:
        return {"found": False, "error": "Belum ada file Excel yang diunggah di percakapan ini "
                                         "(atau sudah kedaluwarsa). Minta user unggah ulang."}

    # Normalkan & validasi spesifikasi.
    specs: list[dict] = []
    for s in (permintaan or []):
        if not isinstance(s, dict):
            continue
        isi = (s.get("isi") or "").strip()
        if not isi:
            continue
        specs.append({"isi": isi,
                      "gudang": (s.get("gudang") or "").strip(),
                      "kolom_tujuan": (s.get("kolom_tujuan") or "").strip()})
    if not specs:
        return {"found": False, "error": "Tidak ada kolom yang diminta untuk diisi."}
    for s in specs:
        if s["isi"] not in ISI_PILIHAN:
            return {"error": f"isi '{s['isi']}' tak dikenal; pilih dari {list(ISI_PILIHAN)}"}
        if s["isi"] == ISI_HARGA_SIMS and not can_sims:
            return {"denied": True,
                    "error": "Harga SIMS (harga modal) hanya untuk admin. Jangan menampilkan, "
                             "memperkirakan, atau menghitung harga SIMS untuk user ini."}

    headers = list(parsed["headers"])
    body = [list(r) for r in parsed["_body"]]

    pn_i = _cari_kolom(headers, kolom_pn) if kolom_pn else None
    if pn_i is None:
        pn_i = parsed["roles"].index("part_number") if "part_number" in parsed["roles"] else None
    if pn_i is None:
        return {"found": False,
                "error": "Kolom Part Number tidak terdeteksi. Minta user menyebut kolom mana "
                         "yang berisi Part Number."}

    pns = [(r[pn_i] or "").strip().upper() for r in body]
    unik = [p for p in dict.fromkeys(pns) if p]

    # Lookup SEKALI, dipakai semua kolom.
    peta_lokal: dict[str, dict] = {}
    if any(s["isi"] in (ISI_STOK, ISI_NAMA, ISI_HARGA_LOKAL) for s in specs):
        try:
            for row in part_index.search_exact_pns(set(unik)):
                peta_lokal.setdefault((row.get("part_number") or "").upper(), row)
        except Exception as e:
            return {"found": False, "error": f"gagal membaca indeks part: {e}"}
    peta_sims: dict[str, dict] = {}
    sumber_sims = ""
    if any(s["isi"] == ISI_HARGA_SIMS for s in specs):
        if len(unik) > _MAX_SIMS:
            return {"found": False,
                    "error": f"Terlalu banyak Part Number ({len(unik)}) untuk harga SIMS live. "
                             f"Maksimum {_MAX_SIMS} per permintaan — minta user memecah filenya."}
        res = harga.batch_harga(unik)
        peta_sims = {r["pn"]: r for r in res["results"]}
        sumber_sims = f"SIMS live (kurs CNY→IDR {res['rate']:.0f})"
    tersedia_gudang = part_index.gudang_names()

    def _nilai_fn(isi: str, gud_canon: str | None):
        """Fungsi nilai per-PN untuk satu kolom (menutup peta lookup)."""
        if isi == ISI_STOK and gud_canon:
            def f(p):
                d = peta_lokal.get(p)
                return "" if not d else str((d.get("gudang") or {}).get(gud_canon, 0))
            return f
        if isi == ISI_HARGA_SIMS:
            def f(p):
                d = peta_sims.get(p)
                return _fmt_rp(d["idr"]) if d and d.get("idr") is not None else ""
            return f
        key = _ISI_KEY[isi]

        def f(p):
            d = peta_lokal.get(p)
            if not d:
                return ""
            v = _txt(d.get(key))
            return "" if v in ("N/A", "—") else v
        return f

    hasil: list[dict] = []
    gudang_tak_dikenal: list[str] = []
    gudang_dipakai = 0
    for s in specs:
        isi, gud = s["isi"], s["gudang"]
        gud_canon = None
        if isi == ISI_STOK and gud:
            if gudang_dipakai >= _MAX_GUDANG:
                continue
            gud_canon = _cocok_gudang(gud, tersedia_gudang)
            if not gud_canon:
                if gud not in gudang_tak_dikenal:
                    gudang_tak_dikenal.append(gud)
                continue
            gudang_dipakai += 1
            label = s["kolom_tujuan"] or f"Stok {gud_canon}"
        else:
            label = s["kolom_tujuan"] or _ISI_LABEL[isi]

        tgt = _cari_kolom(headers, label)
        if tgt is None:
            headers.append(label)
            tgt = len(headers) - 1
            for r in body:
                r.append("")

        fn = _nilai_fn(isi, gud_canon)
        terisi = 0
        for r, p in zip(body, pns):
            r[tgt] = fn(p)
            if r[tgt] != "":
                terisi += 1
        hasil.append({"kolom": headers[tgt], "isi": isi,
                      "gudang": gud_canon, "baris_terisi": terisi})

    if not hasil:
        return {"found": False,
                "error": (f"Tak ada kolom yang bisa diisi. Gudang tak dikenal: {gudang_tak_dikenal}. "
                          f"Pilihan gudang: {tersedia_gudang}.")}

    judul = f"{parsed['filename'].rsplit('.', 1)[0]} + Data"
    export_id, filename = ai_export.stash_export(judul, headers, body)
    return {
        "found": True,
        "export_id": export_id,
        "filename": filename,
        "judul": judul,
        "jumlah_baris": len(body),
        "kolom_part_number": headers[pn_i],
        "kolom": hasil,
        "gudang_tak_dikenal": gudang_tak_dikenal,
        "sumber": ("indeks part lokal (stok.xlsx/harga.xlsx/katalog)"
                   + (f" + {sumber_sims}" if sumber_sims else "")),
        "catatan": (
            "📎 SATU kartu unduh Excel muncul otomatis di bawah — SEMUA kolom yang diminta ada "
            "di file yang SAMA (jangan pernah membuat banyak file kecuali user eksplisit "
            "memintanya). Untuk stok per-gudang, '0' = terlacak tapi kosong di gudang itu, sel "
            "KOSONG = PN tak ada di sumber. "
            + (f"⚠️ Gudang tak dikenal: {gudang_tak_dikenal}. " if gudang_tak_dikenal else "")
            + "⛔ JANGAN mengarang nilai untuk baris kosong."
        ),
    }
