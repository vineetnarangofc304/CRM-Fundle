import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Link } from "react-router-dom";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { UsersThree, TrendUp, ClockCountdown, Warning, Trophy, Tag, Broadcast, CurrencyInr } from "@phosphor-icons/react";
import { API, leadsUrl, apiErr } from "../lib/api";
import { inr } from "../lib/money";
import { toast } from "sonner";
import { StageBadge } from "../components/Bits";

function Kpi({ icon: Icon, label, value, tone = "blue", testid, onClick }) {
  const tones = {
    blue: "bg-[#1A73E8]/10 text-[#1557B0]",
    violet: "bg-[#0B1F3A]/10 text-[#0B1F3A]",
    green: "bg-emerald-50 text-emerald-600",
    amber: "bg-amber-50 text-amber-600",
    rose: "bg-rose-50 text-rose-600",
  };
  return (
    <button onClick={onClick} data-testid={testid}
      className="crm-card p-5 text-left transition-all hover:-translate-y-[2px] hover:border-[#1A73E8]/40 hover:shadow-md cursor-pointer">
      <div className={`mb-3 flex h-10 w-10 items-center justify-center rounded-xl ${tones[tone]}`}>
        <Icon size={20} weight="duotone" />
      </div>
      <p className="font-display text-2xl font-extrabold text-slate-900">{value?.toLocaleString("en-IN") ?? "—"}</p>
      <p className="mt-0.5 text-xs font-semibold uppercase tracking-wider text-slate-500">{label}</p>
    </button>
  );
}

function KpiSkeleton() {
  return (
    <div className="crm-card p-5" data-testid="kpi-skeleton">
      <div className="mb-3 h-10 w-10 animate-pulse rounded-xl bg-slate-100" />
      <div className="h-7 w-16 animate-pulse rounded bg-slate-100" />
      <div className="mt-2 h-3 w-24 animate-pulse rounded bg-slate-100" />
    </div>
  );
}

function PanelSkeleton({ rows = 5, className = "" }) {
  return (
    <div className={`crm-card p-5 ${className}`} data-testid="panel-skeleton">
      <div className="h-4 w-40 animate-pulse rounded bg-slate-100" />
      <div className="mt-5 space-y-3">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="h-3 animate-pulse rounded bg-slate-100" style={{ width: `${90 - i * 12}%` }} />
        ))}
      </div>
    </div>
  );
}

