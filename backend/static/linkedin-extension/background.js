importScripts("li.js");

const DEF = { crmUrl: "", token: "", enabled: false, startHour: 9, endHour: 19, days: [1, 2, 3, 4, 5, 6] };
const cfg = async () => ({ ...DEF, ...(await chrome.storage.local.get(Object.keys(DEF))) });

async function crm(path, opts = {}) {
  const c = await cfg();
  if (!c.crmUrl || !c.token) throw new Error("Set the CRM address and extension key in Options");
  const r = await fetch(c.crmUrl.replace(/\/+$/, "") + "/api" + path, { ...opts, headers: { "x-ext-token": c.token, "x-ext-version": chrome.runtime.getManifest().version, "content-type": "application/json", ...(opts.headers || {}) } });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || `CRM error ${r.status}`);
  return d;
}

async function log(line, ok = true) {
  const { logs = [] } = await chrome.storage.local.get("logs");
  logs.unshift({ at: new Date().toLocaleString(), line, ok });
  await chrome.storage.local.set({ logs: logs.slice(0, 40) });
}

function inHours(c) {
  const d = new Date();
  return c.days.includes(d.getDay()) && d.getHours() >= c.startHour && d.getHours() < c.endHour;
}

async function doSearch(t) {
  const filters = Object.entries(t.filters || {}).map(([k, vals]) => `(key:${k},value:List(${vals.map((x) => encodeURIComponent(x)).join("|")}))`);
  const res = { job_id: t.job_id, ok: true, results: [] };
  try {
    res.results = (await liSearch({ keywords: t.keywords, filters, start: t.start || 0 })).map((r) => ({ ...r, distance: r.distance || "" }));
  } catch (e) { res.ok = false; res.error = String(e.message || e); }
  await crm("/ext/search_result", { method: "POST", body: JSON.stringify(res) });
  await log(`CRM search "${t.keywords || Object.values(t.filters || {}).flat().join(" ")}" — ${res.ok ? `${res.results.length} people` : res.error}`, res.ok);
  return res;
}

async function doTask(t) {
  if (t.kind === "search") return doSearch(t);
  const res = { kind: t.kind, prospect_id: t.prospect_id, name: t.name, ok: true, text: t.text };
  try {
    if (t.kind === "lookup") res.results = (await liSearch({ keywords: t.keywords })).slice(0, 5);
    else if (t.kind === "invite") await liInvite(t.urn, t.text);
    else if (t.kind === "message") await liMessage(t.urn, t.text);
  } catch (e) { res.ok = false; res.error = String(e.message || e); }
  const r = await crm("/ext/result", { method: "POST", body: JSON.stringify(res) });
  await log(`${t.kind} · ${t.name} (${t.brand}) — ${res.ok ? (t.kind === "lookup" ? (r.matched ? "profile found" : "no match") : "done") : res.error}`, res.ok);
  return res;
}

let importing = false;
// Pulls all 1st-degree connections, 40 per page; progress is saved so it resumes by itself if Chrome stops the worker.
async function importLoop() {
  if (importing) return { skipped: "already importing" };
  importing = true;
  let { importNext: start = 0, importTot: tot = { pulled: 0, saved: 0, updated: 0, skipped: 0 } } = await chrome.storage.local.get(["importNext", "importTot"]);
  const report = (status, error) => crm("/ext/import_status", { method: "POST", body: JSON.stringify({ ...tot, status, error }) }).catch(() => {});
  try {
    for (; start < 40000; start += 40) {
      const people = await liConnections(start);
      if (!people.length) break;
      const d = await crm("/ext/save", { method: "POST", body: JSON.stringify({ people }) });
      tot = { pulled: tot.pulled + people.length, saved: tot.saved + d.saved, updated: tot.updated + d.updated, skipped: tot.skipped + d.skipped };
      await chrome.storage.local.set({ importNext: start + 40, importTot: tot, importStatus: `Importing… ${tot.pulled} pulled, ${tot.saved} new (continues by itself)` });
      if ((start / 40) % 5 === 0) await report("running");
      await new Promise((ok) => setTimeout(ok, 4000 + Math.random() * 5000));
    }
    const line = `Imported connections: ${tot.pulled} pulled · ${tot.saved} new · ${tot.updated} updated · ${tot.skipped} without a company in their headline (kept in the CRM's connection list)`;
    await chrome.storage.local.set({ importOn: false, importStatus: line });
    await report("done");
    await log(line);
    return { ok: true, ...tot };
  } catch (e) {
    const msg = String(e.message || e);
    await chrome.storage.local.set({ importStatus: `Paused at ${tot.pulled}: ${msg} — retrying automatically` });
    await report("paused", msg);
    await log(`Import paused: ${msg}`, false);
    return { error: msg };
  } finally { importing = false; }
}

