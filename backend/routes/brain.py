"""Fundle Brain chat: sessions (left sidebar history), streaming turns, audit log."""
import json
import re

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.db import db
from core.permissions import effective_permissions
from core.security import get_current_user
from core.utils import next_id, now_utc_str
from core import brain, brain_data

router = APIRouter(prefix="/brain", tags=["brain"])


async def brain_user(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "admin" and not (await effective_permissions(user.get("role")) or {}).get("brain") \
            and not (user.get("permissions") or {}).get("brain"):
        raise HTTPException(status_code=403, detail="You don't have access to Fundle Brain")
    return user


async def _session(sid: int, user: dict) -> dict:
    s = await db.brain_sessions.find_one({"id": sid, "deleted": {"$ne": True}}, {"_id": 0})
    if not s or (s["user_id"] != user["id"] and user.get("role") != "admin"):
        raise HTTPException(status_code=404, detail="Chat not found")
    return s


@router.get("/sessions")
async def sessions(user: dict = Depends(brain_user)):
    rows = await db.brain_sessions.find({"user_id": user["id"], "deleted": {"$ne": True}}, {"_id": 0}
                                        ).sort("updated_at", -1).limit(200).to_list(200)
    return rows


@router.post("/sessions")
async def new_session(user: dict = Depends(brain_user)):
    doc = {"id": await next_id("brain_session"), "user_id": user["id"], "user_name": user.get("name"), "title": "New chat",
           "created_at": now_utc_str(), "updated_at": now_utc_str()}
    await db.brain_sessions.insert_one(doc)
    doc.pop("_id", None)
    return doc


class Rename(BaseModel):
    title: str


@router.patch("/sessions/{sid}")
async def rename(sid: int, b: Rename, user: dict = Depends(brain_user)):
    await _session(sid, user)
    await db.brain_sessions.update_one({"id": sid}, {"$set": {"title": b.title.strip()[:80] or "Chat"}})
    return {"ok": True}


@router.delete("/sessions/{sid}")
async def delete(sid: int, user: dict = Depends(brain_user)):
    await _session(sid, user)
    await db.brain_sessions.update_one({"id": sid}, {"$set": {"deleted": True}})
    return {"ok": True}


@router.get("/sessions/{sid}/messages")
async def messages(sid: int, user: dict = Depends(brain_user)):
    await _session(sid, user)
    return await db.brain_messages.find({"session_id": sid}, {"_id": 0}).sort("id", 1).to_list(1000)


class Say(BaseModel):
    message: str


@router.post("/sessions/{sid}/chat")
async def chat(sid: int, b: Say, user: dict = Depends(brain_user)):
    s = await _session(sid, user)
    text = b.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Type a message")
    prev = await db.brain_messages.find({"session_id": sid}, {"_id": 0}).sort("id", -1).limit(30).to_list(30)
    history = [{"role": m["role"], "content": m["content"]} for m in reversed(prev) if m.get("content")]
    await db.brain_messages.insert_one({"id": await next_id("brain_message"), "session_id": sid, "role": "user",
                                        "content": text, "at": now_utc_str(), "user_name": user.get("name")})
    if s["title"] == "New chat":
        att = re.match(r"\[Attached file #\d+: (.+?) —.*?\]\s*(.*)", text, re.S)
        title = (att.group(2).strip() or f"File: {att.group(1)}") if att else text
        await db.brain_sessions.update_one({"id": sid}, {"$set": {"title": re.sub(r"\s+", " ", title)[:60]}})
    ctx = {"user": user, "session_id": sid}

    async def gen():
        out, tools = "", []
        try:
            async for ev in brain.run_turn(ctx, history, text):
                if ev["type"] == "text":
                    out += ev["delta"] or ""
                elif ev["type"] == "tool_done":
                    tools.append({"name": ev["name"], "ok": ev["ok"], "summary": ev["summary"]})
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except Exception as e:
            err = str(e)
            msg = ("The AI provider's credit/rate limit was hit — add balance (Profile → Universal Key) and try again."
                   if re.search(r"429|quota|budget|spend|rate.?limit", err, re.I) else f"Something went wrong: {err[:300]}")
            out += f"\n\n⚠️ {msg}"
            yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"
        finally:
            await db.brain_messages.insert_one({"id": await next_id("brain_message"), "session_id": sid, "role": "assistant",
                                                "content": out.strip() or "(no reply)", "tools": tools, "at": now_utc_str()})
            await db.brain_sessions.update_one({"id": sid}, {"$set": {"updated_at": now_utc_str()}})
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/exports")
async def exports(user: dict = Depends(brain_user)):
    f = {} if user.get("role") == "admin" else {"user_id": user["id"]}
    return await db.brain_exports.find(f, {"_id": 0, "csv": 0}).sort("id", -1).limit(300).to_list(300)


@router.get("/exports/{eid}")
async def export_file(eid: int, user: dict = Depends(brain_user)):
    from fastapi.responses import Response
    e = await db.brain_exports.find_one({"id": eid}, {"_id": 0})
    if not e or (e["user_id"] != user["id"] and user.get("role") != "admin"):
        raise HTTPException(status_code=404, detail="Export not found")
    return Response("\ufeff" + e["csv"], media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{e["filename"]}"'})


@router.post("/sessions/{sid}/upload")
async def upload(sid: int, file: UploadFile = File(...), user: dict = Depends(brain_user)):
    await _session(sid, user)
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 15 MB)")
    try:
        doc = await brain_data.save_upload(sid, user, file.filename or "upload.csv", file.content_type, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Couldn't read that file: {str(e)[:200]}")
    return doc


@router.get("/sources")
async def sources(user: dict = Depends(brain_user)):
    return await brain_data.get_sources()


@router.get("/audit")
async def audit(limit: int = 200, user: dict = Depends(brain_user)):
    q = {} if user.get("role") == "admin" else {"user_id": user["id"]}
    return await db.brain_audit.find(q, {"_id": 0}).sort("id", -1).limit(min(limit, 500)).to_list(500)


@router.get("/requests")
async def change_requests(user: dict = Depends(brain_user)):
    return await db.brain_requests.find({}, {"_id": 0}).sort("id", -1).limit(100).to_list(100)
