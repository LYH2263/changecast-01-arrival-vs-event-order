from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.engines.project import APPLY_ORDERS, ORDER_LABELS
from app.services import project_service as svc

router = APIRouter(tags=["streams"])


class SwitchOrderBody(BaseModel):
    apply_order: str
    note: str | None = None


@router.get("/streams")
def list_streams():
    return svc.list_streams()


@router.get("/streams/apply-orders")
def apply_orders():
    return {
        "orders": [
            {"code": order, "label": ORDER_LABELS[order], "mutually_exclusive": True}
            for order in APPLY_ORDERS
        ]
    }


@router.get("/streams/{stream_id}/compare-orders")
def compare_orders(stream_id: int):
    try:
        return svc.compare_orders(stream_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/streams/{stream_id}/compare-batches")
def compare_batches(stream_id: int):
    try:
        return svc.compare_batches(stream_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/streams/{stream_id}/verify")
def verify_stream(stream_id: int):
    try:
        return svc.verify_stream(stream_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/streams/{stream_id}/apply-order")
def switch_apply_order(stream_id: int, body: SwitchOrderBody):
    try:
        return svc.switch_apply_order(stream_id, body.apply_order, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
