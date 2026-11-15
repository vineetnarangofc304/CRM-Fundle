"""Fundle Brain LinkedIn tools: status, reports, contact search/export, connection sync and previewed invites/messages."""
import csv
import io
import re
from datetime import datetime, timedelta, timezone

from core import li_agent as la
from core.db import db
from core.utils import next_id, now_utc_str

STATUS = {"invited": {"c.li_invited_at": {"$exists": True}}, "accepted": {"c.li_accepted_at": {"$exists": True}},
          "messaged": {"c.li_msg_at": {"$exists": True}},
          "not_contacted": {"c.li_invited_at": {"$exists": False}, "c.li_msg_at": {"$exists": False}}}
COLS = ["brand", "name", "role", "headline", "degree", "source", "added_by", "linkedin", "email", "invited_at", "messaged_at", "added_at"]


async def t_li_status(ctx, a):
    from routes.li_agent import overview
    o = await overview(user=ctx["user"])
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    week = await db.li_actions.aggregate([{"$match": {"at": {"$gte": since}}},
                                          {"$group": {"_id": {"kind": "$kind", "ok": "$ok"}, "n": {"$sum": 1}}}]).to_list(50)
    accs = [{"id": x["id"], "name": x["name"], "type": "chrome_extension" if x.get("type") == "extension" else "server_login",
             "active": x.get("active"), "connected": x.get("verified"), "last_error": x.get("last_error"), "last_seen": x.get("last_seen"),
             "used_today": x.get("today"), "connection_import": x.get("import")} for x in o["accounts"]]
    return {"accounts": accs, "settings": o["settings"], "totals": o["totals"], "source_labels": la.SOURCES,
            "last_agent_run": o["last"], "last_7_days_actions": [{**r["_id"], "n": r["n"]} for r in week],
            "how_to_connect": "LinkedIn Prospecting page → Accounts: paste the li_at cookie (server login), or Chrome extension tab: "
                              "download the extension, create a key and paste it into the extension."}


def _match(a: dict) -> dict:
    m = {"$or": [{"c.li_source": {"$exists": True}}, {"c.li_urn": {"$nin": [None, ""]}}]}
    if a.get("source") in la.SOURCES:
        m["c.li_source"] = a["source"]
    if a.get("degree"):
        m["c.li_degree"] = int(a["degree"])
    for k, f in (("brand", "brand_name"), ("name", "c.name"), ("title", "c.li_headline")):
        if a.get(k):
            m[f] = {"$regex": re.escape(a[k]), "$options": "i"}
    m.update(STATUS.get(a.get("status") or "", {}))
    if a.get("added_since"):
        m["c.li_added_at"] = {"$gte": a["added_since"]}
    return m


def _row(r: dict) -> dict:
    c = r["c"]
    return {"brand": r.get("brand_name"), "prospect_id": r.get("id"), "name": c.get("name"), "role": c.get("role"), "headline": c.get("li_headline"),
            "degree": c.get("li_degree"), "source": la.SOURCES.get(c.get("li_source"), c.get("li_source") or "matched by lookup"),
            "added_by": c.get("li_source_by"), "linkedin": c.get("linkedin"), "email": c.get("email"), "invited_at": c.get("li_invited_at"),
            "messaged_at": c.get("li_msg_at"), "added_at": c.get("li_added_at")}


async def t_li_contacts(ctx, a):
    pipe = [{"$project": {"_id": 0, "id": 1, "brand_name": 1, "c": "$contacts"}}, {"$unwind": "$c"}, {"$match": _match(a)}]
    total = await db.prospects.aggregate(pipe + [{"$count": "n"}]).to_list(1)
    total = total[0]["n"] if total else 0
    if a.get("export_csv"):
        rows = [_row(r) async for r in db.prospects.aggregate(pipe + [{"$sort": {"c.li_added_at": -1}}, {"$limit": 50000}])]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(COLS)
        for r in rows:
            w.writerow(["" if r[k] is None else r[k] for k in COLS])
        name = re.sub(r"[^\w\-]+", "_", a.get("file_name") or "linkedin_contacts").strip("_")[:60] + ".csv"
        eid = await next_id("brain_export")
        await db.brain_exports.insert_one({"id": eid, "session_id": ctx["session_id"], "user_id": ctx["user"]["id"], "filename": name,
                                           "rows": len(rows), "csv": buf.getvalue(), "at": now_utc_str()})
        return {"export_id": eid, "filename": name, "rows": len(rows), "note": "A Download CSV button is shown to the user under your reply."}
    lim = max(1, min(int(a.get("limit") or 25), 100))
    rows = await db.prospects.aggregate(pipe + [{"$sort": {"c.li_added_at": -1}}, {"$limit": lim}]).to_list(lim)
    return {"total_matching": total, "rows": [_row(r) for r in rows]}


