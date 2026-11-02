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
