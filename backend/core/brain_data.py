"""Fundle Brain data tools: uploaded spreadsheets (brands / contact emails) and the contact-search source list."""
import io
import re

from core.db import db
from core.utils import log_message, next_id, now_utc_str

DEFAULT_SOURCES = [
    {"name": "Indian Retailer", "url": "https://www.indianretailer.com/news", "notes": "LinkedIn: linkedin.com/company/indianretailer/posts"},
    {"name": "Retailers Association of India (RAI)", "url": "https://rai.net.in/STOrai.php", "notes": "LinkedIn: linkedin.com/company/retailers-association/posts"},
    {"name": "D2C Insider", "url": "https://digest.d2cinsider.com/archive", "notes": "LinkedIn: linkedin.com/company/d2c-insider/posts"},
    {"name": "Franchise India", "url": "https://www.franchiseindia.com/insights/en/franchise", "notes": "LinkedIn: linkedin.com/company/franchiseindia/posts"},
    {"name": "ETRetail", "url": "https://retail.economictimes.indiatimes.com/", "notes": "LinkedIn: linkedin.com/showcase/et-retail"},
]
MAX_ROWS = 20000
AI = {"id": 0, "name": "Fundle Brain"}


async def get_sources() -> list:
    doc = await db.settings.find_one({"key": "contact_sources"}, {"_id": 0})
    return doc["items"] if doc else DEFAULT_SOURCES


def sources_prompt(items: list) -> str:
    if not items:
        return ""
    lines = "; ".join(f"{s['name']} ({s['url']}{' — ' + s['notes'] if s.get('notes') else ''})" for s in items)
    return ("Also search these sources for named executives at this brand (interviews, appointments, quotes, team pages, "
            f"press contacts): {lines}.\n")


def parse_file(name: str, data: bytes) -> list[dict]:
    import pandas as pd
    n = name.lower()
    if n.endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(data), dtype=str)
    elif n.endswith((".csv", ".txt")):
        df = pd.read_csv(io.BytesIO(data), dtype=str, encoding_errors="ignore", sep=None, engine="python")
    else:
        raise ValueError("Upload an Excel (.xlsx) or CSV file")
    df = df.dropna(how="all").fillna("")
    df.columns = [str(c).strip() for c in df.columns]
    return [{k: str(v).strip() for k, v in r.items()} for r in df.head(MAX_ROWS).to_dict("records")]


async def save_upload(session_id: int, user: dict, name: str, ctype: str, data: bytes) -> dict:
    from core.storage import put_object
    rows = parse_file(name, data)
    fid = await next_id("brain_file")
    path = f"fundle-crm/brain/{session_id}/{fid}-{re.sub(r'[^A-Za-z0-9._-]+', '_', name)[-80:]}"
    await put_object(path, data, ctype or "application/octet-stream")
    doc = {"id": fid, "session_id": session_id, "filename": name, "path": path, "rows": len(rows),
           "columns": list(rows[0].keys()) if rows else [], "by": user.get("name"), "at": now_utc_str()}
    await db.brain_files.insert_one(dict(doc))
    if rows:
        await db.brain_file_rows.insert_many([{"file_id": fid, "i": i, "row": r} for i, r in enumerate(rows)])
    return doc


async def _rows(fid: int, offset=0, limit=MAX_ROWS):
    f = await db.brain_files.find_one({"id": int(fid)}, {"_id": 0})
    if not f:
        raise ValueError(f"File #{fid} not found")
    rows = [x["row"] async for x in db.brain_file_rows.find({"file_id": f["id"], "i": {"$gte": offset}}).sort("i", 1).limit(limit)]
    return f, rows


def _get(row, mapping, key, default=None):
    col = (mapping or {}).get(key)
    v = row.get(col, "") if col else ""
    return v.strip() if isinstance(v, str) and v.strip() else default


async def t_read_file(ctx, a):
    f, rows = await _rows(a["file_id"], int(a.get("offset") or 0), max(1, min(int(a.get("limit") or 15), 100)))
    return {"file": f, "rows": rows}


