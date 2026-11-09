# Prompt — add "AI Prospecting" to the other CRM project

Copy everything below the line into the other Emergent project's chat.

---

This CRM is already live (React + FastAPI + MongoDB). Do NOT change any existing feature, data model or UI. ADD one new module, "AI Prospecting", that works non-stop in the background to find B2B companies, research them, find decision-maker contacts and add every company to Leads. Use the Emergent LLM key with Gemini (Flash for discovery, Pro for deep research, both with Google Search grounding) through emergentintegrations. Call the integration expert before writing the LLM code.

## 0. Ask me first (use ask_human)
1. Which industries to cover. I'll paste a list, e.g. Cement, Steel, Paints & Coatings, Pipes & Fittings, Wires & Cables, Tiles & Sanitaryware, Plywood & Laminates, Lubricants, Agrochemicals & Fertilisers, Tyres, Electricals & Switchgear, Building Materials, FMCG distribution. The list must also be editable later in Admin, not hard-coded.
2. Which countries. Default: India and UAE switched on; UK and USA available but off.
3. What we sell (a short pitch paragraph for outreach emails) and the email signature.

## 1. Data model (new collections only)
- `prospects`: id (int counter), company_name, company_key (normalised name, unique per country), country, industry, sub_segment, parent_group, brands [str] (product brands the company owns), website, hq_city, plants_or_branches (int), annual_revenue, employees, founded_year, dealer_distributor_network (str), company_linkedin, description, sources [urls], status ("discovered" | "enriching" | "enriched" | "failed"), enrich_attempts, failed_at, retry_rounds, last_error, contacts [ {name, role, department, linkedin, email, email_status "published"|"pattern", domain_accepts_mail, evidence_url} ], contacts_status, contacts_failed_at, contacts_retry_rounds, email_pattern, fit_score (0-100), lead_id, discovered_at, enriched_at.
- `prospect_cats`: {country, industry, runs, empty_streak, saturated, last_run, companies}, one per country + industry, used to rotate research angles.
- `prospect_runs`: a log of every AI call {at, kind discover|enrich|contacts|draft, model, ok, detail, secs, added, company}.
- `settings` docs: `prospecting` {enabled, countries, industries, phase auto|discovery|enrichment, concurrency 1-6, daily_cap (0 = unlimited)} and `prospect_lock` {until, started, trigger, last_finished, last_stats, ai_blocked_at, ai_blocked_reason, watchdog_at, watchdog_action}.

## 2. Engine (backend/core/prospector.py)
- **Discovery (Gemini Flash + Google Search):** for the next country + industry pair (fewest runs, then oldest run), ask for up to 25 real, currently operating companies, passing the last 300 known names so they aren't repeated. Rotate a research angle on every run:
  1. Industry associations and member directories (CII, FICCI, ASSOCHAM, Cement Manufacturers Association, Indian Steel Association, Indian Paint Association; Dubai Chamber and UAE Industrial Directory for UAE).
  2. BSE/NSE listed companies and annual reports.
  3. IndiaMART, TradeIndia and Justdial top manufacturers.
  4. Trade fairs and expo exhibitor lists.
  5. News of new plants, capacity expansions and acquisitions.
  6. Funding, IPO and M&A news.
  7. Regional and mid-size manufacturers that aren't nationally famous.
  8. LinkedIn posts by industry leaders, plus trade publications (ET Infra, Construction World, Steel 360, Paint India).
  9. Dealer and distributor network announcements.
  10. "Top companies" rankings and market reports.

  Insert each new company with an upsert on (country, company_key), and add a Lead for each one (source "AI Prospecting"). A category counts as saturated when 3 runs in a row add fewer than 3 companies and every angle has been used. Saturated categories are re-checked after 7 days.
- **Deep research (Gemini Pro + Google Search):** for each "discovered" company (largest first), fill in the model fields above, including the list of product BRANDS the company owns. Update the linked Lead.
- **Contact finder (Gemini Pro + Google Search, plus a DNS MX check on the domain):** find up to 6 decision makers, with work emails only if published, otherwise a pattern guess marked "pattern". The target roles, IN THIS PRIORITY:
  - CEO, MD or Founder
  - CMO or Head of Marketing
  - CTO, CIO or Head of IT/Digital
  - Head of Trade Marketing
  - Trade Marketing Manager(s) and other trade marketing / channel marketing / dealer engagement people
  - Head of Sales or Channel Sales

  Store each contact's department. Put the best email on the Lead.
