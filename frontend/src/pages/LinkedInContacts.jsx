import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { LinkedinLogo, MagnifyingGlass, CopySimple, CheckCircle, NotePencil } from "@phosphor-icons/react";
import { API, apiErr } from "../lib/api";
import { liLink } from "../lib/linkedin";

const fill = (t, r) => t.replace(/\{first_name\}/g, (r.name || "").split(" ")[0]).replace(/\{name\}/g, r.name || "")
  .replace(/\{brand\}/g, r.brand || "").replace(/\{role\}/g, r.role || "").replace(/\{stores\}/g, r.store_count ?? "");

function TemplateBox({ tpl, setTpl }) {
  const [open, setOpen] = useState(false);
  const [txt, setTxt] = useState(tpl);
  useEffect(() => setTxt(tpl), [tpl]);
  const save = async () => { try { await API.put("/linkedin/report/template", { text: txt }); setTpl(txt); setOpen(false); toast.success("Message saved"); } catch (e) { toast.error(apiErr(e)); } };
  if (!open) return <button data-testid="li-template-edit" onClick={() => setOpen(true)} className="crm-btn-secondary !py-1.5 text-xs"><NotePencil size={14} /> Edit message</button>;
  return (
    <div className="crm-card w-full p-3" data-testid="li-template-box">
      <textarea data-testid="li-template-input" value={txt} onChange={(e) => setTxt(e.target.value)} rows={4} className="crm-input w-full text-sm" />
      <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
        <span>Use {"{first_name}"}, {"{brand}"}, {"{role}"}, {"{stores}"} · {txt.length} chars (connection notes allow 300)</span>
        <span className="flex gap-2"><button onClick={() => setOpen(false)} className="crm-btn-secondary !py-1 text-xs">Cancel</button><button data-testid="li-template-save" onClick={save} className="crm-btn-primary !py-1 text-xs">Save</button></span>
      </div>
    </div>
  );
}

function Row({ r, tpl, reload }) {
  const l = liLink(r, r.brand);
  const go = async () => {
    try { await navigator.clipboard.writeText(fill(tpl, r)); toast.success("Message copied — press Message on LinkedIn and paste"); } catch (_) { toast.error("Couldn't copy — select the message manually"); }
    if (l) window.open(l.href, "_blank", "noopener");
  };
  const mark = async () => { try { await API.post("/linkedin/report/sent", { prospect_id: r.prospect_id, name: r.name, sent: !r.li_sent_at }); reload(); } catch (e) { toast.error(apiErr(e)); } };
  const id = `${r.prospect_id}-${r.name}`.replace(/\W+/g, "-");
  return (
    <tr className="border-t border-slate-100" data-testid={`li-row-${id}`}>
      <td className="py-2 pr-3"><div className="font-bold text-slate-800">{r.name}</div><div className="text-[11px] text-slate-500">{r.role}</div></td>
      <td className="pr-3"><Link to={`/prospecting/${r.prospect_id}`} className="font-semibold text-[#1557B0] hover:underline">{r.brand}</Link><div className="text-[11px] text-slate-500">{r.store_count ?? "—"} stores · {r.category}</div></td>
      <td className="pr-3 text-xs">{r.email || <span className="text-slate-300">—</span>}</td>
      <td className="pr-3">{l && <a href={l.href} target="_blank" rel="noreferrer" data-testid={`li-link-${id}`} className="inline-flex items-center gap-1 whitespace-nowrap text-xs font-bold text-[#0A66C2]"><LinkedinLogo size={14} weight="fill" />{l.direct ? "Profile" : "Search"}</a>}</td>
      <td className="whitespace-nowrap text-right">
        <button data-testid={`li-copy-open-${id}`} onClick={go} className="crm-btn-primary !py-1 text-xs"><CopySimple size={13} /> Copy & open</button>
        <button data-testid={`li-mark-${id}`} onClick={mark} className={`ml-2 rounded-full px-2 py-1 text-[11px] font-bold ${r.li_sent_at ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>
          {r.li_sent_at ? <><CheckCircle size={12} weight="fill" className="inline" /> Sent{r.li_sent_by ? ` · ${r.li_sent_by}` : ""}</> : "Mark sent"}
        </button>
      </td>
    </tr>
  );
}

export default function LinkedInContacts() {
  const [f, setF] = useState({ q: "", status: "todo", source: "all", page: 1 });
  const [d, setD] = useState(null);
  const [tpl, setTpl] = useState("");
  const load = useCallback(() => API.get("/linkedin/report", { params: { ...f, limit: 50 } }).then(({ data }) => { setD(data); setTpl(data.template); }).catch((e) => toast.error(apiErr(e))), [f]);
  useEffect(() => { load(); }, [load]);
  const pages = d ? Math.max(1, Math.ceil(d.total / d.limit)) : 1;
  return (
    <div className="space-y-4 p-6" data-testid="linkedin-contacts-page">
      <div><h1 className="flex items-center gap-2 font-display text-2xl font-extrabold text-slate-900"><LinkedinLogo size={26} weight="fill" className="text-[#0A66C2]" /> LinkedIn Contacts</h1>
        <p className="text-sm text-slate-500">Every decision-maker with a LinkedIn route. "Copy & open" copies your message and opens their profile — press Message (or Connect → Add a note) and paste.</p></div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative"><MagnifyingGlass size={14} className="absolute left-2 top-2.5 text-slate-400" />
          <input data-testid="li-search" placeholder="Brand, person or title…" defaultValue={f.q} onKeyDown={(e) => e.key === "Enter" && setF({ ...f, q: e.target.value, page: 1 })} className="crm-input !pl-7 text-sm" /></div>
        <select data-testid="li-status" className="crm-select" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value, page: 1 })}>
          <option value="todo">Not messaged yet</option><option value="sent">Messaged</option><option value="all">All</option></select>
        <select data-testid="li-source" className="crm-select" value={f.source} onChange={(e) => setF({ ...f, source: e.target.value, page: 1 })}>
          <option value="all">All contacts</option><option value="sheet">From uploaded sheets (direct profiles)</option></select>
        <span className="text-xs text-slate-500" data-testid="li-total">{d ? d.total.toLocaleString("en-IN") : "…"} contacts</span>
        <span className="ml-auto" /><TemplateBox tpl={tpl} setTpl={setTpl} />
      </div>
      <div className="crm-card overflow-x-auto p-4">
        <table className="w-full text-sm"><thead className="text-left text-[11px] uppercase tracking-wider text-slate-400"><tr><th className="py-2">Person</th><th>Brand</th><th>Email</th><th>LinkedIn</th><th /></tr></thead>
          <tbody>{d?.items.map((r) => <Row key={`${r.prospect_id}-${r.name}`} r={r} tpl={tpl} reload={load} />)}
            {d && !d.items.length && <tr><td colSpan={5} className="py-10 text-center text-slate-400">No contacts match.</td></tr>}</tbody></table>
        <div className="mt-3 flex items-center justify-end gap-2 text-xs">
          <button data-testid="li-prev" disabled={f.page <= 1} onClick={() => setF({ ...f, page: f.page - 1 })} className="crm-btn-secondary !py-1 text-xs">Prev</button>
          <span>Page {f.page} / {pages}</span>
          <button data-testid="li-next" disabled={f.page >= pages} onClick={() => setF({ ...f, page: f.page + 1 })} className="crm-btn-secondary !py-1 text-xs">Next</button>
        </div>
      </div>
    </div>
  );
}
