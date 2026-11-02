import React, { useEffect, useState } from "react";
import { toast } from "sonner";
import { FacebookLogo, PaperPlaneTilt, ArrowsClockwise } from "@phosphor-icons/react";
import { API, apiErr } from "../lib/api";
import { useCatalogs } from "../context/AuthContext";
import { Spinner } from "./Bits";

const STD_EVENTS = ["Purchase", "Schedule", "CompleteRegistration", "Lead", "Contact", "SubmitApplication"];
const Label = ({ children }) => <label className="text-[10px] font-bold uppercase tracking-wider text-slate-400">{children}</label>;

function StatusHeader({ s }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <FacebookLogo size={20} weight="fill" className="text-[#1877F2]" />
      <h3 className="font-display text-sm font-extrabold text-slate-800">Meta Conversions API</h3>
      <span data-testid="capi-status-badge" className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${s.configured && s.config.enabled ? "bg-emerald-50 text-emerald-600" : "bg-amber-50 text-amber-600"}`}>
        {!s.configured ? "NOT CONFIGURED" : s.config.enabled ? "LIVE" : "PAUSED"}
      </span>
      <span className="text-xs text-slate-500" data-testid="capi-dataset-id">Dataset <b>{s.dataset_id || "—"}</b></span>
      <span className="ml-auto flex gap-3 text-xs" data-testid="capi-stats">
        <span className="text-emerald-600"><b>{s.stats_7d.sent}</b> sent</span>
        <span className="text-rose-500"><b>{s.stats_7d.failed}</b> failed</span>
        <span className="text-slate-400">last 7 days</span>
      </span>
    </div>
  );
}

function Toggle({ label, checked, onChange, testid, disabled }) {
  return (
    <label className={`flex items-center gap-2 text-sm text-slate-700 ${disabled ? "opacity-50" : ""}`}>
      <input type="checkbox" data-testid={testid} checked={!!checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} className="h-4 w-4 accent-[#1A73E8]" />
      {label}
    </label>
  );
}

function ConfigForm({ s, isAdmin, onSaved }) {
  const { catalogs } = useCatalogs();
  const stages = (catalogs?.lead_stage || []).map((x) => x.name);
  const [f, setF] = useState(s.config);
  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }));
  const save = async () => {
    try {
      const body = { ...f, conversion_value: f.conversion_value === "" || f.conversion_value == null ? null : Number(f.conversion_value) };
      await API.patch("/admin/meta-capi", body); toast.success("Meta CAPI settings saved ✓"); onSaved();
    } catch (e) { toast.error(apiErr(e)); }
  };
  return (
    <div className="mt-4 grid gap-4 md:grid-cols-2" data-testid="capi-config-form">
      <div className="space-y-3 rounded-xl border border-slate-100 p-3">
        <Toggle label="Send events to Meta" checked={f.enabled} onChange={set("enabled")} testid="capi-enabled" disabled={!isAdmin} />
        <div>
          <Label>Conversion = when lead stage becomes</Label>
          <select data-testid="capi-conversion-stage" className="crm-input mt-1" disabled={!isAdmin} value={f.conversion_stage} onChange={(e) => set("conversion_stage")(e.target.value)}>
            {[...new Set([f.conversion_stage, ...stages])].map((x) => <option key={x}>{x}</option>)}
          </select>
        </div>
        <div>
          <Label>Meta event sent on conversion</Label>
          <input list="capi-std-events" data-testid="capi-conversion-event" className="crm-input mt-1" disabled={!isAdmin} value={f.conversion_event_name} onChange={(e) => set("conversion_event_name")(e.target.value)} />
        </div>
        <div className="flex gap-2">
          <div className="flex-1"><Label>Value (optional)</Label>
            <input type="number" min="0" data-testid="capi-conversion-value" className="crm-input mt-1" disabled={!isAdmin} placeholder="e.g. 5000" value={f.conversion_value ?? ""} onChange={(e) => set("conversion_value")(e.target.value)} /></div>
          <div className="w-24"><Label>Currency</Label>
            <input data-testid="capi-currency" className="crm-input mt-1" disabled={!isAdmin} value={f.currency || ""} onChange={(e) => set("currency")(e.target.value.toUpperCase())} /></div>
        </div>
        {(f.conversion_event_name || "").trim().toLowerCase() === "purchase" && !Number(f.conversion_value) && (
          <p className="text-xs font-semibold text-rose-600" data-testid="capi-value-warning">
            Meta ignores Purchase events without a value. Enter your average conversion amount (e.g. average deal / subscription value), or pick another event such as Schedule.
          </p>
        )}
      </div>
      <div className="space-y-3 rounded-xl border border-slate-100 p-3">
        <Toggle label="Also send an event for every NEW lead" checked={f.send_lead_event} onChange={set("send_lead_event")} testid="capi-send-lead-event" disabled={!isAdmin} />
        <div>
          <Label>Meta event sent on new lead</Label>
          <input list="capi-std-events" data-testid="capi-lead-event" className="crm-input mt-1" disabled={!isAdmin || !f.send_lead_event} value={f.lead_event_name} onChange={(e) => set("lead_event_name")(e.target.value)} />
        </div>
        <div>
          <Label>Default phone country code</Label>
          <input data-testid="capi-country-code" className="crm-input mt-1" disabled={!isAdmin} value={f.phone_country_code || ""} onChange={(e) => set("phone_country_code")(e.target.value)} />
        </div>
        <Toggle label={`Route events to Meta "Test events" only${s.test_code_set ? "" : " (add a Test Event Code first)"}`} checked={f.use_test_code} onChange={set("use_test_code")} testid="capi-use-test-code" disabled={!isAdmin || !s.test_code_set} />
      </div>
      <datalist id="capi-std-events">{STD_EVENTS.map((x) => <option key={x} value={x} />)}</datalist>
      {isAdmin && <div className="md:col-span-2"><button onClick={save} className="crm-btn-primary !py-2" data-testid="capi-save">Save settings</button></div>}
    </div>
  );
}

function TestPanel({ onSent, testCodeSet }) {
  const [leadId, setLeadId] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const { data } = await API.post("/admin/meta-capi/test", leadId ? { lead_id: Number(leadId) } : {});
      data.status === "sent" ? toast.success(`Meta received the test event ✓ (trace ${data.fbtrace_id})`) : toast.error(`Meta rejected it: ${data.error}`, { duration: 10000 });
      onSent();
    } catch (e) { toast.error(apiErr(e)); }
    setBusy(false);
  };
  return (
    <div className="mt-4 flex flex-wrap items-end gap-2 rounded-xl bg-[#1A73E8]/5 p-3">
      <div className="min-w-[200px] flex-1">
        <Label>{testCodeSet ? "Send test \"Lead\" — shows in Meta → Test events (not counted in ads) — optional lead #" : "Credentials check only (add a Test Event Code to see it in Meta → Test events) — optional lead #"}</Label>
        <input data-testid="capi-test-lead-id" className="crm-input mt-1" placeholder="Leave blank for sample data" value={leadId} onChange={(e) => setLeadId(e.target.value.replace(/\D/g, ""))} />
      </div>
      <button onClick={run} disabled={busy} className="crm-btn-secondary inline-flex items-center gap-1 !py-2" data-testid="capi-send-test">
        <PaperPlaneTilt size={15} /> {busy ? "Sending…" : "Send test event"}
      </button>
    </div>
  );
}

const canResend = (r) => r.status !== "sent" || (r.value == null && !(r.event_id || "").includes("_r"));

async function resendLead(leadId, onDone) {
  if (!window.confirm(`Resend the conversion for lead #${leadId}? Only do this if Meta did not receive it (e.g. it was dropped for a missing value).`)) return;
  try {
    const { data } = await API.post(`/admin/meta-capi/resend/${leadId}`);
    data?.status === "sent" ? toast.success(`Conversion for #${leadId} resent to Meta ✓`) : toast.error(data?.error || "Not sent", { duration: 10000 });
    onDone();
  } catch (e) { toast.error(apiErr(e), { duration: 10000 }); }
}

