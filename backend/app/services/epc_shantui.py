"""
Service EPC SHANTUI (山推EPC, epc.shantui.com) — katalog part ALAT BERAT Shantui
(excavator, bulldozer, loader, roller, grader, dll) + EXPLODED VIEW 2D/3D.

⭐ Penemuan 2026-08-10: portal ini DULU terlindung anti-bot Ruishu (瑞数, HTTP polos
selalu 412). Anti-bot itu SUDAH HILANG — kini HTTP polos biasa persis Sinotruk/SIMS.
Login = RSA(password) + CAPTCHA 4-huruf; auth berikutnya header ``token: Bearer <hex>``.

Alur katalog (identik pola Sinotruk EPC 7001):
  1. product/all?type=model      → POHON PENUH: 15 kategori → children = MODEL
     (tiap model {id=ROOTID, code=rootCode "600XX-00-000NN", name, leaf:false}).
  2. part/tree/module?type=model&rootId=<id>&partId=<id>  → assembly level-atas
     (partId=<assemblyId> utk turun 1 level).
  3. part/tree/item?partId=<>&parentId=<>&rootId=<>&type=model → {items[], d2s, d3s}
     item = {code(=PN), name, ballNum(balon), amount(qty), unit}; d2s=SVG 2D, d3s=.pvz 3D.
  4. Cari PN: POST home/match/part/codeitem {k:<PN>, t:"model", v:<rootCode>} (per-model)
     atau {k:<PN>, t:"global", isVehicle:false, productCodes:[...]} (semua kategori).
  5. File gambar: GET file/<nama>  (Referer+UA+token) → SVG (d2s) / octet-stream (d3s .pvz).
     Nama file 2D ber-akhiran ".EN.svg" = versi INGGRIS (CREO 2D, PTC).

Token disimpan di ``data/shantui_token.txt`` (isi 'Bearer xxx' atau cuma 'xxx').
TIDAK ada SSO/bridge seperti Weichai → refresh butuh CAPTCHA (lihat shantui_login.py,
dijalankan admin). Saat token kedaluwarsa (110003/110025) asisten menjawab JUJUR minta
admin me-refresh — TIDAK mengarang 'part tidak ada'.
"""
from __future__ import annotations

import re
import threading
import time
import urllib.parse as _up

import requests
import urllib3

from ..core.config import get_settings
from .cache_util import CacheTTL

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_BASE = "https://epc.shantui.com/api/rest"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120 Safari/537.36")
_REFERER = "https://epc.shantui.com/"

_TREE_TTL = 3600.0        # pohon model per-kategori statis; cache ringan
_ASM_TTL = 1800.0
_WORKERS = 8
_lock = threading.Lock()
# _tree_cache praktis berkunci tunggal ('__all__'), tapi tetap diberi pagar agar
# kunci baru di kemudian hari tak diam-diam jadi kebocoran (lihat cache_util).
_tree_cache = CacheTTL("shantui.tree", _TREE_TTL, 32)   # kategori-code -> {at, models}
_asm_cache = CacheTTL("shantui.asm", _ASM_TTL, 256)     # rootId -> {at, assemblies}

# Kode error Shantui yang menandakan TOKEN/SESI kedaluwarsa vs tak berhak.
_ERR_EXPIRED = {"110003"}             # 登录失效 (login expired)
_ERR_NOAUTH = {"110025"}             # 没有权限 (no permission / token hilang)

# Kategori alat berat: code (utk stvin/models & productCodes) ↔ id (node product/all)
# ↔ nama EN. Statis (jarang berubah); tetap di-cross-check dari product/all bila perlu.
KATEGORI = {
    "1": ("bulldozer", 1, "推土机"),
    "2": ("excavator", 3, "挖掘机"),
    "3": ("loader", 5, "装载机"),
    "4": ("roller", 7, "压路机"),
    "5": ("grader", 9, "平地机"),
    "6": ("milling machine", 11, "铣刨机"),
    "7": ("paver", 13, "摊铺机"),
    "8": ("pipelayer", 15, "吊管机"),
    "9": ("push rake", 17, "推耙机"),
    "10": ("dozer-loader", 19, "推装机"),
    "11": ("skid steer loader", 21, "滑移装载机"),
    "12": ("backhoe loader", 23, "挖掘装载机"),
    "13": ("refuse compactor", 25, "垃圾压实机"),
    "15": ("cold recycler", 29, "冷再生机"),
}
_ALL_PRODUCT_CODES = [str(i) for i in range(1, 17)]


