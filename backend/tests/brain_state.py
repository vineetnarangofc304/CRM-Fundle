# Helper: print Brain-related DB state (prospecting enabled, brain outreach, TEST_ prospects, recent audit)
import asyncio, sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from core.db import db


async def main():
    p = await db.settings.find_one({"key": "prospecting"}, {"_id": 0, "enabled": 1, "phase": 1})
    print("PROSPECTING", p)
    print("MAX_OUTREACH", (await db.outreach.find({}, {"id": 1}).sort("id", -1).limit(1).to_list(1)))
    print("BRAIN_OUTREACH_ACTIVE", await db.outreach.find({"kind": "brain", "status": {"$in": ["approved", "pending_approval", "sending"]}}, {"_id": 0, "id": 1, "to": 1, "subject": 1, "status": 1}).to_list(50))
    print("TEST_PROSPECTS", await db.prospects.find({"brand_name": {"$regex": "^TEST_"}}, {"_id": 0, "id": 1, "brand_name": 1, "contacts": 1, "contacts_priority": 1, "category": 1}).to_list(20))
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if n:
        print("AUDIT", await db.brain_audit.find({"session_id": n}, {"_id": 0, "tool": 1, "reason": 1, "ok": 1, "result": 1}).sort("id", 1).to_list(50))

asyncio.run(main())
