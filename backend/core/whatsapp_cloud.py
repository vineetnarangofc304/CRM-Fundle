"""WhatsApp Business Cloud API (Meta Graph API) service.

Live sending of template + session text messages, plus inbound webhook signature
verification and phone-number/template introspection. Config lives in settings
key="whatsapp_cloud" (managed in Admin → WhatsApp). No secrets in source.
"""
import re
import hmac
import hashlib
from datetime import datetime, timezone
from typing import Optional

import httpx

from core.db import db


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

GRAPH_VERSION = "v25.0"


async def get_config() -> dict:
    return await db.settings.find_one({"key": "whatsapp_cloud"}, {"_id": 0}) or {}


async def is_configured() -> bool:
    c = await get_config()
    return bool(c.get("access_token") and c.get("phone_number_id"))


def norm_msisdn(to: Optional[str]) -> str:
    """Normalize a phone to Meta's digits-only international format.
    Strips punctuation + a leading '00' international call prefix, and adds India's
    country code (91) to a bare 10-digit mobile (or a 0-prefixed 11-digit one) so
    numbers like '8591342426' / '00919024732761' become valid E.164 digits that
    Meta accepts (a malformed 'to' is the usual cause of error #131009)."""
    d = re.sub(r"\D", "", str(to or ""))
    if d.startswith("00"):
        d = d[2:]
    if len(d) == 10 and d[0] in "6789":
        d = "91" + d
    elif len(d) == 11 and d[0] == "0" and d[1] in "6789":
        d = "91" + d[1:]
    return d


def verify_signature(app_secret: str, body: bytes, header: Optional[str]) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


async def _post_message(payload: dict) -> dict:
    c = await get_config()
    if not c.get("access_token") or not c.get("phone_number_id"):
        return {"ok": False, "error": "WhatsApp Cloud API not configured"}
    ver = c.get("graph_api_version") or GRAPH_VERSION
    url = f"https://graph.facebook.com/{ver}/{c['phone_number_id']}/messages"
    headers = {"Authorization": f"Bearer {c['access_token']}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            r = await cl.post(url, headers=headers, json=payload)
        data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"request failed: {e}"}
    if r.status_code >= 400 or data.get("error"):
        err = data.get("error") or {}
        return {"ok": False, "error": err.get("message", str(data)),
                "code": err.get("code"), "raw": data}
    wamid = (data.get("messages") or [{}])[0].get("id")
    return {"ok": True, "wamid": wamid, "raw": data}


async def send_text(to: str, body: str) -> dict:
    return await _post_message({
        "messaging_product": "whatsapp", "recipient_type": "individual",
        "to": norm_msisdn(to), "type": "text",
        "text": {"preview_url": False, "body": body},
    })


async def send_template(to: str, template_name: str, language: str = "en", components: Optional[list] = None) -> dict:
    """Send an approved template. `components` is the fully-built Meta components array
    (header media + body params + button params) — it must match the approved template's
    structure exactly or Meta rejects with #132012 / #132000."""
    template = {"name": template_name, "language": {"code": language or "en"}}
    if components:
        template["components"] = components
    return await _post_message({
        "messaging_product": "whatsapp", "recipient_type": "individual",
        "to": norm_msisdn(to), "type": "template", "template": template,
    })


async def upload_media(data: bytes, content_type: str) -> dict:
    """Upload media bytes to Meta → returns a media id for sending (works without a public URL)."""
    c = await get_config()
    if not c.get("access_token") or not c.get("phone_number_id"):
        return {"ok": False, "error": "WhatsApp Cloud API not configured"}
    ver = c.get("graph_api_version") or GRAPH_VERSION
    url = f"https://graph.facebook.com/{ver}/{c['phone_number_id']}/media"
    files = {"file": ("upload", data, content_type or "application/octet-stream")}
    form = {"messaging_product": "whatsapp", "type": content_type or "application/octet-stream"}
    try:
        async with httpx.AsyncClient(timeout=60) as cl:
            r = await cl.post(url, headers={"Authorization": f"Bearer {c['access_token']}"}, data=form, files=files)
        j = r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if j.get("id"):
        return {"ok": True, "id": j["id"]}
    return {"ok": False, "error": (j.get("error") or {}).get("message", str(j))}


