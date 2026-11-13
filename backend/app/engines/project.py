"""Apply CDC events onto a projection using exactly one chosen ordering.

Two mutually exclusive orders are supported:

* ``by_seq``      — fixture events sorted by event ``seq`` ascending.
* ``by_arrival``  — fixture order preserved (the order the file delivers events).

The caller picks one order per batch; this module never reorders by the other
rule and never falls back to seq when the arrival order is requested.
"""

from __future__ import annotations

from typing import Any

APPLY_ORDERS = ("by_seq", "by_arrival")

ORDER_LABELS = {
    "by_seq": "按事件 seq 升序投影",
    "by_arrival": "按夹具到达顺序依次投影",
}


def normalize_order(apply_order: str) -> str:
    if apply_order not in APPLY_ORDERS:
        raise ValueError(f"unknown apply_order: {apply_order}")
    return apply_order


def order_events(events: list[dict[str, Any]], apply_order: str) -> list[dict[str, Any]]:
    """Return the events ordered by the *selected* rule only.

    ``by_arrival`` uses the arrival index stamped from the fixture file order.
    It deliberately does not fall back to seq sorting.
    """
    apply_order = normalize_order(apply_order)
    if apply_order == "by_arrival":
        return sorted(
            events,
            key=lambda e: (int(e.get("arrival_index", 0)), str(e.get("event_id", ""))),
        )
    return sorted(
        events,
        key=lambda e: (int(e.get("seq", 0)), str(e.get("event_id", ""))),
    )


def apply_events(
    events: list[dict[str, Any]],
    *,
    apply_order: str,
    known_event_ids: set[str] | None = None,
    base_projection: list[dict[str, Any]] | None = None,
    due_buffer_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Project events in one order.

    ``base_projection`` seeds the projection (used when settling held events on
    top of an existing terminal state); otherwise the projection starts empty,
    which is the full-rebuild path.
    """
    apply_order = normalize_order(apply_order)
    known = set(known_event_ids or ())
    buffered_ids = set(due_buffer_ids or ())

    projection: dict[str, dict[str, Any]] = {}
    for row in base_projection or ():
        projection[str(row["entity_key"])] = dict(row)

    applied_log: list[dict[str, Any]] = []
    counts = {
        "source_events": len(events),
        "applied_upsert": 0,
        "applied_delete": 0,
        "skipped_duplicate": 0,
        "max_seq": 0,
        "buffer_cleared": 0,
    }

    ordered = order_events(events, apply_order)

    for position, ev in enumerate(ordered, start=1):
        event_id = str(ev["event_id"])
        entity_key = str(ev["entity_key"])
        op = str(ev.get("op", "upsert"))
        seq = int(ev.get("seq", 0))
        event_time = str(ev.get("event_time", ""))
        counts["max_seq"] = max(counts["max_seq"], seq)

        was_buffered = event_id in buffered_ids
        if was_buffered:
            counts["buffer_cleared"] += 1

        if event_id in known:
            counts["skipped_duplicate"] += 1
            applied_log.append(
                {
                    "event_id": event_id,
                    "entity_key": entity_key,
                    "op": op,
                    "seq": seq,
                    "event_time": event_time,
                    "arrival_index": int(ev.get("arrival_index", 0)),
                    "applied_index": position,
                    "apply_order": apply_order,
                    "was_buffered": was_buffered,
                    "result": "skipped_duplicate",
                    "payload": ev.get("payload"),
                }
            )
            continue

        known.add(event_id)
        if op == "delete":
            projection[entity_key] = {
                "entity_key": entity_key,
                "payload": None,
                "applied_seq": seq,
                "applied_event_id": event_id,
                "deleted": True,
            }
            counts["applied_delete"] += 1
            result = "applied_delete"
        else:
            projection[entity_key] = {
                "entity_key": entity_key,
                "payload": ev.get("payload") or {},
                "applied_seq": seq,
                "applied_event_id": event_id,
                "deleted": False,
            }
            counts["applied_upsert"] += 1
            result = "applied_upsert"

        applied_log.append(
            {
                "event_id": event_id,
                "entity_key": entity_key,
                "op": op,
                "seq": seq,
                "event_time": event_time,
                "arrival_index": int(ev.get("arrival_index", 0)),
                "applied_index": position,
                "apply_order": apply_order,
                "was_buffered": was_buffered,
                "result": result,
                "payload": ev.get("payload"),
            }
        )

    alive = [r for r in projection.values() if not r.get("deleted")]
    deleted = [r for r in projection.values() if r.get("deleted")]
    counts["projected_alive"] = len(alive)
    counts["projected_deleted"] = len(deleted)
    return {
        "apply_order": apply_order,
        "counts": counts,
        "projection": list(projection.values()),
        "applied_log": applied_log,
        "ordered_event_ids": [str(e["event_id"]) for e in ordered],
    }


def late_arrival_ids(events: list[dict[str, Any]]) -> list[str]:
    """Events whose seq is behind the max seq already seen *in arrival order*.

    Used only by the optional "hold late events into buffer" window; it is
    computed from the fixture's arrival order, not from the projection order.
    """
    running_max = 0
    late: list[str] = []
    for ev in sorted(events, key=lambda e: int(e.get("arrival_index", 0))):
        seq = int(ev.get("seq", 0))
        if seq < running_max:
            late.append(str(ev["event_id"]))
        running_max = max(running_max, seq)
    return late


def projection_diff(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Entity-level diff between two terminal projections."""
    lmap = {r["entity_key"]: r for r in left}
    rmap = {r["entity_key"]: r for r in right}
    diff: list[dict[str, Any]] = []
    for key in sorted(set(lmap) | set(rmap)):
        a = lmap.get(key)
        b = rmap.get(key)
        if a is None or b is None:
            diff.append(
                {
                    "entity_key": key,
                    "kind": "only_left" if b is None else "only_right",
                    "left": a,
                    "right": b,
                }
            )
            continue
        same = (
            a.get("deleted") == b.get("deleted")
            and a.get("payload") == b.get("payload")
            and a.get("applied_seq") == b.get("applied_seq")
            and a.get("applied_event_id") == b.get("applied_event_id")
        )
        if not same:
            diff.append(
                {
                    "entity_key": key,
                    "kind": "value_mismatch",
                    "left": a,
                    "right": b,
                    "fields": _diff_fields(a, b),
                }
            )
    return diff


def _diff_fields(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    if a.get("deleted") != b.get("deleted"):
        fields.append("deleted")
    if a.get("payload") != b.get("payload"):
        fields.append("payload")
    if a.get("applied_seq") != b.get("applied_seq"):
        fields.append("applied_seq")
    if a.get("applied_event_id") != b.get("applied_event_id"):
        fields.append("applied_event_id")
    return fields
