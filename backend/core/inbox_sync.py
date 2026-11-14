"""Read replies + bounces from outreach inboxes via Microsoft Graph (delegated Mail.Read, one sign-in per inbox)."""
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from core.db import db
from core import outlook_send as ol
from core.utils import log_message, now_utc_str

logger = logging.getLogger(__name__)
READ_SCOPES = "openid profile offline_access User.Read Mail.Read"
GRAPH = "https://graph.microsoft.com/v1.0"
BOUNCE_FROM = re.compile(r"mailer-daemon|postmaster|microsoftexchange|mail delivery", re.I)
BOUNCE_SUBJ = re.compile(r"undeliverable|delivery (has )?failed|delivery status notification|returned mail|failure notice|not delivered", re.I)
FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "yahoo.co.in",
             "icloud.com", "rediffmail.com", "aol.com", "proton.me", "protonmail.com", "zoho.com"}
AUTO_REPLY = re.compile(r"^(automatic reply|auto(matic)?[- ]?reply|out of (the )?office|ooo\b|away from)", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def auth_url(cfg: dict, redirect: str, state: str, login_hint: str = "") -> str:
    return f"{ol._authority(cfg)}/authorize?" + urlencode({
        "client_id": cfg["client_id"], "response_type": "code", "redirect_uri": redirect, "response_mode": "query",
        "scope": READ_SCOPES, "state": state, "prompt": "select_account", **({"login_hint": login_hint} if login_hint else {})})


async def _token(cfg: dict, **data) -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{ol._authority(cfg)}/token", data={
            "client_id": cfg["client_id"], "client_secret": ol.dec(cfg.get("client_secret_enc")), "scope": READ_SCOPES, **data})
    js = r.json()
    if r.status_code != 200:
        raise RuntimeError(js.get("error_description") or js.get("error") or f"Token error {r.status_code}")
    return js


