import React, { useEffect, useState } from "react";
import { toast } from "sonner";
import { MicrosoftOutlookLogo, Copy, CheckCircle } from "@phosphor-icons/react";
import { API, apiErr } from "../lib/api";
import { Spinner } from "./Bits";

const Field = ({ label, hint, children }) => (
  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-400">
    {label}
    <div className="mt-1 normal-case tracking-normal">{children}</div>
    {hint && <span className="mt-0.5 block text-[10px] font-normal normal-case tracking-normal text-slate-400">{hint}</span>}
  </label>
);

export const EmailProviderPicker = ({ isAdmin, refreshKey }) => {
  const [p, setP] = useState(null);
  const load = () => API.get("/admin/email/provider").then(({ data }) => setP(data)).catch(() => setP({ provider: "gmail" }));
  useEffect(() => { load(); }, [refreshKey]);
  const choose = async (provider) => {
    try { await API.put("/admin/email/provider", { provider }); toast.success(`${provider === "outlook" ? "Outlook" : "Gmail"} will send all CRM emails`); load(); }
    catch (e) { toast.error(apiErr(e)); }
  };
  if (!p) return null;
  const opts = [["outlook", "Outlook / Microsoft 365", p.outlook_connected], ["gmail", "Gmail", p.gmail_connected]];
  return (
    <div className="crm-card p-4" data-testid="email-provider-picker">
      <h3 className="font-display text-sm font-extrabold text-slate-800">Send CRM emails using</h3>
      <p className="mb-3 text-xs text-slate-500">Lead emails, automations and email campaigns all go out through the provider you pick here.</p>
      <div className="flex flex-wrap gap-2">
        {opts.map(([k, label, ok]) => (
          <button key={k} type="button" disabled={!isAdmin} onClick={() => choose(k)} data-testid={`email-provider-${k}`}
            className={`flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-bold transition-colors ${p.provider === k ? "border-[#1A73E8] bg-[#1A73E8]/10 text-[#1557B0]" : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"}`}>
            {p.provider === k && <CheckCircle size={16} weight="fill" />} {label}
            <span className={`rounded-full px-1.5 py-0.5 text-[9px] ${ok ? "bg-emerald-50 text-emerald-600" : "bg-amber-50 text-amber-600"}`}>{ok ? "READY" : "NOT SET UP"}</span>
          </button>
        ))}
      </div>
    </div>
  );
};

function GraphFields({ f, set, s, redirectUri }) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-2 rounded-xl bg-[#1A73E8]/5 p-3">
        <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Redirect URI to add in Azure</span>
        <code className="flex-1 truncate rounded-lg bg-white px-2 py-1.5 text-[11px] text-slate-600" data-testid="outlook-redirect-uri">{redirectUri}</code>
        <button title="Copy" onClick={() => { navigator.clipboard.writeText(redirectUri); toast.success("Copied"); }} className="text-slate-400 hover:text-[#1557B0]"><Copy size={16} /></button>
      </div>
      <ol className="list-decimal space-y-0.5 pl-5 text-[11px] text-slate-500">
        <li>Microsoft Entra admin centre (portal.azure.com) → App registrations → New registration → "Accounts in this organizational directory only".</li>
        <li>Authentication → Add a platform → Web → paste the Redirect URI above.</li>
        <li>API permissions → Microsoft Graph → Delegated: <b>Mail.Send</b>, <b>User.Read</b>, <b>offline_access</b> → Grant admin consent.</li>
        <li>Certificates & secrets → New client secret → copy the <b>Value</b>. Paste the IDs + secret below, Save, then Connect.</li>
      </ol>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Directory (tenant) ID"><input data-testid="outlook-tenant-id" className="crm-input w-full" value={f.tenant_id || ""} onChange={(e) => set("tenant_id", e.target.value)} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" /></Field>
        <Field label="Application (client) ID"><input data-testid="outlook-client-id" className="crm-input w-full" value={f.client_id || ""} onChange={(e) => set("client_id", e.target.value)} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" /></Field>
        <Field label="Client secret value" hint={s.has_client_secret ? "Saved — leave blank to keep it" : ""}><input data-testid="outlook-client-secret" type="password" autoComplete="new-password" className="crm-input w-full" value={f.client_secret || ""} onChange={(e) => set("client_secret", e.target.value)} placeholder={s.has_client_secret ? "••••••••" : "Paste the secret value"} /></Field>
      </div>
    </>
  );
}

