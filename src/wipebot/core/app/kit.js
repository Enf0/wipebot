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
  // The axis's top: the value rounded up to 1, 2, 2.5 or 5 of its power of ten
  function niceMax(v) {
    const step = 10 ** Math.floor(Math.log10(Math.max(v, 1)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * step >= v) return m * step;
    return 10 * step;
  }
  // Labels left to right after their marks, each only where it fits before the next mark of its lane (`marks`, the
  // lane's x positions, else `right`) and after the label before it; the rest stay a mark with a tooltip.
  function labels(items, x, y, cls, marks, right, size = 9.5) {
    let end = -Infinity, out = "";
    const xs = marks.slice().sort((a, b) => a - b);
    for (const [at0, s] of items) {
      const at = x(at0) + 6, w = width(s, size), next = xs.find((m) => m > x(at0) + 1) ?? right;
      if (at > end && at + w < next - 3) { out += text(at, y, s, cls, `font-size="${size}"`); end = at + w + 4; }
    }
    return out;
  }
  // The card's few numbers: [label, value, class of the value, a note under it]
  const stats = (items) => `<div class="stats">${items.filter(Boolean).map(([k, v, cls, note]) =>
    `<div class="stat"><div class="k">${esc(k)}</div><div class="v ${cls || ""}">${esc(v)}</div>`
    + (note ? `<div class="n">${esc(note)}</div>` : "") + "</div>").join("")}</div>`;
  return { esc, clock, short, svg, text, line, width, el, niceMax, labels, stats };
})();
const views = {};  // each view: views.<name>.show(root, key, open), drawn into root
