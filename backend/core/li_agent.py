"""LinkedIn Agent: our own direct LinkedIn connection (logged-in account session, no outside service).
Finds the real LinkedIn profile of every CRM contact, invites 2nd/3rd-degree people and messages 1st-degree
connections, within the daily limits set in Admin."""
import asyncio
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone

from core.db import db
from core.utils import log_message, next_id, now_utc_str

logger = logging.getLogger(__name__)
AGENT = {"id": 0, "name": "LinkedIn Agent"}
DEFAULTS = {"lookup_on": True, "invite_on": False, "message_on": False, "daily_lookups": 80, "daily_invites": 20, "daily_messages": 50,
            "invite_note": "Hi {first_name}, I work with retail brands like {brand} on loyalty & repeat purchases. Would love to connect.",
            "message": "Hi {first_name}, thanks for connecting! At Fundle we help retail brands like {brand} grow repeat purchases with "
                       "loyalty across stores and online. Open to a quick 15-min chat this week?"}
REGIONS = {"India": "102713980", "United Arab Emirates": "104305776", "Saudi Arabia": "100459316", "Qatar": "104170880",
           "Kuwait": "103239229", "Oman": "103619019", "Bahrain": "100425729", "United Kingdom": "101165590", "United States": "103644278"}
_lock = {"on": False}


async def get_settings() -> dict:
    d = await db.settings.find_one({"key": "li_agent"}, {"_id": 0}) or {}
    return {**DEFAULTS, **{k: v for k, v in d.items() if k in DEFAULTS}}


def fill(t: str, c: dict, brand: str) -> str:
    return (t.replace("{first_name}", (c.get("name") or "").split(" ")[0]).replace("{name}", c.get("name") or "")
            .replace("{brand}", brand or "").replace("{role}", c.get("role") or "")).strip()


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00")


async def used_today(acc_id: int, kind: str) -> int:
    return await db.li_actions.count_documents({"account_id": acc_id, "kind": kind, "at": {"$gte": _day()}})


async def _act(acc: dict, kind: str, ok: bool, **extra):
    await db.li_actions.insert_one({"account_id": acc["id"], "kind": kind, "ok": ok, "at": now_utc_str(), **extra})


# ---------- session ----------

def _client_sync(acc: dict):
    from requests.cookies import RequestsCookieJar
    from linkedin_api import Linkedin
    from core.outlook_send import dec
    li_at, js = dec(acc.get("li_at_enc")), dec(acc.get("jsession_enc"))
    if li_at:
        jar = RequestsCookieJar()
        jar.set("li_at", li_at, domain=".www.linkedin.com", path="/")
        jar.set("JSESSIONID", js or f'"ajax:{random.randint(10**18, 10**19 - 1)}"', domain=".www.linkedin.com", path="/")
        return Linkedin("", "", cookies=jar, cookies_dir="/tmp/li_cookies/")
    return Linkedin(acc["email"], dec(acc.get("password_enc")), cookies_dir="/tmp/li_cookies/")


_clients: dict = {}


async def client(acc: dict):
    key = (acc["id"], acc.get("updated_at"))
    if key not in _clients:
        cl = await asyncio.to_thread(_client_sync, acc)
        if not acc.get("li_at_enc"):  # password login → keep its session cookies so we don't log in again
            from core.outlook_send import enc
            ck = cl.client.session.cookies
            await db.li_accounts.update_one({"id": acc["id"]}, {"$set": {"li_at_enc": enc(ck.get("li_at")), "jsession_enc": enc(ck.get("JSESSIONID"))}})
        _clients[key] = cl
    return _clients[key]