function SmtpFields({ f, set, s }) {
  return (
    <>
      <p className="text-[11px] text-slate-500">Your Microsoft 365 admin must turn on <b>Authenticated SMTP</b> for this mailbox (Admin centre → Users → Mail → Manage email apps). If the account has MFA, use an app password.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="SMTP server"><input data-testid="outlook-smtp-host" className="crm-input w-full" value={f.smtp_host || ""} onChange={(e) => set("smtp_host", e.target.value)} /></Field>
        <Field label="Port"><select data-testid="outlook-smtp-port" className="crm-select w-full" value={f.smtp_port || 587} onChange={(e) => set("smtp_port", Number(e.target.value))}><option value={587}>587 (STARTTLS — recommended)</option><option value={465}>465 (SSL)</option><option value={25}>25</option></select></Field>
        <Field label="Mailbox email (login)"><input data-testid="outlook-smtp-user" type="email" className="crm-input w-full" value={f.smtp_user || ""} onChange={(e) => set("smtp_user", e.target.value)} placeholder="sales@fundle.ai" /></Field>
        <Field label="Password / app password" hint={s.has_smtp_password ? "Saved — leave blank to keep it" : ""}><input data-testid="outlook-smtp-password" type="password" autoComplete="new-password" className="crm-input w-full" value={f.smtp_password || ""} onChange={(e) => set("smtp_password", e.target.value)} placeholder={s.has_smtp_password ? "••••••••" : ""} /></Field>
        <Field label="Send as (optional)" hint="Shared mailbox / alias the login has Send As rights for"><input data-testid="outlook-from-email" type="email" className="crm-input w-full" value={f.from_email || ""} onChange={(e) => set("from_email", e.target.value)} placeholder="hello@fundle.ai" /></Field>
      </div>
    </>
  );
}

