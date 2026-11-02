import re
import secrets
from typing import Optional
from urllib.parse import urlparse, parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.db import db
from core.security import require_roles
from core.utils import log_message, next_id, now_utc_str, run_automations, to_ist_str, ist_date_parts, check_duplicate, check_duplicate_today, search_norm, ensure_catalog, pick_available_caller, pick_any_caller, queue_lead_for_assignment
from routes.catalogs import bust_catalogs
from core import meta_capi as capi

router = APIRouter(tags=["webhooks"])

FIELD_ALIASES = {
    "name": ["name", "full_name", "fullname", "lead_name"],
    "contact_name": ["contact_name", "name", "full_name", "fullname"],
    "phone": ["phone", "phone_number", "mobile", "contact", "whatsapp"],
    "email_from": ["email", "email_from", "email_address"],
    "city": ["city"],
    "state_name": ["state", "state_name", "region"],
    "query": ["query", "message", "question"],
    "company_name": ["company_name", "company", "organization", "organisation"],
    "designation": ["designation", "job_title", "title", "role"],
    "team_size": ["team_size", "company_size", "employees"],
    "campaign_name": ["campaign_name", "campaign", "utm_campaign"],
    "ads_platform": ["ads_platform", "platform", "utm_source"],
    "ads_name": ["ads_name", "ad_name"],
    "ads_campaign_name": ["ads_campaign_name", "adset_name"],
    "source_lead": ["source_lead", "source", "lead_source", "source_name"],
    "utm_source": ["utm_source", "utmsource"],
    "utm_medium": ["utm_medium", "utmmedium", "medium", "ads_medium", "lead_medium"],
    "utm_campaign": ["utm_campaign", "utmcampaign", "campaign", "campaign_name"],
    "conversion_page": ["conversion_page", "page_url", "pageurl", "page", "page_name", "pagename",
                        "form_name", "formname", "form", "landing_page", "landing_page_url",
                        "source_url", "sourceurl", "referrer", "referer", "current_url", "url"],
}


