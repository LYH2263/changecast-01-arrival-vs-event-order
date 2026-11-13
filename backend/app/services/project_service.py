from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.project import (
    APPLY_ORDERS,
    ORDER_LABELS,
    apply_events,
    late_arrival_ids,
    normalize_order,
    projection_diff,
)
from app.repositories import batches as batch_repo
from app.repositories import projections as proj_repo
from app.repositories import streams as stream_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_fixture(name: str) -> list[dict[str, Any]]:
    """Load a fixture preserving file order as the arrival order."""
    path = FIXTURES_DIR / name
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"fixture {name} must be a JSON array")
    events: list[dict[str, Any]] = []
    for position, raw in enumerate(data, start=1):
        event = dict(raw)
        event["arrival_index"] = position
        event["event_id"] = str(event["event_id"])
        event["entity_key"] = str(event["entity_key"])
        event["op"] = str(event.get("op", "upsert"))
        event["seq"] = int(event.get("seq", 0))
        event["event_time"] = str(event.get("event_time", ""))
        events.append(event)
    return events


def _buffer_extras(conn, stream_id: int, fixture_ids: set[str]) -> list[dict[str, Any]]:
    """Buffered rows that are not present in the fixture, as apply-able events."""
    extras: list[dict[str, Any]] = []
    for i, row in enumerate(proj_repo.open_buffered_events(conn, stream_id), start=1):
        if row["event_id"] in fixture_ids:
            continue
        extras.append(
            {
                "event_id": row["event_id"],
                "entity_key": row["entity_key"],
                "op": row["op"],
                "seq": int(row["seq"]),
                "event_time": row["event_time"],
                "arrival_index": 10_000 + i,
                "payload": row.get("payload"),
            }
        )
    return extras


def _next_batch_no(conn) -> str:
    seq = conn.execute("SELECT COUNT(*) c FROM project_batches").fetchone()["c"] + 1
    stamp = _now()[:10].replace("-", "")
    return f"PB-{stamp}-{seq:04d}"


def _create_batch(
    conn,
    *,
    stream: dict[str, Any],
    kind: str,
    apply_order: str,
    result: dict[str, Any],
    note: str | None,
    created_at: str,
    counts_override: dict[str, int] | None = None,
) -> int:
    counts = dict(result["counts"])
    if counts_override:
        counts.update(counts_override)
    batch_id = batch_repo.insert_batch(
        conn,
        batch_no=_next_batch_no(conn),
        stream_id=stream["id"],
        status="completed",
        kind=kind,
        apply_order=apply_order,
        counts=counts,
        note=note,
        created_at=created_at,
    )
    proj_repo.insert_batch_snapshot(
        conn, batch_id=batch_id, stream_id=stream["id"], rows=result["projection"]
    )
    for ev in result["applied_log"]:
        batch_repo.insert_applied_event(
            conn, batch_id=batch_id, stream_id=stream["id"], event=ev, created_at=created_at
        )
    proj_repo.replace_stream_projection(
        conn, stream_id=stream["id"], rows=result["projection"], updated_at=created_at
    )
    stream_repo.set_watermark(conn, stream["id"], result["counts"]["max_seq"])
    return batch_id


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        streams = stream_repo.list_streams(conn)
        for s in streams:
            s["open_buffer_count"] = conn.execute(
                "SELECT COUNT(*) c FROM ooo_buffer WHERE stream_id=? AND status='open'",
                (s["id"],),
            ).fetchone()["c"]
            last = conn.execute(
                "SELECT batch_no, kind, apply_order, created_at FROM project_batches "
                "WHERE stream_id=? ORDER BY id DESC LIMIT 1",
                (s["id"],),
            ).fetchone()
            s["last_batch"] = dict(last) if last else None
        global_order = conn.execute(
            "SELECT value FROM settings WHERE key='apply_order'"
        ).fetchone()
        return {
            "stream_count": len(streams),
            "enabled_streams": sum(1 for s in streams if s["enabled"]),
            "batch_count": conn.execute("SELECT COUNT(*) c FROM project_batches").fetchone()["c"],
            "projection_alive": conn.execute(
                "SELECT COUNT(*) c FROM projection_rows WHERE deleted=0"
            ).fetchone()["c"],
            "projection_deleted": conn.execute(
                "SELECT COUNT(*) c FROM projection_rows WHERE deleted=1"
            ).fetchone()["c"],
            "ooo_buffered": conn.execute(
                "SELECT COUNT(*) c FROM ooo_buffer WHERE status='open'"
            ).fetchone()["c"],
            "apply_order": global_order["value"] if global_order else "by_seq",
            "apply_order_labels": ORDER_LABELS,
            "streams": streams,
        }
    finally:
        conn.close()


