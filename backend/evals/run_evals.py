"""
Eval regresi Asisten AI — jalankan golden questions lewat ai_assistant.chat()
NYATA (DeepSeek + tool asli) dan cek ekspektasinya. Dipakai SEBELUM deploy
perubahan prompt/tool untuk memastikan perilaku lama tidak rusak.

    cd backend
    python evals/run_evals.py                 # semua kasus 'lokal' (default)
    python evals/run_evals.py --net           # ikutkan kasus 'epc'/'weichai' (butuh jaringan EPC)
    python evals/run_evals.py --only guard    # hanya kasus yang id-nya memuat 'guard'
    python evals/run_evals.py --list          # daftar kasus tanpa menjalankan
    python evals/run_evals.py -v              # tampilkan jawaban penuh tiap kasus

Catatan:
- Butuh DEEPSEEK_API_KEY di backend/.env (dicek di awal). Tiap kasus = 1+ panggilan
  API DeepSeek → ada biaya kecil; jalankan seperlunya, bukan tiap ketikan.
- Run pertama membangun index katalog dari Excel (bisa ~1 menit); run berikutnya
  pakai cache .cache/*.pkl.
- Kasus ber-tag 'epc'/'weichai' menembak EPC Sinotruk/Weichai sungguhan — default
  di-SKIP; aktifkan dengan --net.
- Hasil lengkap ditulis ke evals/last_run.json (di-gitignore) untuk dibedah.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_EVALS_DIR.parent))  # backend/ → agar 'app' bisa diimpor

from app.core.config import get_settings          # noqa: E402
from app.services import ai_assistant             # noqa: E402

GOLDEN = _EVALS_DIR / "golden.json"
LAST_RUN = _EVALS_DIR / "last_run.json"
NET_TAGS = {"epc", "weichai"}
# Akun eval: role admin = seluruh tool tersedia (kasus tidak menyentuh data pesanan).
EVAL_USER = {"username": "eval-runner", "role": "admin"}


def _contains(hay: str, needle: str) -> bool:
    return needle.casefold() in (hay or "").casefold()


def check_case(case: dict, reply: str, tools_used: list[str],
               tool_pns: set[str] | None = None) -> list[str]:
    """Kembalikan daftar alasan gagal ([] = lolos). Lolos bila blok `expect`
    utama lolos ATAU salah satu blok `expect_alt` lolos (mis. asisten memilih
    minta VIN dulu — sesuai aturan domain — alih-alih langsung cari)."""
    fails = _check_expect(case, case.get("expect") or {}, reply, tools_used, tool_pns or set())
    if fails:
        for alt in case.get("expect_alt") or []:
            if not _check_expect(case, alt, reply, tools_used, tool_pns or set()):
                return []
    return fails


def _check_expect(case: dict, exp: dict, reply: str, tools_used: list[str],
                  tool_pns: set[str]) -> list[str]:
    fails: list[str] = []

    t_any = exp.get("tools_any") or []
    if t_any and not any(t in tools_used for t in t_any):
        fails.append(f"tools_any: tak satu pun dari {t_any} terpakai (terpakai: {tools_used or '—'})")
    for t in exp.get("tools_none") or []:
        if t in tools_used:
            fails.append(f"tools_none: tool terlarang '{t}' terpakai")

    r_any = exp.get("reply_any") or []
    if r_any and not any(_contains(reply, s) for s in r_any):
        fails.append(f"reply_any: jawaban tidak memuat satu pun dari {r_any}")
    for s in exp.get("reply_all") or []:
        if not _contains(reply, s):
            fails.append(f"reply_all: jawaban tidak memuat '{s}'")

    up = (reply or "").upper()
    p_any = exp.get("pn_any") or []
    if p_any and not any(p.upper() in up for p in p_any):
        fails.append(f"pn_any: jawaban tidak memuat satu pun PN dari {p_any}")
    for p in exp.get("pn_all") or []:
        if p.upper() not in up:
            fails.append(f"pn_all: jawaban tidak memuat PN {p}")

    if exp.get("no_new_pn"):
        # PN di jawaban hanya boleh dari (a) pesan user, atau (b) hasil tool turn
        # ini (di-spy oleh runner) — sama seperti definisi 'grounded' di chat().
        allowed = set(tool_pns)
        for t in case.get("turns") or [{"role": "user", "content": case.get("question", "")}]:
            if (t.get("role") or "") == "user":
                allowed |= ai_assistant._extract_pns(t.get("content") or "")
        new = sorted(ai_assistant._extract_pns(reply) - allowed)
        if new:
            fails.append(f"no_new_pn: jawaban memuat PN tak bersumber (dugaan karangan lolos guard): {new}")

    return fails


def main() -> int:
    ap = argparse.ArgumentParser(description="Eval regresi Asisten AI (golden questions).")
    ap.add_argument("--only", default="", help="jalankan hanya kasus yang id-nya memuat substring ini")
    ap.add_argument("--tag", default="", help="jalankan hanya kasus ber-tag ini (mis. lokal/epc)")
    ap.add_argument("--net", action="store_true", help="ikutkan kasus jaringan (tag epc/weichai)")
    ap.add_argument("--limit", type=int, default=0, help="maksimal N kasus")
    ap.add_argument("--list", action="store_true", help="daftar kasus lalu keluar (tanpa API)")
    ap.add_argument("-v", "--verbose", action="store_true", help="cetak jawaban penuh tiap kasus")
    args = ap.parse_args()

    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cases: list[dict] = data["cases"]

    if args.only:
        cases = [c for c in cases if args.only.casefold() in c["id"].casefold()]
    if args.tag:
        cases = [c for c in cases if args.tag in (c.get("tags") or [])]
    elif not args.net:
        skipped = [c["id"] for c in cases if NET_TAGS & set(c.get("tags") or [])]
        cases = [c for c in cases if not (NET_TAGS & set(c.get("tags") or []))]
        if skipped:
            print(f"(skip {len(skipped)} kasus jaringan — jalankan dengan --net untuk ikut: {', '.join(skipped)})")
    if args.limit:
        cases = cases[: args.limit]

    if args.list:
        for c in cases:
            print(f"  {c['id']:35s} tags={','.join(c.get('tags') or [])}")
        print(f"{len(cases)} kasus.")
        return 0

    if not cases:
        print("Tidak ada kasus yang cocok filter.")
        return 1
    if not get_settings().ai_configured:
        print("❌ DEEPSEEK_API_KEY belum di-set di backend/.env — eval butuh model sungguhan.")
        return 1

    print(f"Menjalankan {len(cases)} kasus (model: {get_settings().deepseek_model}) …\n")
    results, n_fail = [], 0
    t_all = time.time()
    # Spy _run_tool: rekam PN dari hasil tool per kasus, untuk cek no_new_pn
    # (definisi 'grounded' yang sama dengan guard di chat()).
    orig_run_tool = ai_assistant._run_tool
    tool_pns: set[str] = set()

    def _spy_run_tool(name, args, user):
        result = orig_run_tool(name, args, user)
        tool_pns.update(ai_assistant._extract_pns(
            json.dumps(result, ensure_ascii=False, default=str)))
        return result

    ai_assistant._run_tool = _spy_run_tool
    for i, case in enumerate(cases, 1):
        turns = case.get("turns") or [{"role": "user", "content": case["question"]}]
        tool_pns.clear()
        t0 = time.time()
        try:
            out = ai_assistant.chat(EVAL_USER, turns)
            reply = out.get("reply") or ""
            tools_used = out.get("tools_used") or []
            fails = check_case(case, reply, tools_used, tool_pns)
        except Exception as e:  # error API/tool = kasus gagal, eval jalan terus
            reply, tools_used, fails = "", [], [f"EXCEPTION: {type(e).__name__}: {e}"]
        dt = time.time() - t0

        ok = not fails
        n_fail += 0 if ok else 1
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {i:2d}/{len(cases)} {case['id']:35s} {dt:5.1f}s  tools={','.join(tools_used) or '—'}")
        for f in fails:
            print(f"       ↳ {f}")
        if args.verbose and reply:
            print("       jawaban: " + reply.replace("\n", "\n                ")[:1500] + "\n")

        results.append({"id": case["id"], "pass": ok, "seconds": round(dt, 1),
                        "tools_used": tools_used, "fails": fails, "reply": reply})

    ai_assistant._run_tool = orig_run_tool
    n_pass = len(cases) - n_fail
    print(f"\n── HASIL: {n_pass}/{len(cases)} lolos, {n_fail} gagal · {time.time()-t_all:.0f}s total ──")
    LAST_RUN.write_text(json.dumps({"pass": n_pass, "fail": n_fail, "results": results},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Detail lengkap: {LAST_RUN}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