async def test(acc: dict) -> dict:
    try:
        cl = await client(acc)
        me = await asyncio.to_thread(cl.get_user_profile, False)
        mp = me.get("miniProfile") or {}
        name = " ".join(x for x in (mp.get("firstName"), mp.get("lastName")) if x)
        if not name:
            raise ValueError("LinkedIn did not accept this login (cookie expired or verification needed)")
        await db.li_accounts.update_one({"id": acc["id"]}, {"$set": {"verified": True, "me_name": name, "last_error": None, "tested_at": now_utc_str()}})
        return {"ok": True, "name": name}
    except Exception as e:
        _clients.pop((acc["id"], acc.get("updated_at")), None)
        await db.li_accounts.update_one({"id": acc["id"]}, {"$set": {"verified": False, "last_error": str(e)[:300]}})
        return {"ok": False, "error": str(e)[:300]}


async def accounts(kind: str | None = None, limit: int = 0) -> list:
    out = []
    async for a in db.li_accounts.find({"active": True, "verified": True, "is_deleted": {"$ne": True}}, {"_id": 0}):
        if kind:
            a["room"] = limit - await used_today(a["id"], kind)
            if a["room"] <= 0:
                continue
        out.append(a)
    return out


def _norm(r: dict) -> dict:
    d = r.get("distance") or ""
    return {"name": r.get("name"), "urn_id": r.get("urn_id"), "headline": r.get("jobtitle"), "location": r.get("location"),
            "degree": 1 if d.endswith("_1") else 2 if d.endswith("_2") else 3 if d.endswith("_3") else None,
            "linkedin": f"https://www.linkedin.com/in/{r['urn_id']}/" if r.get("urn_id") else None}


async def search(acc: dict, keywords=None, title=None, company=None, regions=None, network=None, limit=25, offset=0) -> list:
    cl = await client(acc)
    rs = await asyncio.to_thread(cl.search_people, keywords=keywords or None, keyword_title=title or None, keyword_company=company or None,
                                 regions=[REGIONS[r] for r in regions or [] if r in REGIONS] or None, network_depths=network or None,
                                 limit=min(limit, 100), offset=offset)
    await _act(acc, "search", True, q=" ".join(x for x in (keywords, title, company) if x)[:120])
    return [_norm(r) for r in rs if r.get("name") and r.get("name") != "LinkedIn Member"]


# ---------- agent ----------

def _words(s: str) -> set:
    return {w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(w) > 2 and w not in {"the", "and", "pvt", "ltd", "limited", "private", "india"}}


def best_match(rs: list, c: dict, brand: str):
    nm, bw = _words(c.get("name")), _words(brand)
    named = [r for r in rs if nm and len(nm & _words(r["name"])) >= min(2, len(nm))]
    return next((r for r in named if bw & _words(r.get("headline"))), None)  # brand must appear — never guess a namesake


async def _set_contact(pid: int, name: str, upd: dict):
    await db.prospects.update_one({"id": pid}, {"$set": {f"contacts.$[c].{k}": v for k, v in upd.items()}}, array_filters=[{"c.name": name}])


