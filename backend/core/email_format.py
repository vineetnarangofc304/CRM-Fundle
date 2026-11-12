"""Email format checker: Google-search each brand's real published emails, learn the format, rebuild guessed emails."""
import asyncio
import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

from core.db import db
from core.utils import log_message, now_utc_str

logger = logging.getLogger(__name__)
PER_RUN, LANES, RECHECK_DAYS = 30, 6, 30
FORMATS = {
    "first.last": lambda f, l: f"{f}.{l}", "firstlast": lambda f, l: f"{f}{l}", "first_last": lambda f, l: f"{f}_{l}",
    "first-last": lambda f, l: f"{f}-{l}", "flast": lambda f, l: f"{f[0]}{l}", "f.last": lambda f, l: f"{f[0]}.{l}",
    "first": lambda f, l: f, "firstl": lambda f, l: f"{f}{l[0]}", "first.l": lambda f, l: f"{f}.{l[0]}",
    "last.first": lambda f, l: f"{l}.{f}", "lastfirst": lambda f, l: f"{l}{f}", "lastf": lambda f, l: f"{l}{f[0]}",
    "last": lambda f, l: l,
}
FORMAT_SYS = ("You are an email-format researcher. Run several Google searches for REAL published email addresses of "
              "named employees at the given domain: \"@<domain>\", \"<domain>\" email, press release media contacts, "
              "investor relations, careers/HR pages, annual-report PDFs, conference speaker pages, and email-format sites "
              "(RocketReach, Hunter, SignalHire, ContactOut, LeadIQ 'email format' pages). Ignore generic inboxes "
              "(info@, care@, sales@, hr@). Never invent addresses. Reply with JSON only.")
_lock = {"on": False}


def _parts(name: str):
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    toks = [t for t in re.split(r"[^a-z]+", n) if t and t not in ("dr", "mr", "mrs", "ms", "shri", "smt", "prof", "ca", "er")]
    return (toks[0], toks[-1]) if len(toks) >= 2 else ((toks[0], "") if toks else (None, None))


def build(fmt: str, name: str, domain: str):
    f, l = _parts(name)
    if not f or fmt not in FORMATS or (not l and fmt not in ("first",)):
        return None
    return f"{FORMATS[fmt](f, l)}@{domain}"


