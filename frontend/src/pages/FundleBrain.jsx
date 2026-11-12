import React, { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Brain, Plus, Trash, PaperPlaneRight, Paperclip, X, Wrench, CheckCircle, WarningCircle, ClockCounterClockwise, ChatsCircle, DownloadSimple } from "@phosphor-icons/react";
import { API, apiErr } from "../lib/api";

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const fmt = (s) => esc(s || "").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code class='rounded bg-slate-100 px-1'>$1</code>")
  .split("\n").filter((l) => !/^\s*\|?\s*:?-{3,}/.test(l)).map((l) => l.replace(/^#{1,6}\s*(.*)$/, "<b>$1</b>").replace(/^\s*\|(.*)\|\s*$/, (_, r) => r.split("|").map((c) => c.trim()).join(" · ")))
  .map((l) => (/^\s*[-•*] /.test(l) ? `<li class="ml-4 list-disc">${l.replace(/^\s*[-•*] /, "")}</li>` : l ? `<p>${l}</p>` : "")).join("");
const TOOL_LABEL = { get_system_overview: "Checked system status", find_records: "Looked up records", export_csv: "Created CSV file", aggregate_records: "Ran analysis", update_records: "Updated records", save_settings: "Changed settings", save_email_template: "Saved email template", send_email_campaign: "Queued emails", stop_emails: "Stopped emails", prioritize_contact_search: "Queued contact search", run_job: "Started a job", send_single_email: "Sent email", send_queued_emails_now: "Sent queued emails", inbox_activity: "Checked mailboxes", request_developer_change: "Emailed Vineet a change request", read_uploaded_file: "Read the file", import_brands_from_file: "Imported brands", import_contact_emails_from_file: "Imported verified emails", update_brands_from_file: "Filled store counts & categories", manage_contact_sources: "Updated contact sources" };
const EXAMPLES = ["Attach an Excel of brands → 'Add these brands and start emailing them'", "Also use https://www.example.com/team pages to find contacts — reason: more decision makers", "Send this email to all women fashion brands from Abhinav's inbox: …", "Change the hourly limit from 25 to 60 — reason: inboxes are warmed up", "Stop adding brands and search contacts for grocery brands with up to 5 stores first", "How many intro emails went out today, and how many bounced?"];

const downloadExport = async (id, name) => {
  try {
    const r = await API.get(`/brain/exports/${id}`, { responseType: "blob" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(r.data); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  } catch (e) { toast.error(apiErr(e)); }
};

function Exports() {
  const [rows, setRows] = useState(null);
  useEffect(() => { API.get("/brain/exports").then(({ data }) => setRows(data)).catch((e) => toast.error(apiErr(e))); }, []);
  if (!rows) return <div className="p-6 text-sm text-slate-400">Loading files…</div>;
  return (
    <div className="flex-1 overflow-y-auto p-6" data-testid="brain-exports">
      <h2 className="mb-3 text-sm font-extrabold text-slate-800">CSV files Fundle Brain made</h2>
      <table className="w-full text-xs"><thead className="text-left text-slate-400"><tr><th className="py-2">Created (UTC)</th><th>File</th><th>Rows</th><th /></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.id} className="border-t border-slate-100" data-testid={`brain-export-row-${r.id}`}>
            <td className="whitespace-nowrap py-2 pr-3 text-slate-500">{r.at}</td><td className="pr-3 font-semibold text-slate-700">{r.filename}</td><td className="pr-3">{r.rows}</td>
            <td className="text-right"><button data-testid={`brain-export-row-download-${r.id}`} onClick={() => downloadExport(r.id, r.filename)} className="crm-btn-secondary !py-1 text-xs"><DownloadSimple size={13} /> Download</button></td>
          </tr>))}
          {!rows.length && <tr><td colSpan={4} className="py-8 text-center text-slate-400">No files yet — ask Brain for a CSV.</td></tr>}
        </tbody></table>
    </div>
  );
}

