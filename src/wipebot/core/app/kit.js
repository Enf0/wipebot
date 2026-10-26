// ---- what the views draw with: SVG in the host's colors (page.css's classes), times as the tools write them ----
const kit = (() => {
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const clock = (ms) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  // 1.5B, 1.53M, 860k: as the tools' text writes amounts
  const short = (v) => v >= 1e9 ? `${(v / 1e9).toFixed(v >= 1e10 ? 0 : 1)}B` : v >= 1e6 ? `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M` : v >= 1e3 ? `${Math.round(v / 1e3)}k` : `${Math.round(v)}`;
  // 1st, 2nd, 3rd, 11th, 21st: as the tools' text writes ranks and percentiles
  const ordinal = (n) => { const r = n % 100, t = n % 10; return `${n}${r >= 11 && r <= 13 ? "th" : ["th", "st", "nd", "rd"][t] || "th"}`; };
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
  function labels(items, x, y, cls, marks, right, size = 9.5, pad = 6) {
    let end = -Infinity, out = "";
    const xs = marks.slice().sort((a, b) => a - b);
    for (const [at0, s] of items) {
      const at = x(at0) + pad, w = width(s, size), next = xs.find((m) => m > x(at0) + 1) ?? right;
      if (at > end && at + w < next - 3) { out += text(at, y, s, cls, `font-size="${size}"`); end = at + w + 4; }
    }
    return out;
  }
  // The card's few numbers: [label, value, class of the value, a note under it]
  const stats = (items) => `<div class="stats">${items.filter(Boolean).map(([k, v, cls, note]) =>
    `<div class="stat"><div class="k">${esc(k)}</div><div class="v ${cls || ""}">${esc(v)}</div>`
    + (note ? `<div class="n">${esc(note)}</div>` : "") + "</div>").join("")}</div>`;
  // A mark's tooltip: data-tip on it, shown by the one tooltip below (hover, or a tap on a touch screen)
  const tip = (s) => s ? ` data-tip="${esc(s)}"` : "";
  // An invisible area that shows a tooltip, wider than the thin mark it's for
  const hit = (x, y, w, h, s) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="transparent"${tip(s)}/>`;
  // A spell's icon (views.icon's path, from the view's img_base), a square at (x, y); nothing without one
  const icon = (base, path, x, y, size, s = "") => !base || !path ? ""
    : `<image href="${esc(base + path)}" x="${x}" y="${y}" width="${size}" height="${size}" preserveAspectRatio="xMidYMid slice"${tip(s)}/>`
      + `<rect x="${x}" y="${y}" width="${size}" height="${size}" rx="2" fill="none" stroke="var(--bg)" stroke-width="1" pointer-events="none"/>`;
  const iconImg = (base, path, s = "") => !base || !path ? ""
    : `<img class="ic" src="${esc(base + path)}" alt=""${s ? ` title="${esc(s)}"` : ""}>`;

  // The tooltip: one box, placed by the pointer and kept inside the page
  const box = document.createElement("div");
  box.id = "tooltip"; box.setAttribute("role", "tooltip");
  document.body.appendChild(box);
  function show(target, e) {
    box.textContent = target.getAttribute("data-tip");
    box.style.display = "block";
    const w = box.offsetWidth, h = box.offsetHeight;
    box.style.left = Math.max(4, Math.min(e.clientX + 12, window.innerWidth - w - 4)) + "px";
    box.style.top = (e.clientY - h - 10 < 4 ? e.clientY + 16 : e.clientY - h - 10) + "px";
  }
  const hide = () => { box.style.display = "none"; };
  document.addEventListener("pointermove", (e) => {
    if (e.pointerType !== "mouse") return;
    const t = e.target.closest && e.target.closest("[data-tip]");
    t ? show(t, e) : hide();
  });
  document.addEventListener("pointerdown", (e) => {  // a touch screen: a tap shows it, a tap elsewhere hides it
    if (e.pointerType === "mouse") return;
    const t = e.target.closest && e.target.closest("[data-tip]");
    t ? show(t, e) : hide();
  });
  document.addEventListener("pointerleave", hide);
  window.addEventListener("scroll", hide, true);

  return { esc, clock, short, ordinal, svg, text, line, width, el, niceMax, labels, stats, tip, hit, icon, iconImg };
})();
const views = {};  // each view: views.<name>.show(root, key, open), drawn into root
