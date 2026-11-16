import React, { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { LinkedinLogo, MagnifyingGlass, FloppyDisk, Plugs, Trash, Play } from "@phosphor-icons/react";
import { API, apiErr } from "../lib/api";
import { useAuth } from "../context/AuthContext";
import { Link } from "react-router-dom";
import { ContactsReport } from "./LinkedInContacts";

const DEG = { 1: "1st", 2: "2nd", 3: "3rd" };

function SearchTab({ regions }) {
  const [f, setF] = useState({ keywords: "", title: "", company: "", regions: ["India"], network: [], page: 1 });
  const [rs, setRs] = useState(null);
  const [sel, setSel] = useState({});
  const [busy, setBusy] = useState(false);
  const [wait, setWait] = useState("");
  const poll = async (jid, account) => {
    for (let i = 0; i < 60; i++) {
      await new Promise((ok) => setTimeout(ok, 3000));
      const { data } = await API.get(`/linkedin-agent/search/${jid}`);
      if (data.status === "done") return data;
      if (data.status === "failed") throw new Error(data.error);
      setWait(data.status === "running" ? `${account}'s Chrome is searching LinkedIn…` : `Waiting for ${account}'s Chrome extension (Chrome must be open; picks it up within 30 s)…`);
    }
    throw new Error("The Chrome extension didn't pick up the search. Open Chrome with LinkedIn logged in, click the extension, then try again.");
  };
  const run = async (page = 1) => {
    setBusy(true);
    try {
      let { data } = await API.post("/linkedin-agent/search", { ...f, page }, { timeout: 120000 });
      if (data.job_id) { setWait(`Sent to ${data.account}'s Chrome extension…`); data = await poll(data.job_id, data.account); }
      setRs(data); setF({ ...f, page }); setSel({});
    } catch (e) { toast.error(e.response ? apiErr(e) : e.message); }
    setWait(""); setBusy(false);
  };
  const save = async () => {
    const people = rs.items.filter((r) => sel[r.urn_id]).map((r) => ({ ...r, company: f.company || undefined, country: f.regions.length === 1 ? f.regions[0] : undefined }));
    try { const { data } = await API.post("/linkedin-agent/save", { people }); toast.success(`Saved ${data.saved} new · updated ${data.updated} · ${data.brands_enriched} existing brands enriched · ${data.brands_added} new brands${data.skipped ? ` · ${data.skipped} skipped (no company)` : ""}`); run(f.page); } catch (e) { toast.error(apiErr(e)); }
  };
  const tog = (arr, v) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  return (
    <div className="space-y-4">
      <div className="crm-card grid gap-3 p-4 md:grid-cols-3">
        <input data-testid="lis-keywords" className="crm-input" placeholder="Keywords e.g. founder retail" value={f.keywords} onChange={(e) => setF({ ...f, keywords: e.target.value })} />
        <input data-testid="lis-title" className="crm-input" placeholder="Title e.g. CMO, Head of Marketing" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} />
        <input data-testid="lis-company" className="crm-input" placeholder="Company e.g. Bata" value={f.company} onChange={(e) => setF({ ...f, company: e.target.value })} />
        <div className="flex flex-wrap gap-1.5 md:col-span-2" data-testid="lis-regions">{regions.map((r) => <button key={r} onClick={() => setF({ ...f, regions: tog(f.regions, r) })} className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${f.regions.includes(r) ? "bg-[#0A66C2] text-white" : "bg-slate-100 text-slate-600"}`}>{r}</button>)}</div>
        <div className="flex flex-wrap items-center gap-1.5">{[["F", "1st"], ["S", "2nd"], ["O", "3rd+"]].map(([k, l]) => <button key={k} data-testid={`lis-net-${k}`} onClick={() => setF({ ...f, network: tog(f.network, k) })} className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${f.network.includes(k) ? "bg-emerald-600 text-white" : "bg-slate-100 text-slate-600"}`}>{l}</button>)}
          <button data-testid="lis-search-btn" disabled={busy} onClick={() => run(1)} className="crm-btn-primary ml-auto !py-1.5 text-xs"><MagnifyingGlass size={14} /> {busy ? "Searching LinkedIn…" : "Search"}</button></div>
        {wait && <p data-testid="lis-wait" className="text-xs font-semibold text-[#0A66C2] md:col-span-3">{wait}</p>}
      </div>
      {rs && (
        <div className="crm-card p-4" data-testid="lis-results">
          <div className="mb-2 flex items-center gap-2 text-xs text-slate-500">{rs.items.length} people · page {rs.page} · via {rs.account}
            <button data-testid="lis-select-all" onClick={() => setSel(Object.fromEntries(rs.items.filter((r) => !r.in_crm).map((r) => [r.urn_id, true])))} className="ml-auto font-bold text-[#1557B0]">Select all new</button>
            <button data-testid="lis-save-btn" disabled={!Object.values(sel).some(Boolean)} onClick={save} className="crm-btn-primary !py-1 text-xs"><FloppyDisk size={14} /> Save to CRM</button></div>
          {rs.items.map((r) => (
            <label key={r.urn_id} className="flex items-center gap-3 border-t border-slate-100 py-2 text-sm" data-testid={`lis-row-${r.urn_id}`}>
              <input type="checkbox" disabled={r.in_crm} checked={!!sel[r.urn_id]} onChange={(e) => setSel({ ...sel, [r.urn_id]: e.target.checked })} />
              <div className="min-w-0 flex-1"><a href={r.linkedin} target="_blank" rel="noreferrer" className="font-bold text-slate-800 hover:text-[#0A66C2]">{r.name}</a> <span className="text-[11px] text-slate-400">{DEG[r.degree] || ""}</span>
                <div className="truncate text-xs text-slate-500">{r.headline} · {r.location}</div></div>
              {r.in_crm && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-bold text-emerald-700">In CRM</span>}
            </label>))}
          <div className="mt-2 flex justify-end gap-2"><button disabled={rs.page <= 1 || busy} onClick={() => run(rs.page - 1)} className="crm-btn-secondary !py-1 text-xs disabled:opacity-40">Prev</button><button data-testid="lis-next" disabled={rs.items.length < 10 || busy} onClick={() => run(rs.page + 1)} className="crm-btn-secondary !py-1 text-xs disabled:opacity-40">Next</button></div>
        </div>)}
    </div>
  );
}

