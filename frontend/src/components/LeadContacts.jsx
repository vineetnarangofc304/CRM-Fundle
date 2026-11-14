import React, { useEffect, useState } from "react";
import { LinkedinLogo } from "@phosphor-icons/react";
import { API } from "../lib/api";
import { liLink } from "../lib/linkedin";

export function LeadContacts({ leadId }) {
  const [cs, setCs] = useState([]);
  const [brand, setBrand] = useState("");
  useEffect(() => { API.get(`/linkedin/lead/${leadId}/contacts`).then(({ data }) => { setCs(data.contacts); setBrand(data.brand || ""); }).catch(() => {}); }, [leadId]);
  if (!cs.length) return null;
  return (
    <div className="mt-3 border-t border-slate-100 pt-2" data-testid="lead-contacts">
      <div className="mb-1 text-[11px] font-bold uppercase tracking-wider text-slate-400">Decision makers</div>
      {cs.map((c, i) => (
        <div key={i} className="flex items-center justify-between gap-2 py-1 text-sm" data-testid={`lead-contact-${i}`}>
          <div className="min-w-0"><div className="truncate font-semibold text-slate-800">{c.name}</div><div className="truncate text-[11px] text-slate-500">{c.role}</div></div>
          {liLink(c, brand) && <a data-testid={`lead-contact-linkedin-${i}`} href={liLink(c, brand).href} target="_blank" rel="noreferrer" className="inline-flex shrink-0 items-center gap-1 rounded-full bg-[#0A66C2]/10 px-2 py-0.5 text-[11px] font-bold text-[#0A66C2] transition-colors hover:bg-[#0A66C2]/20"><LinkedinLogo size={14} weight="fill" /> {liLink(c, brand).direct ? "Open LinkedIn" : "Find on LinkedIn"}</a>}
        </div>
      ))}
    </div>
  );
}
