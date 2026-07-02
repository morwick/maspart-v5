"""
Service EPC WEICHAI — dekomposisi PART INTERNAL MESIN (Weichai) OTOMATIS per-VIN.

Unit Sinotruk bermesin Weichai (mis. WP12S400E201): part internal mesin ada di portal
TERPISAH epc-cloud.weichai.com. Node engine di EPC Sinotruk cuma link keluar ke sana.
Modul ini menempuh SELURUH jembatan SSO + BOM secara otomatis, cukup dari nomor rangka:

  1. getParam   : GET epc.sinotruk.com:18080/api/rest/weichai/getParam?type=frameNo&code=<frame>
                  (header token Sinotruk — sama dgn epc_bom, auto-refresh) → {param(=parms)}
  2. checkJump  : GET epc-cloud.weichai.com/Api/integration-api/integration/externalepc/
                  checkJumpParams?jumpParams=<parms>  (Authorization: Weichai null, TANPA token)
                  → {accessToken (token Weichai, auto-mint!), serialCode (nomor mesin)}
  3. getOrder   : GET .../business-api/business/etl-install-bom-header/getOrderNumber?
                  serialNumber=<serial>  → {dhhNumber (order), id (=root/roleAId), effDate}
  4. findBomTree: GET .../business-api/business/part/findBomTree?dhhNumber=<order>&dhhDate=<>
                  → root mesin + ~50 GROUP {id, partNumber, partName(EN)}
  5. findBomList: GET .../findBomList?dhhNumber=<order>&dhhId=<groupId>&ypartFlag=false
                  → PART tiap group (nama EN; field children utk nesting)

Token Weichai TIDAK disimpan file — di-mint ulang otomatis tiap bridge (via parms segar).
Cache hasil bridge + BOM per-frame. Hanya untuk unit yang mesinnya memang Weichai.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3

from ..core.config import get_settings
from . import epc_bom

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_GETPARAM_URL = "https://epc.sinotruk.com:18080/api/rest/weichai/getParam"
_CHECK_URL = "https://epc-cloud.weichai.com/Api/integration-api/integration/externalepc/checkJumpParams"
_ORDER_URL = "https://epc-cloud.weichai.com/Api/business-api/business/etl-install-bom-header/getOrderNumber"
_TREE_URL = "https://epc-cloud.weichai.com/Api/business-api/business/part/findBomTree"
_LIST_URL = "https://epc-cloud.weichai.com/Api/business-api/business/part/findBomList"

_CACHE_TTL = 3000.0     # < masa token (~ jam); di-mint ulang saat kedaluwarsa
_WORKERS = 16
_lock = threading.Lock()
_bridge_cache: dict[str, dict] = {}   # frame -> {at, bridge}
_bom_cache: dict[str, dict] = {}      # frame -> {at, val}
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/149 Safari/537.36"}


def _wc_headers(token: str) -> dict:
    return {"Accept": "application/json, text/plain, */*", "Authorization": f"Weichai {token}",
            "tenant-id": "1", "language": "en_US", "Referer": "https://epc-cloud.weichai.com/", **_UA}


def _sino_getparam(frame: str) -> str:
    """parms Weichai dari EPC Sinotruk (token Sinotruk; auto-refresh bila kedaluwarsa).
    '' bila unit tak punya link Weichai / gagal."""
    def _call() -> dict:
        tok = epc_bom._token()
        if not tok:
            return {"_err": "no_token"}
        try:
            r = requests.get(_GETPARAM_URL, params={"type": "frameNo", "code": frame},
                             headers={"Accept": "application/json", "token": tok,
                                      "Referer": "https://epc.sinotruk.com:18080/", **_UA},
                             timeout=25, verify=False)
            return r.json() if isinstance(r.json(), dict) else {"_err": "api"}
        except Exception:
            return {"_err": "network"}
    res = _call()
    # getParam balas param kosong / error token → refresh token Sinotruk, coba lagi.
    if not res.get("param") and epc_bom.refresh_token():
        res = _call()
    return (res.get("param") or "").strip()


def _bridge(frame: str) -> dict:
    """Tempuh SSO + resolusi order → {found, token, dhhNumber, dhhDate, root_id, serial,
    engine_model} (cache per frame). {found:False, reason} bila bukan mesin Weichai."""
    with _lock:
        c = _bridge_cache.get(frame)
        if c and (time.monotonic() - c["at"] < _CACHE_TTL):
            return c["val"]

    param = _sino_getparam(frame)
    if not param:
        return {"found": False, "reason": "no_link",
                "message": "Unit ini tidak punya link EPC Weichai (mesin non-Weichai / rangka salah)."}

    try:
        cj = requests.get(_CHECK_URL, params={"jumpParams": param},
                          headers={"Accept": "application/json", "Authorization": "Weichai null",
                                   "tenant-id": "1", "Referer": "https://epc-cloud.weichai.com/", **_UA},
                          timeout=25, verify=False).json()
    except Exception:
        return {"found": False, "reason": "network", "message": "Gagal menghubungi EPC Weichai."}
    d = cj.get("data") or {}
    token = d.get("accessToken")
    serial = (d.get("serialCode") or "").strip()
    if not token or not serial:
        return {"found": False, "reason": "no_engine",
                "message": "EPC Weichai tak mengembalikan data mesin untuk unit ini."}

    try:
        go = requests.get(_ORDER_URL, params={"serialNumber": serial},
                          headers=_wc_headers(token), timeout=25, verify=False).json()
    except Exception:
        return {"found": False, "reason": "network", "message": "Gagal ambil order mesin dari Weichai."}
    od = go.get("data")
    if isinstance(od, str):
        dhh, root, ddate, cdate = od, "", "", ""
    elif isinstance(od, dict):
        dhh = od.get("dhhNumber") or od.get("orderNumber") or ""
        root = od.get("id") or ""
        ddate = od.get("effDate") or ""
        cdate = od.get("completionDate") or ""   # tanggal produksi — dipakai repair kit
    else:
        dhh, root, ddate, cdate = "", "", "", ""
    if not dhh:
        return {"found": False, "reason": "no_order",
                "message": f"Order mesin (nomor {serial}) tak ditemukan di Weichai."}

    val = {"found": True, "token": token, "dhhNumber": dhh, "dhhDate": ddate,
           "completionDate": cdate, "root_id": root, "serial": serial}
    with _lock:
        _bridge_cache[frame] = {"at": time.monotonic(), "val": val}
        # Token account-level (zq-login) — cache global utk lookup lintas-part
        # (pengganti/replace) yang tak terikat 1 mesin.
        _tok_cache["token"] = token
        _tok_cache["at"] = time.monotonic()
        _tok_cache["seed"] = frame
    return val


_tok_cache: dict = {"token": "", "at": 0.0, "seed": ""}


def _ensure_token(rangka: str = "") -> str:
    """Token Weichai valid: dari cache (fresh) atau mint via bridge (rangka bila ada,
    kalau tidak pakai seed frame terakhir). '' bila belum ada sesi & tak bisa mint."""
    with _lock:
        if _tok_cache["token"] and (time.monotonic() - _tok_cache["at"] < _CACHE_TTL):
            return _tok_cache["token"]
        seed = rangka or _tok_cache.get("seed") or ""
    if seed:
        br = _bridge(epc_bom._frame(seed))
        if br.get("found"):
            return br["token"]
    return ""


def _get(url: str, params: dict, token: str) -> dict:
    try:
        r = requests.get(url, params=params, headers=_wc_headers(token), timeout=25, verify=False)
        j = r.json()
        return j if isinstance(j, dict) else {"_err": "api"}
    except Exception:
        return {"_err": "network"}


def _norm_part(p: dict) -> dict:
    return {"pn": (p.get("partNumber") or "").strip().upper(),
            "nama": " ".join((p.get("partName") or "").split()),
            "id": p.get("id"),           # dhhId anak — utk drill turunan part ini
            "version": p.get("version")}


_MAX_DEPTH = 5      # kedalaman drill (group → part → sub-part → …)
_MAX_NODES = 2000   # plafon node walk (jaga2 pohon besar)


def _list_node(dhh: str, dhh_id, ddate: str, token: str) -> list[dict]:
    """findBomList satu node → part langsung (ternormalisasi, tiap punya 'id' utk drill).
    [] bila leaf/gagal."""
    lst = _get(_LIST_URL, {"dhhNumber": dhh, "dhhId": dhh_id, "ypartFlag": "false",
                           "dhhDate": ddate}, token)
    if "_err" in lst:
        return []
    return [_norm_part(p) for p in (lst.get("data") or []) if p.get("partNumber")]


def _descendants(dhh: str, node_id, ddate: str, token: str, seen: set,
                 depth: int = 0) -> list[dict]:
    """Semua part TURUNAN di bawah node_id (rekursif, paralel per level, dedup PN).
    Dipakai saat MENGURAI part tertentu (mis. Oil Filter → Filter Element)."""
    if not node_id or depth >= _MAX_DEPTH:
        return []
    kids = _list_node(dhh, node_id, ddate, token)
    out: list[dict] = []
    deeper: list[dict] = []
    for k in kids:
        if k["pn"] and k["pn"] not in seen:
            seen.add(k["pn"])
            out.append(k)
            if k.get("id"):
                deeper.append(k)
    if deeper:
        with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
            subs = list(ex.map(
                lambda k: _descendants(dhh, k["id"], ddate, token, seen, depth + 1), deeper))
        for s in subs:
            out.extend(s)
    return out


def engine_bom(rangka: str) -> dict:
    """BOM MESIN Weichai dari NOMOR RANGKA (full auto: SSO→order→tree→list). Ambil GROUP +
    part LANGSUNG tiap group (cepat, cache). Turunan part (mis. Filter Element di dalam Oil
    Filter) TIDAK diurai di sini — diurai on-demand oleh find_parts (biar responsif).
    {found, engine, jumlah_group, jumlah_part, groups:[{id, nama, parts:[...]}]}."""
    frame = epc_bom._frame(rangka)
    if not frame:
        return {"found": False, "reason": "input", "message": "Nomor rangka kosong/tidak valid."}
    with _lock:
        c = _bom_cache.get(frame)
        if c and (time.monotonic() - c["at"] < _CACHE_TTL):
            return c["val"]

    br = _bridge(frame)
    if not br.get("found"):
        return br
    token, dhh, ddate = br["token"], br["dhhNumber"], br.get("dhhDate") or ""

    tree = _get(_TREE_URL, {"dhhNumber": dhh, "dhhDate": ddate, "lang": "en_US",
                            "ypartFlag": "false"}, token)
    if "_err" in tree:
        return {"found": False, "reason": tree["_err"], "message": "Gagal ambil pohon BOM mesin Weichai."}
    data = tree.get("data")
    root = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
    if not root:
        return {"found": False, "reason": "empty", "message": "BOM mesin Weichai kosong untuk unit ini."}

    groups_raw = [g for g in (root.get("children") or []) if g.get("id")]

    def _fill(g: dict) -> dict:
        parts = _list_node(dhh, g["id"], ddate, token)
        return {"id": g["id"], "pn": (g.get("partNumber") or "").strip().upper(),
                "nama": " ".join((g.get("partName") or "").split()),
                "jumlah_part": len(parts), "parts": parts}

    with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        groups = list(ex.map(_fill, groups_raw))
    total = sum(g["jumlah_part"] for g in groups)
    val = {"found": True,
           "engine": {"model": br.get("serial"),
                      "nama": " ".join((root.get("partName") or "").split()),
                      "order": dhh},
           "jumlah_group": len(groups), "jumlah_part": total, "groups": groups,
           "_ctx": {"dhh": dhh, "ddate": ddate, "token": token}}
    with _lock:
        _bom_cache[frame] = {"at": time.monotonic(), "val": val}
    return val


def find_parts(rangka: str, terms: list[str]) -> dict:
    """Cari komponen mesin yg nama/PN cocok. Untuk part LANGSUNG yang cocok (mis. 'Oil
    Filter') JUGA diurai TURUNANNYA (mis. Filter Element, Seat) — on-demand, jadi cepat.
    {found, engine, cocok, hasil:[{pn, nama, group, dari?}]}."""
    bom = engine_bom(rangka)
    if not bom.get("found"):
        return bom
    ctx = bom.get("_ctx") or {}
    dhh, ddate, token = ctx.get("dhh"), ctx.get("ddate") or "", ctx.get("token")
    kws = [t.lower() for t in terms if t and len(t) >= 2]
    pn_terms = {t.upper() for t in terms if t}

    def _match(p: dict) -> bool:
        hay = (p["nama"] + " " + p["pn"]).lower()
        return p["pn"] in pn_terms or (bool(kws) and any(k in hay for k in kws))

    # 1) part langsung yg cocok (per group). Kumpulkan yg cocok utk diurai turunannya.
    hasil: list[dict] = []
    seen_pn: set = set()
    to_expand: list[tuple] = []   # (part, group_name)
    for g in bom["groups"]:
        for p in g["parts"]:
            if _match(p):
                if p["pn"] not in seen_pn:
                    seen_pn.add(p["pn"])
                    hasil.append({"pn": p["pn"], "nama": p["nama"], "group": g["nama"]})
                if p.get("id"):
                    to_expand.append((p, g["nama"]))

    # 2) urai TURUNAN tiap part yg cocok (mis. Oil Filter → Filter Element) — SEMUA
    #    turunannya disertakan (bukan cuma yg cocok istilah), karena itu isi part tsb.
    if to_expand and dhh and token:
        def _exp(item: tuple) -> tuple:
            p, gname = item
            return gname, _descendants(dhh, p["id"], ddate, token, set([p["pn"]]))
        with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
            expanded = list(ex.map(_exp, to_expand))
        for gname, kids in expanded:
            for k in kids:
                if k["pn"] and k["pn"] not in seen_pn:
                    seen_pn.add(k["pn"])
                    hasil.append({"pn": k["pn"], "nama": k["nama"], "group": gname,
                                  "keterangan": "komponen di dalam part di atas"})

    return {"found": True, "engine": bom["engine"], "jumlah_group": bom["jumlah_group"],
            "jumlah_part": bom["jumlah_part"], "cocok": len(hasil), "hasil": hasil}


_REPLACE_URL = "https://epc-cloud.weichai.com/Api/business-api/business/replace/page"


def replace_part(part_number: str, rangka: str = "") -> dict:
    """PERSAMAAN/PENGGANTI (supersession) part MESIN Weichai — global by PN.
    {found, part_number, digantikan_oleh:[{pn, tanggal, tipe}], menggantikan:[{pn, ...}],
    jumlah_record} atau {found:False, reason}."""
    pn = (part_number or "").strip().upper()
    if not pn:
        return {"found": False, "reason": "input", "message": "Sebutkan Part Number-nya."}
    token = _ensure_token(rangka)
    if not token:
        return {"found": False, "reason": "no_session",
                "message": "Sesi EPC Weichai belum aktif. Cek satu unit bermesin Weichai dulu "
                           "(mis. 'cek piston unit <rangka>') agar token aktif, lalu ulangi."}

    records: list[dict] = []
    for page in range(1, 6):   # ambil s/d ~250 record (cukup)
        r = _get(_REPLACE_URL, {"pageNo": page, "pageSize": 50, "keyword": "",
                                "partNumber": pn, "dhhNumber": ""}, token)
        if "_err" in r:
            break
        d = r.get("data") or {}
        lst = d.get("list") if isinstance(d, dict) else d
        if not lst:
            break
        records.extend(lst)
        try:
            total = int(d.get("total") or 0) if isinstance(d, dict) else 0
        except (TypeError, ValueError):
            total = 0
        if total and len(records) >= total:
            break
        if len(lst) < 50:
            break

    if not records:
        return {"found": False, "part_number": pn,
                "message": f"Tidak ada data pengganti untuk PN '{pn}' di EPC Weichai "
                           "(kemungkinan part masih berlaku / bukan part Weichai)."}

    def _pns(s):  # field bisa multi-PN dipisah koma
        return [x.strip().upper() for x in (s or "").split(",") if x.strip()]

    baru_untuk_pn: dict[str, dict] = {}   # PN ini(old) → digantikan oleh PN baru
    lama_untuk_pn: dict[str, dict] = {}   # PN ini(new) → menggantikan PN lama
    for rec in records:
        news, olds = _pns(rec.get("newPartNumber")), _pns(rec.get("oldPartNumber"))
        info = {"tanggal": rec.get("replacementDate"), "tipe": rec.get("replaceType"),
                "ecn": rec.get("replaceGroup")}
        if pn in olds:
            for n in news:
                if n != pn:
                    baru_untuk_pn.setdefault(n, info)
        if pn in news:
            for o in olds:
                if o != pn:
                    lama_untuk_pn.setdefault(o, info)

    return {
        "found": True, "part_number": pn, "jumlah_record": len(records),
        "digantikan_oleh": [{"pn": k, **v} for k, v in baru_untuk_pn.items()],
        "menggantikan": [{"pn": k, **v} for k, v in lama_untuk_pn.items()],
        "sumber": ("EPC Weichai resmi (data替换/ECN) — riwayat penggantian/supersession part. "
                   "'digantikan_oleh' = PN pengganti terbaru (pakai ini bila PN lama diskontinu); "
                   "'menggantikan' = PN lama yang digantikan PN ini. Tipe: Unidirectional "
                   "(searah, PN lama→baru saja) / Bidirectional (dua arah, bisa saling ganti)."),
    }


_KIT_URL = "https://epc-cloud.weichai.com/Api/business-api/business/part/findRepairKitTree"


def repair_kit(rangka: str) -> dict:
    """REPAIR KIT (维修包) mesin Weichai per-VIN. Walk pohon kit → part tiap kit +
    cross-ref stok/harga dilakukan di pemanggil. {found, engine, kit:[{nama, parts:
    [{pn, nama, qty}]}]} atau {found:False, reason}."""
    frame = epc_bom._frame(rangka)
    if not frame:
        return {"found": False, "reason": "input", "message": "Nomor rangka tidak valid."}
    br = _bridge(frame)
    if not br.get("found"):
        return br
    token, dhh = br["token"], br["dhhNumber"]
    cdate = br.get("completionDate") or br.get("dhhDate") or ""

    tree = _get(_KIT_URL, {"dhhNumber": dhh, "completionDate": cdate,
                           "partNumber": "", "partId": ""}, token)
    if "_err" in tree:
        return {"found": False, "reason": tree["_err"], "message": "Gagal ambil repair kit Weichai."}
    data = tree.get("data")
    nodes = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])

    # Walk pohon → kumpulkan KIT (node yg punya part di bawahnya). Struktur mirip
    # findBomTree: root → kit → part(children). Fleksibel: node ber-partNumber = part.
    kits: list[dict] = []

    def _collect_parts(node: dict) -> list[dict]:
        out: list[dict] = []
        for ch in (node.get("children") or []):
            if ch.get("partNumber"):
                out.append({"pn": (ch.get("partNumber") or "").strip().upper(),
                            "nama": " ".join((ch.get("partName") or "").split()),
                            "qty": ch.get("quantity") or ch.get("amount")})
            out.extend(_collect_parts(ch))
        return out

    for root in nodes:
        for kit in (root.get("children") or [root]):
            parts = _collect_parts(kit)
            if parts:
                kits.append({"nama": " ".join((kit.get("partName") or "").split()) or "Repair Kit",
                             "pn": (kit.get("partNumber") or "").strip().upper(),
                             "jumlah_part": len(parts), "parts": parts})

    if not kits:
        return {"found": False, "reason": "no_kit",
                "message": "Mesin unit ini tidak punya repair kit terdefinisi di EPC Weichai."}
    return {"found": True, "engine": {"model": br.get("serial"), "order": dhh},
            "jumlah_kit": len(kits), "kit": kits}
