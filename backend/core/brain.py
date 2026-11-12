"""Fundle Brain: an operator agent (GPT 6 Luna + tool calling) that can read and change the CRM's data, settings,
templates, inboxes, prospecting and outreach. Every write requires a reason and is audit-logged."""
import json
import logging
import os
import re
from datetime import datetime, timezone

from core.db import db
from core.utils import next_id, now_utc_str

logger = logging.getLogger(__name__)
MODEL = ("openai", "gpt-6-luna")
DEV_EMAIL = "vineet.narang@fundle.ai"
READ_COLS = {"prospects", "leads", "outreach", "email_templates", "mailboxes", "settings", "messages", "users",
             "prospect_runs", "brain_audit", "linkedin_tasks", "stages", "sources"}
WRITE_COLS = {"prospects", "leads", "outreach", "email_templates", "mailboxes", "settings"}
SECRET = {"smtp_password", "password", "password_hash", "hashed_password", "access_token", "refresh_token", "token",
          "client_secret", "api_key", "tokens", "secret"}
BAD_OPS = {"$where", "$function", "$accumulator", "$out", "$merge"}
MAX_WRITE = 5000


def _secret(k: str) -> bool:
    k = k.lower()
    return k in SECRET or k.endswith("_enc") or any(w in k for w in ("password", "secret", "token"))


def _scrub(o):
    from bson import ObjectId
    if isinstance(o, dict):
        return {k: ("***" if _secret(k) else _scrub(v)) for k, v in o.items() if not (k == "_id" and isinstance(v, ObjectId))}
    if isinstance(o, list):
        return [_scrub(x) for x in o]
    return o if isinstance(o, (str, int, float, bool, type(None))) else str(o)


