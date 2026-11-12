"""LinkedIn one-click queue: AI finds profiles + writes notes/messages; a person presses Send on LinkedIn."""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from core.db import db
from core.utils import log_message, next_id, now_utc_str

logger = logging.getLogger(__name__)
DEFAULTS = {"enabled": False, "daily_connects": 20, "daily_messages": 50, "drafts_per_run": 10, "lookups_per_run": 25}
WRITE_SYS = ("You write short, warm, specific LinkedIn messages for B2B outreach to Indian/GCC/UK retail leaders. "
             "No hype, no emojis, no invented facts. Reply with plain text only — no quotes, no subject line.")
LOOKUP_SYS = ("You find public LinkedIn profile URLs. For each person run a Google search like: \"<full name>\" \"<company>\" "
              "linkedin, and read the linkedin.com/in/... URL from the results (the slug usually contains their name). "
              "Only return a URL for that exact person at that company, else null. Reply with JSON only.")
AI = {"id": 0, "name": "LinkedIn Agent"}


async def get_settings() -> dict:
    doc = await db.settings.find_one({"key": "linkedin"}, {"_id": 0}) or {}
    return {**DEFAULTS, **{k: v for k, v in doc.items() if k in DEFAULTS}}


def day_start_utc() -> str:
    ist = timedelta(hours=5, minutes=30)
    start = (datetime.now(timezone.utc) + ist).replace(hour=0, minute=0, second=0, microsecond=0) - ist
    return start.strftime("%Y-%m-%d %H:%M:%S")


async def sent_today(kind: str) -> int:
    return await db.linkedin_tasks.count_documents({"kind": kind, "sent_at": {"$gte": day_start_utc()}})


def pick(p: dict):
    cs = [c for c in (p.get("contacts") or []) if c.get("linkedin")]
    mk = [c for c in cs if any(w in (c.get("role") or "").lower() for w in ("market", "cmo", "crm", "loyal", "customer", "digital"))]
    c = (mk or cs or [None])[0]
    if not c and p.get("ceo_linkedin") and p.get("ceo_name"):
        c = {"name": p["ceo_name"], "role": "CEO", "linkedin": p["ceo_linkedin"]}
    return c


async def _write(prompt: str, fallback: str, limit: int) -> str:
    from core import prospector as pe
    from core import outreach as oc
    st = await oc.get_settings()
    try:
        txt = (await pe._ask(pe.FLASH, WRITE_SYS, f"Seller: {st['pitch']}\n\n{prompt}")).strip().strip('"')
    except Exception as e:
        logger.warning(f"linkedin write failed: {e}")
        txt = ""
    return (txt or fallback)[:limit]


def _ctx(p: dict, c: dict) -> str:
    return (f"Prospect brand: {p.get('brand_name')} — {p.get('category')}, {p.get('country')}; stores: "
            f"{p.get('store_count') or 'unknown'}; existing loyalty programme: {p.get('has_loyalty_program') or 'unknown'}; "
            f"about: {(p.get('description') or '')[:400]}\nRecipient: {c['name']} ({c.get('role') or 'leader'}).\n")


async def draft_connect(p: dict, c: dict) -> str:
    first = c["name"].split()[0]
    fb = (f"Hi {first}, I work with retail brands on customer loyalty and repeat purchase. "
          f"Would love to connect and swap notes on what's working at {p['brand_name']}.")
    return await _write(_ctx(p, c) + "Write a LinkedIn connection request note, max 280 characters, using their first "
                        "name and one specific detail about the brand. No pitch, no link.", fb, 300)


async def draft_message(t: dict, reply: str | None = None) -> str:
    p = await db.prospects.find_one({"id": t["prospect_id"]}, {"_id": 0}) or {"brand_name": t["brand"]}
    c = {"name": t["contact_name"], "role": t.get("contact_role")}
    first = c["name"].split()[0]
    if reply:
        prompt = (_ctx(p, c) + f"They replied on LinkedIn:\n\"\"\"{reply[:2000]}\"\"\"\nWrite our next LinkedIn reply "
                  "(max 600 characters): answer what they said, and if they are open, propose a 20-minute call.")
        fb = f"Thanks {first}, appreciate the reply. Would a quick 20-minute call next week work to share more?"
    else:
        prompt = (_ctx(p, c) + "They just accepted our connection request. Write a first LinkedIn message (max 500 "
                  "characters): thank them, one relevant insight about loyalty for a brand like theirs, and a soft ask "
                  "for a 20-minute call.")
        fb = (f"Thanks for connecting, {first}! We help brands like {t['brand']} grow repeat purchase with an AI loyalty "
              "programme across stores and online. Open to a quick 20-minute chat?")
    return await _write(prompt, fb, 1500)


def _task(p: dict, c: dict, kind: str, body: str, **extra) -> dict:
    return {"prospect_id": p["id"], "lead_id": p.get("lead_id"), "brand": p.get("brand_name") or p.get("brand"),
            "category": p.get("category"), "fit_score": p.get("fit_score"), "contact_name": c["name"],
            "contact_role": c.get("role"), "linkedin": c["linkedin"], "kind": kind, "body": body,
            "status": "queued", "created_at": now_utc_str(), **extra}


