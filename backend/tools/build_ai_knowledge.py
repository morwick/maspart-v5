"""Bangun ulang data/ai_knowledge.json — pengetahuan ter-derive dari data fakta
(catalog_bom.json + gudang_config.json) untuk blok prompt Asisten AI.

    cd backend
    python tools/build_ai_knowledge.py
"""
from __future__ import annotations

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import ai_knowledge  # noqa: E402


def main() -> int:
    p = ai_knowledge.build_and_save()
    d = ai_knowledge._load() or {}
    print(f"✅ ditulis: {p}")
    print(f"   prefix: {len(d.get('prefix_pn') or [])} · sub-prefix: "
          f"{len(d.get('sub_prefix_pn') or [])} · gudang: {len(d.get('gudang') or [])} · "
          f"PN unik: {(d.get('cakupan') or {}).get('pn_unik_bom')}")
    print("\n--- PRATINJAU BLOK PROMPT ---")
    print(ai_knowledge.knowledge_block({"role": "admin"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
