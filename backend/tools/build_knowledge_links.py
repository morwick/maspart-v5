"""Bangun ulang data/knowledge_links.json.gz — indeks tautan entitas antar-store
pengetahuan (PN / kode DTC / model unit / ECU → posting record lintas store).

    cd backend
    python tools/build_knowledge_links.py
"""
from __future__ import annotations

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import knowledge_links  # noqa: E402


def main() -> int:
    p = knowledge_links.build_and_save()
    d = knowledge_links._load() or {}
    cak = d.get("cakupan") or {}
    print(f"✅ ditulis: {p} ({p.stat().st_size / 1024:.0f} KB)")
    print(f"   entitas tertaut: {cak.get('entitas_tertaut')}")
    print("   cakupan per store: "
          + ", ".join(f"{k}={v}" for k, v in sorted(cak.items())
                      if k != "entitas_tertaut"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
