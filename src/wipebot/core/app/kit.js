// ---- what the views draw with: SVG in the host's colors (page.css's classes), times as the tools write them ----
const kit = (() => {
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const clock = (ms) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  // 1.5B, 1.5M, 13M, 860k: an amount in a card's few digits (the tools' text writes two decimals: 1.53M, 13.17M,
  // 860.25k, which `exact` keeps)
  const short = (v) => v >= 1e9 ? `${(v / 1e9).toFixed(v >= 1e10 ? 0 : 1)}B` : v >= 1e6 ? `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M` : v >= 1e3 ? `${Math.round(v / 1e3)}k` : `${Math.round(v)}`;
  // 1.53M, 860.25k: to the tools' two decimals, where pulls a few percent apart are told apart (a trend)
  const exact = (v) => v >= 1e9 ? `${(v / 1e9).toFixed(2)}B` : v >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(2)}k` : `${Math.round(v)}`;
  // 1st, 2nd, 3rd, 11th, 21st: as the tools' text writes ranks and percentiles
  const ordinal = (n) => { const r = n % 100, t = n % 10; return `${n}${r >= 11 && r <= 13 ? "th" : ["th", "st", "nd", "rd"][t] || "th"}`; };
  // 1 pull, 2 pulls: a count with its noun
  const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
  // Which of n marks in a row are numbered when only one in `every` has room: each `every`th, and the last always,
  // the one before it giving way where the two would touch
  const numbered = (i, n, every) => i === n - 1 || (i % every === 0 && n - 1 - i >= every);
  const svg = (w, h, body, label) => `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(label)}">${body}</svg>`;
  // Chart text comes in three sizes, chosen once: a label (axes, legends, captions: the card's smallest text, as
  // its stats' headings), a value (what names data: a line's end, a best, a name) and a number inside a mark. The
  // size is text()'s own parameter, and width() measures with the same number: never a font-size in `attrs`, where
  // the browser keeps the first attribute of a name and drops the second.
  const SIZE = { label: 11, value: 12, mark: 9 };
  const text = (x, y, s, cls = "tm", attrs = "", size = SIZE.label) =>
    `<text x="${x}" y="${y}" class="${cls}" font-size="${size}" ${attrs}>${esc(s)}</text>`;
  const line = (x1, y1, x2, y2, stroke, attrs = "") => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${stroke}" ${attrs}/>`;
  // A text's width in the page's font, for keeping labels apart; about, where the page can't measure it
  const ctx = document.createElement("canvas").getContext("2d");
  function width(s, size = SIZE.label, weight = 400) {
    const font = getComputedStyle(document.documentElement).getPropertyValue("--font").trim();
    if (!ctx || !font) return String(s).length * size * 0.56;
    ctx.font = `${weight} ${size}px ${font}`;
    return ctx.measureText(String(s)).width;
  }
  // A text cut at its end to fit a width, with an ellipsis for what was cut
  function fit(s, most, size = SIZE.label, weight = 400) {
    let out = String(s);
    if (width(out, size, weight) <= most) return out;
    while (out.length > 1 && width(`${out}…`, size, weight) > most) out = out.slice(0, -1);
    return `${out}…`;
  }
  const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; };
  // The axis's top: the value rounded up to 1, 2, 2.5 or 5 of its power of ten
  function niceMax(v) {
    const step = 10 ** Math.floor(Math.log10(Math.max(v, 1)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * step >= v) return m * step;
    return 10 * step;
  }
  // Labels left to right after their marks, each only where it fits before the next mark of its lane (`marks`, the
  // lane's x positions, else `right`) and after the label before it; the rest stay a mark with a tooltip.
  function labels(items, x, y, cls, marks, right, size = SIZE.label, pad = 6) {
    let end = -Infinity, out = "";
    const xs = marks.slice().sort((a, b) => a - b);
    for (const [at0, s] of items) {
      const at = x(at0) + pad, w = width(s, size), next = xs.find((m) => m > x(at0) + 1) ?? right;
      if (at > end && at + w < next - 6) { out += text(at, y, s, cls, "", size); end = at + w + 4; }
    }
    return out;
  }
  // A class's colour on a mark, for page.css to draw as the class's ink ("cls": text and dots; "cl": a line) or as a
  // bar in the true colour with its edge in the ink ("cb")
  const cls = (color, name = "cls") => `class="${name}" style="--c:${esc(color)}"`;
  // What only its colour tells apart: the first four names take page.css's four hues, in the order given, and every
  // other is "other", drawn hollow. svg: a mark's fill attributes; css: the same as an HTML swatch's style
  const HUES = [1, 2, 3, 4].map((i) => `var(--cat-${i})`);
  function palette(names) {
    const hue = new Map(names.slice(0, HUES.length).map((n, i) => [n, HUES[i]]));
    return {
      has: (n) => hue.has(n),
      svg: (n) => hue.has(n) ? `fill="${hue.get(n)}"` : 'fill="none" stroke="var(--muted)" stroke-width="1.2"',
      css: (n) => hue.has(n) ? `background:${hue.get(n)}` : "background:none;box-shadow:inset 0 0 0 1.2px var(--muted)",
    };
  }
  // A card's head: its title (a player's name in their class's ink, after `icon`, an ability's as markup) and the
  // line beside it, which `more` (markup) ends
  const head = (title, sub, color, more = "", icon = "") =>
    `<div class="head"><h1${color ? ` ${cls(color)}` : ""}>${icon}${esc(title)}</h1><span class="sub">${esc(sub)}${more && sub ? " · " : ""}${more}</span></div>`;
  // The card's few numbers: [label, value, class of the value, a note under it]; `cols`: in columns of one width,
  // as many as fit (a card's: its notes then wrap in their column)
  const stats = (items, cols = false) => `<div class="stats${cols ? " cols" : ""}">${items.filter(Boolean).map(([k, v, cls, note]) =>
    `<div class="stat"><div class="k">${esc(k)}</div><div class="v ${cls || ""}">${esc(v)}</div>`
    + (note ? `<div class="n">${esc(note)}</div>` : "") + "</div>").join("")}</div>`;
  // A chart's legend under it, from y: each key ([mark(x, y) => its SVG, name]) left to right, on a new row where
  // the chart's width ends. The markup, and the last row's y
  function legend(keys, W, y) {
    let x = 6, out = "";
    for (const [mark, name] of keys) {
      const w = 18 + width(name) + 14;
      if (x > 6 && x + w > W) { x = 6; y += 17; }
      out += mark(x, y) + text(x + 17, y, name);
      x += w;
    }
    return [out, y];
  }
  // A headline's mark, the same in every view: "+" what went well, "−" what to do differently, "!" what changes how
  // the rest reads
  const MARK = { good: "+", fix: "−", mind: "!" };
  // A view's card, the same shape in every view: its head, one headline (a sentence the server wrote from the tool's
  // facts, views.py's "headline": the card words nothing itself; marked where it says what went well or what
  // to do differently), at most CARD.stats numbers, one chart of about CARD.chart px, and at most CARD.buttons
  // buttons: `open` ("Open pull": the same card in fullscreen, with more under it) first, where the host has a
  // fullscreen, then the view's own ([id, label]). Fullscreen draws the same card without `open`; a still picture
  // has no buttons. `url` (the server's, to the pull or the report on Warcraft Logs) is the card's way out, a link
  // and not a third button: inline at the end of the buttons' row, in fullscreen at the end of the head's line. The
  // host opens it where it says it opens links.
  const CARD = { stats: 4, chart: 220, buttons: 2 };
  // `more`: a line of the tool's own text that the headline is read with, under it in plain ink
  const lead = (h) => !h || !h.text ? "" : `<p class="lead ${h.kind || ""}">${h.kind ? `<span class="mk">${MARK[h.kind]}</span>` : ""}${esc(h.text)}</p>`
    + (h.more ? `<p class="lead-more">${esc(h.more)}</p>` : "");
  const OUT = '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M3.5 1.5h5v5M8.5 1.5l-7 7" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';
  function card({ title, sub, color, icon = "", headline, stats: items = [], chart = "", open = "", buttons = [], url = "" }) {
    const inline = app.context().displayMode !== "fullscreen";
    const all = app.picture() ? [] : [inline && open && app.canGoFullscreen() && ["card-open", open, true], ...buttons].filter(Boolean);
    const out = app.picture() || !/^https:\/\//.test(url) ? ""
      : `<a class="out" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Warcraft Logs${OUT}</a>`;
    // "Report a problem" (main.js), quiet, before the link out, where the page offers a report (kit.report)
    const report = kit.report && app.canReport() ? '<button type="button" class="report-open">Report<span class="wide"> a problem</span></button>' : "";
    return head(title, sub, color, inline ? "" : out, icon) + lead(headline) + stats(items.filter(Boolean).slice(0, CARD.stats), true) + chart
      + (all.length || (inline && out) || report ? `<div class="actions">${all.slice(0, CARD.buttons).map(([id, label, primary]) =>
        `<button id="${id}"${primary ? ' class="primary"' : ""}>${esc(label)}</button>`).join("")}${report}${inline ? out : ""}</div>` : "");
  }
  document.addEventListener("click", (e) => {
    if (!e.target.closest) return;
    // the host's answer redraws the view (main.js, on its new context)
    if (e.target.closest("#card-open")) app.requestDisplayMode("fullscreen");
    const out = e.target.closest("a.out");  // a host that opens links opens this one; elsewhere it is the browser's
    if (out && app.canOpenLinks()) { e.preventDefault(); app.openLink(out.href); }
  });
  // What the model is told the user has on screen, in one line (a view opened from another, the way back, the moment
  // a replay stopped at): `what` is the view's own words for it, from its data
  const looking = (what, report) => app.tellModel(`The user is now looking at ${what}${report ? ` (report ${report})` : ""}.`);
  // What a click on one of the view's own buttons does, where the card drew it
  const press = (root, id, fn) => { const b = root.querySelector(`#${id}`); if (b) b.onclick = fn; };
  // How a view is drawn now: in fullscreen, as a still picture, and the width its charts are drawn at
  const frame = (root) => ({ full: app.context().displayMode === "fullscreen", picture: app.picture(),
                             width: Math.max(300, root.clientWidth || 640) });
  // A chart is drawn at the width it has, so its text stays readable: `render` draws the view again when that
  // width changes, for as long as the view is the one `shown`
  function watch(into, shown, render) {
    let drawn = into.clientWidth;
    new ResizeObserver(() => {
      if (!shown() || Math.abs(into.clientWidth - drawn) <= 24) return;
      drawn = into.clientWidth;
      render();
    }).observe(into);
  }
  // A time axis's step: the shortest that leaves at most `most` marks over `ms`
  const step = (ms, most) => [5, 10, 15, 30, 60, 120, 300].map((s) => s * 1000).find((s) => ms / s <= most) || 600000;
  // The hatch of a stretch dead or lost, the same in every chart: its <defs>, for a mark's fill="url(#id)"
  const hatch = (id) => `<defs><pattern id="${id}" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">`
    + '<rect width="2" height="5" fill="var(--dead)" opacity=".55"/></pattern></defs>';
  // A mark's tooltip: data-tip on it, shown by the one tooltip below (hover, a tap on a touch screen, or the
  // keyboard reaching the mark)
  const tip = (s) => s ? ` data-tip="${esc(s)}"` : "";
  // What a click on a mark or a row opens ("this pull"): the keyboard and a screen reader get it as a button, and
  // its tooltip ends with how to open it from where the reader is (a click, a second tap, Enter)
  const opens = (what = "") => ` data-act="${esc(what)}"`;
  // An invisible area that shows a tooltip, wider than the thin mark it's for
  const hit = (x, y, w, h, s) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="transparent"${tip(s)}/>`;
  // A spell's icon (views.icon's path, from the view's img_base), a square at (x, y); nothing without one
  const icon = (base, path, x, y, size, s = "") => !base || !path ? ""
    : `<image href="${esc(base + path)}" x="${x}" y="${y}" width="${size}" height="${size}" preserveAspectRatio="xMidYMid slice"${tip(s)}/>`
      + `<rect x="${x}" y="${y}" width="${size}" height="${size}" rx="2" fill="none" stroke="var(--bg)" stroke-width="1" pointer-events="none"/>`;
  const iconImg = (base, path, s = "") => !base || !path ? ""
    : `<img class="ic" src="${esc(base + path)}" alt=""${s ? ` title="${esc(s)}"` : ""}>`;

  // The tooltip: one box, placed by the pointer (or over the mark the keyboard is on) and kept inside the page
  const box = document.createElement("div");
  box.id = "tooltip"; box.setAttribute("role", "tooltip");
  document.body.appendChild(box);
  function show(target, x, y, how) {
    const what = target.getAttribute("data-act");
    box.textContent = target.getAttribute("data-tip") + (what ? `\n${how} for ${what}` : "");
    box.style.display = "block";
    const w = box.offsetWidth, h = box.offsetHeight;
    box.style.left = Math.max(4, Math.min(x + 12, window.innerWidth - w - 4)) + "px";
    box.style.top = (y - h - 10 < 4 ? y + 16 : y - h - 10) + "px";
  }
  const hide = () => { box.style.display = "none"; };
  const tipped = (e) => e.target.closest && e.target.closest("[data-tip]");
  let touch = false, armed = null, keyed = false;
  document.addEventListener("pointermove", (e) => {
    if (e.pointerType !== "mouse") return;
    const t = tipped(e);
    t ? show(t, e.clientX, e.clientY, "Click") : hide();
  });
  // A touch screen: a tap shows the tooltip, a tap elsewhere hides it. A night's bars are thinner than a finger, so
  // a tap on a mark that opens something shows which one was hit first (data-armed: page.css marks it), and a
  // second tap on it opens it.
  const arm = (mark) => {
    if (armed) armed.removeAttribute("data-armed");
    armed = mark;
    if (armed) armed.setAttribute("data-armed", "");
  };
  document.addEventListener("pointerdown", (e) => {
    touch = e.pointerType !== "mouse";
    if (!touch) return;
    const t = tipped(e);
    t ? show(t, e.clientX, e.clientY, "Tap again") : hide();
  });
  document.addEventListener("click", (e) => {
    const t = touch && !keyed && e.target.closest && e.target.closest("svg [data-act][data-tip]");
    if (t && t !== armed) { arm(t); e.stopPropagation(); e.preventDefault(); } else arm(null);
  }, true);
  // a finger lifted leaves the page too: what its tap showed stays until the next tap
  document.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") hide(); });
  window.addEventListener("scroll", hide, true);

  // The keyboard's way in. A chart's marks (whatever has a tooltip) and a table's rows that open something are one
  // stop of the Tab key each: the arrow keys step through them as they lie, left to right and top to bottom, the
  // tooltip follows, and Enter or space does what a click does. A chart's mark is named to a screen reader by what
  // its tooltip says, and is a button where it opens something.
  const STEP = "[data-tip], [data-act]";
  const placed = (group) => [...group.querySelectorAll(STEP)]
    .map((m) => { const r = m.getBoundingClientRect(); return [m, Math.round((r.left + r.right) / 2), r.top]; })
    .sort((a, b) => a[1] - b[1] || a[2] - b[2]).map(([m]) => m);
  function reach() {
    for (const group of document.querySelectorAll("#app svg, #app tbody")) {
      if (group.querySelector('[tabindex="0"]')) continue;
      const marks = placed(group), chart = group instanceof SVGElement;
      if (!marks.length) continue;
      if (chart) group.setAttribute("role", "group");
      marks.forEach((m, i) => {
        m.setAttribute("tabindex", i ? "-1" : "0");
        if (!chart) return;
        m.setAttribute("role", m.hasAttribute("data-act") ? "button" : "img");
        m.setAttribute("aria-label", m.getAttribute("data-tip"));
      });
    }
  }
  new MutationObserver(reach).observe(document.getElementById("app"), { childList: true, subtree: true });
  document.addEventListener("keydown", (e) => {
    const at = e.target.closest && e.target.closest(STEP), group = at && at.closest("svg, tbody");
    if (!group) return;
    const marks = placed(group), i = marks.indexOf(at);
    const to = { ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1, Home: 0, End: marks.length - 1 }[e.key];
    if (to !== undefined) {
      const next = marks[Math.max(0, Math.min(marks.length - 1, to))];
      e.preventDefault();
      at.setAttribute("tabindex", "-1"); next.setAttribute("tabindex", "0"); next.focus();
    } else if ((e.key === "Enter" || e.key === " ") && at.hasAttribute("data-act")) {
      e.preventDefault();
      keyed = true;
      try { at.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })); } finally { keyed = false; }
    } else if (e.key === "Escape") hide();
  });
  document.addEventListener("focusin", (e) => {
    const t = tipped(e);
    if (!t || !t.matches(":focus-visible")) return;  // the keyboard's focus, not a click's
    const r = t.getBoundingClientRect();
    show(t, r.left + r.width / 2 - 12, r.top, "Enter");
  });
  document.addEventListener("focusout", hide);

  return { esc, clock, short, exact, ordinal, svg, text, line, width, el, niceMax, labels, stats, tip, hit, icon, iconImg, SIZE, cls, palette, head, plural, legend, opens, fit, numbered,
           CARD, card, press, frame, watch, looking, step, hatch,
           report: null };  // main.js: opens the form offering a report, where the page offers one
})();
const views = {};  // each view: views.<name>.show(root, key, open, asked), drawn into root
