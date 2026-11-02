import React, { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { UploadSimple, DownloadSimple, FileXls, CheckCircle, WarningCircle, X } from "@phosphor-icons/react";
import { API, apiErr } from "../lib/api";
import { useCatalogMaps, useCatalogs } from "../context/AuthContext";

const DUP_MODES = [
  ["skip", "Skip rows whose phone already exists"],
  ["update", "Update the existing lead with the file's values"],
  ["create", "Create anyway and mark as duplicate"],
];

const download = (blob, name) => {
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  a.click();
  URL.revokeObjectURL(url);
};

function UploadStep({ onParsed }) {
  const ref = useRef(null);
  const [busy, setBusy] = useState(false);
  const getTemplate = async () => {
    try {
      const res = await API.get("/import/template.xlsx", { responseType: "blob" });
      download(res.data, "fundle_leads_import_template.xlsx");
    } catch (e) { toast.error(apiErr(e)); }
  };
  const pick = async (file) => {
    if (!file) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const { data } = await API.post("/import/preview", fd);
      onParsed(file, data);
    } catch (e) { toast.error(apiErr(e)); } finally { setBusy(false); if (ref.current) ref.current.value = ""; }
  };
  return (
    <div className="space-y-4">
      <label data-testid="import-dropzone" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); pick(e.dataTransfer.files[0]); }}
        className="flex cursor-pointer flex-col items-center gap-2 rounded-2xl border-2 border-dashed border-slate-200 bg-slate-50 px-6 py-10 text-center transition-colors hover:border-[#1A73E8] hover:bg-[#1A73E8]/5">
        <FileXls size={36} className="text-[#1A73E8]" />
        <span className="text-sm font-bold text-slate-800">{busy ? "Reading file…" : "Drop your Excel / CSV here or click to choose"}</span>
        <span className="text-xs text-slate-500">.xlsx or .csv · up to 50,000 rows · first row must be column headings</span>
        <input ref={ref} data-testid="import-file-input" type="file" accept=".xlsx,.csv" className="hidden" onChange={(e) => pick(e.target.files[0])} />
      </label>
      <button data-testid="import-template-download" onClick={getTemplate} className="crm-btn-secondary !py-1.5 text-xs"><DownloadSimple size={14} /> Download sample template</button>
    </div>
  );
}

