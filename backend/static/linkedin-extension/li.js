// Talks to LinkedIn's own web API using the user's logged-in browser session.
const LI = "https://www.linkedin.com/voyager/api";

async function csrf() {
  const c = await chrome.cookies.get({ url: "https://www.linkedin.com", name: "JSESSIONID" });
  if (!c) throw new Error("Not logged in to LinkedIn in this Chrome — open linkedin.com and log in");
  return c.value.replace(/"/g, "");
}

async function liFetch(path, opts = {}) {
  const r = await fetch(LI + path, {
    credentials: "include",
    ...opts,
    headers: { "csrf-token": await csrf(), "x-restli-protocol-version": "2.0.0", "x-li-lang": "en_US", accept: "application/vnd.linkedin.normalized+json+2.1",
      ...(opts.body ? { "content-type": "application/json" } : {}), ...(opts.headers || {}) },
  });
  if (r.status === 401 || r.status === 403) throw new Error(`LinkedIn refused (${r.status}) — log in to LinkedIn again`);
  return r;
}

function parseSearch(data) {
  const all = [...(data.included || []), ...((data.data?.searchDashClustersByAll?.elements) || []).flatMap((e) => (e.items || []).map((i) => i.item?.entityResult)).filter(Boolean)];
  const out = [];
  for (const e of all) {
    const urn = (e?.entityUrn || "").match(/fsd_profile:([^,)]+)/)?.[1];
    const name = e?.title?.text;
    if (!urn || !name || name === "LinkedIn Member" || out.some((o) => o.urn_id === urn)) continue;
    out.push({ urn_id: urn, name, jobtitle: e.primarySubtitle?.text || "", location: e.secondarySubtitle?.text || "", distance: e.entityCustomTrackingInfo?.memberDistance || "" });
  }
  return out;
}

async function liSearch({ keywords, filters = [], start = 0 }) {
  const fl = ["(key:resultType,value:List(PEOPLE))", ...filters].join(",");
  const kw = keywords ? `keywords:${encodeURIComponent(keywords)},` : "";
  const r = await liFetch(`/graphql?variables=(start:${start},origin:GLOBAL_SEARCH_HEADER,query:(${kw}flagshipSearchIntent:SEARCH_SRP,queryParameters:List(${fl}),includeFiltersInResponse:false))&queryId=voyagerSearchDashClusters.b0928897b71bd00a5a7291755dcd64f0`);
  if (!r.ok) throw new Error(`LinkedIn search failed (${r.status})`);
  return parseSearch(await r.json());
}

async function liInvite(urn, text) {
  const r = await liFetch("/voyagerRelationshipsDashMemberRelationships?action=verifyQuotaAndCreateV2&decorationId=com.linkedin.voyager.dash.deco.relationships.InvitationCreationResultWithInvitee-2", {
    method: "POST", body: JSON.stringify({ invitee: { inviteeUnion: { memberProfile: `urn:li:fsd_profile:${urn}` } }, customMessage: (text || "").slice(0, 300) }),
  });
  if (!r.ok) throw new Error(r.status === 429 ? "LinkedIn weekly invite limit reached" : `Invite failed (${r.status}${r.status === 400 ? " — already invited or connected" : ""})`);
}

async function liMessage(urn, text) {
  const ev = { eventCreate: { originToken: crypto.randomUUID(), value: { "com.linkedin.voyager.messaging.create.MessageCreate": { attributedBody: { text, attributes: [] }, attachments: [] } } },
    dedupeByClientGeneratedToken: false, recipients: [urn], subtype: "MEMBER_TO_MEMBER" };
  const r = await liFetch("/messaging/conversations?action=create", { method: "POST", body: JSON.stringify({ keyVersion: "LEGACY_INBOX", conversationCreate: ev }) });
  if (r.status !== 201 && !r.ok) throw new Error(`Message failed (${r.status})`);
}

async function liConnections(start) {
  const r = await liFetch(`/relationships/dash/connections?decorationId=com.linkedin.voyager.dash.deco.web.mynetwork.ConnectionListWithProfile-16&count=40&q=search&sortType=RECENTLY_ADDED&start=${start}`);
  if (!r.ok) throw new Error(`Connections list failed (${r.status})`);
  const d = await r.json();
  const profs = [...(d.included || []).filter((x) => x.firstName && (x.entityUrn || "").includes("fsd_profile")),
    ...((d.elements || d.data?.elements || []).map((e) => e.connectedMemberResolutionResult).filter((x) => x && x.firstName))];
  const seen = new Set();
  return profs.filter((p) => { const u = p.entityUrn.split(":").pop(); if (seen.has(u)) return false; seen.add(u); return true; })
    .map((p) => { const u = p.entityUrn.split(":").pop(); return { name: `${p.firstName} ${p.lastName || ""}`.trim(), urn_id: u, headline: p.headline || "", degree: 1, linkedin: `https://www.linkedin.com/in/${p.publicIdentifier || u}/` }; });
}

async function liProfile(slug) {
  const r = await liFetch(`/identity/profiles/${encodeURIComponent(slug)}/profileView`);
  if (!r.ok) throw new Error(`Profile read failed (${r.status})`);
  const d = await r.json();
  const p = (d.included || []).find((x) => (x.$type || "").endsWith("Profile") && x.firstName) || d.profile || {};
  const urn = (p.entityUrn || "").split(":").pop();
  let degree = null;
  try { const n = await (await liFetch(`/identity/profiles/${encodeURIComponent(slug)}/networkinfo`)).json(); degree = +(((n.data || n).distance?.value || "").match(/\d/)?.[0] || 0) || null; } catch (_) { /* optional */ }
  return { urn_id: urn, name: `${p.firstName || ""} ${p.lastName || ""}`.trim(), headline: p.headline || "", location: p.locationName || p.geoLocationName || "", degree };
}