function SendProgress({ t }) {
  const id = (t.summary || "").match(/"send_job_id": (\d+)/)?.[1];
  const [j, setJ] = useState(null);
  useEffect(() => {
    if (!t.ok || !id) return undefined;
    let stop = false;
    const tick = () => API.get(`/brain/send-jobs/${id}`).then(({ data }) => { if (stop) return; setJ(data); if (data.status === "running") setTimeout(tick, 3000); }).catch(() => !stop && setTimeout(tick, 6000));
    tick();
    return () => { stop = true; };
  }, [id, t.ok]);
  if (!id || !j) return null;
  const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
  return (
    <div className="mt-2 rounded-xl border border-slate-100 bg-slate-50 p-3" data-testid={`brain-send-progress-${id}`}>
      <div className="flex items-center justify-between text-xs font-semibold text-slate-700">
        <span data-testid="brain-send-progress-text">{j.sent} of {j.total} sent{j.failed ? ` · ${j.failed} failed` : ""}{j.status === "done" ? " · finished" : j.status === "cancelled" ? " · stopped" : j.status === "paused" ? " · paused" : " · sending…"}</span>
        {j.status === "running" && <button data-testid="brain-send-cancel" onClick={() => API.post(`/brain/send-jobs/${id}/cancel`)} className="text-rose-600 hover:underline">Stop</button>}
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200"><div className="h-full bg-[#1A73E8] transition-[width] duration-500" style={{ width: `${pct}%` }} /></div>
      {j.pause_reason && <div className="mt-2 text-[11px] font-semibold text-amber-700" data-testid="brain-send-paused">{j.pause_reason}</div>}
      {!!j.errors?.length && <div className="mt-2 max-h-24 overflow-y-auto text-[10px] text-rose-600" data-testid="brain-send-errors">{j.errors.slice(-5).map((e, i) => <div key={i}>{e}</div>)}</div>}
    </div>
  );
}

function ExportButton({ t }) {
  const id = (t.summary || "").match(/"export_id": (\d+)/)?.[1];
  const name = (t.summary || "").match(/"filename": "([^"]+)"/)?.[1] || "export.csv";
  const rows = (t.summary || "").match(/"rows": (\d+)/)?.[1];
  if (!t.ok || !id) return null;
  return <button onClick={() => downloadExport(id, name)} data-testid={`brain-export-download-${id}`} className="crm-btn-primary !py-1.5 text-xs"><DownloadSimple size={14} /> Download CSV{rows ? ` (${rows} rows)` : ""}</button>;
}

