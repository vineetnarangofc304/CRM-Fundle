"""Email template library + click-to-send per contact + brochure uploads."""
import re
import uuid
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from core import email_templates as et, outreach as oc
from core.db import db
from core.security import require_roles
from core.storage import APP_NAME, get_object, put_object
from core.utils import next_id, now_utc_str

router = APIRouter(tags=["email-templates"])
STAFF = require_roles("admin", "manager")
FILE_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
              "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
              "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


class Attachment(BaseModel):
    file_id: int
    filename: str
    size: int = 0


class Tpl(BaseModel):
    name: str
    subject: str
    body: str
    mode: str = "manual"
    active: bool = True
    attachments: List[Attachment] = []
    video_links: List[str] = []


def _clean(body: Tpl) -> dict:
    if body.mode not in ("auto", "manual"):
        raise HTTPException(status_code=400, detail="Mode must be auto or manual")
    if not body.name.strip() or not body.subject.strip() or not body.body.strip():
        raise HTTPException(status_code=400, detail="Name, subject and body are required")
    links = [v.strip() for v in body.video_links if v.strip()]
    if any(not re.match(r"https?://", v) for v in links):
        raise HTTPException(status_code=400, detail="Video links must start with http:// or https://")
    if sum(a.size for a in body.attachments) > et.MAX_ATTACH_BYTES:
        raise HTTPException(status_code=400, detail="Attachments must total 3 MB or less — share bigger files as links")
    return {**body.model_dump(), "name": body.name.strip()[:80], "subject": body.subject.strip()[:200],
            "body": oc.clean_html(body.body)[:8000], "video_links": links[:5], "updated_at": now_utc_str()}


@router.get("/outreach/templates")
async def list_templates(user: dict = Depends(STAFF)):
    await et.seed_starters()
    return await db.email_templates.find({"is_deleted": {"$ne": True}}, {"_id": 0}).sort("id", 1).to_list(200)


@router.post("/outreach/templates")
async def create_template(body: Tpl, user: dict = Depends(STAFF)):
    doc = {**_clean(body), "id": await next_id("email_template"), "kind": "custom", "is_deleted": False,
           "created_at": now_utc_str(), "created_by": user["name"]}
    await db.email_templates.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


@router.put("/outreach/templates/{tid}")
async def update_template(tid: int, body: Tpl, user: dict = Depends(STAFF)):
    r = await db.email_templates.update_one({"id": tid, "is_deleted": {"$ne": True}},
                                            {"$set": {**_clean(body), "updated_by": user["name"]}})
    if not r.matched_count:
        raise HTTPException(status_code=404, detail="Template not found")
    return await db.email_templates.find_one({"id": tid}, {"_id": 0})


@router.delete("/outreach/templates/{tid}")
async def delete_template(tid: int, user: dict = Depends(STAFF)):
    await db.email_templates.update_one({"id": tid}, {"$set": {"is_deleted": True, "deleted_by": user["name"]}})
    return {"ok": True}


class Suggest(BaseModel):
    name: str
    brief: str = ""
    subject: str = ""
    body: str = ""


@router.post("/outreach/templates/ai-suggest")
async def ai_suggest(body: Suggest, user: dict = Depends(STAFF)):
    st = await oc.get_settings()
    prompt = (f"Seller context: {st['pitch']}\nFundle products: Fundle.ai (AI retail intelligence + loyalty), "
              "MarketplaceMetrics.ai (marketplace payout/commission/returns reconciliation and SKU profit for Amazon, "
              "Flipkart, Myntra, AJIO, JioMart sellers), MallMetrics.ai (automated daily sales reporting, tenant sales "
              "reconciliation and AI sales analysis for malls, MBO and SIS).\n"
              f"Template name: {body.name}\nWhat it should say: {body.brief or 'use the template name'}\n"
              f"Current draft (improve it if present): {body.subject}\n{body.body}\n"
              "Write a reusable B2B cold email template, under 140 words, one clear ask for a 20-minute call. "
              "Use placeholders {{first_name}} and {{brand}}. Body as simple HTML <p> paragraphs, no signature.\n"
              'JSON: {"subject": str, "body": str}')
    try:
        d = await et.ai_text("You write short, specific, human B2B email templates. No hype, no emojis, "
                             "no invented stats. Reply with JSON only.", prompt)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"AI is unavailable right now: {str(e)[:120]}")
    if not d.get("subject") or not d.get("body"):
        raise HTTPException(status_code=502, detail="AI did not return a draft — try again")
    return {"subject": d["subject"][:200], "body": oc.clean_html(d["body"])[:8000]}


