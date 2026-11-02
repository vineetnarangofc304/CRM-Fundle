from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core import meta_capi as capi
from core.db import db
from core.security import require_roles

router = APIRouter(prefix="/admin/meta-capi", tags=["meta-capi"])


class CapiConfig(BaseModel):
    enabled: Optional[bool] = None
    send_lead_event: Optional[bool] = None
    lead_event_name: Optional[str] = None
    conversion_stage: Optional[str] = None
    conversion_event_name: Optional[str] = None
    conversion_value: Optional[float] = None
    currency: Optional[str] = None
    phone_country_code: Optional[str] = None
    use_test_code: Optional[bool] = None


class CapiTest(BaseModel):
    lead_id: Optional[int] = None


@router.get("")
async def capi_status(user: dict = Depends(require_roles("admin", "manager"))):
    c = capi._creds()
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    stats = {s: 0 for s in ("sent", "failed")}
    async for row in db.capi_events.aggregate([
        {"$match": {"created_at": {"$gte": since}, "kind": {"$ne": "test"}}},
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ]):
        stats[row["_id"]] = row["n"]
    recent = await db.capi_events.find({}, {"_id": 0}).sort("created_at", -1).limit(25).to_list(25)
    ds = c["dataset_id"]
    return {
        "configured": capi.is_configured(),
        "dataset_id": ds,
        "token_set": bool(c["token"]),
        "test_code_set": bool(c["test_code"]),
        "config": await capi.get_config(),
        "stats_7d": stats,
        "recent": recent,
    }


@router.patch("")
async def capi_update(body: CapiConfig, user: dict = Depends(require_roles("admin"))):
    upd = body.model_dump(exclude_unset=True)
    for k in ("lead_event_name", "conversion_event_name", "conversion_stage"):
        if k in upd and not (upd[k] or "").strip():
            raise HTTPException(status_code=400, detail=f"{k.replace('_', ' ')} cannot be empty")
        if k in upd:
            upd[k] = upd[k].strip()
    if "phone_country_code" in upd:
        upd["phone_country_code"] = "".join(ch for ch in (upd["phone_country_code"] or "") if ch.isdigit())
    if capi.needs_value({**(await capi.get_config()), **upd}):
        raise HTTPException(status_code=400, detail="Meta ignores Purchase events without a value — enter a Value (e.g. your average conversion amount) or choose another event such as Schedule")
    await db.settings.update_one({"key": capi.SETTINGS_KEY}, {"$set": {**upd, "key": capi.SETTINGS_KEY}}, upsert=True)
    return await capi.get_config()


@router.post("/resend/{lead_id}")
async def capi_resend(lead_id: int, user: dict = Depends(require_roles("admin"))):
    lead = await db.leads.find_one({"id": lead_id}, {"_id": 0}, max_time_ms=5000)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    cfg = await capi.get_config()
    if lead.get("lead_stage") != cfg["conversion_stage"]:
        raise HTTPException(status_code=400, detail=f"Lead #{lead_id} is not in the '{cfg['conversion_stage']}' stage (current: {lead.get('lead_stage') or 'none'}) — only converted leads can be sent")
    last = await db.capi_events.find_one({"lead_id": lead_id, "kind": "conversion"}, {"_id": 0}, sort=[("created_at", -1)])
    if last and last.get("status") == "sent" and (last.get("value") is not None or "_r" in (last.get("event_id") or "")):
        raise HTTPException(status_code=400, detail=f"Meta already received the conversion for lead #{lead_id} — resending would count it twice")
    return await capi.resend_conversion(lead)


@router.post("/test")
async def capi_test(body: CapiTest, user: dict = Depends(require_roles("admin"))):
    if not capi.is_configured():
        raise HTTPException(status_code=400, detail="Meta CAPI credentials are not configured")
    lead = None
    if body.lead_id:
        lead = await db.leads.find_one({"id": body.lead_id}, {"_id": 0}, max_time_ms=5000)
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
    rec = await capi.send_test(lead)
    rec.pop("_id", None)
    return rec