@router.post("/webhook/lead/{token}")
async def webhook_lead(token: str, request: Request):
    hook = await db.webhooks.find_one({"token": token, "active": True}, max_time_ms=3000)
    if not hook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    try:
        payload = await request.json()
    except Exception:
        form = await request.form()
        payload = dict(form)
    if not isinstance(payload, dict):
        payload = {}

    # Attribution (UTMs) reaches us from a website / AI agent in one of THREE ways. We honour all
    # three so UTMs are never silently dropped:
    #   1) explicit fields in the POST body            -> utm_source=googleads
    #   2) query params on the webhook URL itself       -> …/webhook/lead/TOKEN?utm_source=googleads&utm_medium=Youtube
    #   3) embedded in the landing-page / referrer URL   -> page_url=https://example.com/?utm_source=googleads&utm_medium=Youtube
    # Body values take precedence over URL query params on conflicts.
    query = dict(request.query_params)
    merged = {**query, **payload}

    lower = {str(k).strip().lower().replace(" ", "_"): v for k, v in merged.items()}
    data = {}
    for field, aliases in FIELD_ALIASES.items():
        for a in aliases:
            if a in lower and lower[a] not in (None, ""):
                data[field] = str(lower[a]).strip()
                break
    extras = {k: v for k, v in lower.items() if v not in (None, "")}
    # map admin-defined custom fields (Case 4: form/ads field mapping)
    defs = await db.custom_fields.find({"active": True}).to_list(300)
    for d in defs:
        for alias in [d["key"], d["label"]] + (d.get("aliases") or []):
            a = str(alias).strip().lower().replace(" ", "_")
            if a in lower and lower[a] not in (None, ""):
                extras[d["key"]] = str(lower[a]).strip()
                break

    # (way 3) Parse utm_* out of ANY URL the site forwarded (landing page / referrer / page url),
    # e.g. "https://example.com/?utm_source=googleads&utm_medium=Youtube". Only fills gaps — never
    # overwrites an explicit utm_* that was already sent.
    _url_vals = [str(v) for v in lower.values() if isinstance(v, str) and "?" in v and "utm_" in v.lower()]
    for _u in _url_vals:
        try:
            _qs = parse_qs(urlparse(_u).query)
        except Exception:
            continue
        for _k in ("utm_source", "utm_medium", "utm_campaign"):
            if not data.get(_k) and _qs.get(_k):
                data[_k] = str(_qs[_k][0]).strip()

    # Fallback so the dedicated utm_* fields are populated no matter which key variant the site
    # used: mirror source/campaign from the ads_platform / campaign_name attribution fields when
    # a utm_* key wasn't sent verbatim (e.g. the form posts `platform` / `campaign`).
    if not data.get("utm_source") and data.get("ads_platform"):
        data["utm_source"] = data["ads_platform"]
    if not data.get("utm_campaign") and data.get("campaign_name"):
        data["utm_campaign"] = data["campaign_name"]

    # Persist the raw payload + URL query + resolved UTMs on the webhook record so Admins can SEE
    # exactly what each integration sends (Admin → Webhooks → "Last payload"). This ends the blind
    # guessing whenever attribution looks wrong on a lead.
    await db.webhooks.update_one({"id": hook["id"]}, {"$set": {
        "last_payload": {k: (str(v)[:500] if v is not None else v) for k, v in lower.items()},
        "last_query": {k: str(v)[:500] for k, v in query.items()},
        "last_utm": {k: data.get(k) for k in ("utm_source", "utm_medium", "utm_campaign")},
        "last_payload_at": now_utc_str(),
    }})

    now = now_utc_str()
    phone_digits = re.sub(r"\D", "", data.get("phone") or "")[-10:]

    # SAME-DAY de-dupe (client requirement): if an ACTIVE lead for this number was already
    # created TODAY, do NOT create another lead or consume a caller assignment (the Website AI
    # Agent re-posts the same enquiry repeatedly). Merge the repeat onto today's lead and return
    # its id. A same-phone lead from a PREVIOUS day is treated as a genuinely NEW enquiry.
    existing_today = await check_duplicate_today(phone_digits)
    if existing_today:
        existing_id = existing_today
        src = data.get("source_lead") or hook.get("source_default") or "website"
        bits = [f"source: {src}"]
        if data.get("query"):
            bits.append(f"query: {data['query']}")
        if data.get("conversion_page"):
            bits.append(f"page: {data['conversion_page']}")
        await db.leads.update_one({"id": existing_id}, {"$set": {"write_date": now}})
        await log_message(existing_id,
                          f"🔁 Repeat web enquiry via '{hook['name']}' ({'; '.join(bits)}) — same-day duplicate merged into this lead, no new lead created",
                          subtype="comment")
        await db.webhooks.update_one({"id": hook["id"]}, {"$inc": {"hits": 1}})
        if src:
            await ensure_catalog("source_lead", src)
            bust_catalogs()
        return {"ok": True, "lead_id": existing_id, "duplicate": True, "merged_into": existing_id, "merged_same_day": True}

    # Not a same-day repeat → create a new lead. Still FLAG it if an older same-phone lead
    # exists (visibility only, no merge) so callers can see the prior history.
    dup = await check_duplicate(phone_digits)

    lid = await next_id("lead")
    user_id = None
    queue_it = False
    if hook.get("assign_round_robin"):
        # Presence-based round-robin: prefer callers who are Available/On Call. If NObody is
        # available, fall back to round-robin across ALL active callers so the lead is never
        # left invisible/unassigned. Only queue if there are no active callers at all.
        settings = await db.settings.find_one({"key": "assignment"})
        prefer = settings["user_ids"] if (settings and settings.get("enabled") and settings.get("user_ids")) else None
        user_id = await pick_available_caller(prefer)
        if user_id is None:
            user_id = await pick_any_caller(prefer)
            queue_it = user_id is None

    # Canonical source: reuse the catalog's existing casing (ensure_catalog matches
    # case-insensitively) so "website"/"Website" never split into two separate sources.
    src_raw = data.get("source_lead") or hook.get("source_default") or "website"
    _src_cat = await ensure_catalog("source_lead", src_raw)
    source_lead = _src_cat.get("name") or src_raw

    doc = {
        "id": lid, "active": True, "stage_id": 1, "type": "lead", "priority": "0",
        "name": data.get("name") or data.get("contact_name") or data.get("phone") or "Web Lead",
        "tags": hook.get("tag_ids") or [],
        "lead_stage": hook.get("lead_stage_default"),
        "source_lead": source_lead,
        "user_id": user_id,
        "original_user_id": user_id,
        "create_date": now, "create_date_ist": to_ist_str(now), "write_date": now,
        "custom": extras, "webhook_id": hook["id"], "_utm_v": 2,
        "phone_digits": phone_digits,
        "is_duplicate": dup["is_duplicate"], "duplicate_of": dup["duplicate_of"],
        **{k: v for k, v in data.items() if k not in ("name", "source_lead")},
    }
    doc.update(ist_date_parts(doc["create_date_ist"]))
    doc.update(search_norm(doc))
    await db.leads.insert_one(doc)
    if queue_it:
        await queue_lead_for_assignment(lid)
    await db.webhooks.update_one({"id": hook["id"]}, {"$inc": {"hits": 1}})
    # Register the lead's source + UTM values in the catalog so they show in the Source / UTM
    # dropdown filters (e.g. "Website AI Agent", or utm_source "meta"). Idempotent get-or-create.
    _changed = False
    if doc.get("source_lead"):
        await ensure_catalog("source_lead", doc["source_lead"]); _changed = True
    for _ctype in ("utm_source", "utm_medium", "utm_campaign"):
        if doc.get(_ctype):
            await ensure_catalog(_ctype, doc[_ctype]); _changed = True
    if _changed:
        bust_catalogs()
    await log_message(lid, f"Lead captured via webhook '{hook['name']}'")
    if dup["is_duplicate"]:
        await log_message(lid, f"⚠️ Same number as an earlier lead #{dup['duplicate_of']} (different day — kept as a new enquiry)", subtype="comment")
    await run_automations("on_create", doc)
    capi.on_lead_created(doc)
    return {"ok": True, "lead_id": lid}


