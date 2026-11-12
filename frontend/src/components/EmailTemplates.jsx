import React, { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Sparkle, Paperclip, VideoCamera, Plus, Trash, FloppyDisk, X } from "@phosphor-icons/react";
import { API, apiErr } from "../lib/api";

const AUD = { all: "Every brand", d2c: "D2C / e-commerce brands", stores_100: "100+ stores", stores_11_99: "11–99 stores", stores_1_10: "1–10 stores or unknown" };
const EMPTY = { name: "", subject: "", body: "<p>Hi {{first_name}},</p>\n<p></p>", mode: "manual", ai_tailor: true, audience: "all", step: 1, wait_days: 0, active: true, attachments: [], video_links: [] };
const kb = (n) => `${Math.max(1, Math.round((n || 0) / 1024))} KB`;

function Attachments({ f, setF }) {
  const ref = useRef(null);
  const [up, setUp] = useState(false);
  const upload = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUp(true);
    const fd = new FormData();
    fd.append("file", file);
    try { const { data } = await API.post("/outreach/files", fd); setF({ ...f, attachments: [...f.attachments, data] }); toast.success("Attached"); }
    catch (err) { toast.error(apiErr(err)); } finally { setUp(false); }
  };
  return (
    <div className="space-y-1">
      <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400">Attachments (brochure, max 3 MB total)</div>
      {f.attachments.map((a, i) => (
        <div key={a.file_id} className="flex items-center gap-2 text-xs" data-testid={`tpl-attachment-${i}`}><Paperclip size={14} /> {a.filename} <span className="text-slate-400">{kb(a.size)}</span>
          <button onClick={() => setF({ ...f, attachments: f.attachments.filter((_, j) => j !== i) })} className="text-rose-500" data-testid={`tpl-attachment-remove-${i}`}><X size={12} /></button></div>
      ))}
      <input ref={ref} type="file" accept=".pdf,.png,.jpg,.jpeg,.pptx,.docx" className="hidden" onChange={upload} data-testid="tpl-file-input" />
      <button onClick={() => ref.current?.click()} disabled={up} className="crm-btn-secondary !py-1 text-xs" data-testid="tpl-attach-btn"><Paperclip size={14} /> {up ? "Uploading…" : "Attach file"}</button>
    </div>
  );
}

function Videos({ f, setF }) {
  const set = (i, v) => setF({ ...f, video_links: f.video_links.map((x, j) => (j === i ? v : x)) });
  return (
    <div className="space-y-1">
      <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400">Video links (sent as a link)</div>
      {f.video_links.map((v, i) => (
        <div key={i} className="flex gap-1"><input data-testid={`tpl-video-${i}`} className="crm-input w-full text-xs" placeholder="https://youtube.com/…" value={v} onChange={(e) => set(i, e.target.value)} />
          <button onClick={() => setF({ ...f, video_links: f.video_links.filter((_, j) => j !== i) })} className="text-rose-500"><X size={12} /></button></div>
      ))}
      {f.video_links.length < 5 && <button onClick={() => setF({ ...f, video_links: [...f.video_links, ""] })} className="crm-btn-secondary !py-1 text-xs" data-testid="tpl-add-video"><VideoCamera size={14} /> Add video link</button>}
    </div>
  );
}