async def download_media(media_id: str) -> dict:
    """Fetch INBOUND media bytes from Meta by media id (images, documents/reports, voice
    notes, video, stickers a lead sends). Two-step per Meta's API: GET /{media_id} → a
    short-lived CDN url + mime_type, then GET that url WITH the Bearer token (the CDN requires
    auth) → raw bytes. Returned bytes are stored in object storage for CRM viewing/download."""
    c = await get_config()
    if not c.get("access_token"):
        return {"ok": False, "error": "WhatsApp Cloud API not configured"}
    ver = c.get("graph_api_version") or GRAPH_VERSION
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as cl:
            meta = await cl.get(f"https://graph.facebook.com/{ver}/{media_id}",
                                params={"access_token": c["access_token"]})
            mj = meta.json()
            url = mj.get("url")
            if not url:
                return {"ok": False, "error": (mj.get("error") or {}).get("message", "no media url")}
            r = await cl.get(url, headers={"Authorization": f"Bearer {c['access_token']}"})
        if r.status_code >= 400:
            return {"ok": False, "error": f"media fetch failed ({r.status_code})"}
        ctype = (mj.get("mime_type") or r.headers.get("content-type", "")).split(";")[0].strip() or "application/octet-stream"
        return {"ok": True, "data": r.content, "mime_type": ctype,
                "file_size": mj.get("file_size"), "sha256": mj.get("sha256")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def send_media(to: str, mtype: str, link: str = None, media_id: str = None, caption: str = "", filename: str = "") -> dict:
    """Send an image/video/document message via Cloud API by Meta media id or hosted link."""
    mt = (mtype or "document").lower()
    if mt not in ("image", "video", "document", "audio"):
        mt = "document"
    obj = {}
    if media_id:
        obj["id"] = media_id
    elif link:
        obj["link"] = link
    if caption and mt in ("image", "video", "document"):
        obj["caption"] = caption
    if filename and mt == "document":
        obj["filename"] = filename
    return await _post_message({
        "messaging_product": "whatsapp", "recipient_type": "individual",
        "to": norm_msisdn(to), "type": mt, mt: obj,
    })


async def send_reaction(to: str, wamid: str, emoji: str) -> dict:
    return await _post_message({
        "messaging_product": "whatsapp", "recipient_type": "individual",
        "to": norm_msisdn(to), "type": "reaction",
        "reaction": {"message_id": wamid, "emoji": emoji or ""},
    })


async def send_lead_template(lead: dict, template: dict, require_template: bool = False) -> dict:
    """Send a templates_whatsapp record to a lead via Cloud API.

    Sends as an APPROVED Meta template (deliverable OUTSIDE Meta's 24-hour window)
    when the template is linked (wa_template_name set), building the FULL component set
    the approved template requires — media header (image/video/document), every body
    variable {{1}}..{{n}}, and any URL-button variable — via build_components(). Sending
    the wrong number/shape of parameters (or omitting a required media header) is exactly
    what Meta rejects with error #132012. A free-form session text only works INSIDE the
    24h window; for new-lead automations/campaigns it fails with Meta error 131047, so when
    require_template=True we refuse the doomed free-text send with an actionable error."""
    phone = lead.get("phone") or lead.get("mobile")
    if not phone:
        return {"ok": False, "error": "lead has no phone"}
    name = lead.get("contact_name") or lead.get("name") or ""
    wa_name = template.get("wa_template_name")
    if wa_name:
        components = await build_components(lead, template)
        return await send_template(phone, wa_name, template.get("lang") or "en", components or None)
    if require_template:
        return {"ok": False, "error": (
            f"Template “{template.get('name')}” is not linked to an approved Meta template, "
            f"so it can only be sent as a free-form message inside the 24-hour customer "
            f"window (Meta rejects it with error 131047 for new leads). Open this WhatsApp "
            f"template and set its approved Meta template name + language to fix this."
        )}
    body = (template.get("body") or "").replace("{{1}}", name)
    return await send_text(phone, body)


# ---- Approved-template structure parsing + send-time component building ----

def _maxvar(text: str) -> int:
    nums = [int(m) for m in re.findall(r"\{\{\s*(\d+)\s*\}\}", text or "")]
    return max(nums) if nums else 0


def _sanitize_param(v) -> str:
    """Meta rejects template text params containing newlines, tabs, or long runs of
    spaces, and empty params for a variable slot. Coerce to a single-line non-empty string."""
    s = str(v if v is not None else "")
    s = s.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s or "-"


def extract_template_meta(components: list) -> dict:
    """Parse a Meta template's `components` into the structure the CRM needs to build a
    valid send: header format (+ its sample media url), body variable count, URL-button vars."""
    meta = {"wa_header_format": None, "wa_header_sample_url": None,
            "wa_header_text_vars": 0, "wa_body_var_count": 0,
            "wa_body_text": "", "wa_url_button": None}
    for c in (components or []):
        ty = (c.get("type") or "").upper()
        if ty == "HEADER":
            fmt = (c.get("format") or "").upper()
            meta["wa_header_format"] = fmt or None
            if fmt in ("IMAGE", "VIDEO", "DOCUMENT"):
                handle = (c.get("example") or {}).get("header_handle") or []
                meta["wa_header_sample_url"] = handle[0] if handle else None
            elif fmt == "TEXT":
                meta["wa_header_text_vars"] = _maxvar(c.get("text"))
        elif ty == "BODY":
            meta["wa_body_text"] = c.get("text") or ""
            meta["wa_body_var_count"] = _maxvar(c.get("text"))
        elif ty == "BUTTONS":
            for idx, b in enumerate(c.get("buttons") or []):
                if (b.get("type") or "").upper() == "URL" and _maxvar(b.get("url")):
                    meta["wa_url_button"] = {"index": idx, "var_count": _maxvar(b.get("url"))}
                    break
    return meta


def _lead_value(lead: dict, field: str):
    if not field:
        return None
    v = lead.get(field)
    if v in (None, ""):
        v = (lead.get("custom") or {}).get(field)
    return v


def _resolve_variable(mapping: dict, lead: dict, name: str):
    """Resolve one template variable mapping (from the template 'variables' list) to a value."""
    vtype = (mapping.get("type") or "").strip().lower()
    field = mapping.get("field")
    if vtype in ("user name", "username"):
        return name
    if vtype in ("user phone", "userphone"):
        return lead.get("phone") or lead.get("mobile")
    if field:
        val = _lead_value(lead, field)
        if val not in (None, ""):
            return val
    return mapping.get("sample")


def build_body_values(lead: dict, template: dict, count: int) -> list:
    """Build exactly `count` positional body params for {{1}}..{{count}} from the template's
    variable mappings; {{1}} defaults to the lead name, unmapped slots fall back to a safe '-'."""
    name = lead.get("contact_name") or lead.get("name") or ""
    by_index, seq = {}, []
    for v in (template.get("variables") or []):
        idx = _maxvar(v.get("name"))
        (by_index.__setitem__(idx, v) if idx else seq.append(v))
    values = []
    for i in range(1, count + 1):
        m = by_index.get(i) or (seq[i - 1] if i - 1 < len(seq) else None)
        val = _resolve_variable(m, lead, name) if m else None
        if val in (None, "") and i == 1:
            val = name
        values.append(_sanitize_param(val))
    return values


_CTYPE_BY_FMT = {"IMAGE": "image/jpeg", "VIDEO": "video/mp4", "DOCUMENT": "application/pdf"}


async def _download(url: str):
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cl:
            r = await cl.get(url)
        if r.status_code >= 400:
            return None, None
        return r.content, (r.headers.get("content-type", "").split(";")[0].strip() or None)
    except Exception:
        return None, None


async def resolve_header_media(template: dict) -> Optional[dict]:
    """Return a Meta header media parameter (e.g. {"type":"image","image":{"id":...}}) for a
    media-header template. Uploads the header media to Meta ONCE and caches the reusable media
    id (valid ~30 days) on the template record + in-memory dict so a bulk campaign never
    re-fetches an expiring public link per recipient."""
    fmt = (template.get("wa_header_format") or "").upper()
    if fmt not in ("IMAGE", "VIDEO", "DOCUMENT"):
        return None
    key = fmt.lower()
    mid, up = template.get("wa_header_media_id"), template.get("wa_header_media_at")
    if mid and up:
        try:
            age = (datetime.now(timezone.utc) - datetime.strptime(up, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)).days
        except Exception:
            age = 999
        if age < 25:
            return {"type": key, key: {"id": mid}}
    url = template.get("header_media_url") or template.get("wa_header_sample_url")
    if not url:
        return None
    data, ctype = await _download(url)
    if data:
        res = await upload_media(data, ctype or _CTYPE_BY_FMT[fmt])
        if res.get("ok"):
            now = _now_str()
            template["wa_header_media_id"] = res["id"]
            template["wa_header_media_at"] = now
            try:
                if template.get("id") is not None:
                    await db.templates_whatsapp.update_one(
                        {"id": template["id"]},
                        {"$set": {"wa_header_media_id": res["id"], "wa_header_media_at": now}})
            except Exception:
                pass
            return {"type": key, key: {"id": res["id"]}}
    return {"type": key, key: {"link": url}}


async def ensure_template_meta(template: dict) -> dict:
    """Self-heal: if a linked template record is missing its parsed Meta structure
    (older sync, or never re-synced after this fix shipped), fetch the approved template
    from Meta ONCE, enrich the in-memory dict + persist it. This makes campaign/manual/
    automation sends build the correct header + body params even when the admin forgot to
    click 'Sync from WhatsApp' after a deploy — removing the re-sync dependency that would
    otherwise leave sends failing with Meta #132012."""
    if template.get("wa_header_format") is not None or template.get("wa_body_var_count") is not None:
        return template
    wa_name = template.get("wa_template_name")
    if not wa_name:
        return template
    data = await list_templates()
    if data.get("error"):
        return template
    lang = (template.get("lang") or "").strip()
    items = data.get("data") or []
    match = next((t for t in items if (t.get("name") or "") == wa_name and (not lang or (t.get("language") or "") == lang)), None)
    if not match:
        match = next((t for t in items if (t.get("name") or "") == wa_name), None)
    if not match:
        return template
    meta = extract_template_meta(match.get("components"))
    template.update(meta)
    try:
        if template.get("id") is not None:
            await db.templates_whatsapp.update_one({"id": template["id"]}, {"$set": meta})
    except Exception:
        pass
    return template


async def build_components(lead: dict, template: dict) -> list:
    """Assemble the exact Meta `components` array an approved template requires for a lead."""
    await ensure_template_meta(template)
    components = []
    fmt = (template.get("wa_header_format") or "").upper()
    if fmt in ("IMAGE", "VIDEO", "DOCUMENT"):
        hdr = await resolve_header_media(template)
        if hdr:
            components.append({"type": "header", "parameters": [hdr]})
    elif fmt == "TEXT" and template.get("wa_header_text_vars"):
        name = lead.get("contact_name") or lead.get("name") or ""
        params = [{"type": "text", "text": _sanitize_param(name if i == 1 else "-")}
                  for i in range(1, int(template["wa_header_text_vars"]) + 1)]
        components.append({"type": "header", "parameters": params})

    count = template.get("wa_body_var_count")
    if count is None:
        count = _maxvar(template.get("body"))
    count = int(count or 0)
    if count:
        vals = build_body_values(lead, template, count)
        components.append({"type": "body", "parameters": [{"type": "text", "text": v} for v in vals]})

    ub = template.get("wa_url_button")
    if ub and ub.get("var_count"):
        name = lead.get("contact_name") or lead.get("name") or ""
        val = _sanitize_param(lead.get("id") or lead.get("phone_digits") or name)
        components.append({"type": "button", "sub_type": "url", "index": ub.get("index", 0),
                           "parameters": [{"type": "text", "text": val}]})
    return components


async def _graph_get(path: str, params: dict = None) -> dict:
    c = await get_config()
    if not c.get("access_token"):
        return {"error": {"message": "not configured"}}
    ver = c.get("graph_api_version") or GRAPH_VERSION
    url = f"https://graph.facebook.com/{ver}/{path}"
    p = {"access_token": c["access_token"], **(params or {})}
    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            r = await cl.get(url, params=p)
        return r.json()
    except Exception as e:
        return {"error": {"message": str(e)}}


async def _graph_post(path: str, data: dict = None) -> dict:
    c = await get_config()
    if not c.get("access_token"):
        return {"error": {"message": "not configured"}}
    ver = c.get("graph_api_version") or GRAPH_VERSION
    url = f"https://graph.facebook.com/{ver}/{path}"
    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            r = await cl.post(url, params={"access_token": c["access_token"]}, json=(data or {}))
        return r.json()
    except Exception as e:
        return {"error": {"message": str(e)}}


async def subscribe_waba() -> dict:
    """Subscribe the app to this WABA's webhooks so Meta delivers message + status
    events (sent/delivered/read/failed). Required for delivery-status tracking."""
    c = await get_config()
    if not c.get("waba_id"):
        return {"error": {"message": "WABA ID required"}}
    return await _graph_post(f"{c['waba_id']}/subscribed_apps")


async def get_subscribed_apps() -> dict:
    c = await get_config()
    if not c.get("waba_id"):
        return {"error": {"message": "WABA ID required"}}
    return await _graph_get(f"{c['waba_id']}/subscribed_apps")


async def check_app_subscriptions() -> dict:
    """Read the Meta app's webhook subscriptions (uses an app access token).
    Reveals the callback URL + subscribed fields for whatsapp_business_account —
    i.e. whether Meta is pointed at THIS CRM and the 'messages' field is on
    (which carries delivery/read status events)."""
    c = await get_config()
    app_id, app_secret = c.get("app_id"), c.get("app_secret")
    if not app_id or not app_secret:
        return {"error": {"message": "App ID and App Secret required"}}
    ver = c.get("graph_api_version") or GRAPH_VERSION
    url = f"https://graph.facebook.com/{ver}/{app_id}/subscriptions"
    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            r = await cl.get(url, params={"access_token": f"{app_id}|{app_secret}"})
        return r.json()
    except Exception as e:
        return {"error": {"message": str(e)}}


async def list_phone_numbers() -> dict:
    c = await get_config()
    if not c.get("waba_id"):
        return {"error": {"message": "WABA ID required"}}
    return await _graph_get(f"{c['waba_id']}/phone_numbers", {"fields": "id,display_phone_number,verified_name,quality_rating"})


async def list_templates() -> dict:
    """Fetch ALL message templates for the WABA, following Meta's pagination so a newly
    approved template is never missed. An active WABA accumulates hundreds of template
    objects (approved + rejected + paused, across languages), and Meta returns them in
    pages — fetching only the first page silently dropped the newest ones. Returns
    {"data": [...all...]} like the raw Graph API response."""
    c = await get_config()
    if not c.get("waba_id"):
        return {"error": {"message": "WABA ID required"}}
    if not c.get("access_token"):
        return {"error": {"message": "not configured"}}
    ver = c.get("graph_api_version") or GRAPH_VERSION
    url = f"https://graph.facebook.com/{ver}/{c['waba_id']}/message_templates"
    params = {"access_token": c["access_token"], "fields": "name,status,language,category,components", "limit": 100}
    out = []
    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            for _ in range(30):  # safety cap: 30 pages x 100 = 3000 templates
                r = await cl.get(url, params=params)
                j = r.json()
                if j.get("error"):
                    return j if not out else {"data": out}
                out.extend(j.get("data") or [])
                nxt = (j.get("paging") or {}).get("next")
                if not nxt:
                    break
                url, params = nxt, None  # Meta's 'next' is a full URL with cursor + token baked in
    except Exception as e:
        return {"error": {"message": str(e)}} if not out else {"data": out}
    return {"data": out}
