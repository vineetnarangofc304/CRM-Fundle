import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { PaperPlaneTilt, CheckCircle, XCircle, LinkedinLogo, Copy, StopCircle, Play, GearSix } from "@phosphor-icons/react";
import { API, apiErr } from "../lib/api";
import { useAuth } from "../context/AuthContext";
import { EmailTemplates } from "../components/EmailTemplates";
import { LinkedInQueue } from "../components/LinkedInQueue";

const TABS = [["pending_approval", "Needs review"], ["approved", "Approved / scheduled"], ["sent", "Sent"], ["bounced", "Bounced"], ["failed", "Failed"], ["stopped", "Stopped"], ["rejected", "Rejected"]];

function SettingsPanel({ s, isAdmin, reload }) {
  const [f, setF] = useState(s);
  useEffect(() => setF(s), [s]);
  const save = async () => {
    try { const { signature, ...rest } = f; await API.put("/outreach/settings", rest); toast.success("Outreach settings saved"); reload(); } catch (e) { toast.error(apiErr(e)); }
  };
  const num = (k, v) => setF({ ...f, [k]: Number(v) });
  return (
    <details className="crm-card p-4" data-testid="outreach-settings">
      <summary className="flex cursor-pointer items-center gap-2 text-sm font-bold text-slate-700"><GearSix size={16} /> Outreach settings</summary>
      <div className="mt-3 grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
        <label className="font-semibold text-slate-500">Auto-draft for fit score ≥<input data-testid="outreach-min-score" type="number" className="crm-input mt-1 w-full" value={f.min_score} onChange={(e) => num("min_score", e.target.value)} /></label>
        <label className="font-semibold text-slate-500">New drafts per run (every 15 min)<input data-testid="outreach-drafts-per-run" type="number" className="crm-input mt-1 w-full" value={f.drafts_per_run} onChange={(e) => num("drafts_per_run", e.target.value)} /></label>
        <label className="font-semibold text-slate-500">Emails per hour, each inbox<input data-testid="outreach-hourly-cap" type="number" min={1} max={100} className="crm-input mt-1 w-full" value={f.hourly_cap} onChange={(e) => num("hourly_cap", e.target.value)} /></label>
        <label className="font-semibold text-slate-500">Follow-up gaps (days)
          <div className="mt-1 flex gap-1">{[0, 1].map((i) => <input key={i} data-testid={`outreach-gap-${i}`} type="number" className="crm-input w-full" value={f.gaps[i]} onChange={(e) => setF({ ...f, gaps: f.gaps.map((g, j) => (j === i ? Number(e.target.value) : g)) })} />)}</div>
        </label>
        <label className="font-semibold text-slate-500 sm:col-span-2 lg:col-span-3">What we sell (AI uses this to write)<textarea data-testid="outreach-pitch" rows={3} className="crm-input mt-1 w-full" value={f.pitch} onChange={(e) => setF({ ...f, pitch: e.target.value })} /></label>
        <label className="font-semibold text-slate-500">Email signature<div className="mt-1 text-[11px] font-normal text-slate-400">Edit it in “Email templates & auto emailer”.</div></label>
        <label className="font-semibold text-slate-500">CC every outreach email to<input data-testid="outreach-cc" type="email" className="crm-input mt-1 w-full" value={f.cc || ""} onChange={(e) => setF({ ...f, cc: e.target.value })} /></label>
        <label className="flex items-center gap-2 font-semibold text-slate-600"><input data-testid="outreach-auto-send" type="checkbox" checked={!!f.auto_send} onChange={(e) => setF({ ...f, auto_send: e.target.checked })} /> Send automatically (no approval)</label>
        <label className="flex items-center gap-2 font-semibold text-slate-600"><input data-testid="outreach-ai-sequence" type="checkbox" checked={!!f.ai_sequence} onChange={(e) => setF({ ...f, ai_sequence: e.target.checked })} /> Also write the old 3-email AI sequence</label>
        <label className="flex items-center gap-2 font-semibold text-slate-600"><input data-testid="outreach-enabled" type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} /> Outreach agent on</label>
      </div>
      {isAdmin && <button data-testid="outreach-settings-save" onClick={save} className="crm-btn-primary mt-3 !py-1.5 text-xs">Save settings</button>}
    </details>
  );
}

