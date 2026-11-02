"""WhatsApp Cloud API webhook (inbound) + admin endpoints (introspection / test)."""
import json
import re
import uuid
import mimetypes
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from core import whatsapp_cloud as wac
from core.db import db
from core.security import require_roles
from core.storage import put_object, APP_NAME
from core.utils import log_message, next_id, now_utc_str, run_automation_by_id

router = APIRouter(tags=["whatsapp-cloud"])

_MEDIA_TYPES = ("image", "video", "audio", "document", "sticker")
_KIND_LABEL = {"image": "📷 Photo", "video": "🎥 Video", "audio": "🎤 Voice note",
               "document": "📄 Document", "sticker": "🌟 Sticker"}


def _extract_media(m: dict):
    """Pull the media object (image/video/audio/voice/document/sticker) out of an inbound
    message → the media id + mime/filename/caption the CRM needs to fetch and store it."""
    mtype = m.get("type")
    if mtype not in _MEDIA_TYPES:
        return None
    obj = m.get(mtype) or {}
    if not obj.get("id"):
        return None
    kind = {"image": "image", "sticker": "image", "video": "video",
            "audio": "audio", "document": "document"}[mtype]
    return {"media_id": obj["id"], "mime_type": obj.get("mime_type"),
            "filename": obj.get("filename"), "caption": obj.get("caption"),
            "voice": bool(obj.get("voice")), "kind": kind, "wa_type": mtype}


async def _store_inbound_media(media: dict) -> dict:
    """Download the media from Meta and persist it to object storage; returns the
    wa_message media fields (media_url/media_type/media_name/mime_type) or {} on failure."""
    dl = await wac.download_media(media["media_id"])
    if not dl.get("ok"):
        return {}
    ctype = (media.get("mime_type") or dl.get("mime_type") or "application/octet-stream")
    ext = mimetypes.guess_extension(ctype.split(";")[0].strip()) or (".oga" if media.get("voice") else ".bin")
    path = f"{APP_NAME}/wa/{uuid.uuid4()}{ext}"
    try:
        res = await put_object(path, dl["data"], ctype)
    except Exception:
        return {}
    name = media.get("filename") or (f"voice-note{ext}" if media.get("voice") else f"{media['kind']}{ext}")
    return {"media_url": f"/api/whatsapp/media?path={res['path']}",
            "media_type": media["kind"], "media_name": name, "mime_type": ctype}


async def _next_channel_id() -> int:
    """Allocate a wa_channel id, self-healing the counter to max(existing id) so
    auto-created inbound threads never collide with migrated/higher ids (a drifted
    counter otherwise raises DuplicateKeyError and silently drops inbound messages
    from every new number)."""
    mx = await db.wa_channels.find_one({}, sort=[("id", -1)], projection={"id": 1})
    max_id = int((mx or {}).get("id") or 0)
    cur = await db.counters.find_one({"_id": "wa_channel"})
    seq = int((cur or {}).get("seq") or 0)
    if max_id > seq:
        await db.counters.update_one({"_id": "wa_channel"}, {"$set": {"seq": max_id}}, upsert=True)
    return await next_id("wa_channel")


# ---- Inbound webhook verification ----
@router.get("/webhooks/whatsapp")
async def wa_verify(request: Request):
    c = await wac.get_config()
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") and p.get("hub.verify_token") == c.get("verify_token") and p.get("hub.challenge"):
        return PlainTextResponse(content=p.get("hub.challenge"), status_code=200)
    raise HTTPException(status_code=403, detail="Verification failed")


