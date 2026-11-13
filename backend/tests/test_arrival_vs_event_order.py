from __future__ import annotations

import json

import pytest

import app.config as config
from app import seed
from app.engines.project import apply_events, late_arrival_ids
from app.repositories import projections as proj_repo
from app.services import project_service as svc


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    config.DATA_DIR = tmp_path
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    from app.main import app

    seed.init_db()
    with TestClient(app) as c:
        yield c


FIXTURES = config.FIXTURES_DIR


def _events(name: str) -> list[dict]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    out = []
    for i, raw in enumerate(data, start=1):
        ev = dict(raw)
        ev["arrival_index"] = i
        ev["event_id"] = str(ev["event_id"])
        ev["entity_key"] = str(ev["entity_key"])
        ev["seq"] = int(ev["seq"])
        out.append(ev)
    return out


# --- engine-level --------------------------------------------------------


def test_by_arrival_never_reresorts_by_seq():
    events = _events("member_profile.json")
    result = apply_events(events, apply_order="by_arrival")
    assert result["ordered_event_ids"] == [e["event_id"] for e in events]
    # mp-007 carries the lowest seq of the M1003 edits but arrives last, so under
    # the arrival rule it is the one that stands.
    m1003 = next(r for r in result["projection"] if r["entity_key"] == "M1003")
    assert m1003["payload"]["tier"] == "platinum"
    assert m1003["applied_event_id"] == "mp-007"


def test_by_seq_and_by_arrival_diverge_on_store_fixture():
    events = _events("store_archive.json")
    by_seq = apply_events(events, apply_order="by_seq")
    by_arrival = apply_events(events, apply_order="by_arrival")
    s02_seq = next(r for r in by_seq["projection"] if r["entity_key"] == "S02")
    s02_arr = next(r for r in by_arrival["projection"] if r["entity_key"] == "S02")
    assert s02_seq["payload"]["status"] == "closed"
    assert s02_arr["payload"]["status"] == "open"
    assert s02_seq["applied_event_id"] != s02_arr["applied_event_id"]


def test_arrival_log_is_monotonic_in_applied_index():
    result = apply_events(_events("channel_attr.json"), apply_order="by_arrival")
    indexes = [ev["applied_index"] for ev in result["applied_log"]]
    assert indexes == list(range(1, len(indexes) + 1))
    assert all(ev["apply_order"] == "by_arrival" for ev in result["applied_log"])


def test_late_arrival_detection_uses_arrival_stream():
    events = _events("member_profile.json")
    late = late_arrival_ids(events)
    assert "mp-007" in late  # seq 3 arrives after seq 7 already advanced
    assert "mp-006" not in late


# --- service/API ---------------------------------------------------------


def test_run_project_records_selected_order_and_counts(client):
    streams = client.get("/api/streams").json()
    stream = next(s for s in streams if s["code"] == "member_profile")
    resp = client.post("/api/project", json={"stream_id": stream["id"]}).json()
    assert resp["apply_order"] == "by_seq"
    assert resp["apply_order_label"] == "按事件 seq 升序投影"
    assert resp["counts"]["source_events"] == 7
    assert resp["counts"]["applied_upsert"] == 6
    assert resp["counts"]["applied_delete"] == 1
    assert resp["counts"]["held_events"] == 0
    assert resp["status"] == "completed"

    verify = client.get(f"/api/batches/{resp['id']}/verify").json()
    assert verify["ok"] is True
    assert all(c["ok"] for c in verify["checks"])


def test_switch_drains_buffer_under_old_order_then_rebuilds(client):
    streams = client.get("/api/streams").json()
    stream = next(s for s in streams if s["code"] == "member_profile")
    sid = stream["id"]

    held = client.post(
        "/api/project", json={"stream_id": sid, "hold_late": True, "note": "先演示挂起"}
    ).json()
    assert held["counts"]["held_events"] >= 1
    buffer_rows = client.get(f"/api/ooo-buffer?stream_id={sid}").json()
    assert any(r["status"] == "open" for r in buffer_rows)

    switched = client.post(
        f"/api/streams/{sid}/apply-order",
        json={"apply_order": "by_arrival", "note": "展会对照：改到到达序"},
    ).json()

    assert switched["to_order"] == "by_arrival"
    # buffer settled by a dedicated batch that ran under the OLD order
    assert switched["drain_batch"] is not None
    assert switched["drain_batch"]["apply_order"] == "by_seq"
    assert switched["drain_batch"]["kind"] == "buffer_drain"
    assert switched["buffered_settled"] == 1
    # and the terminal state was rebuilt under the NEW order
    assert switched["rebuild_batch"]["apply_order"] == "by_arrival"
    assert switched["rebuild_batch"]["kind"] == "policy_rebuild"

    drain_verify = client.get(
        f"/api/batches/{switched['drain_batch_id']}/verify"
    ).json()
    assert drain_verify["ok"] is True
    rebuild_verify = client.get(
        f"/api/batches/{switched['rebuild_batch_id']}/verify"
    ).json()
    assert rebuild_verify["ok"] is True

    settled = [
        r for r in client.get(f"/api/ooo-buffer?stream_id={sid}").json()
        if r["status"] == "open"
    ]
    assert settled == []

    proj = client.get(f"/api/projections?stream_id={sid}").json()
    m1003 = next(r for r in proj if r["entity_key"] == "M1003")
    assert m1003["payload"]["tier"] == "platinum"
    assert m1003["applied_event_id"] == "mp-007"