function BouncePanel({ reload }) {
  const [open, setOpen] = useState(false);
  const [txt, setTxt] = useState("");
  const [res, setRes] = useState(null);
  const go = async () => {
    try { const { data } = await API.post("/outreach/mark-bounced", { brands: txt.split("\n") }); setRes(data); toast.success(`${data.matched.length} brands marked — AI is finding new contacts`); reload(); }
    catch (e) { toast.error(apiErr(e)); }
  };
  if (!open) return <button data-testid="outreach-bounce-open" onClick={() => setOpen(true)} className="crm-btn-secondary !py-1.5 text-xs">Mark brands as bounced</button>;
  return (
    <div className="crm-card w-full space-y-2 p-4" data-testid="outreach-bounce-panel">
      <div className="text-sm font-bold text-slate-700">Paste brand names whose emails bounced (one per line)</div>
      <textarea data-testid="outreach-bounce-input" rows={6} className="crm-input w-full text-xs" value={txt} onChange={(e) => setTxt(e.target.value)} />
      <div className="flex gap-2">
        <button data-testid="outreach-bounce-submit" onClick={go} className="crm-btn-primary !py-1.5 text-xs">Mark invalid & find new emails</button>
        <button onClick={() => { setOpen(false); setRes(null); }} className="crm-btn-secondary !py-1.5 text-xs">Close</button>
      </div>
      {res && <div className="space-y-0.5 text-xs text-slate-600" data-testid="outreach-bounce-result"><div><b>{res.matched.length}</b> marked</div>
        {res.not_found.length > 0 && <div>Not found: {res.not_found.join(" · ")}</div>}
        {res.ambiguous?.length > 0 && <div className="text-amber-600">Several matches, type the exact name: {res.ambiguous.join(" · ")}</div>}
        {res.ignored?.length > 0 && <div className="text-slate-400">Too short, ignored: {res.ignored.join(" · ")}</div>}</div>}
    </div>
  );
}

function FormatStatus() {
  const [d, setD] = useState(null);
  const load = () => API.get("/outreach/email-format").then(({ data }) => setD(data)).catch(() => {});
  useEffect(() => { load(); }, []);
  const run = async () => { try { await API.post("/outreach/email-format/run"); toast.success("Email format check started — 30 brands per run"); setTimeout(load, 60000); } catch (e) { toast.error(apiErr(e)); } };
  if (!d) return null;
  const e = d.emails || {};
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500" data-testid="email-format-status">
      <span>Email format check: <b>{d.checked}</b> brands checked · <b className="text-sky-700">{d.confirmed}</b> formats confirmed · emails: {e.published || 0} published, {e.confirmed_format || 0} confirmed format, {e.pattern || 0} guessed</span>
      <button data-testid="email-format-run" onClick={run} className="crm-btn-secondary !py-1 text-xs">Check formats now</button>
    </div>
  );
}