class WebhookCreate(BaseModel):
    name: str
    source_default: Optional[str] = "website"
    lead_stage_default: Optional[str] = None
    tag_ids: Optional[list] = None
    assign_round_robin: bool = True


@router.get("/webhooks")
async def list_webhooks(user: dict = Depends(require_roles("admin", "manager"))):
    return await db.webhooks.find({}, {"_id": 0}).sort("id", 1).to_list(100)


@router.post("/webhooks")
async def create_webhook(body: WebhookCreate, user: dict = Depends(require_roles("admin"))):
    wid = await next_id("webhook")
    doc = {
        "id": wid, "name": body.name, "token": secrets.token_urlsafe(16),
        "source_default": body.source_default, "lead_stage_default": body.lead_stage_default,
        "tag_ids": body.tag_ids or [], "assign_round_robin": body.assign_round_robin,
        "active": True, "hits": 0, "created_at": now_utc_str(),
    }
    await db.webhooks.insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.patch("/webhooks/{wid}")
async def update_webhook(wid: int, body: dict, user: dict = Depends(require_roles("admin"))):
    allowed = {"name", "source_default", "lead_stage_default", "tag_ids", "assign_round_robin", "active"}
    updates = {k: v for k, v in body.items() if k in allowed}
    res = await db.webhooks.update_one({"id": wid}, {"$set": updates})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return await db.webhooks.find_one({"id": wid}, {"_id": 0})


@router.delete("/webhooks/{wid}")
async def delete_webhook(wid: int, user: dict = Depends(require_roles("admin"))):
    await db.webhooks.delete_one({"id": wid})
    return {"ok": True}
