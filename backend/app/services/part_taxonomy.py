"""TAKSONOMI PART — pengetahuan "sparepart lebih dalam" (Fase C 2026-07-23).

Mengelompokkan ~33rb nama part katalog ke KELUARGA (sistem → sub-sistem →
keluarga) secara DETERMINISTIK via tabel aturan token nama (match_name), lalu
tiap keluarga diberi ruang kurasi `fungsi` / `gejala_umum` (Indonesia, diisi
offline oleh tools/curate_taxonomy.py — LLM tervalidasi katalog; seed kosong =
belum dikurasi, disajikan jujur).

Store: app/services/part_taxonomy.json.gz (dibangun offline oleh
tools/build_part_taxonomy.py, di-ship git — pola dtc_codes). Record:
  {keluarga, sistem, sub_sistem, nama_kunci[], contoh_pn[], jumlah_pn,
   fungsi, gejala_umum, catatan}
Kunci identitas = `keluarga` (stabil; kurasi dipreservasi rebuild).

Dipakai: tool asisten `info_part` + baris ringkas `keluarga_part` di
detail_part/cari_part + tautan knowledge_links (store "part_taxonomy").
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from .knowledge_util import load_json, norm_pn

logger = logging.getLogger("maspart.part_taxonomy")

_PATH = Path(__file__).resolve().parent / "part_taxonomy.json.gz"

# ── Tabel aturan (urutan = prioritas; aturan pertama yang cocok menang) ──
# Bentuk: (keluarga, sistem, sub_sistem, [alternatif-token, ...]) — nama part
# cocok bila TIAP kelompok alternatif terwakili ≥1 token (AND antar-kelompok,
# OR di dalam kelompok). Token dicocokkan word-boundary pada nama uppercase.
_RULES: list[tuple[str, str, str, list[list[str]]]] = [
    ("filter oli",          "mesin",       "pelumasan",   [["FILTER", "FILT"], ["OIL"]]),
    ("filter bahan bakar",  "mesin",       "bahan bakar", [["FILTER", "FILT"], ["FUEL", "DIESEL"]]),
    ("filter udara",        "mesin",       "pemasukan udara", [["FILTER", "FILT", "ELEMENT"], ["AIR"]]),
    ("filter hidrolik",     "hidrolik",    "pelumasan",   [["FILTER", "FILT"], ["HYDRAULIC", "HYD"]]),
    ("water separator",     "mesin",       "bahan bakar", [["SEPARATOR"]]),
    ("kampas rem",          "rem",         "rem roda",    [["BRAKE"], ["FRICTION", "LINING", "PAD"]]),
    ("sepatu rem",          "rem",         "rem roda",    [["BRAKE"], ["SHOE"]]),
    ("tromol / cakram rem", "rem",         "rem roda",    [["BRAKE"], ["DRUM", "DISC"]]),
    ("katup rem",           "rem",         "kontrol rem", [["BRAKE", "RELAY", "QUICK"], ["VALVE"]]),
    ("booster kopling",     "kopling",     "penggerak",   [["CLUTCH"], ["BOOSTER", "CYLINDER"]]),
    ("plat penekan kopling", "kopling",    "kopling",     [["CLUTCH", "PRESSURE"], ["PRESSURE", "COVER"]]),
    ("kampas kopling",      "kopling",     "kopling",     [["CLUTCH"], ["DISC", "DRIVEN", "FRICTION"]]),
    ("release bearing",     "kopling",     "kopling",     [["RELEASE", "THROWOUT"], ["BEARING"]]),
    ("injector",            "mesin",       "bahan bakar", [["INJECTOR", "NOZZLE"]]),
    ("pompa injeksi",       "mesin",       "bahan bakar", [["PUMP"], ["INJECTION", "FUEL"]]),
    ("pompa air",           "mesin",       "pendingin",   [["PUMP"], ["WATER", "COOLANT"]]),
    ("pompa oli",           "mesin",       "pelumasan",   [["PUMP"], ["OIL"]]),
    ("pompa hidrolik",      "hidrolik",    "pompa",       [["PUMP"], ["HYDRAULIC", "GEAR", "STEERING"]]),
    ("turbocharger",        "mesin",       "pemasukan udara", [["TURBO", "TURBOCHARGER"]]),
    ("kompresor angin",     "rem",         "suplai angin", [["COMPRESSOR"]]),
    ("radiator",            "mesin",       "pendingin",   [["RADIATOR"]]),
    ("intercooler",         "mesin",       "pemasukan udara", [["INTERCOOLER"]]),
    ("thermostat",          "mesin",       "pendingin",   [["THERMOSTAT"]]),
    ("kipas pendingin",     "mesin",       "pendingin",   [["FAN"]]),
    ("alternator",          "kelistrikan", "pengisian",   [["ALTERNATOR"]]),
    ("motor starter",       "kelistrikan", "starter",     [["STARTER", "STARTING"]]),
    ("aki / baterai",       "kelistrikan", "kelistrikan", [["BATTERY"]]),
    ("sensor",              "kelistrikan", "sensor",      [["SENSOR"]]),
    ("saklar",              "kelistrikan", "kontrol",     [["SWITCH"]]),
    ("relay",               "kelistrikan", "kontrol",     [["RELAY"]]),
    ("ECU / modul kontrol", "kelistrikan", "kontrol",     [["ECU", "CONTROLLER", "VECU", "BCM"]]),
    ("lampu",               "kelistrikan", "penerangan",  [["LAMP", "LIGHT", "HEADLAMP"]]),
    ("wiring harness",      "kelistrikan", "kabel",       [["HARNESS", "WIRE", "CABLE"]]),
    ("kaca spion",          "kabin",       "bodi",        [["MIRROR"]]),
    ("wiper",               "kabin",       "bodi",        [["WIPER"]]),
    ("shock absorber",      "suspensi",    "peredam",     [["SHOCK", "ABSORBER", "DAMPER"]]),
    ("pegas daun",          "suspensi",    "pegas",       [["SPRING"], ["LEAF"]]),
    ("pegas / per",         "suspensi",    "pegas",       [["SPRING"]]),
    ("bushing",             "suspensi",    "sendi",       [["BUSH", "BUSHING"]]),
    ("tie rod / ball joint", "kemudi",     "sendi",       [["TIE", "BALL", "DRAG"], ["ROD", "JOINT", "LINK"]]),
    ("king pin",            "kemudi",      "poros depan", [["KING"], ["PIN"]]),
    ("cross joint",         "penggerak",   "propeller",   [["CROSS", "UNIVERSAL"], ["JOINT", "ASSEMBLY"]]),
    ("propeller shaft",     "penggerak",   "propeller",   [["PROPELLER", "TRANSMISSION"], ["SHAFT"]]),
    ("hub roda",            "poros",       "roda",        [["HUB"]]),
    ("piston",              "mesin",       "blok mesin",  [["PISTON"]]),
    ("cylinder liner",      "mesin",       "blok mesin",  [["LINER", "SLEEVE"]]),
    ("ring piston",         "mesin",       "blok mesin",  [["RING"], ["PISTON"]]),
    ("crankshaft",          "mesin",       "blok mesin",  [["CRANKSHAFT"]]),
    ("camshaft",            "mesin",       "blok mesin",  [["CAMSHAFT"]]),
    ("klep / valve mesin",  "mesin",       "kepala silinder", [["INTAKE", "EXHAUST"], ["VALVE"]]),
    ("synchronizer",        "transmisi",   "gearbox",     [["SYNCHRONIZER", "SYNCHRO"]]),
    ("roda gigi",           "transmisi",   "gearbox",     [["GEAR"]]),
    ("silinder hidrolik",   "hidrolik",    "aktuator",    [["CYLINDER"], ["HYDRAULIC", "LIFT", "TILT", "STEERING", "BOOM", "BUCKET"]]),
    ("track roller",        "undercarriage", "rantai",    [["ROLLER"], ["TRACK", "CARRIER"]]),
    ("idler",               "undercarriage", "rantai",    [["IDLER"]]),
    ("sprocket",            "undercarriage", "rantai",    [["SPROCKET"]]),
    ("track shoe",          "undercarriage", "rantai",    [["TRACK", "GROUSER"], ["SHOE", "GROUSER", "LINK"]]),
    ("cutting edge / blade", "perkakas kerja", "blade",   [["CUTTING", "BLADE", "EDGE"]]),
    ("bearing",             "umum",        "bantalan",    [["BEARING"]]),
    ("oil seal / seal",     "umum",        "perapat",     [["SEAL"]]),
    ("gasket / perpak",     "umum",        "perapat",     [["GASKET"]]),
    ("o-ring",              "umum",        "perapat",     [["ORING", "O-RING"]]),
    ("belt",                "mesin",       "penggerak sabuk", [["BELT"]]),
    ("tensioner",           "mesin",       "penggerak sabuk", [["TENSIONER"]]),
    ("mounting",            "umum",        "dudukan",     [["MOUNTING", "MOUNT"]]),
    ("selang",              "umum",        "saluran",     [["HOSE"]]),
    ("pipa",                "umum",        "saluran",     [["PIPE", "TUBE"]]),
    ("tangki",              "umum",        "tangki",      [["TANK"]]),
]

_TOKEN_RE = re.compile(r"[A-Z]+(?:-[A-Z]+)?")


def match_name(nama: str) -> tuple[str, str, str] | None:
    """Cocokkan NAMA part (EN) ke aturan keluarga. None = tak terklasifikasi
    (sengaja TIDAK dipaksa masuk 'lain-lain' — taksonomi hanya keluarga bermakna)."""
    up = str(nama or "").upper().replace("O-RING", "ORING")
    toks = set(_TOKEN_RE.findall(up))
    if not toks:
        return None
    for keluarga, sistem, sub, groups in _RULES:
        if all(any(alt in toks for alt in grp) for grp in groups):
            return keluarga, sistem, sub
    return None


# ── Loader store ──
def _rows() -> list[dict]:
    d = load_json(_PATH)
    return d if isinstance(d, list) else []


def available() -> bool:
    return bool(_rows())


def count() -> int:
    return len(_rows())


def _by_keluarga() -> dict[str, dict]:
    return {r.get("keluarga"): r for r in _rows()}


def cari(nama_atau_pn: str, limit: int = 5) -> list[dict]:
    """Cari keluarga by nama keluarga / nama_kunci / nama part / PN."""
    q = str(nama_atau_pn or "").strip()
    if not q:
        return []
    rows = _rows()
    # 1) PN? → resolve via nama part katalog
    if any(c.isdigit() for c in q) and " " not in q:
        r = for_pn(q)
        return [r] if r else []
    qup = q.upper()
    out: list[dict] = []
    for r in rows:
        kel = str(r.get("keluarga") or "")
        if qup == kel.upper():
            return [r]
        if (qup in kel.upper()
                or any(qup in str(k).upper() or str(k).upper() in qup
                       for k in (r.get("nama_kunci") or []))):
            out.append(r)
    if not out:
        # cocokkan lewat aturan (istilah user diperlakukan sbg nama part) +
        # ekspansi sinonim lapangan (kamus sudah ada)
        cand = [q]
        try:
            from . import sinonim
            terms, _m = sinonim.expand_query(q)
            cand += list(terms or [])
        except Exception:
            pass
        peta = _by_keluarga()
        for c in cand:
            m = match_name(c)
            if m and m[0] in peta and peta[m[0]] not in out:
                out.append(peta[m[0]])
    return out[:limit]


def for_pn(pn: str) -> dict | None:
    """Keluarga untuk satu PN — via nama part di katalog lokal (part_index)."""
    try:
        from . import part_index
        nama = part_index.name_for(pn) or ""
    except Exception:
        nama = ""
    if not nama:
        return None
    m = match_name(nama)
    if not m:
        return None
    return _by_keluarga().get(m[0])


def ringkas(pn: str) -> str:
    """1 baris keluarga+fungsi utk disisipkan di detail_part/cari_part ('' bila
    tak terklasifikasi / store absen)."""
    r = for_pn(pn)
    if not r:
        return ""
    s = f"{r.get('keluarga')} ({r.get('sistem')}/{r.get('sub_sistem')})"
    fungsi = str(r.get("fungsi") or "").strip()
    if fungsi:
        s += " — " + fungsi[:120]
    return s