async def _new_prospect(name, country, category, website=None, store_count=None, city=None, segment=None, priority=1):
    from core import prospector as pe
    key = pe.brand_key(name)
    doc = {"brand_name": name, "brand_key": key, "country": country, "category": category, "website": pe._url(website),
           "store_count": pe._int(store_count), "hq_city": city, "segment": segment if segment in pe.SEGMENTS else None,
           "status": "discovered", "enrich_attempts": 0, "discovered_at": now_utc_str(), "discovered_via": "Uploaded via Fundle Brain",
           "contacts_priority": priority}
    res = await db.prospects.update_one({"country": country, "brand_key": key},
                                        {"$setOnInsert": {"id": await next_id("prospect"), **doc}}, upsert=True)
    p = await db.prospects.find_one({"country": country, "brand_key": key}, {"_id": 0})
    if res.upserted_id:
        await pe.upsert_lead(p)
        p = await db.prospects.find_one({"id": p["id"]}, {"_id": 0})
    return p, bool(res.upserted_id)


async def t_import_brands(ctx, a):
    m, d = a.get("mapping") or {}, a.get("defaults") or {}
    if not m.get("brand_name"):
        raise ValueError("mapping.brand_name (the column with brand names) is required")
    _, rows = await _rows(a["file_id"])
    added, existing = 0, 0
    for r in rows:
        name = _get(r, m, "brand_name")
        if not name or len(name) > 120:
            continue
        _, new = await _new_prospect(name, _get(r, m, "country", d.get("country") or "India"), _get(r, m, "category", d.get("category") or "Uploaded"),
                                     _get(r, m, "website"), _get(r, m, "store_count"), _get(r, m, "city"), _get(r, m, "segment", d.get("segment")))
        added, existing = added + new, existing + (not new)
    return {"added_brands": added, "already_in_crm": existing, "rows": len(rows),
            "next": "New brands are queued first: AI researches them, finds contacts, then the matching intro email goes out automatically."}


def _blank(v) -> bool:
    return v in (None, "", 0, "Uploaded", "Unknown", "unknown")


async def t_update_brands(ctx, a):
    """Fill missing store_count / category / city / website / segment on brands from a file; add unknown brands."""
    from core import prospector as pe
    m, d = a.get("mapping") or {}, a.get("defaults") or {}
    if not m.get("brand_name"):
        raise ValueError("mapping.brand_name (the column with brand names) is required")
    _, rows = await _rows(a["file_id"])
    st = {"rows": len(rows), "brands_updated": 0, "fields_filled": 0, "already_had_values": 0, "added_brands": 0, "skipped": 0}
    for r in rows:
        name = _get(r, m, "brand_name")
        if not name or len(name) > 120:
            st["skipped"] += 1
            continue
        vals = {"store_count": pe._int(_get(r, m, "store_count")), "category": _get(r, m, "category"), "hq_city": _get(r, m, "city"),
                "website": pe._url(_get(r, m, "website")) if _get(r, m, "website") else None, "segment": _get(r, m, "segment")}
        if vals["segment"] not in pe.SEGMENTS:
            vals["segment"] = None
        p = await db.prospects.find_one({"brand_key": pe.brand_key(name)}, {"_id": 0})
        if not p and vals["website"]:
            dom = vals["website"].lower().replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
            p = dom and await db.prospects.find_one({"website": {"$regex": re.escape(dom), "$options": "i"}}, {"_id": 0})
        if not p:
            await _new_prospect(name, _get(r, m, "country", d.get("country") or "India"), vals["category"] or d.get("category") or "Uploaded",
                                vals["website"], vals["store_count"], vals["hq_city"], vals["segment"])
            st["added_brands"] += 1
            continue
        upd = {k: v for k, v in vals.items() if v not in (None, "") and _blank(p.get(k))}
        st["already_had_values"] += sum(1 for k, v in vals.items() if v not in (None, "") and not _blank(p.get(k)))
        if upd:
            await db.prospects.update_one({"id": p["id"]}, {"$set": upd})
            if p.get("lead_id") and ("store_count" in upd or "category" in upd):
                await db.leads.update_one({"id": p["lead_id"]}, {"$set": {k: upd[k] for k in ("store_count", "category") if k in upd}})
            st["brands_updated"] += 1
            st["fields_filled"] += len(upd)
    st["note"] = "Only blank values were filled; existing store counts/categories were kept. New brands go into research → contacts → intro email."
    return st


