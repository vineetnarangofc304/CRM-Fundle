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
- P2: appointment_date/time fields kept in backend only (no UI label exists).
