"""One-time B2B SaaS sales starter pack: dispositions + stage mapping, lost reasons, sources,
follow-up tags, activity types, UTM values, qualification custom fields and KPI config.
Flag-guarded (settings.seed_b2b_v1) and additive, so admin edits/deletions are never undone."""
import logging

from core.db import db
from core.utils import ensure_catalog, next_id

logger = logging.getLogger(__name__)

DISPOSITIONS = {
    "Contact Attempt": ["Ringing", "Busy", "Phone Switched Off", "Not Reachable", "No Answer",
                        "Call Disconnected", "Voicemail Left", "Email Sent - Awaiting Reply",
                        "LinkedIn Request Sent"],
    "Contacted": ["Call back for first pitch", "Interested - Send Details", "Details Shared",
                  "Discovery Call Done", "Qualified (BANT)", "Decision Maker Not Available",
                  "Demo Booked", "Demo Rescheduled", "Demo No-Show", "Demo Done",
                  "Trial / POC Started", "Requirements Gathered", "Proposal Sent", "Negotiation",
                  "Contract Sent", "Awaiting Decision", "Nurture - Future Interest"],
    "Converted": ["Won", "Contract Signed", "Payment Received"],
    "Closed": ["Duplicate Lead", "Incoming Not Available", "Invalid Number", "Job Enquiry", "Junk",
               "Language Barrier", "Not Contactable", "Not Interested (Budget)",
               "Not Interested (Competition)", "Not Interested (No Need)", "Not Interested (Timing)",
               "Valid Not Interested", "Wrong Number", "Abusive Language", "Not a Decision Maker",
               "Individual / Student (Not B2B)", "Company Too Small", "Already a Customer",
               "Vendor / Partnership Enquiry", "Email Bounced", "Lost - No Response", "Lost to Competitor",
               "Lost - No Decision"],
}

VALID_TAGS = ["Call back for first pitch", "Interested - Send Details", "Details Shared", "Discovery Call Done",
              "Qualified (BANT)", "Decision Maker Not Available", "Demo Booked", "Demo Rescheduled",
              "Demo No-Show", "Demo Done", "Trial / POC Started", "Requirements Gathered", "Proposal Sent",
              "Negotiation", "Contract Sent", "Awaiting Decision", "Nurture - Future Interest", "Won",
              "Contract Signed", "Payment Received", "Valid Not Interested", "Not Interested (Budget)",
              "Not Interested (Competition)", "Not Interested (Timing)", "Lost to Competitor",
              "Lost - No Decision"]

LOST_REASONS = ["Price / Budget Too High", "Chose a Competitor", "No Decision / Went Silent",
                "Timing Not Right", "Missing Features", "Not the Right Fit", "Building In-House",
                "Champion Left the Company", "Security / Compliance Concerns", "Integration Limitations",
                "Procurement / Legal Blocked", "Using Existing Vendor", "Poor Demo Experience",
                "Unqualified Lead"]

SOURCES = ["Website Form", "Website Chat", "Website AI Agent", "Inbound Call", "Meta Lead Ads",
           "Google Ads", "LinkedIn Ads", "LinkedIn Outreach", "Cold Calling", "Cold Email",
           "Email Campaign", "WhatsApp Campaign", "Webinar", "Event / Trade Show", "Referral",
           "Partner / Reseller", "Organic Search", "Review Sites (G2 / Capterra)", "Product Hunt",
           "Existing Customer (Upsell)", "Free Trial Signup", "Bulk Upload"]

FOLLOW_UP_TAGS = ["Intro Call", "Discovery Call", "Demo Reminder", "Post-Demo Follow-up", "Proposal Follow-up",
                  "Negotiation Call", "Contract Follow-up", "Payment Follow-up", "Trial Check-in",
                  "Nurture Check-in"]

ACTIVITY_TYPES = ["Call", "Email", "WhatsApp", "Meeting", "Demo", "Proposal", "To-Do", "LinkedIn Message"]

UTM = {
    "utm_source": ["google", "facebook", "instagram", "linkedin", "youtube", "twitter", "email", "whatsapp",
                   "referral", "partner", "g2", "capterra", "producthunt", "direct"],
    "utm_medium": ["cpc", "paid_social", "organic", "social", "email", "referral", "display", "video",
                   "webinar", "event", "affiliate"],
}