- **Fit score:** 0-100, based on company size, dealer network size, a trade marketing contact found, a published email, and a LinkedIn profile.
- **Worker:** a lock doc stops batches overlapping. Each batch runs N parallel lanes until its deadline. Roughly 70% of steps are discovery (while under the cap), the rest are research or contacts. The finally block clears only the lock it took itself.
- **Resilience (must have):**
  - **AI limit errors:** treat as a temporary block, not a failure: spend limit, budget, insufficient balance/quota, "exceeded your current quota", RateLimitError, RESOURCE_EXHAUSTED, a word-boundary 429, "No deployments available". On these errors, stop the batch, set ai_blocked_at, keep the company "discovered" and don't count an attempt.
  - **Pro model fails with one of these errors:** retry once with Flash.
  - **"overloaded" or 503:** back off for 20s then 40s, then retry.
  - **Requeue at the start of every batch:** failed companies and contact lookups caused by AI limits go back in the queue straight away. Other failures are retried after 6 hours, up to 5 rounds.
  - **Stale "enriching" or "running" items:** reset after 10 minutes.

## 3. Scheduling (.emergent/crons.yml, ack 2xx immediately and do the work in the background, Bearer WEBHOOK_CRON_SECRET)
- `ai-prospecting`: `*/15 16-23,0-3 * * *` (UTC). The code only allows scheduled runs from 10 PM to 9 AM IST, and cuts each batch off at 9 AM. Manual "Run now" works at any time.
- `ai-prospecting-watchdog`: `7 16-23,0-3 * * *`. Frees a lock stuck for more than 20 minutes, and starts a batch if none has finished in the last 30 minutes.
- `ai-outreach`: `*/15 * * * *` (see section 5).

## 4. UI: new sidebar page "AI Prospecting" (admins and managers)
- **Engine panel:** status pill (Researching… / Idle / Sleeping — runs nightly 10 PM–9 AM IST / Waiting — AI limit, retrying / Paused), last batch stats, the number waiting for research, Run now, Pause/Resume, Mode, Parallel, Country chips, and an editable Industries list.
- **Red alert** when ai_blocked_at is set.
- **Stats:** companies found, deep researched, with contacts, trade marketing contacts found, CEO identified.
- **Industry grid:** companies and researched count per industry, plus a "DONE" badge when saturated.
- **Table:** filters (search, industry, country, status, min size, has trade marketing contact), sort (largest, best fit, newest, A-Z) and pagination.
- **Company detail page:** all fields, brands, the contacts table with email badges (published vs guessed), and buttons for Research again, Find contacts, Draft outreach, Open lead.
- **Activity log** of recent AI runs.

## 5. Outreach (page "Outreach")
- **Drafting:** for each company with a contact email, AI writes a 3-email sequence (intro of 120 words or less, follow-up after 3 days, break-up after 7 more days), plus a LinkedIn connection note and message. Address the best contact, preferring trade marketing, then marketing, then CEO.
- **Settings:** auto-send on/off (no approval when on), min fit score (0 = all), max emails per day (default 100), drafts per run, follow-up gaps, pitch, signature, and a CC address on every email.
- **Sending:** through the CRM's existing email provider (Outlook Graph/SMTP or Gmail), with CC supported. Log each sent email on the Lead. Stop a sequence when the Lead moves to Contacted, Converted or Closed.
- **Tabs:** Needs approval, Approved/scheduled, Sent, Failed, Stopped, Rejected, with edit, approve, reject and stop actions. LinkedIn drafts get "Copy & open LinkedIn" and "Mark sent".

## 6. Testing
Run the testing agent on: discovery and dedupe, research, contacts, AI-limit handling (nothing marked failed), requeue rules, the night window, the watchdog, cron 401 without the secret, auto-send with CC, and the UI flows. Keep existing CRM tests green.