function Editor({ t, onDone }) {
  const [f, setF] = useState({ ...EMPTY, ...t });
  const [brief, setBrief] = useState("");
  const [ai, setAi] = useState(false);
  const suggest = async () => {
    setAi(true);
    try { const { data } = await API.post("/outreach/templates/ai-suggest", { name: f.name || "Intro email", brief, subject: f.subject, body: f.body }); setF({ ...f, ...data }); toast.success("AI draft ready — edit as you like"); }
    catch (e) { toast.error(apiErr(e)); } finally { setAi(false); }
  };
  const save = async () => {
    try {
      if (t.id) { const { data } = await API.put(`/outreach/templates/${t.id}`, f); toast.success(data.queued_updated ? `Template saved — ${data.queued_updated} queued emails will use the new text` : "Template saved"); }
      else { await API.post("/outreach/templates", f); toast.success("Template saved"); }
      onDone();
    }
    catch (e) { toast.error(apiErr(e)); }
  };
  return (
    <div className="crm-card space-y-3 p-4" data-testid="tpl-editor">
      <div className="grid gap-3 sm:grid-cols-4">
        <label className="text-xs font-semibold text-slate-500">Template name<input data-testid="tpl-name" className="crm-input mt-1 w-full" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label className="text-xs font-semibold text-slate-500">Sending
          <select data-testid="tpl-mode" className="crm-select mt-1 w-full" value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })}>
            <option value="manual">Manual — click to send per contact</option><option value="auto">Auto — send to every brand with an email</option>
          </select></label>
        <label className="text-xs font-semibold text-slate-500">Who gets it (auto)
          <select data-testid="tpl-audience" className="crm-select mt-1 w-full" value={f.audience || "all"} onChange={(e) => setF({ ...f, audience: e.target.value })}>
            {Object.entries(AUD).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select></label>
        <label className="text-xs font-semibold text-slate-500">Email in sequence
          <select data-testid="tpl-step" className="crm-select mt-1 w-full" value={f.step || 1} onChange={(e) => { const st = Number(e.target.value); setF({ ...f, step: st, wait_days: st === 1 ? 0 : (f.wait_days || (st === 2 ? 3 : 7)) }); }}>
            <option value={1}>Intro</option><option value={2}>Follow-up 1</option><option value={3}>Follow-up 2</option>
          </select></label>
        {(f.step || 1) > 1 && <label className="text-xs font-semibold text-slate-500">Days after intro (if no reply)<input data-testid="tpl-wait-days" type="number" min={1} max={60} className="crm-input mt-1 w-full" value={f.wait_days} onChange={(e) => setF({ ...f, wait_days: Number(e.target.value) })} /></label>}
        <label className="flex items-end gap-2 pb-2 text-xs font-semibold text-slate-600"><input data-testid="tpl-active" type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active</label>
      </div>
      <label className="flex items-center gap-2 text-xs font-semibold text-slate-600"><input data-testid="tpl-ai-tailor" type="checkbox" checked={f.ai_tailor !== false} onChange={(e) => setF({ ...f, ai_tailor: e.target.checked })} />
        AI personalises each email (adds one line about the brand). Untick to send exactly what you write here, with only {"{{first_name}}"} / {"{{brand}}"} filled in.</label>

      <div className="flex flex-wrap gap-2">
        <input data-testid="tpl-ai-brief" className="crm-input min-w-[240px] flex-1 text-xs" placeholder="Tell AI what this email should say (optional)" value={brief} onChange={(e) => setBrief(e.target.value)} />
        <button data-testid="tpl-ai-suggest" onClick={suggest} disabled={ai} className="crm-btn-secondary !py-1 text-xs"><Sparkle size={14} /> {ai ? "Writing…" : f.subject ? "AI improve" : "AI suggest draft"}</button>
      </div>
      <input data-testid="tpl-subject" className="crm-input w-full" placeholder="Subject" value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} />
      <div className="grid gap-3 lg:grid-cols-2">
        <textarea data-testid="tpl-body" rows={12} className="crm-input w-full font-mono text-xs" value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} />
        <div className="prose prose-sm max-w-none rounded-lg border border-slate-100 bg-slate-50 p-3 text-slate-600 [&_p]:mb-3 [&_ul]:list-disc [&_ul]:pl-5" data-testid="tpl-preview" dangerouslySetInnerHTML={{ __html: f.body }} />
      </div>
      <p className="text-[11px] text-slate-400">Placeholders: {"{{first_name}}"}, {"{{brand}}"}, {"{{role}}"}. {f.ai_tailor !== false ? "AI also tailors one line to each brand before sending." : "Sent exactly as written."} The sending inbox's own signature and CC are added automatically.</p>
      <div className="grid gap-4 sm:grid-cols-2"><Attachments f={f} setF={setF} /><Videos f={f} setF={setF} /></div>
      <div className="flex gap-2">
        <button data-testid="tpl-save" onClick={save} className="crm-btn-primary !py-1.5 text-xs"><FloppyDisk size={14} /> Save template</button>
        <button data-testid="tpl-cancel" onClick={onDone} className="crm-btn-secondary !py-1.5 text-xs">Cancel</button>
      </div>
    </div>
  );
}

function SignatureCard() {
  const [s, setS] = useState(null);
  const [sig, setSig] = useState("");
  useEffect(() => { API.get("/outreach/settings").then(({ data }) => { setS(data); setSig(data.signature || ""); }).catch(() => {}); }, []);
  if (!s) return null;
  const save = async () => {
    try { await API.put("/outreach/signature", { signature: sig }); toast.success("Main inbox signature saved"); }
    catch (e) { toast.error(apiErr(e)); }
  };
  return (
    <div className="crm-card space-y-2 p-4" data-testid="tpl-signature-card">
      <div className="text-sm font-extrabold text-slate-800">Main inbox signature <span className="text-xs font-normal text-slate-400">— added to emails sent from the main inbox. Extra inboxes use their own signature (Admin → Email → Outreach inboxes → Edit).</span></div>
      <textarea data-testid="tpl-signature" rows={4} className="crm-input w-full text-sm" placeholder={"Abhinav Khanna\nPartnerships, Fundle.ai\n+91 …"} value={sig} onChange={(e) => setSig(e.target.value)} />
      <button data-testid="tpl-signature-save" onClick={save} className="crm-btn-primary !py-1.5 text-xs"><FloppyDisk size={14} /> Save signature</button>
    </div>
  );
}

