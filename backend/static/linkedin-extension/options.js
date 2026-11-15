const $ = (id) => document.getElementById(id);
const NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
chrome.storage.local.get(["crmUrl", "token", "startHour", "endHour", "days"], (c) => {
  $("crmUrl").value = c.crmUrl || "";
  $("token").value = c.token || "";
  $("startHour").value = c.startHour ?? 9;
  $("endHour").value = c.endHour ?? 19;
  const days = c.days || [1, 2, 3, 4, 5, 6];
  $("days").innerHTML = NAMES.map((n, i) => `<label><input type="checkbox" value="${i}" ${days.includes(i) ? "checked" : ""}>${n}</label>`).join("");
});
$("save").onclick = async () => {
  const days = [...document.querySelectorAll("#days input:checked")].map((x) => +x.value);
  await chrome.storage.local.set({ crmUrl: $("crmUrl").value.trim().replace(/\/+$/, ""), token: $("token").value.trim(), startHour: +$("startHour").value, endHour: +$("endHour").value, days });
  $("msg").textContent = "Testing…";
  chrome.runtime.sendMessage({ type: "ping" }, (r) => {
    $("msg").innerHTML = r && !r.error ? `✅ Connected as <b>${r.name}</b>. Open the extension and switch “Agent running” on.` : `❌ ${(r && r.error) || "Could not reach the CRM"}`;
  });
};
