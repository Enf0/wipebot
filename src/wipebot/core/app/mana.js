// ---- the healers' mana on a pull (healing with mana): a line per healer in their class colour until the wipe was
// decided, the low line dashed, each Innervate a gold diamond on the line of the healer it went to; under it a lane
// per druid with their casts and, hatched red, the stretches it sat ready while a healer was low. One panel ----
views.mana = (() => {
  let D = null, root = null;
  const W = 680, LEFT = 34, RIGHT = 12, CH = 190, LANE = 22;

  async function show(into, key) {
    root = into;
    D = await app.callTool("view_data", { view: "mana", report: key.report, pull: key.pull, boss: key.boss });
    render();
  }

  function chart() {
    const x = (ms) => LEFT + ms / Math.max(D.until_ms, 1) * (W - LEFT - RIGHT), y = (v) => 10 + (100 - v) / 100 * CH;
    const lanes = D.innervates.length, h = 10 + CH + 24 + lanes * LANE + 4;
    let body = `<defs><pattern id="hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">`
      + `<rect width="2" height="5" fill="var(--dead)" opacity="0.55"/></pattern></defs>`;
    for (const v of [0, 50, 100]) {
      body += kit.line(LEFT, y(v), W - RIGHT, y(v), "var(--line)") + kit.text(LEFT - 6, y(v) + 3.5, `${v}%`, "tm", 'font-size="9.5" text-anchor="end"');
    }
    body += kit.line(LEFT, y(D.low), W - RIGHT, y(D.low), "var(--dead)", 'stroke-dasharray="4 3" opacity="0.8"');
    body += kit.text(W - RIGHT, y(D.low) - 4, `low: under ${D.low}%`, "td", 'font-size="9.5" text-anchor="end"');
    for (let s = 60; s * 1000 < D.until_ms; s += 60) {
      body += kit.line(x(s * 1000), 10, x(s * 1000), 10 + CH, "var(--line)", 'stroke-dasharray="2 4" opacity="0.6"');
      body += kit.text(x(s * 1000), 10 + CH + 14, kit.clock(s * 1000), "tm", 'font-size="9.5" text-anchor="middle"');
    }
    for (const hl of D.healers) {
      if (!hl.points.length) continue;
      const pts = hl.points.filter(([t]) => hl.dead_ms == null || t <= hl.dead_ms);
      const path = pts.map(([t, v], i) => `${i ? "L" : "M"}${x(t).toFixed(1)} ${y(v).toFixed(1)}`).join("");
      const tip = `${hl.name} (${hl.spec})` + (hl.lowest ? `: lowest ${hl.lowest[1]}% at ${kit.clock(hl.lowest[0])}` : "")
        + (hl.low.length ? `; under ${D.low}% ${hl.low.map(([a, b]) => `${kit.clock(a)}-${kit.clock(b)}`).join(", ")}` : "");
      body += `<path d="${path}" fill="none" stroke="${kit.esc(hl.color)}" stroke-width="2" stroke-linejoin="round"${kit.tip(tip)}/>`;
      const [lt, lv] = pts[pts.length - 1] || hl.points[0];
      if (hl.dead_ms != null) body += `<text x="${x(lt)}" y="${y(lv) + 4}" font-size="12" text-anchor="middle" class="td"${kit.tip(`${hl.name} died at ${kit.clock(hl.dead_ms)}`)}>✕</text>`;
    }
    const at = (name, ms) => {  // a healer's mana at a moment, for the diamond on their line
      const hl = D.healers.find((q) => q.name === name);
      const before = hl ? hl.points.filter(([t]) => t <= ms) : [];
      return before.length ? before[before.length - 1][1] : null;
    };
    D.innervates.forEach((inv, i) => {
      const ly = 10 + CH + 24 + i * LANE + 8;
      body += `<text x="0" y="${ly + 4}" font-size="10.5" font-weight="600" class="cls" style="--c:${kit.esc(inv.color)}">${kit.esc(inv.druid)}</text>`;
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
    const names = `<div class="legend">${D.healers.map((hl) => `<span><i style="background:${kit.esc(hl.color)}"></i>${kit.esc(hl.name)}</span>`).join("")}`
      + `<span><i style="background:var(--gold);transform:rotate(45deg) scale(.8)"></i>Innervate</span>`
      + `<span><i style="background:repeating-linear-gradient(45deg,var(--dead) 0 2px,transparent 2px 5px)"></i>ready while a healer was low</span></div>`;
    const head = `<div class="head"><h1>Healers' mana</h1><span class="sub">${kit.esc(D.boss)} pull ${D.pull}`
      + `${D.decided ? ` · until the wipe was decided, ${kit.clock(D.until_ms)}` : ""}</span></div>`;
    const never = D.never.length ? `<div class="note">${kit.esc(D.never.join(", "))} never cast Innervate in this report.</div>` : "";
    root.innerHTML = `<section data-panel class="mana note-board">${head}${stats}<div class="grid">${chart()}</div>${names}${never}</section>`;
  }

  return { show, relayout: render };
})();