async def t_import_contacts(ctx, a):
    from core import contacts as ct, prospector as pe
    m, d = a.get("mapping") or {}, a.get("defaults") or {}
    if not m.get("email") or not (m.get("brand_name") or m.get("website")):
        raise ValueError("mapping needs email plus brand_name (or website)")
    _, rows = await _rows(a["file_id"])
    stats = {"updated_contacts": 0, "added_contacts": 0, "brands_created": 0, "brands_touched": 0, "skipped": []}
    touched = {}
    for r in rows:
        email = (_get(r, m, "email") or "").lower()
        name = _get(r, m, "brand_name")
        site = _get(r, m, "website")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email):
            stats["skipped"].append(f"{name or site}: bad email '{email}'")
            continue
        p = None
        if name:
            p = await db.prospects.find_one({"brand_key": pe.brand_key(name)}, {"_id": 0})
        if not p:
            dom = (site or email.split("@")[1]).lower().replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
            p = await db.prospects.find_one({"website": {"$regex": re.escape(dom), "$options": "i"}}, {"_id": 0})
        if not p:
            if not name:
                stats["skipped"].append(f"{email}: no brand name and no matching website")
                continue
            p, _ = await _new_prospect(name, _get(r, m, "country", d.get("country") or "India"),
                                       _get(r, m, "category", d.get("category") or "Uploaded"), site or f"https://{email.split('@')[1]}", priority=0)
            stats["brands_created"] += 1
        p = touched.get(p["id"]) or p
        if email in {e.lower() for e in p.get("bounced_emails") or []}:
            stats["skipped"].append(f"{email}: bounced before")
            continue
        cname = _get(r, m, "contact_name") or email.split("@")[0].replace(".", " ").title()
        cs = p.get("contacts") or []
        c = next((x for x in cs if (x.get("email") or "").lower() == email or x["name"].strip().lower() == cname.lower()), None)
        new = {"name": cname, "role": _get(r, m, "role") or (c or {}).get("role"), "email": email, "email_status": "published",
               "uploaded": True, "linkedin": _get(r, m, "linkedin") or (c or {}).get("linkedin"), "evidence_url": "Uploaded list"}
        if c:
            c.update(new)
            stats["updated_contacts"] += 1
        else:
            cs.insert(0, new)
            stats["added_contacts"] += 1
        p["contacts"] = cs
        touched[p["id"]] = p
    for p in touched.values():
        upd = {"contacts": p["contacts"], "contacts_status": "done", "contacts_found_at": now_utc_str(), "contacts_priority": 0}
        upd["fit_score"] = ct.fit_score({**p, **upd})
        await db.prospects.update_one({"id": p["id"]}, {"$set": upd})
        if p.get("lead_id"):
            best = next(c for c in p["contacts"] if c.get("uploaded"))
            await db.leads.update_one({"id": p["lead_id"]}, {"$set": {"email_from": best["email"], "email_lc": best["email"], "fit_score": upd["fit_score"]}})
            await log_message(p["lead_id"], "📥 Verified emails uploaded via Fundle Brain: " + ", ".join(
                f"{c['name']} &lt;{c['email']}&gt;" for c in p["contacts"] if c.get("uploaded")), author=AI)
    stats["brands_touched"] = len(touched)
    stats["skipped"] = stats["skipped"][:50]
    return stats


async def t_sources(ctx, a):
    items = await get_sources()
    act = a.get("action") or "list"
    if act == "add":
        if not a.get("name") or not a.get("url"):
            raise ValueError("name and url are required")
        items = [s for s in items if s["url"] != a["url"]] + [{"name": a["name"], "url": a["url"], "notes": a.get("notes") or ""}]
    elif act == "remove":
        items = [s for s in items if s["url"] != a.get("url") and s["name"].lower() != (a.get("name") or "").lower()]
    elif act != "list":
        raise ValueError("action must be list, add or remove")
    if act != "list":
        await db.settings.update_one({"key": "contact_sources"}, {"$set": {"items": items, "updated_at": now_utc_str()}}, upsert=True)
    return {"sources": items, "note": "Used by every AI contact search from the next run."}