CUSTOM_FIELDS = [
    ("Industry", "selection", ["IT / Software", "BFSI", "E-commerce / Retail", "Healthcare", "Education / EdTech",
                               "Manufacturing", "Real Estate", "Logistics", "Media / Marketing", "Telecom",
                               "Hospitality / Travel", "Government / PSU", "Professional Services", "Other"],
     ["industry", "sector", "vertical"]),
    ("Product Interest", "selection", ["AI Sales Agent", "AI Support Agent", "CRM", "Analytics / BI",
                                       "Custom AI Solution", "Integrations / API", "Other"],
     ["product", "product_interest", "interested_in"]),
    ("Budget Range", "selection", ["Under ₹50K", "₹50K - ₹2L", "₹2L - ₹5L", "₹5L - ₹20L", "₹20L+", "Not Disclosed"],
     ["budget", "budget_range"]),
    ("Decision Timeline", "selection", ["Immediate", "Within 1 Month", "1-3 Months", "3-6 Months", "6+ Months",
                                        "Just Exploring"], ["timeline", "decision_timeline", "purchase_timeline"]),
    ("Decision Role", "selection", ["Decision Maker", "Influencer", "Champion", "Evaluator / User",
                                    "Procurement", "Gatekeeper"], ["decision_role", "buyer_role"]),
    ("Current Solution", "char", [], ["current_solution", "current_tool", "competitor"]),
    ("Company Website", "char", [], ["website", "company_website", "url"]),
    ("Annual Revenue", "selection", ["Under ₹1 Cr", "₹1-10 Cr", "₹10-50 Cr", "₹50-250 Cr", "₹250 Cr+"],
     ["annual_revenue", "revenue", "turnover"]),
    ("Pain Points", "text", [], ["pain_points", "challenges", "problem"]),
]


async def _add_custom_fields():
    import re
    last = await db.custom_fields.find_one({}, sort=[("sequence", -1)])
    seq = last.get("sequence", 0) if last else 0
    for label, ftype, options, aliases in CUSTOM_FIELDS:
        key = "x_custom_" + re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:50]
        if await db.custom_fields.find_one({"key": key}):
            continue
        seq += 1
        await db.custom_fields.insert_one({
            "id": await next_id("custom_field"), "key": key, "label": label, "field_type": ftype,
            "options": options, "section": "general", "sequence": seq, "aliases": aliases, "active": True})


async def seed_b2b_starter():
    if await db.settings.find_one({"key": "seed_b2b_v1"}):
        return
    dm = ((await db.settings.find_one({"key": "disposition_map"})) or {}).get("map") or {}
    mapped = {t for names in dm.values() for t in names}
    for stage, names in DISPOSITIONS.items():
        for t in names:
            await ensure_catalog("tag", t)
            if t not in mapped:
                dm.setdefault(stage, []).append(t)
                mapped.add(t)
    await db.settings.update_one({"key": "disposition_map"}, {"$set": {"map": dm}}, upsert=True)
    for ctype, names in [("lost_reason", LOST_REASONS), ("source_lead", SOURCES), ("follow_up_tag", FOLLOW_UP_TAGS),
                         ("activity_type", ACTIVITY_TYPES), *UTM.items()]:
        for n in names:
            await ensure_catalog(ctype, n)
    await _add_custom_fields()
    # Drop legacy placeholder sources that no lead uses.
    for old in ["landing_page", "chatbot", "website", "App", "Callback_Request"]:
        if not await db.leads.find_one({"source_lead": old}, {"_id": 1}):
            await db.catalogs.delete_one({"type": "source_lead", "name": old})
    kc = await db.settings.find_one({"key": "kpi_config"}) or {}
    valid = list(dict.fromkeys((kc.get("valid_tags") or []) + VALID_TAGS))
    await db.settings.update_one({"key": "kpi_config"}, {"$set": {
        "milestones": kc.get("milestones") or ["Demo Booked", "Demo Done", "Proposal Sent", "Won"],
        "valid_tags": valid}}, upsert=True)
    await db.settings.insert_one({"key": "seed_b2b_v1", "done": True})
    logger.info("Seeded B2B sales starter pack")