function AddAccount({ done }) {
  const [a, setA] = useState({ name: "", li_at: "", jsessionid: "", email: "", password: "" });
  const go = async () => { try { const { data } = await API.post("/linkedin-agent/accounts", a, { timeout: 60000 }); data.test.ok ? toast.success(`Connected as ${data.test.name}`) : toast.error(data.test.error); done(); } catch (e) { toast.error(apiErr(e)); } };
  return (
    <div className="crm-card space-y-2 p-4" data-testid="lia-add">
      <b className="text-sm">Connect a LinkedIn account (server)</b>
      <p className="text-[11px] text-slate-500">Paste the <b>li_at</b> cookie into the <b>li_at</b> box: in Chrome on linkedin.com press F12 → Application → Cookies → https://www.linkedin.com → li_at → copy the Value. Leave email and password empty; password logins get LinkedIn's security check (CHALLENGE). <b>Most reliable: use the Chrome extension tab.</b></p>
      <div className="grid gap-2 md:grid-cols-2">
        {[["name", "Account name (e.g. Vineet)"], ["li_at", "li_at cookie value (starts with AQ…)"], ["jsessionid", "JSESSIONID cookie (optional)"], ["email", "LinkedIn email (only if no cookie)"], ["password", "LinkedIn password (only if no cookie)"]].map(([k, l]) =>
          <input key={k} data-testid={`lia-${k}`} type={k === "password" ? "password" : "text"} className="crm-input text-sm" placeholder={l} value={a[k]} onChange={(e) => setA({ ...a, [k]: e.target.value })} />)}
      </div>
      <button data-testid="lia-connect" onClick={go} className="crm-btn-primary !py-1.5 text-xs"><Plugs size={14} /> Connect & test</button>
    </div>
  );
}

