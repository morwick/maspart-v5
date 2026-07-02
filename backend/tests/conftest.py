"""Pastikan package `app` bisa diimpor saat pytest dijalankan dari mana pun.

    cd backend && python -m pytest tests/ -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
