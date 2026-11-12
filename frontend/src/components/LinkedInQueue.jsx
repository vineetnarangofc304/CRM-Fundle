import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { LinkedinLogo, Copy, CheckCircle, Handshake, ChatCircleText, SkipForward, Play, PencilSimple } from "@phosphor-icons/react";
import { API, apiErr } from "../lib/api";

const VIEWS = [["today", "Today's queue"], ["sent", "Sent — waiting"], ["accepted", "Connected"], ["replied", "Replied"], ["skipped", "Skipped"]];

function ReplyBox({ t, done }) {
  const [txt, setTxt] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try { await API.post(`/linkedin/tasks/${t.id}/reply`, { text: txt }); toast.success("Reply logged — AI drafted your next message in Today's queue"); done(); }
    catch (e) { toast.error(apiErr(e)); } finally { setBusy(false); }
  };
  return (
    <div className="mt-2 space-y-2">
      <textarea data-testid={`li-reply-input-${t.id}`} rows={3} className="crm-input w-full text-xs" placeholder="Paste their LinkedIn reply here" value={txt} onChange={(e) => setTxt(e.target.value)} />
      <button data-testid={`li-reply-save-${t.id}`} disabled={busy || !txt.trim()} onClick={go} className="crm-btn-primary !py-1 text-xs">{busy ? "Saving…" : "Save reply & draft answer"}</button>
    </div>
  );
}

