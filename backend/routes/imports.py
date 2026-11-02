"""Bulk lead upload from Excel (.xlsx) / CSV with column mapping, dedupe and assignment options."""
import asyncio
import csv
import html
import io
import json
import logging
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from core.db import db
from core.security import require_roles
from core.utils import (IST_OFFSET, ensure_catalog, ist_date_parts, next_id, now_utc_str, run_automations,
                        search_norm, to_ist_str)

router = APIRouter(prefix="/import", tags=["import"])
logger = logging.getLogger(__name__)

MAX_ROWS = 50000
_TASKS: set = set()
MAX_BYTES = 15 * 1024 * 1024

# (key, header, aliases) — aliases are lowercase/underscored header variants auto-matched on upload.
FIELDS = [
    ("contact_name", "Name", ["name", "full_name", "contact", "contact_name", "lead_name", "customer_name"]),
    ("phone", "Phone", ["phone", "mobile", "phone_number", "mobile_number", "contact_number", "whatsapp", "number"]),
    ("alternate_number", "Alternate Number", ["alternate_number", "alt_phone", "alternate_phone", "phone_2"]),
    ("email_from", "Email", ["email", "email_id", "email_address", "mail"]),
    ("company_name", "Company", ["company", "company_name", "organization", "organisation", "account"]),
    ("designation", "Designation", ["designation", "job_title", "title", "role", "position"]),
    ("team_size", "Team Size", ["team_size", "company_size", "employees", "no_of_employees"]),
    ("deal_value", "Deal Value (INR)", ["deal_value", "deal_value_inr", "deal_amount", "amount", "value", "expected_revenue"]),
    ("street", "Address", ["address", "street"]),
    ("city", "City", ["city", "town"]),
    ("state_name", "State", ["state", "state_name", "region"]),
    ("country", "Country", ["country"]),
    ("query", "Query", ["query", "message", "requirement", "enquiry", "inquiry", "question"]),
    ("remark", "Remark", ["remark", "remarks", "notes", "note", "comment", "comments"]),
    ("source_lead", "Source", ["source", "lead_source", "source_lead"]),
    ("campaign_name", "Campaign", ["campaign", "campaign_name"]),
    ("ads_platform", "Ads Platform", ["ads_platform", "platform"]),
    ("utm_source", "UTM Source", ["utm_source"]),
    ("utm_medium", "UTM Medium", ["utm_medium"]),
    ("utm_campaign", "UTM Campaign", ["utm_campaign"]),
    ("_tags", "Tags", ["tags", "tag", "disposition", "status", "lead_status"]),
    ("_agent", "Assigned Agent", ["assigned_agent", "agent", "caller", "assigned_to", "owner", "salesperson"]),
    ("follow_up_date", "Follow-up Date", ["follow_up_date", "followup_date", "next_follow_up", "follow_up"]),
    ("follow_up_time", "Follow-up Time", ["follow_up_time", "followup_time"]),
    ("_created", "Created Date", ["created_date", "created", "create_date", "lead_date", "date", "created_ist"]),
    ("_won", "Won Date", ["won_date", "closed_date", "close_date", "won_on", "deal_closed_date"]),
]
SIMPLE_KEYS = {k for k, _, _ in FIELDS if not k.startswith("_")}


def _norm(h) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(h or "").strip().lower()).strip("_")


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _parse_file(raw: bytes, filename: str):
    name = (filename or "").lower()
    if name.endswith(".csv"):
        text = raw.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
    elif name.endswith(".xlsx"):
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception:
            raise HTTPException(status_code=400, detail="Could not read the Excel file. Save it as .xlsx and try again.")
        rows = [[_cell(c) for c in r] for r in wb.active.iter_rows(values_only=True)]
    else:
        raise HTTPException(status_code=400, detail="Upload an Excel (.xlsx) or CSV file")
    rows = [[_cell(c) for c in r] for r in rows]
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        raise HTTPException(status_code=400, detail="The file is empty")
    headers = [h or f"Column {i + 1}" for i, h in enumerate(rows[0])]
    data = rows[1:]
    if len(data) > MAX_ROWS:
        raise HTTPException(status_code=400, detail=f"Max {MAX_ROWS:,} rows per upload — split the file")
    return headers, data