@router.post("/outreach/files")
async def upload_file(file: UploadFile = File(...), user: dict = Depends(STAFF)):
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in FILE_TYPES:
        raise HTTPException(status_code=400, detail="Allowed: PDF, PNG, JPG, PPTX, DOCX")
    data = await file.read()
    if len(data) > et.MAX_ATTACH_BYTES:
        raise HTTPException(status_code=400, detail="File must be 3 MB or less — share bigger files or videos as a link")
    res = await put_object(f"{APP_NAME}/outreach/{uuid.uuid4()}.{ext}", data, FILE_TYPES[ext])
    doc = {"id": await next_id("outreach_file"), "storage_path": res["path"], "filename": file.filename[:120],
           "content_type": FILE_TYPES[ext], "size": len(data), "is_deleted": False,
           "created_at": now_utc_str(), "created_by": user["name"]}
    await db.outreach_files.insert_one(dict(doc))
    return {"file_id": doc["id"], "filename": doc["filename"], "size": doc["size"]}


@router.get("/outreach/files/{fid}")
async def download_file(fid: int, user: dict = Depends(STAFF)):
    f = await db.outreach_files.find_one({"id": fid, "is_deleted": {"$ne": True}})
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    data, _ = await get_object(f["storage_path"])
    return Response(content=data, media_type=f["content_type"],
                    headers={"Content-Disposition": f'attachment; filename="{f["filename"]}"'})


class UseTpl(BaseModel):
    template_id: int
    contact_index: int


@router.post("/prospecting/prospects/{pid}/use-template")
async def use_template(pid: int, body: UseTpl, user: dict = Depends(STAFF)):
    p = await db.prospects.find_one({"id": pid}, {"_id": 0})
    t = await db.email_templates.find_one({"id": body.template_id, "is_deleted": {"$ne": True}}, {"_id": 0})
    if not p or not t:
        raise HTTPException(status_code=404, detail="Prospect or template not found")
    cs = p.get("contacts") or []
    if not 0 <= body.contact_index < len(cs) or not cs[body.contact_index].get("email"):
        raise HTTPException(status_code=400, detail="This contact has no email yet")
    doc = await et.create_message(t, p, cs[body.contact_index], "pending_approval", user["name"])
    return {"id": doc["id"], "subject": doc["subject"]}


@router.get("/prospecting/prospects/{pid}/emails")
async def prospect_emails(pid: int, user: dict = Depends(STAFF)):
    return await db.outreach.find({"prospect_id": pid, "channel": "email"},
                                  {"_id": 0, "id": 1, "to": 1, "contact_name": 1, "subject": 1, "status": 1, "sent_at": 1,
                                   "template_name": 1, "step": 1, "created_at": 1}).sort("id", -1).to_list(200)


@router.post("/outreach/messages/{mid}/send-now")
async def send_now(mid: int, user: dict = Depends(STAFF)):
    from core import mailer
    if not await mailer.is_connected():
        raise HTTPException(status_code=400, detail="No email account connected — connect Outlook in Admin → Email")
    m = await db.outreach.find_one_and_update(
        {"id": mid, "channel": "email", "status": {"$in": ["pending_approval", "approved", "failed"]}},
        {"$set": {"status": "sending", "approved_by": user["name"], "approved_at": oc._now()}}, projection={"_id": 0})
    if not m:
        raise HTTPException(status_code=409, detail="Message already sent, sending or stopped")
    ok = await oc._send_one(m, await oc.get_settings())
    if not ok:
        err = (await db.outreach.find_one({"id": mid}, {"error": 1}) or {}).get("error")
        raise HTTPException(status_code=502, detail=f"Send failed: {err or 'unknown error'}")
    return {"ok": True}


class FinalizeIds(BaseModel):
    ids: List[int]


@router.post("/outreach/messages/send-selected")
async def send_selected(body: FinalizeIds, user: dict = Depends(STAFF)):
    """Finalise reviewed template emails: queue them to go out on the next run (within 15 min)."""
    now = oc._now()
    r = await db.outreach.update_many({"id": {"$in": body.ids[:200]}, "channel": "email", "step": 1,
                                       "status": {"$in": ["pending_approval", "failed"]}},
                                      {"$set": {"status": "approved", "approved_by": user["name"], "approved_at": now,
                                                "scheduled_at": now}})
    return {"queued": r.modified_count}
