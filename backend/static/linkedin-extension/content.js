// Adds a "Save to Fundle CRM" button on LinkedIn people-search and profile pages.
(function () {
  let btn;
  function place() {
    const p = location.pathname;
    const kind = p.startsWith("/search/results/people") ? "search" : /^\/in\/[^/]+\/?$/.test(p) ? "profile" : null;
    if (!kind) { btn && btn.remove(); btn = null; return; }
    if (!btn) {
      btn = document.createElement("button");
      btn.style.cssText = "position:fixed;right:24px;bottom:90px;z-index:99999;background:#1A73E8;color:#fff;border:0;border-radius:999px;padding:10px 16px;font:600 13px system-ui;box-shadow:0 6px 20px rgba(0,0,0,.2);cursor:pointer";
      btn.onclick = save;
      document.body.appendChild(btn);
    }
    btn.dataset.kind = kind;
    btn.textContent = kind === "search" ? "Save this page's people to Fundle" : "Save this person to Fundle";
  }
  function save() {
    const kind = btn.dataset.kind, label = btn.textContent;
    btn.textContent = "Saving…"; btn.disabled = true;
    const msg = kind === "search" ? { type: "saveSearch", url: location.href } : { type: "saveProfile", slug: location.pathname.split("/")[2] };
    chrome.runtime.sendMessage(msg, (r) => {
      btn.disabled = false;
      btn.textContent = r?.error ? `⚠ ${r.error}` : kind === "search" ? `✓ ${r.saved} new · ${r.updated} updated · ${r.skipped} skipped` : `✓ ${r.name} saved`;
      setTimeout(() => { if (btn) btn.textContent = label; }, 6000);
    });
  }
  setInterval(place, 1500);
  place();
})();