async def new_message(t: dict, body: str, **extra) -> dict:
    p = {"id": t["prospect_id"], "lead_id": t.get("lead_id"), "brand": t["brand"], "category": t.get("category"),
         "fit_score": t.get("fit_score")}
    c = {"name": t["contact_name"], "role": t.get("contact_role"), "linkedin": t["linkedin"]}
    doc = {"id": await next_id("linkedin_task"), **_task(p, c, "message", body, parent_id=t["id"], **extra)}
    await db.linkedin_tasks.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def find_profiles(limit: int) -> int:
    """Fill in missing LinkedIn profile URLs for already-found decision makers."""
    from core import prospector as pe
    retry = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
    ps = await db.prospects.find({"contacts.0": {"$exists": True}, "contacts.linkedin": {"$in": [None, ""]},
                                  "$or": [{"li_lookup_at": {"$exists": False}}, {"li_lookup_at": {"$lt": retry}}]}, {"_id": 0}
                                 ).sort([("fit_score", -1)]).limit(limit).to_list(limit)
    if not ps:
        return 0
    await db.prospects.update_many({"id": {"$in": [p["id"] for p in ps]}}, {"$set": {"li_lookup_at": now_utc_str()}})
    sem, out = asyncio.Semaphore(5), {"n": 0, "stop": False}

    async def one(p):
        async with sem:
            if out["stop"]:
                return await db.prospects.update_one({"id": p["id"]}, {"$unset": {"li_lookup_at": 1}})
            cs = p["contacts"]
            for c in cs:
                if not c.get("linkedin") and p.get("ceo_linkedin") and c["name"].lower() == (p.get("ceo_name") or "").lower():
                    c["linkedin"] = p["ceo_linkedin"]
            names = [c["name"] for c in cs if not c.get("linkedin")]
            people = [f"{c['name']} — {c.get('role') or 'leader'}" for c in cs if not c.get("linkedin")]
            urls, t = {}, time.time()
            if names:
                prompt = (f"Company: \"{p['brand_name']}\" ({p.get('category')}, {p.get('country')}). People (name — role): {people}. "
                          'Return JSON with each name exactly as given (without the role): '
                          '{"profiles": [{"name": str, "linkedin": str|null}]}')
                try:
                    d = pe._json(await pe._ask(pe.FLASH, LOOKUP_SYS, prompt), "obj") or {}
                    urls = {str(x.get("name", "")).strip().lower(): x.get("linkedin") for x in d.get("profiles") or [] if isinstance(x, dict)}
                except Exception as e:
                    await pe._log_run("linkedin", pe.FLASH, False, e, time.time() - t, brand=p["brand_name"])
                    if isinstance(e, pe.AiCreditsOut):
                        out["stop"] = True
                        await db.prospects.update_one({"id": p["id"]}, {"$unset": {"li_lookup_at": 1}})
                        return
            new = []
            for c in cs:
                u = urls.get(c["name"].lower())
                if not c.get("linkedin") and isinstance(u, str) and "linkedin.com/in/" in u:
                    c["linkedin"] = pe._url(u)
                if c.get("linkedin") and c["name"] in names:
                    new.append(c["name"])
            await db.prospects.update_one({"id": p["id"]}, {"$set": {"contacts": cs}})
            out["n"] += len(new)
            if new and p.get("lead_id"):
                await log_message(p["lead_id"], "🔗 LinkedIn profiles found: " + ", ".join(new), author=AI)
            await pe._log_run("linkedin", pe.FLASH, True, f"{len(new)} LinkedIn profiles", time.time() - t, brand=p["brand_name"])

    await asyncio.gather(*(one(p) for p in ps))
    return out["n"]


async def build_queue(trigger="cron") -> dict:
    st = await get_settings()
    if not st["enabled"]:
        return {"skipped": "disabled"}
    stats = {"profiles": await find_profiles(st["lookups_per_run"]), "drafted": 0}
    queued = await db.linkedin_tasks.count_documents({"kind": "connect", "status": "queued"})
    need = min(st["drafts_per_run"], max(0, st["daily_connects"] - queued))
    if need:
        ps = await db.prospects.find({"li_task": {"$exists": False}, "$or": [
            {"contacts.linkedin": {"$nin": [None, ""]}}, {"ceo_linkedin": {"$nin": [None, ""]}, "ceo_name": {"$nin": [None, ""]}}]},
            {"_id": 0}).sort([("fit_score", -1), ("store_count", -1)]).limit(need).to_list(need)
        for p in ps:
            c = pick(p)
            if not c:
                continue
            got = await db.prospects.find_one_and_update({"id": p["id"], "li_task": {"$exists": False}},
                                                         {"$set": {"li_task": "queued"}})
            if not got:
                continue
            body = await draft_connect(p, c)
            await db.linkedin_tasks.insert_one({"id": await next_id("linkedin_task"), **_task(p, c, "connect", body)})
            stats["drafted"] += 1
    await db.settings.update_one({"key": "linkedin_last"}, {"$set": {"at": now_utc_str(), "stats": stats, "trigger": trigger}}, upsert=True)
    return stats