function ResendByLead({ onDone }) {
  const [leadId, setLeadId] = useState("");
  return (
    <div className="mt-2 flex flex-wrap items-end gap-2 rounded-xl bg-amber-50/60 p-3">
      <div className="min-w-[200px] flex-1">
        <Label>Resend a conversion Meta dropped (e.g. sent before a value was set) — lead #</Label>
        <input data-testid="capi-resend-lead-id" className="crm-input mt-1" placeholder="e.g. 572472" value={leadId} onChange={(e) => setLeadId(e.target.value.replace(/\D/g, ""))} />
      </div>
      <button disabled={!leadId} onClick={() => resendLead(Number(leadId), onDone)} className="crm-btn-secondary !py-2" data-testid="capi-resend-lead-btn">Resend conversion</button>
    </div>
  );
}

function EventLog({ rows, onRefresh, isAdmin }) {
  const resend = (leadId) => resendLead(leadId, onRefresh);
  return (
    <div className="crm-card p-4">
      <div className="flex items-center gap-2">
        <h3 className="font-display text-sm font-extrabold text-slate-800">Recent events</h3>
        <button onClick={onRefresh} className="ml-auto text-slate-400 hover:text-[#1557B0]" data-testid="capi-refresh"><ArrowsClockwise size={16} /></button>
      </div>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left text-xs" data-testid="capi-events-table">
          <thead className="text-[10px] uppercase tracking-wider text-slate-400">
            <tr><th className="py-1.5">Time (UTC)</th><th>Type</th><th>Event</th><th>Lead</th><th>Status</th><th>Match keys / detail</th><th></th></tr>
          </thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={7} className="py-4 text-center text-slate-400">No events sent yet</td></tr>}
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-slate-100" data-testid={`capi-event-${r.id}`}>
                <td className="py-1.5 whitespace-nowrap">{r.created_at}</td>
                <td className="capitalize">{r.kind}</td>
                <td className="font-semibold">{r.event_name}{r.test ? " (test)" : ""}</td>
                <td>{r.lead_id ? <a className="text-[#1557B0] hover:underline" href={`/leads/${r.lead_id}`}>#{r.lead_id}</a> : "—"}</td>
                <td><span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${r.status === "sent" ? "bg-emerald-50 text-emerald-600" : "bg-rose-50 text-rose-600"}`}>{r.status.toUpperCase()}</span></td>
                <td className="max-w-[360px] truncate text-slate-500" title={r.error || ""}>{r.error || (r.match_keys || []).join(", ")}</td>
                <td>{isAdmin && r.kind === "conversion" && r.lead_id && canResend(r) && (
                  <button onClick={() => resend(r.lead_id)} className="text-[11px] font-bold text-[#1557B0] hover:underline" data-testid={`capi-resend-${r.id}`}>Resend</button>
                )}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function MetaCapiTab({ isAdmin }) {
  const [s, setS] = useState(null);
  const load = () => API.get("/admin/meta-capi").then(({ data }) => setS(data)).catch((e) => toast.error(apiErr(e)));
  useEffect(() => { load(); }, []);
  if (!s) return <Spinner />;
  return (
    <div className="space-y-4" data-testid="meta-capi-tab">
      <div className="crm-card p-4">
        <StatusHeader s={s} />
        <p className="mt-1 text-xs text-slate-500">
          Sends hashed lead details (email, phone, name, city, state, Meta lead ID) to Meta server-side, so Meta can match
          CRM conversions back to your ads and optimise for real customers — not just form fills. Each conversion is reported once per lead.
        </p>
        {s.configured ? <ConfigForm key={JSON.stringify(s.config)} s={s} isAdmin={isAdmin} onSaved={load} /> :
          <p className="mt-3 text-sm text-amber-600">Add the Meta Dataset ID and access token to start sending events.</p>}
        {s.configured && isAdmin && <TestPanel onSent={load} testCodeSet={s.test_code_set} />}
        {s.configured && isAdmin && <ResendByLead onDone={load} />}
      </div>
      <EventLog rows={s.recent} onRefresh={load} isAdmin={isAdmin} />
    </div>
  );
}