function ToolChip({ t }) {
  const warn = t.ok && /"emailed": false/.test(t.summary || "");
  return (
    <div className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${t.ok === false ? "bg-rose-50 text-rose-700" : warn ? "bg-amber-50 text-amber-700" : t.ok ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"}`} title={t.summary || ""} data-testid="brain-tool-chip">
      {t.ok === false ? <WarningCircle size={12} /> : t.ok ? <CheckCircle size={12} /> : <Wrench size={12} className="animate-spin" />}{warn ? "Change request saved (email not sent)" : `${TOOL_LABEL[t.name] || t.name}${t.ok === false ? " — failed" : ""}`}
    </div>
  );
}

function Bubble({ m }) {
  const me = m.role === "user";
  return (
    <div className={`flex ${me ? "justify-end" : "justify-start"}`} data-testid={`brain-msg-${m.role}`}>
      <div className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${me ? "bg-[#0B1F3A] text-white" : "border border-slate-100 bg-white text-slate-700 shadow-sm"}`}>
        {!!m.tools?.length && <div className="mb-2 flex flex-wrap gap-1">{m.tools.map((t, i) => <ToolChip key={i} t={t} />)}</div>}
        {me ? <div className="whitespace-pre-wrap">{m.content}</div> : <div className="space-y-1" dangerouslySetInnerHTML={{ __html: fmt(m.content) || "<p class='text-slate-400'>Thinking…</p>" }} />}
        {m.tools?.some((t) => t.name === "export_csv") && <div className="mt-2 flex flex-wrap gap-2">{m.tools.filter((t) => t.name === "export_csv").map((t, i) => <ExportButton key={i} t={t} />)}</div>}
        {m.tools?.filter((t) => t.name === "send_queued_emails_now").map((t, i) => <SendProgress key={i} t={t} />)}
      </div>
    </div>
  );
}

function Sidebar({ list, cur, pick, add, del, audit, exportsView, open }) {
  if (!open) return null;
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-slate-100 bg-slate-50/60" data-testid="brain-sidebar">
      <div className="space-y-2 p-3">
        <button data-testid="brain-new-chat" onClick={add} className="crm-btn-primary w-full justify-center !py-2 text-xs"><Plus size={14} /> New chat</button>
        <button data-testid="brain-audit-btn" onClick={audit} className={`crm-btn-secondary w-full justify-center !py-1.5 text-xs ${cur === "audit" ? "!bg-slate-200" : ""}`}><ClockCounterClockwise size={14} /> Audit log</button>
        <button data-testid="brain-exports-btn" onClick={exportsView} className={`crm-btn-secondary w-full justify-center !py-1.5 text-xs ${cur === "exports" ? "!bg-slate-200" : ""}`}><DownloadSimple size={14} /> Saved CSV files</button>
      </div>
      <div className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
        {list.map((s) => (
          <div key={s.id} className={`group flex items-center gap-1 rounded-lg px-2 py-2 text-xs transition-colors ${cur === s.id ? "bg-white font-bold text-slate-800 shadow-sm" : "text-slate-600 hover:bg-white"}`}>
            <button data-testid={`brain-session-${s.id}`} onClick={() => pick(s.id)} className="flex min-w-0 flex-1 items-center gap-2 text-left"><ChatsCircle size={14} className="shrink-0 text-slate-400" /><span className="truncate">{s.title}</span></button>
            <button data-testid={`brain-delete-${s.id}`} onClick={() => del(s.id)} className="opacity-40 transition-opacity hover:opacity-100 group-hover:opacity-100"><Trash size={13} className="text-slate-400 hover:text-rose-600" /></button>
          </div>
        ))}
        {!list.length && <p className="px-2 text-[11px] text-slate-400">No chats yet.</p>}
      </div>
    </aside>
  );
}

function Audit() {
  const [rows, setRows] = useState(null);
  useEffect(() => { API.get("/brain/audit").then(({ data }) => setRows(data)).catch((e) => toast.error(apiErr(e))); }, []);
  if (!rows) return <div className="p-6 text-sm text-slate-400">Loading audit log…</div>;
  return (
    <div className="flex-1 overflow-y-auto p-6" data-testid="brain-audit">
      <h2 className="mb-3 text-sm font-extrabold text-slate-800">Every change Fundle Brain made</h2>
      <table className="w-full text-xs"><thead className="text-left text-slate-400"><tr><th className="py-2">When (UTC)</th><th>Who</th><th>Action</th><th>Reason</th><th>Result</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.id} className="border-t border-slate-100 align-top" data-testid={`brain-audit-row-${r.id}`}>
            <td className="whitespace-nowrap py-2 pr-3 text-slate-500">{r.at}</td><td className="pr-3">{r.user_name}</td>
            <td className="pr-3"><span className={r.ok ? "font-semibold text-emerald-700" : "font-semibold text-rose-700"}>{TOOL_LABEL[r.tool] || r.tool}</span><div className="max-w-xs break-all text-[10px] text-slate-400">{JSON.stringify(r.args).slice(0, 200)}</div></td>
            <td className="max-w-xs pr-3 text-slate-600">{r.reason}</td><td className="max-w-xs break-all text-[10px] text-slate-500">{r.result?.slice(0, 220)}</td>
          </tr>))}
          {!rows.length && <tr><td colSpan={5} className="py-8 text-center text-slate-400">No changes yet.</td></tr>}
        </tbody></table>
    </div>
  );
}

export default function FundleBrain() {
  const [list, setList] = useState([]);
  const [cur, setCur] = useState(null);
  const [msgs, setMsgs] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState(null);
  const [chats, setChats] = useState(() => localStorage.getItem("brain_chats_open") !== "0" && window.innerWidth > 900);
  const toggleChats = () => setChats((v) => { localStorage.setItem("brain_chats_open", v ? "0" : "1"); return !v; });
  const end = useRef(null);
  const fileIn = useRef(null);
  const loadList = useCallback(() => API.get("/brain/sessions").then(({ data }) => { setList(data); return data; }), []);
  useEffect(() => { loadList().then((d) => d[0] && setCur(d[0].id)).catch((e) => toast.error(apiErr(e))); }, [loadList]);
  useEffect(() => { if (typeof cur === "number") API.get(`/brain/sessions/${cur}/messages`).then(({ data }) => setMsgs(data)); else setMsgs([]); }, [cur]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);
  const add = async () => { const { data } = await API.post("/brain/sessions"); await loadList(); setCur(data.id); return data.id; };
  const del = async (id) => { if (!window.confirm("Delete this chat? The audit log keeps its changes.")) return; await API.delete(`/brain/sessions/${id}`); const d = await loadList(); if (cur === id) setCur(d[0]?.id ?? null); };

  const send = async (override) => {
    let q = (override ?? text).trim();
    if ((!q && !file) || busy) return;
    const sid = typeof cur === "number" ? cur : await add();
    setBusy(true);
    if (file) {
      try {
        const fd = new FormData(); fd.append("file", file);
        const { data: f } = await API.post(`/brain/sessions/${sid}/upload`, fd);
        q = `[Attached file #${f.id}: ${f.filename} — ${f.rows} rows, columns: ${f.columns.join(", ")}]\n${q || "Please import this file."}`;
        setFile(null);
      } catch (e) { toast.error(apiErr(e)); setBusy(false); return; }
    }
    setText("");
    setMsgs((m) => [...m, { role: "user", content: q }, { role: "assistant", content: "", tools: [] }]);
    const upd = (fn) => setMsgs((m) => { const c = [...m]; c[c.length - 1] = fn({ ...c[c.length - 1] }); return c; });
    try {
      const res = await fetch(`${process.env.REACT_APP_BACKEND_URL}/api/brain/sessions/${sid}/chat`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${localStorage.getItem("crm_token") || ""}` },
        body: JSON.stringify({ message: q }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Error ${res.status}`);
      loadList();
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n"); buf = parts.pop();
        parts.forEach((p) => {
          if (!p.startsWith("data: ")) return;
          const ev = JSON.parse(p.slice(6));
          if (ev.type === "text") upd((x) => ({ ...x, content: x.content + (ev.delta || "") }));
          else if (ev.type === "tool") upd((x) => ({ ...x, tools: [...(x.tools || []), { name: ev.name }] }));
          else if (ev.type === "tool_done") upd((x) => { const t = [...x.tools]; const i = t.map((y) => y.name).lastIndexOf(ev.name); if (i >= 0) t[i] = { ...t[i], ok: ev.ok, summary: ev.summary }; return { ...x, tools: t }; });
          else if (ev.type === "error") upd((x) => ({ ...x, content: `${x.content}\n\n⚠️ ${ev.message}` }));
        });
      }
    } catch (e) { upd((x) => ({ ...x, content: `⚠️ ${e.message}` })); }
    setBusy(false); await loadList();
  };

  return (
    <div className="flex h-[calc(100vh-7rem)] overflow-hidden rounded-2xl border border-slate-100 bg-white" data-testid="fundle-brain-page">
      <Sidebar open={chats} list={list} cur={cur} pick={setCur} add={add} del={del} audit={() => setCur("audit")} exportsView={() => setCur("exports")} />
      {cur === "audit" ? <Audit /> : cur === "exports" ? <Exports /> : (
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center gap-2 border-b border-slate-100 px-6 py-3">
            <button data-testid="brain-chats-toggle" onClick={toggleChats} title={chats ? "Hide chats" : "Show chats"} className="crm-btn-secondary !px-2 !py-1 text-xs"><ChatsCircle size={14} /> {chats ? "Hide chats" : "Chats"}</button>
            {!chats && <button data-testid="brain-new-chat-top" onClick={add} className="crm-btn-secondary !px-2 !py-1 text-xs"><Plus size={14} /> New</button>}
            <Brain size={20} weight="fill" className="text-[#1A73E8]" /><span className="font-display text-sm font-extrabold text-slate-800">Fundle Brain</span>
            <span className="hidden text-[11px] text-slate-500 md:inline">runs prospecting, outreach, templates, inboxes and data for you · every change is logged with its reason</span>
          </div>
          <div className="flex-1 space-y-4 overflow-y-auto bg-slate-50/40 px-6 py-5" data-testid="brain-messages">
            {!msgs.length && (
              <div className="mx-auto mt-10 max-w-xl text-center">
                <Brain size={40} weight="duotone" className="mx-auto text-[#1A73E8]" />
                <p className="mt-2 text-sm font-bold text-slate-700">Tell me what to change, send or find.</p>
                <div className="mt-4 grid gap-2">{EXAMPLES.map((e, i) => <button key={i} data-testid={`brain-example-${i}`} onClick={() => setText(e)} className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-left text-xs text-slate-600 transition-colors hover:border-[#1A73E8]">{e}</button>)}</div>
              </div>
            )}
            {msgs.map((m, i) => <Bubble key={m.id || i} m={m} />)}
            <div ref={end} />
          </div>
          <div className="border-t border-slate-100 p-4">
            {file && <div className="mb-2 inline-flex items-center gap-2 rounded-full bg-sky-50 px-3 py-1 text-xs font-semibold text-sky-700" data-testid="brain-file-chip"><Paperclip size={12} />{file.name}<button data-testid="brain-file-remove" onClick={() => setFile(null)}><X size={12} /></button></div>}
            <div className="flex items-end gap-2">
              <input ref={fileIn} type="file" accept=".csv,.xlsx,.xls,.txt" className="hidden" data-testid="brain-file-input" onChange={(e) => { setFile(e.target.files?.[0] || null); e.target.value = ""; }} />
              <button data-testid="brain-attach" onClick={() => fileIn.current?.click()} title="Attach Excel or CSV" className="crm-btn-secondary !px-3 !py-2.5"><Paperclip size={16} /></button>
              <textarea data-testid="brain-input" rows={2} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                placeholder="e.g. Send this email to all D2C brands with under 10 stores from Hardik's inbox… (Enter to send, Shift+Enter for a new line)" className="crm-input flex-1 resize-none text-sm" />
              <button data-testid="brain-send" disabled={busy || (!text.trim() && !file)} onClick={() => send()} className="crm-btn-primary !py-2.5"><PaperPlaneRight size={16} weight="fill" /> {busy ? "Working…" : "Send"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