async def check_brand(p: dict) -> dict:
    from core import contacts as ct
    from core import prospector as pe
    dom = ct._domain(p)
    upd = {"fmt_checked_at": now_utc_str()}
    if not dom:
        await db.prospects.update_one({"id": p["id"]}, {"$set": {**upd, "email_format": {"pattern": None, "note": "no website domain", "checked_at": now_utc_str()}},
                                                     "$unset": {"fmt_running": 1}})
        return {"ok": False}
    prompt = (f"Brand: \"{p['brand_name']}\" ({p.get('category')}, {p.get('country')}). Email domain: {dom}.\n"
              f"Known people: {[c['name'] for c in p.get('contacts') or []][:6]}\n"
              "Find real published emails of named people at this domain and work out the format. Allowed formats: "
              f"{list(FORMATS)}. If you find any of the known people's real published email, include it.\n"
              'JSON: {"pattern": str|null, "examples": [{"name": str, "email": str, "source_url": str|null}], '
              '"published": [{"name": str, "email": str}]}')
    t = time.time()
    try:
        d = pe._json(await pe._ask(pe.PRO, FORMAT_SYS, prompt), "obj") or {}
    except pe.AiCreditsOut:
        await db.prospects.update_one({"id": p["id"]}, {"$set": {"email_format.error": "AI credits ran out — will retry", "email_format.checked_at": now_utc_str()},
                                                        "$unset": {"fmt_running": 1}})
        raise
    except Exception as e:
        await db.prospects.update_one({"id": p["id"]}, {"$set": upd, "$unset": {"fmt_running": 1}})
        await pe._log_run("email_format", pe.PRO, False, e, time.time() - t, brand=p["brand_name"])
        return {"ok": False}
    ok_mail = re.compile(rf"^[a-z0-9._%+'-]+@{re.escape(dom)}$")
    examples = [x for x in d.get("examples") or [] if isinstance(x, dict) and ok_mail.match(str(x.get("email", "")).lower())]
    examples += [{"name": c["name"], "email": c["email"], "source_url": c.get("evidence_url")} for c in p.get("contacts") or []
                 if c.get("email_status") == "published" and ok_mail.match((c.get("email") or "").lower())
                 and c["email"].lower() not in {e.lower() for e in p.get("bounced_emails") or []}]
    fmt = d.get("pattern") if d.get("pattern") in FORMATS else None
    if not fmt or not any(build(fmt, x.get("name", ""), dom) == str(x["email"]).lower() for x in examples):
        fmt = next((k for k in FORMATS for x in examples if build(k, x.get("name", ""), dom) == str(x["email"]).lower()), fmt)
    # Only trust a format that matches at least one real example.
    confirmed = bool(fmt) and any(build(fmt, x.get("name", ""), dom) == str(x["email"]).lower() for x in examples)
    published = {str(x.get("name", "")).strip().lower(): str(x.get("email", "")).lower()
                 for x in d.get("published") or [] if isinstance(x, dict) and ok_mail.match(str(x.get("email", "")).lower())}
    bounced = {e.lower() for e in p.get("bounced_emails") or []}
    cs, changed = p.get("contacts") or [], []
    for c in cs:
        pub = published.get(c["name"].strip().lower())
        if pub and pub not in bounced:
            if c.get("email") != pub or c.get("email_status") != "published":
                changed.append(f"{c['name']}: {pub} (published)")
            c.update(email=pub, email_status="published")
            continue
        if c.get("email_status") == "published" and (c.get("email") or "").lower() not in bounced:
            continue
        new = build(fmt, c["name"], dom) if confirmed else None
        if new and new not in bounced:
            if new != (c.get("email") or "").lower():
                changed.append(f"{c['name']}: {new}")
            c.update(email=new, email_status="confirmed_format")
        elif (c.get("email") or "").lower() in bounced:
            c.update(email=None, email_status=None)
            changed.append(f"{c['name']}: bounced, no other address")
    upd.update(email_format={"pattern": fmt, "confirmed": confirmed, "domain": dom, "checked_at": now_utc_str(),
                             "examples": [{"name": x.get("name"), "email": str(x["email"]).lower(),
                                           "source_url": pe._url(x.get("source_url"))} for x in examples[:5]]},
               contacts=cs)
    if confirmed:
        upd["email_pattern"] = fmt.replace("first", "firstname").replace("last", "lastname") + f"@{dom}"
    upd["fit_score"] = ct.fit_score({**p, **upd})
    unset = {"fmt_running": 1}
    if not any(c.get("email") for c in cs) and not cs and p.get("contacts_status") in ("done", "failed"):
        unset["contacts_status"] = 1
        upd["contacts_priority"] = 1
    await db.prospects.update_one({"id": p["id"]}, {"$set": upd, "$unset": unset})
    if p.get("lead_id"):
        await db.leads.update_one({"id": p["lead_id"]}, {"$set": {"fit_score": upd["fit_score"]}})
        best = next((c for c in cs if c.get("email") and c["email_status"] == "published"), None) or \
            next((c for c in cs if c.get("email")), None)
        lead = await db.leads.find_one({"id": p["lead_id"]}, {"_id": 0, "email_from": 1})
        if best and lead is not None and (not lead.get("email_from") or lead["email_from"].lower() in bounced or changed):
            await db.leads.update_one({"id": p["lead_id"]}, {"$set": {"email_from": best["email"], "email_lc": best["email"]}})
        what = (f"format <b>{fmt}@{dom}</b> confirmed from {len(examples)} published example(s)" if confirmed
                else f"no published examples found for @{dom}")
        await log_message(p["lead_id"], f"📮 AI email format check: {what}" + (f". Updated: {'; '.join(changed)}" if changed else ""),
                          author={"id": 0, "name": "AI Prospecting"})
    await pe._log_run("email_format", pe.PRO, True, f"{fmt or 'unknown'}{' confirmed' if confirmed else ''}, {len(changed)} updated",
                      time.time() - t, brand=p["brand_name"])
    return {"ok": True, "confirmed": confirmed, "changed": len(changed)}


async def _next():
    old = (datetime.now(timezone.utc) - timedelta(days=RECHECK_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    due = {"$or": [{"fmt_checked_at": {"$exists": False}}, {"fmt_checked_at": {"$lt": old}}], "fmt_running": {"$exists": False}}
    for f in ({"bounced_emails.0": {"$exists": True}}, {"contacts.email_status": "pattern"},
              {"contacts.0": {"$exists": True}, "contacts.email": {"$in": [None, ""]}}, {"contacts_status": "done", "contacts.0": {"$exists": False}}):
        p = await db.prospects.find_one_and_update({**f, **due}, {"$set": {"fmt_running": now_utc_str()}},
                                                   sort=[("fit_score", -1), ("store_count", -1)], projection={"_id": 0})
        if p:
            return p
    return None


async def run(trigger="cron") -> dict:
    from core import prospector as pe
    if _lock["on"]:
        return {"skipped": "already running"}
    _lock["on"] = True
    stats = {"checked": 0, "confirmed": 0, "updated": 0}
    try:
        stale = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        await db.prospects.update_many({"fmt_running": {"$lt": stale}}, {"$unset": {"fmt_running": 1}})
        left = {"n": PER_RUN, "stop": False}

        async def lane():
            while left["n"] > 0 and not left["stop"]:
                left["n"] -= 1
                p = await _next()
                if not p:
                    return
                try:
                    r = await check_brand(p)
                except pe.AiCreditsOut:
                    left["stop"] = True
                    return
                except Exception as e:
                    logger.warning(f"format check {p.get('brand_name')}: {e}")
                    await db.prospects.update_one({"id": p["id"]}, {"$unset": {"fmt_running": 1}})
                    continue
                stats["checked"] += 1
                stats["confirmed"] += 1 if r.get("confirmed") else 0
                stats["updated"] += r.get("changed", 0)
        await asyncio.gather(*(lane() for _ in range(LANES)))
    finally:
        _lock["on"] = False
    await db.settings.update_one({"key": "email_format_last"}, {"$set": {"at": now_utc_str(), "stats": stats, "trigger": trigger}}, upsert=True)
    return stats