def _check(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in BAD_OPS:
                raise ValueError(f"Operator {k} is not allowed")
            _check(v)
    elif isinstance(o, list):
        for x in o:
            _check(x)


def _fields_ok(d: dict):
    for k in d:
        if any(_secret(x) for x in k.split(".")) or k in ("_id", "id"):
            raise ValueError(f"Field {k} can't be changed by the Brain")


async def _audit(ctx, tool, args, result, ok=True):
    await db.brain_audit.insert_one({"id": await next_id("brain_audit"), "at": now_utc_str(), "user_id": ctx["user"]["id"],
                                     "user_name": ctx["user"].get("name"), "session_id": ctx["session_id"], "tool": tool,
                                     "reason": args.get("reason"), "args": _scrub({k: v for k, v in args.items() if k != "reason"}),
                                     "ok": ok, "result": str(result)[:1500]})


# ---------------- tools ----------------
async def t_overview(ctx, a):
    from core import outreach as oc, prospector as pe, mailboxes as mb, linkedin as li
    st = await oc.get_settings()
    boxes = await db.mailboxes.find({"is_deleted": {"$ne": True}}, {"_id": 0, "smtp_password": 0}).to_list(50)
    tpls = await db.email_templates.find({"is_deleted": {"$ne": True}}, {"_id": 0, "body": 0}).to_list(100)
    pool = []
    try:
        pool = [{k: s[k] for k in ("id", "email", "remaining")} for s in await mb.senders(st, oc._now()[:10])]
    except Exception:
        pass
    main = await ol_main()
    return {"main_inbox": {"id": 0, "email": main, "connected": bool(main), "how_to_pin": "from_inbox='main' or this email"},
            "outreach_settings": {k: v for k, v in st.items() if k != "pitch"}, "prospecting_settings": await pe.get_settings(),
            "linkedin_settings": await li.get_settings(), "inboxes_extra": _scrub(boxes), "senders_now": pool,
            "templates": _scrub(tpls),
            "counts": {"prospects": await db.prospects.estimated_document_count(),
                       "prospects_with_email": await db.prospects.count_documents({"contacts": {"$elemMatch": {"email": {"$nin": [None, ""]}}}}),
                       "outreach_queued": await db.outreach.count_documents({"status": {"$in": ["approved", "pending_approval"]}}),
                       "outreach_sent": await db.outreach.count_documents({"status": "sent"}),
                       "leads": await db.leads.estimated_document_count()},
            "last_outreach_run": await db.settings.find_one({"key": "outreach_last"}, {"_id": 0})}


async def t_find(ctx, a):
    col = a["collection"]
    if col not in READ_COLS:
        raise ValueError(f"Unknown collection {col}")
    f = a.get("filter") or {}
    _check(f)
    lim = max(1, min(int(a.get("limit") or 20), 200))
    cur = db[col].find(f, a.get("projection") or None)
    if a.get("sort"):
        cur = cur.sort(list(a["sort"].items()))
    rows = await cur.limit(lim).to_list(lim)
    return {"count": await db[col].count_documents(f), "rows": _scrub(rows)}


async def t_aggregate(ctx, a):
    col = a["collection"]
    if col not in READ_COLS:
        raise ValueError(f"Unknown collection {col}")
    pipe = a.get("pipeline") or []
    _check(pipe)
    rows = await db[col].aggregate(pipe + [{"$limit": 200}], maxTimeMS=20000).to_list(200)
    return {"rows": _scrub(rows)}


def _cell(row: dict, col: str):
    v = row
    for k in col.split("."):
        v = v.get(k) if isinstance(v, dict) else None
    if isinstance(v, list):
        return "; ".join(" ".join(str(x.get(f)) for f in ("name", "role", "email") if x.get(f)) if isinstance(x, dict) else str(x) for x in v)
    return "" if v is None else str(v)


async def t_export(ctx, a):
    import csv
    import io
    col = a["collection"]
    if col not in READ_COLS:
        raise ValueError(f"Unknown collection {col}")
    f = a.get("filter") or {}
    _check(f)
    cols = [c for c in (a.get("columns") or []) if not any(_secret(x) for x in c.split("."))]
    if not cols:
        cols = ["brand_name", "category", "segment", "country", "city", "store_count", "website", "fit_score", "contacts"] \
            if col == "prospects" else None
    per_contact = bool(a.get("one_row_per_contact")) and col == "prospects"
    cur = db[col].find(f, {"_id": 0})
    if a.get("sort"):
        cur = cur.sort(list(a["sort"].items()))
    rows = await cur.limit(50000).to_list(50000)
    if cols is None:
        cols = sorted({k for r in rows[:200] for k in r if not _secret(k)})
    if per_contact:
        base = [c for c in cols if c != "contacts"]
        cols = base + ["contact_name", "contact_role", "contact_email", "email_status", "contact_linkedin"]
        flat = []
        for r in rows:
            for c in r.get("contacts") or [{}]:
                flat.append({**{k: _cell(r, k) for k in base}, "contact_name": c.get("name"), "contact_role": c.get("role"),
                             "contact_email": c.get("email"), "email_status": c.get("email_status"), "contact_linkedin": c.get("linkedin")})
        rows = flat
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([_cell(r, c) for c in cols])
    name = re.sub(r"[^\w\-]+", "_", re.sub(r"\.csv$", "", a.get("file_name") or f"{col}_export", flags=re.I)).strip("_")[:60] + ".csv"
    eid = await next_id("brain_export")
    await db.brain_exports.insert_one({"id": eid, "session_id": ctx["session_id"], "user_id": ctx["user"]["id"], "filename": name,
                                       "rows": len(rows), "csv": buf.getvalue(), "at": now_utc_str()})
    return {"export_id": eid, "filename": name, "rows": len(rows), "columns": cols,
            "note": "A Download CSV button is shown to the user under your reply."}


async def t_update(ctx, a):
    col = a["collection"]
    if col not in WRITE_COLS:
        raise ValueError(f"Collection {col} is read-only for the Brain")
    f, s, u = a.get("filter") or {}, a.get("set") or {}, a.get("unset") or []
    if not f:
        raise ValueError("A filter is required")
    _check(f)
    _check(s)
    _fields_ok(s)
    _fields_ok({k: 1 for k in u})
    n = await db[col].count_documents(f)
    if n > MAX_WRITE:
        raise ValueError(f"{n} records match — narrow the filter (max {MAX_WRITE})")
    upd = {}
    if s:
        upd["$set"] = {**s, **({"updated_at": now_utc_str()} if col in ("email_templates", "mailboxes") else {})}
    if u:
        upd["$unset"] = {k: 1 for k in u}
    if not upd:
        raise ValueError("Nothing to change")
    r = await db[col].update_many(f, upd)
    return {"matched": r.matched_count, "modified": r.modified_count}


async def t_settings(ctx, a):
    from core import outreach as oc, linkedin as li
    sec, ch = a["section"], a.get("changes") or {}
    allowed = {"outreach": set(oc.DEFAULTS), "linkedin": set(li.DEFAULTS),
               "prospecting": {"enabled", "countries", "phase", "concurrency", "create_leads", "daily_cap"}}.get(sec)
    if allowed is None:
        raise ValueError("Section must be outreach, prospecting or linkedin")
    bad = set(ch) - allowed
    if bad:
        raise ValueError(f"Unknown settings: {sorted(bad)}; allowed: {sorted(allowed)}")
    if sec == "prospecting" and "phase" in ch and ch["phase"] not in ("auto", "discovery", "enrichment", "contacts"):
        raise ValueError("phase must be auto, discovery, enrichment or contacts")
    await db.settings.update_one({"key": sec}, {"$set": ch}, upsert=True)
    return {"saved": ch}


async def t_template(ctx, a):
    from core import email_templates as et
    fields = {k: a[k] for k in ("name", "subject", "body_html", "mode", "audience", "step", "wait_days", "ai_tailor", "active")
              if a.get(k) is not None}
    if "body_html" in fields:
        from core.outreach import clean_html
        fields["body"] = clean_html(fields.pop("body_html"))
    if fields.get("audience") and fields["audience"] not in et.AUDIENCES:
        raise ValueError(f"audience must be one of {list(et.AUDIENCES)}")
    fields["updated_at"] = now_utc_str()
    if a.get("template_id"):
        r = await db.email_templates.update_one({"id": int(a["template_id"]), "is_deleted": {"$ne": True}}, {"$set": fields})
        if not r.matched_count:
            raise ValueError("Template not found")
        return {"updated": int(a["template_id"])}
    if not fields.get("name") or not fields.get("subject") or not fields.get("body"):
        raise ValueError("New templates need name, subject and body_html")
    doc = {"kind": "custom", "mode": "manual", "audience": "all", "step": 1, "wait_days": 0, "ai_tailor": False, "active": True,
           "attachments": [], "video_links": [], "is_deleted": False, "created_at": now_utc_str(), **fields,
           "id": await next_id("email_template")}
    await db.email_templates.insert_one(doc)
    return {"created": doc["id"]}


async def ol_main():
    from core import mailer, outlook_send as ol
    try:
        cfg = await ol.get_config() or {}
        if await mailer.is_connected():
            return cfg.get("email") or cfg.get("smtp_user") or cfg.get("from_email")
    except Exception:
        pass
    return None


async def _sender_id(email):
    from core import mailboxes as mb, outlook_send as ol
    if email is None or str(email).strip() == "":
        return None
    e = str(email).strip().lower()
    if e in ("main", "0", "primary", "main inbox"):
        from core import mailer
        if not await mailer.is_connected():
            raise ValueError("The main inbox isn't connected (Admin → Email). Connect it or choose another inbox.")
        return mb.PRIMARY
    b = await db.mailboxes.find_one({"smtp_user": e, "is_deleted": {"$ne": True}}, {"id": 1})
    if b:
        return b["id"]
    cfg = await ol.get_config() or {}
    if (cfg.get("email") or cfg.get("smtp_user") or "").lower() == e:
        return mb.PRIMARY
    raise ValueError(f"No connected inbox {email}. Connected: main + " +
                     ", ".join([x["smtp_user"] async for x in db.mailboxes.find({"is_deleted": {"$ne": True}}, {"smtp_user": 1})]))


async def t_send(ctx, a):
    from core import email_templates as et
    from core.outreach import clean_html, pick_contact
    f = a.get("prospect_filter") or {}
    _check(f)
    sid = await _sender_id(a.get("from_inbox"))
    lim = max(1, min(int(a.get("limit") or 500), MAX_WRITE))
    t = {"subject": a["subject"], "body": clean_html(a["body_html"]), "ai_tailor": False}
    made, skipped, now = 0, 0, now_utc_str()
    if not await db.prospects.count_documents(f):
        raise ValueError("No brands match that filter — check categories/fields with find_records first")
    async for p in db.prospects.find({"$and": [f, {"replied": {"$ne": True}}]}, {"_id": 0}).limit(lim):
        bounced = {e.lower() for e in p.get("bounced_emails") or []}
        cs = [c for c in p.get("contacts") or [] if c.get("email") and c["email"].lower() not in bounced]
        if a.get("contacts") != "all":
            cs = [pick_contact(p)] if cs else []
        for c in [x for x in cs if x]:
            if await db.outreach.find_one({"to": c["email"], "subject_raw": a["subject"]}):
                skipped += 1
                continue
            subject, body = await et.tailor(t, p, c)
            lead_id = p.get("lead_id")
            await db.outreach.insert_one({
                "id": await next_id("outreach"), "seq_id": await next_id("outreach_seq"), "channel": "email", "step": 1,
                "kind": "brain", "template_name": a.get("campaign_name") or "Fundle Brain email", "prospect_id": p["id"],
                "lead_id": lead_id, "brand": p.get("brand_name"), "category": p.get("category"), "fit_score": p.get("fit_score"),
                "contact_name": c.get("name"), "contact_role": c.get("role"), "to": c["email"], "email_status": c.get("email_status"),
                "subject": subject, "subject_raw": a["subject"], "body": body, "attachments": [], "video_links": [],
                "force_sender": sid, "status": "approved", "created_by": f"Fundle Brain ({ctx['user'].get('name')})",
                "created_at": now, "scheduled_at": now, "approved_by": "Fundle Brain", "approved_at": now})
            made += 1
    return {"queued": made, "skipped_duplicates": skipped, "from_inbox": a.get("from_inbox") or "auto (rotating)",
            "note": "Emails go out within each inbox's hourly limit, starting with the next run (every 15 min) — call run_job outreach to start now."}


async def t_stop(ctx, a):
    f = a.get("outreach_filter") or {}
    _check(f)
    if not f:
        raise ValueError("A filter is required")
    r = await db.outreach.update_many({"$and": [f, {"status": {"$in": ["approved", "pending_approval", "sending"]}}]},
                                      {"$set": {"status": "stopped", "stop_reason": f"Fundle Brain: {a.get('reason')}"}})
    return {"stopped": r.modified_count}


async def t_contacts(ctx, a):
    f = a.get("prospect_filter") or {}
    _check(f)
    q = [f, {"contacts_status": {"$ne": "running"}}]
    if not a.get("include_brands_with_emails"):
        q.append({"$nor": [{"contacts": {"$elemMatch": {"email": {"$nin": [None, ""]}}}}]})
    r = await db.prospects.update_many({"$and": q},
                                       {"$set": {"contacts_priority": int(a.get("priority") or 5)}, "$unset": {"contacts_status": 1}})
    if a.get("contacts_only_mode"):
        await db.settings.update_one({"key": "prospecting"}, {"$set": {"phase": "contacts"}}, upsert=True)
    return {"queued_for_contact_search": r.matched_count, "contacts_only_mode": bool(a.get("contacts_only_mode"))}


async def t_job(ctx, a):
    from routes.prospecting import _spawn
    from core import outreach as oc, email_format as ef, inbox_sync, prospector as pe
    jobs = {"outreach": lambda: oc.run_outreach("brain"), "email_format": lambda: ef.run("brain"),
            "inbox_sync": inbox_sync.sync_all, "prospecting": lambda: pe.run_worker(trigger="brain")}
    if a["job"] not in jobs:
        raise ValueError(f"job must be one of {list(jobs)}")
    _spawn(jobs[a["job"]]())
    return {"started": a["job"]}


async def _send_now(ids: list) -> dict:
    """Send queued outreach docs right away through the normal sender (signature, retries, lead log)."""
    from core import mailboxes as mb, outreach as oc
    st = await oc.get_settings()
    pool = await mb.senders(st, oc._now()[:10])
    sent, failed = [], []
    for i in ids:
        m = await db.outreach.find_one_and_update({"id": i, "status": "approved"}, {"$set": {"status": "sending"}}, projection={"_id": 0})
        if not m:
            continue
        ok = await oc._send_one(m, st, pool, force=True)
        d = await db.outreach.find_one({"id": i}, {"_id": 0, "status": 1, "error": 1, "sent_from": 1, "to": 1})
        err = d.get("error")
        if ok is None:
            err = "Stopped: its template was removed or switched off"
        elif not ok and (not err or err == m.get("error")):
            pin = m.get("force_sender")
            err = ("No usable inbox: " + ("the main inbox isn't connected" if pin == mb.PRIMARY else
                   f"inbox #{pin} is inactive, untested or over its hourly limit" if pin is not None else
                   "no connected inbox has room this hour"))
            await db.outreach.update_one({"id": i}, {"$set": {"error": err}})
        (sent if ok else failed).append({"id": i, "to": d.get("to"), "from": d.get("sent_from"), "status": d.get("status"), "error": err})
    return {"sent": sent, "failed": failed,
            "note": "Failed ones stay queued and retry automatically within the hour." if failed else ""}


async def t_email(ctx, a):
    from core.outreach import clean_html
    sid = await _sender_id(a.get("from_inbox"))
    to = a["to"].strip().lower()
    p = await db.prospects.find_one({"contacts.email": {"$regex": f"^{re.escape(to)}$", "$options": "i"}}, {"_id": 0}) or {}
    c = next((x for x in p.get("contacts") or [] if (x.get("email") or "").lower() == to), {})
    now = now_utc_str()
    oid = await next_id("outreach")
    await db.outreach.insert_one({
        "id": oid, "seq_id": await next_id("outreach_seq"), "channel": "email", "step": 1, "kind": "brain",
        "template_name": a.get("campaign_name") or "Fundle Brain email", "prospect_id": p.get("id"), "lead_id": p.get("lead_id"),
        "brand": p.get("brand_name"), "category": p.get("category"), "contact_name": c.get("name") or to, "contact_role": c.get("role"),
        "to": to, "subject": a["subject"], "subject_raw": a["subject"], "body": clean_html(a["body_html"]), "attachments": [],
        "video_links": [], "force_sender": sid, "status": "approved", "created_by": f"Fundle Brain ({ctx['user'].get('name')})",
        "created_at": now, "scheduled_at": now, "approved_by": "Fundle Brain", "approved_at": now})
    r = await _send_now([oid])
    if r["failed"]:
        raise ValueError(f"Send failed from {r['failed'][0].get('from') or 'the inbox'}: {r['failed'][0].get('error')}. It stays queued and retries automatically (outreach #{oid}).")
    return r


async def _send_job(jid: int, ids: list):
    import asyncio
    from core.outreach import _conn_err
    streak = 0
    for i in ids:
        if (await db.brain_send_jobs.find_one({"id": jid}, {"status": 1}) or {}).get("status") == "cancelled":
            return
        try:
            r = await _send_now([i])
        except Exception as e:
            r = {"sent": [], "failed": [{"id": i, "error": str(e)[:200]}]}
        inc = {"done": 1, "sent": len(r["sent"]), "failed": len(r["failed"])}
        upd = {"$inc": inc, "$set": {"updated_at": now_utc_str()}}
        if r["failed"]:
            upd["$push"] = {"errors": {"$each": [f"#{x['id']} {x.get('to') or ''}: {x.get('error')}" for x in r["failed"]], "$slice": -20}}
        await db.brain_send_jobs.update_one({"id": jid}, upd)
        streak = streak + 1 if any(_conn_err(x.get("error")) for x in r["failed"]) else 0
        if streak >= 3:
            await db.brain_send_jobs.update_one({"id": jid}, {"$set": {
                "status": "paused", "finished_at": now_utc_str(),
                "pause_reason": "Paused: the email server keeps dropping the connection. Unsent emails stay queued and retry automatically."}})
            return
        await asyncio.sleep(2)
    await db.brain_send_jobs.update_one({"id": jid}, {"$set": {"status": "done", "finished_at": now_utc_str()}})


async def t_send_queued(ctx, a):
    f = a.get("outreach_filter") or {}
    _check(f)
    if not f:
        raise ValueError("A filter is required (e.g. {\"brand\": \"Go Colors\"} or {\"status\": \"approved\"} for everything queued)")
    ids = [m["id"] async for m in db.outreach.find({"$and": [f, {"channel": "email", "status": {"$in": ["approved", "pending_approval"]}}]},
                                                     {"id": 1}).sort("scheduled_at", 1).limit(3000)]
    if not ids:
        return {"sent": [], "failed": [], "note": "Nothing queued matches that filter."}
    await db.outreach.update_many({"id": {"$in": ids}, "status": "pending_approval"},
                                  {"$set": {"status": "approved", "approved_by": "Fundle Brain", "approved_at": now_utc_str()}})
    if a.get("from_inbox"):
        await db.outreach.update_many({"id": {"$in": ids}}, {"$set": {"force_sender": await _sender_id(a["from_inbox"])}})
    if len(ids) <= 3:
        r = await _send_now(ids)
        if r["failed"] and not r["sent"]:
            raise ValueError("Nothing sent: " + "; ".join(f"#{x['id']} {x['error']}" for x in r["failed"][:5]))
        return r
    from routes.prospecting import _spawn
    jid = await next_id("brain_send_job")
    await db.brain_send_jobs.insert_one({"id": jid, "session_id": ctx["session_id"], "user_id": ctx["user"]["id"], "total": len(ids),
                                         "done": 0, "sent": 0, "failed": 0, "errors": [], "status": "running", "started_at": now_utc_str()})
    _spawn(_send_job(jid, ids))
    return {"send_job_id": jid, "total": len(ids),
            "note": "Sending one by one in the background (about 2 s apart); a live 'x of N sent' progress bar is shown to the user under your reply."}


async def t_inbox(ctx, a):
    from core import mailboxes as mb
    days = max(1, min(int(a.get("days") or 7), 90))
    since = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    names = {mb.PRIMARY: (await ol_main()) or "main inbox"}
    names.update({b["id"]: b["smtp_user"] async for b in db.mailboxes.find({}, {"id": 1, "smtp_user": 1})})
    rows = await db.outreach.aggregate([{"$match": {"sent_at": {"$gte": since}}},
                                        {"$group": {"_id": {"via": "$sent_via", "status": "$status"}, "n": {"$sum": 1}}}]).to_list(200)
    per = {}
    for r in rows:
        k = names.get(r["_id"].get("via"), str(r["_id"].get("via")))
        per.setdefault(k, {})[r["_id"]["status"]] = r["n"]
    replies = await db.outreach.find({"replied_at": {"$gte": since}}, {"_id": 0, "brand": 1, "to": 1, "subject": 1, "replied_at": 1,
                                                                     "reply_snippet": 1, "sent_via": 1}).sort("replied_at", -1).limit(30).to_list(30)
    fails = await db.outreach.find({"error": {"$exists": True}, "status": {"$in": ["approved", "failed"]}},
                                   {"_id": 0, "id": 1, "brand": 1, "to": 1, "error": 1, "sent_via_pref": 1, "force_sender": 1}).sort("id", -1).limit(15).to_list(15)
    readers = await db.mailbox_readers.find({}, {"_id": 0, "mailbox_id": 1, "email": 1, "last_sync": 1, "last_error": 1}).to_list(50) \
        if "mailbox_readers" in await db.list_collection_names() else []
    return {"days": days, "per_inbox_status_counts": per, "recent_replies": replies, "recent_send_errors": fails,
            "reply_tracking": _scrub(readers)}


async def t_escalate(ctx, a):
    from core import mailer
    doc = {"id": await next_id("brain_request"), "at": now_utc_str(), "by": ctx["user"].get("name"), "summary": a["summary"],
           "details": a.get("details") or "", "reason": a.get("reason"), "session_id": ctx["session_id"], "status": "open"}
    await db.brain_requests.insert_one(doc)
    body = (f"<p><b>Fundle Brain change request #{doc['id']}</b> from {doc['by']}</p><p><b>{doc['summary']}</b></p>"
            f"<p>{doc['details']}</p><p>Reason: {doc['reason']}</p>")
    try:
        res = await mailer.send_email(DEV_EMAIL, f"Fundle Brain request: {doc['summary'][:80]}", body, html=True)
    except Exception:
        res = {"ok": False}
    if res.get("ok"):
        return {"request_id": doc["id"], "logged": True, "emailed": True, "to": DEV_EMAIL}
    return {"request_id": doc["id"], "logged": True, "emailed": False,
            "note": f"Request #{doc['id']} is saved for Vineet, but the email couldn't be sent (main inbox not connected or failed)."}


def _fn(name, desc, props, req, write=True):
    if write:
        props = {**props, "reason": {"type": "string", "description": "The user's reason for this change (ask if not given)"}}
        req = req + ["reason"]
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props, "required": req}}}


