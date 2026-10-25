// ---- the pull: how it ended, boss health against raid damage taken, phases, the boss's casts, cooldowns, deaths ----
// Inline: the card (four numbers, the chart, two actions). Fullscreen: the same with lanes and the deaths to click.
views.pull = (() => {
  let D = null, root = null, open = null, drawnWidth = 0;

  async function show(into, key, opener) {
    root = into; open = opener;
    D = await app.callTool("view_data", { view: "pull", report: key.report, pull: key.pull, boss: key.boss });
    render();
    // The chart is drawn at the width it has, so its text stays readable: drawn again when that changes.
    new ResizeObserver(() => { if (root === into && Math.abs(into.clientWidth - drawnWidth) > 24) render(); }).observe(into);
  }

  function counted() { return D.deaths.filter((d) => !d.fallout); }

  function stats() {
    const out = [];
    if (D.kill) out.push(["Result", "Kill", ""]);
    else if (D.ended) out.push(["Ended by", D.ended.text + (D.ended.ms != null ? ` ${kit.clock(D.ended.ms)}` : ""), "decided"]);
    if (!D.kill && D.boss_pct != null) out.push(["Boss", `${D.boss_pct}%`, ""]);
    out.push([D.decided ? `Deaths before ${kit.clock(D.decided.ms)}` : "Deaths", String(counted().length), ""]);
    const first = D.deaths[0];
    if (first) out.push(["First death", `${first.name} ${kit.clock(first.ms)}`, "dead"]);
    return `<div class="stats">${out.map(([k, v, cls]) =>
      `<div class="stat"><div class="k">${kit.esc(k)}</div><div class="v ${cls}">${kit.esc(v)}</div></div>`).join("")}</div>`;
  }

  function render() {
    if (!D || !root) return;
    const full = app.context().displayMode === "fullscreen";
    const sub = [D.difficulty, kit.clock(D.duration_ms), D.kill ? "kill" : D.live ? "still going" : null].filter(Boolean).join(" · ");
    const head = `<div class="head"><h1>${kit.esc(D.title)}</h1><span class="sub">${kit.esc(sub)}</span></div>`;
    const startAt = D.deaths.length ? Math.max(0, D.deaths[0].ms - 3000) : 0;
    drawnWidth = root.clientWidth;
    const width = Math.max(300, drawnWidth || 640);
    if (app.picture()) {  // a still picture: the card without its buttons
      root.innerHTML = head + stats() + `<div id="pv-chart">${chart({ W: width, L: 40, R: 46, plotH: 130 })}</div>`;
      return;
    }
    if (!full) {
      root.innerHTML = head + stats() + `<div id="pv-chart">${chart({ W: width, L: 40, R: 46, plotH: width < 500 ? 100 : 120 })}</div>`
        + `<div class="actions">${app.canGoFullscreen() ? '<button class="primary" id="pv-open">Open pull</button>' : ""}`
        + `<button id="pv-replay">Replay</button></div>`;
      const o = root.querySelector("#pv-open");
      if (o) o.onclick = () => app.requestDisplayMode("fullscreen").then(render);
    } else {
      root.innerHTML = head + stats() + `<div class="full"><div class="scroll-x" id="pv-chart">${chart({ W: Math.max(560, width > 720 ? width - 264 : width), L: 112, R: 50, plotH: 240, lanes: true })}</div>`
        + deathsList() + `</div><div class="actions"><button id="pv-replay">Replay from the first death</button>`
        + (D.deaths.length ? `<button id="pv-ask">Ask what killed ${kit.esc(D.deaths[0].name)}</button>` : "") + `</div>`;
      root.querySelectorAll(".deaths button").forEach((b) => b.onclick = () => replayAt(+b.dataset.ms - 3000));
      const ask = root.querySelector("#pv-ask");
      if (ask) ask.onclick = () => app.sendMessage(`What killed ${D.deaths[0].name} at ${kit.clock(D.deaths[0].ms)} on ${D.title}?`);
    }
    root.querySelector("#pv-replay").onclick = () => replayAt(startAt);
  }

  function replayAt(ms) {
    open("replay", { report: D.report, pull: D.pull, boss: D.boss, at_ms: Math.max(0, Math.round(ms)), players: [] });
  }

  function deathsList() {
    let cutShown = false;
    const rows = D.deaths.map((d) => {
      let cut = "";
      if (d.fallout && !cutShown && D.decided) { cut = `<li class="cut">${kit.esc(kit.clock(D.decided.ms))}: ${kit.esc(D.decided.why)}; later deaths are fallout</li>`; cutShown = true; }
      return cut + `<li class="${d.fallout ? "fallout" : ""}"><button data-ms="${d.ms}" title="Replay from just before">`
        + `<span class="at">${kit.clock(d.ms)}</span><span><span class="who" style="color:${kit.esc(d.color)}">${kit.esc(d.name)}</span>`
        + `<span class="by">${kit.esc(d.by)} · ${d.alive} alive</span></span></button></li>`;
    }).join("");
    return `<div class="deaths"><h2>Deaths (${D.deaths.length})</h2><ol>${rows || '<li class="cut">none</li>'}</ol></div>`;
  }

  // Phase bands: the first phase quiet, a phase the fight went back to gold, the others blue.
  function phaseBands() {
    const seen = new Set();
    return D.phases.map((p, i) => {
      const end = i + 1 < D.phases.length ? D.phases[i + 1].ms : D.duration_ms;
      const again = seen.has(p.id); seen.add(p.id);
      return { from: p.ms, to: end, label: `P${p.id}${again ? " (2nd time)" : ""}`, name: p.name,
               fill: again ? "var(--gold)" : i === 0 ? "var(--line)" : "var(--p2)", opacity: again ? 0.55 : i === 0 ? 1 : 0.6 };
    });
  }

  function niceMax(v) {
    const step = 10 ** Math.floor(Math.log10(Math.max(v, 1)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * step >= v) return m * step;
    return 10 * step;
  }

  // Labels left to right after their marks, each only where it fits before the next mark of its lane (`marks`, the
  // lane's x positions) and after the label before it; the rest stay a mark with a tooltip.
  function labels(items, x, y, cls, marks, right, size = 9.5) {
    let end = -Infinity, out = "";
    const xs = marks.slice().sort((a, b) => a - b);
    for (const [ms, s] of items) {
      const at = x(ms) + 6, w = kit.width(s, size), next = xs.find((m) => m > x(ms) + 1) ?? right;
      if (at > end && at + w < next - 3) { out += kit.text(at, y, s, cls, `font-size="${size}"`); end = at + w + 4; }
    }
    return out;
  }

  function chart({ W, L, R, plotH, lanes }) {
    const x = (ms) => L + ms / D.duration_ms * (W - L - R);
    let y = 4, o = "";
    const label = (s, yy) => lanes ? kit.text(6, yy, s, "tm", 'font-size="10.5"') : "";
    // phases
    if (D.phases.length > 1 || lanes) {
      for (const b of phaseBands()) {
        o += `<rect x="${x(b.from)}" y="${y}" width="${Math.max(1, x(b.to) - x(b.from) - 1)}" height="13" rx="3" fill="${b.fill}" opacity="${b.opacity}"/>`;
        const room = x(b.to) - x(b.from) - 8;
        const s = [lanes && b.name ? `${b.label} ${b.name}` : null, b.label, `P${b.label.slice(1, 2)}`]
          .find((t) => t && kit.width(t, 9.5) < room);
        if (s) o += kit.text(x(b.from) + 5, y + 10, s, "t", 'font-size="9.5" font-weight="600"');
      }
      o += label("Phase", y + 10);
      y += 20;
    }
    const tracked = D.casts.filter((c) => c.tracked);
    if (lanes) {
      o += label("Boss casts", y + 12);
      for (const c of D.casts) if (!c.tracked) o += `<rect x="${x(c.ms) - 1}" y="${y + 5}" width="2" height="9" fill="var(--muted)" opacity=".7"><title>${kit.esc(c.name)} ${kit.clock(c.ms)}</title></rect>`;
      for (const c of tracked) o += `<path d="M${x(c.ms)} ${y + 3} l5 6 l-5 6 l-5 -6z" fill="var(--gold)"><title>${kit.esc(c.name)} ${kit.clock(c.ms)}</title></path>`;
      o += labels(tracked.map((c) => [c.ms, c.name]), x, y + 13, "tg", D.casts.map((c) => x(c.ms)), W);
      y += 22;
      for (const [kind, title, color] of [["raid", "Raid cooldowns", "var(--blue)"], ["healing", "Healer cooldowns", "var(--green)"]]) {
        const list = D.cooldowns.filter((c) => c.kind === kind).map((c) => [c.ms, c.name, c.player]);
        if (kind === "raid") for (const ms of D.lusts) list.push([ms, "Bloodlust", ""]);
        list.sort((a, b) => a[0] - b[0]);
        o += label(title, y + 12);
        for (const [ms, name, who] of list) o += `<circle cx="${x(ms)}" cy="${y + 9}" r="3.2" fill="${color}"><title>${kit.esc(name)}${who ? " (" + kit.esc(who) + ")" : ""} ${kit.clock(ms)}</title></circle>`;
        o += labels(list.map(([ms, name]) => [ms, name]), x, y + 13, "t", list.map(([ms]) => x(ms)), W);
        y += 20;
      }
      y += 4;
    } else if (tracked.length) {
      for (const c of tracked) o += `<path d="M${x(c.ms)} ${y} l4 5 l-4 5 l-4 -5z" fill="var(--gold)"/>`;
      y += 14;
    }
    const top = y, bottom = y + plotH;
    const tick = [5, 10, 15, 30, 60, 120, 300].map((s) => s * 1000).find((s) => D.duration_ms / s <= (lanes ? 12 : 8)) || 600000;
    for (let t = 0; t <= D.duration_ms; t += tick) o += kit.line(x(t), top, x(t), bottom, "var(--line)");
    o += kit.line(L, bottom, W - R, bottom, "var(--line)");
    // raid damage taken per second
    const per = D.damage.per_s, step = D.damage.step_ms;
    if (per.length) {
      const max = niceMax(Math.max(...per.map((p) => p[1])));
      const yD = (v) => bottom - v / max * plotH;
      const pts = per.map(([ms, v]) => [Math.min(ms + step / 2, D.duration_ms), v]);
      o += `<path d="M${x(pts[0][0])} ${bottom} ${pts.map(([ms, v]) => `L${x(ms)} ${yD(v)}`).join(" ")} L${x(pts[pts.length - 1][0])} ${bottom}Z" fill="var(--blue)" opacity=".18"/>`;
      o += `<path d="${pts.map(([ms, v], i) => `${i ? "L" : "M"}${x(ms)} ${yD(v)}`).join(" ")}" fill="none" stroke="var(--blue)" stroke-width="1.2" opacity=".8"/>`;
      o += kit.text(L - 6, yD(max) + 8, `${kit.short(max)}/s`, "tm", 'text-anchor="end" font-size="10"');
      o += kit.text(L - 6, bottom, "0", "tm", 'text-anchor="end" font-size="10"');
    }
    // boss health
    if (D.health.length) {
      const least = Math.min(...D.health.map((p) => p[1]));
      const lo = Math.max(0, Math.floor((least - 5) / 25) * 25);
      const yH = (p) => top + (100 - p) / (100 - lo) * plotH;
      // a new health bar (the next boss of a phase) starts a new line instead of a jump up
      const d = D.health.map(([ms, p], i) => `${i && p - D.health[i - 1][1] <= 10 ? "L" : "M"}${x(ms)} ${yH(p)}`).join(" ");
      o += `<path d="${d}" fill="none" stroke="var(--gold)" stroke-width="2.4" stroke-linejoin="round"/>`;
      const [lastMs, lastPct] = D.health[D.health.length - 1];
      o += `<circle cx="${x(lastMs)}" cy="${yH(lastPct)}" r="3.5" fill="var(--gold)"/>`;
      o += kit.text(W - R + 5, yH(lastPct) + 4, `${D.kill ? 0 : (D.boss_pct ?? lastPct)}%`, "tg", 'font-size="10.5" font-weight="600"');
      if (Math.abs(yH(100) - yH(lastPct)) > 14) o += kit.text(W - R + 5, yH(100) + 4, "100%", "tm", 'font-size="10"');
    }
    // when the wipe was decided
    if (D.decided) {
      const dx = x(D.decided.ms), right = dx > (W - R) * 0.6;
      o += kit.line(dx, top - 4, dx, bottom + 28, "var(--decided)", 'stroke-width="1.6" stroke-dasharray="4 3"');
      const anchor = right ? `text-anchor="end"` : "", tx = right ? dx - 5 : dx + 5;
      o += kit.text(tx, top + 22, `decided ${kit.clock(D.decided.ms)}`, "to", `${anchor} font-size="10.5" font-weight="600"`);
      o += kit.text(tx, top + 35, D.decided.why, "to", `${anchor} font-size="10"`);
    }
    // deaths, those whose marks would touch as one; after the wipe was decided grey
    const dy = bottom + 16, groups = [];
    for (const d of D.deaths) {
      const g = groups[groups.length - 1];
      if (g && x(d.ms) - x(g[0].ms) < 14 && g[0].fallout === d.fallout) g.push(d); else groups.push([d]);
    }
    o += label("Deaths", dy + 4);
    let lastEdge = -Infinity;
    for (const ds of groups) {  // several at once: how many, in the mark; a mark right after another moves over
      const d = ds[0], r = ds.length > 1 ? 7 : 4.2, cx = Math.max(x(d.ms), lastEdge + r + 1);
      lastEdge = cx + r;
      o += `<circle cx="${cx}" cy="${dy}" r="${r}" fill="${d.fallout ? "var(--muted)" : "var(--dead)"}" opacity="${d.fallout ? 0.65 : 1}"><title>${ds.map((e) => `${e.name} (${e.by})`).map(kit.esc).join(", ")} ${kit.clock(d.ms)}</title></circle>`;
      if (ds.length > 1) o += kit.text(cx, dy + 3.5, ds.length, "", 'text-anchor="middle" font-size="9.5" font-weight="700" fill="var(--bg)"');
    }
    const ay = bottom + 40;
    for (let t = 0; t <= D.duration_ms; t += tick) o += kit.text(x(t), ay, kit.clock(t), "tm", 'text-anchor="middle" font-size="10"');
    // the legend, under the time axis, wrapping on a narrow chart
    const keys = [[(lx, ly) => `<rect x="${lx}" y="${ly - 6}" width="12" height="3" fill="var(--gold)"/>`, "boss health"],
      [(lx, ly) => `<rect x="${lx + 1}" y="${ly - 8}" width="10" height="8" fill="var(--blue)" opacity=".35"/>`, "raid damage taken /s"],
      [(lx, ly) => `<circle cx="${lx + 5}" cy="${ly - 4}" r="4" fill="var(--dead)"/>`, "death"]];
    if (D.decided) keys.push([(lx, ly) => `<circle cx="${lx + 5}" cy="${ly - 4}" r="4" fill="var(--muted)" opacity=".6"/>`, "after it was decided"]);
    let lx = 6, ly = ay + 20;
    for (const [mark, name] of keys) {
      const w = 16 + kit.width(name, 10) + 14;
      if (lx > 6 && lx + w > W) { lx = 6; ly += 16; }
      o += mark(lx, ly) + kit.text(lx + 16, ly, name, "tm", 'font-size="10"');
      lx += w;
    }
    return kit.svg(W, ly + 6, o, `${D.title}: boss health and raid damage taken over time, phases, deaths`);
  }

  return { show, relayout: render };
})();
