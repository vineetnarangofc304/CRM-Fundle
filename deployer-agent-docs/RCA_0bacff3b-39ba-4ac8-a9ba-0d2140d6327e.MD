# Fundle CRM — Outreach/Prospecting Data Audit (read-only)

Deployment: fundle-sales | DB: fundle-leads-test_database | active run 0bacff3b-39ba-4ac8-a9ba-0d2140d6327e
**Current time: 2026-11-12 09:54:14 UTC**
All `at`/timestamp fields in this DB are UTC strings (e.g. outlook.connected_at = "2026-11-10 11:49:18" matches the reported connect time).

## (1) settings docs
- **outreach_v3**: EXISTS — `{key:"outreach_v3", done:true}` (one-time migration/flag marker, no other fields).
- **outreach** settings doc:
  - auto_send: **true**
  - ai_sequence: **false**
  - daily_cap: **100**
  - cc: **vineet.narang@fundle.ai**
  - last_run: **field NOT present on the `outreach` doc.** Closest is a separate `outreach_last` doc → at="2026-11-12 09:31:38", trigger="cron", stats {auto_approved:0, auto_template:0, drafted:0, failed:0, sent:0}.
  - (other fields: enabled:true, drafts_per_run:30, gaps:[3,7], min_score:0, pitch+signature set)

## (2) email_templates (5 docs; all active:true)
| name | mode | active | subject |
|---|---|---|---|
| Intro email | auto | true | {{brand}} x Fundle.AI \| Demo Request – AI Powered Unified Loyalty OS |
| Marketplace emailer | manual | true | Is {{brand}} recovering every rupee from Amazon, Flipkart and Myntra? |
| Loyalty emailer | manual | true | Bringing {{brand}}'s customers back more often |
| MBO emailer | manual | true | Daily sell-through from every MBO counter for {{brand}} |
| SIS emailer | manual | true | Shop-in-shop sales visibility for {{brand}}, every morning |

Only "Intro email" is mode=auto → it is the template auto-send uses.

## (3) outreach, channel='email' grouped by kind / template_name / status (total 2063)
All docs are kind=`template`.
- kind=template · template_name="Intro email" · status=**approved** → **336**
- kind=template · template_name="Intro email" · status=**sent** → **302**
- kind=template · template_name=(null/none) · status=**stopped** → **1425**
(638 docs carry template_name "Intro email"; the 1425 stopped docs have no template_name.)