async def t_li_sync(ctx, a):
    from routes.prospecting import _spawn
    q = {"is_deleted": {"$ne": True}, "active": True}
    if a.get("account_id"):
        q["id"] = int(a["account_id"])
    accs = await db.li_accounts.find(q, {"_id": 0}).to_list(50)
    started, notes = [], []
    for acc in accs:
        if acc.get("type") == "extension":
            notes.append(f"{acc['name']} is a Chrome extension — open LinkedIn in that browser and click Import connections in the extension.")
        elif not acc.get("verified"):
            notes.append(f"{acc['name']} is signed out — reconnect it on the LinkedIn Prospecting page.")
        else:
            _spawn(la.import_connections(acc))
            started.append(acc["name"])
    if not accs:
        raise ValueError("No active LinkedIn account. Connect one on the LinkedIn Prospecting page first.")
    return {"sync_started_for": started, "notes": notes,
            "note": "Runs in the background (about 40 connections every few seconds); ask for linkedin_status to see progress."}


async def _eligible(kind: str, limit: int):
    em = {"li_urn": {"$nin": [None, ""]}, "li_sent_at": {"$in": [None, ""]}}
    em.update({"li_degree": {"$in": [2, 3]}, "li_invited_at": {"$exists": False}} if kind == "invite" else {"li_degree": 1, "li_msg_at": {"$exists": False}})
    pipe = [{"$match": {"contacts": {"$elemMatch": em}, "replied": {"$ne": True}}}, {"$unwind": "$contacts"},
            {"$match": {f"contacts.{k}": v for k, v in em.items()}}]
    n = await db.prospects.aggregate(pipe + [{"$count": "n"}]).to_list(1)
    sample = await db.prospects.aggregate(pipe + [{"$sort": {"fit_score": -1}}, {"$limit": limit},
                                                 {"$project": {"_id": 0, "brand_name": 1, "contacts": 1}}]).to_list(limit)
    return (n[0]["n"] if n else 0), sample


async def t_li_preview(ctx, a):
    kind = a["kind"]
    if kind not in ("invite", "message"):
        raise ValueError("kind must be invite or message")
    st = await la.get_settings()
    text = a.get("text") or st["invite_note" if kind == "invite" else "message"]
    if kind == "invite" and len(text) > 300:
        raise ValueError("LinkedIn invite notes are limited to 300 characters — shorten it")
    total, sample = await _eligible(kind, 5)
    accs = await db.li_accounts.find({"active": True, "verified": True, "is_deleted": {"$ne": True}}, {"_id": 0, "name": 1, "type": 1}).to_list(50)
    cap = st["daily_invites" if kind == "invite" else "daily_messages"]
    await db.brain_li_previews.update_one({"session_id": ctx["session_id"]},
                                          {"$set": {"kind": kind, "text": text, "at": now_utc_str(), "used": False}}, upsert=True)
    return {"kind": kind, "eligible_contacts": total, "text_template": text, "daily_limit_per_account": cap,
            "accounts_that_will_send": [x["name"] for x in accs], "currently_on": st[f"{kind}_on"],
            "examples": [{"to": p["contacts"]["name"], "brand": p["brand_name"], "text": la.fill(text, p["contacts"], p["brand_name"])} for p in sample],
            "note": "Show this preview to the user and ask for OK. Nothing is sent until they confirm."}


async def t_li_start(ctx, a):
    from routes.prospecting import _spawn
    pv = await db.brain_li_previews.find_one({"session_id": ctx["session_id"]}, {"_id": 0})
    cut = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    if not pv or pv.get("used") or pv.get("kind") != a.get("kind") or pv["at"] < cut:
        raise ValueError("Call linkedin_preview_outreach first, show the preview and wait for the user's OK")
    key = "invite" if pv["kind"] == "invite" else "message"
    ch = {f"{key}_on": True, ("invite_note" if key == "invite" else "message"): pv["text"]}
    await db.settings.update_one({"key": "li_agent"}, {"$set": ch}, upsert=True)
    await db.brain_li_previews.update_one({"session_id": ctx["session_id"]}, {"$set": {"used": True}})
    _spawn(la.run("brain"))
    return {"started": key, "saved": ch, "note": "Server accounts start now and every 15 min; Chrome extensions pick tasks up while LinkedIn is open. Daily limits apply."}


async def t_li_pause(ctx, a):
    kinds = [k for k in (a.get("kinds") or ["invite", "message"]) if k in ("invite", "message", "lookup")]
    ch = {f"{k}_on": False for k in kinds}
    await db.settings.update_one({"key": "li_agent"}, {"$set": ch}, upsert=True)
    return {"paused": kinds}
