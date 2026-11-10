# Prompt 1 — MarketplaceMetrics.ai: AI Prospecting + Email Outreach

Paste everything below the line into the marketplacemetrics.ai Emergent project.

---

This project is the marketplacemetrics.ai website (marketplace payout, commission, returns and fee reconciliation, plus SKU profitability, for brands selling on Amazon, Flipkart, Myntra, AJIO, Nykaa, Meesho, JioMart, Tata CLiQ, FirstCry, Blinkit/Zepto/Swiggy Instamart, Amazon.ae, Noon). Do NOT change the public site's design or content. ADD a private admin back office at /admin with two modules: **AI Prospecting**, which finds and researches sellers on these marketplaces, and **Email Outreach**, which sells them MarketplaceMetrics.

## 0. Ask me first (use ask_human)
1. Login for the admin area. Default: email + password (JWT, bcrypt), with a seeded admin from .env; admins can invite team members. Call the integration expert before writing any auth code. If the project already has a CRM or admin area, reuse its login, Leads and email setup instead.
2. Countries. Default: India on; UAE and Saudi Arabia (Amazon.ae, Noon) available but off.
3. Which email inbox sends outreach (Outlook / Microsoft 365 via Graph OAuth, or SMTP; Gmail optional), plus the CC address and the signature.
4. The pitch paragraph and any proof points (numbers, client names) the AI may use.

## 1. Data model
- **Collections:** `leads` (only if the project has no CRM), `prospects`, `prospect_cats`, `prospect_runs`, `email_templates`, `outreach`, `outreach_files`, `settings`. Use int ids from a counters collection, and soft delete.
- **`prospects`:**
  - Identity: brand_name, brand_key (normalised; unique per country), legal_entity, category (see section 2), country, website, hq_city.
  - Marketplaces: `marketplaces` [ {name, store_url, rating, review_count, est_sku_count, est_monthly_gmv_band, seller_type "brand-owned"|"authorised seller"|"aggregator"} ], marketplace_count.
  - Business details: own D2C site (bool), offline stores, est_annual_revenue_band, employees, funding, founded_year, description, sources [urls].
  - Pain signals [str]: e.g. sells on 4+ marketplaces, high return category (fashion, footwear), fast SKU growth, recent funding, hiring for marketplace finance or reconciliation, reviews mentioning late payouts.
  - Research status: status discovered|enriching|enriched|failed, enrich_attempts, failed_at, retry_rounds, last_error.
  - Contacts: contacts [ {name, role, department, linkedin, email, email_status published|pattern, domain_accepts_mail, evidence_url} ], contacts_status, contacts_failed_at, contacts_retry_rounds, email_pattern.
  - Other: fit_score 0-100, lead_id, tpl_sent [template ids], discovered_at, enriched_at.

