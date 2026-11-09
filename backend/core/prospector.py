"""AI prospecting engine: discovers consumer brands per country+category with Gemini Flash (Google Search
grounding), deep-researches each brand with Gemini Pro, and lists every brand as a CRM lead."""
import asyncio
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone

from emergentintegrations.llm.chat import LlmChat, UserMessage

from core.db import db
from core.utils import ensure_catalog, ist_date_parts, log_message, next_id, now_utc_str, search_norm, to_ist_str

logger = logging.getLogger(__name__)

FLASH = "gemini-3.5-flash"
PRO = "gemini-3.1-pro-preview"
SOURCE = "AI Prospecting"

COUNTRIES = ["India", "UAE", "Saudi Arabia", "Qatar", "Kuwait", "Oman", "Bahrain", "United Kingdom"]
MARKETPLACES = {
    "India": "Amazon.in, Flipkart, Myntra, Nykaa, Ajio, Tata CLiQ, FirstCry, Swiggy/Zomato",
    "United Kingdom": "Amazon.co.uk, ASOS, John Lewis, Boots, Deliveroo, Just Eat",
}
ASSOCIATIONS = {
    "India": "Retailers Association of India (RAI), Franchise India (franchiseindia.com), CMAI, NRAI",
    "United Kingdom": "British Retail Consortium, British Franchise Association",
}
GCC_MARKET = "Amazon.ae, Noon, Namshi, Ounass, Talabat, Deliveroo"
GCC_ASSOC = "Dubai Chamber retail groups, franchise portfolios of Majid Al Futtaim, Alshaya, Landmark Group, Apparel Group, Al-Futtaim"

CATEGORIES = [
    "Women's Western Fashion", "Women's Ethnic & Indian Traditional Wear", "Men's Fashion", "Kids' Wear & Toys",
    "Footwear", "Gold & Diamond Jewellery", "Fashion Jewellery & Accessories", "Watches & Eyewear",
    "Innerwear, Lingerie & Athleisure", "Sportswear & Sports Goods", "Beauty & Cosmetics",
    "Personal Care & Grooming", "Health, Wellness & Pharmacy", "Gyms & Fitness Centres", "Salons & Spas",
    "QSR & Restaurant Chains", "Cafés, Bakeries & Desserts", "Packaged Food & Beverages", "Grocery & Supermarkets",
    "Consumer Electronics & Mobiles", "Home Appliances", "Home Décor & Furnishings", "Furniture & Mattresses",
    "Kitchenware & Homeware", "Cars & Automobile Dealers", "Two-Wheelers & EVs", "Optical Stores",
    "Books, Stationery & Gifting", "Pet Care", "Baby & Maternity", "Travel, Bags & Luggage",
    "Malls & Shopping Centres", "Department Stores & Hypermarkets",
]
ANGLES = [
    "industry association member lists and franchise directories ({assoc})",
    "brand stores and top sellers on marketplaces ({market})",
    "shopping-mall store directories in the biggest cities",
    "recent news, funding and store-expansion announcements",
    "fast-growing D2C / e-commerce brands with a large customer base",
    "regional and city-level brands that are not nationally famous",
    "franchise opportunity listings",
    "'top brands' rankings and market reports",
    "LinkedIn posts by founders, retail leaders and pages like D2C Insider, Inc42, YourStory, Entrackr and ET Retail",
    "news of new store / outlet / flagship openings and city launches in the last 12 months",
    "startup funding and acquisition news for consumer brands (Inc42, YourStory, Entrackr, VCCircle, Economic Times)",
    "D2C newsletters, brand lists and award shortlists (D2C Insider, Brand Stack, Shark Tank featured brands)",
]
SEGMENTS = ["Offline Retail", "D2C / E-commerce", "Omnichannel"]

DISCOVER_SYS = ("You are a B2B market researcher building a prospect list for a customer-loyalty platform. "
                "Use Google Search. Only return real, currently operating brands. Reply with a JSON array only.")
ENRICH_SYS = ("You are a meticulous company researcher. Use Google Search to verify facts from official sites, "
              "LinkedIn, news and filings. Never invent names, numbers or URLs — use null when unknown. "
              "Reply with one JSON object only.")


