"""Peningkat pencarian GENERIK — koreksi typo, stem Indonesia ringan, PN
ternormal. Diekstrak dari mesin cari `pengetahuan` V2 (algoritma & bobot
IDENTIK) supaya store lain (manual_teks, dst.) dapat kepintaran yang sama tanpa
menduplikasi/menyentuh pengetahuan. Semua fungsi MURNI — tak membaca file/store.

Prinsip (sama seperti pengetahuan): fallback typo/stem hanya menambah skor bila
kecocokan EKSAK gagal, dan selalu DIREDAM di bawah kecocokan langsung — jadi tak
pernah menggeser peringkat yang benar. Kedua sisi (indeks & kueri) distem dengan
algoritme yang sama; konsistensi lebih penting daripada ketepatan linguistik.
"""
from __future__ import annotations

import re

# Bobot varian (di bawah kecocokan eksak = 1.0).
BOBOT_TIPO = 0.8      # kueri hasil koreksi typo
BOBOT_STEM = 0.7      # kecocokan lewat stem

# ── Stem Indonesia ringan ────────────────────────────────────────────
_NASAL = {"meng": "k", "meny": "s", "men": "t", "mem": "p",
          "peng": "k", "peny": "s", "pen": "t", "pem": "p"}
_PREFIKS = ("meng", "meny", "men", "mem", "me", "peng", "peny", "pen", "pem",
            "pe", "ber", "ter", "di", "ke", "se")
_SUFIKS = ("lah", "kah", "pun", "nya", "kan", "an", "i")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ALPHA_RE = re.compile(r"[a-z]{4,}")


def stem_kandidat(w: str) -> tuple[str, ...]:
    """Kandidat akar kata: buang sufiks umum lalu prefiks; prefiks nasal
    (mem-/men-/meng- dst.) melahirkan varian ber-huruf-pulih ('memasang' →
    asang + pasang) supaya ketemu dgn 'pasang' polos."""
    if len(w) < 5 or any(c.isdigit() for c in w):
        return ()
    kandidat: set[str] = set()
    s = w
    for suf in _SUFIKS:
        if s.endswith(suf) and len(s) - len(suf) >= 4:
            s = s[: -len(suf)]
            kandidat.add(s)
            break
    for pre in _PREFIKS:
        if s.startswith(pre) and len(s) - len(pre) >= 3:
            akar = s[len(pre):]
            kandidat.add(akar)
            pulih = _NASAL.get(pre)
            if pulih:
                kandidat.add(pulih + akar)
            break
    kandidat.discard(w)
    return tuple(kandidat)


def stem_set(gabung: str) -> frozenset[str]:
    """Token + seluruh kandidat stemnya untuk SATU dokumen (sisi indeks).
    Dihitung sekali per penulisan store, bukan per kueri."""
    out: set[str] = set()
    for t in _TOKEN_RE.findall(gabung or ""):
        if len(t) < 4:
            continue
        out.add(t)
        out.update(stem_kandidat(t))
    return frozenset(out)


def stems_kueri(words: list[str]) -> dict[str, tuple[str, ...]]:
    """Per kata kueri → (kata, *kandidat stem) untuk dicek ke stemset dokumen."""
    return {w: (w, *stem_kandidat(w)) for w in words}


# ── Koreksi typo (kueri → kosakata store) ────────────────────────────
def vocab_ember(teks_list) -> dict[str, set[str]]:
    """Kosakata store dikelompokkan per HURUF AWAL (bucket) — huruf pertama
    dianggap benar, menekan biaya scan & salah-koreksi. `teks_list` = iterable
    string (gabungan ladang tiap dokumen)."""
    ember: dict[str, set[str]] = {}
    for teks in teks_list:
        for t in _ALPHA_RE.findall(teks or ""):
            ember.setdefault(t[0], set()).add(t)
    return ember


def _jarak_edit(a: str, b: str, maks: int) -> int:
    """Levenshtein terpangkas: begitu seluruh baris > maks, menyerah (maks+1)."""
    if abs(len(a) - len(b)) > maks:
        return maks + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        terbaik = i
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            cur.append(v)
            if v < terbaik:
                terbaik = v
        if terbaik > maks:
            return maks + 1
        prev = cur
    return prev[-1]


def koreksi_tipo(ql: str, ember: dict[str, set[str]]) -> str:
    """Kueri dgn kata tak-dikenal diganti tetangga terdekat (jarak edit ≤1, ≤2
    utk kata ≥8) di kosakata store. '' bila tak ada yang perlu/bisa dikoreksi."""
    kata = re.findall(r"(?<![a-z0-9])[a-z]{5,}(?![a-z0-9])", ql)
    if not kata or not ember:
        return ""
    ganti: dict[str, str] = {}
    for w in dict.fromkeys(kata):
        kandidat = ember.get(w[0])
        if not kandidat or w in kandidat:
            continue
        maks = 1 if len(w) < 8 else 2
        best, best_d = "", maks + 1
        for k in kandidat:
            d = _jarak_edit(w, k, maks)
            if d < best_d:
                best, best_d = k, d
                if d == 1:
                    break
        if best:
            ganti[w] = best
    if not ganti:
        return ""
    out = ql
    for a, b in ganti.items():
        out = re.sub(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", b, out)
    return out if out != ql else ""


# ── PN/kode ternormal ────────────────────────────────────────────────
_SEP_RE = re.compile(r"[-./]")
_PN_RUN_RE = re.compile(r"[a-z0-9][a-z0-9\-./]{4,}[a-z0-9]")
MIN_KODE_NORM = 6


def pn_normal_tokens(ql: str) -> list[str]:
    """Runtun PN berpemisah ('wg-9725.190102') → token ternormal tanpa pemisah
    ('wg9725190102') utk dicocokkan ke kode dokumen yang juga dinormalkan."""
    out: list[str] = []
    for run in _PN_RUN_RE.findall(ql.lower()):
        if "-" not in run and "." not in run and "/" not in run:
            continue
        norm = _SEP_RE.sub("", run)
        if len(norm) >= MIN_KODE_NORM and any(c.isdigit() for c in norm) and norm not in out:
            out.append(norm)
    return out


def kode_norm(kode_list) -> str:
    """Kode dokumen tanpa pemisah, satu string siap-substring ('wg9725190102')."""
    return " ".join(_SEP_RE.sub("", str(k)).lower() for k in (kode_list or []))
