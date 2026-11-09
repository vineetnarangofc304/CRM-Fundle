import React, { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ArrowLeft, LinkedinLogo, Globe, Sparkle, UserPlus, Storefront } from "@phosphor-icons/react";
import { API, apiErr } from "../lib/api";
import { SEGMENT_STYLE } from "../lib/prospecting";
import { useAuth } from "../context/AuthContext";

const Row = ({ label, children, testid }) => (
  <div className="flex gap-3 border-b border-slate-50 py-2 text-sm" data-testid={testid}>
    <span className="w-40 shrink-0 text-[11px] font-bold uppercase tracking-wider text-slate-400">{label}</span>
    <span className="min-w-0 break-words text-slate-800">{children ?? <span className="text-slate-300">—</span>}</span>
  </div>
);

const ext = (url, label) => url ? <a href={url} target="_blank" rel="noreferrer" className="font-semibold text-[#1557B0] hover:underline">{label || url}</a> : null;

const EMAIL_BADGE = {
  published: ["FOUND PUBLISHED", "bg-emerald-50 text-emerald-700"],
  pattern: ["GUESSED FROM PATTERN", "bg-amber-50 text-amber-700"],
};

function ContactsCard({ p, reload }) {
  const find = async () => { try { await API.post(`/prospecting/prospects/${p.id}/contacts`); toast.success("Email finder started — about a minute"); setTimeout(reload, 45000); } catch (e) { toast.error(apiErr(e)); } };
  const draft = async (i) => { try { await API.post(`/prospecting/prospects/${p.id}/draft`, { contact_index: i }); toast.success("Outreach drafted — review it in Outreach"); reload(); } catch (e) { toast.error(apiErr(e)); } };
  const cs = p.contacts || [];
  return (
    <div className="crm-card p-4" data-testid="prospect-contacts">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">Decision makers</span>
        {p.fit_score != null && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700" data-testid="prospect-fit-score">Loyalty fit {p.fit_score}/100</span>}
        {p.email_pattern && <span className="text-[11px] text-slate-500">Email format: <b>{p.email_pattern}</b></span>}
        {p.mx_ok === false && <span className="text-[11px] text-rose-600">Domain has no mail server</span>}
        <div className="ml-auto flex gap-2">
          {p.outreach_seq && <Link to="/outreach" className="text-xs font-bold text-[#1557B0]" data-testid="prospect-outreach-link">Outreach: {p.outreach_status?.replace("_", " ")} →</Link>}
          <button data-testid="prospect-find-emails" onClick={find} disabled={p.contacts_status === "running"} className="crm-btn-secondary !py-1 text-xs">{p.contacts_status === "running" ? "Searching…" : cs.length ? "Search again" : "Find emails"}</button>
        </div>
      </div>
      {cs.length === 0 ? <p className="text-xs text-slate-400">{p.contacts_status === "failed" ? `Search failed: ${p.contacts_error || ""}` : "Not searched yet. It runs automatically after deep research, or click Find emails."}</p> : (
        <table className="w-full text-sm">
          <tbody>
            {cs.map((c, i) => (
              <tr key={i} className="border-t border-slate-50" data-testid={`prospect-contact-${i}`}>
                <td className="py-2 font-bold text-slate-800">{c.name}<div className="text-[11px] font-normal text-slate-500">{c.role}</div></td>
                <td className="text-xs">{c.email ? <>{c.email} {EMAIL_BADGE[c.email_status] && <span className={`ml-1 rounded px-1 text-[9px] font-bold ${EMAIL_BADGE[c.email_status][1]}`}>{EMAIL_BADGE[c.email_status][0]}</span>}</> : <span className="text-slate-300">no email found</span>}</td>
                <td>{c.linkedin && <a href={c.linkedin} target="_blank" rel="noreferrer" className="text-[#0A66C2]"><LinkedinLogo size={16} weight="fill" /></a>}</td>
                <td className="text-right">{c.email && <button data-testid={`prospect-draft-${i}`} onClick={() => draft(i)} className="text-xs font-bold text-[#1557B0] hover:underline">Draft outreach</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function ProspectDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const [p, setP] = useState(null);
  const [denied, setDenied] = useState(false);
  const load = useCallback(() => API.get(`/prospecting/prospects/${id}`).then(({ data }) => setP(data)).catch((e) => {
    if (e?.response?.status === 403) setDenied(true); else toast.error(apiErr(e));
  }), [id]);
  useEffect(() => { if (["admin", "manager"].includes(user?.role)) load(); else setDenied(true); }, [load, user]);
  useEffect(() => { if (p?.status !== "enriching") return undefined; const t = setInterval(load, 8000); return () => clearInterval(t); }, [p?.status, load]);
  const research = async () => { try { await API.post(`/prospecting/prospects/${id}/research`); toast.success("Deep research started — takes about a minute"); load(); } catch (e) { toast.error(apiErr(e)); } };
  const toLead = async () => { try { const { data } = await API.post(`/prospecting/prospects/${id}/lead`); toast.success("Lead updated"); setP({ ...p, lead_id: data.lead_id }); } catch (e) { toast.error(apiErr(e)); } };
  if (denied) return <div className="p-8 text-sm text-slate-500" data-testid="prospect-denied">AI Prospecting is available to admins and managers only.</div>;
  if (!p) return <div className="p-8 text-sm text-slate-400">Loading…</div>;
  return (
    <div className="space-y-5 p-6" data-testid="prospect-detail">
      <Link to="/prospecting" className="flex items-center gap-1 text-xs font-bold text-slate-500 hover:text-[#1557B0]" data-testid="prospect-back"><ArrowLeft size={14} /> All prospects</Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-3xl font-extrabold text-slate-900" data-testid="prospect-name">{p.brand_name}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <span>{p.category}</span><span>·</span><span>{p.country}</span>
            {p.segment && <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${SEGMENT_STYLE[p.segment]}`}>{p.segment}</span>}
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-bold uppercase" data-testid="prospect-status">{p.status === "enriching" ? "Researching…" : p.status}</span>
          </div>
        </div>
        <div className="flex gap-2">
          <button data-testid="prospect-research-now" onClick={research} disabled={p.status === "enriching"} className="crm-btn-secondary !py-1.5 text-xs"><Sparkle size={14} /> {p.status === "enriched" ? "Re-research" : "Deep research now"}</button>
          {p.lead_id ? <Link data-testid="prospect-open-lead" to={`/leads/${p.lead_id}`} className="crm-btn-primary !py-1.5 text-xs"><UserPlus size={14} /> Open lead #{p.lead_id}</Link>
            : <button data-testid="prospect-add-lead" onClick={toLead} className="crm-btn-primary !py-1.5 text-xs"><UserPlus size={14} /> Add to Leads</button>}
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="crm-card p-4 lg:col-span-1">
          <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-slate-400"><Storefront size={16} /> Stores</div>
          <div className="mt-1 font-display text-4xl font-extrabold tabular-nums text-slate-900" data-testid="prospect-store-count">{p.store_count != null ? p.store_count.toLocaleString("en-IN") : "—"}</div>
          <p className="text-xs text-slate-500">{p.store_count_note || (p.cities_present ? `${p.cities_present} cities` : "")}</p>
          <div className="mt-4 text-[11px] font-bold uppercase tracking-wider text-slate-400">CEO</div>
          <div className="text-lg font-bold text-slate-800" data-testid="prospect-ceo">{p.ceo_name || "—"}</div>
          <div className="text-xs text-slate-500">{p.ceo_title}</div>
          {p.ceo_linkedin && <a data-testid="prospect-ceo-linkedin" href={p.ceo_linkedin} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs font-bold text-[#0A66C2]"><LinkedinLogo size={16} weight="fill" /> LinkedIn profile</a>}
          {p.description && <p className="mt-4 text-sm text-slate-600">{p.description}</p>}
        </div>
        <div className="crm-card p-4 lg:col-span-2">
          <Row label="Official name">{p.official_name}</Row>
          <Row label="Parent company">{p.parent_company}</Row>
          <Row label="Website" testid="prospect-website">{p.website && <span className="inline-flex items-center gap-1"><Globe size={14} />{ext(p.website)}</span>}</Row>
          <Row label="Company LinkedIn">{ext(p.company_linkedin)}</Row>
          <Row label="Business email">{p.contact_email}</Row>
          <Row label="Phone">{p.contact_phone}</Row>
          <Row label="Headquarters">{p.hq_city}</Row>
          <Row label="Founded">{p.founded_year}</Row>
          <Row label="Founders">{p.founders?.length ? p.founders.join(", ") : null}</Row>
          <Row label="Revenue">{p.annual_revenue}</Row>
          <Row label="Employees">{p.employees}</Row>
          <Row label="Existing loyalty">{p.has_loyalty_program}</Row>
          <Row label="Marketplaces">{p.marketplaces?.length ? p.marketplaces.join(", ") : null}</Row>
          <Row label="Found via">{p.discovered_via}</Row>
          <Row label="Researched">{p.enriched_at ? `${p.enriched_at} UTC` : p.last_error ? `Last try failed: ${p.last_error}` : "Not yet"}</Row>
        </div>
      </div>
      <ContactsCard p={p} reload={load} />
      {p.sources?.length > 0 && (
        <div className="crm-card p-4" data-testid="prospect-sources">
          <div className="mb-2 text-[11px] font-bold uppercase tracking-wider text-slate-400">Sources</div>
          <ul className="space-y-1 text-xs">{p.sources.map((u) => <li key={u} className="truncate">{ext(u)}</li>)}</ul>
        </div>
      )}
      <p className="text-[11px] text-slate-400">AI-researched data — verify key facts (especially emails and store counts) before outreach.</p>
    </div>
  );
}