function SettingsBox({ s, done }) {
  const [v, setV] = useState(s);
  useEffect(() => setV(s), [s]);
  const save = async () => { try { await API.put("/linkedin-agent/settings", v); toast.success("Saved"); done(); } catch (e) { toast.error(apiErr(e)); } };
  const sw = (k, l) => <label className="flex items-center gap-2 text-sm"><input type="checkbox" data-testid={`lia-${k}`} checked={!!v[k]} onChange={(e) => setV({ ...v, [k]: e.target.checked })} />{l}</label>;
  const num = (k, l) => <label className="text-xs text-slate-600">{l}<input type="number" data-testid={`lia-${k}`} className="crm-input mt-1 w-full" value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} /></label>;
  return (
    <div className="crm-card space-y-3 p-4" data-testid="lia-settings">
      <b className="text-sm">Agent jobs & daily limits (per account)</b>
      <div className="flex flex-wrap gap-5">{sw("lookup_on", "Find every contact's LinkedIn profile")}{sw("invite_on", "Send invites to 2nd/3rd-degree")}{sw("message_on", "Message 1st-degree connections")}</div>
      <div className="grid gap-3 md:grid-cols-3">{num("daily_lookups", "Profile lookups / day")}{num("daily_invites", "Invites / day (safe ≤ 20)")}{num("daily_messages", "Messages / day (safe ≤ 50)")}</div>
      <label className="block text-xs text-slate-600">Invite note ({(v.invite_note || "").length}/300) — {"{first_name} {brand} {role}"}<textarea data-testid="lia-invite_note" rows={2} className="crm-input mt-1 w-full" value={v.invite_note} onChange={(e) => setV({ ...v, invite_note: e.target.value })} /></label>
      <label className="block text-xs text-slate-600">Message to 1st-degree connections<textarea data-testid="lia-message" rows={3} className="crm-input mt-1 w-full" value={v.message} onChange={(e) => setV({ ...v, message: e.target.value })} /></label>
      <button data-testid="lia-save-settings" onClick={save} className="crm-btn-primary !py-1.5 text-xs">Save</button>
    </div>
  );
}