## (4) last 10 sent outreach docs (sent_at desc) — all kind=template, template_name="Intro email", step=1
1. 2026-11-12 06:42:00 · approved_by=**Fundle.ai Admin** · to=alexander.tang@xiaomi.com · subj "Xiaomi India x Fundle.ai — retail intelligence across stores, malls and marketplaces" · body(300): "<p>Hi Alexander,</p><p>I lead partnerships at Fundle.ai. As Xiaomi India expands its consumer durables ecosystem with smart home appliances across online and offline channels, unified tracking becomes essential. We build AI-powered retail intelligence that connects customers, stores, malls and marketpl"
2. 2026-11-12 00:13:13 · approved_by=**Auto-send** · to=anand.narang@manyavar.com · subj "Manyavar x Fundle.ai —…" · body(300): "<p>Hi Anand,</p><p>I lead partnerships at Fundle.ai. With Manyavar operating hundreds of exclusive brand outlets alongside a major online marketplace presence for men's celebration wear, managing unified channel data at scale is essential. We build AI-powered retail intelligence that connects customer"
3. 2026-11-12 00:13:06 · **Auto-send** · charath.narsimhan@indianterrain.com · "Indian Terrain x Fundle.ai —…" · "<p>Hi Charath,</p><p>I lead partnerships at Fundle.ai. We build AI-powered retail intelligence that connects customers, stores, malls and marketplaces, so brands like Indian Terrain see sales, payouts and customer behaviour in one place.</p><p>Given Indian Terrain's extensive distribution footprint ac"
4. 2026-11-12 00:12:59 · **Auto-send** · purvaa.kapadia@blackberrys.com · "Blackberrys x Fundle.ai —…" · "<p>Hi Purvaa,</p><p>I lead partnerships at Fundle.ai. We build AI-powered retail intelligence that connects customers, stores, malls and marketplaces, so brands like Blackberrys see sales, payouts and customer behaviour in one place. With your network of over 350 exclusive stores and strong e-commerce"
5. 2026-11-12 00:12:53 · **Auto-send** · vaibhav.annam@abfrl.adityabirla.com · "Peter England x Fundle.ai —…" · "<p>Hi Vaibhav,</p><p>I lead partnerships at Fundle.ai. We build AI-powered retail intelligence that connects customers, stores, malls and marketplaces, so brands like Peter England see sales, payouts and customer behaviour in one place.</p><p>Given your network of over 1,000 exclusive stores across 8"
6. 2026-11-12 00:12:45 · **Auto-send** · raghuvar.seth@abfrl.adityabirla.com · "Allen Solly x Fundle.ai —…" · "<p>Hi Raghuvar,</p><p>I lead partnerships at Fundle.ai. We build AI-powered retail intelligence that connects customers, stores, malls and marketplaces, so brands like Allen Solly see sales, payouts and customer behaviour in one place. With Allen Solly operating over 600 physical stores alongside a st"
7. 2026-11-12 00:12:38 · **Auto-send** · kalpana.singh@raymond.in · "Raymond x Fundle.ai —…" · "<p>Hi Kalpana,</p><p>I lead partnerships at Fundle.ai. We build AI-powered retail intelligence that connects customers, stores, malls and marketplaces, so brands like Raymond see sales, payouts and customer behaviour in one place. With Raymond operating over 1,600 physical locations alongside a strong"
8. 2026-11-12 00:12:31 · **Auto-send** · keyur@apsaraicecreams.com · "Apsara Ice Cream x Fundle.ai —…" · "<p>Hi Keyur,</p><p>I lead partnerships at Fundle.ai. We build AI-powered retail intelligence that connects customers, stores, malls and marketplaces, so brands like Apsara Ice Cream see sales, payouts and customer behaviour in one place.</p><p>With Apsara Ice Cream operating over 150 outlets alongside"
9. 2026-11-12 00:12:25 · **Auto-send** · amit@byshree.com · "Shree x Fundle.ai —…" · "<p>Hi Amit,</p><p>I lead partnerships at Fundle.ai. We build AI-powered retail intelligence that connects customers, stores, malls and marketplaces, so brands like Shree see sales, payouts and customer behaviour in one place. With Shree operating over 130 physical outlets alongside a strong e-commerce"
10. 2026-11-12 00:12:19 · **Auto-send** · ashwin@ivoomi.co · "iVoomi Energy x Fundle.ai —…" · "<p>Hi Ashwin,</p><p>I lead partnerships at Fundle.ai. We build AI-powered retail intelligence that connects customers, stores, malls and marketplaces, so brands like iVoomi Energy see sales, payouts and customer behaviour in one place. With iVoomi Energy distributing across over 100 physical dealershi"

NOTE: 9 of the last 10 sends (the 2026-11-12 00:12–00:13 batch) carry approved_by="Auto-send"; the most recent (06:42) was approved_by="Fundle.ai Admin". This confirms automatic sending is active, consistent with outreach.auto_send=true + "Intro email" mode=auto.

## (5) prospects (total 12515)
- grouped by contacts_status: **done = 637**; **field absent/none = 11878** ("done" is the only distinct value present).
- status='enriched' total = **679**
  - with no contacts array OR empty contacts: **44**
  - with ≥1 contact email: **635**  (44 + 635 = 679)

## (6) prospect_runs since 2026-11-09 16:30 UTC (total 2529) — grouped by kind & ok, avg secs
| kind | total | ok=true | ok=false | avg secs |
|---|---|---|---|---|
| discover | 1713 | 739 | 974 | **35.7** (ok=true 61.2s / ok=false 16.35s) |
| enrich | 384 | 158 | 226 | **20.9** |
| contacts | 251 | 164 | 87 | **32.15** |
| draft | 181 | 181 | 0 | **11.51** |

Averages computed exactly in a sandbox (mongo_query has no aggregate): all secs values pulled and mean()'d.

## (7) prospecting settings doc
- enabled: **true**
- concurrency: **3**
- phase: **auto**
- (countries:["India","United Kingdom"], create_leads:true)

## Supporting context (not asked, but relevant)
- settings.outlook: email="Abhinav.Khanna@fundle.ai", method=smtp, smtp_host=smtp.office365.com, smtp_port=587, smtp_verified=**true**, connected_at="2026-11-10 11:49:18". settings.email_provider.provider="outlook".
- settings.prospect_lock shows ai_blocked_at="2026-11-11 21:05:18", reason=Gemini 429 RateLimitError — likely explains a share of the ok=false discover/enrich runs.

No application errors investigated here; this was a read-only data inventory per request. App is live (active run success).
