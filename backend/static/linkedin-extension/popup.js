const $ = (id) => document.getElementById(id);
async function render() {
  const { enabled = false, logs = [] } = await chrome.storage.local.get(["enabled", "logs"]);
  $("enabled").checked = enabled;
  $("log").innerHTML = logs.slice(0, 12).map((l) => `<div class="${l.ok ? "" : "bad"}">${l.at} — ${l.line.replace(/</g, "&lt;")}</div>`).join("") || '<div class="muted">No activity yet.</div>';
  chrome.runtime.sendMessage({ type: "ping" }, (r) => {
    if (!r || r.error) { $("status").innerHTML = `<span class="bad">${(r && r.error) || "Not connected"}</span>`; return; }
    $("status").textContent = `Connected to CRM as ${r.name}`;
    $("counts").innerHTML = ["lookup", "invite", "message"].map((k) => `<div class="row"><span>${{ lookup: "Profile lookups", invite: "Invites", message: "Messages" }[k]} today</span><b>${r.today[k]} / ${r.limits[k] || "off"}</b></div>`).join("");
  });
}
$("enabled").onchange = (e) => chrome.storage.local.set({ enabled: e.target.checked });
$("opts").onclick = () => chrome.runtime.openOptionsPage();
$("run").onclick = () => { $("run").textContent = "Working…"; chrome.runtime.sendMessage({ type: "runOnce" }, () => { $("run").textContent = "Run one task now"; render(); }); };
render();
$("imp").onclick = () => { if (!confirm("Start importing all your 1st-degree connections from the beginning? It runs in the background and resumes by itself.")) return; $("imp").textContent = "Importing… (you can close this)"; chrome.runtime.sendMessage({ type: "importConnections" }, (r) => { $("imp").textContent = "Import my 1st-degree connections"; $("impStatus").textContent = r?.error || ""; render(); }); };
chrome.storage.local.get("importStatus", (s) => { $("impStatus").textContent = s.importStatus || ""; });
