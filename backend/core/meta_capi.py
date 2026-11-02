"""Meta Conversions API (CAPI) — sends CRM lead + conversion events server-side to a Meta dataset."""
import asyncio
import hashlib
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone

import httpx

from core.db import db
from core.utils import now_utc_str

log = logging.getLogger("meta_capi")
GRAPH_VERSION = "v25.0"
SETTINGS_KEY = "meta_capi"
DEFAULTS = {
    "enabled": True,
    "send_lead_event": True,
    "lead_event_name": "Lead",
    "conversion_stage": "Converted",
    "conversion_event_name": "Purchase",
    "conversion_value": None,
    "currency": "INR",
    "phone_country_code": "91",
    "use_test_code": False,
}
COUNTRY_MAP = {"india": "in", "united states": "us", "usa": "us", "united kingdom": "gb", "uk": "gb",
               "uae": "ae", "united arab emirates": "ae", "nepal": "np", "bangladesh": "bd"}
_tasks: set = set()


def _creds():
    return {
        "dataset_id": re.sub(r"\D", "", os.environ.get("META_CAPI_DATASET_ID") or ""),
        "token": (os.environ.get("META_CAPI_ACCESS_TOKEN") or "").strip(),
        "test_code": (os.environ.get("META_CAPI_TEST_EVENT_CODE") or "").strip(),
    }


async def get_config() -> dict:
    doc = await db.settings.find_one({"key": SETTINGS_KEY}, {"_id": 0, "key": 0}) or {}
    return {**DEFAULTS, **{k: v for k, v in doc.items() if k in DEFAULTS}}


def is_configured() -> bool:
    c = _creds()
    return bool(c["dataset_id"] and c["token"])


def _sha(v) -> str:
    return hashlib.sha256(str(v).encode("utf-8")).hexdigest()


def _norm_phone(raw, cc: str):
    d = re.sub(r"\D", "", str(raw or ""))
    if d.startswith("00"):
        d = d[2:]
    d = d.lstrip("0")
    if len(d) == 10 and cc:
        d = cc + d
    return d if len(d) >= 8 else None


def _alpha(v):
    v = re.sub(r"[^a-z\u0900-\u097f]", "", str(v or "").lower())
    return v or None


def build_user_data(lead: dict, cc: str) -> dict:
    ud = {"external_id": [_sha(lead["id"])]}
    email = (lead.get("email_from") or "").strip().lower()
    if "@" in email:
        ud["em"] = [_sha(email)]
    phones = {p for p in (_norm_phone(lead.get(f), cc) for f in ("phone", "mobile")) if p}
    if phones:
        ud["ph"] = [_sha(p) for p in phones]
    parts = str(lead.get("contact_name") or "").strip().split()
    if parts:
        if _alpha(parts[0]):
            ud["fn"] = [_sha(_alpha(parts[0]))]
        if len(parts) > 1 and _alpha(parts[-1]):
            ud["ln"] = [_sha(_alpha(parts[-1]))]
    if _alpha(lead.get("city")):
        ud["ct"] = [_sha(_alpha(lead.get("city")))]
    if _alpha(lead.get("state_name")):
        ud["st"] = [_sha(_alpha(lead.get("state_name")))]
    country = str(lead.get("country") or "").strip().lower()
    country = country if len(country) == 2 else COUNTRY_MAP.get(country) or ("in" if cc == "91" else None)
    if country:
        ud["country"] = [_sha(country)]
    fb_lead = re.sub(r"\D", "", str(lead.get("facebook_leadgen_id") or ""))
    if fb_lead:
        ud["lead_id"] = int(fb_lead)
    return ud


def _event_time(ts: str = None) -> int:
    if ts:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return int((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp())
        except ValueError:
            pass
    return int(time.time())


async def _send(kind: str, lead: dict, event_name: str, event_id: str, custom: dict, cfg: dict,
                event_time: int = None, force_test: bool = False) -> dict:
    c = _creds()
    event = {
        "event_name": event_name, "event_time": event_time or int(time.time()), "event_id": event_id,
        "action_source": "system_generated", "user_data": build_user_data(lead, cfg["phone_country_code"]),
        "custom_data": {"event_source": "crm", "lead_event_source": "Fundle.ai CRM", **custom},
    }
    payload = {"data": [event]}
    use_test = bool(c["test_code"]) and (force_test or cfg.get("use_test_code"))
    if use_test:
        payload["test_event_code"] = c["test_code"]
    rec = {"id": str(uuid.uuid4()), "kind": kind, "lead_id": lead.get("id"), "event_name": event_name,
           "event_id": event_id, "test": use_test, "created_at": now_utc_str(), "value": custom.get("value"),
           "match_keys": sorted(k for k in event["user_data"])}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"https://graph.facebook.com/{GRAPH_VERSION}/{c['dataset_id']}/events",
                                  params={"access_token": c["token"]}, json=payload)
        body = r.json() if r.headers.get("content-type", "").startswith(("application/json", "text/javascript")) else {"raw": r.text[:500]}
        if r.status_code == 200 and body.get("events_received"):
            rec.update(status="sent", fbtrace_id=body.get("fbtrace_id"), events_received=body.get("events_received"))
        else:
            err = body.get("error") or {}
            rec.update(status="failed", http_status=r.status_code,
                       error=err.get("error_user_msg") or err.get("message") or str(body)[:500], fbtrace_id=err.get("fbtrace_id"))
    except Exception as e:  # network / timeout — never break the CRM flow
        rec.update(status="failed", error=f"{type(e).__name__}: {e}"[:500])
    await db.capi_events.insert_one(dict(rec))
    if rec["status"] == "failed":
        log.warning("Meta CAPI %s failed for lead %s: %s", event_name, lead.get("id"), rec.get("error"))
    return rec