function AutoEmailerBar({ items, loaded, onEdit }) {
  const autos = items.filter((t) => t.mode === "auto" && t.active);
  return (
    <div className="crm-card border-l-4 border-emerald-500 p-4" data-testid="tpl-auto-bar">
      <div className="mb-2 text-sm font-extrabold text-slate-800">Auto emailer <span className="text-xs font-normal text-slate-500">— goes out on its own to every brand with an email</span></div>
      {!loaded && <div className="text-xs text-slate-400">Loading…</div>}
      {loaded && autos.length === 0 && <div className="text-xs text-slate-500">No template is set to Auto. Edit any template and set Sending to “Auto”.</div>}
      {autos.map((t) => (
        <div key={t.id} className="flex flex-wrap items-center gap-3 rounded-lg bg-emerald-50/60 p-2">
          <div className="min-w-0 flex-1"><div className="font-bold text-slate-800">{t.name} {t.audience && t.audience !== "all" && <span className="text-[10px] font-semibold text-sky-700">· {AUD[t.audience]}</span>}</div><div className="truncate text-xs text-slate-600">{t.subject}</div></div>
          <button data-testid={`tpl-auto-edit-${t.id}`} onClick={() => onEdit(t)} className="crm-btn-primary !py-1.5 text-xs">Edit auto emailer</button>
        </div>
      ))}
    </div>
  );
}

export function EmailTemplates() {
  const [items, setItems] = useState([]);
  const [edit, setEdit] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const load = () => API.get("/outreach/templates").then(({ data }) => { setItems(data); setLoaded(true); }).catch((e) => toast.error(apiErr(e)));
  useEffect(() => { load(); }, []);
  const del = async (t) => { if (!window.confirm(`Delete “${t.name}”?`)) return; try { await API.delete(`/outreach/templates/${t.id}`); load(); } catch (e) { toast.error(apiErr(e)); } };
  if (edit) return <Editor t={edit} onDone={() => { setEdit(null); load(); }} />;
  return (
    <div className="space-y-3" data-testid="tpl-list">
      <AutoEmailerBar items={items} loaded={loaded} onEdit={setEdit} />
      <SignatureCard />
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">Auto templates go to every brand with an email (within the daily limit). Manual ones appear next to each contact on the brand page.</p>
        <button data-testid="tpl-new" onClick={() => setEdit({})} className="crm-btn-primary !py-1.5 text-xs"><Plus size={14} /> New template</button>
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {items.map((t) => (
          <div key={t.id} className="crm-card flex flex-col gap-2 p-4" data-testid={`tpl-card-${t.id}`}>
            <div className="flex items-center gap-2">
              <span className="font-bold text-slate-800">{t.name}</span>
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${t.mode === "auto" ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600"}`} data-testid={`tpl-mode-badge-${t.id}`}>{t.mode === "auto" ? "AUTO EMAILER" : "MANUAL"}</span>
              {t.step > 1 && <span className="rounded-full bg-violet-50 px-2 py-0.5 text-[10px] font-bold text-violet-700" data-testid={`tpl-step-badge-${t.id}`}>Follow-up {t.step - 1} · day {t.wait_days}</span>}
              {t.audience && t.audience !== "all" && <span className="rounded-full bg-sky-50 px-2 py-0.5 text-[10px] font-bold text-sky-700" data-testid={`tpl-audience-badge-${t.id}`}>{AUD[t.audience]}</span>}
              <span className="text-[10px] font-semibold text-slate-400" data-testid={`tpl-tailor-label-${t.id}`}>{t.ai_tailor === false ? "sent as written" : "AI-personalised"}</span>
              {!t.active && <span className="rounded-full bg-rose-50 px-2 py-0.5 text-[10px] font-bold text-rose-600">OFF</span>}
            </div>
            <div className="text-xs font-semibold text-slate-600">{t.subject}</div>
            <div className="text-[11px] text-slate-400">{t.attachments?.length || 0} attachment(s) · {t.video_links?.length || 0} video link(s)</div>
            <div className="mt-auto flex gap-2">
              <button data-testid={`tpl-edit-${t.id}`} onClick={() => setEdit(t)} className="crm-btn-secondary !py-1 text-xs">Edit</button>
              <button data-testid={`tpl-delete-${t.id}`} onClick={() => del(t)} className="crm-btn-secondary !py-1 text-xs text-rose-600"><Trash size={14} /></button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