def brand_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())[:80]


def _json(text: str, kind: str):
    text = text or ""
    a, b = ("[", "]") if kind == "list" else ("{", "}")
    s, e = text.find(a), text.rfind(b)
    if s < 0 or e <= s:
        return None
    try:
        return json.loads(text[s:e + 1])
    except ValueError:
        return None


def _int(v):
    if v is None:
        return None
    m = re.search(r"\d[\d,]*", str(v))
    return int(m.group().replace(",", "")) if m else None


def _url(v):
    v = (v or "").strip() if isinstance(v, str) else ""
    return v if v.startswith("http") else (f"https://{v}" if v and "." in v and " " not in v else None)


RETRY_COOLDOWN_H = 6
MAX_RETRY_ROUNDS = 5


class AiCreditsOut(Exception):
    pass


_QUOTA_MARKERS = ("spend limit", "budget", "insufficient balance", "insufficient_quota")
_blocked = {"on": None}


async def _ask(model: str, system: str, prompt: str) -> str:
    chat = (LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"prospect-{time.time_ns()}",
                    system_message=system).with_model("gemini", model).with_tools([{"googleSearch": {}}]))
    try:
        r = await asyncio.wait_for(chat.send_message_with_tools(UserMessage(text=prompt)), timeout=240)
    except Exception as e:
        if any(m in str(e).lower() for m in _QUOTA_MARKERS):
            _blocked["on"] = True
            await db.settings.update_one({"key": "prospect_lock", "ai_blocked_at": {"$exists": False}},
                                         {"$set": {"ai_blocked_at": now_utc_str()}})
            await db.settings.update_one({"key": "prospect_lock"}, {"$set": {"ai_blocked_reason": str(e)[:200]}})
            raise AiCreditsOut(str(e)) from e
        raise
    if _blocked["on"] is not False:
        _blocked["on"] = False
        await db.settings.update_one({"key": "prospect_lock"}, {"$unset": {"ai_blocked_at": 1, "ai_blocked_reason": 1}})
    return str(getattr(r, "content", r) or "")


async def get_settings() -> dict:
    doc = await db.settings.find_one({"key": "prospecting"}, {"_id": 0}) or {}
    return {"enabled": doc.get("enabled", True), "countries": doc.get("countries") or ["India"],
            "phase": doc.get("phase", "auto"), "concurrency": int(doc.get("concurrency") or 3),
            "create_leads": doc.get("create_leads", True), "daily_cap": int(doc.get("daily_cap") or 0)}


async def under_cap(settings) -> bool:
    return not settings["daily_cap"] or await added_today() < settings["daily_cap"]


async def added_today() -> int:
    ist = timedelta(hours=5, minutes=30)
    start = ((datetime.now(timezone.utc) + ist).replace(hour=0, minute=0, second=0, microsecond=0) - ist)
    rows = await db.prospect_runs.aggregate([
        {"$match": {"kind": "discover", "at": {"$gte": start.strftime("%Y-%m-%d %H:%M:%S")}}},
        {"$group": {"_id": None, "n": {"$sum": "$added"}}}]).to_list(1)
    return rows[0]["n"] if rows else 0


async def _log_run(kind, model, ok, detail, secs, **extra):
    await db.prospect_runs.insert_one({"at": now_utc_str(), "kind": kind, "model": model, "ok": ok,
                                       "detail": str(detail)[:300], "secs": round(secs, 1), **extra})


# ---------------- leads ----------------
async def _lead_fields(p: dict) -> dict:
    return {k: v for k, v in {
        "company_name": p["brand_name"], "contact_name": p.get("ceo_name") or p["brand_name"],
        "prospect_id": p["id"], "prospect_category": p["category"], "prospect_segment": p.get("segment"),
        "store_count": p.get("store_count"), "ceo_name": p.get("ceo_name"), "ceo_linkedin": p.get("ceo_linkedin"),
        "company_website": p.get("website"), "company_linkedin": p.get("company_linkedin"),
        "email_from": p.get("contact_email"), "phone": p.get("contact_phone"), "city": p.get("hq_city"),
        "country": p["country"], "designation": "CEO" if p.get("ceo_name") else None,
    }.items() if v not in (None, "")}