def _kode_model(code: str) -> bool:
    r"""Apakah `code` sebuah kode MODEL (bukan node kategori)?

    ⛔⛔ Dulu ini regex `^\d{4,6}-` — pola rootCode EXCAVATOR ('60070-00-00001')
    saja. Kelas unit lain memakai bentuk yang sama sekali berbeda dan karenanya
    dibuang DIAM-DIAM dari pohon model: '16Y-00-00001' (dozer SD22), 'RA10AB6A'
    (roller, TANPA tanda hubung), 'L36-B3' (loader), 'DA17AB3A'/'DH13-B3'
    (dozer), 'GA15BB6A' (grader). Pengukuran 2026-08-13: pohon mentah 332 model /
    14 kategori, setelah filter tersisa 139 → **193 model tak bisa disentuh**
    keempat tool Shantui. Gejalanya menyesatkan karena tak ada error sama sekali:
    asisten hanya menjawab "tipe itu tidak ada di katalog EPC Shantui" (kejadian
    nyata: 'kunci/ignition SD22', 1 Sep 2026, dua kali kepada admin).

    Predikatnya kini SIFAT NEGATIF, bukan pola positif yang selalu ketinggalan:
    model = punya kode, dan kodenya BUKAN kode kategori. Kode kategori satu-satunya
    bentuk yang perlu dikecualikan — angka polos '1'..'15' (lihat KATEGORI)."""
    c = (code or "").strip().upper()
    return bool(c) and c not in KATEGORI and not c.isdigit()

# Kode subsistem (segmen YY di '600XX-YY-NNNNN') → label EN. Dipakai memfilter/menamai
# assembly (nama aslinya Mandarin). Diverifikasi pada SE75 (excavator).
SUBSISTEM = {
    "01": "spare parts / tools", "03": "engine mounting", "04": "cooling system",
    "05": "fuel system", "06": "muffler", "07": "intake", "21": "swing motor",
    "22": "swivel joint", "23": "swing bearing", "24": "main pump", "25": "main valve",
    "31": "left control box", "32": "right control box", "33": "cab", "34": "travel control",
    "35": "control lever", "36": "throttle control", "37": "seat", "38": "air conditioner",
    "3A": "floor frame", "3C": "shock absorber", "41": "travel motor", "42": "track roller",
    "43": "carrier roller", "44": "track adjuster", "45": "track", "51": "swing platform",
    "52": "track frame", "53": "dozer blade", "61": "boom", "62": "arm", "63": "bucket",
    "65": "lubrication", "71": "counterweight", "72": "side door", "73": "fender",
    "74": "frame / partition", "76": "top cover", "77": "mirror / toolbox",
    "78": "hydraulic tank", "7A": "nameplate", "7B": "graphic decal", "81": "pilot piping",
    "83": "platform piping", "84": "pump-side piping", "85": "boom piping",
    "87": "track-frame piping", "89": "breaker piping", "8A": "control/drain piping",
    "8B": "inlet/bypass piping", "8C": "cooling piping", "8D": "arm piping",
    "91": "battery / electrical",
}


# ── Token ─────────────────────────────────────────────────────────────────────

def _token() -> str:
    """Token Shantui dari data/shantui_token.txt ('Bearer xxx' atau 'xxx').
    '' bila file tak ada/kosong. Header dipakai apa adanya (dgn 'Bearer ')."""
    try:
        p = get_settings().data_path / "shantui_token.txt"
        if p.exists():
            t = p.read_text(encoding="utf-8").strip()
            if t and not t.lower().startswith("bearer"):
                t = "Bearer " + t
            return t
    except Exception:
        pass
    return ""


def save_token(token: str) -> None:
    token = (token or "").strip()
    if token and not token.lower().startswith("bearer"):
        token = "Bearer " + token
    get_settings().data_path.joinpath("shantui_token.txt").write_text(token, encoding="utf-8")


def _headers(extra: dict | None = None) -> dict:
    h = {"token": _token(), "Accept": "application/json, text/plain, */*",
         "User-Agent": _UA, "Referer": _REFERER}
    if extra:
        h.update(extra)
    return h


# ── HTTP inti ────────────────────────────────────────────────────────────────

def _classify(j) -> dict:
    """Normalisasi respons Shantui → {'data':..} sukses, atau {'_err':..}.
    ⛔ kode 110003/110025 = token/akses (BUKAN 'part tidak ada') → 'token_expired'."""
    if not isinstance(j, dict):
        return {"_err": "api", "message": "bad_json"}
    if j.get("success"):
        return {"data": j.get("data")}
    code = str(j.get("code") or "")
    if code in _ERR_EXPIRED or code in _ERR_NOAUTH:
        return {"_err": "token_expired", "message": j.get("message")}
    return {"_err": "api", "message": j.get("message"), "code": code}