def test_switch_rejects_same_order(client):
    streams = client.get("/api/streams").json()
    sid = streams[0]["id"]
    r = client.post(f"/api/streams/{sid}/apply-order", json={"apply_order": "by_seq"})
    assert r.status_code == 400
    assert "无需切换" in r.json()["detail"]


def test_switch_fails_running_batches_with_trace(client):
    streams = client.get("/api/streams").json()
    stream = next(s for s in streams if s["code"] == "channel_attr")
    sid = stream["id"]
    client.post("/api/project", json={"stream_id": sid})
    # fabricate an in-progress batch, then switch while it exists
    from app.db import connect as db_connect

    conn = db_connect()
    conn.execute(
        "UPDATE project_batches SET status='running' WHERE stream_id=? AND id="
        "(SELECT MAX(id) FROM project_batches WHERE stream_id=?)",
        (sid, sid),
    )
    conn.commit()
    conn.close()

    switched = client.post(
        f"/api/streams/{sid}/apply-order", json={"apply_order": "by_arrival"}
    ).json()
    assert switched["running_failed"] == 1
    batches = client.get("/api/batches").json()
    failed = [b for b in batches if b["status"] == "failed_on_switch"]
    assert failed and failed[0]["stream_id"] == sid


def test_compare_orders_is_read_only_and_shows_divergence(client):
    streams = client.get("/api/streams").json()
    sid = next(s for s in streams if s["code"] == "store_archive")["id"]
    before = client.get("/api/batches").json()
    diff = client.get(f"/api/streams/{sid}/compare-orders").json()
    after = client.get("/api/batches").json()
    assert len(before) == len(after)
    assert diff["same_terminal_state"] is False
    assert any(d["entity_key"] == "S02" for d in diff["diff"])


def test_verify_then_compare_batches_same_fixture(client):
    streams = client.get("/api/streams").json()
    sid = next(s for s in streams if s["code"] == "store_archive")["id"]
    client.post("/api/project", json={"stream_id": sid})
    client.post(f"/api/streams/{sid}/apply-order", json={"apply_order": "by_arrival"})
    client.post("/api/project", json={"stream_id": sid})

    cmp = client.get(f"/api/streams/{sid}/compare-batches").json()
    assert set(cmp["snapshots"]) == {"by_seq", "by_arrival"}
    assert cmp["same_terminal_state"] is False
    for v in cmp["verifications"].values():
        assert v["ok"] is True


def test_normal_run_settles_fixtureless_buffer_under_current_order(client):
    streams = client.get("/api/streams").json()
    sid = next(s for s in streams if s["code"] == "channel_attr")["id"]
    client.post("/api/project", json={"stream_id": sid, "hold_late": True})

    from app.db import connect as db_connect

    conn = db_connect()
    conn.execute(
        "UPDATE ooo_buffer SET event_id='ch-999', seq=0, "
        "payload_json='{\"label\": \"通道补录\", \"active\": false}' "
        "WHERE stream_id=? AND status='open'",
        (sid,),
    )
    conn.commit()
    conn.close()

    run = client.post("/api/project", json={"stream_id": sid}).json()
    assert run["apply_order"] == "by_seq"
    assert run["counts"]["source_events"] >= 7
    rows = client.get(f"/api/ooo-buffer?stream_id={sid}").json()
    assert all(r["status"] == "drained" for r in rows)
    assert all(r["drained_batch_id"] == run["id"] for r in rows)
    verify = client.get(f"/api/batches/{run['id']}/verify").json()
    assert verify["ok"] is True
