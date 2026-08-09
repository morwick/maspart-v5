"""Bangun data/sinonim/gejala_map.json — jembatan GEJALA/KELUHAN lapangan → part.

    cd backend
    python tools/build_gejala_map.py                 # bangun dari semua sumber benih
    python tools/build_gejala_map.py --limit 40      # batasi jumlah benih (uji coba)
    python tools/build_gejala_map.py --dry-run       # tampilkan hasil, JANGAN tulis file
    python tools/build_gejala_map.py --merge         # gabung dgn gejala_map.json lama

MASALAH YANG DIPECAHKAN
Seluruh pencarian asisten (search_boost, manual_teks, pengetahuan) murni
LEKSIKAL — pencocokan token + Levenshtein + stem, tanpa embedding teks sama
sekali. Satu-satunya jembatan dari bahasa lapangan Indonesia ke katalog
Inggris/Mandarin adalah kamus manual 327 entri, dan kamus itu berisi NAMA PART
("cucuk per" → spring pin), bukan GEJALA. Akibatnya montir yang bertanya dengan
cara paling alami — menyebut keluhannya, bukan nama partnya — gagal secara
SENYAP, dan kegagalannya terlihat persis seperti "datanya memang tidak ada".

MENGAPA DATASET, BUKAN EMBEDDING
Server 3,8 GB RAM sudah menanggung DINOv2 untuk pencarian foto; model embedding
teks in-process tak muat. Embedding lewat API berarti vendor baru (DeepSeek tak
punya endpoint embedding) + latensi tiap query di jalur terpanas + kualitas
slang-Indonesia → katalog EN/CN yang tak bisa diverifikasi. Sementara itu,
keluhan lapangan bersifat BERHINGGA dan berulang. Jadi: enumerasi sekali secara
offline, validasi ke katalog nyata, kirim sebagai data.

SKEMANYA SENGAJA SAMA dengan sinonim.json {grup, triggers[], keywords[]} →
expand_query, umbrella_keywords, subset kamus di prompt, dan penjaga istilah
deterministik semuanya ikut pintar TANPA kode runtime baru (lihat
services/sinonim.py::_file_gejala).

ARAHNYA SENGAJA DIBALIK — dan ini pembeda terpenting skrip ini.
Cara yang tampak wajar (ambil daftar gejala, minta LLM menebak partnya) sudah
dicoba dan gagal di dua sisi: sumber gejala yang kita punya (judul manual, label
fault code) ternyata berisi PROSEDUR PERBAIKAN, bukan keluhan — dan LLM benar
menolak semuanya; sementara di sisi keluaran, LLM bebas mengarang istilah
katalog. Jadi arahnya dibalik: benihnya adalah GRUP KAMUS KURASI yang keyword
katalognya SUDAH terbukti kena part, dan LLM hanya diminta menyumbang UNGKAPAN
INDONESIA-nya. Keyword tidak pernah datang dari LLM → satu kelas halusinasi
hilang sepenuhnya, bukan sekadar disaring.

Data kegagalan produksi (data/ai_belajar/gap_topik.json, search_misses.json)
ikut jadi benih tambahan begitu tersedia — di situ gejalanya nyata, bukan
tebakan. Saat ini keduanya masih kosong (loop belajar belum berjalan dengan
trafik nyata), jadi jalur itu otomatis tak aktif.

PAGAR ANTI-HALUSINASI: tiap keyword tetap divalidasi harus kena ≥1 part di
katalog lokal (validator yang sama dipakai loop belajar kamus) — menangkap
keyword kamus yang sudah basi, dan menutup jalur benih gap yang keyword-nya
memang berasal dari LLM.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests                                             # noqa: E402

from app.core.config import get_settings                    # noqa: E402
from app.services import fault_codes, manual_teks, sinonim   # noqa: E402
from app.services.ai_sinonim_learn import _validate_keywords  # noqa: E402

_TIMEOUT = 120
# Benih per panggilan LLM. 25 terbukti terlalu banyak: 25 grup × ~5 trigger
# menabrak max_tokens dan respons JSON-nya terpotong di tengah ("Unterminated
# string") → seluruh batch hangus. 15 memberi ruang lega.
# ⚠️ 2026-08-09: model sekarang (deepseek-v4-flash) adalah REASONING model —
# 85-88% completion token habis untuk penalaran (terukur: 3.388 dari 3.844).
# Dengan _BATCH=15 & max_tokens=4000, JSON balasan TERPOTONG di tengah string
# ("Unterminated string") dan seluruh batch hangus; batch yang lolos pun
# confidence-nya tertekan ke 0,5 (di bawah _MIN_CONF) sehingga builder ini
# DIAM-DIAM menghasilkan NOL entri. Batch dikecilkan + anggaran token dinaikkan;
# terukur ulang: 8 grup → 8 usulan utuh, confidence menyebar 0,5-0,85.
_BATCH = 8
_MIN_CONF = 0.55     # di bawah ini usulan dibuang — dataset ini tak diaudit manual
# Batas jumlah GRUP yang boleh berbagi satu trigger saat --merge. Di atas ini
# trigger berhenti membedakan sistem & justru mencemari expand_query.
_MAKS_GRUP_PER_TRIGGER = 5
_MAX_KEYWORDS = 5

_PROMPT_SISTEM = (
    "Kamu montir senior truk berat (Sinotruk/HOWO/Shacman/Weichai) di Indonesia. "
    "Diberi daftar PART (nama lapangan + sistemnya + istilah katalognya), tulis "
    "KELUHAN yang biasa diucapkan sopir/montir Indonesia ketika part itu "
    "bermasalah.\n"
    "Jawab HANYA JSON: {\"gejala\": [{\"part\": \"<nama part PERSIS seperti "
    "diberikan>\", \"triggers\": [\"<keluhan bahasa lapangan>\", ...], "
    "\"confidence\": <0..1>}]}\n"
    "ATURAN KERAS:\n"
    "- triggers = istilah gejala bahasa lapangan Indonesia APA ADANYA, termasuk "
    "slang bengkel. 4–6 triggers per part.\n"
    "- ⚠️ PALING PENTING: minimal 3 trigger harus SATU atau DUA KATA saja — inti "
    "keluhannya, tanpa kata sambung. Contoh BAIK: 'ngelitik', 'loyo', 'ngebul', "
    "'asap hitam', 'rem blong', 'setir berat', 'bunyi gluduk'. Contoh BURUK "
    "(terlalu panjang, takkan pernah cocok): 'mesin terasa loyo waktu menanjak', "
    "'ada bunyi gluduk kalau sedang mengerem'. Sistem mencocokkan frasa UTUH, "
    "jadi trigger panjang praktis tidak berguna.\n"
    "- Pakai bentuk DASAR yang lazim diketik, bukan berimbuhan: 'asap hitam' "
    "(bukan 'asapnya hitam'), 'tarikan berat' (bukan 'tarikannya berat').\n"
    "- ⛔ JANGAN menulis nama part sebagai trigger (itu sudah ada di kamus); "
    "tulis GEJALA yang dirasakan/terdengar/terlihat.\n"
    "- ⛔ JANGAN menulis kalimat perintah/prosedur ('periksa kabel...', "
    "'ukur tahanan...') — itu instruksi bengkel, bukan keluhan.\n"
    "- ⛔ JANGAN mengarang istilah katalog; field keywords TIDAK diminta dan akan "
    "diabaikan.\n"
    "- confidence rendah (<0.5) bila gejala part itu terlalu umum sehingga mudah "
    "tertukar dengan sistem lain. Part yang tak punya gejala khas (baut, mur, "
    "ring, klem, gasket generik) JANGAN disertakan sama sekali."
)

# Benih dari fault code: ambil deskripsi yang berbentuk KELUHAN, bukan kode.
_KODE_RE = re.compile(r"\b[PBUC]\d[0-9A-F]{2,5}\b|\bSPN\b|\bFMI\b", re.I)
# Kalimat prosedur, bukan keluhan — pernah mencemari benih & ditolak LLM 100%.
_PROSEDUR_RE = re.compile(
    r"^(pemeriksaan|periksa|langkah|tes |test |cek |ukur|pengukuran|penggantian|"
    r"ganti |prosedur|pemasangan|pembongkaran)", re.I)

# Kata SIFAT/kualitas yang, berdiri SENDIRI, tidak menunjuk sistem mana pun.
# Uji nyata: trigger "bunyi" menarik keyword 'Right door', "berat" menarik
# 'fuel filter' — expand_query menyuntikkan keyword SETIAP grup yang cocok, jadi
# satu trigger generik langsung mencemari pencarian dengan istilah tak relevan.
# Sebagai BAGIAN frasa dua kata ("bunyi gluduk", "asap hitam", "setir berat")
# kata-kata ini justru bagus — yang dilarang hanya pemakaian tunggal.
# Daftar ini menampung kata yang tak menunjuk sistem apa pun bila berdiri
# sendiri. Perhatikan yang SENGAJA TIDAK ada di sini: 'loyo', 'ngelitik',
# 'ngebul', 'brebet', 'selip' — itu gejala khas yang justru paling sering
# diketik montir, dan aturan panjang minimum yang tumpul (≥5 huruf) sempat
# membuang 'loyo' begitu saja. Yang menentukan bukan panjangnya, melainkan
# apakah kata itu mempersempit ke satu sistem.
_GENERIK_TUNGGAL = frozenset("""
bunyi suara berisik berat ringan panas dingin putih hitam biru abu asap keras
lembek bocor getar goyang macet longgar kendor susah cepat lambat rusak mati
hidup aus retak patah pecah oleng miring kasar halus boros lemah turun naik
kurang lebih tidak kadang sering jarang mesin truk mobil unit part kondisi
masalah gangguan klik grem kaku sulit jelek parah aneh
""".split())


def _benih(batas: int) -> list[dict]:
    """Benih = tiap ENTRI kamus kurasi beserta keyword katalognya.

    Ini sumber yang benar untuk tujuan kita: tiap entri adalah keluarga part yang
    benar-benar kita stok, dan keyword-nya sudah pernah terbukti kena katalog.
    Yang belum ada hanyalah cara montir MENGELUHKANNYA — itulah satu-satunya hal
    yang diminta dari LLM.

    Kuncinya trigger pertama, BUKAN field `grup`: `grup` di sinonim.json adalah
    kategori luas (body/rem/mesin/…), jadi memakainya sebagai kunci meruntuhkan
    327 entri jadi ~22 dan membuang hampir seluruh kamus."""
    out: list[dict] = []
    seen: set[str] = set()
    for e in sinonim.load():
        kw = [str(k).strip() for k in (e.get("keywords") or []) if str(k or "").strip()]
        trig = [str(t).strip() for t in (e.get("triggers") or []) if str(t or "").strip()]
        if not kw or not trig:
            continue
        kunci = trig[0].strip().lower()
        if kunci in seen:
            continue
        seen.add(kunci)
        out.append({"kunci": kunci, "kategori": str(e.get("grup") or "umum").strip().lower(),
                    "keywords": kw, "triggers_lama": trig})
        if len(out) >= batas:
            break
    return out


def _benih_gap() -> list[str]:
    """Pertanyaan yang BENAR-BENAR gagal di produksi (ai_belajar gap topik +
    pencarian nihil). Kelak ini benih paling berharga — bukti, bukan tebakan.
    Saat ini biasanya kosong: loop belajar belum berjalan dengan trafik nyata."""
    out: list[str] = []
    d = get_settings().data_path
    for nama, kunci in (("ai_belajar/gap_topik.json", "contoh"),
                        ("search_misses.json", "query")):
        p = d / nama
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in (data if isinstance(data, list) else (data or {}).get("items") or []):
            if isinstance(row, str):
                out.append(row)
            elif isinstance(row, dict):
                v = row.get(kunci)
                out.extend(v if isinstance(v, list) else [v] if v else [])
    return [s for s in (str(x).strip() for x in out)
            if s and not _PROSEDUR_RE.match(s) and not _KODE_RE.search(s)]


def _llm(batch: list[dict], gap: list[str]) -> list[dict]:
    s = get_settings()
    daftar = "\n".join(
        f"- part \"{b['kunci']}\" (sistem: {b['kategori']}) — istilah katalog: "
        f"{', '.join(b['keywords'][:6])}"
        for b in batch)
    if gap:
        daftar += ("\n\nSebagai gambaran, ini pertanyaan NYATA yang pernah gagal "
                   "dicari user (tirukan gayanya):\n"
                   + "\n".join(f"  · {g}" for g in gap[:15]))
    r = requests.post(
        f"{s.deepseek_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {s.deepseek_api_key}",
                 "Content-Type": "application/json"},
        json={
            "model": s.deepseek_model,
            "messages": [
                {"role": "system", "content": _PROMPT_SISTEM},
                {"role": "user", "content": f"Kelompok part:\n{daftar}"},
            ],
            "temperature": 0.0,
            # Harus jauh di atas kebutuhan JSON-nya: penalaran model ikut memakan
            # jatah ini (lihat catatan _BATCH). Kekurangan token TIDAK memberi
            # error dari API — balasannya cuma terpotong, lalu json.loads gagal
            # dan SELURUH batch hilang tanpa jejak selain satu baris "GAGAL".
            "max_tokens": 12000,
            "response_format": {"type": "json_object"},
        },
        timeout=_TIMEOUT,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"DeepSeek error {r.status_code}: {r.text[:200]}")
    isi = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
    data = json.loads(isi)
    items = data.get("gejala") if isinstance(data, dict) else None
    return [i for i in (items or []) if isinstance(i, dict)]


def _trigger_layak(s: str) -> bool:
    """Trigger yang boleh masuk dataset.

    Satu kata hanya diterima bila ia benar-benar menunjuk gejala khas
    ('ngelitik', 'ngebul', 'loyo', 'brebet') — bukan kata sifat umum. Frasa dua
    kata ke atas selalu diterima: konteksnya sudah membatasi sendiri."""
    kata = s.split()
    if len(kata) >= 2:
        return True
    w = kata[0].casefold() if kata else ""
    return len(w) >= 4 and w not in _GENERIK_TUNGGAL


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").casefold()).strip("-")[:40] or "umum"


def _bersihkan(usulan: list[dict], benih: list[dict]) -> tuple[list[dict], dict]:
    """Usulan LLM → entri berskema sinonim.json, hanya yang LOLOS validasi.

    Keyword TIDAK diambil dari LLM: dipetakan balik dari grup benih (kamus
    kurasi). Yang LLM sumbangkan hanya triggers. Validasi katalog tetap
    dijalankan — untuk menangkap keyword kamus yang sudah basi."""
    peta = {b["kunci"]: b for b in benih}
    entri: list[dict] = []
    stat = {"masuk": len(usulan), "conf_rendah": 0, "grup_asing": 0,
            "tanpa_trigger_baru": 0, "tanpa_keyword_valid": 0, "keyword_dibuang": 0,
            "trigger_generik": 0}
    grup_terpakai: set[str] = set()
    for u in usulan:
        try:
            conf = float(u.get("confidence") or 0)
        except Exception:
            conf = 0.0
        if conf < _MIN_CONF:
            stat["conf_rendah"] += 1
            continue
        asal = peta.get(str(u.get("part") or u.get("grup") or "").strip().lower())
        if not asal:
            stat["grup_asing"] += 1     # LLM mengarang nama part → buang
            continue
        # Trigger BARU saja: yang sudah ada di kamus kurasi tak perlu diulang.
        lama = {t.casefold() for t in asal["triggers_lama"]}
        triggers, seen = [], set()
        for t in (u.get("triggers") or []):
            s = " ".join(str(t or "").split())
            k = s.casefold()
            if not s or k in lama or k in seen or len(s) < 4:
                continue
            if _PROSEDUR_RE.match(s):   # instruksi bengkel, bukan keluhan
                continue
            if not _trigger_layak(s):   # kata sifat tunggal → mencemari pencarian
                stat["trigger_generik"] += 1
                continue
            seen.add(k)
            triggers.append(s)
        # Trigger PENDEK didahulukan: `sinonim.hit` mencocokkan frasa UTUH, jadi
        # "tarikan loyo nanjak" tak akan pernah kena "loyo waktu nanjak" yang
        # diketik user. Yang panjang tetap disimpan di ekor (kadang persis cocok),
        # tapi tak boleh menggeser yang pendek keluar dari plafon 6.
        triggers.sort(key=lambda s: len(s.split()))
        if not triggers:
            stat["tanpa_trigger_baru"] += 1
            continue
        valid, invalid = _validate_keywords(asal["keywords"])
        stat["keyword_dibuang"] += len(invalid)
        if not valid:
            stat["tanpa_keyword_valid"] += 1
            continue
        grup = f"gejala:{_slug(asal['kunci'])}"
        n, dasar = 2, grup
        while grup in grup_terpakai:    # grup wajib unik: entries() dedup by grup
            grup = f"{dasar}-{n}"
            n += 1
        grup_terpakai.add(grup)
        entri.append({"grup": grup, "triggers": triggers[:6],
                      "keywords": valid[:_MAX_KEYWORDS]})
    return entri, stat


def main() -> int:
    ap = argparse.ArgumentParser(description="Bangun data/sinonim/gejala_map.json")
    ap.add_argument("--limit", type=int, default=400,
                    help="maksimal grup kamus yang diproses (default 400 = semua)")
    ap.add_argument("--mulai", type=int, default=0,
                    help="lewati N benih pertama — untuk memotong panen jadi beberapa "
                         "jalan tanpa MEMBAYAR ULANG benih yang sudah diproses "
                         "(pakai bersama --merge)")
    ap.add_argument("--dry-run", action="store_true", help="tampilkan saja, jangan tulis")
    ap.add_argument("--merge", action="store_true",
                    help="gabung dengan gejala_map.json yang sudah ada")
    args = ap.parse_args()

    if not get_settings().ai_configured:
        print("❌ DEEPSEEK_API_KEY belum di-set di backend/.env — builder ini butuh LLM.")
        return 1

    benih = _benih(args.limit)
    if args.mulai:
        benih = benih[args.mulai:]
    if not benih:
        print("❌ Kamus sinonim kosong (data/sinonim/sinonim.json) — tak ada benih.")
        return 1
    gap = _benih_gap()
    print(f"Benih: {len(benih)} grup kamus · contoh kegagalan nyata: {len(gap)} "
          f"· model {get_settings().deepseek_model}\n")

    semua: list[dict] = []
    for i in range(0, len(benih), _BATCH):
        batch = benih[i:i + _BATCH]
        print(f"  batch {i // _BATCH + 1}/{-(-len(benih) // _BATCH)} ({len(batch)} grup) …",
              end=" ", flush=True)
        try:
            hasil = _llm(batch, gap)
            semua.extend(hasil)
            print(f"{len(hasil)} usulan")
        except Exception as e:
            print(f"GAGAL: {e}")
        time.sleep(1)   # santun ke API

    entri, stat = _bersihkan(semua, benih)
    print(f"\nValidasi: {stat['masuk']} usulan → {len(entri)} entri "
          f"(confidence rendah {stat['conf_rendah']}, grup asing {stat['grup_asing']}, "
          f"tanpa trigger baru {stat['tanpa_trigger_baru']}, tanpa keyword valid "
          f"{stat['tanpa_keyword_valid']}, keyword basi dibuang {stat['keyword_dibuang']})")

    if args.merge:
        p = sinonim._file_gejala()
        lama: list = []
        if p.exists():
            try:
                lama = json.loads(p.read_text(encoding="utf-8")) or []
            except Exception:
                lama = []
        # ⚠️ UNION per grup, BUKAN 'baru menimpa lama'. Aturan lama membuang
        # trigger lama begitu grupnya diregenerasi: run 2026-08-09 menghasilkan
        # 285 trigger BARU tapi sekaligus MENGHAPUS 302 trigger lama — total
        # coverage TURUN (763 → 746) padahal token sudah dibayar. Kerugiannya
        # senyap: jumlah ENTRI nyaris tak berubah, hanya jumlah TRIGGER yang
        # menyusut. Union menjaga keduanya (763 + 285 → 1.048).
        # LAMA dipertahankan UTUH; trigger BARU hanya diterima bila setelahnya ia
        # masih muncul di ≤ _MAKS_GRUP grup. expand_query menyuntikkan keyword
        # SETIAP grup yang cocok, jadi trigger yang menempel di 20-30 grup
        # berhenti menunjuk sistem mana pun — union naif sempat menaikkan
        # 'ngelitik' dari 7 → 20 grup dan trigger generik 24 → 53.
        by_grup: dict[str, dict] = {}
        for e in lama:
            if not isinstance(e, dict) or not str(e.get("grup") or ""):
                continue
            by_grup[str(e["grup"]).lower()] = {
                "grup": e["grup"], "triggers": list(e.get("triggers") or []),
                "keywords": list(e.get("keywords") or [])}
        hitung: dict[str, int] = {}
        for e in by_grup.values():
            for t in e["triggers"]:
                hitung[str(t).lower()] = hitung.get(str(t).lower(), 0) + 1
        ditolak = 0
        for e in entri:
            g = str(e.get("grup") or "")
            if not g:
                continue
            cur = by_grup.setdefault(g.lower(), {"grup": g, "triggers": [], "keywords": []})
            punya = {str(t).lower() for t in cur["triggers"]}
            for t in (e.get("triggers") or []):
                tl = str(t).lower()
                if tl in punya:
                    continue
                if hitung.get(tl, 0) + 1 > _MAKS_GRUP_PER_TRIGGER:
                    ditolak += 1
                    continue
                cur["triggers"].append(t)
                punya.add(tl)
                hitung[tl] = hitung.get(tl, 0) + 1
            for w in (e.get("keywords") or []):
                if w and w not in cur["keywords"]:
                    cur["keywords"].append(w)
        sebelum_t = len({str(t).lower() for e in lama for t in (e.get("triggers") or [])})
        entri = sorted(by_grup.values(), key=lambda e: str(e.get("grup") or ""))
        sesudah_t = len({str(t).lower() for e in entri for t in (e.get("triggers") or [])})
        print(f"Digabung (UNION, lama dipertahankan) → {len(entri)} entri, "
              f"trigger {sebelum_t} → {sesudah_t}; ditolak krn generik: {ditolak}")

    for e in entri[:10]:
        print(f"  · {', '.join(e['triggers'][:3])} → {', '.join(e['keywords'])}")
    if len(entri) > 10:
        print(f"  … dan {len(entri) - 10} entri lain")

    if args.dry_run:
        print("\n(--dry-run: file TIDAK ditulis)")
        return 0
    if not entri:
        print("\n❌ Nol entri lolos validasi — file tidak ditulis (file lama dibiarkan).")
        return 1

    p = sinonim._file_gejala()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entri, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)      # atomik — pembaca tak pernah melihat file setengah jadi
    print(f"\n✅ ditulis: {p} ({len(entri)} entri)")
    print("   Deploy: scp file ini ke data/sinonim/ di server (tanpa restart — "
          "loader cache per-mtime).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
