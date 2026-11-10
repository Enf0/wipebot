// ---- the healers' mana on a pull (healing with mana): a line per healer in their class colour until the wipe was
// decided, named at its end (two healers of one class: the second's line dashed, the third's dotted, and a piece
// of each one's line before its name), the low line dashed, each Innervate a gold diamond on the line of the healer
// it went to; under it a lane per druid with their casts and, hatched red, the stretches it sat ready while a healer
// was low. One panel ----
views.mana = (() => {
  let D = null, root = null;
  const W = 680, LEFT = 34, CH = 190, LANE = 22;
  const DASH = ["", "7 3", "2 3"];  // a class's first healer, its second, its third and later
  const SAMPLE = 20;  // a piece of a healer's line before their name, where another healer has its colour
  // each healer's line: its dash among the healers of its class, and whether the class has more than one
  const lines = () => {
    const seen = {}, of = {};
    for (const hl of D.healers) of[hl.color] = (of[hl.color] || 0) + 1;
    return new Map(D.healers.map((hl) => {
      seen[hl.color] = (seen[hl.color] || 0) + 1;
      return [hl.name, { dash: DASH[Math.min(seen[hl.color], DASH.length) - 1], shared: of[hl.color] > 1 }];
    }));
  };
  const stroke = (dash) => dash ? ` stroke-dasharray="${dash}"` : "";

  async function show(into, key) {
    root = into;
    D = await app.callTool("view_data", { view: "mana", report: key.report, pull: key.pull, boss: key.boss });
    render();
  }

  function chart() {
    const line = lines();
    // A healer's name, in their class's ink, from x; before it a piece of their line where the colour alone doesn't
    // say whose it is. The markup, and how wide it is
    const name = (hl, x0, y0, cls = "cls") => {
      const { dash, shared } = line.get(hl.name), lead = shared ? SAMPLE + 4 : 0;
      return (shared ? `<path d="M${x0} ${y0 - 4}h${SAMPLE}" fill="none" ${kit.cls(hl.color, "cl")} stroke-width="2"${stroke(dash)}/>` : "")
        + kit.text(x0 + lead, y0, hl.name, cls, `font-weight="600" style="--c:${kit.esc(hl.color)}"`);
    };
    const wide = (hl) => kit.width(hl.name, kit.SIZE.label, 600) + (line.get(hl.name).shared ? SAMPLE + 4 : 0);
    // the names at the lines' ends take a margin on the right as wide as the longest
    const RIGHT = Math.ceil(Math.max(12, ...D.healers.filter((hl) => hl.points.length).map((hl) => wide(hl) + 10)));
    const x = (ms) => LEFT + ms / Math.max(D.until_ms, 1) * (W - LEFT - RIGHT), y = (v) => 10 + (100 - v) / 100 * CH;
    const lanes = D.innervates.length, h = 10 + CH + 24 + lanes * LANE + 4;
    const ends = [];  // [the y a line ends at, its healer]: who lived to the end, named in the margin
    let body = kit.hatch("hatch");
    for (const v of [0, 50, 100]) {
      body += kit.line(LEFT, y(v), W - RIGHT, y(v), "var(--line)") + kit.text(LEFT - 6, y(v) + 3.5, `${v}%`, "tm", 'text-anchor="end"');
    }
    body += kit.line(LEFT, y(D.low), W - RIGHT, y(D.low), "var(--dead)", 'stroke-dasharray="4 3" opacity="0.8"');
    body += kit.text(W - RIGHT, y(D.low) - 4, `low: under ${D.low}%`, "td", 'text-anchor="end"');
    for (let s = 60; s * 1000 < D.until_ms; s += 60) {
      body += kit.line(x(s * 1000), 10, x(s * 1000), 10 + CH, "var(--line)", 'stroke-dasharray="2 4" opacity="0.6"');
      body += kit.text(x(s * 1000), 10 + CH + 14, kit.clock(s * 1000), "tm", 'text-anchor="middle"');
    }
    for (const hl of D.healers) {
      if (!hl.points.length) continue;
      const pts = hl.points.filter(([t]) => hl.dead_ms == null || t <= hl.dead_ms);
      const path = pts.map(([t, v], i) => `${i ? "L" : "M"}${x(t).toFixed(1)} ${y(v).toFixed(1)}`).join("");
      const tip = `${hl.name} (${hl.spec})` + (hl.lowest ? `: lowest ${hl.lowest[1]}% at ${kit.clock(hl.lowest[0])}` : "")
        + (hl.low.length ? `; under ${D.low}% ${hl.low.map(([a, b]) => `${kit.clock(a)}-${kit.clock(b)}`).join(", ")}` : "");
      body += `<path d="${path}" fill="none" ${kit.cls(hl.color, "cl")} stroke-width="2" stroke-linejoin="round"${stroke(line.get(hl.name).dash)}${kit.tip(tip)}/>`;
      const [lt, lv] = pts[pts.length - 1] || hl.points[0];
      if (hl.dead_ms == null) { ends.push([y(lv), hl]); continue; }
      // who died: the cross where their line stops, and their name beside it, on the side with room
      const left = x(lt) + 8 + wide(hl) > W - RIGHT;
      body += kit.text(x(lt), y(lv) + 4, "✕", "td", `text-anchor="middle"${kit.tip(`${hl.name} died at ${kit.clock(hl.dead_ms)}`)}`, kit.SIZE.value);
      body += name(hl, left ? x(lt) - 8 - wide(hl) : x(lt) + 8, y(lv) + 4, "cls over");
    }
    // the names in the margin, top to bottom, each at its line's end or just under the name above it
    ends.sort((a, b) => a[0] - b[0]);
    let under = -Infinity;
    for (const [at, hl] of ends) {
      const ly = Math.max(at + 4, under + 12);
      body += name(hl, W - RIGHT + 6, ly);
      under = ly;
    }
    const at = (name, ms) => {  // a healer's mana at a moment, for the diamond on their line
      const hl = D.healers.find((q) => q.name === name);
      const before = hl ? hl.points.filter(([t]) => t <= ms) : [];
      return before.length ? before[before.length - 1][1] : null;
    };
    D.innervates.forEach((inv, i) => {
      const ly = 10 + CH + 24 + i * LANE + 8;
      body += kit.text(0, ly + 4, inv.druid, "cls", `font-weight="600" style="--c:${kit.esc(inv.color)}"`);
      body += kit.line(LEFT + 40, ly, W - RIGHT, ly, "var(--line)");
      for (const r of inv.ready_low) {
        body += `<rect x="${x(r.from_ms)}" y="${ly - 6}" width="${Math.max(2, x(r.to_ms) - x(r.from_ms))}" height="12" fill="url(#hatch)" stroke="var(--dead)" stroke-width="0.8"`
          + `${kit.tip(`${inv.druid}'s Innervate ready ${kit.clock(r.from_ms)}-${kit.clock(r.to_ms)} while ${r.who.join(", ")} under ${D.low}%`)}/>`;
      }
      for (const c of inv.casts) {
        const tip = `${inv.druid}: Innervate at ${kit.clock(c.ms)} on ${c.on}${c.pct == null ? "" : ` (at ${c.pct}% mana)`}`;
        body += `<path d="M${x(c.ms)} ${ly - 6}l6 6l-6 6l-6-6z" fill="var(--gold)"${kit.tip(tip)}/>`;
        const v = at(c.on, c.ms);
        if (v != null) {
          body += kit.line(x(c.ms), y(v) + 6, x(c.ms), ly - 7, "var(--gold)", 'stroke-dasharray="2 3" opacity="0.6"');
          body += `<path d="M${x(c.ms)} ${y(v) - 6}l6 6l-6 6l-6-6z" fill="var(--gold)" stroke="var(--bg)" stroke-width="1"${kit.tip(tip)}/>`;
        }
      }
    });
    return kit.svg(W, h, body, `${D.boss} pull ${D.pull}: healers' mana`);
  }

  function render() {
    if (!D || !root) return;
    const lowest = D.healers.filter((hl) => hl.lowest).sort((a, b) => a.lowest[1] - b.lowest[1])[0];
    const casts = D.innervates.reduce((n, i) => n + i.casts.length, 0);
    const idle = D.innervates.reduce((n, i) => n + i.ready_low.reduce((s, r) => s + (r.to_ms - r.from_ms), 0), 0) / 1000;
    const stats = kit.stats([
      lowest ? ["Lowest", `${lowest.lowest[1]}%`, lowest.lowest[1] < D.low ? "dead" : "", `${lowest.name} at ${kit.clock(lowest.lowest[0])}`] : null,
      ["Innervates", String(casts), "", D.innervates.length ? `from ${D.innervates.map((i) => i.druid).join(", ")}` : "no druid had one"],
      D.innervates.length ? ["Ready while a healer was low", `${Math.round(idle)} s`, idle ? "dead" : "", D.cooldown_s ? `its cooldown ${D.cooldown_s / 60} min, ready at the pull` : ""] : null,
    ]);
    const names = `<div class="legend">`  // the healers' names are at their lines
      + `<span><i style="background:var(--gold);transform:rotate(45deg) scale(.8)"></i>Innervate</span>`
      + `<span><i style="background:repeating-linear-gradient(45deg,var(--dead) 0 2px,transparent 2px 5px)"></i>ready while a healer was low</span></div>`;
    const head = `<div class="head"><h1>Healers' mana</h1><span class="sub">${kit.esc(D.boss)} pull ${D.pull}`
      + `${D.decided ? ` · until the wipe was decided, ${kit.clock(D.until_ms)}` : ""}</span></div>`;
    const never = D.never.length ? `<div class="note">${kit.esc(D.never.join(", "))} never cast Innervate in this report.</div>` : "";
    root.innerHTML = `<section data-panel class="mana note-board">${head}${stats}<div class="grid">${chart()}</div>${names}${never}</section>`;
  }

  return { show, relayout: render };
})();
