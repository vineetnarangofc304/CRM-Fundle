"""Chrome-extension LinkedIn worker: the extension runs in a teammate's own logged-in browser, asks the CRM for
one task at a time (lookup / invite / message), does it on LinkedIn and reports back. Limits come from Admin."""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from core import li_agent as la
from core.db import db
from core.utils import log_message, next_id, now_utc_str

GAP = {"lookup": 30, "invite": 90, "message": 90}  # min seconds between actions of one kind per browser


def _hash(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


async def new_token(name: str, user: dict) -> dict:
    token = "fx_" + secrets.token_urlsafe(32)
    doc = {"id": await next_id("li_account"), "type": "extension", "name": (name or user["name"]).strip()[:60], "token_hash": _hash(token),
           "owner": user["name"], "active": True, "verified": True, "created_at": now_utc_str(), "updated_at": now_utc_str()}
    await db.li_accounts.insert_one(doc)
    return {"id": doc["id"], "name": doc["name"], "token": token}


async def account_for(token: str):
    if not token:
        return None
    return await db.li_accounts.find_one({"token_hash": _hash(token), "type": "extension", "active": True, "is_deleted": {"$ne": True}}, {"_id": 0})


async def _recent(acc_id: int, kind: str) -> bool:
    since = (datetime.now(timezone.utc) - timedelta(seconds=GAP[kind])).strftime("%Y-%m-%d %H:%M:%S")
    return bool(await db.li_actions.find_one({"account_id": acc_id, "kind": kind, "at": {"$gte": since}}))


def _free(c: dict, cut: str) -> bool:
    return not c.get("li_task_at") or c["li_task_at"] < cut


async def _find(kind: str, st: dict):
    cut = (datetime.now(timezone.utc) - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
    if kind == "lookup":
        em = {"name": {"$nin": [None, ""]}, "li_checked_at": {"$exists": False}}
        ok = lambda c: c.get("name") and not c.get("li_checked_at")  # noqa: E731
    elif kind == "invite":
        em = {"li_urn": {"$nin": [None, ""]}, "li_degree": {"$in": [2, 3]}, "li_invited_at": {"$exists": False}, "li_sent_at": {"$in": [None, ""]}}
        ok = lambda c: c.get("li_urn") and c.get("li_degree") in (2, 3) and not c.get("li_invited_at") and not c.get("li_sent_at")  # noqa: E731
    else:
        em = {"li_urn": {"$nin": [None, ""]}, "li_degree": 1, "li_msg_at": {"$exists": False}, "li_sent_at": {"$in": [None, ""]}}
        ok = lambda c: c.get("li_urn") and c.get("li_degree") == 1 and not c.get("li_msg_at") and not c.get("li_sent_at")  # noqa: E731
    async for p in db.prospects.find({"contacts": {"$elemMatch": em}, "replied": {"$ne": True}},
                                     {"_id": 0, "id": 1, "brand_name": 1, "contacts": 1}).sort("fit_score", -1).limit(20):
        for c in p["contacts"]:
            if ok(c) and _free(c, cut):
                await la._set_contact(p["id"], c["name"], {"li_task_at": now_utc_str()})
                t = {"kind": kind, "prospect_id": p["id"], "name": c["name"], "brand": p["brand_name"]}
                if kind == "lookup":
                    t["keywords"] = f"{c['name']} {p['brand_name']}"
                elif kind == "invite":
                    t.update(urn=c["li_urn"], text=la.fill(st["invite_note"], c, p["brand_name"])[:300])
                else:
                    t.update(urn=c["li_urn"], text=la.fill(st["message"], c, p["brand_name"]))
                return t
    return None


async def search_task(acc: dict):
    """A CRM user's people search waiting for a browser (only taken if asked in the last 5 minutes)."""
    cut = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    j = await db.li_search_jobs.find_one_and_update({"status": "pending", "created_at": {"$gte": cut}},
                                                    {"$set": {"status": "running", "account_id": acc["id"], "account": acc["name"]}},
                                                    sort=[("id", 1)], projection={"_id": 0})
    if j:
        return {"kind": "search", "job_id": j["id"], "keywords": j["keywords"], "filters": j["filters"], "start": j["start"], "name": "search", "brand": j["keywords"]}
    return None


async def next_task(acc: dict):
    st = await la.get_settings()
    for kind, on, lim in (("message", st["message_on"], st["daily_messages"]), ("invite", st["invite_on"], st["daily_invites"]),
                          ("lookup", st["lookup_on"], st["daily_lookups"])):
        if not on or await la.used_today(acc["id"], kind) >= lim or await _recent(acc["id"], kind):
            continue
        t = await _find(kind, st)
        if t:
            return t
    return None


async def result(acc: dict, b: dict) -> dict:
    kind, pid, name = b["kind"], b["prospect_id"], b["name"]
    p = await db.prospects.find_one({"id": pid}, {"_id": 0})
    c = next((x for x in (p or {}).get("contacts") or [] if x.get("name") == name), None)
    if not c:
        return {"ok": False, "error": "contact gone"}
    await la._set_contact(pid, name, {"li_task_at": None})
    if not b.get("ok"):
        await la._act(acc, kind, False, prospect_id=pid, name=name, error=(b.get("error") or "")[:200])
        if kind == "lookup":
            await la._set_contact(pid, name, {"li_checked_at": now_utc_str()})
        return {"ok": True}
    if kind == "lookup":
        m = await la.apply_lookup(p, c, [la._norm(r) for r in b.get("results") or []])
        await la._act(acc, kind, bool(m), prospect_id=pid, name=name)
        return {"ok": True, "matched": bool(m)}
    now = now_utc_str()
    if kind == "invite":
        await la._set_contact(pid, name, {"li_invited_at": now, "li_invited_by": acc["name"]})
        note = f"🤝 LinkedIn invite sent to {name} from {acc['name']}'s browser"
    else:
        await la._set_contact(pid, name, {"li_msg_at": now, "li_sent_at": now, "li_sent_by": acc["name"]})
        note = f"💬 LinkedIn message sent to {name} from {acc['name']}'s browser"
    await la._act(acc, kind, True, prospect_id=pid, name=name)
    await db.li_accounts.update_one({"id": acc["id"]}, {"$set": {"last_seen": now}})
    if p.get("lead_id"):
        await log_message(p["lead_id"], f"{note}:<br>{re.sub('<', '&lt;', b.get('text') or '')}", author=la.AGENT)
    return {"ok": True}