async def _field_options():
    custom = await db.custom_fields.find({"active": {"$ne": False}}, {"_id": 0, "key": 1, "label": 1}).to_list(300)
    opts = [{"key": k, "label": lbl, "group": "Standard"} for k, lbl, _ in FIELDS]
    opts += [{"key": c["key"], "label": c.get("label") or c["key"], "group": "Custom"} for c in custom]
    return opts, custom


def _suggest(headers, custom):
    alias = {}
    for k, lbl, al in FIELDS:
        for a in [_norm(lbl), *al]:
            alias.setdefault(a, k)
    for c in custom:
        alias.setdefault(_norm(c.get("label")), c["key"])
        alias.setdefault(_norm(c["key"]), c["key"])
    out, used = {}, set()
    for h in headers:
        k = alias.get(_norm(h))
        if k and k not in used:
            out[h] = k
            used.add(k)
    return out


@router.get("/template.xlsx")
async def download_template(user: dict = Depends(require_roles("admin", "manager"))):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    opts, _ = await _field_options()
    sample = {"contact_name": "Priya Sharma", "phone": "9876543210", "email_from": "priya@acme.in",
              "company_name": "Acme Technologies", "designation": "CTO", "team_size": "51-200",
              "deal_value": "250000", "city": "Bengaluru", "state_name": "Karnataka", "country": "India",
              "query": "Wants a demo of the AI agent", "source_lead": "Website", "_tags": "Ringing",
              "follow_up_date": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"), "follow_up_time": "11:00"}
    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    for ci, o in enumerate(opts, 1):
        c = ws.cell(row=1, column=ci, value=o["label"])
        c.fill = PatternFill("solid", fgColor="1A73E8")
        c.font = Font(bold=True, color="FFFFFF")
        ws.cell(row=2, column=ci, value=sample.get(o["key"], ""))
        ws.column_dimensions[c.column_letter].width = max(14, len(o["label"]) + 4)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": 'attachment; filename="fundle_leads_import_template.xlsx"'})


@router.post("/preview")
async def preview(file: UploadFile = File(...), user: dict = Depends(require_roles("admin", "manager"))):
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 15 MB)")
    headers, data = _parse_file(raw, file.filename)
    opts, custom = await _field_options()
    return {"headers": headers, "rows": data[:5], "total_rows": len(data),
            "fields": opts, "mapping": _suggest(headers, custom)}


