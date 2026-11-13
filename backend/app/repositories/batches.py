from __future__ import annotations

import json
from typing import Any

import sqlite3


def insert_batch(
    conn: sqlite3.Connection,
    *,
    batch_no: str,
    stream_id: int,
    status: str,
    counts: dict[str, int],
    note: str | None,
    created_at: str,
    kind: str = "project",
    apply_order: str = "by_seq",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO project_batches(
            batch_no, stream_id, status, kind, apply_order, source_events,
            applied_upsert, applied_delete, skipped_duplicate, failed_events,
            held_events, projected_alive, projected_deleted, max_seq, note, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            batch_no,
            stream_id,
            status,
            kind,
            apply_order,
            counts["source_events"],
            counts["applied_upsert"],
            counts["applied_delete"],
            counts["skipped_duplicate"],
            counts.get("failed_events", 0),
            counts.get("held_events", 0),
            counts["projected_alive"],
            counts["projected_deleted"],
            counts["max_seq"],
            note,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM project_batches ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM project_batches WHERE id=?", (batch_id,)).fetchone()
    return dict(r) if r else None


def insert_applied_event(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    stream_id: int,
    event: dict[str, Any],
    created_at: str,
) -> None:
    payload = event.get("payload")
    conn.execute(
        """
        INSERT INTO applied_events(
            batch_id, stream_id, event_id, entity_key, op, seq, event_time,
            arrival_index, applied_index, apply_order, result, payload_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            batch_id,
            stream_id,
            event["event_id"],
            event["entity_key"],
            event["op"],
            event["seq"],
            event["event_time"],
            int(event.get("arrival_index", 0)),
            int(event.get("applied_index", 0)),
            event.get("apply_order", "by_seq"),
            event["result"],
            json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            created_at,
        ),
    )


def list_applied_events(
    conn: sqlite3.Connection,
    *,
    batch_id: int | None = None,
    stream_id: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM applied_events WHERE 1=1"
    params: list[Any] = []
    if batch_id is not None:
        sql += " AND batch_id=?"
        params.append(batch_id)
    if stream_id is not None:
        sql += " AND stream_id=?"
        params.append(stream_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    out: list[dict[str, Any]] = []
    for r in conn.execute(sql, params).fetchall():
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json")) if d["payload_json"] else None
        out.append(d)
    return out


def list_applied_events_asc(conn: sqlite3.Connection, batch_id: int) -> list[dict[str, Any]]:
    """Applied log in the exact order this batch applied events."""
    rows = conn.execute(
        "SELECT * FROM applied_events WHERE batch_id=? ORDER BY applied_index, id",
        (batch_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json")) if d["payload_json"] else None
        out.append(d)
    return out
