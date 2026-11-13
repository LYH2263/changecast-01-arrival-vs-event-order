from __future__ import annotations

import json
from typing import Any

import sqlite3


def replace_stream_projection(
    conn: sqlite3.Connection,
    *,
    stream_id: int,
    rows: list[dict[str, Any]],
    updated_at: str,
) -> None:
    conn.execute("DELETE FROM projection_rows WHERE stream_id=?", (stream_id,))
    for row in rows:
        conn.execute(
            """
            INSERT INTO projection_rows(
                stream_id, entity_key, payload_json, applied_seq, applied_event_id, deleted, updated_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                stream_id,
                row["entity_key"],
                json.dumps(row.get("payload"), ensure_ascii=False) if row.get("payload") is not None else None,
                row["applied_seq"],
                row["applied_event_id"],
                1 if row.get("deleted") else 0,
                updated_at,
            ),
        )


def insert_batch_snapshot(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    stream_id: int,
    rows: list[dict[str, Any]],
) -> None:
    """Persist the terminal projection of this batch for later cross-checking."""
    for row in rows:
        conn.execute(
            """
            INSERT INTO batch_projection_rows(
                batch_id, stream_id, entity_key, payload_json, applied_seq,
                applied_event_id, deleted
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                batch_id,
                stream_id,
                row["entity_key"],
                json.dumps(row.get("payload"), ensure_ascii=False) if row.get("payload") is not None else None,
                row["applied_seq"],
                row["applied_event_id"],
                1 if row.get("deleted") else 0,
            ),
        )


def list_projections(
    conn: sqlite3.Connection,
    *,
    stream_id: int | None = None,
    include_deleted: bool = True,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM projection_rows WHERE 1=1"
    params: list[Any] = []
    if stream_id is not None:
        sql += " AND stream_id=?"
        params.append(stream_id)
    if not include_deleted:
        sql += " AND deleted=0"
    sql += " ORDER BY stream_id, entity_key"
    out: list[dict[str, Any]] = []
    for r in conn.execute(sql, params).fetchall():
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json")) if d["payload_json"] else None
        d.pop("payload_json", None)
        d["deleted"] = bool(d["deleted"])
        out.append(d)
    return out


def list_batch_snapshot(conn: sqlite3.Connection, batch_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM batch_projection_rows WHERE batch_id=? ORDER BY entity_key",
        (batch_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json")) if d["payload_json"] else None
        d.pop("payload_json", None)
        d["deleted"] = bool(d["deleted"])
        out.append(d)
    return out


def list_known_event_ids(conn: sqlite3.Connection, stream_id: int) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT event_id FROM applied_events WHERE stream_id=?",
        (stream_id,),
    ).fetchall()
    return {r["event_id"] for r in rows}


def list_ooo_buffer(conn: sqlite3.Connection, stream_id: int | None = None) -> list[dict[str, Any]]:
    if stream_id is None:
        rows = conn.execute("SELECT * FROM ooo_buffer ORDER BY id DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ooo_buffer WHERE stream_id=? ORDER BY id DESC",
            (stream_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload_json"]) if d["payload_json"] else None
        d.pop("payload_json", None)
        out.append(d)
    return out


def open_buffered_events(conn: sqlite3.Connection, stream_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM ooo_buffer WHERE stream_id=? AND status='open' ORDER BY id",
        (stream_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload_json"]) if d["payload_json"] else None
        out.append(d)
    return out


def insert_buffered_event(conn: sqlite3.Connection, *, stream_id: int, event: dict[str, Any]) -> int:
    payload = event.get("payload")
    cur = conn.execute(
        """
        INSERT INTO ooo_buffer(
            stream_id, event_id, entity_key, op, seq, event_time,
            arrival_index, payload_json, buffered_at, status, note
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            stream_id,
            event["event_id"],
            event["entity_key"],
            event["op"],
            int(event["seq"]),
            event["event_time"],
            int(event.get("arrival_index", 0)),
            json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            event["buffered_at"],
            event.get("status", "open"),
            event.get("note"),
        ),
    )
    return int(cur.lastrowid)


def mark_buffer_status(
    conn: sqlite3.Connection,
    *,
    buffer_ids: list[int],
    status: str,
    drained_batch_id: int | None = None,
    note: str | None = None,
) -> None:
    for buffer_id in buffer_ids:
        conn.execute(
            "UPDATE ooo_buffer SET status=?, drained_batch_id=?, note=? WHERE id=?",
            (status, drained_batch_id, note, buffer_id),
        )


def insert_policy_switch(
    conn: sqlite3.Connection,
    *,
    stream_id: int,
    from_order: str,
    to_order: str,
    note: str | None,
    created_at: str,
    drain_batch_id: int | None = None,
    rebuild_batch_id: int | None = None,
    buffered_settled: int = 0,
    buffered_failed: int = 0,
    running_failed: int = 0,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO policy_switches(
            stream_id, from_order, to_order, buffered_settled, buffered_failed, running_failed,
            drain_batch_id, rebuild_batch_id, note, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            stream_id,
            from_order,
            to_order,
            buffered_settled,
            buffered_failed,
            running_failed,
            drain_batch_id,
            rebuild_batch_id,
            note,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def list_policy_switches(
    conn: sqlite3.Connection, stream_id: int | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM policy_switches"
    params: list[Any] = []
    if stream_id is not None:
        sql += " WHERE stream_id=?"
        params.append(stream_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]