OBJ, STR, INT, BOOL = {"type": "object"}, {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
TOOLS = {
    "get_system_overview": (t_overview, _fn("get_system_overview", "Current settings (outreach, prospecting, linkedin), inboxes, templates (without body), live sender quotas and key counts. Call this first when unsure.", {}, [], False)),
    "find_records": (t_find, _fn("find_records", "Read records from a collection with a MongoDB filter. Returns total count + up to `limit` rows.",
                                 {"collection": STR, "filter": OBJ, "projection": OBJ, "sort": {"type": "object", "description": "e.g. {\"fit_score\": -1}"}, "limit": INT}, ["collection"], False)),
    "aggregate_records": (t_aggregate, _fn("aggregate_records", "Run a read-only MongoDB aggregation pipeline (for counts/breakdowns/analytics).",
                                           {"collection": STR, "pipeline": {"type": "array", "items": OBJ}}, ["collection", "pipeline"], False)),
    "export_csv": (t_export, _fn("export_csv", "Create a downloadable CSV file (up to 50,000 rows) of records matching a MongoDB filter — use whenever the user asks for a CSV/Excel/export/list download. A Download button appears in the chat. columns = field names (dotted ok; 'contacts' becomes name role email list). one_row_per_contact for contact lists.",
                                 {"collection": STR, "filter": OBJ, "columns": {"type": "array", "items": STR}, "sort": OBJ,
                                  "one_row_per_contact": BOOL, "file_name": STR}, ["collection"], False)),
    "update_records": (t_update, _fn("update_records", f"Change fields on records matching a filter (prospects, leads, outreach, email_templates, mailboxes, settings). $set / unset only, max {MAX_WRITE} records. Use for repairs, data fixes, inbox signature/sender_name/active, etc.",
                                     {"collection": STR, "filter": OBJ, "set": OBJ, "unset": {"type": "array", "items": STR}}, ["collection", "filter"])),
    "save_settings": (t_settings, _fn("save_settings", "Change engine settings. outreach: enabled, auto_send, hourly_cap, use_main_inbox (false = main inbox not used for outreach, only extra inboxes send), cc, drafts_per_run, signature (main inbox), sender_name, min_score, gaps, ai_sequence, daily_cap, pitch. prospecting: enabled, countries, phase (auto|discovery|enrichment|contacts), concurrency, create_leads, daily_cap (new brands/day, 0=unlimited). linkedin: enabled, daily_connects, daily_messages.",
                                      {"section": {"type": "string", "enum": ["outreach", "prospecting", "linkedin"]}, "changes": OBJ}, ["section", "changes"])),
    "save_email_template": (t_template, _fn("save_email_template", "Create (no template_id) or edit an email template. mode auto = goes out automatically to matching brands; audience all|d2c|stores_100|stores_11_99|stores_1_10; step 1 intro, 2/3 follow-ups with wait_days after the intro. Placeholders {{first_name}}, {{brand}}, {{role}}, {{intro_subject}}. Set active false to switch off.",
                                            {"template_id": INT, "name": STR, "subject": STR, "body_html": STR, "mode": {"type": "string", "enum": ["auto", "manual"]},
                                             "audience": STR, "step": INT, "wait_days": INT, "ai_tailor": BOOL, "active": BOOL}, [])),
    "send_email_campaign": (t_send, _fn("send_email_campaign", "Queue an email to brands matching a prospects filter (e.g. {\"category\": {\"$regex\": \"women\", \"$options\": \"i\"}} or {\"store_count\": {\"$lte\": 5}}). Uses each brand's best contact (or all contacts). Optional from_inbox (email address of a connected inbox) pins the sender. Skips replied brands, bounced addresses and exact repeats.",
                                        {"prospect_filter": OBJ, "subject": STR, "body_html": STR, "from_inbox": STR, "contacts": {"type": "string", "enum": ["best", "all"]},
                                         "limit": INT, "campaign_name": STR}, ["prospect_filter", "subject", "body_html"])),
    "stop_emails": (t_stop, _fn("stop_emails", "Stop queued (not yet sent) outreach emails matching an outreach filter, e.g. {\"template_name\": \"Intro email\"} or {\"brand\": \"X\"}.", {"outreach_filter": OBJ}, ["outreach_filter"])),
    "prioritize_contact_search": (t_contacts, _fn("prioritize_contact_search", "Put brands matching a prospects filter at the front of the AI contact search (e.g. category grocery, store_count 5). Optionally switch prospecting to contacts-only mode (no new brands).",
                                                  {"prospect_filter": OBJ, "priority": INT, "contacts_only_mode": BOOL,
                                                   "include_brands_with_emails": {"type": "boolean", "description": "also re-research brands that already have emails (costs more)"}}, ["prospect_filter"])),
    "run_job": (t_job, _fn("run_job", "Start a background job now: outreach (send queued emails + auto templates), prospecting, email_format, inbox_sync.", {"job": STR}, ["job"])),
    "send_single_email": (t_email, _fn("send_single_email", "Send one email RIGHT NOW to an address (e.g. a brand's contact), optionally from a specific inbox (email, sender name match or 'main'). Goes through the normal sender: inbox signature, logged on the brand's lead, auto-retry if it fails.",
                                       {"to": STR, "subject": STR, "body_html": STR, "from_inbox": STR, "campaign_name": STR}, ["to", "subject", "body_html"])),
    "send_queued_emails_now": (t_send_queued, _fn("send_queued_emails_now", "Send already-queued outreach emails immediately instead of waiting for the next run — one brand, or ALL queued ones (filter {\"status\": \"approved\"} for the whole Approved/scheduled bucket; up to 3000). More than 3 run in the background one by one with a live progress bar. Optional from_inbox pins the sender.",
                                                  {"outreach_filter": OBJ, "from_inbox": STR}, ["outreach_filter"])),
    "inbox_activity": (t_inbox, _fn("inbox_activity", "Mailbox knowledge: per-inbox sent/bounced/failed counts for the last N days, recent replies, recent send errors and reply-tracking status.",
                                    {"days": INT}, [], False)),
    "request_developer_change": (t_escalate, _fn("request_developer_change", "Only when a request truly needs new code or a new screen that no tool can do: logs it and emails Vineet.",
                                                 {"summary": STR, "details": STR}, ["summary"])),
}
from core import brain_data as bd  # noqa: E402
TOOLS.update({
    "read_uploaded_file": (bd.t_read_file, _fn("read_uploaded_file", "Read rows of a spreadsheet the user attached (file_id from the '[Attached file #N …]' note). Use it to see columns and sample rows before importing.",
                                               {"file_id": INT, "offset": INT, "limit": INT}, ["file_id"], False)),
    "import_brands_from_file": (bd.t_import_brands, _fn("import_brands_from_file", "Add every row of an attached file as a new brand + lead (skips brands already in the CRM). New brands go first in the queue: AI research → contacts → matching intro email automatically. mapping = {brand_name, category, country, website, store_count, city, segment} → column names; defaults for missing values.",
                                                        {"file_id": INT, "mapping": OBJ, "defaults": OBJ}, ["file_id", "mapping"])),
    "update_brands_from_file": (bd.t_update_brands, _fn("update_brands_from_file", "Fill in MISSING store counts, categories, city, website or segment on existing brands from an attached file (never overwrites values a brand already has). Matches by brand name, then website. Brands not in the CRM are added as new brands. mapping = {brand_name, store_count, category, city, website, segment, country} → column names.",
                                                        {"file_id": INT, "mapping": OBJ, "defaults": OBJ}, ["file_id", "mapping"])),
    "import_contact_emails_from_file": (bd.t_import_contacts, _fn("import_contact_emails_from_file", "Add/update verified contact emails on brands from an attached file. Matches brand by name or website/email domain; creates the brand if missing. Uploaded emails are trusted (published) and the AI stops searching that brand. mapping = {brand_name, website, contact_name, role, email, linkedin, country, category} → column names.",
                                                                  {"file_id": INT, "mapping": OBJ, "defaults": OBJ}, ["file_id", "mapping"])),
    "manage_contact_sources": (bd.t_sources, _fn("manage_contact_sources", "List, add or remove websites/LinkedIn pages the AI contact search must use when looking for decision makers (trade media, directories, association member lists...).",
                                                 {"action": {"type": "string", "enum": ["list", "add", "remove"]}, "name": STR, "url": STR, "notes": STR}, ["action"])),
})
READ_ONLY = {"get_system_overview", "find_records", "export_csv", "aggregate_records", "read_uploaded_file", "inbox_activity"}
ACTION_TOOLS = {"send_single_email", "send_email_campaign", "send_queued_emails_now", "stop_emails", "run_job"}

SYSTEM = """You are Fundle Brain, the operator of the Fundle.ai CRM (B2B sales CRM selling AI loyalty/retail software to brands in India/GCC/UK). You act on the live system through tools — you do not just advise.

SYSTEM MAP
- prospects: AI-researched brands. Fields: id, brand_name, category, segment ("Offline Retail" | "D2C / E-commerce" | "Omnichannel"), country, city, store_count, fit_score, website, status (discovered|enriching|enriched|failed), contacts [{name, role, email, email_status (published|confirmed_format|pattern), linkedin}], contacts_status (running|done|failed|absent=queued), contacts_priority, lead_id, replied, bounced_emails, sender_mailbox (inbox id the brand sticks to; 0 = main), tpl_sent [template ids], email_format.
- leads: CRM leads (id, name/company fields, lead_stage, user_id, tags, email_from, fit_score). Each prospect has lead_id.
- outreach: each email. Fields: id, seq_id, kind (template|brain), template_id, template_name, step, prospect_id, lead_id, brand, to, subject, status (pending_approval|approved|sending|sent|failed|stopped|bounced), scheduled_at, sent_at, sent_via (inbox id), force_sender, replied_at.
- email_templates: id, name, subject, body (HTML), mode (auto|manual), audience, step, wait_days, ai_tailor, active. Auto intros: one intro per brand chosen by audience (d2c segment; stores_100 = 100+; stores_11_99; stores_1_10 incl unknown). Follow-ups step 2/3 go wait_days after the intro to the same person if no reply.
- To email a specific brand/contact now: find the contact, use the brand's matching intro template text (email_templates by audience, fill {{first_name}}/{{brand}}) unless the user gave text, and call send_single_email (or send_queued_emails_now if an intro is already queued for that brand). Report the real result (sent from which inbox, or the exact error).
- CSV / Excel / export / "give me a list" requests: ALWAYS call export_csv (never say you can't make files). Then tell the user the row count and to tap Download CSV.
- Mailbox questions (what was sent, replies, bounces, errors per inbox): use inbox_activity.
- Inboxes: call get_system_overview first to see main_inbox and inboxes_extra; to send from the main inbox use from_inbox "main".
- mailboxes: extra sending inboxes (id, smtp_user email, sender_name, signature, active, verified). Main inbox = id 0 (its signature = outreach setting `signature`). Each inbox sends max hourly_cap per hour (default 50), spread over runs every 15 min. Ask "from Hardik's email" -> find the mailbox whose smtp_user/sender_name matches.
- settings: outreach / prospecting / linkedin sections (use save_settings).
- Engines run every 15 minutes automatically (prospecting, outreach send + reply/bounce sync, email-format check).

RULES
1. Do what the user asks immediately — no confirmation step. Direct orders to send, stop or run something need NO extra reason (the order is the reason) — just do it. Changes to settings, templates or data need the USER'S OWN reason (why they want it). Never invent or paraphrase a reason like "user requested it". If the user's messages don't say why, do NOT call any change tool — reply with one short question asking why, then act once they answer. Pass their reason in `reason`.
2. Before bulk changes, check scope with find_records/aggregate_records (counts), then do it, then report exactly what changed (numbers, names). Prefer specific filters; regex with $options "i" for categories/names.
3. Write emails in clean HTML (<p>, <ul><li>, <b>). Don't add a signature — the sending inbox adds its own.
4. New emails/templates/campaigns: first DRAFT the email in the chat as plain text (subject + body, no HTML tags) and ask "Shall I save/send it?"; only call save_email_template or send_email_campaign after the user approves. Uploaded or new brands get the automatic intro templates by themselves — NEVER call send_email_campaign or run_job after an import unless the user's latest message explicitly asks to send a specific email or run a job.
5. Attached files appear as "[Attached file #N: name — rows, columns]". Read a few rows, decide if it's a brand list, a contact-email list, or a sheet to update store counts/categories (user says update/fill/correct store counts or categories → update_brands_from_file), map the columns, then run it and report the counts. A file attachment with an instruction is the reason itself.
6. Contact sources: when the user gives sites/pages to find contacts, add them with manage_contact_sources (any non-list action needs a reason). Read-only actions never need a reason.
7. Only do what was asked. Never switch engines on/off, change settings or start jobs unless the user explicitly asked for that.
9. If something truly can't be done with your tools (new screens, new code features, integrations), call request_developer_change with a clear summary so Vineet is emailed, and tell the user.
8. Never reveal passwords or tokens. Format replies with short paragraphs and "- " bullet points and **bold** only — no markdown tables or # headings. Be concise, and answer analytics questions with real numbers from the tools.
Today: {today}."""


STOP = {"this", "that", "with", "from", "have", "they", "them", "their", "want", "need", "because", "should", "would", "will",
        "user", "asked", "requested", "explicitly", "please", "change", "make", "into", "these", "those", "about", "there"}


def _reason_ok(reason: str, said: str) -> bool:
    r = reason.strip().lower()
    if r.startswith("instruction:"):
        return False
    if len(r) < 4 or re.match(r"^(the )?user\b|^as (requested|asked)|^per (the )?user", r) or re.search(r"explicitly asked|user (requested|asked|wants)", r):
        return False
    words = {w for w in re.findall(r"[a-z0-9]{4,}", r) if w not in STOP}
    return not words or sum(w in said for w in words) * 2 >= len(words)


def _user_reason(said_msgs: list):
    for t in reversed(said_msgs):
        m = re.search(r"(?:reason\s*[:\-–]|because|since|so that)\s*(.+)", t, re.I | re.S)
        if m:
            return m.group(1).strip().split("\n")[0][:300]
    return None


GATES = {"send_queued_emails_now": r"\bsend\b|go ahead|\byes\b|approve|\bok(ay)?\b|\bnow\b",
         "send_email_campaign": r"\bsend\b|\bblast\b|\bshoot\b|go ahead|\byes\b|approve|\bok(ay)?\b|email (this|it|them) to",
         "send_single_email": r"\bsend\b|go ahead|\byes\b|approve|\bok(ay)?\b",
         "run_job": r"\b(run|start|now|sync|check|trigger|kick|yes)\b|go ahead"}


async def run_turn(ctx: dict, history: list, text: str):
    """Async generator of events: {"type": "text"|"tool"|"tool_done"|"error", ...}."""
    from emergentintegrations.llm.chat import LlmChat, UserMessage, TextDelta, ToolCallReady, StreamDone
    chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"brain-{ctx['session_id']}-{datetime.now().timestamp()}",
                   system_message=SYSTEM.replace("{today}", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
                   initial_messages=history).with_model(*MODEL).with_tools([v[1] for v in TOOLS.values()], tool_choice="auto")
    said_msgs = [re.sub(r"\[Attached file[^\]]*\]", "", m) for m in [m["content"] for m in history if m.get("role") == "user"][-6:] + [text]]
    said = " ".join(said_msgs).lower()
    given = _user_reason(said_msgs)
    latest = text.lower()
    msg = UserMessage(text=text)
    for _ in range(15):
        pending = []
        async for ev in chat.stream_message(msg):
            if isinstance(ev, TextDelta):
                yield {"type": "text", "delta": ev.content}
            elif isinstance(ev, ToolCallReady):
                pending.append(ev.tool_call)
            elif isinstance(ev, StreamDone):
                break
        if not pending:
            return
        for tc in pending:
            args = tc.arguments if isinstance(tc.arguments, dict) else json.loads(tc.arguments or "{}")
            yield {"type": "tool", "name": tc.name, "args": _scrub(args)}
            fn = TOOLS.get(tc.name)
            try:
                if not fn:
                    raise ValueError("Unknown tool")
                r = str(args.get("reason") or "").strip()
                if given and tc.name not in READ_ONLY:
                    args["reason"] = r = given  # the user's own words win over the model's paraphrase
                elif tc.name in ACTION_TOOLS:
                    args["reason"] = r = f"Instruction: {text.strip()[:250]}"  # a direct send/stop/run order is its own reason
                if tc.name not in READ_ONLY and tc.name not in ACTION_TOOLS and not (tc.name == "manage_contact_sources" and args.get("action") == "list") and not _reason_ok(r, said):
                    raise ValueError("No real reason from the user — ask the user WHY they want this change, then retry using their words")
                if tc.name in GATES and not re.search(GATES[tc.name], latest):
                    raise ValueError(f"The user's latest message didn't ask for {tc.name} — don't do it; just report what you did")
                if tc.name == "prioritize_contact_search" and args.get("contacts_only_mode") and not re.search(r"contacts? only|stop adding|no new brands|stop (searching|finding) brands", said):
                    args["contacts_only_mode"] = False
                out, ok = await fn[0](ctx, args), True
            except Exception as e:
                out, ok = {"error": str(e)[:500]}, False
            if tc.name not in READ_ONLY and not (tc.name == "manage_contact_sources" and args.get("action") == "list"):
                await _audit(ctx, tc.name, args, out, ok)
            res = json.dumps(_scrub(out), default=str)[:20000]
            yield {"type": "tool_done", "name": tc.name, "ok": ok, "summary": res[:300]}
            chat.add_tool_result(tc.id, res)
        msg = None
    yield {"type": "text", "delta": "\n\n(Stopped after many steps — tell me to continue if needed.)"}