let busy = false;
async function tick(force = false) {
  const { importOn } = await chrome.storage.local.get("importOn");
  if (importOn && !importing) importLoop();
  if (busy) return { skipped: "busy" };
  const c = await cfg();
  const paused = !force && (!c.enabled || !inHours(c));
  if (!c.crmUrl || !c.token) return { skipped: "not set up" };
  busy = true;
  try {
    const { task } = await crm(paused ? "/ext/task?only=search" : "/ext/task");
    if (!task) return paused ? { skipped: "paused or outside working hours" } : { idle: true };
    if (task.kind !== "search") await new Promise((ok) => setTimeout(ok, 2000 + Math.random() * 6000));  // human-like pause
    return await doTask(task);
  } catch (e) {
    await log(String(e.message || e), false);
    return { error: String(e.message || e) };
  } finally { busy = false; }
}

chrome.runtime.onInstalled.addListener(() => chrome.alarms.create("tick", { periodInMinutes: 0.5 }));
chrome.runtime.onStartup.addListener(() => chrome.alarms.create("tick", { periodInMinutes: 0.5 }));
chrome.alarms.onAlarm.addListener((a) => a.name === "tick" && tick());

const SEARCH_KEYS = { geoUrn: "geoUrn", network: "network", currentCompany: "currentCompany", industry: "industry", titleFreeText: "title", company: "company" };

function filtersFromUrl(u) {
  const q = new URL(u).searchParams, f = [];
  for (const [k, key] of Object.entries(SEARCH_KEYS)) {
    const v = q.get(k);
    if (!v) continue;
    let vals; try { vals = JSON.parse(v); } catch (_) { vals = [v]; }
    if (!Array.isArray(vals)) vals = [vals];
    f.push(`(key:${key},value:List(${vals.map((x) => encodeURIComponent(x)).join("|")}))`);
  }
  return { keywords: q.get("keywords") || "", filters: f, start: ((+q.get("page") || 1) - 1) * 10 };
}

chrome.runtime.onMessage.addListener((m, _s, reply) => {
  (async () => {
    try {
      if (m.type === "ping") return reply(await crm("/ext/ping"));
      if (m.type === "runOnce") return reply(await tick(true));
      if (m.type === "saveSearch") {
        const people = (await liSearch(filtersFromUrl(m.url))).map((r) => ({ name: r.name, urn_id: r.urn_id, headline: r.jobtitle, location: r.location,
          degree: +(r.distance.match(/\d/)?.[0] || 0) || null, linkedin: `https://www.linkedin.com/in/${r.urn_id}/` }));
        const d = await crm("/ext/save", { method: "POST", body: JSON.stringify({ people }) });
        await log(`Saved search page: ${d.saved} new, ${d.updated} updated, ${d.skipped} skipped`);
        return reply({ ok: true, ...d, found: people.length });
      }
      if (m.type === "importConnections") {
        await chrome.storage.local.set({ importOn: true, importNext: 0, importTot: { pulled: 0, saved: 0, updated: 0, skipped: 0 } });
        return reply(await importLoop());
      }
      if (m.type === "saveProfile") {
        const p = await liProfile(m.slug);
        const d = await crm("/ext/save", { method: "POST", body: JSON.stringify({ people: [{ ...p, linkedin: `https://www.linkedin.com/in/${m.slug}/` }] }) });
        await log(`Saved profile ${p.name}: ${d.saved ? "new contact" : d.updated ? "updated" : "skipped (no company in headline)"}`);
        return reply({ ok: true, ...d, name: p.name });
      }
      reply({ error: "unknown" });
    } catch (e) { reply({ error: String(e.message || e) }); }
  })();
  return true;
});
