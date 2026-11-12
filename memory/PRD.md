# Fundle.ai CRM — PRD

## Original problem statement
Rebuild the production "HomeIVF CRM" (github.com/vineetnarangofc304/HomeIVF-CLM) for a new client, Fundle.ai (B2B SaaS / AI products), as an exact functional replica with no data. Only rebrand, remove all fertility/medical content, and make the fertility-specific KPI ("Lead Pulse") logic configurable from Admin. Keep "Powered by TifTech". Default market India / +91 / INR. Brand colour #1A73E8 (from fundle.ai).

## Architecture
React (CRA + Tailwind + shadcn + phosphor + recharts/chart.js) / FastAPI (motor) / MongoDB. Backend: server.py (indexes, seeds, admin seed), core/, routes/ (auth, users, leads, chatter, catalogs, templates, whatsapp, wa_cloud, wa_tracking, reports, export, webhooks, facebook, calls, agent, marketing, filters, attachments, ai, gmail, admin, meta_capi).

## Users
Admin, Manager, Caller (per-module RBAC).

## Done (Jun 2026)
- Imported full codebase; removed HomeIVF tests, seed_perf.py, memory, reports.
- Rebrand: brand constant `frontend/src/lib/brand.js`, logos in `public/brand/` (fundle-logo.png, fundle-logo-white.png, fundle-icon.png), colours, Login, Layout, ScreenPop, index.html, API title, admin seed name, Gmail sender, export file names/PDF title, AI prompt, CAPI source, storage APP_NAME. CSS class prefix `hivf-` → `crm-`.
- Removed fertility fields (gender, age, male/female_age, spouse_*, pre_conditions, doctor_name) from LeadDetail, leads.py, export, webhooks/facebook maps, Admin mapping lists, template field options. "Case Details" → "Enquiry Details". Q&A card: question = label has "?" or Q&A custom field.
- Generic disposition seed (Contact Attempt / Contacted / Converted (empty) / Closed).
- Lead Pulse: stage rows from Admin Tag→Stage mapping; new Admin → "KPI Settings" tab (settings key `kpi_config`: milestones ≤4 ordered, valid_tags); empty states with "Configure in Admin" links.

## Integrations awaiting client credentials
Gmail OAuth (GOOGLE_CLIENT_ID/SECRET, GMAIL_REDIRECT_URI), Meta CAPI (dataset id/token), Facebook Lead Ads, WhatsApp Cloud API, Ozonetel (entered in Admin).

## Backlog
- P1: Full regression with test data on every module.
- P2: appointment_date/time kept in backend only (no UI label). Lead list shows "Many leads" on first load (estimated counts, by design for 120k+ leads).

## Jun 2026 (iteration 2)
- "Powered by Fundle.ai" everywhere (login, sidebar, screen-pop, title, API health); logo aspect fixed; no Emergent/TifTech mentions in UI.
- Admin creds: admin@fundle.ai (see test_credentials.md). Testing: backend 24/24, frontend all pages pass.

## Jun 2026 (iteration 3)
- SaaS starter funnel (one-time seed, flag seed_saas_starter): Demo Booked, Demo Done, Proposal Sent -> Contacted; Won -> Converted; KPI milestones pre-filled.
- Lead fields company_name, designation, team_size, deal_value (INR, validated, chatter shows formatted).
- Deal value: Dashboard strip (open pipeline, won MTD, won in range) + KPI "Won Deal Value" card. Won = lead_stage Converted; pipeline = active not Converted/Closed.

## Jun 2026 (iteration 4) — Bulk Upload
- Leads page "Bulk Upload" (admin/manager): .xlsx/.csv up to 50k rows / 15MB, sample template, auto column mapping (standard + custom fields), duplicate mode skip/update/create, assign by column / round-robin callers / one user / none, default source, optional automations. Background job (db.import_jobs) with live progress + downloadable issues list. Routes: /api/import/template.xlsx, /preview, /leads, /jobs, /jobs/{id}. Unknown tags auto-created with a note.

## Jun 2026 (iteration 5)
- Bulk upload chunked (1,000 rows: one $in dupe lookup, insert_many, bulk_write, batched message ids): 50k rows in ~11s. New "Won Date" import column.
- Win date: won_date/won_date_ist stamped first time a lead reaches Converted (lead edit, bulk set stage, automations, call disposition, import). Won value reports use won date (fallback create date). Export has Won Date.
- Leads list: Company + Deal Value columns (sortable, indexed), "Min Deal ₹" filter (min_deal_value).

