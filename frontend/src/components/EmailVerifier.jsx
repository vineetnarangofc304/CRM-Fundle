import React, { useEffect, useState } from "react";
import { toast } from "sonner";
import { API, apiErr } from "../lib/api";

const RES = { delivered: "Kept (delivered)", dropped: "Removed (another guess won)", bounced: "Bounced", undefined: "Waiting (6 h window)", null: "Waiting (6 h window)", cancelled: "Cancelled" };
const Stat = ({ l, v, id }) => <div className="crm-card p-3"><div className="text-[11px] text-slate-500">{l}</div><div className="font-display text-xl font-extrabold text-[#1557B0]" data-testid={id}>{(v || 0).toLocaleString("en-IN")}</div></div>;

export function EmailVerifier() {
  const [d, setD] = useState(null);
  useEffect(() => { API.get("/outreach/verifier", { timeout: 60000 }).then(({ data }) => setD(data)).catch((e) => toast.error(apiErr(e))); }, []);
  if (!d) return <p className="text-sm text-slate-400">Loading…</p>;
  const s = d.probe_by_status;
  return (
    <div className="space-y-4" data-testid="verifier-panel">
      <p className="text-xs text-slate-500">Every 15 minutes the CRM reads the inboxes below for "Undeliverable" replies. A guessed address that bounces is marked bounced; when no bounce arrives for 6 hours the address is kept on the contact and the other guesses are removed. If all 3 bounce, the next 3 combinations are tried. Last check: <b>{d.last_check || "—"}</b>{d.last_stats?.verifier ? ` · ${d.last_stats.verifier.verified} verified, ${d.last_stats.verifier.retry} retrying, ${d.last_stats.verifier.exhausted} with no working email` : ""}</p>
      <div className="crm-card p-4" data-testid="verifier-readers"><b className="text-sm">Inboxes being read</b>
        {d.readers.length === 0 && <p className="mt-2 text-xs text-rose-600" data-testid="verifier-no-readers">No inbox is being read — bounces can't be detected. Connect reading in Admin → Outreach inboxes (Microsoft sign-in or app password).</p>}
        {d.readers.map((r, i) => <div key={i} className="flex flex-wrap gap-2 border-t border-slate-100 py-1.5 text-xs" data-testid={`verifier-reader-${i}`}><b>{r.inbox}</b><span className="text-slate-500">{r.how}</span><span className="text-slate-500">last read {r.last_read || "never"}</span>
          <span className={`rounded-full px-2 font-bold ${r.error ? "bg-rose-50 text-rose-700" : "bg-emerald-50 text-emerald-700"}`}>{r.error ? `Error: ${r.error}` : "OK"}</span></div>)}</div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Stat l="Test emails sent" v={(s.sent || 0) + (s.bounced || 0)} id="verifier-sent" /><Stat l="Bounced (undelivered)" v={s.bounced} id="verifier-bounced" />
        <Stat l="Waiting to send" v={(s.approved || 0) + (s.sending || 0)} id="verifier-queued" /><Stat l="Addresses kept" v={d.probe_by_result.delivered} id="verifier-kept" />
        <Stat l="Contacts being tested" v={d.contacts.testing} id="verifier-testing" /><Stat l="Contacts verified" v={d.contacts.delivered} id="verifier-verified" /></div>
      <div className="crm-card overflow-x-auto p-4"><b className="text-sm">Latest test emails</b>
        <table className="mt-2 w-full text-xs"><thead className="text-left text-[10px] uppercase text-slate-400"><tr><th className="py-1">Brand / person</th><th>Address tried</th><th>From</th><th>Sent</th><th>Result</th></tr></thead>
          <tbody>{d.recent.map((m) => <tr key={m.id} className="border-t border-slate-100" data-testid={`verifier-row-${m.id}`}><td className="py-1.5"><b>{m.brand}</b> · {m.contact_name}</td><td>{m.to}</td><td className="text-slate-500">{m.from}</td><td className="text-slate-500">{m.sent_at}</td>
            <td><span className={`rounded-full px-2 py-0.5 font-bold ${m.status === "bounced" ? "bg-rose-50 text-rose-700" : m.probe_result === "delivered" ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600"}`}>{m.status === "bounced" ? `Bounced ${m.bounced_at || ""}` : RES[m.probe_result]}</span></td></tr>)}
            {!d.recent.length && <tr><td colSpan={5} className="py-6 text-center text-slate-400">No test emails sent yet.</td></tr>}</tbody></table></div>
    </div>
  );
}