async def track_lead_created(lead: dict):
    cfg = await get_config()
    if not (is_configured() and cfg["enabled"] and cfg["send_lead_event"]):
        return
    et = _event_time(lead.get("create_date"))
    if time.time() - et > 7 * 86400:
        return  # Meta rejects events older than 7 days (e.g. historical backfills)
    rec = await _send("lead", lead, cfg["lead_event_name"], f"lead_{lead['id']}", {}, cfg, event_time=et)
    if rec["status"] == "sent":
        await db.leads.update_one({"id": lead["id"]}, {"$set": {"capi_lead_sent": True}})


def needs_value(cfg: dict) -> bool:
    return (cfg.get("conversion_event_name") or "").strip().lower() == "purchase" and not cfg.get("conversion_value")


async def track_conversion(lead: dict, resend: bool = False) -> dict:
    cfg = await get_config()
    if not (is_configured() and cfg["enabled"]) or lead.get("lead_stage") != cfg["conversion_stage"]:
        return None
    claimed = await db.leads.update_one({"id": lead["id"], "capi_converted_sent": {"$ne": True}},
                                        {"$set": {"capi_converted_sent": True}})
    if not claimed.modified_count:
        return None  # already reported once — never double-count a conversion
    if needs_value(cfg):
        rec = {"id": str(uuid.uuid4()), "kind": "conversion", "lead_id": lead["id"], "event_name": cfg["conversion_event_name"],
               "status": "failed", "created_at": now_utc_str(), "test": False,
               "error": "Not sent — Meta drops Purchase events without a value. Set a Value in Admin → Meta CAPI, then click Resend."}
        await db.capi_events.insert_one(dict(rec))
        await db.leads.update_one({"id": lead["id"]}, {"$set": {"capi_converted_sent": False}})
        return rec
    custom = {}
    if cfg.get("conversion_value") not in (None, ""):
        custom.update(value=float(cfg["conversion_value"]), currency=(cfg.get("currency") or "INR").upper())
    event_id = f"conv_{lead['id']}" + (f"_r{int(time.time())}" if resend else "")
    rec = await _send("conversion", lead, cfg["conversion_event_name"], event_id, custom, cfg)
    if rec["status"] != "sent":
        await db.leads.update_one({"id": lead["id"]}, {"$set": {"capi_converted_sent": False}})
    return rec


async def resend_conversion(lead: dict) -> dict:
    await db.leads.update_one({"id": lead["id"]}, {"$set": {"capi_converted_sent": False}})
    return await track_conversion(lead, resend=True)


async def send_test(lead: dict = None) -> dict:
    cfg = await get_config()
    sample = lead or {"id": 0, "email_from": "test@fundle.ai", "phone": "9999999999", "contact_name": "Test Lead",
                      "city": "Delhi", "country": "India"}
    # With a Test Event Code, send a standard "Lead" (visible in Meta → Test events, never counted);
    # without one, a harmless custom event that only validates the credentials.
    name = "Lead" if _creds()["test_code"] else "FundleCRMTest"
    return await _send("test", sample, name, f"test_{uuid.uuid4().hex[:12]}", {}, cfg, force_test=True)


def _fire(coro):
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def _created_flow(lead: dict):
    await track_lead_created(lead)
    if lead.get("lead_stage"):
        await track_conversion(lead)


def on_lead_created(lead: dict):
    if is_configured():
        _fire(_created_flow({k: v for k, v in lead.items() if k != "_id"}))


def on_stage_changed(lead: dict):
    if is_configured() and lead and lead.get("lead_stage"):
        _fire(track_conversion(lead))


async def _bulk_conversions(ids: list):
    async for lead in db.leads.find({"id": {"$in": ids}}, {"_id": 0}, max_time_ms=10000):
        await track_conversion(lead)


def on_bulk_stage_set(ids: list, stage: str):
    if is_configured() and ids and stage:
        _fire(_bulk_conversions(ids))