_refresh_lock = threading.Lock()


def _get(path: str, params: dict | None = None, timeout: float = 25.0,
         _retry: bool = True) -> dict:
    if not _token():
        return _get_after_refresh("GET", path, params, None, timeout) if _retry else {"_err": "no_token"}
    try:
        r = requests.get(_BASE + path, params=params or {}, headers=_headers(),
                         timeout=timeout, verify=False)
    except Exception:
        return {"_err": "network"}
    try:
        res = _classify(r.json())
    except Exception:
        return {"_err": "network"}     # 500-HTML dll = pabrik macet (spt Sinotruk)
    if res.get("_err") == "token_expired" and _retry:
        return _get_after_refresh("GET", path, params, None, timeout)
    return res


def _post(path: str, body: dict, timeout: float = 25.0, _retry: bool = True) -> dict:
    if not _token():
        return _get_after_refresh("POST", path, None, body, timeout) if _retry else {"_err": "no_token"}
    try:
        r = requests.post(_BASE + path, json=body, headers=_headers(),
                          timeout=timeout, verify=False)
    except Exception:
        return {"_err": "network"}
    try:
        res = _classify(r.json())
    except Exception:
        return {"_err": "network"}
    if res.get("_err") == "token_expired" and _retry:
        return _get_after_refresh("POST", path, None, body, timeout)
    return res


def _get_after_refresh(method, path, params, body, timeout) -> dict:
    """Token hilang/kedaluwarsa → coba refresh OTOMATIS sekali, lalu ulang panggilan
    (tanpa retry lagi). Bila refresh gagal (kredensial/captcha tak tersedia) →
    'token_expired' jujur agar asisten minta admin refresh manual."""
    with _refresh_lock:
        if not refresh_token():
            return {"_err": "token_expired"}
    if method == "GET":
        return _get(path, params, timeout, _retry=False)
    return _post(path, body, timeout, _retry=False)


# ── Pohon model / varian ─────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[\s\-_]", "", (s or "").upper())


# Kode model yang "manusiawi" (huruf lalu angka: SD22, L36-B3, RA10AB6A, GA15BB6A)
# vs rootCode teknis excavator ('60070-00-00001') yang tak pernah diucapkan user.
_KODE_MANUSIAWI_RE = re.compile(r"^[A-Z]{1,3}\d")


def _label_model(code: str, nama: str, mesin: str | None = None) -> str:
    """Nama tipe yang DITAMPILKAN & DICARI — memuat kode model bila `name` tak memuatnya.

    Pengukuran 2026-09-04 pada pohon asli (415 model): **363 model (87%)** punya
    `name` Mandarin TANPA kode modelnya — dozer SD22 = {code:'SD22',
    name:'液力推土机'}, roller {code:'RA10AB6A', name:'SR10-B6单钢轮压路机',
    machineType:'SR10-B6'}, grader {code:'GA15BB6A', name:'SG15-B6 平地机'}.
    Hanya excavator yang `name`-nya kode pasaran ('SE75-9') dengan `code`
    rootCode teknis. variants()/_resolve() dulu mencocokkan `name` SAJA →
    'SD22' dijawab "tak ada di katalog Shantui" walau node-nya ADA (kejadian
    produksi 1 Sep 2026, dua kali ke admin — dan perbaikan _kode_model
    sebelumnya tak menolong karena masalahnya di pencocokan, bukan di walk).
    Label = kode (bila manusiawi & belum ada di nama) + machineType (bila belum
    ada) + nama; rootCode teknis excavator tak diawali (test & tampilan lama
    'SE75-9' tetap)."""
    nm = (nama or "").strip()
    c = (code or "").strip()
    mt = (mesin or "").strip()
    bagian: list[str] = []
    if c and _KODE_MANUSIAWI_RE.match(c.upper()) and _norm(c) not in _norm(nm):
        bagian.append(c)
    if mt and _norm(mt) not in _norm(nm) and _norm(mt) != _norm(c):
        bagian.append(mt)
    bagian.append(nm or c)
    return " ".join(bagian)