## Jun 2026 (iteration 6) — B2B starter pack
- core/b2b_seed.py (one-time, flag seed_b2b_v1, additive): 52 disposition tags mapped to 4 stages, 14 lost reasons, 22 sources, 10 B2B follow-up tags, 8 activity types, UTM source/medium values, 9 qualification custom fields (Industry, Product Interest, Budget Range, Decision Timeline, Decision Role, Current Solution, Company Website, Annual Revenue, Pain Points), KPI valid tags. Legacy placeholder sources removed.

## Jun 2026 (iteration 7) — Website Lead API
- POST /api/webhook/lead/{token} (existing) now maps alternate_number, country, remark, deal_value; rejects requests with no valid phone/email (422); default source "Website Form". Webhook "Fundle.ai Website" created in preview. Docs: /app/docs/WEBSITE_LEAD_API.md.

## Jun 2026 (iteration 8) — Outlook email
- core/outlook_send.py (Graph OAuth delegated Mail.Send via httpx, refresh-token rotation; SMTP via aiosmtplib smtp.office365.com:587), secrets Fernet-encrypted (key derived from JWT_SECRET) in settings.outlook. core/mailer.py routes all CRM email (lead compose, automations, campaigns) to active provider (settings.email_provider: gmail|outlook). Routes: /api/admin/outlook/status|settings|auth-url|disconnect|send-test, /api/oauth/outlook/callback, /api/admin/email/provider. Admin → Email: provider picker + Outlook card + Gmail card.
- Found: fundle.ai M365 tenant has SMTP AUTH disabled → use Graph sign-in.
- Admin login email changed to vineetnarangofc@gmail.com.

## Jun 2026 (iteration 9) — AI Prospecting engine
- core/prospector.py: 33 consumer categories x countries (India on; UAE, Saudi, Qatar, Kuwait, Oman, Bahrain, UK available). Discovery = Gemini 3.5 Flash + Google Search grounding, 8 rotating research angles (RAI/Franchise India, marketplaces Amazon/Flipkart/Myntra/Nykaa, malls, news, D2C, regional, franchise listings, rankings); category saturates after 3 low-yield rounds once all angles tried, re-opens weekly. Enrichment = Gemini 3.1 Pro + Google Search: stores, cities, CEO, CEO LinkedIn, founders, company LinkedIn, email, phone, revenue, employees, existing loyalty programme, sources. Every brand upserted as CRM lead (source AI Prospecting, prospect_category/segment/store_count/ceo fields).
- Worker: 14-min batches, N parallel lanes (default 3), mongo lease lock; triggered by platform cron */15 (.emergent/crons.yml -> POST /api/cron/prospecting, WEBHOOK_CRON_SECRET), "Run now", and on backend startup.
- UI: /prospecting (engine controls, stats, category grid, filterable table, run log), /prospecting/:id profile; Leads filters Category/Segment/Min stores + columns; lead AI Prospect Research card.
- Backlog: agent outreach (auto emails, LinkedIn messages), email-finding, credit budget cap.

## Jun 2026 (iteration 10) — Email finder + Outreach
- core/contacts.py: Gemini Pro + Google Search finds up to 4 decision makers (CEO/MD/Founder, CMO/Head of Marketing/CRM/Loyalty/CX/Digital) with work emails marked published vs pattern-guessed, company email format, domain MX check (dnspython). Runs automatically in prospect worker lanes after enrichment (50% of non-discovery slots) or via "Find emails". Loyalty fit score 0-100 (stores, segment, no existing loyalty, reachable contacts) on prospects + leads.
- core/outreach.py + routes/outreach.py: for fit >= min_score with an email, Gemini Flash drafts 3-step email sequence + LinkedIn connect note + follow-up; everything pending approval. Approved emails sent via active mailer (Outlook/Gmail) with signature, daily cap, follow-ups after configurable gaps, auto-stop when lead moves to Contacted/Converted/Closed; chatter log. LinkedIn = copy/open + mark sent (no API auto-send). Cron ai-outreach */15 -> /api/cron/outreach. UI /outreach approval queue + settings; prospect page Decision makers card.