async def upsert_lead(p: dict, user=None):
    fields = await _lead_fields(p)
    if fields.get("phone"):
        fields["phone_digits"] = re.sub(r"\D", "", fields["phone"])[-10:]
    if p.get("lead_id"):
        await db.leads.update_one({"id": p["lead_id"]}, {"$set": {**fields, "write_date": now_utc_str(),
                                                                  **{k: v for k, v in search_norm(fields).items() if v}}})
        return p["lead_id"]
    lid = await next_id("lead")
    now = now_utc_str()
    doc = {"id": lid, "active": True, "stage_id": 1, "type": "lead", "priority": "0", "tags": [], "custom": {},
           "create_date": now, "create_date_ist": to_ist_str(now), "write_date": now, "source_lead": SOURCE,
           "phone_digits": "", "name": p["brand_name"], "query": p.get("description"), **fields}
    doc.update(ist_date_parts(doc["create_date_ist"]))
    doc.update(search_norm(doc))
    await db.leads.insert_one(doc)
    await db.prospects.update_one({"id": p["id"]}, {"$set": {"lead_id": lid}})
    await log_message(lid, f"Brand discovered by AI prospecting — {p['category']} · {p['country']}",
                      author=user or {"id": 0, "name": "AI Prospecting"})
    return lid


# ---------------- discovery ----------------
def _angle_text(country: str, idx: int) -> str:
    gcc = country not in MARKETPLACES
    return ANGLES[idx % len(ANGLES)].format(assoc=GCC_ASSOC if gcc else ASSOCIATIONS[country],
                                            market=GCC_MARKET if gcc else MARKETPLACES[country])


async def discover(country: str, category: str, settings: dict) -> dict:
    cat = await db.prospect_cats.find_one({"country": country, "category": category}) or {}
    idx = cat.get("runs", 0)
    known = [d["brand_name"] async for d in db.prospects.find(
        {"country": country, "category": category}, {"_id": 0, "brand_name": 1}).limit(400)]
    prompt = (f"Country: {country}. Category: {category}.\n"
              f"Find up to 25 consumer brands in this category in {country} that either run their own physical "
              f"stores/outlets (priority) or sell direct-to-consumer online to a large customer base. "
              f"Research angle for this round: {_angle_text(country, idx)}.\n"
              f"Do NOT repeat any of these already-listed brands: {', '.join(known[-300:]) or 'none'}.\n"
              'Return a JSON array of objects: {"brand": str, "parent_company": str|null, "segment": '
              '"Offline Retail"|"D2C / E-commerce"|"Omnichannel", "approx_stores": int|null, "website": str|null, '
              '"hq_city": str|null, "marketplaces": [str], "one_line": str, "source_url": str|null}. '
              "Return [] if you cannot find new ones.")
    t = time.time()
    try:
        items = _json(await _ask(FLASH, DISCOVER_SYS, prompt), "list") or []
    except AiCreditsOut as e:
        await _log_run("discover", FLASH, False, e, time.time() - t, country=country, category=category)
        raise
    except Exception as e:
        await _log_run("discover", FLASH, False, e, time.time() - t, country=country, category=category)
        await db.prospect_cats.update_one({"country": country, "category": category},
                                          {"$set": {"last_run": now_utc_str()}}, upsert=True)
        return {"added": 0}
    added = 0
    for it in items[:40]:
        name = (it.get("brand") or "").strip() if isinstance(it, dict) else ""
        key = brand_key(name)
        if not key or len(name) > 120:
            continue
        doc = {"brand_name": name, "brand_key": key, "country": country, "category": category,
               "parent_company": it.get("parent_company"), "segment": it.get("segment") if it.get("segment") in SEGMENTS else None,
               "store_count": _int(it.get("approx_stores")), "website": _url(it.get("website")),
               "hq_city": it.get("hq_city"), "marketplaces": [m for m in (it.get("marketplaces") or []) if isinstance(m, str)][:10],
               "description": (it.get("one_line") or "")[:400], "sources": [u for u in [_url(it.get("source_url"))] if u],
               "status": "discovered", "enrich_attempts": 0, "discovered_at": now_utc_str(),
               "discovered_via": _angle_text(country, idx)[:120]}
        res = await db.prospects.update_one({"country": country, "brand_key": key},
                                            {"$setOnInsert": {"id": await next_id("prospect"), **doc}}, upsert=True)
        if res.upserted_id:
            added += 1
            if settings.get("create_leads", True):
                p = await db.prospects.find_one({"_id": res.upserted_id}, {"_id": 0})
                await upsert_lead(p)
    streak = 0 if added >= 3 else cat.get("empty_streak", 0) + 1
    total = await db.prospects.count_documents({"country": country, "category": category})
    await db.prospect_cats.update_one({"country": country, "category": category}, {"$set": {
        "runs": idx + 1, "empty_streak": streak, "brands": total, "last_run": now_utc_str(),
        "saturated": streak >= 3 and idx + 1 >= len(ANGLES)}}, upsert=True)
    await _log_run("discover", FLASH, True, f"+{added} new ({len(items)} returned)", time.time() - t,
                   country=country, category=category, added=added)
    return {"added": added}


