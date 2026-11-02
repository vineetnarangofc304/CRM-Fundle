# Fundle.ai CRM — Website Lead API

Send every website / landing-page / chatbot enquiry straight into the CRM.

## Endpoint
```
POST https://<CRM-DOMAIN>/api/webhook/lead/<TOKEN>
Content-Type: application/json        (application/x-www-form-urlencoded also accepted)
```
- `<TOKEN>`: from CRM → Admin → Webhooks → "Fundle.ai Website" → Copy URL. The token is the only credential, so keep it server-side if you can.
- No login or header is needed. A wrong or deactivated token returns `404`.

## Body (send whatever you have — only phone OR email is required)
| Field (any alias works) | CRM field |
|---|---|
| `name`, `full_name` | Name |
| `phone`, `mobile`, `whatsapp` | Phone (any format, e.g. `+91 98111 22334`) |
| `email` | Email |
| `company`, `company_name`, `organization` | Company |
| `designation`, `job_title`, `role` | Designation |
| `team_size`, `company_size`, `employees` | Team Size (e.g. `51-200`) |
| `deal_value`, `expected_value` | Deal Value (₹, number or `2,50,000`) |
| `message`, `query`, `question` | Query |
| `remark`, `notes` | Remark |
| `city`, `state`, `country` | Location |
| `source` | Source (defaults to `Website Form`; e.g. `Website Chat`, `Free Trial Signup`) |
| `utm_source`, `utm_medium`, `utm_campaign` | UTM (also read from the `page_url` query string or from the webhook URL's own query) |
| `page_url`, `landing_page`, `referrer` | Conversion page |
| `industry`, `product_interest`, `budget`, `timeline`, `decision_role`, `current_solution`, `annual_revenue`, `pain_points` | Matching qualification fields |

Extra keys are not lost: they are saved on the lead and shown in the Q&A card.

## Example
```bash
curl -X POST "https://<CRM-DOMAIN>/api/webhook/lead/<TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Priya Sharma",
    "phone": "+91 98765 43210",
    "email": "priya@acme.in",
    "company": "Acme Technologies",
    "job_title": "CTO",
    "company_size": "51-200",
    "industry": "BFSI",
    "product_interest": "AI Sales Agent",
    "message": "Want a demo of the AI sales agent",
    "source": "Website Form",
    "page_url": "https://fundle.ai/pricing?utm_source=google&utm_medium=cpc&utm_campaign=ai-agent"
  }'
```

```js
// Node / Next.js server action or API route
await fetch(process.env.FUNDLE_CRM_WEBHOOK_URL, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ name, phone, email, company, message, page_url: window.location.href }),
});
```

## Responses
| Status | Body | Meaning |
|---|---|---|
| 200 | `{"ok": true, "lead_id": 500123}` | New lead created and auto-assigned to a caller (round-robin) |
| 200 | `{"ok": true, "lead_id": 500123, "duplicate": true, "merged_same_day": true}` | The same phone already sent an enquiry today, so it was added to that lead instead of creating a new one |
| 422 | `{"detail": "A valid phone (or email) is required"}` | Ask the visitor for a phone or email |
| 404 | `{"detail": "Webhook not found"}` | Wrong or deactivated token |

## What happens in the CRM
- The lead appears instantly in Leads with source, UTM and conversion page filled in.
- It is round-robin assigned to an available caller, and your new-lead automations run (e.g. a welcome WhatsApp).
- If Meta CAPI is set up, a "Lead" event is sent to Meta.
- If the same phone came in on an earlier day, the new lead is flagged as a duplicate and linked to the older one.
- Admin → Webhooks → "Last payload" shows exactly what the website sent last.