## 2026-11-09
- Added daily cap on AI brand discovery (default 500 new brands/day, reset at midnight IST; setting key `daily_cap` in settings.prospecting). Enrichment/email-finding continue after the cap is hit. Verified via direct function call (cap=500, today=2211 → discovery paused).

## 2026-11-09
- Published to production: https://fundlecrm.fundle.ai (includes 500/day brand discovery cap)

## 2026-11-09 — Prospecting stalled (AI credits)
- Root cause: Emergent LLM key daily spend limit reached; enrich calls failed, brands wrongly marked failed (prod: 2,082)
- Fix: credit-limit errors stop the batch, keep brands pending (no attempts used), auto-reset previously failed brands each run, red alert + "Blocked — no AI credits" pill on Prospecting page. Needs publish to reach production.
- 2026-11-09: Auto-retry — each run requeues failed brands/contact lookups (credit errors at once, others after 6h, max 5 rounds)
- 2026-11-09: Nonstop engine — no daily cap, 4 news/LinkedIn discovery angles, hourly watchdog cron, 429/No-deployments treated as transient (no failed marking, Pro→Flash fallback, overloaded backoff)
- 2026-11-10: Prospecting scheduled nightly 10PM-9AM IST only (crons + code window); outreach auto-send (no approval), all brands with email, 100/day, CC vineet.narang@fundle.ai, run lock + atomic send claim + retry x3; replication prompt at /app/memory/AI_PROSPECTING_REPLICATION_PROMPT.md
- 2026-11-10: Contact finder searches LinkedIn profiles via Google, press releases, newspaper/magazine articles and appointment news
- 2026-11-10: Email template library (Intro auto; Marketplace/Loyalty/MBO/SIS manual), AI suggest/improve, attachments (<=3MB, object storage) + video links, per-contact click-to-tailor -> review -> Send now / Finalise & send, auto templates to all brands with email (tpl_sent dedupe), history per contact, lead activity log. Old 3-email AI sequence off (toggle), unsent old emails retired by migration v3.
- 2026-11-12: Engine 24h (day = contacts + research only, night = discovery too), contacts also for un-researched brands, 429 -> 60s pause not stop, Parallel up to 10; templates ai_tailor toggle ("sent as written"), AUTO EMAILER badge
- 2026-11-12: Multiple outreach inboxes (Admin → Email → Outreach inboxes): extra O365 SMTP boxes, 200/day each, rotation by most room, sticky per brand, failing inbox dropped from run + auto-unverified on auth errors
- 2026-11-12: Template edits flow into queued auto emails (tpl_version re-render), user signature (no default Team Fundle), Auto emailer bar + signature card on templates tab, "Sent emails" view; inbox warm-up 30->200 over 14 days; per-inbox report; reply/bounce detection via Graph Mail.Read per inbox (inbox_readers), replies stop auto emails + flag lead, bounces mark email bounced and re-find contacts then auto resend; contacts up to 6
- 2026-11-12: Mark-bounced bulk tool (exact match first, ambiguous/ignored reported), bounced brands get priority contact re-search + auto resend; every AI activity (research, contact search, outreach) upserts the lead (fills blanks only) and logs to lead timeline; read-replies Microsoft app setup card

## 2026-11-12 — LinkedIn one-click queue (Outreach → LinkedIn tab)
- Cookie/session or cloud-browser automation declined (LinkedIn ToS, ban risk). Hands-free option for later: paid service (HeyReach/Unipile) via their API.
- core/linkedin.py: AI fills missing LinkedIn profile URLs for found contacts (Flash + Google, 5/run), writes connection notes (≤300 chars) for top-fit brands with a profile (10/run, keeps 20 queued), first message on "accepted", AI answer to pasted replies; fallback text if AI fails. Runs inside the */15 outreach cron + "Fill queue now".
- routes/linkedin.py: GET /api/linkedin/queue?view=today|sent|accepted|replied|skipped, PATCH /tasks/{id}, POST /tasks/{id}/sent|accepted|reply|skip, PUT /settings (20 connects / 50 messages per day, enforced on "I sent it"), POST /run. Collection linkedin_tasks; prospect.li_task marks brand as tasked. Every step logged on the Lead timeline (escaped text). Separate from the email sequence.
- Tested via curl (full connect→accepted→message→reply→skip flow, limits, timeline) + screenshot.