function MapStep({ preview, mapping, setMapping, opts, setOpts, users }) {
  const used = new Set(Object.values(mapping).filter(Boolean));
  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500" data-testid="import-row-count">{preview.total_rows.toLocaleString("en-IN")} rows found. Match each column in your file to a CRM field (unmatched columns are ignored).</p>
      <div className="max-h-64 overflow-auto rounded-xl border border-slate-100">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-slate-50 text-left text-[10px] uppercase tracking-wider text-slate-500">
            <tr><th className="px-3 py-2">Your column</th><th className="px-3 py-2">Sample</th><th className="px-3 py-2">CRM field</th></tr>
          </thead>
          <tbody>
            {preview.headers.map((h, i) => (
              <tr key={h} className="border-t border-slate-100">
                <td className="px-3 py-1.5 font-semibold text-slate-700">{h}</td>
                <td className="max-w-[160px] truncate px-3 py-1.5 text-slate-500">{preview.rows[0]?.[i] || "—"}</td>
                <td className="px-3 py-1.5">
                  <select data-testid={`import-map-${i}`} className="crm-select w-full !py-1 text-xs" value={mapping[h] || ""} onChange={(e) => setMapping({ ...mapping, [h]: e.target.value })}>
                    <option value="">— Ignore —</option>
                    {preview.fields.filter((f) => f.key === mapping[h] || !used.has(f.key)).map((f) => (
                      <option key={f.key} value={f.key}>{f.group === "Custom" ? `${f.label} (custom)` : f.label}</option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs font-semibold text-slate-500">If the phone already exists
          <select data-testid="import-duplicate-mode" className="crm-select mt-1 w-full" value={opts.duplicate_mode} onChange={(e) => setOpts({ ...opts, duplicate_mode: e.target.value })}>
            {DUP_MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label className="text-xs font-semibold text-slate-500">Assign leads to
          <select data-testid="import-assign-mode" className="crm-select mt-1 w-full" value={opts.assign_mode} onChange={(e) => setOpts({ ...opts, assign_mode: e.target.value })}>
            <option value="column">Use the "Assigned Agent" column (if mapped)</option>
            <option value="round_robin">Share equally among callers</option>
            <option value="user">One specific user</option>
            <option value="none">Leave unassigned</option>
          </select>
        </label>
        {opts.assign_mode === "user" && (
          <label className="text-xs font-semibold text-slate-500">User
            <select data-testid="import-assign-user" className="crm-select mt-1 w-full" value={opts.user_id || ""} onChange={(e) => setOpts({ ...opts, user_id: e.target.value })}>
              <option value="">— choose —</option>
              {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
          </label>
        )}
        <label className="text-xs font-semibold text-slate-500">Source for rows without one
          <input data-testid="import-default-source" className="crm-input mt-1 w-full" value={opts.default_source} onChange={(e) => setOpts({ ...opts, default_source: e.target.value })} />
        </label>
      </div>
      <label className="flex items-center gap-2 text-xs text-slate-600">
        <input data-testid="import-run-automations" type="checkbox" checked={opts.run_automations} onChange={(e) => setOpts({ ...opts, run_automations: e.target.checked })} />
        Run new-lead automations (e.g. welcome WhatsApp) for imported leads
      </label>
    </div>
  );
}

function ProgressStep({ job }) {
  const pct = job.total ? Math.round((job.processed / job.total) * 100) : 100;
  const done = job.status === "done";
  const errCsv = () => download(new Blob([["Row,Issue", ...(job.errors || []).map((e) => `${e.row},"${String(e.error).replace(/"/g, '""')}"`)].join("\n")], { type: "text/csv" }), "import_issues.csv");
  return (
    <div className="space-y-4" data-testid="import-progress">
      <div className="h-2 overflow-hidden rounded-full bg-slate-100"><div className="h-full bg-[#1A73E8] transition-[width] duration-500" style={{ width: `${pct}%` }} /></div>
      <p className="text-xs font-semibold text-slate-600" data-testid="import-status">{done ? "Import finished" : `Importing… ${job.processed.toLocaleString("en-IN")} of ${job.total.toLocaleString("en-IN")}`}</p>
      <div className="grid grid-cols-4 gap-2 text-center">
        {[["created", "Created", "text-emerald-600"], ["updated", "Updated", "text-[#1557B0]"], ["skipped", "Skipped", "text-amber-600"], ["failed", "Failed", "text-rose-600"]].map(([k, l, c]) => (
          <div key={k} className="rounded-xl bg-slate-50 p-3" data-testid={`import-count-${k}`}>
            <div className={`font-display text-xl font-extrabold ${c}`}>{(job[k] || 0).toLocaleString("en-IN")}</div>
            <div className="text-[10px] uppercase tracking-wider text-slate-500">{l}</div>
          </div>
        ))}
      </div>
      {done && job.error_count > 0 && (
        <div className="rounded-xl border border-amber-100 bg-amber-50 p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="flex items-center gap-1 text-xs font-bold text-amber-700"><WarningCircle size={14} /> {job.error_count.toLocaleString("en-IN")} rows need attention</span>
            <button data-testid="import-errors-download" onClick={errCsv} className="text-xs font-bold text-[#1557B0] hover:underline">Download list</button>
          </div>
          <ul className="max-h-28 space-y-0.5 overflow-auto text-[11px] text-amber-800" data-testid="import-errors-list">
            {(job.errors || []).slice(0, 50).map((e, i) => <li key={i}>Row {e.row}: {e.error}</li>)}
          </ul>
        </div>
      )}
      {done && job.error_count === 0 && <p className="flex items-center gap-1 text-xs font-bold text-emerald-600"><CheckCircle size={14} /> All rows imported</p>}
    </div>
  );
}

export const ImportLeadsModal = ({ onClose, onDone }) => {
  const { userById } = useCatalogMaps();
  const { refreshCatalogs } = useCatalogs();
  const users = Object.values(userById || {}).filter((u) => u.active !== false);
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [mapping, setMapping] = useState({});
  const [opts, setOpts] = useState({ duplicate_mode: "skip", assign_mode: "column", default_source: "Bulk Upload", run_automations: false });
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!job || job.status === "done") return undefined;
    const t = setInterval(async () => {
      try {
        const { data } = await API.get(`/import/jobs/${job.id}`);
        setJob(data);
        if (data.status === "done") {
          onDone?.();
          refreshCatalogs?.();
          const parts = [`${data.created} created`, data.updated && `${data.updated} updated`, data.skipped && `${data.skipped} skipped`, data.failed && `${data.failed} failed`].filter(Boolean);
          (data.fatal ? toast.error(`Import stopped: ${data.fatal}`) : toast.success(`Import finished — ${parts.join(", ")}`));
        }
      } catch (e) { /* keep polling */ }
    }, 1500);
    return () => clearInterval(t);
  }, [job, onDone, refreshCatalogs]);

  const start = async () => {
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("mapping", JSON.stringify(mapping));
      fd.append("options", JSON.stringify(opts));
      const { data } = await API.post("/import/leads", fd);
      setJob({ id: data.job_id, total: data.total, processed: 0, status: "running" });
    } catch (e) { toast.error(apiErr(e)); } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onClick={() => !busy && onClose()}>
      <div onClick={(e) => e.stopPropagation()} className="w-full max-w-2xl rounded-2xl bg-white p-6 shadow-xl" data-testid="import-modal">
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h3 className="flex items-center gap-2 font-display text-lg font-extrabold text-slate-900"><UploadSimple size={20} className="text-[#1557B0]" /> Bulk Upload Leads</h3>
            <p className="text-xs text-slate-500">{file ? file.name : "Upload all your leads with their details from Excel or CSV."}</p>
          </div>
          <button data-testid="import-close" onClick={onClose} className="rounded-lg p-1 text-slate-400 hover:bg-slate-100"><X size={18} /></button>
        </div>
        {!preview && <UploadStep onParsed={(f, p) => { setFile(f); setPreview(p); setMapping(p.mapping); }} />}
        {preview && !job && <MapStep preview={preview} mapping={mapping} setMapping={setMapping} opts={opts} setOpts={setOpts} users={users} />}
        {job && <ProgressStep job={job} />}
        <div className="mt-5 flex justify-end gap-2">
          {preview && !job && <button data-testid="import-back" onClick={() => { setPreview(null); setFile(null); }} className="crm-btn-secondary">Choose another file</button>}
          {preview && !job && <button data-testid="import-start" onClick={start} disabled={busy} className="crm-btn-primary">{busy ? "Starting…" : `Import ${preview.total_rows.toLocaleString("en-IN")} leads`}</button>}
          {job?.status === "done" && <button data-testid="import-finish" onClick={onClose} className="crm-btn-primary">Done</button>}
        </div>
      </div>
    </div>
  );
};
