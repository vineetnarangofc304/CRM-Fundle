# Prompt 2 — MallMetrics.ai: seed every mall + AI Prospecting + Email Outreach

Paste everything below the line into the mallmetrics.ai Emergent project.

---

This project is the mallmetrics.ai website, which sells ADSR (Automated Daily Sales Reporting) and tenant sales tracking software to malls: daily tenant sales capture, tenant sales reconciliation, turnover-rent / revenue-share calculation, footfall-to-sales conversion and AI sales analytics for mall management. Do NOT change the public site's design or content. ADD a private admin back office at /admin with two modules: **Mall Prospecting**, which seeds and researches every mall, and **Email Outreach**, which sells MallMetrics to them.

## 0. Ask me first (use ask_human)
1. Login for the admin area. Default: email + password (JWT, bcrypt), with a seeded admin from .env; admins can invite team members. Call the integration expert before writing any auth code. If the project already has a CRM or admin area, reuse its login, Leads and email setup instead.
2. Countries. Default: India on; UAE, Saudi Arabia, Qatar, Oman, Bahrain and Kuwait available but off.
3. Which email inbox sends outreach (Outlook / Microsoft 365 via Graph OAuth, or SMTP; Gmail optional), plus the CC address and the signature.
4. The pitch paragraph and any proof points (malls already live, numbers) the AI may use.

## 1. Data model
- **Collections:** `leads` (only if the project has no CRM), `malls`, `mall_regions`, `prospect_runs`, `email_templates`, `outreach`, `outreach_files`, `settings`. Use int ids from a counters collection, and soft delete.
- **`malls`:**
  - Identity: mall_name, mall_key (normalised name + city; unique), country, state, city, tier (1/2/3 city), address, lat/lng (if found), website.
  - Owner and size: developer_group (e.g. Phoenix Mills, DLF, Nexus Malls, Lulu, Inorbit/K Raheja, Prestige, Brigade, VR, Pacific, Ambience, Forum/CapitaLand, Majid Al Futtaim, Emaar, Aldar), operator, opened_year, gla_sqft, floors, tenant_count, anchor_tenants [str], footfall_per_year, parking.
  - Current software: current_software (any visible POS / sales-reporting / mall management system), status "operational"|"under construction"|"announced".
  - Other: description, sources [urls], fit_score 0-100 (weighted by size, tenant count, group with several malls, newly opened or under construction, and signs they still collect tenant sales manually).
  - Research status: status discovered|enriching|enriched|failed, enrich_attempts, failed_at, retry_rounds, last_error.
  - Contacts: contacts [ {name, role, department, linkedin, email, email_status, domain_accepts_mail, evidence_url} ], contacts_status, contacts_failed_at, contacts_retry_rounds, email_pattern.
  - Links: lead_id, tpl_sent.
- **Developer groups:** also create a "group" lead per developer, linked to its malls, so one email can sell to a whole portfolio.

## 2. Mall seeding engine (Gemini via the Emergent LLM key and emergentintegrations: Flash for discovery, Pro for research, both with Google Search grounding; call the integration expert first)
- **Goal:** seed EVERY operational and upcoming mall in each enabled country.
- **Regions:** auto-create `mall_regions` = every state/emirate × its main cities (India: all 28 states and 8 UTs, with tier 1, 2 and 3 cities; UAE: the 7 emirates). The engine moves through the regions systematically.
- **Discovery:** each run takes the next region (fewest runs, then oldest) and asks for up to 25 real malls in that city or state, passing the names already known there so they aren't repeated. Rotate these angles:
  1. Lists of malls in the city (Wikipedia, city guides, Justdial, Google Maps listings).
  2. Developer and REIT portfolios (Nexus Select Trust REIT, Phoenix, DLF, Lulu, Inorbit, MAF, Emaar).
  3. Shopping Centres Association of India (SCAI), ICSC and MAPIC award lists.
  4. Retail real-estate reports (Anarock, JLL, CBRE, Knight Frank, Cushman & Wakefield).
  5. News of new mall openings and malls under construction.
  6. Mall leasing news in Images Retail, ET Retail and ET Realty.
  7. LinkedIn posts by mall heads and centre directors.
  8. Tier-2 and tier-3 city malls and high-street shopping centres.
  9. Airport and transit-hub retail centres.
  10. Outlet malls and lifestyle centres.

  Upsert each mall on mall_key and create a Lead (source "Mall Seeding"). A region counts as saturated after 3 runs in a row that each add fewer than 2 malls, once every angle has been used. Re-check saturated regions after 30 days. There is no daily cap.