## 2. Prospecting engine (Gemini via the Emergent LLM key and emergentintegrations: Flash for discovery, Pro for research, both with Google Search grounding; call the integration expert first)
- **Categories (editable in admin):** Fashion & Apparel, Ethnic Wear, Footwear, Beauty & Personal Care, Skincare D2C, Fragrances, Jewellery & Accessories, Watches & Eyewear, Bags & Luggage, Home & Kitchen, Home Décor & Furnishing, Appliances, Electronics & Accessories, Mobile Accessories, Health & Nutrition, Ayurveda & Wellness, Baby & Kids, Toys, Sports & Fitness, Pet Care, Food & Beverages (packaged), Stationery & Books, Automotive Accessories, Grocery & FMCG.
- **Discovery:** each run takes the next country + category pair (fewest runs, then oldest) and asks for up to 25 real brands actively selling on 2 or more of the marketplaces, passing the last 300 known names so they aren't repeated. Rotate these angles:
  1. Amazon and Flipkart best-seller and "Top brands" pages per category.
  2. Myntra, AJIO and Nykaa brand directories.
  3. Amazon Launchpad and Flipkart Samarth/Leap programmes.
  4. Shark Tank India brands.
  5. D2C funding news (Inc42, YourStory, Entrackr, VCCircle).
  6. Lists like "Top D2C brands", including D2C Insider and Brand Stack.
  7. Quick-commerce (Blinkit, Zepto, Instamart) brand launches.
  8. LinkedIn posts by e-commerce heads and founders.
  9. Marketplace seller awards (Amazon Smbhav, Flipkart awards).
  10. Regional and mid-size sellers with strong marketplace ratings.

  Upsert each brand on (country, brand_key) and create a Lead (source "AI Prospecting"). A category counts as saturated after 3 runs in a row that each add fewer than 3 brands, once every angle has been used. Re-check saturated categories after 7 days. There is no daily cap (it's a setting; 0 = unlimited).
- **Deep research (Pro):** fill in every field above, including each marketplace store URL, an estimate of SKU count, pain signals and fit score. Weight the fit score by marketplace_count, the GMV band, a high-return category and the pain signals.
- **Contact finder (Pro + Google Search + a DNS MX check):** run several searches:
  - LinkedIn profiles via Google (`site:linkedin.com/in "<brand>" "e-commerce"`, and the same for each role), plus the company's LinkedIn People section.
  - Press releases, appointment news ("appointed as", "joins as") and interviews in ET Retail, ET BrandEquity, Inc42, YourStory, Mint, Business Standard, Afaqs and exchange4media.
  - The company's team pages, and RocketReach, ZoomInfo and Apollo snippets.

  Target roles, in priority order:
  - Founder or CEO
  - CFO, Finance Head or Finance Controller
  - Head of E-commerce or Marketplaces
  - Marketplace or Key Account Manager (Amazon, Flipkart)
  - Head of Online Sales or Growth
  - CTO or Head of Tech

  Collect up to 6 contacts, with a work email if it's published, otherwise a guess from the email pattern marked "pattern". Never invent people.
- **Worker:** a lock doc stops batches overlapping (the finally block clears only its own lock). N parallel lanes run until the deadline: about 70% discovery, the rest research or contacts.
- **Resilience:**
  - **AI-limit errors** (spend limit, budget, insufficient balance or quota, "exceeded your current quota", RateLimitError, RESOURCE_EXHAUSTED, a word-boundary 429, "No deployments available") are a temporary block: stop the batch, set ai_blocked_at, and don't mark anything failed.
  - **Pro fails:** fall back to Flash once.
  - **"overloaded" or 503:** back off 20s, then 40s.
  - **Requeue at every batch start:** AI-limit failures immediately; other failures after 6 hours, up to 5 rounds. Reset stale "enriching" items after 10 minutes.

## 3. Email outreach
- **Templates** (`email_templates`: name, subject, body HTML with {{first_name}} {{brand}} {{role}}, mode auto|manual, active, attachments [file ids], video_links). Seed these:
  1. Intro (auto): "Is {{brand}} recovering every rupee from Amazon, Flipkart and Myntra?"
  2. Finance/CFO emailer: payout reconciliation, recovered claims, the Morning Finance Brief.
  3. E-commerce head emailer: SKU-level true profit, returns and fee leakage.
  4. Fashion returns emailer: return and RTO claim recovery.
  5. Multi-marketplace emailer: one dashboard for 5+ marketplaces.
  6. Follow-up / bump.
- **Templates UI:** an "AI suggest / AI improve" button, a live preview, file attachments (PDF/PNG/JPG/PPTX/DOCX, 3 MB total, stored in Emergent Object Storage; call the integration expert) and video links (sent as links).
- **Brand page:** each contact gets a button for every active template. Clicking one has AI tailor that template to the person and brand (keeping the offer, links and length), and adds it to "Needs review". From there you can edit it and click **Send now**, or select several and **Finalise & send**.
- **Auto templates** go once per brand (tracked with tpl_sent) to every brand with an email, within the daily limit (default 100) and with CC. Rejecting a message frees that template for the brand again.
- **Sending:** through Outlook Graph or SMTP (Gmail optional), with CC and attachments (Graph fileAttachment / MIME). A run lock plus an atomic approved→sending claim means nothing goes out twice. A failed send is retried 3 times, 1 hour apart. Every sent email is logged on the Lead (template name, recipient, attachments), and each contact shows a history of what was sent and when.
- **Outreach page tabs:** Needs review, Approved/scheduled, Sent, Failed, Stopped, Rejected, plus an Email templates tab. Settings: enabled, auto-send, max emails per day, CC, signature, pitch.

## 4. Scheduling (.emergent/crons.yml; every endpoint replies 2xx immediately and does the work in the background; Bearer WEBHOOK_CRON_SECRET; 401 otherwise)
- `ai-prospecting`: `*/15 16-23,0-3 * * *` UTC. The code only allows scheduled runs from 10 PM to 9 AM IST and cuts each batch off at 9 AM. Manual "Run now" works at any time.
- `ai-prospecting-watchdog`: `7 16-23,0-3 * * *`. Frees a lock stuck for more than 20 minutes and restarts the engine if it has been idle for 30 minutes.
- `ai-outreach`: `*/15 * * * *`.

## 5. Admin UI (/admin, a clean dashboard look separate from the marketing site)
- **AI Prospecting page:**
  - Engine panel with a status pill: Researching / Idle / Sleeping 10 PM–9 AM IST / Waiting — AI limit / Paused. It also shows the last batch, how many are waiting, and controls for Run now, Pause, Mode, Parallel, Countries and Categories.
  - A red alert when the AI is blocked.
  - Stats: brands found, researched, with contacts, on 3+ marketplaces, with a finance contact.
  - A category grid.
  - A table with filters (marketplace, category, number of marketplaces, GMV band, fit, status, has email) and sorting.
  - A brand detail page with marketplace store links, contacts, template buttons and email history.
- **Leads page:** each lead's activity timeline.

## 6. Testing
Run the testing agent on:
- Discovery dedupe, research, contacts.
- AI-limit handling (nothing marked failed), requeue rules, the night window, the watchdog, and the cron endpoints (401 without the secret).
- The template CRUD, the AI suggest button, attachments, click → review → send, auto-send with CC and dedupe, and the lead activity log.
- Admin auth.
