const slug = (u) => (u || "").match(/linkedin\.com\/in\/([^/?#\s]+)/i)?.[1];

export const liSearch = (name, brand) =>
  `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent([name, brand].filter(Boolean).join(" "))}`;

// Sheet-uploaded profiles open directly; AI-guessed ones (often wrong) open a LinkedIn people search instead.
export const liLink = (c, brand) => {
  const s = slug(c?.linkedin);
  if (s && (c.uploaded || c.verified)) return { href: `https://www.linkedin.com/in/${s}/`, direct: true };
  return c?.name ? { href: liSearch(c.name, brand), direct: false } : null;
};
