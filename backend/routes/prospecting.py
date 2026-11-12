"""AI prospecting: engine control, category overview, prospect list/detail, cron trigger."""
import asyncio
import hmac
import os
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.db import db
from core.security import require_roles
from core import prospector as pe

router = APIRouter(tags=["prospecting"])
_TASKS: set = set()


def _spawn(coro):
    t = asyncio.create_task(coro)
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)


def _cron_auth(request: Request):
    secret = os.environ.get("WEBHOOK_CRON_SECRET", "")
    auth = request.headers.get("authorization", "")
    if not secret or not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:], secret):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/cron/prospecting-watchdog")
async def cron_watchdog(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    _cron_auth(request)
    enabled = (await pe.get_settings())["enabled"]
    res = await pe.watchdog(enabled)
    if res["restart"] and enabled:
        _spawn(pe.run_worker(840, "watchdog"))
    return {"ok": True, **res}


@router.post("/cron/prospecting")
async def cron_prospecting(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    _cron_auth(request)
    run_id = request.headers.get("x-webhook-id")
    if run_id and await db.cron_runs.find_one({"run_id": run_id}):
        return {"ok": True, "duplicate": True}
    await db.cron_runs.insert_one({"run_id": run_id, "name": "prospecting"})
    _spawn(pe.run_worker(840, "cron"))
    return {"ok": True, "queued": True}


class EngineSettings(BaseModel):
    enabled: bool = True
    countries: List[str] = ["India"]
    phase: str = "auto"
    concurrency: int = 3
    create_leads: bool = True


@router.get("/prospecting/overview")
async def overview(user: dict = Depends(require_roles("admin", "manager"))):
    settings = await pe.get_settings()
    lock = await db.settings.find_one({"key": "prospect_lock"}, {"_id": 0}) or {}
    from datetime import datetime, timezone
    running = bool(lock.get("until")) and lock["until"] > datetime.now(timezone.utc).isoformat()
    agg = await db.prospects.aggregate([{"$group": {
        "_id": {"country": "$country", "category": "$category"}, "brands": {"$sum": 1},
        "enriched": {"$sum": {"$cond": [{"$eq": ["$status", "enriched"]}, 1, 0]}},
        "offline": {"$sum": {"$cond": [{"$in": ["$segment", ["Offline Retail", "Omnichannel"]]}, 1, 0]}},
        "d2c": {"$sum": {"$cond": [{"$eq": ["$segment", "D2C / E-commerce"]}, 1, 0]}},
        "with_ceo": {"$sum": {"$cond": [{"$gt": ["$ceo_name", None]}, 1, 0]}},
        "stores": {"$sum": {"$ifNull": ["$store_count", 0]}}}}]).to_list(2000)
    cats = {(d["country"], d["category"]): d async for d in db.prospect_cats.find({}, {"_id": 0})}
    rows = []
    for country in sorted({*settings["countries"], *[a["_id"]["country"] for a in agg]}):
        for cat in pe.CATEGORIES:
            a = next((x for x in agg if x["_id"] == {"country": country, "category": cat}), {})
            st = cats.get((country, cat), {})
            rows.append({"country": country, "category": cat, **{k: a.get(k, 0) for k in ("brands", "enriched", "offline", "d2c", "with_ceo", "stores")},
                         "runs": st.get("runs", 0), "saturated": st.get("saturated", False), "last_run": st.get("last_run")})
    totals = {k: sum(r[k] for r in rows) for k in ("brands", "enriched", "offline", "d2c", "with_ceo")}
    recent = await db.prospect_runs.find({}, {"_id": 0}).sort("at", -1).limit(25).to_list(25)
    return {"settings": settings, "running": running, "lock": {k: lock.get(k) for k in ("started", "trigger", "last_finished", "last_stats", "ai_blocked_at", "watchdog_at", "watchdog_action")},
            "totals": totals, "categories": rows, "recent_runs": recent,
            "all_countries": pe.COUNTRIES, "all_categories": pe.CATEGORIES, "segments": pe.SEGMENTS,
            "pending_enrich": await db.prospects.count_documents({"status": "discovered"}),
            "night_window": pe.night_seconds_left() > 0}


@router.put("/prospecting/settings")
async def save_settings(body: EngineSettings, user: dict = Depends(require_roles("admin"))):
    if body.phase not in ("auto", "discovery", "enrichment"):
        raise HTTPException(status_code=400, detail="Invalid phase")
    countries = [c for c in body.countries if c in pe.COUNTRIES] or ["India"]
    await db.settings.update_one({"key": "prospecting"}, {"$set": {
        "enabled": body.enabled, "countries": countries, "phase": body.phase,
        "concurrency": max(1, min(body.concurrency, 10)), "create_leads": body.create_leads}}, upsert=True)
    return await pe.get_settings()


@router.post("/prospecting/run")
async def run_now(user: dict = Depends(require_roles("admin", "manager"))):
    _spawn(pe.run_worker(840, f"manual:{user['name']}"))
    return {"ok": True}


@router.post("/prospecting/stop")
async def stop(user: dict = Depends(require_roles("admin"))):
    await db.settings.update_one({"key": "prospecting"}, {"$set": {"enabled": False}}, upsert=True)
    return {"ok": True}


@router.get("/prospecting/prospects")
async def list_prospects(country: Optional[str] = None, category: Optional[str] = None, segment: Optional[str] = None,
                         status: Optional[str] = None, min_stores: Optional[int] = None, has_ceo: Optional[bool] = None,
                         q: Optional[str] = None, sort: str = "store_count", page: int = 1, limit: int = 50,
                         user: dict = Depends(require_roles("admin", "manager"))):
    f = {}
    for k, v in (("country", country), ("category", category), ("segment", segment), ("status", status)):
        if v:
            f[k] = v
    if min_stores:
        f["store_count"] = {"$gte": min_stores}
    if has_ceo:
        f["ceo_name"] = {"$nin": [None, ""]}
    if q:
        f["brand_name"] = {"$regex": re.escape(q), "$options": "i"}
    srt = {"store_count": [("store_count", -1), ("id", 1)], "brand": [("brand_name", 1)],
           "score": [("fit_score", -1), ("store_count", -1)], "recent": [("id", -1)]}.get(sort, [("store_count", -1)])
    limit = max(1, min(limit, 200))
    items = await db.prospects.find(f, {"_id": 0, "sources": 0}).sort(srt).skip((max(page, 1) - 1) * limit).limit(limit).to_list(limit)
    return {"items": items, "total": await db.prospects.count_documents(f), "page": page, "limit": limit}


@router.get("/prospecting/prospects/{pid}")
async def get_prospect(pid: int, user: dict = Depends(require_roles("admin", "manager"))):
    p = await db.prospects.find_one({"id": pid}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="Prospect not found")
    return p


@router.post("/prospecting/prospects/{pid}/research")
async def research_now(pid: int, user: dict = Depends(require_roles("admin", "manager"))):
    p = await db.prospects.find_one_and_update({"id": pid, "status": {"$ne": "enriching"}},
                                               {"$set": {"status": "enriching", "enrich_attempts": 0,
                                                         "enrich_started_at": pe.now_utc_str()}},
                                               projection={"_id": 0})
    if not p:
        raise HTTPException(status_code=409, detail="Research already in progress or prospect not found")
    _spawn(pe.enrich({**p, "enrich_attempts": 0}))
    return {"ok": True}


@router.post("/prospecting/prospects/{pid}/lead")
async def to_lead(pid: int, user: dict = Depends(require_roles("admin", "manager"))):
    p = await db.prospects.find_one({"id": pid}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="Prospect not found")
    return {"lead_id": await pe.upsert_lead(p, user)}