function TaskCard({ t, reload }) {
  const [edit, setEdit] = useState(false);
  const [body, setBody] = useState(t.body);
  const [reply, setReply] = useState(false);
  const act = async (path, msg) => { try { await API.post(path); toast.success(msg); reload(); } catch (e) { toast.error(apiErr(e)); } };
  const open = () => { navigator.clipboard.writeText(body).catch(() => {}); window.open(t.linkedin, "_blank", "noopener"); toast.success("Text copied — paste it on LinkedIn and press Send"); };
  const save = async () => { try { await API.patch(`/linkedin/tasks/${t.id}`, { body }); toast.success("Saved"); setEdit(false); reload(); } catch (e) { toast.error(apiErr(e)); } };
  const connect = t.kind === "connect";
  return (
    <div className="crm-card p-4" data-testid={`li-task-${t.id}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-full bg-[#0A66C2]/10 px-2 py-0.5 text-[10px] font-bold text-[#0A66C2]">{connect ? "Connection request" : t.reply_to ? "Reply" : "Message"}</span>
        <Link to={`/prospecting/${t.prospect_id}`} className="font-bold text-slate-800 hover:underline">{t.brand}</Link>
        {t.category && <span className="text-slate-400">{t.category}</span>}
        {t.fit_score != null && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700">Fit {t.fit_score}</span>}
        <span className="text-slate-500">→ {t.contact_name}{t.contact_role ? ` (${t.contact_role})` : ""}</span>
        {t.sent_at && <span className="text-emerald-600">sent {t.sent_at} UTC by {t.sent_by}</span>}
        {t.replied_at && <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-700">REPLIED {t.replied_at}</span>}
      </div>
      {t.reply_to && <p className="mt-2 rounded-lg bg-slate-50 p-2 text-xs italic text-slate-500">They said: {t.reply_to}</p>}
      {t.reply_text && <p className="mt-2 rounded-lg bg-slate-50 p-2 text-xs italic text-slate-500" data-testid={`li-reply-text-${t.id}`}>They said: {t.reply_text}</p>}
      {edit ? (
        <div className="mt-2 space-y-2">
          <textarea data-testid={`li-edit-body-${t.id}`} rows={4} className="crm-input w-full text-sm" value={body} onChange={(e) => setBody(e.target.value)} />
          <div className="text-[11px] text-slate-400">{body.length} characters{connect && body.length > 300 ? " — LinkedIn notes allow 300" : ""}</div>
          <button data-testid={`li-save-${t.id}`} onClick={save} className="crm-btn-primary !py-1 text-xs">Save</button>
        </div>
      ) : <p className="mt-2 whitespace-pre-wrap text-sm text-slate-700" data-testid={`li-body-${t.id}`}>{t.body}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        {t.status === "queued" && <>
          <button data-testid={`li-open-${t.id}`} onClick={open} className="crm-btn-primary !py-1 text-xs"><Copy size={14} /> Copy & open profile</button>
          <button data-testid={`li-sent-${t.id}`} onClick={() => act(`/linkedin/tasks/${t.id}/sent`, "Marked as sent — logged on the lead")} className="crm-btn-secondary !py-1 text-xs"><CheckCircle size={14} /> I sent it</button>
          <button data-testid={`li-edit-${t.id}`} onClick={() => setEdit(!edit)} className="crm-btn-secondary !py-1 text-xs"><PencilSimple size={14} /> {edit ? "Cancel" : "Edit"}</button>
          <button data-testid={`li-skip-${t.id}`} onClick={() => act(`/linkedin/tasks/${t.id}/skip`, "Skipped")} className="crm-btn-secondary !py-1 text-xs"><SkipForward size={14} /> Skip</button>
        </>}
        {t.status === "sent" && connect && <button data-testid={`li-accepted-${t.id}`} onClick={() => act(`/linkedin/tasks/${t.id}/accepted`, "Connected — AI drafted a first message in Today's queue")} className="crm-btn-primary !py-1 text-xs"><Handshake size={14} /> They accepted</button>}
        {["sent", "accepted"].includes(t.status) && <button data-testid={`li-reply-${t.id}`} onClick={() => setReply(!reply)} className="crm-btn-secondary !py-1 text-xs"><ChatCircleText size={14} /> They replied</button>}
        {t.status !== "queued" && <a data-testid={`li-profile-${t.id}`} href={t.linkedin} target="_blank" rel="noreferrer" className="crm-btn-secondary !py-1 text-xs"><LinkedinLogo size={14} /> Profile</a>}
      </div>
      {reply && <ReplyBox t={t} done={() => { setReply(false); reload(); }} />}
    </div>
  );
}

function LiSettings({ s, isAdmin, reload }) {
  const [f, setF] = useState(s);
  useEffect(() => setF(s), [s]);
  const save = async () => { try { await API.put("/linkedin/settings", f); toast.success("LinkedIn settings saved"); reload(); } catch (e) { toast.error(apiErr(e)); } };
  return (
    <div className="flex flex-wrap items-end gap-3 text-xs" data-testid="li-settings">
      <label className="font-semibold text-slate-500">Connection requests / day<input data-testid="li-daily-connects" type="number" className="crm-input mt-1 w-28" value={f.daily_connects} onChange={(e) => setF({ ...f, daily_connects: Number(e.target.value) })} /></label>
      <label className="font-semibold text-slate-500">Messages / day<input data-testid="li-daily-messages" type="number" className="crm-input mt-1 w-28" value={f.daily_messages} onChange={(e) => setF({ ...f, daily_messages: Number(e.target.value) })} /></label>
      <label className="flex items-center gap-2 pb-2 font-semibold text-slate-600"><input data-testid="li-enabled" type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} /> AI builds the queue</label>
      {isAdmin && <button data-testid="li-settings-save" onClick={save} className="crm-btn-secondary !py-1.5 text-xs">Save</button>}
    </div>
  );
}

export function LinkedInQueue({ isAdmin }) {
  const [view, setView] = useState("today");
  const [d, setD] = useState(null);
  const load = useCallback(() => { API.get("/linkedin/queue", { params: { view } }).then(({ data }) => setD(data)).catch((e) => toast.error(apiErr(e))); }, [view]);
  useEffect(() => { load(); }, [load]);
  const run = async () => { try { await API.post("/linkedin/run"); toast.success("AI is finding profiles and writing notes — refresh in a minute"); setTimeout(load, 30000); } catch (e) { toast.error(apiErr(e)); } };
  if (!d) return <div className="p-6 text-sm text-slate-400">Loading LinkedIn queue…</div>;
  return (
    <div className="space-y-4" data-testid="li-queue">
      <div className="crm-card space-y-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-slate-600" data-testid="li-left-today">Left today: <b>{d.left.connect}</b> connection requests · <b>{d.left.message}</b> messages. Tap <i>Copy & open profile</i>, paste on LinkedIn, press Send, then tap <i>I sent it</i>.</p>
          <button data-testid="li-run-now" onClick={run} className="crm-btn-primary !py-1.5 text-xs"><Play size={14} /> Fill queue now</button>
        </div>
        <LiSettings s={d.settings} isAdmin={isAdmin} reload={load} />
        {d.last_run?.at && <p className="text-[11px] text-slate-400">Last run {d.last_run.at} UTC · {d.last_run.stats?.profiles ?? 0} profiles found · {d.last_run.stats?.drafted ?? 0} notes written</p>}
      </div>
      <div className="flex flex-wrap gap-2">
        {VIEWS.map(([k, l]) => <button key={k} data-testid={`li-view-${k}`} onClick={() => setView(k)} className={`rounded-full px-3 py-1.5 text-xs font-bold transition-colors ${view === k ? "bg-[#0A66C2] text-white" : "bg-white text-slate-600 hover:bg-slate-100"}`}>{l}{k !== "today" ? ` · ${d.counts[k] || 0}` : ""}</button>)}
      </div>
      <div className="space-y-3">
        {d.items.length === 0 && <div className="crm-card p-10 text-center text-xs text-slate-400" data-testid="li-empty">{view === "today" ? "Nothing to send right now. The AI adds new notes every 15 minutes for brands with LinkedIn profiles." : "Nothing here yet."}</div>}
        {d.items.map((t) => <TaskCard key={t.id} t={t} reload={load} />)}
      </div>
    </div>
  );
}
