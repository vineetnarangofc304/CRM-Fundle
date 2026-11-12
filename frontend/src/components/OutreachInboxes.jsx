import React, { useEffect, useState } from "react";
import { toast } from "sonner";
import { EnvelopeSimple, Plus, Trash, CheckCircle, WarningCircle } from "@phosphor-icons/react";
import { API, apiErr } from "../lib/api";

const BLANK = { smtp_user: "", smtp_password: "", sender_name: "Fundle.ai", smtp_host: "smtp.office365.com", smtp_port: 587, daily_cap: 200, active: true };

function BoxForm({ b, onDone }) {
  const [f, setF] = useState({ ...BLANK, ...b, smtp_password: "" });
  const save = async () => {
    try {
      const { data } = b.id ? await API.put(`/admin/mailboxes/${b.id}`, f) : await API.post("/admin/mailboxes", f);
      toast.success("Inbox saved — send a test to switch it on");
      onDone(data);
    } catch (e) { toast.error(apiErr(e)); }
  };
  const inp = (k, props = {}) => <input data-testid={`mailbox-${k}`} className="crm-input mt-1 w-full" value={f[k]} onChange={(e) => setF({ ...f, [k]: props.type === "number" ? Number(e.target.value) : e.target.value })} {...props} />;
  return (
    <div className="grid gap-3 rounded-xl border border-slate-100 bg-slate-50 p-3 text-xs sm:grid-cols-3" data-testid="mailbox-form">
      <label className="font-semibold text-slate-500">Inbox email{inp("smtp_user", { placeholder: "name@fundle.ai" })}</label>
      <label className="font-semibold text-slate-500">Password {b.has_password && <span className="font-normal text-slate-400">(leave blank to keep)</span>}{inp("smtp_password", { type: "password" })}</label>
      <label className="font-semibold text-slate-500">Sender name{inp("sender_name")}</label>
      <label className="font-semibold text-slate-500">SMTP server{inp("smtp_host")}</label>
      <label className="font-semibold text-slate-500">Port{inp("smtp_port", { type: "number" })}</label>
      <label className="font-semibold text-slate-500">Emails per day{inp("daily_cap", { type: "number", min: 1, max: 1000 })}</label>
      <label className="flex items-center gap-2 font-semibold text-slate-600"><input data-testid="mailbox-active" type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Use for outreach</label>
      <div className="flex gap-2 sm:col-span-2 sm:justify-end">
        <button data-testid="mailbox-save" onClick={save} className="crm-btn-primary !py-1.5 text-xs">Save inbox</button>
        <button data-testid="mailbox-cancel" onClick={() => onDone(null)} className="crm-btn-secondary !py-1.5 text-xs">Cancel</button>
      </div>
    </div>
  );
}

function BoxRow({ b, reload, onEdit }) {
  const [to, setTo] = useState("");
  const test = async () => { try { await API.post(`/admin/mailboxes/${b.id}/test`, { to: to.trim() || b.smtp_user }); toast.success("Test sent — inbox is live for outreach"); reload(); } catch (e) { toast.error(apiErr(e)); reload(); } };
  const del = async () => { if (!window.confirm(`Remove ${b.smtp_user}?`)) return; try { await API.delete(`/admin/mailboxes/${b.id}`); reload(); } catch (e) { toast.error(apiErr(e)); } };
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-100 p-2 text-xs" data-testid={`mailbox-row-${b.id}`}>
      <EnvelopeSimple size={16} className="text-slate-400" />
      <b className="text-slate-700">{b.smtp_user}</b>
      {b.verified ? <span className="flex items-center gap-1 font-bold text-emerald-600" data-testid={`mailbox-status-${b.id}`}><CheckCircle size={14} /> Live</span>
        : <span className="flex items-center gap-1 font-bold text-amber-600" data-testid={`mailbox-status-${b.id}`} title={b.last_error || ""}><WarningCircle size={14} /> Send a test to switch on</span>}
      {!b.active && <span className="font-bold text-slate-400">Paused</span>}
      <span className="text-slate-500" data-testid={`mailbox-usage-${b.id}`}>{b.sent_today}/{b.daily_cap} today</span>
      <div className="ml-auto flex items-center gap-1">
        <input data-testid={`mailbox-test-to-${b.id}`} className="crm-input w-44 !py-1 text-xs" placeholder="Test to (optional)" value={to} onChange={(e) => setTo(e.target.value)} />
        <button data-testid={`mailbox-test-${b.id}`} onClick={test} className="crm-btn-secondary !py-1 text-xs">Send test</button>
        <button data-testid={`mailbox-edit-${b.id}`} onClick={() => onEdit(b)} className="crm-btn-secondary !py-1 text-xs">Edit</button>
        <button data-testid={`mailbox-delete-${b.id}`} onClick={del} className="crm-btn-secondary !py-1 text-xs text-rose-600"><Trash size={14} /></button>
      </div>
    </div>
  );
}

export function OutreachInboxes({ isAdmin }) {
  const [d, setD] = useState(null);
  const [edit, setEdit] = useState(null);
  const load = () => API.get("/admin/mailboxes").then(({ data }) => setD(data)).catch((e) => toast.error(apiErr(e)));
  useEffect(() => { load(); }, []);
  if (!d) return null;
  const total = (d.primary ? d.primary.daily_cap : 0) + d.boxes.filter((b) => b.active && b.verified).reduce((a, b) => a + b.daily_cap, 0);
  return (
    <div className="crm-card space-y-3 p-4" data-testid="outreach-inboxes">
      <div className="flex items-center gap-2">
        <h3 className="font-display text-sm font-extrabold text-slate-800">Outreach inboxes</h3>
        <span className="rounded-full bg-[#1A73E8]/10 px-2 py-0.5 text-[10px] font-bold text-[#1557B0]" data-testid="outreach-inboxes-total">up to {total} emails/day</span>
        {isAdmin && !edit && <button data-testid="mailbox-add" onClick={() => setEdit({})} className="crm-btn-primary ml-auto !py-1 text-xs"><Plus size={14} /> Add inbox</button>}
      </div>
      <p className="text-xs text-slate-500">Outreach emails take turns across these inboxes, each with its own daily limit. A brand always hears from the same inbox. Office 365 inboxes need Authenticated SMTP switched on.</p>
      {d.primary ? <div className="flex items-center gap-2 rounded-lg border border-slate-100 p-2 text-xs" data-testid="mailbox-primary"><EnvelopeSimple size={16} className="text-slate-400" /><b className="text-slate-700">{d.primary.email}</b><span className="font-bold text-emerald-600">Main inbox</span><span className="text-slate-500">{d.primary.sent_today}/{d.primary.daily_cap} today</span></div>
        : <div className="text-xs text-amber-600">Main inbox not connected — set it up above.</div>}
      {d.boxes.map((b) => <BoxRow key={b.id} b={b} reload={load} onEdit={setEdit} />)}
      {edit && <BoxForm b={edit} onDone={() => { setEdit(null); load(); }} />}
    </div>
  );
}