def list_streams() -> list[dict[str, Any]]:
    conn = connect()
    try:
        streams = stream_repo.list_streams(conn)
        for s in streams:
            s["open_buffer_count"] = conn.execute(
                "SELECT COUNT(*) c FROM ooo_buffer WHERE stream_id=? AND status='open'",
                (s["id"],),
            ).fetchone()["c"]
            s["apply_order_label"] = ORDER_LABELS.get(s["apply_order"], s["apply_order"])
        return streams
    finally:
        conn.close()


def list_batches(limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = batch_repo.list_batches(conn, limit=limit)
        streams = {s["id"]: s for s in stream_repo.list_streams(conn)}
        for r in rows:
            s = streams.get(r["stream_id"])
            r["stream_code"] = s["code"] if s else None
            r["stream_title"] = s["title"] if s else None
            r["apply_order_label"] = ORDER_LABELS.get(r["apply_order"], r["apply_order"])
        return rows
    finally:
        conn.close()


def list_projections(stream_id: int | None = None, include_deleted: bool = True) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = proj_repo.list_projections(
            conn, stream_id=stream_id, include_deleted=include_deleted
        )
        streams = {s["id"]: s for s in stream_repo.list_streams(conn)}
        for r in rows:
            s = streams.get(r["stream_id"])
            r["stream_code"] = s["code"] if s else None
            r["stream_title"] = s["title"] if s else None
        return rows
    finally:
        conn.close()


def list_events(batch_id: int | None = None, stream_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_applied_events(conn, batch_id=batch_id, stream_id=stream_id)
    finally:
        conn.close()


def list_ooo_buffer(stream_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = proj_repo.list_ooo_buffer(conn, stream_id=stream_id)
        streams = {s["id"]: s for s in stream_repo.list_streams(conn)}
        for r in rows:
            s = streams.get(r["stream_id"])
            r["stream_title"] = s["title"] if s else None
            r["stream_code"] = s["code"] if s else None
        return rows
    finally:
        conn.close()


def list_policy_switches(stream_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = proj_repo.list_policy_switches(conn, stream_id=stream_id)
        streams = {s["id"]: s for s in stream_repo.list_streams(conn)}
        for r in rows:
            s = streams.get(r["stream_id"])
            r["stream_title"] = s["title"] if s else None
            r["from_order_label"] = ORDER_LABELS.get(r["from_order"], r["from_order"])
            r["to_order_label"] = ORDER_LABELS.get(r["to_order"], r["to_order"])
        return rows
    finally:
        conn.close()


def list_settings() -> dict[str, str]:
    conn = connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def run_project(stream_id: int, note: str | None = None, hold_late: bool = False) -> dict[str, Any]:
    """Load the CDC fixture and project it under this stream's current order.

    When ``hold_late`` is set, events that arrive behind the running max seq
    (computed from fixture arrival order) are parked in the open buffer and are
    not applied by this batch. A normal run consumes and settles any open
    buffer under the same, current order and records that in the batch trace.
    """
    conn = connect()
    try:
        stream = stream_repo.get_stream(conn, stream_id)
        if not stream:
            raise ValueError("stream not found")
        if not stream["enabled"]:
            raise ValueError("stream disabled")
        apply_order = normalize_order(stream["apply_order"])

        events = _load_fixture(stream["fixture"])
        fixture_ids = {e["event_id"] for e in events}
        open_rows = proj_repo.open_buffered_events(conn, stream_id)
        open_ids = {r["event_id"] for r in open_rows}

        if hold_late:
            late_ids = set(late_arrival_ids(events)) | open_ids
            late_ids &= fixture_ids
            for ev in events:
                if ev["event_id"] in late_ids and ev["event_id"] not in open_ids:
                    proj_repo.insert_buffered_event(
                        conn,
                        stream_id=stream_id,
                        event={
                            **ev,
                            "buffered_at": _now(),
                            "status": "open",
                            "note": "按到达序判定为晚到，挂起等待清算",
                        },
                    )
            excluded_ids = late_ids
            due_buffer_ids: set[str] = set()
        else:
            excluded_ids = set()
            due_buffer_ids = open_ids

        # Buffered rows absent from the fixture still need settling; append them
        # under their stamped arrival index so the same single order applies.
        extras = [
            e for e in _buffer_extras(conn, stream_id, fixture_ids)
            if e["event_id"] in due_buffer_ids
        ]
        considered = [e for e in events if e["event_id"] not in excluded_ids] + extras
        result = apply_events(
            considered,
            apply_order=apply_order,
            base_projection=None,
            due_buffer_ids=due_buffer_ids,
        )
        counts_override = {
            "source_events": len(considered) + len(excluded_ids),
            "held_events": len(excluded_ids),
            "failed_events": 0,
        }
        now = _now()
        batch_note = note
        if excluded_ids:
            held_note = f"挂起晚到事件 {len(excluded_ids)} 条：{', '.join(sorted(excluded_ids))}"
            batch_note = f"{note} · {held_note}" if note else held_note
        elif due_buffer_ids:
            drain_note = f"随本批按当前序清算缓冲 {len(due_buffer_ids)} 条：{', '.join(sorted(due_buffer_ids))}"
            batch_note = f"{note} · {drain_note}" if note else drain_note

        batch_id = _create_batch(
            conn,
            stream=stream,
            kind="project",
            apply_order=apply_order,
            result=result,
            note=batch_note,
            created_at=now,
            counts_override=counts_override,
        )

        if due_buffer_ids:
            buffered_row_ids = [
                r["id"] for r in open_rows if r["event_id"] in due_buffer_ids
            ]
            proj_repo.mark_buffer_status(
                conn,
                buffer_ids=buffered_row_ids,
                status="drained",
                drained_batch_id=batch_id,
                note="随投影批按当前应用序清算",
            )
        conn.commit()

        return _batch_detail(conn, batch_id, stream)
    finally:
        conn.close()


def switch_apply_order(
    stream_id: int, to_order: str, note: str | None = None
) -> dict[str, Any]:
    """Switch a stream's projection order.

    Chosen policy for unsettled work: settle first, then switch, always with a
    separate trace. Open buffer rows are drained by a dedicated batch that runs
    entirely under the *old* order; batches still in progress are failed under
    the old order. Only then is the stream's order flipped and the projection
    rebuilt from scratch under the new order. No batch ever mixes the two rules.
    """
    normalize_order(to_order)
    conn = connect()
    try:
        stream = stream_repo.get_stream(conn, stream_id)
        if not stream:
            raise ValueError("stream not found")
        from_order = normalize_order(stream["apply_order"])
        if from_order == to_order:
            raise ValueError(
                f"该流已在使用「{ORDER_LABELS[to_order]}」，无需切换（互斥策略同时只能一种）"
            )

        events = _load_fixture(stream["fixture"])
        fixture_ids = {e["event_id"] for e in events}
        open_rows = proj_repo.open_buffered_events(conn, stream_id)
        running = conn.execute(
            "SELECT * FROM project_batches WHERE stream_id=? AND status NOT IN ('completed','failed')",
            (stream_id,),
        ).fetchall()

        now = _now()
        drain_batch_id: int | None = None
        buffered_failed = 0

        if open_rows:
            drain_events = events + _buffer_extras(conn, stream_id, fixture_ids)
            buffered_ids = {r["event_id"] for r in open_rows}
            drain_result = apply_events(
                drain_events,
                apply_order=from_order,
                due_buffer_ids=buffered_ids,
            )
            drain_ids = sorted(buffered_ids)
            drain_note = (
                f"切换前按旧策略（{ORDER_LABELS[from_order]}）清算缓冲 "
                f"{len(open_rows)} 条：{', '.join(drain_ids)}"
            )
            drain_batch_id = _create_batch(
                conn,
                stream=stream,
                kind="buffer_drain",
                apply_order=from_order,
                result=drain_result,
                note=drain_note,
                created_at=now,
                counts_override={"held_events": 0, "failed_events": 0},
            )
            proj_repo.mark_buffer_status(
                conn,
                buffer_ids=[r["id"] for r in open_rows],
                status="drained",
                drained_batch_id=drain_batch_id,
                note=f"切换至{ORDER_LABELS[to_order]}前，按旧策略清算",
            )

        running_failed = 0
        for b in running:
            running_failed += 1
            conn.execute(
                "UPDATE project_batches SET status='failed_on_switch', note=? WHERE id=?",
                (
                    f"{b['note'] or ''} · 切换应用序时按旧策略失败掉未完成批次".strip(" ·"),
                    b["id"],
                ),
            )

        stream_repo.set_apply_order(conn, stream_id, to_order)

        has_state = conn.execute(
            "SELECT COUNT(*) c FROM projection_rows WHERE stream_id=?", (stream_id,)
        ).fetchone()["c"] > 0 or conn.execute(
            "SELECT COUNT(*) c FROM project_batches WHERE stream_id=?", (stream_id,)
        ).fetchone()["c"] > 0

        rebuild_batch_id: int | None = None
        if has_state:
            rebuild_result = apply_events(events, apply_order=to_order, due_buffer_ids=set())
            rebuild_note = (
                f"切换应用序后按新策略（{ORDER_LABELS[to_order]}）重建终态"
            )
            if note:
                rebuild_note = f"{note} · {rebuild_note}"
            rebuild_batch_id = _create_batch(
                conn,
                stream=stream,
                kind="policy_rebuild",
                apply_order=to_order,
                result=rebuild_result,
                note=rebuild_note,
                created_at=now,
                counts_override={"held_events": 0, "failed_events": 0},
            )

        switch_id = proj_repo.insert_policy_switch(
            conn,
            stream_id=stream_id,
            from_order=from_order,
            to_order=to_order,
            note=note,
            created_at=now,
            drain_batch_id=drain_batch_id,
            rebuild_batch_id=rebuild_batch_id,
            buffered_settled=len(open_rows),
            buffered_failed=buffered_failed,
            running_failed=running_failed,
        )
        conn.commit()

        detail = {
            "switch_id": switch_id,
            "stream_id": stream_id,
            "from_order": from_order,
            "to_order": to_order,
            "from_order_label": ORDER_LABELS[from_order],
            "to_order_label": ORDER_LABELS[to_order],
            "buffered_settled": len(open_rows),
            "buffered_failed": buffered_failed,
            "running_failed": running_failed,
            "drain_batch_id": drain_batch_id,
            "rebuild_batch_id": rebuild_batch_id,
            "drain_batch": batch_repo.get_batch(conn, drain_batch_id) if drain_batch_id else None,
            "rebuild_batch": batch_repo.get_batch(conn, rebuild_batch_id) if rebuild_batch_id else None,
            "note": note,
        }
        return detail
    finally:
        conn.close()


def _batch_detail(conn, batch_id: int, stream: dict[str, Any] | None = None) -> dict[str, Any]:
    batch = batch_repo.get_batch(conn, batch_id)
    if not batch:
        raise ValueError("batch not found")
    if stream is None:
        stream = stream_repo.get_stream(conn, batch["stream_id"])
    batch["stream_code"] = stream["code"] if stream else None
    batch["stream_title"] = stream["title"] if stream else None
    batch["apply_order_label"] = ORDER_LABELS.get(batch["apply_order"], batch["apply_order"])
    batch["current_stream_apply_order"] = stream["apply_order"] if stream else None
    batch["counts"] = {
        "source_events": batch["source_events"],
        "applied_upsert": batch["applied_upsert"],
        "applied_delete": batch["applied_delete"],
        "skipped_duplicate": batch["skipped_duplicate"],
        "held_events": batch.get("held_events", 0),
        "failed_events": batch.get("failed_events", 0),
        "projected_alive": batch["projected_alive"],
        "projected_deleted": batch["projected_deleted"],
        "max_seq": batch["max_seq"],
    }
    batch["projection_preview"] = proj_repo.list_batch_snapshot(conn, batch_id)[:20]
    batch["event_preview"] = batch_repo.list_applied_events(conn, batch_id=batch_id, limit=20)
    return batch


def get_batch(batch_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        return _batch_detail(conn, batch_id)
    finally:
        conn.close()


def _derived_from_log(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recompute terminal rows purely from an applied log."""
    state: dict[str, dict[str, Any]] = {}
    for ev in log:
        if ev["result"] == "skipped_duplicate":
            continue
        key = ev["entity_key"]
        if ev["op"] == "delete" or ev["result"] == "applied_delete":
            state[key] = {
                "entity_key": key,
                "payload": None,
                "applied_seq": ev["seq"],
                "applied_event_id": ev["event_id"],
                "deleted": True,
            }
        else:
            state[key] = {
                "entity_key": key,
                "payload": ev.get("payload") or {},
                "applied_seq": ev["seq"],
                "applied_event_id": ev["event_id"],
                "deleted": False,
            }
    return list(state.values())


def verify_batch(conn, batch_id: int) -> dict[str, Any]:
    batch = batch_repo.get_batch(conn, batch_id)
    if not batch:
        raise ValueError("batch not found")
    log = batch_repo.list_applied_events_asc(conn, batch_id)
    snapshot = proj_repo.list_batch_snapshot(conn, batch_id)
    checks: list[dict[str, Any]] = []

    expected_source = (
        batch["applied_upsert"]
        + batch["applied_delete"]
        + batch["skipped_duplicate"]
        + batch.get("held_events", 0)
        + batch.get("failed_events", 0)
    )
    checks.append(
        {
            "name": "批次计数自洽",
            "ok": batch["source_events"] == expected_source,
            "detail": (
                f"源事件 {batch['source_events']} = 写入 {batch['applied_upsert']} + "
                f"删除 {batch['applied_delete']} + 跳过 {batch['skipped_duplicate']} + "
                f"挂起 {batch.get('held_events', 0)} + 失败 {batch.get('failed_events', 0)}"
            ),
        }
    )

    log_counts = {"applied_upsert": 0, "applied_delete": 0, "skipped_duplicate": 0}
    for ev in log:
        if ev["result"] in log_counts:
            log_counts[ev["result"]] += 1
    checks.append(
        {
            "name": "留痕条数与批次计数一致",
            "ok": (
                log_counts["applied_upsert"] == batch["applied_upsert"]
                and log_counts["applied_delete"] == batch["applied_delete"]
                and log_counts["skipped_duplicate"] == batch["skipped_duplicate"]
            ),
            "detail": (
                f"留痕 写入 {log_counts['applied_upsert']} / 删除 {log_counts['applied_delete']} / "
                f"跳过 {log_counts['skipped_duplicate']}"
            ),
        }
    )

    offenders = [ev["event_id"] for ev in log if ev["apply_order"] != batch["apply_order"]]
    checks.append(
        {
            "name": "单批未混用应用序",
            "ok": not offenders,
            "detail": (
                f"本批应用序 {batch['apply_order']}，留痕全部一致"
                if not offenders
                else f"以下事件应用序不一致：{', '.join(offenders)}"
            ),
        }
    )

    log_order_ok = [int(ev["applied_index"]) for ev in log] == list(
        range(1, len(log) + 1)
    )
    checks.append(
        {
            "name": "执行序连续可重放",
            "ok": log_order_ok,
            "detail": (
                f"applied_index 依次为 1..{len(log)}，顺序即本批实际应用顺序"
                if log_order_ok
                else "applied_index 不连续，留痕无法按记录顺序重放"
            ),
        }
    )

    derived = _derived_from_log(log)
    diff = projection_diff(snapshot, derived)
    checks.append(
        {
            "name": "事件留痕可重放出投影快照",
            "ok": not diff,
            "detail": (
                f"由 {len(log)} 条留痕重放得到 {len(derived)} 行，与批次快照 {len(snapshot)} 行一致"
                if not diff
                else f"{len(diff)} 个实体不一致：" + ", ".join(d["entity_key"] for d in diff)
            ),
            "diff": diff,
        }
    )

    alive = sum(1 for r in snapshot if not r["deleted"])
    deleted = sum(1 for r in snapshot if r["deleted"])
    checks.append(
        {
            "name": "快照存活/删除数与批次一致",
            "ok": alive == batch["projected_alive"] and deleted == batch["projected_deleted"],
            "detail": (
                f"快照 存活 {alive} / 已删 {deleted}；批次 "
                f"{batch['projected_alive']} / {batch['projected_deleted']}"
            ),
        }
    )

    return {
        "batch_id": batch_id,
        "batch_no": batch["batch_no"],
        "stream_id": batch["stream_id"],
        "kind": batch["kind"],
        "apply_order": batch["apply_order"],
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
    }


def verify_batch_for_api(batch_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        return verify_batch(conn, batch_id)
    finally:
        conn.close()


def verify_stream(stream_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        stream = stream_repo.get_stream(conn, stream_id)
        if not stream:
            raise ValueError("stream not found")
        batch_rows = conn.execute(
            "SELECT id FROM project_batches WHERE stream_id=? AND status='completed' ORDER BY id",
            (stream_id,),
        ).fetchall()
        batch_verifications = [verify_batch(conn, r["id"]) for r in batch_rows]

        live = proj_repo.list_projections(conn, stream_id=stream_id)
        latest = None
        if batch_rows:
            latest_id = batch_rows[-1]["id"]
            latest = _batch_detail(conn, latest_id, stream)
            snapshot = proj_repo.list_batch_snapshot(conn, latest_id)
            live_diff = projection_diff(snapshot, live)
            live_check = {
                "name": "当前投影与最近批次快照一致",
                "ok": not live_diff,
                "detail": (
                    f"当前投影 {len(live)} 行，最近批次（{latest['batch_no']}）快照 {len(snapshot)} 行，一致"
                    if not live_diff
                    else f"{len(live_diff)} 个实体不一致："
                    + ", ".join(d["entity_key"] for d in live_diff)
                ),
                "diff": live_diff,
            }
        else:
            live_check = {
                "name": "当前投影与最近批次快照一致",
                "ok": True,
                "detail": "尚无批次，无快照可对照",
                "diff": [],
            }

        return {
            "stream_id": stream_id,
            "stream_code": stream["code"],
            "stream_title": stream["title"],
            "apply_order": stream["apply_order"],
            "apply_order_label": ORDER_LABELS[stream["apply_order"]],
            "ok": all(v["ok"] for v in batch_verifications) and live_check["ok"],
            "live_check": live_check,
            "batches": batch_verifications,
        }
    finally:
        conn.close()


def compare_orders(stream_id: int) -> dict[str, Any]:
    """Project the same fixture under both orders without writing anything."""
    conn = connect()
    try:
        stream = stream_repo.get_stream(conn, stream_id)
        if not stream:
            raise ValueError("stream not found")
        events = _load_fixture(stream["fixture"])
        by_seq = apply_events(events, apply_order="by_seq")
        by_arrival = apply_events(events, apply_order="by_arrival")
        diff = projection_diff(by_seq["projection"], by_arrival["projection"])
        open_rows = proj_repo.open_buffered_events(conn, stream_id)
        return {
            "stream_id": stream_id,
            "stream_code": stream["code"],
            "stream_title": stream["title"],
            "current_apply_order": stream["apply_order"],
            "open_buffer_count": len(open_rows),
            "by_seq": {
                "apply_order": "by_seq",
                "apply_order_label": ORDER_LABELS["by_seq"],
                "counts": by_seq["counts"],
                "projection": sorted(by_seq["projection"], key=lambda r: r["entity_key"]),
                "order": by_seq["ordered_event_ids"],
            },
            "by_arrival": {
                "apply_order": "by_arrival",
                "apply_order_label": ORDER_LABELS["by_arrival"],
                "counts": by_arrival["counts"],
                "projection": sorted(by_arrival["projection"], key=lambda r: r["entity_key"]),
                "order": by_arrival["ordered_event_ids"],
            },
            "diff": diff,
            "same_terminal_state": not diff,
        }
    finally:
        conn.close()


def compare_batches(stream_id: int) -> dict[str, Any]:
    """Compare the latest completed batch snapshots for each order."""
    conn = connect()
    try:
        stream = stream_repo.get_stream(conn, stream_id)
        if not stream:
            raise ValueError("stream not found")
        picked: dict[str, Any] = {}
        for order in APPLY_ORDERS:
            r = conn.execute(
                "SELECT * FROM project_batches WHERE stream_id=? AND apply_order=? "
                "AND status='completed' ORDER BY id DESC LIMIT 1",
                (stream_id, order),
            ).fetchone()
            if r:
                picked[order] = dict(r)

        snapshots: dict[str, list[dict[str, Any]]] = {}
        for order, b in picked.items():
            snapshots[order] = proj_repo.list_batch_snapshot(conn, b["id"])

        diff: list[dict[str, Any]] = []
        if "by_seq" in snapshots and "by_arrival" in snapshots:
            diff = projection_diff(snapshots["by_seq"], snapshots["by_arrival"])

        verifications = {
            order: verify_batch(conn, b["id"]) for order, b in picked.items()
        }

        return {
            "stream_id": stream_id,
            "stream_code": stream["code"],
            "stream_title": stream["title"],
            "current_apply_order": stream["apply_order"],
            "batches": picked,
            "snapshots": {
                order: sorted(rows, key=lambda x: x["entity_key"])
                for order, rows in snapshots.items()
            },
            "diff": diff,
            "same_terminal_state": (not diff) if len(snapshots) == 2 else None,
            "verifications": verifications,
        }
    finally:
        conn.close()