function MessageCard({ m, reload, selected, onSelect }) {
  const [edit, setEdit] = useState(false);
  const [d, setD] = useState({ subject: m.subject, body: m.body, to: m.to });
  const act = async (path, msg) => { try { await API.post(path); toast.success(msg); reload(); } catch (e) { toast.error(apiErr(e)); } };
  const save = async () => { try { await API.patch(`/outreach/messages/${m.id}`, d); toast.success("Saved"); setEdit(false); reload(); } catch (e) { toast.error(apiErr(e)); } };
  const li = m.channel === "linkedin";
  const canAct = ["pending_approval", "failed"].includes(m.status);
  const tpl = m.kind === "template";
  const sendNow = async () => { try { await API.post(`/outreach/messages/${m.id}/send-now`); toast.success(`Sent to ${m.to}`); reload(); } catch (e) { toast.error(apiErr(e)); } };
  return (
    <div className="crm-card p-4" data-testid={`outreach-msg-${m.id}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {tpl && canAct && <input type="checkbox" data-testid={`outreach-select-msg-${m.id}`} checked={selected} onChange={() => onSelect(m.id)} />}
        {m.step === 1 && m.channel === "email" && canAct && !tpl && <input type="checkbox" data-testid={`outreach-select-${m.seq_id}`} checked={selected} onChange={() => onSelect(m.seq_id)} />}
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${li ? "bg-[#0A66C2]/10 text-[#0A66C2]" : "bg-[#1A73E8]/10 text-[#1557B0]"}`}>{li ? "LinkedIn" : tpl ? m.template_name : `Email ${m.step}/3`}</span>
        <Link to={`/prospecting/${m.prospect_id}`} className="font-bold text-slate-800 hover:underline">{m.brand}</Link>
        <span className="text-slate-400">{m.category}</span>
        {m.fit_score != null && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700">Fit {m.fit_score}</span>}
        <span className="text-slate-500">→ {m.contact_name}{m.contact_role ? ` (${m.contact_role})` : ""}</span>
        {!li && <span className="text-slate-500">{m.to}{m.email_status === "pattern" && <span className="ml-1 rounded bg-amber-50 px-1 text-[9px] font-bold text-amber-700">GUESSED EMAIL</span>}{m.email_status === "confirmed_format" && <span className="ml-1 rounded bg-sky-50 px-1 text-[9px] font-bold text-sky-700">FORMAT CONFIRMED</span>}</span>}
        {m.scheduled_at && m.status === "approved" && <span className="text-slate-400">sends {m.scheduled_at} UTC</span>}
        {m.status === "approved" && !m.scheduled_at && m.channel === "email" && <span className="text-slate-400">sends after previous step</span>}
        {m.sent_at && <span className="text-emerald-600">sent {m.sent_at}{m.sent_from ? ` from ${m.sent_from}` : ""}</span>}
        {m.replied_at && <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-700" data-testid={`outreach-replied-${m.id}`}>REPLIED {m.replied_at}</span>}
        {m.bounced_at && <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-700" data-testid={`outreach-bounced-${m.id}`}>BOUNCED {m.bounced_at}</span>}
        {m.error && <span className="text-rose-600">{m.error}</span>}
      </div>
      {edit ? (
        <div className="mt-2 space-y-2">
          {!li && <input data-testid={`outreach-edit-to-${m.id}`} className="crm-input w-full" value={d.to || ""} onChange={(e) => setD({ ...d, to: e.target.value })} />}
          {!li && <input data-testid={`outreach-edit-subject-${m.id}`} className="crm-input w-full" value={d.subject} onChange={(e) => setD({ ...d, subject: e.target.value })} />}
          <textarea data-testid={`outreach-edit-body-${m.id}`} rows={7} className="crm-input w-full font-mono text-xs" value={d.body} onChange={(e) => setD({ ...d, body: e.target.value })} />
          <button data-testid={`outreach-save-${m.id}`} onClick={save} className="crm-btn-primary !py-1 text-xs">Save</button>
        </div>
      ) : (
        <div className="mt-2">
          {!li && <div className="text-sm font-bold text-slate-800">{m.subject}</div>}
          {li ? <p className="whitespace-pre-wrap text-sm text-slate-600">{m.body}</p> : <div className="prose prose-sm max-w-none text-slate-600" dangerouslySetInnerHTML={{ __html: m.body }} />}
          {(m.attachments?.length > 0 || m.video_links?.length > 0) && <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-slate-500" data-testid={`outreach-extras-${m.id}`}>
            {m.attachments?.map((a) => <span key={a.file_id}>📎 {a.filename}</span>)}{m.video_links?.map((v) => <span key={v}>🎬 {v}</span>)}</div>}
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {!li && ["pending_approval", "approved", "failed"].includes(m.status) && <button data-testid={`outreach-send-now-${m.id}`} onClick={sendNow} className="crm-btn-primary !py-1 text-xs"><PaperPlaneTilt size={14} /> Send now</button>}
        {canAct && !li && m.step === 1 && !tpl && <button data-testid={`outreach-approve-seq-${m.seq_id}`} onClick={() => act(`/outreach/sequences/${m.seq_id}/approve`, "Sequence approved — email 1 goes out on the next run")} className="crm-btn-primary !py-1 text-xs"><CheckCircle size={14} /> Approve sequence</button>}
        {canAct && <button data-testid={`outreach-approve-${m.id}`} onClick={() => act(`/outreach/messages/${m.id}/approve`, "Approved")} className="crm-btn-secondary !py-1 text-xs"><CheckCircle size={14} /> Approve this</button>}
        {["pending_approval", "approved", "failed"].includes(m.status) && <button data-testid={`outreach-edit-${m.id}`} onClick={() => setEdit(!edit)} className="crm-btn-secondary !py-1 text-xs">{edit ? "Cancel" : "Edit"}</button>}
        {li && m.status !== "sent" && <>
          <button data-testid={`outreach-copy-${m.id}`} onClick={() => { navigator.clipboard.writeText(m.body); toast.success("Copied"); if (m.linkedin) window.open(m.linkedin, "_blank"); }} className="crm-btn-secondary !py-1 text-xs"><Copy size={14} /> Copy & open LinkedIn</button>
          <button data-testid={`outreach-mark-sent-${m.id}`} onClick={() => act(`/outreach/messages/${m.id}/mark-sent`, "Marked as sent")} className="crm-btn-secondary !py-1 text-xs"><LinkedinLogo size={14} /> Mark sent</button>
        </>}
        {canAct && <button data-testid={`outreach-reject-${m.id}`} onClick={() => act(`/outreach/messages/${m.id}/reject`, "Rejected")} className="crm-btn-secondary !py-1 text-xs"><XCircle size={14} /> Reject</button>}
        {["pending_approval", "approved"].includes(m.status) && !tpl && <button data-testid={`outreach-stop-${m.seq_id}-${m.id}`} onClick={() => act(`/outreach/sequences/${m.seq_id}/stop`, "Sequence stopped")} className="crm-btn-secondary !py-1 text-xs"><StopCircle size={14} /> Stop sequence</button>}
      </div>
    </div>
  );
}

export default function Outreach() {
  const { user } = useAuth();
  const [s, setS] = useState(null);
  const [stats, setStats] = useState({});
  const [tab, setTab] = useState(null);
  const [channel, setChannel] = useState("");
  const [data, setData] = useState({ items: [], total: 0 });
  const [sel, setSel] = useState([]);
  const [view, setView] = useState("messages");
  const load = useCallback(() => {
    if (!["admin", "manager"].includes(user?.role)) return;
    API.get("/outreach/settings").then(({ data: d }) => { setS(d); setTab((t) => t || "pending_approval"); }).catch((e) => toast.error(apiErr(e)));
    API.get("/outreach/stats").then(({ data: d }) => setStats(d)).catch(() => {});
    if (!tab) return;
    API.get("/outreach/messages", { params: { status: tab, channel: channel || undefined, limit: 100 } }).then(({ data: d }) => setData(d)).catch((e) => toast.error(apiErr(e)));
  }, [tab, channel]);
  useEffect(() => { load(); }, [load]);
  const count = (st) => (stats.email?.[st] || 0) + (stats.linkedin?.[st] || 0);
  const bulk = async () => {
    try {
      const tplIds = sel.filter((x) => typeof x === "string").map((x) => Number(x.slice(1)));
      const seqIds = sel.filter((x) => typeof x === "number");
      let n = 0;
      if (tplIds.length) n += (await API.post("/outreach/messages/send-selected", { ids: tplIds })).data.queued;
      if (seqIds.length) n += (await API.post("/outreach/bulk-approve", { seq_ids: seqIds })).data.approved;
      toast.success(`${n} emails finalised — they go out within 15 min`); setSel([]); load();
    } catch (e) { toast.error(apiErr(e)); }
  };
  const runNow = async () => {
    try { await API.post("/outreach/run"); toast.success("Outreach run started — drafts appear in a minute"); setTimeout(load, 30000); }
    catch (e) { toast.error(apiErr(e)); }
  };
  if (!["admin", "manager"].includes(user?.role)) return <div className="p-8 text-sm text-slate-500" data-testid="outreach-denied">Outreach is available to admins and managers only.</div>;
  if (!s) return <div className="p-8 text-sm text-slate-400">Loading outreach…</div>;
  return (
    <div className="space-y-4 p-6" data-testid="outreach-page">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 font-display text-3xl font-extrabold text-slate-900"><PaperPlaneTilt size={28} className="text-[#1A73E8]" /> Outreach</h1>
          <p className="text-sm text-slate-500" data-testid="outreach-mode-text">The store-count intro emails go out as written, with each brand and first name filled in. {s.auto_send ? `Auto templates send on their own (max ${s.hourly_cap}/hour per inbox, no daily limit${s.cc ? `, CC ${s.cc}` : ""}); manual ones are click-to-send from each brand page.` : "Nothing is sent until you approve it."}</p>
        </div>
        <button data-testid="outreach-run-now" onClick={runNow} className="crm-btn-primary !py-1.5 text-xs"><Play size={14} /> Run now</button>
      </div>
      {!s.mail_connected && <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800" data-testid="outreach-mail-warning">No email account connected — approved emails will wait. <Link to="/admin?tab=Email" className="font-bold underline">Connect Outlook in Admin → Email</Link></div>}
      {s.last_run?.at && <p className="text-[11px] text-slate-400">Last run {s.last_run.at} UTC · drafted {s.last_run.stats?.drafted ?? 0} · sent {s.last_run.stats?.sent ?? 0} · via {s.mail_provider}</p>}
      <SettingsPanel s={s} isAdmin={user.role === "admin"} reload={load} />
      <div className="flex gap-1 border-b border-slate-200" data-testid="outreach-view-tabs">
        {[["messages", "Emails & review"], ["sent", "Sent emails"], ["templates", "Email templates & auto emailer"]].map(([k, l]) => (
          <button key={k} data-testid={`outreach-view-${k}`} onClick={() => { if (k === "sent") { setTab("sent"); setView("messages"); } else { setView(k); if (k === "messages" && tab === "sent") setTab("pending_approval"); } }} className={`-mb-px border-b-2 px-4 py-2 text-sm font-bold transition-colors ${(k === "sent" ? view === "messages" && tab === "sent" : k === "messages" ? view === "messages" && tab !== "sent" : view === k) ? "border-[#1A73E8] text-[#1557B0]" : "border-transparent text-slate-500 hover:text-slate-700"}`}>{l}{k === "sent" && stats.email?.sent ? ` (${stats.email.sent})` : ""}</button>
        ))}
      </div>
      {view === "linkedin" ? <LinkedInQueue isAdmin={user.role === "admin"} /> : view === "templates" ? <EmailTemplates /> : <>
      <div className="flex flex-wrap items-center gap-2"><BouncePanel reload={load} /><FormatStatus /></div>
      <div className="flex flex-wrap items-center gap-2">
        {TABS.map(([k, l]) => (
          <button key={k} data-testid={`outreach-tab-${k}`} onClick={() => { setTab(k); setSel([]); }} className={`rounded-full px-3 py-1.5 text-xs font-bold transition-colors ${tab === k ? "bg-[#1A73E8] text-white" : "bg-white text-slate-600 hover:bg-slate-100"}`}>{l} · {count(k)}</button>
        ))}
        <select data-testid="outreach-channel" className="crm-select !w-36 !py-1 text-xs" value={channel} onChange={(e) => setChannel(e.target.value)}>
          <option value="">All channels</option><option value="email">Email</option>
        </select>
        {sel.length > 0 && <button data-testid="outreach-bulk-approve" onClick={bulk} className="crm-btn-primary ml-auto !py-1.5 text-xs"><CheckCircle size={14} /> Finalise & send {sel.length}</button>}
      </div>
      <div className="space-y-3">
        {data.items.length === 0 && <div className="crm-card p-10 text-center text-xs text-slate-400" data-testid="outreach-empty">Nothing here yet. Click a template next to a contact on any brand page, or switch a template to Auto.</div>}
        {data.items.map((m) => { const key = m.kind === "template" ? `t${m.id}` : m.seq_id; return <MessageCard key={m.id} m={m} reload={load} selected={sel.includes(key)} onSelect={() => setSel((x) => (x.includes(key) ? x.filter((y) => y !== key) : [...x, key]))} />; })}
      </div>
      </>}
    </div>
  );
}
