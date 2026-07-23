"""Kurasi OFFLINE taksonomi part — isi `fungsi` & `gejala_umum` (Indonesia)
untuk keluarga yang masih kosong, via LLM DeepSeek JSON-mode + VALIDASI katalog
(pola ai_sinonim_learn._llm_usulan + _validate_keywords).

Dijalankan OPERATOR (bukan scheduler) → biaya LLM terkendali & sekali bayar;
hasil dipreservasi build_part_taxonomy antar-rebuild (kunci `keluarga`).

    cd backend
    python tools/curate_taxonomy.py            # 20 keluarga terbanyak-PN dulu
    python tools/curate_taxonomy.py --limit 65 # semua
    python tools/curate_taxonomy.py --dry      # lihat tanpa menulis
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services import part_index, part_taxonomy  # noqa: E402
from app.services.knowledge_util import load_json, write_json_gz  # noqa: E402

_PROMPT = (
    "Kamu ahli suku cadang truk Sinotruk/HOWO dan alat berat Shantui di Indonesia. "
    "Untuk tiap KELUARGA PART berikut, tulis dalam BAHASA INDONESIA teknis yang "
    "membumi: (1) 'fungsi' — apa kerjanya di kendaraan, 2-3 kalimat; (2) "
    "'gejala_umum' — gejala khas di lapangan bila part itu rusak/aus, 2-3 "
    "kalimat. Jawab HANYA JSON: {\"keluarga\": [{\"keluarga\": \"<persis>\", "
    "\"fungsi\": \"...\", \"gejala_umum\": \"...\"}]}. Aturan: TANPA nomor part, "
    "TANPA merek selain konteks umum, jangan mengarang spesifikasi angka."
)


def _llm(batch: list[dict]) -> list[dict]:
    s = get_settings()
    if not s.ai_configured:
        raise SystemExit("DEEPSEEK_API_KEY belum diset.")
    daftar = "\n".join(
        f"- {r['keluarga']} (sistem {r['sistem']}/{r['sub_sistem']}; contoh nama "
        f"katalog: {', '.join(r.get('nama_kunci') or [])[:120]})" for r in batch)
    resp = requests.post(
        f"{s.deepseek_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {s.deepseek_api_key}",
                 "Content-Type": "application/json"},
        json={"model": s.deepseek_model, "temperature": 0.0,
              "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": _PROMPT},
                           {"role": "user", "content": daftar}]},
        timeout=120)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return (json.loads(content).get("keluarga") or [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    rows = load_json(part_taxonomy._PATH) or []
    kosong = [r for r in rows if not str(r.get("fungsi") or "").strip()]
    kosong.sort(key=lambda r: -(r.get("jumlah_pn") or 0))
    batch = kosong[:max(1, a.limit)]
    if not batch:
        print("Semua keluarga sudah terkurasi.")
        return 0
    print(f"Kurasi {len(batch)} keluarga (dari {len(kosong)} kosong)…")

    by_kel = {r["keluarga"]: r for r in rows}
    n_ok = 0
    for i in range(0, len(batch), 10):        # batch 10/panggilan
        usulan = _llm(batch[i:i + 10])
        for u in usulan:
            kel = str(u.get("keluarga") or "").strip()
            r = by_kel.get(kel)
            if not r:
                print(f"  ⛔ halusinasi keluarga: {kel!r} — dibuang")
                continue
            # VALIDASI: nama_kunci keluarga ini benar-benar kena katalog
            # (pagar pola _validate_keywords — kurasi hanya utk keluarga nyata).
            if not any(part_index.search_part_name(k)
                       for k in (r.get("nama_kunci") or [])[:2]):
                print(f"  ⛔ {kel}: nama_kunci tak terbukti di katalog — dilewati")
                continue
            fungsi = str(u.get("fungsi") or "").strip()
            gejala = str(u.get("gejala_umum") or "").strip()
            if len(fungsi) < 20:
                print(f"  ⛔ {kel}: fungsi terlalu pendek — dilewati")
                continue
            r["fungsi"], r["gejala_umum"] = fungsi, gejala
            n_ok += 1
            print(f"  ✅ {kel}")
        time.sleep(1.0)

    if a.dry:
        print(f"(dry) {n_ok} keluarga akan terisi — TIDAK ditulis.")
        return 0
    write_json_gz(part_taxonomy._PATH, rows)
    print(f"✅ {n_ok} keluarga terkurasi → {part_taxonomy._PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