# ---------------- enrichment ----------------
async def enrich(p: dict) -> dict:
    prompt = (f"Deep-research this brand: \"{p['brand_name']}\" ({p['category']}, {p['country']})"
              f"{' — parent: ' + p['parent_company'] if p.get('parent_company') else ''}"
              f"{' — website: ' + p['website'] if p.get('website') else ''}.\n"
              "Return JSON: {\"official_name\": str|null, \"website\": str|null, \"segment\": \"Offline Retail\"|"
              "\"D2C / E-commerce\"|\"Omnichannel\", \"store_count\": int|null (own + franchise physical stores in "
              f"{p['country']}), \"store_count_note\": str|null, \"cities_present\": int|null, \"hq_city\": str|null, "
              "\"founded_year\": int|null, \"annual_revenue\": str|null, \"employees\": str|null, \"ceo_name\": str|null, "
              "\"ceo_title\": str|null, \"ceo_linkedin\": str|null (linkedin.com/in/ URL), \"founders\": [str], "
              "\"company_linkedin\": str|null, \"contact_email\": str|null (official business/corporate email), "
              "\"contact_phone\": str|null, \"has_loyalty_program\": str|null (name of existing loyalty/rewards program "
              "or 'none found'), \"marketplaces\": [str], \"summary\": str (2 sentences), \"sources\": [str] (URLs used)}")
    t = time.time()
    await db.prospects.update_one({"id": p["id"]}, {"$inc": {"enrich_attempts": 1}})
    try:
        d = _json(await _ask(PRO, ENRICH_SYS, prompt), "obj")
        if not d:
            raise ValueError("No JSON in model reply")
    except AiCreditsOut as e:
        await db.prospects.update_one({"id": p["id"]}, {"$set": {"status": "discovered"}, "$inc": {"enrich_attempts": -1}})
        await _log_run("enrich", PRO, False, e, time.time() - t, brand=p["brand_name"])
        raise
    except Exception as e:
        status = "failed" if p.get("enrich_attempts", 0) + 1 >= 3 else "discovered"
        await db.prospects.update_one({"id": p["id"]}, {"$set": {"status": status, "last_error": str(e)[:200],
                                                                 **({"failed_at": now_utc_str()} if status == "failed" else {})}})
        await _log_run("enrich", PRO, False, e, time.time() - t, brand=p["brand_name"])
        return {"ok": False}
    li = d.get("ceo_linkedin") if isinstance(d.get("ceo_linkedin"), str) and "linkedin.com/" in d.get("ceo_linkedin", "") else None
    upd = {k: v for k, v in {
        "official_name": d.get("official_name"), "website": _url(d.get("website")) or p.get("website"),
        "segment": d.get("segment") if d.get("segment") in SEGMENTS else p.get("segment"),
        "store_count": _int(d.get("store_count")) if d.get("store_count") is not None else p.get("store_count"),
        "store_count_note": d.get("store_count_note"), "cities_present": _int(d.get("cities_present")),
        "hq_city": d.get("hq_city") or p.get("hq_city"), "founded_year": _int(d.get("founded_year")),
        "annual_revenue": d.get("annual_revenue"), "employees": d.get("employees"), "ceo_name": d.get("ceo_name"),
        "ceo_title": d.get("ceo_title"), "ceo_linkedin": _url(li), "founders": d.get("founders") or [],
        "company_linkedin": _url(d.get("company_linkedin")), "contact_email": d.get("contact_email") if "@" in str(d.get("contact_email") or "") else None,
        "contact_phone": d.get("contact_phone"), "has_loyalty_program": d.get("has_loyalty_program"),
        "marketplaces": d.get("marketplaces") or p.get("marketplaces") or [], "description": d.get("summary") or p.get("description"),
    }.items() if v not in (None, "", [])}
    upd["sources"] = list(dict.fromkeys((p.get("sources") or []) + [u for u in (d.get("sources") or []) if isinstance(u, str)]))[:15]
    upd.update({"status": "enriched", "enriched_at": now_utc_str()})
    from core.contacts import fit_score
    upd["fit_score"] = fit_score({**p, **upd})
    await db.prospects.update_one({"id": p["id"]}, {"$set": upd, "$unset": {"last_error": 1}})
    if (await get_settings()).get("create_leads", True):
        await upsert_lead({**p, **upd})
    await _log_run("enrich", PRO, True, f"stores={upd.get('store_count')} ceo={upd.get('ceo_name')}", time.time() - t,
                   brand=p["brand_name"])
    return {"ok": True}


