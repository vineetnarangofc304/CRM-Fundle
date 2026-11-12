import React, { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Binoculars, Play, Pause, Storefront, UserCircle, LinkedinLogo, MagnifyingGlass, ArrowsClockwise } from "@phosphor-icons/react";
import { API, apiErr } from "../lib/api";
import { SEGMENT_STYLE } from "../lib/prospecting";
import { useAuth } from "../context/AuthContext";

const Stat = ({ label, value, testid }) => (
  <div className="crm-card px-4 py-3" data-testid={testid}>
    <div className="font-display text-2xl font-extrabold tabular-nums text-slate-900">{(value || 0).toLocaleString("en-IN")}</div>
    <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">{label}</div>
  </div>
);

function EnginePanel({ ov, isAdmin, reload }) {
  const s = ov.settings;
  const save = async (patch) => {
    try { await API.put("/prospecting/settings", { ...s, ...patch }); toast.success("Engine settings saved"); reload(); }
    catch (e) { toast.error(apiErr(e)); }
  };
  const runNow = async () => { try { await API.post("/prospecting/run"); if (ov.lock.ai_blocked_at) toast.warning("The AI provider is limiting requests right now. The engine will retry automatically"); else toast.success("Research batch started"); setTimeout(reload, 2500); } catch (e) { toast.error(apiErr(e)); } };
  const toggleCountry = (c) => save({ countries: s.countries.includes(c) ? s.countries.filter((x) => x !== c) : [...s.countries, c] });
  return (
    <div className="crm-card space-y-3 p-4" data-testid="prospect-engine-panel">
      {ov.lock.ai_blocked_at && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700" data-testid="prospect-ai-credits-alert">
          <b>AI provider is refusing requests</b> (credit or rate limit, since {ov.lock.ai_blocked_at} UTC). Brands are kept in the queue and nothing is marked failed.
          The engine retries automatically every 15 min. If it lasts, check Profile → Manage plan → Universal Key balance.
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <span className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-bold ${ov.running ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"}`} data-testid="prospect-engine-status">
          <span className={`h-2 w-2 rounded-full ${ov.running ? "animate-pulse bg-emerald-500" : "bg-slate-400"}`} />
          {ov.running ? `Researching… (started ${ov.lock.started} UTC)` : !s.enabled ? "Paused" : ov.lock.ai_blocked_at ? "Waiting — AI limit, retrying" : ov.night_window ? "Idle — next batch within 15 min" : "Daytime — finding contacts & researching (new brands 10 PM–9 AM IST)"}
        </span>
        {ov.lock.last_stats && <span className="text-xs text-slate-500">Last batch: +{ov.lock.last_stats.added} brands · {ov.lock.last_stats.enriched} researched</span>}
        <span className="text-xs text-slate-500">{ov.pending_enrich.toLocaleString("en-IN")} brands waiting for deep research</span>
        <div className="ml-auto flex gap-2">
          <button data-testid="prospect-run-now" onClick={runNow} disabled={ov.running || !s.enabled} className="crm-btn-primary !py-1.5 text-xs"><Play size={14} /> Run now</button>
          {isAdmin && <button data-testid="prospect-toggle-contacts-only" onClick={() => save({ phase: s.phase === "contacts" ? "auto" : "contacts" })} className={s.phase === "contacts" ? "crm-btn-primary !py-1.5 text-xs" : "crm-btn-secondary !py-1.5 text-xs"}>{s.phase === "contacts" ? "Contacts only ON — resume adding brands" : "Stop adding brands (contacts only)"}</button>}
          {isAdmin && <button data-testid="prospect-toggle-engine" onClick={() => save({ enabled: !s.enabled })} className="crm-btn-secondary !py-1.5 text-xs">{s.enabled ? <><Pause size={14} /> Pause engine</> : <><Play size={14} /> Resume engine</>}</button>}
        </div>
      </div>
      {isAdmin && (
        <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3 text-xs">
          <span className="font-bold text-slate-500">Mode</span>
          <select data-testid="prospect-phase" className="crm-select !w-60 !py-1 text-xs" value={s.phase} onChange={(e) => save({ phase: e.target.value })}>
            <option value="auto">Auto — find brands first, then research</option>
            <option value="discovery">Only find new brands</option>
            <option value="enrichment">Only research details</option>
            <option value="contacts">Contacts only — no new brands, 24/7</option>
          </select>
          <span className="ml-3 font-bold text-slate-500">Parallel</span>
          <select data-testid="prospect-concurrency" className="crm-select !w-16 !py-1 text-xs" value={s.concurrency} onChange={(e) => save({ concurrency: Number(e.target.value) })}>
            {[1, 2, 3, 4, 5, 6, 8, 10].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <span className="ml-3 font-bold text-slate-500">Countries</span>
          {ov.all_countries.map((c) => (
            <button key={c} data-testid={`prospect-country-${c.toLowerCase().replace(/\s/g, "-")}`} onClick={() => toggleCountry(c)}
              className={`rounded-full border px-2.5 py-1 font-semibold transition-colors ${s.countries.includes(c) ? "border-[#1A73E8] bg-[#1A73E8]/10 text-[#1557B0]" : "border-slate-200 text-slate-500 hover:bg-slate-50"}`}>{c}</button>
          ))}
        </div>
      )}
    </div>
  );
}

function CategoryGrid({ rows, active, onPick }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-5" data-testid="prospect-category-grid">
      {rows.map((r) => {
        const on = active === `${r.country}|${r.category}`;
        return (
          <button key={`${r.country}|${r.category}`} onClick={() => onPick(on ? null : r)} data-testid={`prospect-cat-${r.category.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}
            className={`rounded-xl border p-3 text-left transition-[border-color,background-color,transform] hover:-translate-y-0.5 ${on ? "border-[#1A73E8] bg-[#1A73E8]/5" : "border-slate-100 bg-white hover:border-slate-200"}`}>
            <div className="flex items-start justify-between gap-2">
              <span className="text-xs font-bold leading-tight text-slate-800">{r.category}</span>
              {r.saturated && <span className="rounded-full bg-emerald-50 px-1.5 text-[9px] font-bold text-emerald-600">DONE</span>}
            </div>
            <div className="mt-2 flex items-baseline gap-1">
              <span className="font-display text-xl font-extrabold tabular-nums text-slate-900">{r.brands}</span>
              <span className="text-[10px] text-slate-400">brands · {r.enriched} researched</span>
            </div>
            <div className="mt-1 text-[10px] text-slate-400">{r.offline} with stores · {r.d2c} D2C · {r.country}</div>
          </button>
        );
      })}
    </div>
  );
}

function ProspectTable({ filters, setFilters, ov }) {
  const [data, setData] = useState({ items: [], total: 0 });
  const navigate = useNavigate();
  const seq = useRef(0);
  useEffect(() => {
    const params = Object.fromEntries(Object.entries(filters).filter(([, v]) => v !== "" && v != null));
    const my = ++seq.current;
    const t = setTimeout(() => {
      API.get("/prospecting/prospects", { params }).then(({ data: d }) => { if (my === seq.current) setData(d); })
        .catch((e) => { if (my === seq.current) toast.error(apiErr(e)); });
    }, 300);
    return () => clearTimeout(t);
  }, [filters]);
  const set = (k, v) => setFilters({ ...filters, [k]: v, page: 1 });
  return (
    <div className="crm-card p-4" data-testid="prospect-table">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative"><MagnifyingGlass size={14} className="absolute left-2.5 top-2.5 text-slate-400" />
          <input data-testid="prospect-search" className="crm-input !w-56 !pl-8" placeholder="Search brand" value={filters.q || ""} onChange={(e) => set("q", e.target.value)} /></div>
        <select data-testid="prospect-filter-category" className="crm-select !w-52" value={filters.category || ""} onChange={(e) => set("category", e.target.value)}>
          <option value="">All categories</option>{ov.all_categories.map((c) => <option key={c}>{c}</option>)}
        </select>
        <select data-testid="prospect-filter-segment" className="crm-select !w-40" value={filters.segment || ""} onChange={(e) => set("segment", e.target.value)}>
          <option value="">All segments</option>{ov.segments.map((c) => <option key={c}>{c}</option>)}
        </select>
        <select data-testid="prospect-filter-status" className="crm-select !w-36" value={filters.status || ""} onChange={(e) => set("status", e.target.value)}>
          <option value="">Any status</option><option value="discovered">Found</option><option value="enriched">Researched</option><option value="failed">Research failed</option>
        </select>
        <input data-testid="prospect-filter-min-stores" type="number" min="0" className="crm-input !w-28" placeholder="Min stores" value={filters.min_stores || ""} onChange={(e) => set("min_stores", e.target.value)} />
        <label className="flex items-center gap-1 text-xs text-slate-600"><input data-testid="prospect-filter-has-ceo" type="checkbox" checked={!!filters.has_ceo} onChange={(e) => set("has_ceo", e.target.checked || "")} /> Has CEO</label>
        <select data-testid="prospect-sort" className="crm-select !w-36" value={filters.sort || "store_count"} onChange={(e) => set("sort", e.target.value)}>
          <option value="store_count">Most stores</option><option value="score">Best fit</option><option value="recent">Newest</option><option value="brand">A–Z</option>
        </select>
        <span className="ml-auto text-xs text-slate-500" data-testid="prospect-total">{data.total.toLocaleString("en-IN")} brands</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-[10px] uppercase tracking-wider text-slate-400">
            <tr><th className="py-2">Brand</th><th>Category</th><th>Segment</th><th className="text-right">Stores</th><th className="text-right">Fit</th><th>CEO</th><th>Status</th></tr>
          </thead>
          <tbody>
            {data.items.length === 0 && <tr><td colSpan={7} className="py-10 text-center text-xs text-slate-400" data-testid="prospect-empty">No brands yet — the engine fills this list automatically. Click "Run now" to start.</td></tr>}
            {data.items.map((p) => (
              <tr key={p.id} onClick={() => navigate(`/prospecting/${p.id}`)} className="cursor-pointer border-t border-slate-100 hover:bg-slate-50" data-testid={`prospect-row-${p.id}`}>
                <td className="py-2"><div className="font-bold text-slate-800">{p.brand_name}</div><div className="text-[11px] text-slate-400">{p.hq_city || p.country}{p.parent_company ? ` · ${p.parent_company}` : ""}</div></td>
                <td className="text-xs text-slate-600">{p.category}</td>
                <td>{p.segment && <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${SEGMENT_STYLE[p.segment]}`}>{p.segment}</span>}</td>
                <td className="text-right tabular-nums font-semibold">{p.store_count != null ? p.store_count.toLocaleString("en-IN") : "—"}</td>
                <td className="text-right tabular-nums text-xs font-bold text-emerald-700">{p.fit_score ?? "—"}</td>
                <td className="text-xs">{p.ceo_name ? <span className="flex items-center gap-1">{p.ceo_name}{p.ceo_linkedin && <a href={p.ceo_linkedin} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="text-[#0A66C2]"><LinkedinLogo size={14} weight="fill" /></a>}</span> : <span className="text-slate-300">—</span>}</td>
                <td className="text-[10px] font-bold uppercase text-slate-400">{p.status === "enriched" ? <span className="text-emerald-600">Researched</span> : p.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.total > data.limit && (
        <div className="mt-3 flex justify-end gap-2 text-xs">
          <button data-testid="prospect-prev" disabled={(filters.page || 1) <= 1} onClick={() => setFilters({ ...filters, page: (filters.page || 1) - 1 })} className="crm-btn-secondary !py-1">Prev</button>
          <span className="py-1 text-slate-500">Page {filters.page || 1} of {Math.ceil(data.total / data.limit)}</span>
          <button data-testid="prospect-next" disabled={(filters.page || 1) * data.limit >= data.total} onClick={() => setFilters({ ...filters, page: (filters.page || 1) + 1 })} className="crm-btn-secondary !py-1">Next</button>
        </div>
      )}
    </div>
  );
}

export default function Prospecting() {
  const { user } = useAuth();
  const [ov, setOv] = useState(null);
  const [denied, setDenied] = useState(false);
  const [sp] = useSearchParams();
  const [filters, setFilters] = useState({ category: sp.get("category") || "", page: 1 });
  const load = useCallback(() => API.get("/prospecting/overview").then(({ data }) => setOv(data)).catch((e) => {
    if (e?.response?.status === 403) setDenied(true); else toast.error(apiErr(e));
  }), []);
  const allowed = ["admin", "manager"].includes(user?.role);
  useEffect(() => {
    if (!allowed || denied) return undefined;
    load(); const t = setInterval(load, 20000); return () => clearInterval(t);
  }, [load, allowed, denied]);
  if (!allowed || denied) return <div className="p-8 text-sm text-slate-500" data-testid="prospecting-denied">AI Prospecting is available to admins and managers only.</div>;
  if (!ov) return <div className="p-8 text-sm text-slate-400">Loading prospecting…</div>;
  const rows = ov.categories.filter((r) => ov.settings.countries.includes(r.country) || r.brands > 0);
  return (
    <div className="space-y-5 p-6" data-testid="prospecting-page">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="flex items-center gap-2 font-display text-3xl font-extrabold text-slate-900"><Binoculars size={30} className="text-[#1A73E8]" /> AI Prospecting</h1>
          <p className="text-sm text-slate-500">Brands with stores and D2C brands for the loyalty platform — found and researched automatically, every brand added to Leads.</p>
        </div>
        <button data-testid="prospect-refresh" onClick={load} className="crm-btn-secondary !py-1.5 text-xs"><ArrowsClockwise size={14} /> Refresh</button>
      </div>
      <EnginePanel ov={ov} isAdmin={user.role === "admin"} reload={load} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Brands found" value={ov.totals.brands} testid="prospect-stat-brands" />
        <Stat label="Deep researched" value={ov.totals.enriched} testid="prospect-stat-enriched" />
        <Stat label="With stores" value={ov.totals.offline} testid="prospect-stat-offline" />
        <Stat label="D2C / E-com" value={ov.totals.d2c} testid="prospect-stat-d2c" />
        <Stat label="CEO identified" value={ov.totals.with_ceo} testid="prospect-stat-ceo" />
      </div>
      <CategoryGrid rows={rows} active={filters.category ? `${filters.country || rows[0]?.country}|${filters.category}` : null}
        onPick={(r) => setFilters({ ...filters, category: r ? r.category : "", country: r ? r.country : "", page: 1 })} />
      <ProspectTable filters={filters} setFilters={setFilters} ov={ov} />
      <details className="crm-card p-4 text-xs" data-testid="prospect-run-log">
        <summary className="cursor-pointer font-bold text-slate-600">Recent research activity</summary>
        <ul className="mt-2 space-y-1 text-slate-500">
          {ov.recent_runs.map((r, i) => <li key={i}><span className={r.ok ? "text-emerald-600" : "text-rose-600"}>●</span> {r.at} · {r.kind === "discover" ? `Find brands — ${r.category} (${r.country})` : `Research — ${r.brand}`} · {r.detail} · {r.secs}s</li>)}
        </ul>
      </details>
      <p className="text-[11px] text-slate-400">Tip: every brand is also in <Link className="font-bold text-[#1557B0]" to="/leads?source_lead=AI+Prospecting">Leads</Link> with Category, Segment and Stores filters.</p>
      <Storefront className="hidden" /><UserCircle className="hidden" />
    </div>
  );
}