export const OutlookCard = ({ isAdmin, onChanged }) => {
  const [s, setS] = useState(null);
  const [f, setF] = useState({});
  const [testTo, setTestTo] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => API.get("/admin/outlook/status").then(({ data }) => {
    setS(data);
    setF({ method: data.method, tenant_id: data.tenant_id, client_id: data.client_id, smtp_host: data.smtp_host || "smtp.office365.com",
      smtp_port: data.smtp_port || 587, smtp_user: data.smtp_user, from_email: data.from_email, sender_name: data.sender_name || "Fundle.ai" });
  }).catch(() => setS({ method: "graph" }));
  useEffect(() => { load(); }, []);
  const set = (k, v) => setF((x) => ({ ...x, [k]: v }));

  const save = async () => {
    setBusy(true);
    try { await API.put("/admin/outlook/settings", f); toast.success("Outlook settings saved"); await load(); onChanged?.(); }
    catch (e) { toast.error(apiErr(e)); } finally { setBusy(false); }
  };
  const connect = async () => {
    try { const { data } = await API.get("/admin/outlook/auth-url", { params: { origin: window.location.origin } }); window.location.href = data.url; }
    catch (e) { toast.error(apiErr(e)); }
  };
  const disconnect = async () => {
    if (!window.confirm("Disconnect Outlook? Emails will queue until it is set up again.")) return;
    await API.post("/admin/outlook/disconnect"); toast.success("Outlook disconnected"); load(); onChanged?.();
  };
  const sendTest = async () => {
    if (!testTo.trim()) { toast.error("Enter a recipient email"); return; }
    setBusy(true);
    try { await API.post("/admin/outlook/send-test", { to: testTo.trim() }); toast.success("Test email sent ✓"); load(); onChanged?.(); }
    catch (e) { toast.error(apiErr(e), { duration: 10000 }); } finally { setBusy(false); }
  };

  if (!s) return <Spinner />;
  const redirectUri = `${window.location.origin}/api/oauth/outlook/callback`;
  const graph = f.method !== "smtp";
  const verified = s.connected && (s.method !== "smtp" || s.smtp_verified);
  return (
    <div className="crm-card space-y-4 p-4" data-testid="outlook-card">
      <div className="flex items-center gap-2">
        <MicrosoftOutlookLogo size={20} weight="fill" className="text-[#0A64C8]" />
        <h3 className="font-display text-sm font-extrabold text-slate-800">Outlook / Microsoft 365</h3>
        <span data-testid="outlook-status" className={`ml-2 rounded-full px-2 py-0.5 text-[10px] font-bold ${verified ? "bg-emerald-50 text-emerald-600" : "bg-amber-50 text-amber-600"}`}>{verified ? "CONNECTED" : s.connected ? "SAVED — SEND A TEST" : "NOT CONNECTED"}</span>
        {s.email && <span className="text-xs text-slate-500">as <b>{s.email}</b></span>}
      </div>
      {s.auth_error && <p className="rounded-lg bg-rose-50 p-2 text-[11px] text-rose-700" data-testid="outlook-auth-error">Microsoft rejected the last send: {s.auth_error}. Click Connect again.</p>}
      <div className="flex flex-wrap gap-2">
        {[["graph", "Sign in with Microsoft (recommended)"], ["smtp", "Email + password (SMTP)"]].map(([k, l]) => (
          <button key={k} type="button" disabled={!isAdmin} onClick={() => set("method", k)} data-testid={`outlook-method-${k}`}
            className={`rounded-full border px-3 py-1.5 text-xs font-bold transition-colors ${f.method === k ? "border-[#0A64C8] bg-[#0A64C8]/10 text-[#0A64C8]" : "border-slate-200 text-slate-500 hover:bg-slate-50"}`}>{l}</button>
        ))}
      </div>
      {graph ? <GraphFields f={f} set={set} s={s} redirectUri={redirectUri} /> : <SmtpFields f={f} set={set} s={s} />}
      <Field label="Sender display name (shown to recipients)"><input data-testid="outlook-sender-name" className="crm-input w-full sm:w-1/2" value={f.sender_name || ""} onChange={(e) => set("sender_name", e.target.value)} /></Field>
      {isAdmin && (
        <div className="flex flex-wrap gap-2">
          <button data-testid="outlook-save" onClick={save} disabled={busy} className="crm-btn-secondary !py-2">Save settings</button>
          {graph && <button data-testid="outlook-connect" onClick={connect} disabled={!s.client_id || !s.has_client_secret} title={!s.has_client_secret ? "Save the IDs and secret first" : ""} className="crm-btn-primary !py-2"><MicrosoftOutlookLogo size={16} /> {s.oauth_connected ? "Reconnect Microsoft account" : "Connect Microsoft account"}</button>}
          {s.connected && <button data-testid="outlook-disconnect" onClick={disconnect} className="crm-btn-secondary !py-2">Disconnect</button>}
        </div>
      )}
      {s.connected && (
        <div className="flex flex-wrap items-end gap-2 border-t border-slate-100 pt-3">
          <div className="min-w-[220px] flex-1"><Field label="Send test email to"><input data-testid="outlook-test-to" className="crm-input w-full" placeholder="someone@example.com" value={testTo} onChange={(e) => setTestTo(e.target.value)} /></Field></div>
          <button data-testid="outlook-send-test" onClick={sendTest} disabled={busy} className="crm-btn-primary !py-2">{busy ? "Sending…" : "Send test"}</button>
        </div>
      )}
    </div>
  );
};
