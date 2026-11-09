"""Outlook / Microsoft 365 sending: Microsoft Graph OAuth (delegated Mail.Send) or SMTP (smtp.office365.com)."""
import asyncio
import base64
import hashlib
import os
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import urlencode

import aiosmtplib
import httpx
from cryptography.fernet import Fernet, InvalidToken

from core.db import db

SCOPES = "openid profile offline_access User.Read Mail.Send"
GRAPH = "https://graph.microsoft.com/v1.0"
SECRET_FIELDS = ("client_secret", "smtp_password", "refresh_token")


def _fernet() -> Fernet:
    key = hashlib.sha256(("outlook:" + os.environ["JWT_SECRET"]).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def enc(v: str | None):
    return _fernet().encrypt(v.encode()).decode() if v else None


def dec(v: str | None):
    if not v:
        return None
    try:
        return _fernet().decrypt(v.encode()).decode()
    except InvalidToken:
        return None


async def get_config() -> dict:
    return await db.settings.find_one({"key": "outlook"}, {"_id": 0}) or {}


def _authority(cfg) -> str:
    return f"https://login.microsoftonline.com/{cfg.get('tenant_id') or 'common'}/oauth2/v2.0"


async def is_connected() -> bool:
    cfg = await get_config()
    if cfg.get("method") == "smtp":
        return bool(cfg.get("smtp_user") and cfg.get("smtp_password_enc"))
    return bool(cfg.get("refresh_token_enc") and cfg.get("client_id"))


def authorize_url(cfg: dict, redirect: str, state: str) -> str:
    return f"{_authority(cfg)}/authorize?" + urlencode({
        "client_id": cfg["client_id"], "response_type": "code", "redirect_uri": redirect,
        "response_mode": "query", "scope": SCOPES, "state": state, "prompt": "select_account"})


async def _token(cfg: dict, **data) -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{_authority(cfg)}/token", data={
            "client_id": cfg["client_id"], "client_secret": dec(cfg.get("client_secret_enc")),
            "scope": SCOPES, **data})
    js = r.json()
    if r.status_code != 200:
        raise RuntimeError(js.get("error_description") or js.get("error") or f"Token error {r.status_code}")
    return js


async def exchange_code(cfg: dict, code: str, redirect: str) -> dict:
    tok = await _token(cfg, grant_type="authorization_code", code=code, redirect_uri=redirect)
    async with httpx.AsyncClient(timeout=20) as c:
        me = (await c.get(f"{GRAPH}/me", headers={"Authorization": f"Bearer {tok['access_token']}"})).json()
    return {"refresh_token": tok.get("refresh_token"),
            "email": me.get("mail") or me.get("userPrincipalName"), "display_name": me.get("displayName")}


async def _graph_send(cfg, to, subject, body, html, cc=None):
    try:
        tok = await _token(cfg, grant_type="refresh_token", refresh_token=dec(cfg.get("refresh_token_enc")))
    except RuntimeError as e:
        if "AADSTS" in str(e) or "invalid_grant" in str(e):
            await db.settings.update_one({"key": "outlook"}, {"$set": {"auth_error": str(e)[:300]}})
        raise
    if tok.get("refresh_token"):
        await db.settings.update_one({"key": "outlook"}, {"$set": {"refresh_token_enc": enc(tok["refresh_token"])},
                                                          "$unset": {"auth_error": 1}})
    payload = {"message": {"subject": subject or "(no subject)",
                           "body": {"contentType": "HTML" if html else "Text", "content": body or ""},
                           "toRecipients": [{"emailAddress": {"address": to}}],
                           **({"ccRecipients": [{"emailAddress": {"address": cc}}]} if cc else {})}, "saveToSentItems": True}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{GRAPH}/me/sendMail", json=payload,
                         headers={"Authorization": f"Bearer {tok['access_token']}"})
    if r.status_code != 202:
        try:
            msg = r.json().get("error", {}).get("message")
        except ValueError:
            msg = None
        raise RuntimeError(msg or f"Microsoft Graph error {r.status_code}")
    return {"ok": True}


async def _smtp_send(cfg, to, subject, body, html, cc=None):
    sender = cfg.get("from_email") or cfg["smtp_user"]
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.get("sender_name") or "Fundle.ai", sender))
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject or "(no subject)"
    if html:
        msg.set_content("This email contains HTML content.")
        msg.add_alternative(body or "", subtype="html")
    else:
        msg.set_content(body or "")
    port = int(cfg.get("smtp_port") or 587)
    try:
        await asyncio.wait_for(aiosmtplib.send(
            msg, hostname=cfg.get("smtp_host") or "smtp.office365.com", port=port,
            username=cfg["smtp_user"], password=dec(cfg.get("smtp_password_enc")),
            start_tls=port != 465, use_tls=port == 465, timeout=15), timeout=25)
    except asyncio.TimeoutError:
        raise RuntimeError(f"Could not reach {cfg.get('smtp_host') or 'smtp.office365.com'}:{port} in time — check server/port")
    return {"ok": True}


async def send_email(to: str, subject: str, body: str, html: bool = False, cc: str | None = None) -> dict:
    cfg = await get_config()
    try:
        if cfg.get("method") == "smtp":
            return await _smtp_send(cfg, to, subject, body, html, cc)
        return await _graph_send(cfg, to, subject, body, html, cc)
    except Exception as e:
        msg = str(e)
        if "SmtpClientAuthentication is disabled" in msg:
            msg = ("Microsoft 365 has SMTP sending turned off for your organisation. Use 'Sign in with Microsoft' "
                   "instead, or ask your M365 admin to enable Authenticated SMTP for this mailbox.")
        elif "535" in msg:
            msg = "Microsoft rejected the mailbox/password. Check them (use an app password if MFA is on)."
        return {"ok": False, "error": msg[:300]}