async def connect(code: str, redirect: str, mailbox_id: int) -> str:
    cfg = await ol.get_config()
    tok = await _token(cfg, grant_type="authorization_code", code=code, redirect_uri=redirect)
    async with httpx.AsyncClient(timeout=20) as c:
        me = (await c.get(f"{GRAPH}/me", headers={"Authorization": f"Bearer {tok['access_token']}"})).json()
    email = (me.get("mail") or me.get("userPrincipalName") or "").lower()
    await db.inbox_readers.update_one({"mailbox_id": mailbox_id}, {"$set": {
        "email": email, "refresh_token_enc": ol.enc(tok["refresh_token"]), "connected_at": now_utc_str(),
        "last_checked": (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "$unset": {"error": 1}}, upsert=True)
    return email


def _text(m: dict) -> str:
    body = (m.get("body") or {}).get("content") or m.get("bodyPreview") or ""
    return re.sub(r"<[^>]+>", " ", body)


async def _bounce(m: dict, now: str) -> int:
    n = 0
    for addr in {e.lower() for e in EMAIL_RE.findall(_text(m) + " " + (m.get("subject") or ""))}:
        o = await db.outreach.find_one({"channel": "email", "status": "sent", "to": {"$regex": f"^{re.escape(addr)}$", "$options": "i"},
                                        "bounced_at": {"$exists": False}}, sort=[("sent_at", -1)])
        if not o:
            continue
        n += 1
        await db.outreach.update_one({"id": o["id"]}, {"$set": {"status": "bounced", "bounced_at": now}})
        pid = o.get("prospect_id")
        if o.get("probe"):  # bounce-test address: the verifier decides what to try next
            if pid:
                await db.prospects.update_one({"id": pid}, {"$addToSet": {"bounced_emails": addr}})
            continue
        if pid:
            await db.prospects.update_one({"id": pid}, {
                "$addToSet": {"bounced_emails": addr}, "$pull": {"tpl_sent": o.get("template_id")},
                "$unset": {"contacts_status": 1, "contacts_retry_rounds": 1}})
        if o.get("lead_id"):
            await log_message(o["lead_id"], f"⚠️ Email to {addr} bounced (undelivered). Finding a new contact to resend.",
                              author={"id": 0, "name": "Outreach Agent"})
    return n


async def _reply(m: dict, now: str) -> int:
    frm = (((m.get("from") or {}).get("emailAddress") or {}).get("address") or "").lower()
    if not frm or "@" not in frm or AUTO_REPLY.search((m.get("subject") or "").strip()):
        return 0
    o = await db.outreach.find_one({"channel": "email", "status": {"$in": ["sent", "bounced"]},
                                    "to": {"$regex": f"^{re.escape(frm)}$", "$options": "i"}}, sort=[("sent_at", -1)])
    dom = frm.split("@")[1]
    if not o and dom not in FREE_MAIL:
        o = await db.outreach.find_one({"channel": "email", "status": "sent", "to": {"$regex": f"@{re.escape(dom)}$", "$options": "i"}},
                                       sort=[("sent_at", -1)])
    if not o or o.get("replied_at"):
        return 0
    pid = o.get("prospect_id")
    await db.outreach.update_one({"id": o["id"]}, {"$set": {"replied_at": now, "replied_from": frm}})
    await db.outreach.update_many({"prospect_id": pid, "status": {"$in": ["approved", "pending_approval"]}},
                                  {"$set": {"status": "stopped", "stop_reason": f"Brand replied ({frm})"}})
    await db.prospects.update_one({"id": pid}, {"$set": {"replied": True, "replied_at": now, "outreach_status": "replied"}})
    if o.get("lead_id"):
        await db.leads.update_one({"id": o["lead_id"]}, {"$set": {"outreach_replied": True, "outreach_replied_at": now}})
        await log_message(o["lead_id"], f"💬 <b>Reply received</b> from {frm}: “{(m.get('subject') or '')[:120]}” — auto emails to this brand stopped.",
                          author={"id": 0, "name": "Outreach Agent"})
    return 1


async def sync_all() -> dict:
    """Check every connected inbox for new replies and bounces since the last check."""
    cfg = await ol.get_config()
    stats = {"replies": 0, "bounces": 0, "inboxes": 0}
    if not cfg.get("client_id"):
        return stats
    async for r in db.inbox_readers.find({"refresh_token_enc": {"$exists": True}}):
        try:
            tok = await _token(cfg, grant_type="refresh_token", refresh_token=ol.dec(r["refresh_token_enc"]))
            start = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            url = (f"{GRAPH}/me/mailFolders/inbox/messages?$top=50&$orderby=receivedDateTime asc"
                   f"&$filter=receivedDateTime ge {r['last_checked']}&$select=from,subject,body,bodyPreview,receivedDateTime")
            now = now_utc_str()
            async with httpx.AsyncClient(timeout=30) as c:
                for _ in range(10):
                    res = await c.get(url, headers={"Authorization": f"Bearer {tok['access_token']}"})
                    if res.status_code != 200:
                        raise RuntimeError(f"Graph {res.status_code}: {res.text[:150]}")
                    js = res.json()
                    for m in js.get("value", []):
                        frm = (((m.get("from") or {}).get("emailAddress") or {}).get("address") or "")
                        if BOUNCE_FROM.search(frm) or BOUNCE_SUBJ.search(m.get("subject") or ""):
                            stats["bounces"] += await _bounce(m, now)
                        else:
                            stats["replies"] += await _reply(m, now)
                    url = js.get("@odata.nextLink")
                    if not url:
                        break
            upd = {"last_checked": start, "last_sync": now}
            if tok.get("refresh_token"):
                upd["refresh_token_enc"] = ol.enc(tok["refresh_token"])
            await db.inbox_readers.update_one({"_id": r["_id"]}, {"$set": upd, "$unset": {"error": 1}})
            stats["inboxes"] += 1
        except Exception as e:
            logger.warning(f"inbox sync failed for {r.get('email')}: {e}")
            await db.inbox_readers.update_one({"_id": r["_id"]}, {"$set": {"error": str(e)[:200]}})
    return stats
