"""
Asisten AI MASPART — FACADE modul `app.services.ai_assistant`.

Sejak Fase 5 rombakan (2026-07-17) implementasinya dipecah menjadi file-file di
`ai_parts/` yang dimuat BERURUTAN ke namespace modul INI via exec — runtime-nya
tetap SATU modul (bukan paket): 37 file test yang mem-patch `ai.<attr>`
(mis. monkeypatch _post_chat/part_index) dan relative import `from . import x`
bekerja persis seperti saat masih satu file 9.5rb baris.

Menambah/memindah kode: edit file ai_parts/ yang sesuai; jaga urutan _PARTS
(definisi harus sudah ada sebelum dipakai saat import-time; pemakaian runtime
bebas). File baru wajib didaftarkan di _PARTS.
"""
from pathlib import Path as _Path

_PARTS_DIR = _Path(__file__).parent / "ai_parts"
_PARTS = [
    "p1_dasar.py",
    "p2_tool_specs.py",
    "p3_tools_stok.py",
    "p4_tools_diagnosa.py",
    "p5_tools_epc.py",
    "p6_tools_excel_katalog.py",
    "p7_router_dispatch.py",
    "p8_prompt.py",
    "p9_chat_loop.py",
]

for _f in _PARTS:
    _p = _PARTS_DIR / _f
    exec(compile(_p.read_text(encoding="utf-8"), str(_p), "exec"), globals())
del _f, _p
