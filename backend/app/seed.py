from __future__ import annotations

from datetime import datetime, timezone

from app.db import connect

STREAM_SEEDS = [
    {
        "code": "member_profile",
        "title": "会员档案变更",
        "domain": "member",
        "entity_key_name": "member_id",
        "payload_fields": ["display_name", "tier", "region"],
        "fixture": "member_profile.json",
        "enabled": 1,
    },
    {
        "code": "store_archive",
        "title": "门店档案变更",
        "domain": "store",
        "entity_key_name": "store_id",
        "payload_fields": ["name", "status", "city"],
        "fixture": "store_archive.json",
        "enabled": 1,
    },
    {
        "code": "channel_attr",
        "title": "渠道属性变更",
        "domain": "channel",
        "entity_key_name": "channel_code",
        "payload_fields": ["label", "active"],
        "fixture": "channel_attr.json",
        "enabled": 1,
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_column(conn, table: str, name: str, ddl: str) -> None:
    """Additive migration for existing docker volumes."""
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    import json

    conn = connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS project_streams(
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                domain TEXT NOT NULL,
                entity_key_name TEXT NOT NULL,
                payload_fields_json TEXT NOT NULL,
                fixture TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                watermark_seq INTEGER NOT NULL DEFAULT 0,
                apply_order TEXT NOT NULL DEFAULT 'by_seq'
                    CHECK(apply_order IN ('by_seq', 'by_arrival')),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS project_batches(
                id INTEGER PRIMARY KEY,
                batch_no TEXT NOT NULL UNIQUE,
                stream_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'project',
                apply_order TEXT NOT NULL DEFAULT 'by_seq',
                source_events INTEGER NOT NULL,
                applied_upsert INTEGER NOT NULL,
                applied_delete INTEGER NOT NULL,
                skipped_duplicate INTEGER NOT NULL,
                failed_events INTEGER NOT NULL DEFAULT 0,
                held_events INTEGER NOT NULL DEFAULT 0,
                projected_alive INTEGER NOT NULL,
                projected_deleted INTEGER NOT NULL,
                max_seq INTEGER NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(stream_id) REFERENCES project_streams(id)
            );
            CREATE TABLE IF NOT EXISTS projection_rows(
                id INTEGER PRIMARY KEY,
                stream_id INTEGER NOT NULL,
                entity_key TEXT NOT NULL,
                payload_json TEXT,
                applied_seq INTEGER NOT NULL,
                applied_event_id TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                UNIQUE(stream_id, entity_key),
                FOREIGN KEY(stream_id) REFERENCES project_streams(id)
            );
            CREATE TABLE IF NOT EXISTS applied_events(
                id INTEGER PRIMARY KEY,
                batch_id INTEGER NOT NULL,
                stream_id INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                entity_key TEXT NOT NULL,
                op TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_time TEXT NOT NULL,
                arrival_index INTEGER NOT NULL DEFAULT 0,
                applied_index INTEGER NOT NULL DEFAULT 0,
                apply_order TEXT NOT NULL DEFAULT 'by_seq',
                result TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(batch_id) REFERENCES project_batches(id),
                FOREIGN KEY(stream_id) REFERENCES project_streams(id)
            );
            CREATE TABLE IF NOT EXISTS batch_projection_rows(
                id INTEGER PRIMARY KEY,
                batch_id INTEGER NOT NULL,
                stream_id INTEGER NOT NULL,
                entity_key TEXT NOT NULL,
                payload_json TEXT,
                applied_seq INTEGER NOT NULL,
                applied_event_id TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                UNIQUE(batch_id, entity_key),
                FOREIGN KEY(batch_id) REFERENCES project_batches(id),
                FOREIGN KEY(stream_id) REFERENCES project_streams(id)
            );
            CREATE TABLE IF NOT EXISTS ooo_buffer(
                id INTEGER PRIMARY KEY,
                stream_id INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                entity_key TEXT NOT NULL,
                op TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_time TEXT NOT NULL,
                arrival_index INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT,
                buffered_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                drained_batch_id INTEGER,
                note TEXT,
                FOREIGN KEY(stream_id) REFERENCES project_streams(id)
            );
            CREATE TABLE IF NOT EXISTS policy_switches(
                id INTEGER PRIMARY KEY,
                stream_id INTEGER NOT NULL,
                from_order TEXT NOT NULL,
                to_order TEXT NOT NULL,
                buffered_settled INTEGER NOT NULL DEFAULT 0,
                buffered_failed INTEGER NOT NULL DEFAULT 0,
                running_failed INTEGER NOT NULL DEFAULT 0,
                drain_batch_id INTEGER,
                rebuild_batch_id INTEGER,
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(stream_id) REFERENCES project_streams(id)
            );
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        # Existing volumes from the base snapshot need additive columns.
        _ensure_column(conn, "project_streams", "apply_order", "apply_order TEXT NOT NULL DEFAULT 'by_seq'")
        _ensure_column(conn, "project_batches", "kind", "kind TEXT NOT NULL DEFAULT 'project'")
        _ensure_column(conn, "project_batches", "apply_order", "apply_order TEXT NOT NULL DEFAULT 'by_seq'")
        _ensure_column(conn, "project_batches", "failed_events", "failed_events INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "project_batches", "held_events", "held_events INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "policy_switches", "buffered_settled", "buffered_settled INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "applied_events", "arrival_index", "arrival_index INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "applied_events", "applied_index", "applied_index INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "applied_events", "apply_order", "apply_order TEXT NOT NULL DEFAULT 'by_seq'")
        _ensure_column(conn, "applied_events", "payload_json", "payload_json TEXT")
        _ensure_column(conn, "ooo_buffer", "arrival_index", "arrival_index INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "ooo_buffer", "drained_batch_id", "drained_batch_id INTEGER")
        _ensure_column(conn, "ooo_buffer", "note", "note TEXT")

        if conn.execute("SELECT COUNT(*) c FROM project_streams").fetchone()["c"] == 0:
            now = _now()
            for s in STREAM_SEEDS:
                conn.execute(
                    """
                    INSERT INTO project_streams(
                        code, title, domain, entity_key_name, payload_fields_json,
                        fixture, enabled, watermark_seq, apply_order, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        s["code"],
                        s["title"],
                        s["domain"],
                        s["entity_key_name"],
                        json.dumps(s["payload_fields"], ensure_ascii=False),
                        s["fixture"],
                        s["enabled"],
                        0,
                        "by_seq",
                        now,
                    ),
                )
        if conn.execute("SELECT COUNT(*) c FROM settings").fetchone()["c"] == 0:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?)",
                ("apply_order", "by_seq"),
            )
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?)",
                ("duplicate_policy", "skip"),
            )
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?)",
                ("tombstone_mode", "soft_delete"),
            )
        conn.commit()
    finally:
        conn.close()