# ---- Inbound messages ----
@router.post("/webhooks/whatsapp")
async def wa_webhook(request: Request):
    c = await wac.get_config()
    if not c.get("app_secret"):
        raise HTTPException(status_code=503, detail="WhatsApp not configured")
    body = await request.body()
    if not wac.verify_signature(c["app_secret"], body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        payload = json.loads(body.decode())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    stored = 0
    status_updates = 0
    # diagnostics — record what Meta actually delivered (esp. delivered/read events)
    log_statuses = []
    log_matched = 0
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                # Case 23 — per-message delivery status lifecycle (sent→delivered→read / failed)
                for st in value.get("statuses", []):
                    wamid = st.get("id")
                    new_status = st.get("status")  # sent | delivered | read | failed
                    if not wamid or not new_status:
                        continue
                    log_statuses.append({"wamid": wamid, "status": new_status})
                    now = now_utc_str()
                    err = None
                    if st.get("errors"):
                        err = (st["errors"][0] or {}).get("title") or str(st["errors"][0])
                    res_wam = await db.wa_messages.update_one(
                        {"wamid": wamid},
                        {"$set": {"status": new_status, "status_at": now, "error": err},
                         "$push": {"status_history": {"status": new_status, "at": now}}},
                    )
                    if res_wam.modified_count:
                        status_updates += 1
                    # Case 5 — update the tracked outbound record with lifecycle + failure detail
                    failure_type = None
                    error_code = None
                    if st.get("errors"):
                        e0 = st["errors"][0] or {}
                        error_code = e0.get("code")
                        ed = e0.get("error_data")
                        failure_type = ed.get("details") if isinstance(ed, dict) else e0.get("href")
                    setd = {"status": new_status, "status_at": now, "error": err,
                            "failure_type": failure_type, "error_code": error_code}
                    tr = await db.wa_tracking.update_one(
                        {"wamid": wamid},
                        {"$set": setd, "$push": {"status_history": {"status": new_status, "at": now}}},
                    )
                    if tr.modified_count:
                        status_updates += 1
                        log_matched += 1
                for m in value.get("messages", []):
                    frm = m.get("from")
                    mtype = m.get("type")
                    now = now_utc_str()
                    # Inbound emoji reaction → attach to the target message, skip as a chat line
                    if mtype == "reaction":
                        rc = m.get("reaction") or {}
                        tgt_wamid, emoji = rc.get("message_id"), rc.get("emoji")
                        if tgt_wamid:
                            await db.wa_messages.update_one({"wamid": tgt_wamid}, {"$set": {"reaction": emoji or None}})
                            await db.wa_tracking.update_one({"wamid": tgt_wamid}, {"$set": {"reaction": emoji or None}})
                        continue
                    # Case 5 — Quick Reply / interactive reply button tap
                    reply_title = reply_id = None
                    if mtype == "button":
                        b = m.get("button") or {}
                        reply_title, reply_id = b.get("text"), b.get("payload")
                    elif mtype == "interactive":
                        br = (m.get("interactive") or {}).get("button_reply") or {}
                        reply_title, reply_id = br.get("title"), br.get("id")
                    text = (m.get("text") or {}).get("body") or reply_title or f"[{mtype}]"
                    # Inbound media (photo / document / report / voice note / video / sticker):
                    # download from Meta + store in object storage so it's viewable/downloadable
                    # on the CRM chat screen. Caption (if any) becomes the message text.
                    media = _extract_media(m)
                    media_fields = {}
                    chatter_text = text
                    if media:
                        media_fields = await _store_inbound_media(media)
                        cap = media.get("caption")
                        text = cap or ""
                        chatter_text = cap or _KIND_LABEL.get(media["kind"], "[media]")
                    referral = m.get("referral") or None
                    digits = re.sub(r"\D", "", frm or "")[-10:]
                    # mirror into a WhatsApp thread for this number — auto-create one
                    # if it doesn't exist yet so inbound always shows in the 2-way inbox.
                    ch = await db.wa_channels.find_one({"phone_digits": {"$regex": digits + "$"}}) if len(digits) >= 8 else None
                    if not ch and len(digits) >= 8:
                        lead_nm = await db.leads.find_one({"phone_digits": digits}, {"_id": 0, "contact_name": 1, "name": 1, "user_id": 1})
                        ch_id = await _next_channel_id()
                        ch = {"id": ch_id, "phone_digits": digits,
                              "name": (lead_nm or {}).get("contact_name") or (lead_nm or {}).get("name") or frm,
                              "owner_id": (lead_nm or {}).get("user_id"),
                              "whatsapp_number": frm, "last_message_date": now, "created_via": "inbound_webhook"}
                        await db.wa_channels.insert_one(dict(ch))
                    if ch:
                        await db.wa_messages.insert_one({
                            "id": await next_id("wa_message"), "channel_id": ch["id"], "body": text,
                            "author_name": ch.get("name") or frm, "date": now,
                            "message_type": "comment", "direction": "inbound", "status": "received",
                            "wamid": m.get("id"), **media_fields,
                        })
                        chset = {"last_message_date": now}
                        # Click-to-WhatsApp ad referral → record which ad/campaign the lead
                        # came from, so it shows on the chat header.
                        if referral:
                            chset["ctwa_referral"] = {
                                "source_type": referral.get("source_type"), "source_id": referral.get("source_id"),
                                "source_url": referral.get("source_url"), "headline": referral.get("headline"),
                                "body": referral.get("body"), "ctwa_clid": referral.get("ctwa_clid"),
                                "media_type": referral.get("media_type"), "at": now}
                        await db.wa_channels.update_one({"id": ch["id"]}, {"$set": chset, "$inc": {"unread_count": 1}})
                    # log to a matching lead's chatter
                    if len(digits) >= 8:
                        lead = await db.leads.find_one({"phone_digits": digits}, sort=[("write_date", -1)])
                        if lead:
                            await log_message(lead["id"], f"💬 Inbound WhatsApp from {frm}: {chatter_text[:500]}", subtype="comment")
                            if referral:
                                await log_message(lead["id"],
                                    f"📣 Lead arrived via Click-to-WhatsApp ad: <b>{referral.get('headline') or referral.get('source_id') or 'ad'}</b>",
                                    subtype="comment", extra={"kind": "ctwa_referral", "referral": chset.get("ctwa_referral") if ch else None})
                            # Case 5 — mark the most recent outbound template to this lead as Replied
                            last = await db.wa_tracking.find_one(
                                {"lead_id": lead["id"], "status": {"$in": ["sent", "delivered", "read"]}},
                                {"_id": 0, "id": 1, "template_id": 1, "campaign_id": 1}, sort=[("id", -1)])
                            if last:
                                await db.wa_tracking.update_one(
                                    {"id": last["id"]},
                                    {"$set": {"status": "replied", "status_at": now},
                                     "$push": {"status_history": {"status": "replied", "at": now}}})
                                # Attribute which marketing CAMPAIGN this reply belongs to → show on chat header
                                if last.get("campaign_id") and ch:
                                    camp = await db.campaigns.find_one({"id": last["campaign_id"]}, {"_id": 0, "name": 1})
                                    if camp:
                                        await db.wa_channels.update_one({"id": ch["id"]},
                                            {"$set": {"source_campaign": camp.get("name"), "source_campaign_id": last["campaign_id"]}})
                            # Case 5 — Quick Reply → capture + run the button's mapped automation
                            if reply_title or reply_id:
                                tmpl = None
                                if last and last.get("template_id"):
                                    tmpl = await db.templates_whatsapp.find_one({"id": last["template_id"]}, {"_id": 0})
                                btn = None
                                for b in ((tmpl or {}).get("buttons") or []):
                                    if (reply_title and (b.get("text") or "").strip().lower() == reply_title.strip().lower()) \
                                            or (reply_id and str(b.get("id") or b.get("payload") or "") == str(reply_id)):
                                        btn = b
                                        break
                                await log_message(
                                    lead["id"],
                                    f"↩️ WhatsApp Quick Reply: <b>{reply_title or reply_id}</b>"
                                    + (f" (template: {tmpl['name']})" if tmpl else ""),
                                    subtype="comment",
                                    extra={"kind": "wa_quick_reply", "reply": reply_title or reply_id,
                                           "template_name": (tmpl or {}).get("name"),
                                           "track_id": (last or {}).get("id")})
                                aid = (btn or {}).get("automation_id")
                                if aid:
                                    await run_automation_by_id(aid, lead)
                    stored += 1
    except Exception as _e:
        # A webhook must NEVER 5xx: Meta retries the same payload on any non-2xx, turning one
        # bad event into a retry storm (the /api/webhooks/whatsapp 500s). Swallow, record the
        # failure (with traceback) for diagnosis, and fall through to the 200 ack below.
        import traceback as _tb
        try:
            await db.wa_webhook_log.insert_one({
                "id": await next_id("wa_webhook_log"), "create_date": now_utc_str(),
                "kind": "processing_error", "error_type": type(_e).__name__,
                "error": str(_e)[:500], "traceback": _tb.format_exc()[:4000],
                "messages_in": stored, "status_updates": status_updates,
            })
        except Exception:
            pass
    # persist a diagnostic log entry (keeps last ~200)
    try:
        await db.wa_webhook_log.insert_one({
            "id": await next_id("wa_webhook_log"), "create_date": now_utc_str(),
            "statuses": log_statuses, "status_updates": status_updates,
            "matched": log_matched, "messages_in": stored,
            "has_statuses": bool(log_statuses),
        })
    except Exception:
        pass
    return {"status": "ok", "stored": stored, "status_updates": status_updates}


# ---- Admin introspection / test ----
@router.get("/admin/whatsapp/status")
async def wa_status(user: dict = Depends(require_roles("admin", "manager"))):
    c = await wac.get_config()
    return {
        "configured": bool(c.get("access_token") and c.get("phone_number_id")),
        "waba_id": c.get("waba_id"),
        "phone_number_id": c.get("phone_number_id"),
        "has_verify_token": bool(c.get("verify_token")),
        "graph_api_version": c.get("graph_api_version") or wac.GRAPH_VERSION,
    }


@router.post("/admin/whatsapp/subscribe")
async def wa_subscribe(user: dict = Depends(require_roles("admin", "manager"))):
    """Subscribe the app to this WABA's webhooks (enables delivered/read status events)."""
    res = await wac.subscribe_waba()
    if res.get("error"):
        raise HTTPException(status_code=400, detail=res["error"].get("message", str(res["error"])))
    subs = await wac.get_subscribed_apps()
    return {"ok": True, "subscribe_result": res, "subscribed_apps": subs}


@router.get("/admin/whatsapp/subscribed-apps")
async def wa_subscribed_apps(user: dict = Depends(require_roles("admin", "manager"))):
    data = await wac.get_subscribed_apps()
    if data.get("error"):
        raise HTTPException(status_code=400, detail=data["error"].get("message", str(data["error"])))
    return data


@router.get("/admin/whatsapp/webhook-log")
async def wa_webhook_log(user: dict = Depends(require_roles("admin", "manager"))):
    items = await db.wa_webhook_log.find({}, {"_id": 0}).sort("id", -1).limit(30).to_list(30)
    return {"items": items}


@router.get("/admin/whatsapp/diagnose")
async def wa_diagnose(user: dict = Depends(require_roles("admin", "manager"))):
    """One-click end-to-end check for the 'status stuck at Sent' problem.
    Walks the full chain and returns a plain-English verdict + next step."""
    c = await wac.get_config()
    checks = []

    configured = bool(c.get("access_token") and c.get("phone_number_id"))
    checks.append({"key": "configured", "ok": configured,
                   "label": "Access token + Phone Number ID configured",
                   "detail": (c.get("phone_number_id") or "missing phone_number_id")})

    subs = await wac.get_subscribed_apps()
    sub_list = subs.get("data") if isinstance(subs, dict) else None
    waba_subscribed = bool(sub_list)
    checks.append({"key": "waba_subscribed", "ok": waba_subscribed,
                   "label": "WABA is subscribed to an app",
                   "detail": (subs.get("error", {}).get("message") if subs.get("error")
                              else f"{len(sub_list or [])} app(s) subscribed")})

    appsub = await wac.check_app_subscriptions()
    wa_obj = None
    for o in (appsub.get("data") or []):
        if o.get("object") == "whatsapp_business_account":
            wa_obj = o
            break
    callback_url = wa_obj.get("callback_url") if wa_obj else None
    fields = []
    if wa_obj:
        fields = [(f.get("name") if isinstance(f, dict) else f) for f in (wa_obj.get("fields") or [])]
    has_messages_field = "messages" in fields
    points_here = bool(callback_url and "/api/webhooks/whatsapp" in callback_url)
    checks.append({"key": "app_webhook", "ok": bool(wa_obj) and has_messages_field,
                   "label": "App 'whatsapp_business_account' webhook has the 'messages' field",
                   "detail": ({"callback_url": callback_url, "fields": fields, "points_to_this_crm": points_here}
                              if wa_obj else (appsub.get("error", {}).get("message") or "no whatsapp_business_account webhook on this app"))})

    total_hits = await db.wa_webhook_log.count_documents({})
    status_hits = await db.wa_webhook_log.count_documents({"has_statuses": True})
    matched_hits = await db.wa_webhook_log.count_documents({"matched": {"$gt": 0}})
    checks.append({"key": "status_received", "ok": status_hits > 0,
                   "label": "Delivery-status webhooks received from Meta",
                   "detail": f"{status_hits} status webhook(s) received, {matched_hits} matched a tracked message (total {total_hits} webhook hits)"})

    tracked = await db.wa_tracking.count_documents({})
    with_wamid = await db.wa_tracking.count_documents({"wamid": {"$ne": None}})
    checks.append({"key": "tracked_msgs", "ok": with_wamid > 0,
                   "label": "Outbound messages tracked with a Meta message id (wamid)",
                   "detail": f"{with_wamid}/{tracked} tracked messages have a wamid (needed to match status events)"})

    # Verdict — trust observed status webhooks first (they prove the chain works)
    app_check_ran = bool(wa_obj) or not appsub.get("error")
    if matched_hits > 0:
        verdict, next_step = "healthy", "Delivered/read status IS flowing (status webhooks are arriving and matching your messages). If a specific old message stays 'Sent', it was sent before Meta started delivering status — send a fresh one to confirm."
    elif not configured:
        verdict, next_step = "not_connected", "Enter the System User Access Token and Phone Number ID (use 'Fetch phone numbers'), then Save."
    elif wa_obj and callback_url and not points_here:
        verdict, next_step = ("wrong_callback",
                              f"Meta is delivering WhatsApp events to a DIFFERENT URL ({callback_url}), not this CRM. In Meta → App → WhatsApp → Configuration, set the Callback URL to this CRM's /api/webhooks/whatsapp and subscribe the 'messages' field.")
    elif wa_obj and not has_messages_field:
        verdict, next_step = ("messages_field_off",
                              "In Meta → App → WhatsApp → Configuration → Webhook fields, subscribe the 'messages' field (it carries sent/delivered/read/failed). Then click 'Subscribe WABA to webhooks' here.")
    elif not waba_subscribed:
        verdict, next_step = "waba_not_subscribed", "Click 'Subscribe WABA to webhooks' above (token needs whatsapp_business_management permission)."
    elif status_hits == 0:
        verdict, next_step = ("no_status_yet",
                              "Config looks correct — now send a FRESH WhatsApp message to a real number and watch its status. Messages sent before this setup stay 'Sent' forever (Meta only delivers status for messages sent after subscription)." + ("" if app_check_ran else " (Tip: add App ID + App Secret in the form above so this tool can also verify your Meta webhook 'messages' field.)"))
    else:
        verdict, next_step = "received_not_matched", "Status webhooks are arriving but not matching a tracked message by wamid. Send a fresh message from a lead and re-check."

    return {"verdict": verdict, "next_step": next_step, "checks": checks,
            "callback_url": callback_url, "waba_id": c.get("waba_id")}


@router.post("/admin/whatsapp/phone-numbers")
async def wa_phone_numbers(user: dict = Depends(require_roles("admin", "manager"))):
    data = await wac.list_phone_numbers()
    if data.get("error"):
        raise HTTPException(status_code=400, detail=data["error"].get("message", str(data["error"])))
    return data


@router.post("/admin/whatsapp/templates")
async def wa_templates(user: dict = Depends(require_roles("admin", "manager"))):
    data = await wac.list_templates()
    if data.get("error"):
        raise HTTPException(status_code=400, detail=data["error"].get("message", str(data["error"])))
    return data


@router.post("/admin/whatsapp/templates/sync")
async def wa_templates_sync(user: dict = Depends(require_roles("admin", "manager"))):
    """Import every APPROVED Meta template into the CRM's usable WhatsApp templates
    (templates_whatsapp) so they can be picked for retargeting campaigns / lead sends.
    Create-or-update keyed by (wa_template_name, lang); pulls the BODY text + category so
    imported templates are immediately usable & previewable. Idempotent — re-running only
    adds newly approved ones and refreshes the linked Meta fields."""
    data = await wac.list_templates()
    if data.get("error"):
        raise HTTPException(status_code=400, detail=data["error"].get("message", str(data["error"])))
    approved = [t for t in (data.get("data") or []) if (t.get("status") or "").upper() == "APPROVED"]
    coll = db.templates_whatsapp

    def _body_text(t):
        for comp in (t.get("components") or []):
            if (comp.get("type") or "").upper() == "BODY":
                return comp.get("text") or ""
        return ""

    def _seed_vars(n):
        out = []
        for i in range(1, int(n or 0) + 1):
            out.append({"name": "Body - {{" + str(i) + "}}",
                        "type": "User Name" if i == 1 else "Free Text",
                        "field": "contact_name" if i == 1 else "",
                        "sample": ""})
        return out

    _HDR_LABEL = {"IMAGE": "Image", "VIDEO": "Video", "DOCUMENT": "Document", "TEXT": "Text"}

    created = updated = 0
    first_lang: dict = {}
    for t in approved:
        nm = (t.get("name") or "").strip()
        if not nm:
            continue
        lang = (t.get("language") or "en").strip()
        cat = t.get("category")
        body = _body_text(t)
        meta = wac.extract_template_meta(t.get("components"))
        if nm not in first_lang:
            first_lang[nm] = lang
            display = nm
        else:
            display = nm if first_lang[nm] == lang else f"{nm} ({lang})"
        existing = await coll.find_one({"wa_template_name": nm, "lang": lang}, {"_id": 0, "id": 1, "variables": 1})
        if existing:
            set_fields = {"wa_template_name": nm, "lang": lang, "category": cat, "status": "approved",
                          "channel": "whatsapp", "template_type": "whatsapp", **meta}
            if not existing.get("variables") and meta["wa_body_var_count"]:
                set_fields["variables"] = _seed_vars(meta["wa_body_var_count"])
            await coll.update_one({"id": existing["id"]}, {"$set": set_fields})
            updated += 1
        else:
            tid = await next_id("template_whatsapp")
            await coll.insert_one({
                "id": tid, "name": display, "wa_template_name": nm, "lang": lang,
                "category": cat, "status": "approved", "channel": "whatsapp",
                "template_type": "whatsapp", "body": body, "active": True,
                "source": "meta_sync", "created_at": now_utc_str(),
                "header_type": _HDR_LABEL.get(meta["wa_header_format"], "None"),
                "variables": _seed_vars(meta["wa_body_var_count"]), **meta})
            created += 1
    return {"ok": True, "created": created, "updated": updated, "total_approved": len(approved)}


class WaTestBody(BaseModel):
    to: str
    body: str = "Hello from Fundle.ai CRM 👋"


class WaPreviewBody(BaseModel):
    lead_id: Optional[int] = None


def _var_mapped(v: dict) -> bool:
    ty = (v.get("type") or "").strip().lower()
    return bool(v.get("field")) or ty in ("user name", "user phone") or bool(str(v.get("sample") or "").strip())


@router.post("/admin/whatsapp/templates/{tid}/preview")
async def wa_template_preview(tid: int, body: WaPreviewBody = WaPreviewBody(),
                              user: dict = Depends(require_roles("admin", "manager"))):
    """Build (WITHOUT sending) the exact parameter set that would go to Meta for a template —
    header media + resolved body variables + readiness — so campaigns can be validated safely
    before a real send. Uses a real lead if lead_id given, else a sample lead."""
    t = await db.templates_whatsapp.find_one({"id": tid}, {"_id": 0})
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    await wac.ensure_template_meta(t)
    lead = None
    if body and body.lead_id:
        lead = await db.leads.find_one({"id": body.lead_id}, {"_id": 0})
    if not lead:
        lead = {"id": 0, "contact_name": "Riya Sharma", "name": "Riya Sharma",
                "phone": "919999999999", "mobile": "919999999999", "phone_digits": "9999999999"}
    count = t.get("wa_body_var_count")
    if count is None:
        count = wac._maxvar(t.get("body"))
    count = int(count or 0)
    body_values = wac.build_body_values(lead, t, count) if count else []
    vars_ = t.get("variables") or []
    unmapped = sum(1 for i in range(count) if not (i < len(vars_) and _var_mapped(vars_[i])))
    fmt = (t.get("wa_header_format") or "").upper()
    is_media = fmt in ("IMAGE", "VIDEO", "DOCUMENT")
    return {
        "template": t.get("wa_template_name"),
        "language": t.get("lang") or "en",
        "linked": bool(t.get("wa_template_name")),
        "header_format": fmt or None,
        "header_media_url": (t.get("header_media_url") or t.get("wa_header_sample_url")) if is_media else None,
        "body_var_count": count,
        "body_values": body_values,
        "url_button": t.get("wa_url_button"),
        "unmapped_vars": unmapped,
        "ready": unmapped == 0 and bool(t.get("wa_template_name")),
    }


@router.post("/admin/whatsapp/send-test")
async def wa_send_test(b: WaTestBody, user: dict = Depends(require_roles("admin", "manager"))):
    res = await wac.send_text(b.to, b.body)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "send failed"))
    return res