def _identitas_model(m: dict) -> list[str]:
    """Semua sebutan sah satu model: label, nama, kode, machineType (non-kosong)."""
    out: list[str] = []
    for s in (m.get("label"), m.get("nama"), m.get("rootCode"), m.get("mesin")):
        s = (s or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _model_tree() -> dict:
    """product/all?type=model → pohon penuh (kategori→children=model). Cache ringkas
    {'kategori': [...node model...]} per kategori-code. {'_err':..} bila gagal."""
    with _lock:
        c = _tree_cache.get("__all__")
        if c and time.monotonic() - c["at"] < _TREE_TTL:
            return {"data": c["models"]}
    res = _get("/product/all", {"type": "model"})
    if res.get("_err"):
        return res
    data = res.get("data") or []
    models: list[dict] = []

    def walk(n, kat_code, kat_name):
        if isinstance(n, list):
            for x in n:
                walk(x, kat_code, kat_name)
            return
        if not isinstance(n, dict):
            return
        code = str(n.get("code") or "")
        kids = n.get("children") or []
        # node kategori level-atas: code numerik pendek & punya entri KATEGORI
        if code in KATEGORI and not kat_code:
            kn = KATEGORI[code][0]
            for c in kids:
                walk(c, code, kn)
            return
        # node model = punya id + kode yang bukan kode kategori (lihat _kode_model;
        # pola lama hanya mengenali excavator → 193 model kelas lain hilang diam-diam).
        if n.get("id") and _kode_model(code):
            nm = n.get("name") or n.get("title") or ""
            mt = str(n.get("machineType") or "").strip()
            models.append({"id": n.get("id"), "rootCode": code, "nama": nm,
                           "mesin": mt, "label": _label_model(code, nm, mt),
                           "kategori": kat_name, "kategori_code": kat_code,
                           "leaf": n.get("leaf")})
        for c in kids:
            walk(c, kat_code, kat_name)

    walk(data, "", "")
    with _lock:
        _tree_cache["__all__"] = {"at": time.monotonic(), "models": models}
    return {"data": models}


def variants(query: str) -> dict:
    """Semua TIPE unit yang cocok `query` (mis. 'SE75' → 8 tipe SE75-*).
    ⚠️ pencocokan sadar-batas: 'SE75' TIDAK menangkap 'SE750' (model beda kelas).
    {found, query, tipe:[{tipe, rootCode, rootId, kategori}], catatan?} atau {_err}."""
    # Untuk deteksi BATAS, tanda hubung WAJIB dipertahankan ('SE75-9' vs 'SE750'):
    # buang spasi/underscore saja, JANGAN dash.
    q = re.sub(r"[\s_]", "", (query or "").upper())
    if not q:
        return {"found": False, "_err": "input"}
    tree = _model_tree()
    if tree.get("_err"):
        return _mint_gagal(tree["_err"])
    exact, prefixnum = [], []
    for m in tree["data"]:
        # Label memuat kode + machineType + nama (lihat _label_model) — 'SD22'
        # cocok ke node {code:'SD22', name:'液力推土机'}, 'SR10' ke roller
        # {code:'RA10AB6A', machineType:'SR10-B6'}.
        nm = m["label"]
        n = re.sub(r"[\s_]", "", nm.upper())
        if q not in n:
            continue
        # 'SE75' vs 'SE750': char TEPAT setelah query digit → nomor lanjut = model BEDA.
        # '-'/huruf/habis = varian query ('SE75-9', 'SE75-G').
        idx = n.find(q) + len(q)
        next_is_digit = idx < len(n) and n[idx].isdigit()
        row = {"tipe": nm, "rootCode": m["rootCode"], "rootId": m["id"],
               "kategori": m["kategori"]}
        (prefixnum if next_is_digit else exact).append(row)
    exact.sort(key=lambda r: r["tipe"])
    prefixnum.sort(key=lambda r: r["tipe"])
    out = {"found": bool(exact or prefixnum), "query": query,
           "tipe": exact, "jumlah": len(exact)}
    if prefixnum:
        out["tipe_serupa_beda_model"] = prefixnum
        out["catatan"] = (f"{len(prefixnum)} tipe mengandung '{query}' TAPI angka setelahnya "
                          f"lanjut (mis. {prefixnum[0]['tipe']}) → model BERBEDA, bukan varian.")
    if not out["found"]:
        # ── FALLBACK: query itu NOMOR SERI, bukan kode model ────────────────
        # Kasus produksi 1 Sep 2026: user menempel 'CHSD22AFKN1024321' (serial
        # dozer Shantui). Tak ada model bernama itu → asisten jatuh ke tebakan
        # dan akhirnya menyerah, padahal nomor itu MEMUAT nama modelnya (SD22).
        # Dibalik: cari model yang NAMANYA muncul sebagai substring di dalam
        # query. ⛔ Digerbangi >=4 karakter supaya model bernama pendek ('L36')
        # tak menyambar sembarang nomor. Yang TERPANJANG diurutkan duluan:
        # 'SD22F' lebih spesifik dari 'SD22' bila keduanya sama-sama cocok.
        dalam = []
        qq = re.sub(r"[\s_-]", "", q)
        for m in tree["data"]:
            # Kode/machineType/nama mana pun yang muncul di dalam nomor seri.
            # 'CHSD22AFKN1024321' memuat kode 'SD22' — nama node-nya sendiri
            # Mandarin ('液力推土机'), jadi mencocokkan nama saja tak pernah kena.
            cocok = [s for s in (m["rootCode"], m.get("mesin") or "", m["nama"])
                     if len(re.sub(r"[\s_-]", "", s.upper())) >= 4
                     and re.sub(r"[\s_-]", "", s.upper()) in qq]
            if cocok:
                dalam.append({"tipe": m["label"], "rootCode": m["rootCode"],
                              "rootId": m["id"], "kategori": m["kategori"],
                              "cocok_dari": "kode model ditemukan DI DALAM nomor seri: "
                                            + max(cocok, key=len),
                              "_len": max(len(s) for s in cocok)})
        if dalam:
            dalam.sort(key=lambda r: (-r.pop("_len"), r["tipe"]))
            out.update(found=True, tipe=dalam, jumlah=len(dalam),
                       dari_nomor_seri=True,
                       catatan=("'" + str(query) + "' dikenali sebagai NOMOR SERI/PIN, bukan "
                                "kode model: nama model di bawah ditemukan DI DALAM nomor itu. "
                                "⚠️ Ini identifikasi MODEL, bukan BOM per-unit — Shantui tak "
                                "memberi katalog per-PIN untuk akun kita, jadi sampaikan bahwa "
                                "part-nya level MODEL dan mungkin beda antar unit."))
            return out
        out["message"] = f"Tidak ada tipe unit Shantui yang cocok '{query}'."
    return out


def list_models(kategori: str = "") -> dict:
    """Daftar semua tipe pada satu kategori (mis. 'excavator'). Kosong = semua kategori.
    {found, kategori, tipe:[nama...]} atau {_err}."""
    tree = _model_tree()
    if tree.get("_err"):
        return _mint_gagal(tree["_err"])
    kat = (kategori or "").strip().lower()
    rows = tree["data"]
    if kat:
        rows = [m for m in rows if kat in (m["kategori"] or "").lower()]
    names = sorted({m["label"] for m in rows})
    return {"found": bool(names), "kategori": kategori or "semua", "jumlah": len(names),
            "tipe": names}


def _resolve(tipe: str) -> dict | None:
    """Nama tipe (mis. 'SE75-9W1') → node model {rootId, rootCode, nama, kategori}."""
    tree = _model_tree()
    if tree.get("_err"):
        return None
    tn = _norm(tipe)
    best = None
    for m in tree["data"]:
        node = {"rootId": m["id"], "rootCode": m["rootCode"], "nama": m["nama"],
                "label": m.get("label") or m["nama"], "kategori": m["kategori"]}
        # EKSAK ke sebutan mana pun: label ('SD22 液力推土机'), nama, kode ('SD22'),
        # machineType ('SR10-B6') — model boleh mengoper balik salah satunya.
        if tn and tn in {_norm(s) for s in _identitas_model(m)}:
            return node
        if tn and tn in _norm(node["label"]) and best is None:
            best = node
    return best


# ── Assembly / part ──────────────────────────────────────────────────────────

def _subsys(code: str) -> str:
    p = str(code).split("-")
    return p[1] if len(p) > 2 else ""


def _modules(root_id, part_id) -> list[dict]:
    res = _get("/part/tree/module",
               {"type": "model", "rootId": root_id, "partId": part_id})
    d = res.get("data")
    return d if isinstance(d, list) else []


def _item_raw(root_id, part_id, parent_id) -> dict:
    res = _get("/part/tree/item",
               {"partId": part_id, "parentId": parent_id, "rootId": root_id, "type": "model"})
    d = res.get("data")
    return d if isinstance(d, dict) else {}


def top_assemblies(tipe: str) -> dict:
    """Daftar ASSEMBLY level-atas satu tipe + label subsistem EN.
    {found, tipe, rootCode, jumlah, assembly:[{kode, nama, subsistem, subsistem_label, id, leaf}]}"""
    node = _resolve(tipe)
    if node is None:
        tree = _model_tree()
        return _mint_gagal(tree.get("_err") or "not_found", tipe)
    rid = node["rootId"]
    with _lock:
        c = _asm_cache.get(rid)
        if c and time.monotonic() - c["at"] < _ASM_TTL:
            asm = c["assemblies"]
        else:
            asm = None
    if asm is None:
        mods = _modules(rid, rid)
        if not mods:
            return {"found": False, "tipe": node["nama"], "rootCode": node["rootCode"],
                    "jumlah": 0, "assembly": [], "message": "Assembly tidak terbaca (server?)."}
        asm = []
        for m in mods:
            yy = _subsys(m.get("code"))
            asm.append({"kode": m.get("code"), "nama": (m.get("name") or "").strip(),
                        "subsistem": yy, "subsistem_label": SUBSISTEM.get(yy, ""),
                        "id": m.get("id"), "leaf": m.get("leaf")})
        with _lock:
            _asm_cache[rid] = {"at": time.monotonic(), "assemblies": asm}
    return {"found": True, "tipe": node.get("label") or node["nama"],
            "rootCode": node["rootCode"],
            "kategori": node["kategori"], "jumlah": len(asm), "assembly": asm}


def _figure_items(root_id, part_id, parent_id) -> tuple[list[dict], list[str], list[str]]:
    """Isi satu figure: (items[{balon,pn,nama,qty,unit}], d2s[], d3s[])."""
    d = _item_raw(root_id, part_id, parent_id)
    its = d.get("items") or []
    rows = [{"balon": it.get("ballNum"), "pn": it.get("code"),
             "nama": (it.get("name") or "").strip(), "qty": it.get("amount"),
             "unit": it.get("unit")} for it in its if isinstance(it, dict)]
    d2s = [s for s in (d.get("d2s") or []) if isinstance(s, str)]
    d3s = [s for s in (d.get("d3s") or []) if isinstance(s, str)]
    return rows, d2s, d3s


def part_list(tipe: str, subsistem: str = "") -> dict:
    """Daftar PART satu tipe, difilter subsistem (kode YY '03'/'45' atau kata kunci EN
    'engine'/'track'/'boom'). Menelusuri assembly cocok → part di dalamnya.
    {found, tipe, subsistem, figures:[{assembly, kode, subsistem_label, jumlah, items}]}"""
    t = top_assemblies(tipe)
    if not t.get("found"):
        return t
    node = _resolve(tipe)
    rid = node["rootId"]
    sub = (subsistem or "").strip().lower()
    sel = []
    for a in t["assembly"]:
        if not sub:
            sel.append(a)
        elif sub == a["subsistem"].lower() or sub in a["subsistem_label"].lower():
            sel.append(a)
    if not sel:
        return {"found": False, "tipe": t["tipe"], "subsistem": subsistem,
                "message": (f"Tak ada assembly subsistem '{subsistem}' pada {t['tipe']}. "
                            "Coba kode YY (mis. '03'=engine, '45'=track) atau kata kunci EN.")}
    figs = []
    for a in sel[:12]:      # plafon agar responsif
        rows, d2s, d3s = _figure_items(rid, a["id"], rid)
        figs.append({"assembly": a["nama"], "kode": a["kode"],
                     "subsistem_label": a["subsistem_label"], "jumlah_part": len(rows),
                     "ada_gambar_2d": bool(d2s), "ada_gambar_3d": bool(d3s),
                     "items": rows})
    return {"found": True, "tipe": t["tipe"], "rootCode": t["rootCode"],
            "subsistem": subsistem or "semua", "jumlah_figure": len(figs), "figures": figs}


def find_part(pn: str, tipe: str = "") -> dict:
    """Cari NOMOR PART Shantui. tipe kosong = pencarian GLOBAL (semua kategori);
    tipe diisi = dalam satu model (v=rootCode). {found, pn, hasil:[{pn,nama,berat,lwh}]}"""
    p = (pn or "").strip().upper()
    if not p:
        return {"found": False, "_err": "input"}
    if tipe:
        node = _resolve(tipe)
        if node is None:
            return {"found": False, "_err": "not_found", "message": f"Tipe '{tipe}' tak dikenal."}
        res = _post("/home/match/part/codeitem",
                    {"k": p, "t": "model", "v": node["rootCode"]})
    else:
        res = _post("/home/match/part/codeitem",
                    {"k": p, "t": "global", "isVehicle": False,
                     "productCodes": _ALL_PRODUCT_CODES})
    if res.get("_err"):
        return _mint_gagal(res["_err"], pn)
    data = res.get("data") or []
    hasil = [{"pn": x.get("code"), "nama": x.get("name"), "nama_en": x.get("transName"),
              "berat": x.get("weight"), "lwh": x.get("lwh")}
             for x in data if isinstance(x, dict)]
    return {"found": bool(hasil), "pn": p, "lingkup": tipe or "global",
            "jumlah": len(hasil), "hasil": hasil}


# ── Exploded view ────────────────────────────────────────────────────────────

def exploded_figures(tipe: str, pn: str = "", subsistem: str = "") -> dict:
    """Figure exploded-view (2D/3D) untuk satu tipe. Bila `pn` diisi → hanya figure yg
    MEMUAT PN itu (+ balon-nya). {found, tipe, figures:[{assembly, svg, balon?, d3s?, items}]}.
    svg = nama file → unduh via fetch_file()."""
    t = top_assemblies(tipe)
    if not t.get("found"):
        return t
    node = _resolve(tipe)
    rid = node["rootId"]
    pnu = (pn or "").strip().upper()
    sub = (subsistem or "").strip().lower()
    figs = []
    for a in t["assembly"]:
        if sub and not (sub == a["subsistem"].lower() or sub in a["subsistem_label"].lower()):
            continue
        rows, d2s, d3s = _figure_items(rid, a["id"], rid)
        if not d2s:
            continue
        balon = None
        if pnu:
            hit = next((r for r in rows if str(r.get("pn") or "").upper() == pnu), None)
            if not hit:
                continue
            balon = hit.get("balon")
        figs.append({"assembly": a["nama"], "kode": a["kode"],
                     "subsistem_label": a["subsistem_label"], "svg": d2s[0],
                     "pvz_3d": d3s[0] if d3s else None, "balon": balon,
                     "jumlah_item": len(rows),
                     "items_ringkas": [{"balon": r["balon"], "pn": r["pn"], "nama": r["nama"]}
                                       for r in rows]})
        if pnu and len(figs) >= 4:
            break
        if not pnu and len(figs) >= 8:
            break
    if not figs:
        msg = (f"PN {pnu} tak muncul di figure ber-gambar untuk {t['tipe']}."
               if pnu else f"Tak ada figure ber-gambar (subsistem '{subsistem}') untuk {t['tipe']}.")
        return {"found": False, "tipe": t["tipe"], "pn": pnu, "message": msg}
    return {"found": True, "tipe": t["tipe"], "rootCode": t["rootCode"],
            "pn": pnu, "jumlah_figure": len(figs), "figures": figs}


def fetch_file(name: str) -> bytes | None:
    """Unduh FILE EPC Shantui (gambar exploded SVG d2s, atau .pvz 3D d3s) via
    GET /api/rest/file/<nama>. None bila gagal/kosong. Nama di-URL-encode."""
    if not name or not _token():
        return None
    url = _BASE + "/file/" + _up.quote(name)
    try:
        r = requests.get(url, headers=_headers({"Accept": "*/*"}),
                         timeout=45, verify=False)
    except Exception:
        return None
    if r.status_code == 200 and r.content and "json" not in (r.headers.get("Content-Type") or "").lower():
        return r.content
    return None


# ── Login / refresh token ────────────────────────────────────────────────────

def _credentials() -> tuple[str, str]:
    """Kredensial login Shantui dari ENV (SHANTUI_USER/SHANTUI_PASS) atau file
    data/shantui_cred.json {"username","password"}. ('','') bila tak ada — refresh
    otomatis DIMATIKAN (jatuh ke token manual). ⛔ JANGAN hardcode kredensial."""
    import os
    u = os.environ.get("SHANTUI_USER", "")
    p = os.environ.get("SHANTUI_PASS", "")
    if u and p:
        return u, p
    try:
        import json
        fp = get_settings().data_path / "shantui_cred.json"
        if fp.exists():
            d = json.loads(fp.read_text(encoding="utf-8"))
            return d.get("username", ""), d.get("password", "")
    except Exception:
        pass
    return "", ""


_ocr_cache: dict = {}


def _ocr():
    """Instance ddddocr (dibuat sekali; model onnx ~beberapa MB). None bila tak ada."""
    if "ocr" not in _ocr_cache:
        try:
            import ddddocr  # type: ignore
            _ocr_cache["ocr"] = ddddocr.DdddOcr(show_ad=False)
        except Exception:
            _ocr_cache["ocr"] = None
    return _ocr_cache["ocr"]


def _solve_captcha(img: bytes) -> str:
    """Pecahkan CAPTCHA 4-huruf pakai ddddocr → UPPERCASE (server case-insensitive).
    Buang non-alfanumerik (garis-coret sering terbaca jadi karakter). '' bila ddddocr
    tak ada / hasil bukan 4 char (kemungkinan salah — biarkan pemanggil ambil captcha
    baru; login yang memvalidasi, jadi tebakan meleset cukup diulang)."""
    ocr = _ocr()
    if ocr is None:
        return ""
    try:
        code = re.sub(r"[^A-Za-z0-9]", "", (ocr.classification(img) or "")).upper()
    except Exception:
        return ""
    return code if len(code) == 4 else ""


def login(captcha_code: str) -> str:
    """Login SEKALI dengan kode CAPTCHA yang SUDAH dipecahkan (dipakai CLI admin).
    Kembalikan token (dan menyimpannya) atau '' bila gagal. Kredensial dari _credentials()."""
    user, pw = _credentials()
    if not user or not pw or not captcha_code:
        return ""
    try:
        import base64
        from Crypto.PublicKey import RSA
        from Crypto.Cipher import PKCS1_v1_5
        s = requests.Session()
        s.headers["User-Agent"] = _UA
        pk = s.get(_BASE + "/log/public/key", timeout=20, verify=False).text.strip()
        key = RSA.import_key(base64.b64decode(pk))
        enc = base64.b64encode(PKCS1_v1_5.new(key).encrypt(pw.encode())).decode()
        body = {"username": user, "phone": "", "password": enc,
                "code": captcha_code, "source": "PC", "loginType": "PASSWORD"}
        j = s.post(_BASE + "/login/in", json=body, timeout=20, verify=False).json()
        if not j.get("success"):
            return ""
        tok = ((j.get("data") or {}).get("token") or "").strip()
        if tok:
            save_token(tok)
        return tok
    except Exception:
        return ""


_REFRESH_TRIES = 6      # ddddocr meleset kadang (garis-coret) → coba beberapa captcha


def refresh_token() -> str:
    """Refresh token OTOMATIS penuh (kredensial + CAPTCHA via ddddocr). '' bila
    kredensial/ddddocr tak tersedia → asisten jatuh ke pesan 'minta admin refresh'.
    CAPTCHA terikat verifyId=username di server → tiap percobaan fetch captcha SEGAR
    lalu login; login memvalidasi captcha, jadi tebakan meleset cukup diulang
    (≤ _REFRESH_TRIES). ⚠️ akun MAS mungkin SESI-TUNGGAL → refresh menendang sesi lain."""
    user, pw = _credentials()
    if not user or not pw or _ocr() is None:
        return ""
    s = requests.Session()
    s.headers["User-Agent"] = _UA
    for _ in range(_REFRESH_TRIES):
        try:
            img = s.get(_BASE + "/verifyCode/login", params={"verifyId": user},
                        timeout=20, verify=False).content
            code = _solve_captcha(img)
            if not code:
                continue
            tok = login(code)
            if tok:
                return tok
        except Exception:
            continue
    return ""


# ── Kegagalan jujur ──────────────────────────────────────────────────────────

def _mint_gagal(reason: str, konteks: str = "") -> dict:
    """Jawaban JUJUR saat gagal — bukan 'tidak ada'. Bedakan token vs pabrik macet."""
    if reason in ("token_expired", "no_token"):
        return {"found": False, "reason": "token_expired", "gagal_dicek": True,
                "message": ("Token EPC Shantui kedaluwarsa / belum diisi. Minta admin "
                            "me-refresh token Shantui (login ulang di epc.shantui.com). "
                            "⛔ Ini BUKAN berarti data/part tak ada.")}
    if reason == "network":
        return {"found": False, "reason": "shantui_down", "gagal_dicek": True,
                "message": ("Server EPC Shantui sedang tidak menjawab. Coba lagi nanti. "
                            "⛔ Status data belum diketahui — bukan berarti tak ada.")}
    # SISA reason ('api' dari kode balasan tak sukses, 'bad_json', 'not_found'
    # internal, dsb). ⛔⛔ Dulu cabang ini TIDAK memasang `gagal_dicek`, sehingga
    # pemanggilnya — yang menyaring dengan MENYEBUT reason satu per satu — tak
    # mengenalinya sebagai kegagalan dan menjatuhkannya ke jalur "tidak ada".
    # Akibat nyata di produksi (log 1 Sep 2026): pertanyaan 'kunci/ignition SD22'
    # dijawab "katalog EPC Shantui tidak memuat tipe SD22" — klaim tentang DUNIA
    # yang lahir dari kegagalan panggilan. 'api' = kita GAGAL bertanya, bukan
    # pabrik menjawab 'tidak ada'.
    return {"found": False, "reason": reason, "gagal_dicek": True,
            "message": (f"Gagal mengambil data EPC Shantui"
                        f"{(' untuk ' + konteks) if konteks else ''} (sebab: {reason}). "
                        "⛔ Status data BELUM DIKETAHUI — jangan simpulkan tipe/part "
                        "itu tidak ada di katalog Shantui.")}


def circuit_state() -> dict:
    """Diagnosa ringan (dipakai admin/test)."""
    return {"punya_token": bool(_token())}