async def _lookups(st: dict, deadline: float) -> int:
    n = 0
    accs = await accounts("lookup", st["daily_lookups"])
    if not accs:
        return 0
    recheck = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    q = {"contacts": {"$elemMatch": {"name": {"$nin": [None, ""]}, "$or": [
        {"li_checked_at": {"$exists": False}},
        {"li_invited_at": {"$lte": recheck}, "li_degree": {"$ne": 1}, "li_checked_at": {"$lte": recheck}}]}}}
    per_run = sum(max(1, -(-a["room"] // 24)) for a in accs)
    async for p in db.prospects.find(q, {"_id": 0, "id": 1, "brand_name": 1, "contacts": 1, "lead_id": 1}).sort("fit_score", -1).limit(per_run * 2):
        for c in p.get("contacts") or []:
            if n >= per_run or time.time() > deadline or not accs:
                return n
            if not c.get("name") or (c.get("li_checked_at") and not (c.get("li_invited_at") and c.get("li_degree") != 1)):
                continue
            acc = accs[n % len(accs)]
            try:
                rs = await search(acc, keywords=f"{c['name']} {p['brand_name']}", limit=5)
                m = best_match(rs, c, p["brand_name"])
                upd = {"li_checked_at": now_utc_str()}
                if m:
                    upd.update(linkedin=m["linkedin"], li_urn=m["urn_id"], li_degree=m["degree"], li_headline=m["headline"], li_verified=True)
                    if m["degree"] == 1 and c.get("li_invited_at") and not c.get("li_accepted_at"):
                        upd["li_accepted_at"] = now_utc_str()
                await _set_contact(p["id"], c["name"], upd)
                await _act(acc, "lookup", bool(m), prospect_id=p["id"], name=c["name"])
                n += 1
            except Exception as e:
                await _fail(acc, e)
                accs = [a for a in accs if a["id"] != acc["id"]]
            await asyncio.sleep(random.uniform(25, 60))
    return n


async def _fail(acc: dict, e: Exception):
    msg = str(e)[:300]
    logger.warning(f"linkedin agent {acc.get('name')}: {msg}")
    if re.search(r"401|403|challenge|unauthor|login|expired|redirect", msg, re.I):
        await db.li_accounts.update_one({"id": acc["id"]}, {"$set": {"verified": False, "last_error": "LinkedIn signed this account out — reconnect it. " + msg}})
    else:
        await db.li_accounts.update_one({"id": acc["id"]}, {"$set": {"last_error": msg}})


async def _outreach(st: dict, kind: str, deadline: float) -> int:
    """kind invite → 2nd/3rd-degree verified contacts; message → 1st-degree contacts not yet messaged."""
    lim = st["daily_invites" if kind == "invite" else "daily_messages"]
    accs = await accounts(kind, lim)
    if not accs:
        return 0
    em = {"li_urn": {"$nin": [None, ""]}, "li_sent_at": {"$in": [None, ""]}}
    em.update({"li_degree": {"$in": [2, 3]}, "li_invited_at": {"$exists": False}} if kind == "invite" else {"li_degree": 1, "li_msg_at": {"$exists": False}})
    per_run, n = sum(max(1, -(-a["room"] // 24)) for a in accs), 0
    async for p in db.prospects.find({"contacts": {"$elemMatch": em}, "replied": {"$ne": True}},
                                     {"_id": 0, "id": 1, "brand_name": 1, "contacts": 1, "lead_id": 1}).sort("fit_score", -1).limit(per_run * 2):
        for c in p["contacts"]:
            if n >= per_run or time.time() > deadline or not accs:
                return n
            ok_c = c.get("li_urn") and not c.get("li_sent_at") and (
                (kind == "invite" and c.get("li_degree") in (2, 3) and not c.get("li_invited_at")) or
                (kind == "message" and c.get("li_degree") == 1 and not c.get("li_msg_at")))
            if not ok_c:
                continue
            acc = accs[n % len(accs)]
            try:
                cl = await client(acc)
                if kind == "invite":
                    body = fill(st["invite_note"], c, p["brand_name"])[:300]
                    err = await asyncio.to_thread(cl.add_connection, None, body, c["li_urn"])
                    if err:
                        raise ValueError("LinkedIn refused the invite (weekly invite limit reached or already invited)")
                    await _set_contact(p["id"], c["name"], {"li_invited_at": now_utc_str(), "li_invited_by": acc["name"]})
                    note = f"🤝 LinkedIn invite sent to {c['name']} from {acc['name']}'s LinkedIn"
                else:
                    body = fill(st["message"], c, p["brand_name"])
                    err = await asyncio.to_thread(cl.send_message, body, None, [c["li_urn"]])
                    if err:
                        raise ValueError("LinkedIn refused the message")
                    await _set_contact(p["id"], c["name"], {"li_msg_at": now_utc_str(), "li_sent_at": now_utc_str(), "li_sent_by": acc["name"]})
                    note = f"💬 LinkedIn message sent to {c['name']} from {acc['name']}'s LinkedIn"
                await _act(acc, kind, True, prospect_id=p["id"], name=c["name"])
                if p.get("lead_id"):
                    await log_message(p["lead_id"], f"{note}:<br>{body}", author=AGENT)
                n += 1
            except Exception as e:
                await _act(acc, kind, False, prospect_id=p["id"], name=c["name"], error=str(e)[:200])
                await _fail(acc, e)
                accs = [a for a in accs if a["id"] != acc["id"]]
            await asyncio.sleep(random.uniform(40, 120))
    return n


async def run(trigger="cron") -> dict:
    if _lock["on"]:
        return {"skipped": "already running"}
    _lock["on"] = True
    st, stats = await get_settings(), {"trigger": trigger}
    deadline = time.time() + 13 * 60
    try:
        if st["lookup_on"]:
            stats["lookups"] = await _lookups(st, deadline - 300 if st["invite_on"] or st["message_on"] else deadline)
        if st["message_on"]:
            stats["messages"] = await _outreach(st, "message", deadline)
        if st["invite_on"]:
            stats["invites"] = await _outreach(st, "invite", deadline)
    except Exception as e:
        logger.warning(f"linkedin agent run failed: {e}")
        stats["error"] = str(e)[:200]
    finally:
        _lock["on"] = False
    await db.settings.update_one({"key": "li_agent_last"}, {"$set": {"at": now_utc_str(), "stats": stats}}, upsert=True)
    return stats


async def save_people(people: list, user: dict) -> dict:
    """Search results → brands + contacts in the CRM (brand from 'Title at Company' or the given company)."""
    from core import brain_data as bd
    from core import prospector as pe
    st = {"saved": 0, "brands_added": 0, "updated": 0, "skipped": 0}
    for x in people:
        brand = (x.get("company") or "").strip() or (re.split(r"\s+(?:at|@)\s+", x.get("headline") or "", maxsplit=1)[1:] or [""])[0].split("|")[0].strip()
        if not brand or not x.get("name"):
            st["skipped"] += 1
            continue
        p = await db.prospects.find_one({"brand_key": pe.brand_key(brand)}, {"_id": 0})
        if not p:
            p, _ = await bd._new_prospect(brand, x.get("country") or "India", "LinkedIn search", None, None, priority=0)
            st["brands_added"] += 1
        cs = p.get("contacts") or []
        c = next((c for c in cs if c.get("name", "").lower() == x["name"].lower()), None)
        li = {"linkedin": x.get("linkedin"), "li_urn": x.get("urn_id"), "li_degree": x.get("degree"), "li_headline": x.get("headline"),
              "li_verified": True, "li_checked_at": now_utc_str()}
        if c:
            c.update({k: v for k, v in li.items() if v is not None})
            st["updated"] += 1
        else:
            cs.append({"name": x["name"], "role": (re.split(r"\s+(?:at|@)\s+", x.get("headline") or "")[0])[:80], "email": None, **li,
                       "evidence_url": "LinkedIn search"})
            st["saved"] += 1
        await db.prospects.update_one({"id": p["id"]}, {"$set": {"contacts": cs, "contacts_status": "done"}})
        if p.get("lead_id"):
            await db.leads.update_one({"id": p["lead_id"]}, {"$unset": {"search_words": 1}})
    return st


async def add_account(data: dict, user: dict) -> dict:
    from core.outlook_send import enc
    li_at = (data.get("li_at") or "").strip().strip('"')
    js = (data.get("jsessionid") or "").strip()
    if js and not js.startswith('"'):
        js = f'"{js}"'
    if not li_at and not (data.get("email") and data.get("password")):
        raise ValueError("Give the li_at cookie (recommended) or the LinkedIn email + password")
    doc = {"id": await next_id("li_account"), "name": (data.get("name") or data.get("email") or "LinkedIn account").strip()[:60],
           "email": (data.get("email") or "").strip() or None, "li_at_enc": enc(li_at) if li_at else None,
           "jsession_enc": enc(js) if js else None, "password_enc": enc(data["password"]) if data.get("password") else None,
           "active": True, "verified": False, "created_at": now_utc_str(), "updated_at": now_utc_str(), "created_by": user["name"]}
    await db.li_accounts.insert_one(doc)
    return doc
