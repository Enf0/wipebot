// ---- what the views draw with: SVG in the host's colors (page.css's classes), times as the tools write them ----
const kit = (() => {
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const clock = (ms) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  // 1.53M, 860k: as the tools' text writes amounts
  const short = (v) => v >= 1e6 ? `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M` : v >= 1e3 ? `${Math.round(v / 1e3)}k` : `${Math.round(v)}`;
  const svg = (w, h, body, label) => `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(label)}">${body}</svg>`;
  const text = (x, y, s, cls = "tm", attrs = "") => `<text x="${x}" y="${y}" class="${cls}" font-size="11" ${attrs}>${esc(s)}</text>`;
  const line = (x1, y1, x2, y2, stroke, attrs = "") => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${stroke}" ${attrs}/>`;
  const width = (s, size = 11) => String(s).length * size * 0.56;  // about, for keeping labels apart
  const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; };
  return { esc, clock, short, svg, text, line, width, el };
})();
const views = {};  // each view: views.<name>.show(root, key, open), drawn into root