## 2026-11-12 — Store-count intro emails, per-inbox signatures, email-format checker; LinkedIn OFF
- LinkedIn queue switched off at user request (settings.linkedin.enabled=false, tab removed). Code kept inert in core/linkedin.py, routes/linkedin.py. Gemini grounding cannot find LinkedIn URLs reliably; scraping search engines is blocked from the server.
- 4 auto intro templates (core/email_templates.INTRO_V2, seeded once via settings flag tpl_intro_v2, ai_tailor=false, editable): D2C segment → "Intro — D2C brands"; 100+ stores; 11–99 stores; 1–10 or unknown. Template field `audience` (all|d2c|stores_100|stores_11_99|stores_1_10). One intro per brand (run_auto + manual use-template 409). Old "Intro email" → manual.
- Signatures per sending inbox: mailboxes.signature (Abhinav's pre-filled for abhinav@ inboxes); main inbox uses settings.outreach.signature.
- core/email_format.py: every 15 min (with outreach cron) up to 30 brands, 6 lanes: AI Google search + existing published emails → confirm format → rebuild guessed/bounced emails (email_status confirmed_format), requeue brands with no contacts. Manual: POST /api/prospecting/prospects/{pid}/check-format; status GET /api/outreach/email-format.
- Tested: iteration_18 (backend 17/18 → fixed, frontend 100%) + follow-up fixes verified via curl.

## 2026-11-12 — Auto follow-ups + Abhinav signature fix
- Bug: prod emails had no signature (main inbox signature empty). Fix: seed_followups_v1 sets settings.outreach.signature = Abhinav's if empty; extra inboxes fall back to it when they have none.
- 8 follow-up templates (fu2_/fu3_<audience>, step 2 day 3 / step 3 day 7, subject "Re: {{intro_subject}}", ai_tailor false, editable incl. step + days). run_followups: same recipient + seq_id, skips replied/bounced, step 3 only after step 2 sent, no duplicates, scans all eligible intros (sorted by sent_at). Queued follow-ups keep intro subject after template edits.
- Follow-ups are new messages with "Re:" subject (not threaded via In-Reply-To headers).
- Tested: iteration_19 (14/14 after fixes, frontend 100%).

## 2026-11-12 — Contacts-only mode
- Prospecting phase "contacts" (button "Stop adding brands (contacts only)" + Mode option): no discovery, runs 24/7 (day mode all hours), contact finding first then deep research of already-added brands. Contacts + enrichment queues now go smallest store count first.
- Contact finder prompt also searches Indian retail trade media: Indian Retailer, RAI (STOrai), D2C Insider, Franchise India, ETRetail (sites + LinkedIn posts).
- Preview engine kept DISABLED (phase contacts, concurrency 10). Self-tested via API + screenshot.

## 2026-11-12 — Signature fix (per inbox, incl. tests)
- Test emails (main + extra inboxes) now include the inbox signature. Main inbox signature editable in Admin → Email → Outreach inboxes (primary-signature). Extra inbox signature saved on mailbox doc, kept on edits that omit it; fallback = main signature. iteration_20: 100%.

## 2026-11-12 — Hourly sending limit
- No daily limit / warm-up for sending. Each inbox (main + extra) sends max settings.outreach.hourly_cap (default 25, 1–100) per rolling hour, spread over the 4 runs/hour (≤7 per run). UI shows "X/25 this hour". iteration_21: 100%.

## 2026-11-12 — Fundle Brain (operator agent)
- /brain page (sidebar card "Fundle Brain", perm "brain"; admins always, others via Admin → Users permissions). ChatGPT-style sessions (brain_sessions, brain_messages), SSE streaming, tool chips, Audit log view (brain_audit: user, tool, args, reason, result).
- core/brain.py: GPT-6-Luna (openai gpt-6-luna) tool calling. Tools: get_system_overview, find_records, aggregate_records, update_records ($set/unset on prospects/leads/outreach/email_templates/mailboxes/settings, ≤5000, secrets blocked/masked), save_settings (outreach/prospecting/linkedin), save_email_template, send_email_campaign (kind "brain", force_sender pins inbox; from_inbox "main" = main), stop_emails, prioritize_contact_search, run_job, send_single_email, request_developer_change (brain_requests + email to vineet.narang@fundle.ai).
- Acts immediately but every write needs the user's own reason (server rejects missing/invented reasons). Can't change app code/screens — escalates.
- Tested: iteration_22, iteration_23 (20/20 pytest, live LLM flows pass) + follow-up minor fixes.

## 2026-11-12 — Fundle Brain data features
- Attach Excel/CSV in Brain chat (POST /api/brain/sessions/{sid}/upload → object storage + brain_files/brain_file_rows). Tools: read_uploaded_file, import_brands_from_file (new brands+leads, contacts_priority 1 → research → auto intro), import_contact_emails_from_file (verified/published, uploaded:true, contacts_status done, brand auto-created, lead email_from + timeline), manage_contact_sources (settings.contact_sources, defaults = 5 Indian trade sources; injected into contacts.find_contacts prompt).
- Drafts emails/campaigns in chat, saves/sends only after approval. Server guards: user's own "reason:/because" phrase is used as audit reason; send/run tools gated on the latest message (GATES); contacts_only_mode only if asked; rule: never change engines/settings unless asked.
- Tested: iteration_24, iteration_25 (45/45 pytest + live LLM turns).

## 2026-11-12 — Prod fixes: Brain sending, collapsible menu, AI model fallback
- Brain: direct send/stop/run orders need no extra reason (server sets "Instruction: <msg>"); settings/data/templates still need the user's WHY ("instruction:" reasons rejected there). send_single_email goes through outreach._send_one (signature, lead log, retry); new tools send_queued_emails_now, inbox_activity; clear "No usable inbox…" errors.
- Left menu collapsible (sidebar-toggle, localStorage crm_nav_mini); Brain chat list toggle (brain-chats-toggle).
- Prospecting stuck: Google rate-limits gemini-3.x models (not key balance). prospector._ask now falls back FLASH → gemini-3-flash-preview → gemini-2.5-flash and skips limited models for 30 min; banner shows provider's reason.
- PROD SMTP: deployer RCA — since the 19:26 redeploy, prod pod times out connecting to smtp.office365.com:587 (network/egress, not code). Abhinav's inbox had sent its 200/day earlier. Pending: user retry / platform egress check; option: send via Microsoft Graph (HTTPS) instead of SMTP.
- Tested: iteration_26, iteration_27 (65/65).

## 2026-11-12 — Multi-LLM web research (universal key)
- prospector._call supports "provider:model": gemini (googleSearch), anthropic (web_search_20250305, max_uses 8), openai (web_search_options — NOT available on universal key: gpt-5.5/4.1/6-luna rejected, gpt-4o-search-preview invalid → GPT not used for web research).
- _ask fallback chain on limits: requested → gemini-3.5-flash → gemini-3-flash-preview → gemini-2.5-flash → claude-sonnet-5-5 (limited models skipped 30 min).
- Contact finder CONTACT_CHAIN: claude-sonnet-5-5 (best real sources) → gemini-3.1-pro → gemini-2.5-flash → claude-opus-5-5; moves to next model when no emails found (max 3), merges contacts; prospects.contacts_models records models used.
- iter29 fixes: merge fills missing email/role into same-name contacts; per-model errors continue to next model (fail only if all fail); partial contacts saved if credits run out mid-chain; _ask(meta) records the model that actually answered (contacts_models) and the chain skips models already used. Tests: iteration_28/29 (41/41).

## 2026-06 Email-only outreach switch
- Migration outreach_v6: LinkedIn drafts (pending/approved/failed) -> stopped 'Switched to email outreach'; linkedin.enabled=False; outreach hourly_cap=50, auto_send on.
- Default hourly cap per inbox now 50.
- Pending: production SMTP timeout check (deployer debug requested).
- Fundle Brain export_csv tool: any collection+filter -> CSV (<=50k rows) stored in brain_exports; GET /api/brain/exports/{id}; Download CSV button in chat.
- Brain update_brands_from_file: fills only blank store_count/category/city/website/segment; adds unknown brands. Saved CSV files view (GET /api/brain/exports).
- Contact search rate-limit on prod: RCA requested from deployer (pending).
- Prod RCA: Gemini 429 'exceeded quota' on Emergent key + concurrency 10. Fix: lanes capped at 4, quota-limited models skipped 3h, exponential backoff (60s→300s) on AI limits.
