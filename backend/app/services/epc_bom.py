"""
Service EPC-BOM (Sinotruk, portal baru port 7001) — DAFTAR PART per NOMOR RANGKA.

Berbeda dari epc.py (endpoint config PUBLIK). Di sini kita ambil BOM PABRIK
(work/factory BOM) untuk SATU unit — daftar PART lengkap persis untuk VIN itu,
bukan asumsi per-prefix katalog. Satu panggilan = seluruh part unit.

    GET {BASE}/otherDoc/loadingList?vin=<frame>&part=

Auth: header  ``token: Bearer <hex>``. Token disimpan di ``data/epc_token.txt``,
dibaca FRESH tiap panggil → refresh cukup ganti isi file (scp), tanpa redeploy
(pola sama spt sinonim.json). Token EPC kedaluwarsa berkala; saat itu endpoint
balas ``code 110025 'Not has role!'`` ATAU ``message 'Login expired!'`` →
kita kembalikan _err='token_expired' agar pemanggil bisa minta user me-refresh
token (lihat _TOKEN_ERR_RE).

Catatan: nama part dari endpoint ini berbahasa China (field fldThMc). Pencocokan
nama Inggris / istilah Indonesia & stok/harga dilakukan di lapis pemanggil
(ai_assistant) dengan menyilangkan PN ke part_index lokal.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import urllib3

from ..core.config import get_settings

# Sertifikat tidak relevan (HTTP), tapi redam warning bila nanti pindah ke HTTPS.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EPC_BOM_BASE = "http://epc.sinotruk.com:7001/api/rest"
_LOADING_URL = f"{EPC_BOM_BASE}/otherDoc/loadingList"
# Parts Atlas (katalog terstruktur EPC) — tree walk: root → module → node → item.
# Inilah satu-satunya sumber yang MENGURAI assembly sampai PART AUS (kampas rem,
# friction plate, brake shoe) PERSIS untuk SATU VIN — Loading List berhenti di
# level assembly. Lihat _atlas_find().
_ATLAS_ROOT_URL = f"{EPC_BOM_BASE}/part/tree/root"
_ATLAS_MODULE_URL = f"{EPC_BOM_BASE}/part/tree/module"
_ATLAS_NODE_URL = f"{EPC_BOM_BASE}/part/tree/node"
_ATLAS_ITEM_URL = f"{EPC_BOM_BASE}/part/tree/item"
# Daftar ASSEMBLY UTAMA per-VIN (1 panggilan): kabin, gardan depan/tengah/belakang,
# mesin, transmisi, kopling — tiap baris {assemblyName(EN), assemblyCode(PN assy),
# remark(CN), assemblyTypeCode(CN)}. Jauh lebih murah dari walk pohon; memberi PN
# assembly NYATA unit itu (bukan sekadar kode model) → bisa disilang stok/harga.
_ASSEMBLY_URL = f"{EPC_BOM_BASE}/part/tree/assembly"
# Reverse lookup global: PN → kendaraan/model yang memakainya.
_MATCH_URL = f"{EPC_BOM_BASE}/home/match/part"      # GET {t:global,k:<pn>} → [{code,name}]
_REVERSE_URL = f"{EPC_BOM_BASE}/home/reverse/part"  # GET {t:global,v:<pn>,k:<pn>} → [{model,rootId,partCode,...}]

# SSO 云桥/yunqiao: tukar token SimsCloud (ICMCP) → token EPC. Inilah yang dipakai
# tombol "EPC" di SimsCloud. icmcpToken = JWT login SIMS (tanpa 'Bearer '),
# sysCode = 'intl' (kode sistem 'international company', global param yunqiaoSysCode).
_SSO_EXCHANGE_URL = "http://epc.sinotruk.com:7001/api/integrate/getUserInfoByIcmcpToken"
_SSO_SYSCODE = "intl"
_refresh_lock = threading.Lock()

# Pesan error EPC yang menandakan TOKEN/SESI kedaluwarsa (selain code 110025).
_TOKEN_ERR_RE = re.compile(
    r"login\s*expired|not\s*has\s*role|token.*expired|expired.*token|"
    r"invalid\s*token|unauthor|not\s*log",
    re.IGNORECASE,
)

_CACHE_TTL = 3600.0  # detik — BOM per-VIN statis; cache ringan agar hemat panggilan.
_cache: dict[str, dict] = {}  # frame -> {"at": monotonic, "val": dict}
_lock = threading.Lock()


def _frame(rangka: str) -> str:
    """Normalisasi → frame number (8 char terakhir bila VIN penuh). Sama spt epc."""
    n = re.sub(r"[^A-Z0-9]", "", (rangka or "").upper())
    return n[-8:] if len(n) >= 11 else n


def _token() -> str:
    """Token EPC dari data/epc_token.txt (boleh isi 'Bearer xxx' atau cuma 'xxx').
    '' bila file tak ada/kosong."""
    try:
        p = get_settings().data_path / "epc_token.txt"
        if p.exists():
            t = p.read_text(encoding="utf-8").strip()
            if t and not t.lower().startswith("bearer"):
                t = "Bearer " + t
            return t
    except Exception:
        pass
    return ""


def _sims_fetcher():
    """Impor lazy modul shared/sims_fetcher (login SIMS otomatis: RSA + captcha
    di-bypass; sudah dipakai di produksi untuk harga/foto)."""
    shared = Path(__file__).resolve().parents[2] / "shared"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    import sims_fetcher  # type: ignore
    return sims_fetcher


def refresh_token() -> str:
    """Ambil token EPC BARU sepenuhnya OTOMATIS via SSO SimsCloud (云桥/yunqiao) —
    tanpa campur tangan manusia, tanpa captcha:
      1. Login SIMS (sims_fetcher) → JWT ICMCP.
      2. Tukar JWT itu jadi token EPC via getUserInfoByIcmcpToken (sysCode=intl) —
         endpoint yang sama dipakai tombol 'EPC' di SimsCloud.
      3. Tulis token (hex, tanpa 'Bearer ') ke data/epc_token.txt.
    Kembalikan token hex, atau '' bila gagal (mis. SIMS down) → pemanggil jatuh ke
    pesan 'token kedaluwarsa' agar admin bisa isi manual sebagai cadangan."""
    try:
        sf = _sims_fetcher()
        jwt = (sf._get_token() or "").replace("Bearer ", "").strip()
        if not jwt:
            return ""
        r = requests.get(
            _SSO_EXCHANGE_URL,
            params={"icmcpToken": jwt, "sysCode": _SSO_SYSCODE},
            timeout=30, verify=False,
        )
        j = r.json()
        tok = ((j.get("data") or {}).get("token") or "").replace("Bearer ", "").strip()
        if not j.get("success") or not tok:
            return ""
        get_settings().data_path.joinpath("epc_token.txt").write_text(tok, encoding="utf-8")
        return tok
    except Exception:
        return ""


def available() -> bool:
    return bool(_token())


def _get(url: str, params: dict) -> dict:
    """GET ber-token → {'data': ...} atau {'_err': <kode>, 'message': ...}.
    Kode _err: no_token | network | token_expired | api."""
    tok = _token()
    if not tok:
        return {"_err": "no_token"}
    headers = {
        "token": tok,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-us",
        "Referer": "http://epc.sinotruk.com:7001/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36",
    }
    # Server EPC China kerap timeout/putus sesaat; walk Atlas bisa puluhan call →
    # satu blip jangan jatuhkan seluruh hasil. Retry ringan utk error JARINGAN saja
    # (bukan api/token — itu bukan transient).
    j = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30, verify=False)
            j = r.json()
            break
        except Exception:
            if attempt == 2:
                return {"_err": "network"}
            time.sleep(1.0 + attempt)
    # EPC/proxy kadang balas JSON non-objek (null/array/string) → jangan crash di .get.
    if not isinstance(j, dict):
        return {"_err": "api", "message": "bad_json"}
    if not j.get("success"):
        code = str(j.get("code") or "")
        msg = str(j.get("message") or "")
        # Token EPC kedaluwarsa muncul dalam beberapa bentuk: code 110025
        # ('Not has role!') ATAU message 'Login expired!' (kode lain). Kenali
        # keduanya agar pemanggil minta admin me-refresh token, bukan salah
        # mengira 'BOM tidak ditemukan'.
        if code == "110025" or _TOKEN_ERR_RE.search(msg):
            return {"_err": "token_expired"}
        return {"_err": "api", "message": j.get("message")}
    return {"data": j.get("data")}


def _get_auto(url: str, params: dict) -> dict:
    """_get + AUTO-REFRESH token via SSO bila kedaluwarsa, lalu coba sekali lagi.
    Transparan: user tak perlu tahu, admin tak perlu isi token manual."""
    res = _get(url, params)
    if res.get("_err") in ("token_expired", "no_token"):
        with _refresh_lock:
            # Mungkin thread lain sudah me-refresh — coba ulang dulu sebelum login lagi.
            res = _get(url, params)
            if res.get("_err") in ("token_expired", "no_token") and refresh_token():
                res = _get(url, params)
    return res


# BOM kendaraan penuh Sinotruk biasanya ratusan–ribuan part. Respons di bawah ini
# hampir pasti TRUNCATED/partial (glitch server China yg pernah balas 20 part utk
# unit yg sebenarnya 1150) → JANGAN dipercaya sbg 'lengkap', retry & jangan cache.
_LL_MIN_EXPECTED = 100


def _parse_loading_rows(rows: list) -> list[dict]:
    parts: list[dict] = []
    by_pn: dict[str, dict] = {}
    for r in rows or []:
        pn = (r.get("fldTh") or "").strip().upper()
        if not pn:
            continue
        qty = r.get("fldSl")
        if pn in by_pn:
            # PN sama muncul >1 baris (varian rakitan) → akumulasi qty bila numerik.
            try:
                by_pn[pn]["qty"] = (by_pn[pn]["qty"] or 0) + (qty or 0)
            except Exception:
                pass
            continue
        e = {"pn": pn, "nama_cn": (r.get("fldThMc") or "").strip(), "qty": qty}
        by_pn[pn] = e
        parts.append(e)
    return parts


def loading_list(rangka: str) -> dict:
    """BOM pabrik (daftar part) untuk satu unit dari nomor rangka.

    Sukses → {found:True, frame_number, jumlah_part, parts:[{pn, nama_cn, qty}], partial?}.
    Gagal  → {found:False, frame_number, _err, message?}.

    Anti-truncation: bila respons mencurigakan kecil (<100 part) — gejala respons
    terpotong dari server China — coba ulang & ambil yg TERBANYAK; hasil yg masih
    kecil ditandai partial=True dan TIDAK di-cache (agar panggilan berikut fetch lagi,
    tak menyajikan data tak lengkap yg bikin salah simpul 'part tidak ada di unit').
    """
    frame = _frame(rangka)
    if not frame:
        return {"found": False, "frame_number": "", "_err": "input"}

    with _lock:
        c = _cache.get(frame)
        if c and (time.monotonic() - c["at"] < _CACHE_TTL):
            return c["val"]

    res = _get_auto(_LOADING_URL, {"vin": frame, "part": ""})
    if "_err" in res:
        # Jangan cache kegagalan (token bisa segera di-refresh).
        return {"found": False, "frame_number": frame,
                "_err": res["_err"], "message": res.get("message")}

    parts = _parse_loading_rows(res.get("data") or [])
    # Guard truncation: respons kecil bisa karena (a) TERPOTONG (glitch — count berubah
    # bila di-fetch lagi) atau (b) BOM unit memang kecil (trailer/light — count STABIL).
    # Pakai STABILITAS, bukan ambang absolut: retry; bila dua fetch berturut sama → itu
    # angka asli (terima & cache walau <100). Ambil yg TERBANYAK; tandai partial hanya
    # bila masih kecil DAN tak stabil (kemungkinan terpotong) → jangan cache.
    confirmed = False
    if len(parts) < _LL_MIN_EXPECTED:
        prev = len(parts)
        for _ in range(2):
            res2 = _get_auto(_LOADING_URL, {"vin": frame, "part": ""})
            if "_err" in res2:
                break
            p2 = _parse_loading_rows(res2.get("data") or [])
            if len(p2) > len(parts):
                parts = p2
            if len(p2) == prev:           # dua fetch berturut sama → stabil (BOM asli kecil)
                confirmed = True
                break
            prev = len(p2)

    if not parts:
        # 0 part di semua percobaan: bukan 'found' yang berguna — jangan klaim found:True.
        return {"found": False, "frame_number": frame, "_err": "empty"}

    partial = (len(parts) < _LL_MIN_EXPECTED) and not confirmed
    val = {"found": True, "frame_number": frame,
           "jumlah_part": len(parts), "parts": parts}
    if partial:
        # Tandai & JANGAN cache — biar panggilan berikut coba lagi dapat data penuh.
        val["partial"] = True
        return val
    with _lock:
        _cache[frame] = {"at": time.monotonic(), "val": val}
    return val


_rev_cache: dict[str, dict] = {}  # pn -> {"at", "val"} (reverse lookup, statis)

# Nama Inggris RESMI EPC per-PN (home/match/part). PN→nama statis global → cache panjang.
# Dipakai utk MENERJEMAHKAN part Loading List yg namanya cuma China (tak ada di katalog
# lokal). Sumber: EPC sendiri (bukan terjemahan karangan). Ekspor kamus penuh (trans/export)
# terkunci utk akun ini, tapi lookup per-PN ini tidak.
_EN_TTL = 86400.0
_en_cache: dict[str, str | None] = {}  # pn -> en_name (None bila tak ada)
_en_at: dict[str, float] = {}


def _english_one(pn: str) -> str | None:
    res = _get_auto(_MATCH_URL, {"t": "global", "k": pn})
    if "_err" in res:
        return None
    rows = res.get("data") or []
    exact = next((x for x in rows if (x.get("code") or "").upper() == pn), None)
    nm = (exact or (rows[0] if rows else {})).get("name") or ""
    return " ".join(nm.split()) or None


def english_names(pns) -> dict[str, str]:
    """{PN: nama Inggris resmi EPC} utk daftar PN (paralel + cache). PN tanpa nama
    dilewati. Aman dipanggil dgn banyak PN — yg ter-cache tak di-fetch ulang."""
    want = list(dict.fromkeys((str(p).strip().upper() for p in pns if p)))
    out: dict[str, str] = {}
    need: list[str] = []
    now = time.monotonic()
    with _lock:
        for pn in want:
            if pn in _en_cache and (now - _en_at.get(pn, 0) < _EN_TTL):
                if _en_cache[pn]:
                    out[pn] = _en_cache[pn]
            else:
                need.append(pn)
    if need:
        with ThreadPoolExecutor(max_workers=_ATLAS_WORKERS) as ex:
            results = list(ex.map(lambda p: (p, _english_one(p)), need))
        with _lock:
            t = time.monotonic()
            for pn, en in results:
                _en_cache[pn] = en
                _en_at[pn] = t
                if en:
                    out[pn] = en
    return out


# Kamus CN→EN dari KATALOG EPC sendiri (data/epc_dict/cn_en.json, dibangun dari
# field name+originalName Atlas). Untuk menerjemahkan nama part Loading List yg
# China-saja TANPA panggilan jaringan. Dibaca segar (mtime) → bisa diperbarui live.
_cn_en = {"mtime": None, "map": {}, "keys": []}
_cn_en_lock = threading.Lock()


def _load_cn_en() -> tuple[dict, list]:
    p = get_settings().data_path / "epc_dict" / "cn_en.json"
    try:
        mt = p.stat().st_mtime
        with _cn_en_lock:
            if _cn_en["mtime"] != mt:
                m = json.loads(p.read_text(encoding="utf-8")) or {}
                # kunci diurut dari TERPANJANG → cocokkan istilah paling spesifik dulu.
                _cn_en.update(mtime=mt, map=m,
                              keys=sorted(m.keys(), key=len, reverse=True))
            return _cn_en["map"], _cn_en["keys"]
    except Exception:
        return _cn_en["map"], _cn_en["keys"]


def translate_cn(name: str) -> str | None:
    """Terjemahkan nama part China → Inggris pakai kamus katalog EPC. Exact dulu,
    lalu cocokkan kunci TERPANJANG yang jadi substring (tangani sufiks ukuran, mis.
    '塑料紧固带A5*280' → kunci '塑料紧固带' → 'plastic fastening belt'). None bila tak ada."""
    name = " ".join((name or "").split())
    if not name:
        return None
    m, keys = _load_cn_en()
    if name in m:
        return m[name]
    for k in keys:  # sudah urut terpanjang
        if k in name:
            return m[k]
    return None


def reverse_part(pn: str) -> dict:
    """REVERSE lookup global: dari Part Number → daftar MODEL kendaraan Sinotruk
    yang memakainya (sumber EPC resmi, lintas semua model — bukan cuma katalog lokal).

    Sukses → {found:True, part_number, nama, jumlah_model, jumlah_entri, model:[...]}.
    PN dikenal tapi tak terpetakan → found:False + alasan. PN tak ada → found:False
    + kandidat (PN mirip). Token mati → _err token_expired (pemanggil tangani)."""
    pn = (pn or "").strip().upper()
    if not pn:
        return {"found": False, "_err": "input"}

    with _lock:
        c = _rev_cache.get(pn)
        if c and (time.monotonic() - c["at"] < _CACHE_TTL):
            return c["val"]

    # 1) match: validasi PN ada di EPC + ambil nama Inggris resmi.
    m = _get_auto(_MATCH_URL, {"t": "global", "k": pn})
    if "_err" in m:
        return {"found": False, "part_number": pn, "_err": m["_err"]}
    matches = m.get("data") or []
    exact = next((x for x in matches if (x.get("code") or "").upper() == pn), None)
    if not exact:
        return {"found": False, "part_number": pn,
                "kandidat": [{"pn": x.get("code"), "nama": x.get("name")}
                             for x in matches[:10]]}

    # 2) reverse: daftar kendaraan/model yang memakai PN itu.
    rv = _get_auto(_REVERSE_URL, {"t": "global", "v": pn, "k": pn})
    if "_err" in rv:
        return {"found": False, "part_number": pn, "_err": rv["_err"]}
    rows = rv.get("data") or []
    models: list[str] = []
    seen = set()
    for r in rows:
        mdl = (r.get("model") or "").strip()
        if mdl and mdl not in seen:
            seen.add(mdl)
            models.append(mdl)

    val = {"found": bool(models), "part_number": pn,
           "nama": exact.get("name") or "",
           "jumlah_model": len(models), "jumlah_entri": len(rows),
           "model": models}
    with _lock:
        _rev_cache[pn] = {"at": time.monotonic(), "val": val}
    return val


# ===========================================================================
# PARTS ATLAS (tree walk) — PART AUS presisi per-VIN
# ===========================================================================
# Loading List (loading_list) berhenti di level ASSEMBLY: kampas rem terbungkus
# di '制动器总成/brake assembly', tak muncul sbg item. Parts Atlas EPC menguraikan
# tiap assembly sampai komponen aus. Walk: root(frameNo) → module(kode kategori)
# → node (rekursi selama leaf=false) → item (part nyata: code, name, originalName
# CN, amount, weight, partAlternates=supersession). Banyak HTTP call ke server
# China → kita batasi modul (default poros depan+belakang) + budget node + cache.
#
# Kategori → posisi poros (aturan domain user): part di module poros itu mewakili
# posisinya. CDQ (06 Driven axle/从动桥) = DEPAN; QDQ (07 Drive axle/驱动桥) = BELAKANG.
ATLAS_POSISI = {"CDQ": "depan", "QDQ": "belakang"}

# Cache walk MENTAH per (frame, modul) — LEPAS dari kata kunci. Walk EPC identik
# apa pun kata kuncinya (filter kata kunci hanya di akhir, in-memory), jadi query
# ke-2 dst utk unit yg sama → instan tanpa walk ulang.
_atlas_raw_cache: dict[str, dict] = {}  # "frame|MOD1,MOD2" -> {"at", "val"}
_ATLAS_MAX_NODES = 600              # plafon node per panggilan (jaga2 walk liar)
# Walk = puluhan HTTP call ke server China lambat (~1.5-1.8s/call) → JALANKAN
# PARALEL per level (node sibling independen). Pool kecil agar tak dianggap abuse.
_ATLAS_WORKERS = 10


# errbox = [bool] opsional: di-set True bila ADA sub-call yang ERROR (network/token/api)
# di tengah walk. Penting agar walk yg TERDEGRADASI (sebagian call gagal) TIDAK
# disimpulkan sbg 'part tidak ada' & TIDAK di-cache — beda dgn 'sah-sah kosong'.
def _atlas_get_list(url: str, params: dict, errbox: list | None = None) -> list:
    res = _get_auto(url, params)
    if "_err" in res:
        if errbox is not None:
            errbox[0] = True
        return []
    d = res.get("data")
    return d if isinstance(d, list) else []


def _atlas_root(frame: str) -> dict:
    """root Parts Atlas utk frame → {rootId, orderNo} atau {_err}."""
    res = _get_auto(_ATLAS_ROOT_URL, {"type": "frameNo", "code": frame})
    if "_err" in res:
        return res
    d = res.get("data") or {}
    roots = d.get("partRoots") if isinstance(d, dict) else None
    if not isinstance(roots, list) or not roots or not isinstance(roots[0], dict):
        return {"_err": "not_found"}
    return {"rootId": roots[0].get("id"), "orderNo": d.get("orderNo")}


def _atlas_items(frame: str, root_id, part_list_id, part_id, code,
                 errbox: list | None = None) -> list:
    """Daftar PART nyata di satu node (leaf)."""
    res = _get_auto(_ATLAS_ITEM_URL, {
        "id": part_list_id, "partId": part_id, "parentId": root_id,
        "rootId": root_id, "partCode": code, "type": "frameNo",
        "isSearch": "false", "vin": frame,
    })
    if "_err" in res:
        if errbox is not None:
            errbox[0] = True
        return []
    d = res.get("data")
    return (d.get("items") or []) if isinstance(d, dict) else []


def _atlas_children(frame: str, root_id, module: str, part_id,
                    errbox: list | None = None) -> list:
    """Sub-node di bawah satu node assembly."""
    return _atlas_get_list(_ATLAS_NODE_URL, {
        "partId": part_id, "type": "frameNo", "moduleType": module,
        "rootId": root_id, "vin": frame, "cjh": frame,
    }, errbox)


def _atlas_item_row(p: dict, module: str) -> dict | None:
    """Item EPC mentah → baris ternormalisasi (TANPA filter kata kunci). None bila tak ber-PN."""
    pn = str(p.get("code") or "").strip().upper()
    if not pn:
        return None
    # Nama EPC kadang memuat newline/spasi ganda → rapikan jadi satu baris.
    name_en = " ".join(str(p.get("name") or "").split())
    name_cn = " ".join(str(p.get("originalName") or "").split())
    # PENGGANTI/SUPERSESI: partAlternates = beforeTh(PN lama)→afterTh(PN baru).
    alt: list[dict] = []
    seen_a: set = set()
    for a in (p.get("partAlternates") or []):
        after = str(a.get("afterTh") or "").strip().upper()
        if after and after != pn and after not in seen_a:
            seen_a.add(after)
            alt.append({"pn": after,
                        "nama": " ".join(str(a.get("afterName") or "").split()),
                        "nama_cn": " ".join(str(a.get("originalAfterName") or "").split())})
    return {"pn": pn, "nama": name_en, "nama_cn": name_cn, "qty": p.get("amount"),
            "posisi": ATLAS_POSISI.get(module), "modul": module,
            "pengganti": alt, "berat": p.get("weight")}


def _atlas_collect(frame: str, modules: tuple[str, ...]) -> dict:
    """Walk Atlas PARALEL & kumpulkan SEMUA item (lepas kata kunci) utk (frame,modul).
    Di-cache per (frame,modul) → query kata-kunci berikutnya untuk unit sama instan.
    Walk per-level dengan thread pool (node sibling independen) → potong wall-time.
    {found, root_id, order_no, items:[row...], incomplete} atau {_err}."""
    ckey = frame + "|" + ",".join(sorted(modules))
    with _lock:
        c = _atlas_raw_cache.get(ckey)
        if c and (time.monotonic() - c["at"] < _CACHE_TTL):
            return c["val"]

    r = _atlas_root(frame)
    if "_err" in r:
        return {"_err": r["_err"]}
    root_id = r["rootId"]

    items: list[dict] = []
    seen_nodes: set = set()
    budget = [_ATLAS_MAX_NODES]
    errbox = [False]
    wlock = threading.Lock()  # lindungi items/seen/budget antar-thread

    def _process(task: tuple) -> list:
        """Proses 1 node: ambil itemnya + (bila bukan leaf) kembalikan anak (module, child, depth)."""
        module, node, depth = task
        if depth > 6:
            return []
        nid, plid = node.get("id"), node.get("partListId")
        with wlock:
            if (nid, plid) in seen_nodes or budget[0] <= 0:
                return []
            seen_nodes.add((nid, plid))
            budget[0] -= 1
        for p in _atlas_items(frame, root_id, plid, nid, node.get("code"), errbox):
            row = _atlas_item_row(p, module)
            if row:
                with wlock:
                    items.append(row)
        if not node.get("leaf"):
            kids = _atlas_children(frame, root_id, module, nid, errbox)
            return [(module, ch, depth + 1) for ch in kids]
        return []

    # Frontier awal: node teratas tiap modul.
    frontier: list = []
    for module in modules:
        for top in _atlas_get_list(_ATLAS_MODULE_URL,
                                   {"type": "frameNo", "module": module,
                                    "rootId": root_id, "cjh": frame}, errbox):
            frontier.append((module, top, 0))

    # Pool TERSATURASI: tiap node yg menemukan anak LANGSUNG submit anaknya (bukan
    # menunggu level selesai) → pohon dalam/sempit tetap jalan paralel penuh.
    # Worker tak pernah block menunggu future (fire-and-forget) → tak ada deadlock.
    pending = [0]
    cnt_lock = threading.Lock()
    done = threading.Event()
    if not frontier:
        done.set()

    with ThreadPoolExecutor(max_workers=_ATLAS_WORKERS) as ex:
        def _submit(task):
            with cnt_lock:
                pending[0] += 1
            ex.submit(_run, task)

        def _run(task):
            try:
                for child in _process(task):
                    _submit(child)
            finally:
                with cnt_lock:
                    pending[0] -= 1
                    if pending[0] == 0:
                        done.set()

        for t in frontier:
            _submit(t)
        done.wait()

    incomplete = errbox[0] or budget[0] <= 0
    val = {"found": True, "root_id": root_id, "order_no": r.get("orderNo"),
           "items": items, "incomplete": incomplete,
           "terpotong": budget[0] <= 0, "node_dijelajah": _ATLAS_MAX_NODES - budget[0]}
    # JANGAN cache walk terdegradasi/terpotong (bisa kurang).
    if not incomplete:
        with _lock:
            _atlas_raw_cache[ckey] = {"at": time.monotonic(), "val": val}
        _grow_cn_en(items)  # kamus CN→EN tumbuh sendiri dari tiap walk (grounded EPC)
    return val


_HANZI_RE = re.compile(r"[一-鿿]")


def _grow_cn_en(items: list) -> None:
    """Tambah pasangan CN→EN BARU dari item Atlas ke data/epc_dict/cn_en.json
    (best-effort). Membuat kamus terjemahan makin lengkap seiring unit yang dibuka."""
    try:
        pairs = {}
        for it in items:
            cn = " ".join(str(it.get("nama_cn") or "").split())
            en = " ".join(str(it.get("nama") or "").split())
            if cn and en and re.search(r"[a-zA-Z]", en) and not _HANZI_RE.search(en):
                pairs.setdefault(cn, en)
        if not pairs:
            return
        p = get_settings().data_path / "epc_dict" / "cn_en.json"
        with _cn_en_lock:
            cur = {}
            if p.exists():
                cur = json.loads(p.read_text(encoding="utf-8")) or {}
            new = {k: v for k, v in pairs.items() if k not in cur}
            if new:
                cur.update(new)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
                _cn_en["mtime"] = None  # paksa reload kamus
    except Exception:
        pass


def atlas_find(rangka: str, keywords: list[str],
               modules: tuple[str, ...] = ("CDQ", "QDQ")) -> dict:
    """Cari PART poros presisi per-VIN via Parts Atlas. Walk (paralel + cached per
    unit) dipisah dari filter kata kunci (in-memory) → cepat & query ulang instan.

    keywords: substring (huruf kecil) dicocokkan ke nama Inggris+China+PN item.
    modules : kode kategori yg di-walk (default poros DEPAN(CDQ)+BELAKANG(QDQ)).

    Sukses → {found:bool, frame_number, order_no, jumlah, parts:[{pn, nama, nama_cn,
              qty, posisi, modul, pengganti:[...], berat}], incomplete, terpotong}.
    Gagal  → {found:False, frame_number, _err?}.
    """
    frame = _frame(rangka)
    if not frame:
        return {"found": False, "frame_number": "", "_err": "input"}
    kws = [k.lower() for k in keywords if k]
    if not kws:
        return {"found": False, "frame_number": frame, "_err": "no_keyword"}

    coll = _atlas_collect(frame, modules)
    if "_err" in coll:
        err = coll["_err"]
        if err == "not_found":
            return {"found": False, "frame_number": frame, "_err": "not_found"}
        return {"found": False, "frame_number": frame, "_err": err}

    # Filter kata kunci + agregasi: dedup per (PN, posisi); jumlahkan qty; gabung pengganti.
    agg: dict[tuple, dict] = {}
    for row in coll["items"]:
        hay = (row["nama"] + " " + row["nama_cn"] + " " + row["pn"]).lower()
        if not any(k in hay for k in kws):
            continue
        key = (row["pn"], row["posisi"])
        e = agg.get(key)
        if e is None:
            agg[key] = {**row, "pengganti": list(row["pengganti"])}
        else:
            try:
                e["qty"] = (e["qty"] or 0) + (row["qty"] or 0)
            except Exception:
                pass
            have = {x["pn"] for x in e["pengganti"]}
            for a in row["pengganti"]:
                if a["pn"] not in have:
                    have.add(a["pn"])
                    e["pengganti"].append(a)

    parts = sorted(agg.values(), key=lambda x: (x["posisi"] or "z", x["pn"]))
    return {"found": bool(parts), "frame_number": frame,
            "order_no": coll.get("order_no"), "jumlah": len(parts),
            "parts": parts, "node_dijelajah": coll.get("node_dijelajah"),
            "terpotong": coll.get("terpotong", False),
            "incomplete": coll.get("incomplete", False)}


_asm_list_cache: dict[str, dict] = {}   # frame -> {"at", "val"}


def assembly_list(rangka: str) -> dict:
    """Daftar ASSEMBLY UTAMA satu unit dari nomor rangka (1 panggilan + cache).
    Kabin, gardan depan/tengah/belakang, mesin, transmisi, kopling — tiap baris
    memberi PN assembly NYATA unit itu (bukan sekadar kode model), plus nama
    Inggris & label kategori China. Jauh lebih murah dari walk pohon; ideal untuk
    'kartu spesifikasi' unit yang bisa disilang ke stok/harga lokal.

    Sukses → {found:True, frame_number, jumlah, assemblies:[{pn, nama, kategori_cn,
              tipe_cn}]}. Gagal → {found:False, frame_number, _err?}."""
    frame = _frame(rangka)
    if not frame:
        return {"found": False, "frame_number": "", "_err": "input"}
    with _lock:
        c = _asm_list_cache.get(frame)
        if c and (time.monotonic() - c["at"] < _CACHE_TTL):
            return c["val"]

    res = _get_auto(_ASSEMBLY_URL, {"ddh": frame})
    if "_err" in res:
        return {"found": False, "frame_number": frame, "_err": res["_err"]}
    rows = res.get("data")
    if not isinstance(rows, list):
        return {"found": False, "frame_number": frame, "_err": "not_found"}

    seen: set = set()
    asm: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        pn = str(r.get("assemblyCode") or "").strip().upper()
        if not pn or pn in seen:
            continue
        seen.add(pn)
        asm.append({
            "pn": pn,
            "nama": " ".join(str(r.get("assemblyName") or "").split()),
            "kategori_cn": " ".join(str(r.get("remark") or "").split()),
            "tipe_cn": " ".join(str(r.get("assemblyTypeCode") or "").split()),
        })

    val = {"found": bool(asm), "frame_number": frame, "jumlah": len(asm),
           "assemblies": asm}
    if asm:
        with _lock:
            _asm_list_cache[frame] = {"at": time.monotonic(), "val": val}
    return val


# ═══════════════════════════════════════════════════════════════════════
#  POHON KATEGORI per-VIN (STAGED + CACHE) — "mengerti semua kategori EPC"
#  ---------------------------------------------------------------------
#  node(partId=rootId) → SEMUA kategori/assembly tingkat-atas unit (mis. 117).
#  node(partId=<id>)   → turunan (sub-kategori) di bawah satu kategori.
#  _atlas_items(...)   → part nyata di node leaf.
#  Ambil bertahap (top dulu, drill saat diminta) + cache per node → hemat panggilan.
#  _cat_index: nama(EN/CN)→node dari SEMUA kategori yg pernah dilihat (top + dibuka)
#  agar user bisa menyebut kategori mana pun dg NAMA (lintas kedalaman & lintas turn).
# ═══════════════════════════════════════════════════════════════════════
_CAT_TTL = 3600.0
_cat_top_cache: dict[str, dict] = {}    # frame -> {at, val}
_cat_open_cache: dict[str, dict] = {}   # "frame|nodeId" -> {at, val}
_cat_index: dict[str, dict] = {}        # frame -> {norm_name: node}
_cat_lock = threading.Lock()
_CAT_CODE_RE = re.compile(r"^([A-Z0-9][A-Z0-9\-.]+)\s+(.*)$")


def _norm_cat(n: dict) -> dict:
    """Node EPC mentah → kategori ternormalisasi. 'partsm' = '<KODE> <NAMA CN>'
    (mis. 'ZZ-05-1100-0040 驱动桥总成-双桥AH') → pisahkan kode & nama China."""
    partsm = " ".join(str(n.get("partsm") or "").split())
    kode, nama_cn = "", partsm
    m = _CAT_CODE_RE.match(partsm)
    if m:
        kode, nama_cn = m.group(1), m.group(2)
    return {
        "id": n.get("id"),
        "part_list_id": n.get("partListId"),
        "code": n.get("code"),
        "nama": " ".join(str(n.get("name") or "").split()),   # Inggris
        "nama_cn": nama_cn,                                     # China (tanpa kode)
        "kode_kategori": kode,
        "leaf": bool(n.get("leaf")),
    }


def _index_cats(frame: str, cats: list[dict]) -> None:
    with _cat_lock:
        idx = _cat_index.setdefault(frame, {})
        for c in cats:
            for nm in (c.get("nama"), c.get("nama_cn")):
                nm = (nm or "").strip().lower()
                if nm and c.get("id") is not None:
                    idx.setdefault(nm, c)


def _tree_node(frame: str, root_id, part_id) -> dict:
    """Sub-node (kategori/turunan) di bawah part_id. TANPA moduleType → jalan dari
    root generik (bukan per-modul). Dipakai walk kategori penuh."""
    return _get_auto(_ATLAS_NODE_URL, {
        "type": "frameNo", "rootId": root_id, "vin": frame,
        "cjh": frame, "partId": part_id,
    })


def category_top(rangka: str) -> dict:
    """Daftar kategori/assembly TINGKAT-ATAS untuk 1 unit (1 panggilan + cache).
    Sukses → {found, frame_number, order_no, root_id, jumlah, kategori:[norm_cat...]}.
    Gagal  → {found:False, frame_number, _err}."""
    frame = _frame(rangka)
    if not frame:
        return {"found": False, "frame_number": "", "_err": "input"}
    with _cat_lock:
        c = _cat_top_cache.get(frame)
        if c and (time.monotonic() - c["at"] < _CAT_TTL):
            return c["val"]
    r = _atlas_root(frame)
    if "_err" in r:
        return {"found": False, "frame_number": frame, "_err": r["_err"]}
    rid = r["rootId"]
    res = _tree_node(frame, rid, rid)
    if "_err" in res:
        return {"found": False, "frame_number": frame, "_err": res["_err"]}
    cats = [_norm_cat(n) for n in (res.get("data") or []) if n.get("id") is not None]
    _index_cats(frame, cats)
    val = {"found": True, "frame_number": frame, "order_no": r.get("orderNo"),
           "root_id": rid, "jumlah": len(cats), "kategori": cats}
    with _cat_lock:
        _cat_top_cache[frame] = {"at": time.monotonic(), "val": val}
    return val


def category_open(rangka: str, node_id, part_list_id=None, code=None) -> dict:
    """Buka SATU kategori: turunan langsung (sub-kategori) + part langsung di node itu.
    Bertahap (drill 1 level) + cache per node.
    Sukses → {found, frame_number, root_id, sub_kategori:[norm_cat...],
              parts:[{pn, nama, nama_cn, qty, pengganti}]}."""
    frame = _frame(rangka)
    if not frame:
        return {"found": False, "_err": "input"}
    ckey = f"{frame}|{node_id}"
    with _cat_lock:
        c = _cat_open_cache.get(ckey)
        if c and (time.monotonic() - c["at"] < _CAT_TTL):
            return c["val"]
    r = _atlas_root(frame)
    if "_err" in r:
        return {"found": False, "frame_number": frame, "_err": r["_err"]}
    rid = r["rootId"]
    res = _tree_node(frame, rid, node_id)
    subs = ([_norm_cat(n) for n in (res.get("data") or []) if n.get("id") is not None]
            if "_err" not in res else [])
    _index_cats(frame, subs)
    parts: list[dict] = []
    if part_list_id and part_list_id != -1:
        for p in _atlas_items(frame, rid, part_list_id, node_id, code):
            row = _atlas_item_row(p, "")   # module tak relevan di sini → posisi None
            if row:
                parts.append({"pn": row["pn"], "nama": row["nama"],
                              "nama_cn": row["nama_cn"], "qty": row["qty"],
                              "pengganti": row["pengganti"]})
    val = {"found": True, "frame_number": frame, "root_id": rid,
           "jumlah_sub": len(subs), "sub_kategori": subs,
           "jumlah_part": len(parts), "parts": parts}
    with _cat_lock:
        _cat_open_cache[ckey] = {"at": time.monotonic(), "val": val}
    return val


def resolve_category(rangka: str, terms: list[str]) -> list[dict]:
    """Cocokkan istilah (sudah termasuk sinonim/China) ke kategori yg SUDAH ter-index
    (top-level + yg pernah dibuka). Skor = jumlah term jadi substring nama EN/CN.
    Kembalikan kandidat terbaik (maks 5). Panggil category_top dulu agar index terisi."""
    frame = _frame(rangka)
    with _cat_lock:
        idx = list(_cat_index.get(frame, {}).values())
    kws = [t.lower() for t in terms if t and len(t) >= 2]
    if not idx or not kws:
        return []
    scored: list[tuple] = []
    seen: set = set()
    for c in idx:
        cid = c.get("id")
        if cid in seen:
            continue
        hay = (c.get("nama", "") + " " + c.get("nama_cn", "")).lower()
        score = sum(1 for k in kws if k in hay)
        if score:
            seen.add(cid)
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _s, c in scored[:5]]


# ═══════════════════════════════════════════════════════════════════════
#  DEKOMPOSISI ASSEMBLY per-VIN — "isi/turunan dari satu PN assembly"
#  ---------------------------------------------------------------------
#  Sebuah assembly (mis. V型推力杆总成 AZ000052000229) muncul sbg NODE di pohon
#  unit dengan partListId; _atlas_items(node) → komponennya (mis. 11 part: karet/
#  球面销, seal, dudukan…) — PERSIS view "Spare Part List" bergambar di UI EPC.
#  Cara: walk SELURUH node pohon unit SEKALI (cache per frame), lalu cari node yg
#  cocok PN/nama assembly & ambil item-nya. Reuse endpoint per-VIN yg sudah stabil.
# ═══════════════════════════════════════════════════════════════════════
_ASM_TTL = 3600.0
_ASM_BUDGET = 700          # plafon node walk (unit tipikal ~70-300 node)
_ASM_WORKERS = 10
_asm_nodes_cache: dict[str, dict] = {}   # frame -> {at, root_id, nodes:[norm_cat...]}
_asm_lock = threading.Lock()


def _walk_all_nodes(rangka: str) -> dict:
    """Walk SELURUH node pohon unit (paralel, budget, cache per frame). Kembalikan
    {found, root_id, nodes:[norm_cat...]} — tiap node punya id/part_list_id/code/
    nama/nama_cn/leaf. Node ber-part_list_id valid = bisa diurai jadi komponen."""
    frame = _frame(rangka)
    if not frame:
        return {"found": False, "frame_number": "", "_err": "input"}
    with _asm_lock:
        c = _asm_nodes_cache.get(frame)
        if c and (time.monotonic() - c["at"] < _ASM_TTL):
            return c["val"]
    r = _atlas_root(frame)
    if "_err" in r:
        return {"found": False, "frame_number": frame, "_err": r["_err"]}
    rid = r["rootId"]

    nodes: list[dict] = []
    seen: set = set()
    budget = [_ASM_BUDGET]
    wlock = threading.Lock()

    def _children(pid) -> list:
        res = _tree_node(frame, rid, pid)
        return res.get("data") or [] if "_err" not in res else []

    def _process(pid_depth: tuple) -> list:
        pid, depth = pid_depth
        if depth > 7:
            return []
        with wlock:
            if pid in seen or budget[0] <= 0:
                return []
            seen.add(pid)
            budget[0] -= 1
        kids = _children(pid)
        nxt: list = []
        for n in kids:
            if n.get("id") is None:
                continue
            with wlock:
                nodes.append(_norm_cat(n))
            if not n.get("leaf"):
                nxt.append((n["id"], depth + 1))
        return nxt

    pending = [0]
    cnt_lock = threading.Lock()
    done = threading.Event()
    with ThreadPoolExecutor(max_workers=_ASM_WORKERS) as ex:
        def _submit(task):
            with cnt_lock:
                pending[0] += 1
            ex.submit(_run, task)

        def _run(task):
            try:
                for child in _process(task):
                    _submit(child)
            finally:
                with cnt_lock:
                    pending[0] -= 1
                    if pending[0] == 0:
                        done.set()
        _submit((rid, 0))
        done.wait()

    incomplete = budget[0] <= 0
    val = {"found": True, "frame_number": frame, "root_id": rid,
           "nodes": nodes, "incomplete": incomplete}
    if not incomplete:  # jangan cache walk terpotong
        with _asm_lock:
            _asm_nodes_cache[frame] = {"at": time.monotonic(), "val": val}
    return val


def assembly_components(rangka: str, terms: list[str], pn: str = "") -> dict:
    """Uraikan satu ASSEMBLY (per-VIN) → daftar komponennya.

    Match assembly via `pn` (exact di node.code) ATAU `terms` (kata kunci nama EN/CN
    + sinonim). Kembalikan {found, assembly:{pn,nama,nama_cn}, jumlah, components:
    [{pn, nama, nama_cn, qty, pengganti}]}. Hanya node ber-part_list_id valid yg
    bisa diurai."""
    walk = _walk_all_nodes(rangka)
    if not walk.get("found"):
        return {"found": False, "frame_number": walk.get("frame_number"),
                "_err": walk.get("_err"), "incomplete": walk.get("incomplete")}
    frame = walk["frame_number"]
    rid = walk["root_id"]
    nodes = [n for n in walk["nodes"] if n.get("part_list_id") and n["part_list_id"] != -1]

    target = None
    pnu = (pn or "").strip().upper()
    if pnu:
        target = next((n for n in nodes if (n.get("code") or "").upper() == pnu), None)
    if target is None:
        kws = [t.lower() for t in terms if t and len(t) >= 2]
        scored: list[tuple] = []
        for n in nodes:
            hay = ((n.get("nama") or "") + " " + (n.get("nama_cn") or "")).lower()
            score = sum(1 for k in kws if k in hay)
            if score:
                scored.append((score, n))
        scored.sort(key=lambda x: -x[0])
        target = scored[0][1] if scored else None

    if target is None:
        return {"found": False, "frame_number": frame,
                "error": "assembly tak ditemukan di pohon unit ini",
                "incomplete": walk.get("incomplete")}

    items = _atlas_items(frame, rid, target["part_list_id"], target["id"], target.get("code"))
    comps: list[dict] = []
    for p in items:
        row = _atlas_item_row(p, "")
        if row:
            comps.append({"pn": row["pn"], "nama": row["nama"], "nama_cn": row["nama_cn"],
                          "qty": row["qty"], "pengganti": row["pengganti"]})
    return {
        "found": True, "frame_number": frame,
        "assembly": {"pn": target.get("code"), "nama": target.get("nama"),
                     "nama_cn": target.get("nama_cn"), "kode": target.get("kode_kategori")},
        "jumlah": len(comps), "components": comps,
        "incomplete": walk.get("incomplete"),
    }


def atlas_find_in_tree(rangka: str, keywords: list[str], max_nodes: int = 12) -> dict:
    """Cari NODE pohon unit (SEMUA modul/grup) yang NAMANYA cocok kata kunci, lalu
    URAIKAN tiap node jadi komponennya. Menangkap ELEMENT/komponen yang tersembunyi
    DI DALAM assembly di luar modul walk domain — kasus nyata: query 'filter' via
    modul FDJ hanya memberi assembly 'air filter' & element terpasang; padahal
    safety/main element (Mann-Hummel) dan element varian (Parker) ada di node
    'Double-element air filter assembly'/'Fuel coarse filter' pada grup intake/fuel
    supply pohon utama. Per-VIN, memakai cache _walk_all_nodes yang sama dgn
    assembly_components (1 jam) → murah setelah walk pertama.

    Hanya item yang COCOK kata kunci yang dikembalikan (node yang cocok bisa berisi
    komponen lain yang tak relevan). Tiap item membawa 'dari_assembly' = node induk.
    """
    walk = _walk_all_nodes(rangka)
    if not walk.get("found"):
        return {"found": False, "frame_number": walk.get("frame_number"),
                "_err": walk.get("_err")}
    frame, rid = walk["frame_number"], walk["root_id"]
    kws = [k.lower() for k in keywords if k]
    if not kws:
        return {"found": False, "frame_number": frame, "_err": "no_keyword"}

    nodes = [n for n in walk["nodes"] if n.get("part_list_id") and n["part_list_id"] != -1]
    matched: list[dict] = []
    for n in nodes:
        hay = ((n.get("nama") or "") + " " + (n.get("nama_cn") or "") + " "
               + (n.get("code") or "")).lower()
        if any(k in hay for k in kws):
            matched.append(n)
    # Leaf dulu — node leaf = view 'Spare Part List' berisi komponen nyata;
    # non-leaf umumnya cuma wadah. Urutan kedua by code agar deterministik.
    matched.sort(key=lambda n: (not n.get("leaf"), str(n.get("code") or "")))
    matched = matched[:max_nodes]

    parts: list[dict] = []
    plock = threading.Lock()
    errbox = [False]

    def _open(n: dict) -> None:
        for p in _atlas_items(frame, rid, n["part_list_id"], n["id"], n.get("code"), errbox):
            row = _atlas_item_row(p, "")
            if not row:
                continue
            hay = (row["nama"] + " " + row["nama_cn"] + " " + row["pn"]).lower()
            if not any(k in hay for k in kws):
                continue
            row["dari_assembly"] = {"pn": n.get("code"),
                                    "nama": n.get("nama") or n.get("nama_cn")}
            with plock:
                parts.append(row)

    if matched:
        with ThreadPoolExecutor(max_workers=min(6, len(matched))) as ex:
            list(ex.map(_open, matched))

    parts.sort(key=lambda x: (str(x.get("dari_assembly", {}).get("pn") or ""), x["pn"]))
    return {"found": bool(parts), "frame_number": frame, "jumlah": len(parts),
            "parts": parts, "jumlah_node_cocok": len(matched),
            "incomplete": walk.get("incomplete") or errbox[0]}
