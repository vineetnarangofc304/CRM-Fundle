"""Active email provider (Gmail or Outlook) — every email in the CRM goes through here."""
from core.db import db
from core import gmail_send, outlook_send

PROVIDERS = {"gmail": gmail_send, "outlook": outlook_send}


async def active_provider() -> str:
    doc = await db.settings.find_one({"key": "email_provider"}, {"_id": 0}) or {}
    if doc.get("provider") in PROVIDERS:
        return doc["provider"]
    return "gmail" if await gmail_send.is_connected() else "outlook"


async def is_connected() -> bool:
    return await PROVIDERS[await active_provider()].is_connected()


async def send_email(to: str, subject: str, body: str, html: bool = False, cc: str | None = None) -> dict:
    return await PROVIDERS[await active_provider()].send_email(to, subject, body, html=html, cc=cc)