def _to_date(v: str):
    v = (v or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d-%m-%Y %H:%M", "%d-%m-%Y", "%d/%m/%Y %H:%M",
                "%d/%m/%Y", "%d.%m.%Y", "%d %b %Y", "%d-%b-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def _to_amount(v: str):
    v = re.sub(r"[\s,₹]", "", v or "")
    if not v:
        return None
    amt = float(v)
    if amt != amt or amt < 0 or amt in (float("inf"),):
        raise ValueError
    return amt


class _Ctx:
    """Lookup tables loaded once per import job."""

    async def load(self):
        tags = await db.catalogs.find({"type": "tag"}, {"_id": 0, "id": 1, "name": 1}).to_list(2000)
        self.tags = {t["name"].strip().lower(): t["id"] for t in tags}
        dm = ((await db.settings.find_one({"key": "disposition_map"}, {"_id": 0})) or {}).get("map") or {}
        self.tag_stage = {t.strip().lower(): st for st, names in dm.items() for t in names}
        users = await db.users.find({"active": True}, {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1}).to_list(1000)
        self.users = {}
        for u in users:
            self.users[(u.get("name") or "").strip().lower()] = u["id"]
            self.users[(u.get("email") or "").strip().lower()] = u["id"]
        self.callers = [u["id"] for u in users if u.get("role") == "caller"]
        self.rr = 0
        self.sources = set()
        return self


def _build_row(row, headers, mapping, custom_keys, ctx, opts):
    """Return (data, custom, tag_ids, stage, error)."""
    data, custom, tag_ids, stage = {}, {}, [], None
    for i, h in enumerate(headers):
        key = mapping.get(h)
        val = row[i].strip() if i < len(row) and row[i] else ""
        if not key or not val:
            continue
        if key == "deal_value":
            try:
                data[key] = _to_amount(val)
            except ValueError:
                return None, None, None, None, f"Deal value '{val}' is not a valid amount"
        elif key == "phone" or key == "alternate_number":
            data[key] = re.sub(r"[^\d+]", "", val)
        elif key == "follow_up_date":
            d = _to_date(val)
            if not d:
                return None, None, None, None, f"Follow-up date '{val}' not understood (use YYYY-MM-DD)"
            data[key] = d.strftime("%Y-%m-%d")
        elif key == "_created":
            d = _to_date(val)
            if not d:
                return None, None, None, None, f"Created date '{val}' not understood (use YYYY-MM-DD)"
            data["_created"] = d  # treated as IST
        elif key == "_won":
            d = _to_date(val)
            if not d:
                return None, None, None, None, f"Won date '{val}' not understood (use YYYY-MM-DD)"
            data["_won"] = d
        elif key == "_tags":
            for t in [x.strip() for x in re.split(r"[,;|]", val) if x.strip()]:
                tid = ctx.tags.get(t.lower())
                if tid is None:
                    data.setdefault("_new_tags", []).append(t)
                    continue
                tag_ids.append(tid)
                stage = ctx.tag_stage.get(t.lower(), stage)
        elif key == "_agent":
            uid = ctx.users.get(val.lower())
            if uid is None:
                return None, None, None, None, f"No active user named '{val}'"
            data["user_id"] = uid
        elif key in custom_keys:
            custom[key] = val
        elif key in SIMPLE_KEYS:
            data[key] = val
    pd = re.sub(r"\D", "", data.get("phone") or "")[-10:]
    if len(pd) < 8 and not data.get("email_from"):
        return None, None, None, None, "Missing a valid phone number or email"
    return data, custom, tag_ids, stage, None


def _apply_new_lead_defaults(data, ctx, opts):
    """Assignment + default source apply only to NEW leads (never overwrite an existing lead)."""
    if opts.get("assign_mode") == "user" and opts.get("user_id"):
        data["user_id"] = int(opts["user_id"])
    elif opts.get("assign_mode") == "round_robin" and ctx.callers and "user_id" not in data:
        data["user_id"] = ctx.callers[ctx.rr % len(ctx.callers)]
        ctx.rr += 1
    elif opts.get("assign_mode") == "none":
        data.pop("user_id", None)
    if not data.get("source_lead") and opts.get("default_source"):
        data["source_lead"] = opts["default_source"]


async def _resolve_new_tags(data, tag_ids, ctx, errors, n):
    for t in data.pop("_new_tags", []):
        if t.lower() not in ctx.tags:
            ctx.tags[t.lower()] = (await ensure_catalog("tag", t))["id"]
            errors.append({"row": n, "error": f"Note: new tag '{t}' created — map it to a stage in Admin → Dropdowns"})
        tag_ids.append(ctx.tags[t.lower()])


async def _process(job_id: int, headers, rows, mapping, opts, user):
    try:
        await _run(job_id, headers, rows, mapping, opts, user)
    except Exception as e:
        logger.exception("import job failed")
        await db.import_jobs.update_one({"id": job_id}, {"$set": {"status": "done", "fatal": str(e)[:300]}})
    finally:
        _clear_caches()


def _clear_caches():
    from routes import leads as leads_routes, reports as reports_routes
    leads_routes._count_cache.clear()
    reports_routes._dash_cache.clear()
    reports_routes._kpi_cache.clear()


CHUNK = 1000


async def _reserve_ids(name: str, n: int) -> list:
    doc = await db.counters.find_one_and_update({"_id": name}, {"$inc": {"seq": n}}, upsert=True, return_document=True)
    return list(range(doc["seq"] - n + 1, doc["seq"] + 1))


def _ist_to_utc(d: datetime) -> str:
    return (d - IST_OFFSET).strftime("%Y-%m-%d %H:%M:%S")


def _msg(mid, lead_id, body, user, subtype="tracking", now=None):
    return {"id": mid, "lead_id": lead_id, "body": body, "author_id": user["id"], "author_name": user["name"],
            "date": now, "message_type": "comment" if subtype == "comment" else "notification", "subtype": subtype}


def _new_doc(lid, data, custom, tag_ids, stage, pd, dup_of, created_ist, won_ist, user, now):
    create = _ist_to_utc(created_ist) if created_ist else now
    data.setdefault("country", "India")
    data["name"] = data.get("contact_name") or data.get("company_name") or data.get("phone") or "New Lead"
    doc = {
        "id": lid, "active": True, "stage_id": 1, "type": "lead", "priority": "0",
        "tags": tag_ids, "create_date": create, "create_date_ist": to_ist_str(create),
        "write_date": now, "create_uid": user["id"], "custom": custom, "phone_digits": pd,
        "is_duplicate": bool(dup_of), "duplicate_of": dup_of, "original_user_id": data.get("user_id"),
        "bulk_import": True, **data,
    }
    if stage:
        doc["lead_stage"] = stage
        if stage == "Converted":
            won = _ist_to_utc(won_ist) if won_ist else create
            doc["won_date"], doc["won_date_ist"] = won, to_ist_str(won)
    doc.update(ist_date_parts(doc["create_date_ist"]))
    doc.update(search_norm(doc))
    return doc


def _update_op(data, custom, tag_ids, stage, won_ist, now):
    upd = dict(data)
    if stage:
        upd["lead_stage"] = stage
    upd["write_date"] = now
    if "contact_name" in upd:
        upd["name"] = upd["contact_name"]
    upd.update({k: v for k, v in search_norm(upd).items() if v})
    for k, v in custom.items():
        upd[f"custom.{k}"] = v
    op = {"$set": upd}
    if tag_ids:
        op["$addToSet"] = {"tags": {"$each": tag_ids}}
    return op


async def _run(job_id: int, headers, rows, mapping, opts, user):
    """Chunked import: one $in duplicate lookup, one insert_many and one bulk_write per 1,000 rows."""
    from pymongo import UpdateOne
    ctx = await _Ctx().load()
    custom_keys = {c["key"] for c in await db.custom_fields.find({}, {"_id": 0, "key": 1}).to_list(300)}
    stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
    errors = []
    dup_mode = opts.get("duplicate_mode", "skip")
    who = html.escape(user["name"])
    job_seen: dict = {}  # phone_digits -> first lead id created earlier in this file
    for c0 in range(0, len(rows), CHUNK):
        chunk = rows[c0:c0 + CHUNK]
        now = now_utc_str()
        items = []
        for i, row in enumerate(chunk):
            n = c0 + i + 2
            data, custom, tag_ids, stage, err = _build_row(row, headers, mapping, custom_keys, ctx, opts)
            if err:
                stats["failed"] += 1
                errors.append({"row": n, "error": err})
                continue
            pd = re.sub(r"\D", "", data.get("phone") or "")[-10:]
            items.append((n, data, custom, tag_ids, stage, pd if len(pd) >= 8 else ""))
        pds = list({it[5] for it in items if it[5] and it[5] not in job_seen})
        existing = {}
        if pds:
            async for d in db.leads.find({"phone_digits": {"$in": pds}, "active": True},
                                         {"_id": 0, "id": 1, "phone_digits": 1}).sort("id", 1):
                existing.setdefault(d["phone_digits"], d["id"])
        existing.update(job_seen)
        ids = await _reserve_ids("lead", len(items)) if items else []
        new_docs, pending, ops, msgs = [], {}, [], []
        for idx, (n, data, custom, tag_ids, stage, pd) in enumerate(items):
            try:
                dup = existing.get(pd) if pd else None
                in_chunk = pending.get(pd) if pd else None
                if (dup or in_chunk) and dup_mode == "skip":
                    stats["skipped"] += 1
                    errors.append({"row": n, "error": f"Skipped — phone already exists on lead #{dup or in_chunk['id']}"})
                    continue
                await _resolve_new_tags(data, tag_ids, ctx, errors, n)
                created_ist, won_ist = data.pop("_created", None), data.pop("_won", None)
                is_update = bool(dup or in_chunk) and dup_mode == "update"
                if not is_update:
                    _apply_new_lead_defaults(data, ctx, opts)
                if data.get("source_lead") and data["source_lead"] not in ctx.sources:
                    await ensure_catalog("source_lead", data["source_lead"])
                    ctx.sources.add(data["source_lead"])
                if is_update and in_chunk:
                    in_chunk.update({k: v for k, v in data.items()})
                    in_chunk["custom"].update(custom)
                    in_chunk["tags"] = list(dict.fromkeys(in_chunk["tags"] + tag_ids))
                    if data.get("contact_name"):
                        in_chunk["name"] = data["contact_name"]
                    in_chunk.update(search_norm(in_chunk))
                    if stage:
                        in_chunk["lead_stage"] = stage
                        if stage == "Converted" and not in_chunk.get("won_date"):
                            won = _ist_to_utc(won_ist) if won_ist else in_chunk["create_date"]
                            in_chunk["won_date"], in_chunk["won_date_ist"] = won, to_ist_str(won)
                    msgs.append((in_chunk["id"], f"Lead updated from bulk upload by {who}", "tracking"))
                    stats["updated"] += 1
                    continue
                if is_update:
                    op = _update_op(data, custom, tag_ids, stage, won_ist, now)
                    ops.append(UpdateOne({"id": dup}, op))
                    if stage == "Converted":
                        won = _ist_to_utc(won_ist) if won_ist else now
                        ops.append(UpdateOne({"id": dup, "won_date": {"$exists": False}},
                                             {"$set": {"won_date": won, "won_date_ist": to_ist_str(won)}}))
                    msgs.append((dup, f"Lead updated from bulk upload by {who}", "tracking"))
                    stats["updated"] += 1
                    continue
                dup_of = dup or (in_chunk["id"] if in_chunk else None)
                doc = _new_doc(ids[idx], data, custom, tag_ids, stage, pd, dup_of, created_ist, won_ist, user, now)
                new_docs.append(doc)
                if pd and not dup_of:
                    pending[pd] = doc
                msgs.append((doc["id"], f"Lead created from bulk upload by {who}", "tracking"))
                if dup_of:
                    msgs.append((doc["id"], f"⚠️ Possible duplicate — same phone as lead #{dup_of}", "comment"))
                stats["created"] += 1
            except Exception as e:
                logger.exception("import row failed")
                stats["failed"] += 1
                errors.append({"row": n, "error": f"Unexpected error: {e}"[:200]})
        if new_docs:
            await db.leads.insert_many(new_docs, ordered=False)
            job_seen.update({pd: d["id"] for pd, d in pending.items()})
        if ops:
            await db.leads.bulk_write(ops, ordered=True)
        if msgs:
            mids = await _reserve_ids("message", len(msgs))
            await db.messages.insert_many([_msg(mid, lid, body, user, st, now) for mid, (lid, body, st) in zip(mids, msgs)],
                                          ordered=False)
        if opts.get("run_automations"):
            for d in new_docs:
                await run_automations("on_create", {k: v for k, v in d.items() if k != "_id"})
        await db.import_jobs.update_one({"id": job_id}, {"$set": {**stats, "processed": min(c0 + CHUNK, len(rows))}})
    await db.import_jobs.update_one({"id": job_id}, {"$set": {
        **stats, "processed": len(rows), "status": "done", "errors": errors[:1000],
        "error_count": len(errors), "finished_at": now_utc_str()}})


@router.post("/leads")
async def start_import(file: UploadFile = File(...), mapping: str = Form(...), options: str = Form("{}"),
                       user: dict = Depends(require_roles("admin", "manager"))):
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 15 MB)")
    headers, rows = _parse_file(raw, file.filename)
    try:
        mapping, opts = json.loads(mapping), json.loads(options)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid mapping")
    mapping = {h: k for h, k in mapping.items() if k and h in headers}
    keys = list(mapping.values())
    if "phone" not in keys and "email_from" not in keys:
        raise HTTPException(status_code=400, detail="Map at least a Phone or Email column")
    if len(keys) != len(set(keys)):
        raise HTTPException(status_code=400, detail="Each CRM field can be mapped to only one column")
    if opts.get("duplicate_mode") not in ("skip", "update", "create"):
        opts["duplicate_mode"] = "skip"
    if opts.get("assign_mode") == "user" and not opts.get("user_id"):
        raise HTTPException(status_code=400, detail="Choose the user to assign the leads to")
    jid = await next_id("import_job")
    await db.import_jobs.insert_one({
        "id": jid, "filename": file.filename, "total": len(rows), "processed": 0, "status": "running",
        "created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": [], "error_count": 0,
        "options": opts, "user_id": user["id"], "user_name": user["name"], "started_at": now_utc_str()})
    task = asyncio.create_task(_process(jid, headers, rows, mapping, opts, user))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"job_id": jid, "total": len(rows)}


@router.get("/jobs")
async def list_jobs(user: dict = Depends(require_roles("admin", "manager"))):
    return await db.import_jobs.find({}, {"_id": 0, "errors": 0}).sort("id", -1).limit(20).to_list(20)


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, user: dict = Depends(require_roles("admin", "manager"))):
    job = await db.import_jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Import not found")
    return job
