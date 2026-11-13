from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import project_service as svc

router = APIRouter(tags=["batches"])


class RunBody(BaseModel):
    stream_id: int
    note: str | None = None
    hold_late: bool = False


@router.get("/batches")
def list_batches(limit: int = 50):
    return svc.list_batches(limit=limit)


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int):
    try:
        return svc.get_batch(batch_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/batches/{batch_id}/verify")
def verify_batch(batch_id: int):
    try:
        return svc.verify_batch_for_api(batch_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/project")
def run_project(body: RunBody):
    try:
        return svc.run_project(body.stream_id, body.note, hold_late=body.hold_late)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