- **Deep research (Pro):** fill in every field above, including the developer group, GLA, tenant count, anchors, footfall, status and fit score.
- **Contact finder (Pro + Google Search + a DNS MX check):** run several searches:
  - LinkedIn profiles via Google (`site:linkedin.com/in "<mall name>" "centre director"`, and the same for each role), plus the People section of the developer group's LinkedIn page.
  - Press releases, appointment news and interviews in Images Retail, ET Retail, ET Realty, Business Standard, Mint, Gulf News, Khaleej Times and Arabian Business.
  - The mall's and developer's websites.
  - RocketReach, ZoomInfo and Apollo snippets.

  Target roles, in priority order:
  - Mall Head, Centre Director or Centre Manager
  - GM or Head of Mall Operations
  - Head of Leasing or Leasing Manager
  - Mall Marketing Head
  - CFO or Finance Head (turnover rent)
  - CIO, CTO or IT Head
  - CEO or COO of the developer group, and Head of Retail Assets

  Collect up to 6 contacts per mall (plus group-level contacts), with a published work email or a pattern guess marked "pattern". Never invent people.
- **Worker:** a lock doc stops batches overlapping (the finally block clears only its own lock). N parallel lanes run until the deadline: about 60% discovery until every region is saturated, then mostly research and contacts.
- **Resilience:**
  - **AI-limit errors** (spend limit, budget, insufficient balance or quota, "exceeded your current quota", RateLimitError, RESOURCE_EXHAUSTED, a word-boundary 429, "No deployments available") are a temporary block: stop the batch, set ai_blocked_at, and don't mark anything failed.
  - **Pro fails:** fall back to Flash once.
  - **"overloaded" or 503:** back off 20s, then 40s.
  - **Requeue at every batch start:** AI-limit failures immediately; other failures after 6 hours, up to 5 rounds. Reset stale items after 10 minutes.

## 3. Email outreach
- **Templates** (`email_templates`: name, subject, body HTML with {{first_name}} {{mall}} {{city}} {{group}} {{role}}, mode auto|manual, active, attachments, video_links). Seed these:
  1. Intro to Mall Head (auto): "Daily tenant sales for {{mall}}, every morning, with no follow-ups".
  2. Finance/CFO emailer: accurate turnover rent and revenue-share reconciliation.
  3. Leasing emailer: tenant performance data for renewals and remerchandising.
  4. Developer group emailer: one dashboard across all of {{group}}'s malls.
  5. New / under-construction mall emailer: go live with ADSR from day one.
  6. Follow-up / bump.
- **Templates UI:** an "AI suggest / AI improve" button, a live preview, file attachments (brochure or case study PDF up to 3 MB, stored in Emergent Object Storage; call the integration expert) and video or demo links (sent as links).
- **Mall page:** each contact gets a button for every active template. Clicking one has AI tailor that template to the person and mall, and adds it to "Needs review". From there you can edit it and click **Send now**, or select several and **Finalise & send**.
- **Auto templates** go once per mall (tracked with tpl_sent) to every mall with an email, within the daily limit (default 100) and with CC. For a developer group, send the group template once to the group contacts rather than to each mall separately. Rejecting a message frees that template for the mall again.
- **Sending:** through Outlook Graph or SMTP (Gmail optional), with CC and attachments. A run lock plus an atomic approved→sending claim means nothing goes out twice. A failed send is retried 3 times, 1 hour apart. Every sent email is logged on the Lead, and each contact shows a history of what was sent and when.
- **Outreach page tabs:** Needs review, Approved/scheduled, Sent, Failed, Stopped, Rejected, plus an Email templates tab. Settings: enabled, auto-send, max emails per day, CC, signature, pitch.

## 4. Scheduling (.emergent/crons.yml; every endpoint replies 2xx immediately and does the work in the background; Bearer WEBHOOK_CRON_SECRET; 401 otherwise)
- `mall-seeding`: `*/15 16-23,0-3 * * *` UTC. The code only allows scheduled runs from 10 PM to 9 AM IST and cuts each batch off at 9 AM. Manual "Run now" works at any time.
- `mall-seeding-watchdog`: `7 16-23,0-3 * * *`. Frees a lock stuck for more than 20 minutes and restarts the engine if it has been idle for 30 minutes.
- `mall-outreach`: `*/15 * * * *`.

## 5. Admin UI (/admin, a clean dashboard look separate from the marketing site)
- **Mall Prospecting page:**
  - Engine panel with a status pill: Researching / Idle / Sleeping 10 PM–9 AM IST / Waiting — AI limit / Paused. It also shows the last batch, how many are waiting, and controls for Run now, Pause, Mode, Parallel and Countries.
  - A red alert when the AI is blocked.
  - Stats: malls seeded, researched, with contacts, with a Centre Director email, developer groups.
  - A coverage map grid by state/emirate showing malls per region, with a "DONE" badge when saturated.
  - A table with filters (country, state, city, tier, developer group, status operational/upcoming, GLA, tenant count, fit, has email) and sorting.
  - A mall detail page with all fields, anchors, contacts, template buttons and email history.
  - A Developer Groups view that rolls up each group's malls.
- **Leads page:** each lead's activity timeline.

## 6. Testing
Run the testing agent on:
- Region seeding and dedupe (same mall name in different cities is allowed), research, contacts.
- AI-limit handling (nothing marked failed), requeue rules, the night window, the watchdog, and the cron endpoints (401 without the secret).
- The template CRUD, the AI suggest button, attachments, click → review → send, auto-send with CC and dedupe, the group-level send, and the lead activity log.
- Admin auth.