function AgentTab({ d, load, admin }) {
  const t = d.totals;
  const act = async (fn) => { try { await fn(); load(); } catch (e) { toast.error(apiErr(e)); } };
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-7" data-testid="lia-stats">{[["contacts", "Contacts"], ["checked", "Checked"], ["found", "Profiles found"], ["first_degree", "1st-degree"], ["invited", "Invited"], ["accepted", "Accepted"], ["messaged", "Messaged"]].map(([k, l]) =>
        <Link key={k} to={`/linkedin-contacts?status=all${k === "contacts" ? "" : `&filter=${k}`}`} className="crm-card p-3 transition-colors hover:bg-blue-50" data-testid={`lia-stat-link-${k}`}><div className="text-[11px] text-slate-500">{l}</div><div className="font-display text-xl font-extrabold text-[#1557B0]" data-testid={`lia-stat-${k}`}>{(t[k] || 0).toLocaleString("en-IN")}</div></Link>)}</div>
      <div className="grid gap-3 md:grid-cols-3" data-testid="lia-sources">{[["li_server", "Imported from LinkedIn (server)"], ["li_ext", "Added by Chrome extension"], ["li_search", "Saved from CRM search"]].map(([k, l]) =>
        <Link key={k} to={`/linkedin-contacts?status=all&source=${k}`} className="crm-card p-3 transition-colors hover:bg-indigo-50" data-testid={`lia-src-link-${k}`}><div className="text-[11px] text-slate-500">{l}</div>
          <div className="font-display text-xl font-extrabold text-indigo-700">{(t[k] || 0).toLocaleString("en-IN")} <span className="text-xs font-semibold text-slate-500">contacts · {(t[`${k}_brands`] || 0).toLocaleString("en-IN")} brands enriched/added</span></div></Link>)}</div>
      <div className="crm-card p-4" data-testid="lia-accounts">
        <div className="mb-2 flex items-center"><b className="text-sm">LinkedIn accounts</b><span className="ml-2 text-[11px] text-slate-400">Last run {d.last?.at || "—"} {d.last?.stats ? JSON.stringify(d.last.stats).replace(/[{}"]/g, " ") : ""}</span>
          {admin && <button data-testid="lia-run" onClick={() => act(() => API.post("/linkedin-agent/run"))} className="crm-btn-secondary ml-auto !py-1 text-xs"><Play size={13} /> Run now</button>}</div>
        {d.accounts.map((a) => (
          <div key={a.id} className="flex flex-wrap items-center gap-2 border-t border-slate-100 py-2 text-xs" data-testid={`lia-account-${a.id}`}>
            <LinkedinLogo size={16} weight="fill" className="text-[#0A66C2]" /><b>{a.name}</b>{a.type === "extension" && <span className="rounded-full bg-indigo-50 px-2 py-0.5 font-bold text-indigo-700">Chrome extension{a.last_seen ? ` · seen ${a.last_seen}` : " · not used yet"}</span>}{a.me_name && <span className="text-slate-500">({a.me_name})</span>}
            <span className={`rounded-full px-2 py-0.5 font-bold ${a.verified ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"}`}>{a.verified ? "Connected" : "Not connected"}</span>
            <span className="text-slate-500">Today: {a.today.lookup} lookups · {a.today.invite} invites · {a.today.message} messages</span>
            <Link to={`/linkedin-contacts?status=all&account=${encodeURIComponent(a.name)}`} data-testid={`lia-conns-${a.id}`} className="font-bold text-[#1557B0] hover:underline">{(a.connections || 0).toLocaleString("en-IN")} connections imported · {(a.connections_with_brand || 0).toLocaleString("en-IN")} with a company</Link>
            {a.import && <span className="w-full text-slate-600" data-testid={`lia-import-status-${a.id}`}>Connections import: {a.import.status} · {a.import.pulled} pulled · {a.import.saved} new · {a.import.updated} updated · {a.import.brands_enriched || 0} existing brands enriched · {a.import.brands_added} new brands · {a.import.skipped} skipped (no company in headline){a.import.error ? ` · ${a.import.error}` : ""}</span>}
            {a.last_error && <span className="w-full text-rose-600">{a.last_error === "CHALLENGE" ? "LinkedIn asked this server login for a security check (CHALLENGE). Delete it and reconnect with the li_at cookie, or use the Chrome extension (most reliable)." : a.last_error}</span>}
            {admin && <span className="ml-auto flex gap-2">{a.type !== "extension" && a.verified && <button data-testid={`lia-import-${a.id}`} onClick={() => act(async () => { await API.post(`/linkedin-agent/accounts/${a.id}/import-connections`); toast.success("Importing your 1st-degree connections — refresh in a minute"); })} className="crm-btn-primary !py-0.5 text-[11px]">Import my connections</button>}{a.type !== "extension" && <button data-testid={`lia-test-${a.id}`} onClick={() => act(async () => { const { data } = await API.post(`/linkedin-agent/accounts/${a.id}/test`, {}, { timeout: 60000 }); data.ok ? toast.success(`Connected as ${data.name}`) : toast.error(data.error); })} className="crm-btn-secondary !py-0.5 text-[11px]">Test</button>}
              <button data-testid={`lia-toggle-${a.id}`} onClick={() => act(() => API.put(`/linkedin-agent/accounts/${a.id}`, { active: !a.active }))} className="crm-btn-secondary !py-0.5 text-[11px]">{a.active ? "Pause" : "Resume"}</button>
              <button data-testid={`lia-delete-${a.id}`} onClick={() => window.confirm("Remove this LinkedIn account?") && act(() => API.delete(`/linkedin-agent/accounts/${a.id}`))} className="text-rose-600"><Trash size={14} /></button></span>}
          </div>))}
        {!d.accounts.length && <p className="py-3 text-xs text-slate-400">No LinkedIn account connected yet.</p>}
      </div>
      {admin && <AddAccount done={load} />}
      {admin && <SettingsBox s={d.settings} done={load} />}
    </div>
  );
}

function ExtensionTab() {
  const [name, setName] = useState("");
  const [key, setKey] = useState(null);
  const make = async () => { try { const { data } = await API.post("/linkedin-agent/ext/token", { name }); setKey(data); } catch (e) { toast.error(apiErr(e)); } };
  const dl = async () => {
    try {
      const r = await API.get("/linkedin-agent/extension.zip", { responseType: "blob" });
      const a = document.createElement("a"); a.href = URL.createObjectURL(r.data); a.download = "fundle-linkedin-extension.zip"; a.click();
    } catch (e) { toast.error(apiErr(e)); }
  };
  const steps = [
    <>Press <b>Download extension</b> below and unzip it. You'll get a folder called <b>fundle-linkedin</b>.</>,
    <>In Chrome, open <b>chrome://extensions</b>, turn on <b>Developer mode</b> (top right) and click <b>Load unpacked</b>. Pick the <b>fundle-linkedin</b> folder.</>,
    <>Pin it: click the puzzle icon in Chrome's toolbar, then the pin next to <b>Fundle CRM – LinkedIn Agent</b>.</>,
    <>Type your name below and press <b>Create key</b>. Copy the key; it's shown only once.</>,
    <>Click the extension → <b>Settings</b>. Paste the CRM address <b>{window.location.origin}</b> and the key, set working hours and days, then press <b>Save & test connection</b>.</>,
    <>Make sure you're logged in to <b>linkedin.com</b> in the same Chrome. Open the extension and switch <b>Agent running</b> on.</>,
    <>Keep Chrome open. Every minute it takes one task from the CRM (find a profile, send an invite or send a message), within the limits on the <b>Agent & accounts</b> tab.</>,
    <>On LinkedIn, a blue <b>Save to Fundle</b> button appears on people-search pages and profiles. It saves those people straight into the CRM.</>,
  ];
  return (
    <div className="space-y-4" data-testid="lia-ext">
      <div className="crm-card p-4">
        <b className="text-sm">Install the Fundle LinkedIn Chrome extension</b>
        <ol className="mt-2 list-decimal space-y-1.5 pl-5 text-sm text-slate-700">{steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
        <button data-testid="lia-ext-download" onClick={dl} className="crm-btn-primary mt-3 !py-1.5 text-xs"><FloppyDisk size={14} /> Download extension</button>
      </div>
      <div className="crm-card space-y-2 p-4">
        <b className="text-sm">Create an extension key</b>
        <div className="flex gap-2"><input data-testid="lia-ext-name" className="crm-input text-sm" placeholder="Whose Chrome is this? e.g. Hardik" value={name} onChange={(e) => setName(e.target.value)} />
          <button data-testid="lia-ext-create" onClick={make} className="crm-btn-primary !py-1.5 text-xs">Create key</button></div>
        {key && <div className="rounded-lg bg-amber-50 p-3 text-xs" data-testid="lia-ext-key">Key for <b>{key.name}</b> (copy it now; it's shown only once):<div className="mt-1 flex gap-2"><code className="break-all rounded bg-white px-2 py-1">{key.token}</code>
          <button onClick={() => navigator.clipboard.writeText(key.token).then(() => toast.success("Copied"))} className="crm-btn-secondary !py-0.5 text-[11px]">Copy</button></div></div>}
        <p className="text-[11px] text-slate-500">Each key appears as an account on the Agent & accounts tab. Pause or delete it there to cut that browser off.</p>
      </div>
    </div>
  );
}

export default function LinkedInAgent() {
  const { user } = useAuth();
  const [tab, setTab] = useState("search");
  const [d, setD] = useState(null);
  const load = useCallback(() => API.get("/linkedin-agent/overview", { timeout: 60000 }).then(({ data }) => setD(data)).catch((e) => toast.error(apiErr(e))), []);
  useEffect(() => { load(); }, [load]);
  return (
    <div className="space-y-4 p-6" data-testid="linkedin-agent-page">
      <div><h1 className="flex items-center gap-2 font-display text-2xl font-extrabold text-slate-900"><LinkedinLogo size={26} weight="fill" className="text-[#0A66C2]" /> LinkedIn Prospecting</h1>
        <p className="text-sm text-slate-500">Direct from our CRM through your connected LinkedIn accounts. The agent runs every 15 minutes within the daily limits below.</p></div>
      <div className="flex gap-2">{[["search", "Search & save"], ["report", "LinkedIn contacts report"], ["agent", "Agent & accounts"], ["ext", "Chrome extension"]].map(([k, l]) => <button key={k} data-testid={`lia-tab-${k}`} onClick={() => setTab(k)} className={`rounded-full px-3 py-1.5 text-xs font-bold ${tab === k ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-600"}`}>{l}</button>)}</div>
      {!d ? <p className="text-sm text-slate-400">Loading…</p> : tab === "search" ? <SearchTab regions={d.regions} /> : tab === "report" ? <ContactsReport embedded initial={{ source: "any_li", status: "all" }} /> : tab === "ext" ? <ExtensionTab /> : <AgentTab d={d} load={load} admin={user?.role === "admin"} />}
    </div>
  );
}