function ConvTable({ title, icon: Icon, rows, testid, onRowClick, nameLabel = "Source", tone = "blue" }) {
  const accent = tone === "violet" ? "text-[#0B1F3A]" : tone === "green" ? "text-emerald-600" : "text-[#1557B0]";
  return (
    <div className="crm-card p-5" data-testid={testid}>
      <div className="flex items-center gap-2">
        {Icon && <Icon size={18} weight="duotone" className={accent} />}
        <h3 className="font-display text-base font-bold text-slate-800">{title}</h3>
      </div>
      {(!rows || rows.length === 0) ? (
        <p className="mt-8 text-center text-sm text-slate-400" data-testid={`${testid}-empty`}>No data in this range</p>
      ) : (
        <table className="mt-3 w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 text-left text-[11px] uppercase tracking-wider text-slate-400">
              <th className="py-2">{nameLabel}</th>
              <th className="py-2 text-right">Leads</th>
              <th className="py-2 text-right">Conv.</th>
              <th className="py-2 text-right">Rate</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} data-testid={`${testid}-row-${i}`}
                onClick={() => onRowClick && onRowClick(r)}
                className="cursor-pointer border-b border-slate-50 last:border-0 transition-colors hover:bg-[#1A73E8]/5">
                <td className="max-w-[180px] truncate py-2 font-semibold text-slate-700" title={r.name}>{r.name}</td>
                <td className="py-2 text-right text-slate-600">{r.leads.toLocaleString("en-IN")}</td>
                <td className="py-2 text-right font-bold text-emerald-600">{r.converted.toLocaleString("en-IN")}</td>
                <td className="py-2 text-right">
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-bold text-slate-600">{r.rate}%</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function Dashboard() {
  const [kpis, setKpis] = useState(null);
  const [panels, setPanels] = useState(null);
  const [srcConv, setSrcConv] = useState(null);
  const [range, setRange] = useState({ from: "", to: "" });
  const navigate = useNavigate();

  const load = () => {
    const params = {};
    if (range.from) params.date_from = range.from;
    if (range.to) params.date_to = range.to;
    setKpis(null);
    setPanels(null);
    setSrcConv(null);
    // Fetch the sections in PARALLEL — the fast KPI counts render immediately while the
    // heavier aggregation panels stream in behind their own skeletons (no single blocking spinner).
    API.get("/reports/dashboard", { params: { ...params, section: "kpis" } })
      .then(({ data }) => setKpis(data))
      .catch((e) => { const m = apiErr(e); if (m) toast.error(m); setKpis({}); });
    API.get("/reports/dashboard", { params: { ...params, section: "panels" } })
      .then(({ data }) => setPanels(data))
      .catch(() => setPanels({}));
    API.get("/reports/source-conversion", { params })
      .then(({ data }) => setSrcConv(data))
      .catch(() => setSrcConv({}));
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [range.from, range.to]);

  const meta = kpis || panels || {};
  const today = meta.today;
  const monthStart = meta.month_start;
  const hasRange = range.from || range.to;
  const chartData = (panels?.by_day || []).map((d) => ({ day: (d._id || "").slice(5), fullDay: d._id, count: d.count }));
  const rangeParams = {};
  if (range.from) rangeParams.date_from = range.from;
  if (range.to) rangeParams.date_to = range.to;

  return (
    <div className="space-y-6 p-6" data-testid="dashboard-page">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-extrabold text-slate-900">Dashboard</h1>
          <p className="text-sm text-slate-500">
            Your lead engine at a glance — click any card or row to drill down
            {hasRange && kpis && <span className="ml-1 font-semibold text-[#1557B0]">· In range: {(kpis.leads_range || 0).toLocaleString("en-IN")} leads · {(kpis.converted_range || 0).toLocaleString("en-IN")} converted</span>}
          </p>
        </div>
        <div className="flex items-end gap-2 rounded-xl border border-slate-200 bg-white p-2" data-testid="dashboard-date-filter">
          <div>
            <label className="text-[10px] font-bold uppercase tracking-wider text-slate-400">From</label>
            <input type="date" value={range.from} onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))} className="crm-input !w-36 !py-1.5 text-sm" data-testid="dashboard-date-from" />
          </div>
          <div>
            <label className="text-[10px] font-bold uppercase tracking-wider text-slate-400">To</label>
            <input type="date" value={range.to} onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))} className="crm-input !w-36 !py-1.5 text-sm" data-testid="dashboard-date-to" />
          </div>
          {hasRange && <button onClick={() => setRange({ from: "", to: "" })} className="crm-btn-secondary !py-1.5 text-xs" data-testid="dashboard-date-clear">Clear</button>}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
        {!kpis ? (
          Array.from({ length: 6 }).map((_, i) => <KpiSkeleton key={i} />)
        ) : (
          <>
            <Kpi icon={UsersThree} label="Leads Today" value={kpis.leads_today} testid="kpi-leads-today"
              onClick={() => navigate(leadsUrl({ date_from: today }))} />
            <Kpi icon={UsersThree} label="Leads MTD" value={kpis.leads_mtd} tone="violet" testid="kpi-leads-mtd"
              onClick={() => navigate(leadsUrl({ date_from: monthStart }))} />
            <Kpi icon={TrendUp} label="Converted MTD" value={kpis.converted_mtd} tone="green" testid="kpi-converted-mtd"
              onClick={() => navigate(leadsUrl({ lead_stage: "Converted", date_from: monthStart }))} />
            <Kpi icon={ClockCountdown} label="Follow-ups Today" value={kpis.followups_today} tone="amber" testid="kpi-followups-today"
              onClick={() => navigate("/followups")} />
            <Kpi icon={Warning} label="Overdue Follow-ups" value={kpis.followups_overdue} tone="rose" testid="kpi-followups-overdue"
              onClick={() => navigate("/followups")} />
            <Kpi icon={UsersThree} label="Total Active Leads" value={kpis.total_leads} testid="kpi-total-leads"
              onClick={() => navigate("/leads")} />
          </>
        )}
      </div>

      {kpis && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3" data-testid="deal-value-strip">
          <Kpi icon={CurrencyInr} label={`Open Pipeline Value · ${(kpis.pipeline_count || 0).toLocaleString("en-IN")} ${kpis.pipeline_count === 1 ? "deal" : "deals"}`} value={inr(kpis.pipeline_value)} testid="kpi-pipeline-value"
            onClick={() => navigate("/leads")} />
          <Kpi icon={CurrencyInr} label="Won Value MTD" value={inr(kpis.won_value_mtd)} tone="green" testid="kpi-won-value-mtd"
            onClick={() => navigate(leadsUrl({ lead_stage: "Converted", date_from: monthStart }))} />
          <Kpi icon={CurrencyInr} label={hasRange ? "Won Value · In Range" : "Won Value · This Month"} value={inr(kpis.won_value_range)} tone="violet" testid="kpi-won-value-range"
            onClick={() => navigate(leadsUrl({ lead_stage: "Converted", date_from: meta.range_start, date_to: meta.range_end }))} />
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {!panels ? (
          <>
            <PanelSkeleton rows={6} className="lg:col-span-2" />
            <PanelSkeleton rows={5} />
          </>
        ) : (
          <>
            <div className="crm-card p-5 lg:col-span-2">
              <h3 className="font-display text-base font-bold text-slate-800">Incoming Leads — last 14 days <span className="text-xs font-normal text-slate-400">(click a bar)</span></h3>
              <div className="mt-4 h-64 min-h-64 w-full min-w-0">
                <ResponsiveContainer width="100%" height={256} minWidth={200}>
                  <BarChart data={chartData} onClick={(e) => e?.activePayload?.[0] && navigate(leadsUrl({ date_from: e.activePayload[0].payload.fullDay, date_to: e.activePayload[0].payload.fullDay }))}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                    <XAxis dataKey="day" tick={{ fontSize: 11, fill: "#64748b" }} />
                    <YAxis tick={{ fontSize: 11, fill: "#64748b" }} width={40} />
                    <Tooltip cursor={{ fill: "#f1f5f9" }} />
                    <Bar dataKey="count" fill="#1A73E8" radius={[6, 6, 0, 0]} className="cursor-pointer" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="crm-card p-5">
              <h3 className="font-display text-base font-bold text-slate-800">Funnel — Lead Stage</h3>
              <div className="mt-4 space-y-3" data-testid="funnel-by-stage">
                {(panels.by_stage || []).map((s) => {
                  const max = Math.max(...panels.by_stage.map((x) => x.count), 1);
                  return (
                    <button key={String(s._id)} className="block w-full text-left"
                      data-testid={`funnel-stage-${String(s._id || "none").replace(/\s/g, "-")}`}
                      onClick={() => navigate(leadsUrl({ lead_stage: s._id || "__none__" }))}>
                      <div className="mb-1 flex items-center justify-between">
                        <StageBadge stage={s._id || "Undefined"} />
                        <span className="text-xs font-bold text-slate-600">{s.count.toLocaleString("en-IN")}</span>
                      </div>
                      <div className="h-2 rounded-full bg-slate-100 transition-all hover:h-2.5">
                        <div className="h-full rounded-full bg-gradient-to-r from-[#1A73E8] to-[#0B1F3A]"
                          style={{ width: `${Math.max((s.count / max) * 100, 2)}%` }} />
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {!panels ? (
          <>
            <PanelSkeleton rows={5} />
            <PanelSkeleton rows={5} />
          </>
        ) : (
          <>
            <div className="crm-card p-5">
              <div className="flex items-center gap-2">
                <Trophy size={18} weight="duotone" className="text-amber-500" />
                <h3 className="font-display text-base font-bold text-slate-800">Caller Leaderboard — MTD</h3>
              </div>
              <table className="mt-3 w-full text-sm" data-testid="leaderboard-table">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-[11px] uppercase tracking-wider text-slate-400">
                    <th className="py-2">Caller</th>
                    <th className="py-2 text-right">Leads</th>
                    <th className="py-2 text-right">Converted</th>
                  </tr>
                </thead>
                <tbody>
                  {(panels.leaderboard || []).map((l, i) => (
                    <tr key={i} data-testid={`leaderboard-row-${l._id}`}
                      onClick={() => l._id && navigate(leadsUrl({ user_id: l._id, date_from: monthStart }))}
                      className="cursor-pointer border-b border-slate-50 last:border-0 transition-colors hover:bg-[#1A73E8]/5">
                      <td className="py-2 font-semibold text-slate-700">{l.name}</td>
                      <td className="py-2 text-right text-slate-600">{l.count}</td>
                      <td className="py-2 text-right font-bold text-emerald-600">{l.converted}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="crm-card p-5">
              <div className="flex items-center gap-2">
                <Tag size={18} weight="duotone" className="text-[#0B1F3A]" />
                <h3 className="font-display text-base font-bold text-slate-800">Top Dispositions — MTD</h3>
              </div>
              <div className="mt-4 space-y-2" data-testid="top-tags">
                {(panels.top_tags || []).map((t) => {
                  const max = Math.max(...panels.top_tags.map((x) => x.count), 1);
                  return (
                    <button key={t._id} className="flex w-full items-center gap-3 text-left"
                      data-testid={`top-tag-${t._id}`}
                      onClick={() => navigate(leadsUrl({ tags: t._id, date_from: monthStart }))}>
                      <span className="w-44 truncate text-xs font-semibold text-slate-600 hover:text-[#1557B0]">{t.name}</span>
                      <div className="h-2 flex-1 rounded-full bg-slate-100">
                        <div className="h-2 rounded-full bg-[#0B1F3A]/70" style={{ width: `${Math.max((t.count / max) * 100, 2)}%` }} />
                      </div>
                      <span className="w-12 text-right text-xs font-bold text-slate-600">{t.count}</span>
                    </button>
                  );
                })}
              </div>
              <Link to="/reports" className="mt-4 inline-block text-xs font-bold text-[#1557B0] hover:underline">
                Open full reports →
              </Link>
            </div>
          </>
        )}
      </div>

      <div data-testid="conversion-section">
        <h2 className="mb-3 font-display text-lg font-bold text-slate-900">Conversion by Source &amp; UTM</h2>
        {!srcConv ? (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <PanelSkeleton rows={5} />
            <PanelSkeleton rows={5} />
            <PanelSkeleton rows={5} />
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <ConvTable title="By Source" icon={TrendUp} rows={srcConv.by_source} testid="conv-by-source"
              nameLabel="Source" tone="blue"
              onRowClick={(r) => navigate(leadsUrl({ source_lead: r.name, ...rangeParams }))} />
            <ConvTable title="By UTM Source" icon={Broadcast} rows={srcConv.by_utm_source} testid="conv-by-utm-source"
              nameLabel="UTM Source" tone="violet"
              onRowClick={(r) => navigate(leadsUrl({ utm_source: r.name, ...rangeParams }))} />
            <ConvTable title="By UTM Campaign" icon={Tag} rows={srcConv.by_utm_campaign} testid="conv-by-utm-campaign"
              nameLabel="UTM Campaign" tone="green"
              onRowClick={(r) => navigate(leadsUrl({ utm_campaign: r.name, ...rangeParams }))} />
          </div>
        )}
      </div>
    </div>
  );
}