# ---------------- worker ----------------
_busy_cats: set = set()


async def _next_discovery(settings):
    stale = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    best = None
    for country in settings["countries"]:
        states = {d["category"]: d async for d in db.prospect_cats.find({"country": country}, {"_id": 0})}
        for cat in CATEGORIES:
            st = states.get(cat, {})
            if (country, cat) in _busy_cats or (st.get("saturated") and (st.get("last_run") or "") > stale):
                continue
            score = (st.get("runs", 0), st.get("last_run") or "")
            if best is None or score < best[0]:
                best = (score, country, cat)
    return best[1:] if best else None


async def _next_enrich():
    return await db.prospects.find_one_and_update(
        {"status": "discovered", "enrich_attempts": {"$lt": 3}},
        {"$set": {"status": "enriching", "enrich_started_at": now_utc_str()}},
        sort=[("store_count", -1), ("id", 1)], projection={"_id": 0})


async def _lane(deadline, settings, stats):
    while time.time() < deadline:
        try:
            if not await _step(settings, stats):
                return
        except AiCreditsOut:
            return
        except Exception:
            logger.exception("prospecting lane step failed")
            await asyncio.sleep(5)


async def _step(settings, stats) -> bool:
    """One unit of work; returns False when the lane should stop."""
    if not (await get_settings())["enabled"]:
        return False
    disc = None
    if settings["phase"] in ("auto", "discovery") and (settings["phase"] == "discovery" or random.random() < 0.7):
        if await under_cap(settings):
            disc = await _next_discovery(settings)
    if disc:
        _busy_cats.add(disc)
        try:
            stats["added"] += (await discover(disc[0], disc[1], settings))["added"]
            stats["discover_runs"] += 1
        finally:
            _busy_cats.discard(disc)
        return True
    if settings["phase"] == "discovery":
        return False
    if random.random() < 0.5:
        from core import contacts as ct
        cj = await ct.next_contacts_job()
        if cj:
            stats["contacts"] = stats.get("contacts", 0) + (1 if (await ct.find_contacts(cj))["ok"] else 0)
            return True
    p = await _next_enrich()
    if not p:
        return (settings["phase"] == "auto" and await under_cap(settings)
                and bool(await _next_discovery(settings)))
    stats["enriched"] += 1 if (await enrich(p))["ok"] else 0
    return True


