"""Outlook / Microsoft 365 settings, OAuth connect/callback, test send + active email provider."""
import secrets
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from core.db import db
from core.security import require_roles
from core.utils import now_utc_str
from core import gmail_send, mailer, outlook_send as ol

router = APIRouter(tags=["outlook"])


class OutlookSettings(BaseModel):
    method: str = "graph"
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    smtp_host: Optional[str] = "smtp.office365.com"
    smtp_port: Optional[int] = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    from_email: Optional[str] = None
    sender_name: Optional[str] = None


def _public(cfg: dict) -> dict:
    out = {k: v for k, v in cfg.items() if not k.endswith("_enc") and k != "key"}
    out["has_client_secret"] = bool(cfg.get("client_secret_enc"))
    out["has_smtp_password"] = bool(cfg.get("smtp_password_enc"))
    out["oauth_connected"] = bool(cfg.get("refresh_token_enc"))
    out.setdefault("method", "graph")
    out.setdefault("sender_name", "Fundle.ai")
    return out


@router.get("/admin/outlook/status")
async def outlook_status(user: dict = Depends(require_roles("admin", "manager"))):
    cfg = await ol.get_config()
    return {**_public(cfg), "connected": await ol.is_connected()}


@router.put("/admin/outlook/settings")
async def save_outlook(body: OutlookSettings, user: dict = Depends(require_roles("admin"))):
    if body.method not in ("graph", "smtp"):
        raise HTTPException(status_code=400, detail="Method must be graph or smtp")
    if body.smtp_port and body.smtp_port not in (25, 465, 587):
        raise HTTPException(status_code=400, detail="SMTP port must be 587, 465 or 25")
    cur = await ol.get_config()
    upd = {"method": body.method, "updated_at": now_utc_str()}
    for k in ("tenant_id", "client_id", "smtp_host", "smtp_port", "smtp_user", "from_email", "sender_name"):
        v = getattr(body, k)
        upd[k] = v.strip() if isinstance(v, str) else v
    for k in ("client_secret", "smtp_password"):
        v = (getattr(body, k) or "").strip()
        if v:
            upd[f"{k}_enc"] = ol.enc(v)
    if any(upd.get(k) != cur.get(k) for k in ("smtp_host", "smtp_port", "smtp_user")) or body.smtp_password:
        upd["smtp_verified"] = False
    unset = {}
    if cur.get("client_id") and (cur.get("client_id"), cur.get("tenant_id")) != (upd["client_id"], upd["tenant_id"]):
        unset = {"refresh_token_enc": 1, "email": 1, "connected_at": 1}  # app changed → reconnect
    op = {"$set": upd}
    if unset:
        op["$unset"] = unset
    await db.settings.update_one({"key": "outlook"}, op, upsert=True)
    return {**_public(await ol.get_config()), "connected": await ol.is_connected()}


@router.get("/admin/outlook/auth-url")
async def outlook_auth_url(origin: str = Query(...), user: dict = Depends(require_roles("admin"))):
    cfg = await ol.get_config()
    if not cfg.get("client_id") or not cfg.get("client_secret_enc"):
        raise HTTPException(status_code=400, detail="Save the Application (client) ID and client secret first")
    redirect = f"{origin.rstrip('/')}/api/oauth/outlook/callback"
    state = secrets.token_urlsafe(24)
    await db.oauth_states.delete_many({"provider": "outlook"})
    await db.oauth_states.insert_one({"provider": "outlook", "state": state, "user_id": user["id"],
                                      "redirect": redirect, "created_at": now_utc_str()})
    return {"url": ol.authorize_url(cfg, redirect, state)}


def _back(base: str, status: str, reason: str = "") -> RedirectResponse:
    extra = f"&reason={quote(reason[:180], safe='')}" if reason else ""
    return RedirectResponse(f"{base}/admin?tab=Email&outlook={status}{extra}")


@router.get("/oauth/outlook/callback")
async def outlook_callback(code: str = Query(None), state: str = Query(None),
                           error: str = Query(None), error_description: str = Query(None)):
    st = await db.oauth_states.find_one({"provider": "outlook", "state": state}) if state else None
    if not st:
        return RedirectResponse("/admin?tab=Email&outlook=badstate")
    await db.oauth_states.delete_one({"_id": st["_id"]})
    base = st["redirect"].split("/api/")[0]
    if error or not code:
        return _back(base, "error", error_description or error or "Microsoft did not return a code")
    cfg = await ol.get_config()
    try:
        res = await ol.exchange_code(cfg, code, st["redirect"])
    except Exception as e:
        return _back(base, "error", str(e))
    if not res.get("refresh_token"):
        return _back(base, "error", "No refresh token — add the offline_access permission")
    await db.settings.update_one({"key": "outlook"}, {"$set": {
        "refresh_token_enc": ol.enc(res["refresh_token"]), "email": res.get("email"),
        "connected_at": now_utc_str()}, "$unset": {"auth_error": 1}})
    await db.settings.update_one({"key": "email_provider"}, {"$set": {"provider": "outlook"}}, upsert=True)
    return _back(base, "connected")


@router.post("/admin/outlook/disconnect")
async def outlook_disconnect(user: dict = Depends(require_roles("admin"))):
    await db.settings.update_one({"key": "outlook"}, {"$unset": {
        "refresh_token_enc": 1, "email": 1, "connected_at": 1, "smtp_password_enc": 1, "auth_error": 1}})
    return {"ok": True}


class TestEmail(BaseModel):
    to: str
    subject: str = "Fundle.ai CRM — Test Email"
    body: str = "<p>This is a test email from <b>Fundle.ai CRM</b> via Outlook. If you received it, email is working.</p>"


@router.post("/admin/outlook/send-test")
async def outlook_send_test(body: TestEmail, user: dict = Depends(require_roles("admin"))):
    if not await ol.is_connected():
        raise HTTPException(status_code=400, detail="Outlook is not set up yet")
    res = await ol.send_email(body.to.strip(), body.subject, body.body, html=True)
    is_smtp = (await ol.get_config()).get("method") == "smtp"
    if is_smtp:
        await db.settings.update_one({"key": "outlook"}, {"$set": {"smtp_verified": bool(res.get("ok"))}})
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    if is_smtp:
        await db.settings.update_one({"key": "outlook"}, {"$set": {"email": (await ol.get_config()).get("smtp_user"),
                                                                    "connected_at": now_utc_str()}})
    return res


class Provider(BaseModel):
    provider: str


@router.get("/admin/email/provider")
async def get_provider(user: dict = Depends(require_roles("admin", "manager"))):
    return {"provider": await mailer.active_provider(), "gmail_connected": await gmail_send.is_connected(),
            "outlook_connected": await ol.is_connected()}


@router.put("/admin/email/provider")
async def set_provider(body: Provider, user: dict = Depends(require_roles("admin"))):
    if body.provider not in mailer.PROVIDERS:
        raise HTTPException(status_code=400, detail="Provider must be gmail or outlook")
    await db.settings.update_one({"key": "email_provider"}, {"$set": {"provider": body.provider}}, upsert=True)
    return {"provider": body.provider}
