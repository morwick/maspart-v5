"""
Service: Stok Opname — sesi opname per user (draft + history) di Supabase.

Mirror stok_opname.py + supabase.SupabaseOpname. Sesi disimpan utuh sebagai
JSONB di tabel opname_sessions (kolom: session_id, username, is_draft,
payload, started_at, updated_at, finalized_at). Decoupled dari Streamlit.
"""
from __future__ import annotations

import io
import time
import uuid

import requests

from ..core.config import get_settings
from .supabase_client import _rest_url, _service_headers

_TABLE = "opname_sessions"
_TIMEOUT = 15


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _to_int(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s in ("—", "-", "nan", "None", "NaN"):
        return None
    s = s.replace(",", "").replace(".", "")
    try:
        return int(float(s))
    except Exception:
        return None


# ── Parse upload → parsed_items ──────────────────────────────────────
def parse_upload(filename: str, data: bytes) -> dict:
    import pandas as pd

    bio = io.BytesIO(data)
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(bio, dtype=str)
    else:
        df = pd.read_excel(bio, dtype=str)
    if df.empty:
        return {}

    def _find(keys: list[str]) -> str | None:
        for c in df.columns:
            cl = str(c).strip().lower()
            if any(k in cl for k in keys):
                return c
        return None

    pn_col = _find(["part number", "partnumber", "no part", "kode"]) or df.columns[0]
    qty_col = _find(["sistem", "stok", "qty", "kuantitas", "quantity"])
    if qty_col is None:
        qty_col = df.columns[1] if len(df.columns) > 1 else None
    name_col = _find(["part name", "nama", "deskripsi"])

    parsed: dict = {}
    for _, row in df.iterrows():
        pn = str(row[pn_col]).strip().upper()
        if not pn or pn in ("NAN", "NONE"):
            continue
        parsed[pn] = {
            "qty_sistem": _to_int(row[qty_col]) if qty_col else None,
            "part_name": (str(row[name_col]).strip() if name_col and str(row[name_col]) != "nan" else ""),
        }
    return parsed


def build_session(parsed_items: dict, username: str, source_filename: str = "") -> dict:
    items = {}
    for pn, payload in (parsed_items or {}).items():
        pn_key = str(pn).strip().upper()
        if not pn_key:
            continue
        items[pn_key] = {
            "qty_sistem": _to_int(payload.get("qty_sistem")) if isinstance(payload, dict) else _to_int(payload),
            "qty_fisik": None,
            "note": "",
            "part_name": (payload.get("part_name", "") if isinstance(payload, dict) else "") or "",
        }
    now = _now()
    return {
        "session_id": str(uuid.uuid4()),
        "username": username,
        "started_at": now,
        "updated_at": now,
        "finalized": False,
        "source_filename": source_filename or "",
        "items": items,
    }


# ── Supabase CRUD ────────────────────────────────────────────────────
def load_draft(username: str) -> dict | None:
    if not get_settings().supabase_configured:
        return None
    try:
        resp = requests.get(
            _rest_url(_TABLE),
            headers={**_service_headers(), "Accept": "application/json"},
            params={"select": "payload", "username": f"eq.{username}", "is_draft": "eq.true", "limit": "1"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0].get("payload") if rows else None
    except Exception:
        return None


def save_draft(username: str, session: dict) -> tuple[bool, str | None]:
    sid = session.get("session_id", "")
    if not sid:
        return False, "session_id kosong"
    session["updated_at"] = _now()
    session["username"] = username
    try:
        r0 = requests.get(
            _rest_url(_TABLE),
            headers={**_service_headers(), "Accept": "application/json"},
            params={"select": "id", "username": f"eq.{username}", "is_draft": "eq.true", "limit": "1"},
            timeout=_TIMEOUT,
        )
        r0.raise_for_status()
        body = {
            "session_id": sid,
            "payload": session,
            "started_at": session.get("started_at"),
            "updated_at": session["updated_at"],
        }
        if r0.json():
            resp = requests.patch(
                _rest_url(_TABLE),
                headers=_service_headers("return=minimal"),
                params={"username": f"eq.{username}", "is_draft": "eq.true"},
                json=body,
                timeout=_TIMEOUT,
            )
            return (resp.status_code in (200, 204)), None if resp.status_code in (200, 204) else resp.text[:200]
        resp = requests.post(
            _rest_url(_TABLE),
            headers=_service_headers("return=minimal"),
            json={**body, "username": username, "is_draft": True, "finalized_at": None},
            timeout=_TIMEOUT,
        )
        return (resp.status_code in (200, 201, 204)), None if resp.status_code in (200, 201, 204) else resp.text[:200]
    except Exception as e:
        return False, str(e)


def delete_draft(username: str) -> bool:
    try:
        resp = requests.delete(
            _rest_url(_TABLE),
            headers=_service_headers("return=minimal"),
            params={"username": f"eq.{username}", "is_draft": "eq.true"},
            timeout=_TIMEOUT,
        )
        return resp.status_code in (200, 204)
    except Exception:
        return False


def load_history(username: str, limit: int = 100) -> list:
    if not get_settings().supabase_configured:
        return []
    try:
        resp = requests.get(
            _rest_url(_TABLE),
            headers={**_service_headers(), "Accept": "application/json"},
            params={
                "select": "payload",
                "username": f"eq.{username}",
                "is_draft": "eq.false",
                "order": "finalized_at.desc",
                "limit": str(limit),
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return [r.get("payload") for r in resp.json() if r.get("payload")]
    except Exception:
        return []


def finalize(username: str, session: dict) -> tuple[bool, str | None]:
    sid = session.get("session_id", "")
    if not sid:
        return False, "session_id kosong"
    now = _now()
    session["finalized"] = True
    session["finalized_at"] = now
    session["username"] = username
    try:
        r0 = requests.get(
            _rest_url(_TABLE),
            headers={**_service_headers(), "Accept": "application/json"},
            params={
                "select": "id",
                "username": f"eq.{username}",
                "session_id": f"eq.{sid}",
                "is_draft": "eq.true",
                "limit": "1",
            },
            timeout=_TIMEOUT,
        )
        r0.raise_for_status()
        if r0.json():
            resp = requests.patch(
                _rest_url(_TABLE),
                headers=_service_headers("return=minimal"),
                params={"username": f"eq.{username}", "is_draft": "eq.true", "session_id": f"eq.{sid}"},
                json={"is_draft": False, "payload": session, "finalized_at": now, "updated_at": now},
                timeout=_TIMEOUT,
            )
            return (resp.status_code in (200, 204)), None if resp.status_code in (200, 204) else resp.text[:200]
        resp = requests.post(
            _rest_url(_TABLE),
            headers=_service_headers("return=minimal"),
            json={
                "session_id": sid,
                "username": username,
                "is_draft": False,
                "payload": session,
                "started_at": session.get("started_at"),
                "updated_at": now,
                "finalized_at": now,
            },
            timeout=_TIMEOUT,
        )
        return (resp.status_code in (200, 201, 204)), None if resp.status_code in (200, 201, 204) else resp.text[:200]
    except Exception as e:
        return False, str(e)


def delete_history_entry(username: str, session_id: str) -> bool:
    try:
        resp = requests.delete(
            _rest_url(_TABLE),
            headers=_service_headers("return=minimal"),
            params={"username": f"eq.{username}", "session_id": f"eq.{session_id}", "is_draft": "eq.false"},
            timeout=_TIMEOUT,
        )
        return resp.status_code in (200, 204)
    except Exception:
        return False