async def requeue_failed():
    """Put failed brands/contact lookups back in the queue: credit errors at once, others after a 6h cooldown (max 5 rounds)."""
    quota = {"$regex": "spend limit|budget|insufficient balance|insufficient_quota", "$options": "i"}
    cool = (datetime.now(timezone.utc) - timedelta(hours=RETRY_COOLDOWN_H)).strftime("%Y-%m-%d %H:%M:%S")
    due = {"$or": [{"failed_at": None}, {"failed_at": {"$lt": cool}}]}
    await db.prospects.update_many({"status": "failed", "last_error": quota},
                                   {"$set": {"status": "discovered", "enrich_attempts": 0}, "$unset": {"last_error": 1}})
    await db.prospects.update_many({"status": "failed", "retry_rounds": {"$not": {"$gte": MAX_RETRY_ROUNDS}}, **due},
                                   {"$set": {"status": "discovered", "enrich_attempts": 0}, "$inc": {"retry_rounds": 1}})
    await db.prospects.update_many({"contacts_status": "failed", "contacts_error": quota},
                                   {"$unset": {"contacts_status": 1, "contacts_error": 1}})
    cdue = {"$or": [{"contacts_failed_at": None}, {"contacts_failed_at": {"$lt": cool}}]}
    await db.prospects.update_many({"contacts_status": "failed", "contacts_retry_rounds": {"$not": {"$gte": MAX_RETRY_ROUNDS}}, **cdue},
                                   {"$unset": {"contacts_status": 1}, "$inc": {"contacts_retry_rounds": 1}})


async def watchdog():
    """Hourly health check: free a stuck lock and start a batch if none finished in the last 30 min."""
    lock = await db.settings.find_one({"key": "prospect_lock"}) or {}
    now = datetime.now(timezone.utc)
    started = lock.get("started") or ""
    stuck = bool(lock.get("until")) and started < (now - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
    if stuck:
        await db.settings.update_one({"key": "prospect_lock"}, {"$set": {"until": ""}})
    idle = (lock.get("last_finished") or "") < (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    running = bool(lock.get("until")) and lock["until"] > now.isoformat() and not stuck
    await db.settings.update_one({"key": "prospect_lock"}, {"$set": {"watchdog_at": now_utc_str(),
                                 "watchdog_action": "restarted" if (idle and not running) else "ok"}})
    return {"stuck": stuck, "restart": idle and not running}


async def run_worker(max_seconds: int = 840, trigger: str = "cron"):
    now = datetime.now(timezone.utc)
    lock = await db.settings.find_one_and_update(
        {"key": "prospect_lock", "$or": [{"until": {"$lt": now.isoformat()}}, {"until": {"$exists": False}}]},
        {"$set": {"until": (now + timedelta(seconds=max_seconds + 120)).isoformat(), "started": now_utc_str(),
                  "trigger": trigger}}, upsert=False)
    if lock is None:
        if await db.settings.find_one({"key": "prospect_lock"}):
            return {"skipped": "already running"}
        await db.settings.insert_one({"key": "prospect_lock", "until": (now + timedelta(seconds=max_seconds + 120)).isoformat(),
                                      "started": now_utc_str(), "trigger": trigger})
    settings = await get_settings()
    stats = {"added": 0, "enriched": 0, "discover_runs": 0}
    try:
        if not settings["enabled"]:
            return {"skipped": "disabled"}
        stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        await db.prospects.update_many({"status": "enriching", "$or": [
            {"enrich_started_at": {"$lt": stale}}, {"enrich_started_at": {"$exists": False}}]},
            {"$set": {"status": "discovered"}})
        await db.prospects.update_many({"contacts_status": "running", "contacts_started_at": {"$lt": stale}},
                                       {"$unset": {"contacts_status": 1}})
        await requeue_failed()
        if not await db.settings.find_one({"key": "prospect_angles_v2"}):
            await db.prospect_cats.update_many({}, {"$set": {"saturated": False, "empty_streak": 0}})
            await db.settings.insert_one({"key": "prospect_angles_v2", "done": True})
        await ensure_catalog("source_lead", SOURCE)
        deadline = time.time() + max_seconds
        await asyncio.gather(*[_lane(deadline, settings, stats) for _ in range(max(1, min(settings["concurrency"], 6)))],
                             return_exceptions=True)
    except Exception:
        logger.exception("prospecting worker failed")
    finally:
        await db.settings.update_one({"key": "prospect_lock"}, {"$set": {"until": "", "last_finished": now_utc_str(),
                                                                         "last_stats": stats}})
        from routes import leads as leads_routes
        leads_routes._count_cache.clear()
    return stats
