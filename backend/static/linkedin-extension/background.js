importScripts("li.js");

const DEF = { crmUrl: "", token: "", enabled: false, startHour: 9, endHour: 19, days: [1, 2, 3, 4, 5, 6] };
const cfg = async () => ({ ...DEF, ...(await chrome.storage.local.get(Object.keys(DEF))) });

async function crm(path, opts = {}) {
  const c = await cfg();
  if (!c.crmUrl || !c.token) throw new Error("Set the CRM address and extension key in Options");
  const r = await fetch(c.crmUrl.replace(/\/+$/, "") + "/api" + path, { ...opts, headers: { "x-ext-token": c.token, "content-type": "application/json", ...(opts.headers || {}) } });
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

async function doTask(t) {
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

let busy = false;
async function tick(force = false) {
  if (busy) return { skipped: "busy" };
  const c = await cfg();
  if (!force && (!c.enabled || !inHours(c))) return { skipped: "paused or outside working hours" };
  busy = true;
  try {
    const { task } = await crm("/ext/task");
    if (!task) return { idle: true };
    await new Promise((ok) => setTimeout(ok, 2000 + Math.random() * 6000));  // human-like pause
    return await doTask(task);
  } catch (e) {
    await log(String(e.message || e), false);
    return { error: String(e.message || e) };
  } finally { busy = false; }
}

chrome.runtime.onInstalled.addListener(() => chrome.alarms.create("tick", { periodInMinutes: 1 }));
chrome.runtime.onStartup.addListener(() => chrome.alarms.create("tick", { periodInMinutes: 1 }));
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
