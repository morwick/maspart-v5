"""
SIMS EOL AI — asisten DIAGNOSA/PERBAIKAN bawaan Sinotruk (RAG atas manual
perbaikan resmi + kasus kerusakan + materi pelatihan pabrik).

Endpoint (riset PROJECT.md 2026-07-09, diverifikasi ulang 2026-07-13):
  GET {8082}/intlapi/intl.service.basic/tKnowledgeBases/eolQuestStreamingLexiangOriginalTrans
      ?query=<pertanyaan>&language=id   → SSE stream

Stream: tiap baris `data:{...}`; `delta_content` = potongan jawaban, kecuali saat
`processes.stage` == 'thinking' (itu nalar internal — DIBUANG, bukan untuk user).
Berakhir dengan `is_stop: true` (+ `logId`).

⚠️ Auth: token sims_fetcher standar (Bearer). ⛔ JANGAN kirim header
`Accept: text/event-stream` — server justru membalas 401 (ditemukan 2026-07-13);
header sw8/x-shsnc/_dd_s di cURL browser cuma telemetri, tak perlu.

Karakter (dari evaluasi): KUAT untuk kode SPN/FMI & diagnosa gejala; JUJUR bilang
"konten belum terindex" bila tak ada (jangan dipaksa); terjemahan CN→ID kadang
kasar ("HAWO"/"rem ekspresi"). Latensi 20–90 detik → SELALU dipanggil dengan
timeout & jangan dijadikan jalur wajib.
"""
from __future__ import annotations

import json
import logging
import re
import time

import requests

from . import sims  # menyiapkan sys.path ke backend/shared (jangan dibuang)

logger = logging.getLogger("maspart.sims_eol")

_URL = ("http://simscloud.cnhtcerp.com:8082/intlapi/intl.service.basic/"
        "tKnowledgeBases/eolQuestStreamingLexiangOriginalTrans")
_TIMEOUT = 90          # detik (jawaban 20–90 dtk)
_MAX_CHARS = 6000      # pagar: jangan biarkan stream tak berujung membanjiri prompt

# Balasan jujur SIMS saat pengetahuannya tak memuat topik itu.
_TAK_ADA_RE = re.compile(r"belum terindex|tidak dapat dijawab|belum tersedia", re.IGNORECASE)


def available() -> bool:
    """SIMS siap dipakai (kredensial ada & modul fetcher termuat)."""
    return bool(getattr(sims, "_SIMS_OK", False))


def _token() -> str:
    import sims_fetcher as sf
    return sf._get_token()


def _headers() -> dict:
    import sims_fetcher as sf
    return {**sf.BASE_HEADERS, "Authorization": _token()}


def _stream(query: str, language: str, timeout: int) -> tuple[str, str | None]:
    """(jawaban, log_id) — konsumsi SSE sampai is_stop. Buang tahap 'thinking'."""
    import sims_fetcher as sf

    ans: list[str] = []
    log_id: str | None = None
    r = requests.get(_URL, params={"query": query, "language": language},
                     headers=_headers(), stream=True, timeout=timeout)
    if r.status_code == 401:            # token basi → login ulang sekali
        sf._reset_token()
        r = requests.get(_URL, params={"query": query, "language": language},
                         headers=_headers(), stream=True, timeout=timeout)
    r.raise_for_status()
    total = 0
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        try:
            d = json.loads(line[5:])
        except (ValueError, TypeError):
            continue
        stage = (d.get("processes") or {}).get("stage")
        c = d.get("delta_content") or ""
        if c and stage != "thinking":   # 'thinking' = nalar internal, bukan jawaban
            ans.append(c)
            total += len(c)
        if d.get("logId"):
            log_id = str(d["logId"])
        if d.get("is_stop") or total > _MAX_CHARS:
            break
    r.close()
    return "".join(ans).strip(), log_id


def tanya(query: str, language: str = "id", timeout: int = _TIMEOUT) -> dict:
    """Tanya EOL AI Sinotruk. Return:
      {found: True, jawaban, log_id, detik}                  — ada isinya
      {found: False, kosong: True, jawaban}                  — SIMS jujur bilang tak ada
      {found: False, error}                                  — SIMS mati/timeout
    TIDAK PERNAH raise: pemanggil (tool asisten) harus tetap bisa fallback."""
    q = " ".join((query or "").split())
    if not q:
        return {"found": False, "error": "Pertanyaan kosong."}
    if not available():
        return {"found": False, "error": "SIMS belum dikonfigurasi di server."}
    t0 = time.time()
    try:
        jawaban, log_id = _stream(q, language, timeout)
    except requests.Timeout:
        return {"found": False, "error": f"SIMS EOL AI tak merespons dalam {timeout} detik."}
    except Exception as e:
        logger.warning("SIMS EOL AI gagal: %s", e)
        return {"found": False, "error": f"Gagal menghubungi SIMS EOL AI: {str(e)[:120]}"}
    detik = round(time.time() - t0)
    if not jawaban:
        return {"found": False, "error": "SIMS EOL AI membalas kosong.", "detik": detik}
    if _TAK_ADA_RE.search(jawaban):
        # Jujur: pengetahuan pabrik tak memuat topik ini. JANGAN dipoles jadi jawaban.
        return {"found": False, "kosong": True, "jawaban": jawaban,
                "log_id": log_id, "detik": detik}
    return {"found": True, "jawaban": jawaban, "log_id": log_id, "detik": detik}
