// ---- the player: their output and where it stands, the time they lost, when they cast, capped or died, their
// cooldowns; fullscreen their casts, and against top players of their spec on asking (compare_with_top's casts) ----
views.player = (() => {
  let D = null, root = null, drawnWidth = 0, loadingTop = false, topFailed = "";
  const TOP_ROWS = 5;  // a picture's or card's casts against the top: the biggest differences

  async function show(into, key) {
    root = into; loadingTop = false; topFailed = "";
    D = await app.callTool("view_data", { view: "player", report: key.report, pull: key.pull, boss: key.boss,
                                          player: key.player, top: !!key.top });
    render();
    new ResizeObserver(() => { if (root === into && Math.abs(into.clientWidth - drawnWidth) > 24) render(); }).observe(into);
  }

  const who = () => `${D.player.spec} ${D.player.class}`;

  function stats() {
    const o = D.output, items = [];
    if (o) items.push([`${o.what === "healing" ? "Healing" : "Damage"} per s alive`, kit.short(o.per_s_alive), "",
                       o.rank ? `rank ${o.rank} of ${o.of} ${o.role}` : ""]);
    const costs = [[D.lost.dead_s, "dead"], [D.lost.gaps_s, "casting gaps"], [D.lost.capped_s, `${D.lost.resource} capped`]]
      .filter(([s]) => s >= 1).sort((a, b) => b[0] - a[0]);
    items.push([D.decided ? `Time lost before ${kit.clock(D.decided.ms)}` : "Time lost",
                costs.length ? `${Math.round(costs[0][0])} s ${costs[0][1]}` : "none", costs[0] && costs[0][1] === "dead" ? "dead" : "",
                costs.slice(1).map(([s, w]) => `${Math.round(s)} s ${w}`).join(", ")]);
    const judged = D.cooldowns.filter((c) => c.possible);
    if (judged.length) {
      const used = judged.reduce((n, c) => n + Math.min(c.uses.length, c.possible), 0), possible = judged.reduce((n, c) => n + c.possible, 0);
      items.push(["Cooldowns", `${used} of ${possible}`, used < possible ? "decided" : "", "pressed on cooldown"]);
    }
    const d = D.died[0];
    items.push(["Died", d ? kit.clock(d.ms) : "no", d && !d.fallout ? "dead" : "", d ? (d.fallout ? "after the wipe was decided" : d.by) : ""]);
    return kit.stats(items) + notes();
  }

  // What went well and what to fix, picked by the server (views.player_notes)
  function notes() {
    const n = D.notes;
    if (!n || !(n.good.length || n.fix.length)) return "";
    const li = (cls, mark, s) => `<li class="${cls}"><span class="mk">${mark}</span>${kit.esc(s)}</li>`;
    return `<ul class="notes">${n.good.map((s) => li("good", "+", s)).join("")}${n.fix.map((s) => li("fix", "−", s)).join("")}</ul>`;
  }

  function render() {
    if (!D || !root) return;
    const full = app.context().displayMode === "fullscreen", picture = app.picture();
    drawnWidth = root.clientWidth;
    const width = Math.max(300, drawnWidth || 640);
    const p = D.player;
    const sub = [who(), p.hero, p.ilvl ? `item level ${p.ilvl}` : null, D.title].filter(Boolean).join(" · ");
    const head = `<div class="head"><h1 style="color:${kit.esc(p.color)}">${kit.esc(p.name)}</h1><span class="sub">${kit.esc(sub)}</span></div>`;
    let html = head + stats() + standing(width) + ribbon({ W: full ? Math.max(560, width) : width, lanes: full });
    if (picture) {
      root.innerHTML = html + (D.top && typeof D.top === "object" ? topSection(width, TOP_ROWS) : "");
      return;
    }
    if (!full) {
      html += (D.top && typeof D.top === "object" ? topSection(width, TOP_ROWS) : "")
        + `<div class="actions">${app.canGoFullscreen() ? '<button class="primary" id="pl-open">Open player</button>' : ""}`
        + (D.top ? "" : `<button id="pl-top">Against top players</button>`) + `</div>`;
      root.innerHTML = html;
      const o = root.querySelector("#pl-open");
      if (o) o.onclick = () => app.requestDisplayMode("fullscreen").then(render);
    } else {
      root.innerHTML = html + `<div class="full"><div>${topOrCasts(Math.max(400, width > 720 ? width - 264 : width))}</div>${side()}</div>`;
    }
    const t = root.querySelector("#pl-top");
    if (t) t.onclick = () => (app.canGoFullscreen() && !full ? app.requestDisplayMode("fullscreen") : Promise.resolve()).then(loadTop);
  }

  async function loadTop() {
    if (loadingTop || D.top) { render(); return; }
    loadingTop = true; render();
    try {
      D = await app.callTool("view_data", { view: "player", report: D.report, pull: D.pull, boss: D.boss, player: D.player.name, top: true });
    } catch (e) {
      topFailed = e.message;
      app.log(`player top: ${e.message}`);
    }
    loadingTop = false; render();
  }

  // Where their output stands: among the spec's ranked kills of the boss (a wipe), or their parse (a kill).
  function standing(W) {
    const s = D.standing;
    if (!s) return "";
    const L = 10, R = 14;
    const what = D.output.what === "healing" ? "Healing" : "Damage";
    if (s.kind === "parse") {
      const x = (v) => L + v / 100 * (W - L - R);
      let o = kit.text(L, 12, `${what} parse against ${who()} kills of ${D.boss}`, "tm", 'font-size="10.5"');
      o += `<rect x="${x(0)}" y="22" width="${x(100) - x(0)}" height="8" rx="4" fill="var(--line)"/>`;
      o += `<rect x="${x(0)}" y="22" width="${x(s.percentile) - x(0)}" height="8" rx="4" fill="var(--gold)"/>`;
      o += `<circle cx="${x(s.percentile)}" cy="26" r="7" fill="${kit.esc(D.player.color)}" stroke="var(--bg)" stroke-width="2"/>`;
      o += kit.text(x(s.percentile), 48, `${s.percentile}th percentile`, "t", 'text-anchor="middle" font-size="11" font-weight="600"');
      return kit.svg(W, 56, o, `${D.player.name}'s parse: ${s.percentile}th percentile`);
    }
    const values = [...s.points.map((p) => p[1]), s.mine], lo = Math.min(...values) * 0.92, hi = Math.max(...values) * 1.04;
    const x = (v) => L + (v - lo) / (hi - lo) * (W - L - R), y = 32;
    const caption = [`${what} per second alive among ${s.exact ? s.n : "more than " + s.n} ranked ${who()} kills of ${D.boss}`,
                     `${what}/s alive among ${s.n}${s.exact ? "" : "+"} ranked ${who()} kills`]
      .find((c) => kit.width(c, 10.5) < W - L) || `${what}/s alive among the spec's ranked kills`;
    let o = kit.text(L, 12, caption, "tm", 'font-size="10.5"');
    o += `<rect x="${x(lo)}" y="${y}" width="${x(hi) - x(lo)}" height="8" rx="4" fill="var(--line)"/>`;
    const ends = [];  // labels under the scale: a second row where one would run into the one before
    for (const [label, v] of s.points.slice().sort((a, b) => a[1] - b[1])) {
      o += kit.line(x(v), y - 3, x(v), y + 11, "var(--muted)");
      const w = Math.max(kit.width(label, 10), kit.width(kit.short(v), 9.5)), left = x(v) - w / 2;
      const row = ends[0] === undefined || left > ends[0] + 4 ? 0 : 1;
      ends[row] = x(v) + w / 2;
      o += kit.text(x(v), y + 24 + row * 22, label, "tm", 'text-anchor="middle" font-size="10"');
      o += kit.text(x(v), y + 35 + row * 22, kit.short(v), "tm", 'text-anchor="middle" font-size="9.5"');
    }
    o += `<circle cx="${x(s.mine)}" cy="${y + 4}" r="7" fill="${kit.esc(D.player.color)}" stroke="var(--bg)" stroke-width="2"/>`;
    const mine = `${D.player.name} ${kit.short(s.mine)}`, right = x(s.mine) + kit.width(mine) + 12 > W;
    o += kit.text(right ? x(s.mine) - 10 : x(s.mine) + 10, y - 6, mine, "t", `${right ? 'text-anchor="end"' : ""} font-size="11" font-weight="600"`);
    const h = y + (ends[1] !== undefined ? 62 : 40);
    return kit.svg(W, h, o, `${D.player.name}'s ${what.toLowerCase()} per second alive against the spec's ranked kills`);
  }

  // The pull as they played it: casting, casting gaps, their resource at its cap and dead, their cooldowns above,
  // the boss's main casts below.
  function ribbon({ W, lanes }) {
    const L = lanes ? 112 : 10, R = 14, x = (ms) => L + ms / D.duration_ms * (W - L - R);
    let y = 6, o = '<defs><pattern id="pl-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
      + '<rect width="2" height="5" fill="var(--dead)" opacity=".55"/></pattern></defs>';
    const label = (s, yy) => lanes ? kit.text(6, yy, s, "tm", 'font-size="10.5"') : "";
    const diamond = (cx, top, tip = "") => `<path d="M${cx} ${top} l6 6 l-6 6 l-6 -6z" fill="var(--gold)"${kit.tip(tip)}/>`;
    // cooldowns: an icon at each use, nudged up a row where it would cover another's; their names in the legend
    const uses = D.cooldowns.flatMap((c) => c.uses.map((ms) => [ms, c.name, c.icon])).sort((a, b) => a[0] - b[0]);
    const S = 16, ends = [];  // per row, where its last icon ends
    const placed = uses.map(([ms, name, ic]) => {
      const left = Math.max(0, x(ms) - S / 2);
      let row = ends.findIndex((e) => left >= e + 1);
      if (row < 0) row = ends.push(-Infinity) - 1;
      ends[row] = left + S;
      return [ms, name, ic, left, row];
    });
    if (uses.length) {
      const top = (row) => y + (ends.length - 1 - row) * (S + 2);
      o += label("Cooldowns", top(0) + 12);
      for (const [ms, name, ic, left, row] of placed) {
        const tip = `${name} ${kit.clock(ms)}`;
        o += kit.icon(D.img_base, ic, left, top(row), S, tip) || diamond(left + S / 2, top(row) + 2, tip);
      }
      y = top(0) + S + 6;
    }
    // casting
    const bar = y, h = 18, end = Math.min(D.alive_until_ms, D.duration_ms);
    o += label("Casting", bar + 13);
    o += `<rect x="${x(0)}" y="${bar}" width="${x(end) - x(0)}" height="${h}" rx="3" fill="var(--blue)" opacity=".45"/>`;
    const span = (a, b) => `${kit.clock(a)}–${kit.clock(b)} (${Math.max(1, Math.round((b - a) / 1000))} s)`;
    for (const [a, b] of D.gaps) {
      o += `<rect x="${x(a)}" y="${bar}" width="${Math.max(1.5, x(b) - x(a))}" height="${h}" fill="var(--bg)" stroke="var(--muted)" stroke-width=".8"/>`
        + kit.hit(Math.min(x(a), (x(a) + x(b)) / 2 - 4), bar, Math.max(8, x(b) - x(a)), h, `No cast ${span(a, b)}`);
    }
    for (const [a, b] of D.capped) {
      o += `<rect x="${x(a)}" y="${bar}" width="${Math.max(1.5, x(b) - x(a))}" height="${h / 2}" fill="var(--decided)" opacity=".85"/>`
        + kit.hit(Math.min(x(a), (x(a) + x(b)) / 2 - 4), bar, Math.max(8, x(b) - x(a)), h / 2, `${D.lost.resource} at its cap ${span(a, b)}`);
    }
    // dead until they were back (a battle res, Reincarnation: their next cast) or the pull's end
    for (const [a, b] of D.dead) {
      const died = D.died.find((d) => Math.abs(d.ms - a) < 50);
      o += `<rect x="${x(a)}" y="${bar}" width="${Math.max(4, x(b) - x(a))}" height="${h}" fill="url(#pl-hatch)"${kit.tip(`Dead ${span(a, b)}${died ? `: ${died.by}` : ""}`)}/>`;
    }
    if (D.decided) {
      o += kit.line(x(D.decided.ms), bar - 4, x(D.decided.ms), bar + h + 4, "var(--decided)", 'stroke-width="1.6" stroke-dasharray="3 3"');
    }
    y = bar + h + 6;
    // the boss's main casts
    if (D.tracked.length) {
      o += label("Boss main casts", y + 8);
      for (const c of D.tracked) {
        o += `<rect x="${x(c.ms) - 1}" y="${y}" width="2" height="8" fill="var(--gold)" opacity=".8"/>` + kit.hit(x(c.ms) - 4, y - 1, 8, 10, `${c.name} ${kit.clock(c.ms)}, one of the boss's main casts`);
      }
      if (lanes) o += kit.labels(D.tracked.map((c) => [c.ms, c.name]), x, y + 8, "tg", D.tracked.map((c) => x(c.ms)), W, 9.5);
      y += 12;
    }
    const tick = [5, 10, 15, 30, 60, 120].map((s) => s * 1000).find((s) => D.duration_ms / s <= (lanes ? 12 : 7)) || 300000;
    for (let t = 0; t <= D.duration_ms; t += tick) {
      o += kit.text(x(t), y + 12, kit.clock(t), "tm", `text-anchor="${t || lanes ? "middle" : "start"}" font-size="10"`);
    }
    // the legend, wrapping on a narrow card
    const keys = [[(lx, ly) => `<rect x="${lx}" y="${ly - 8}" width="12" height="8" rx="2" fill="var(--blue)" opacity=".45"/>`, "casting"],
      [(lx, ly) => `<rect x="${lx}" y="${ly - 8}" width="12" height="8" fill="var(--bg)" stroke="var(--muted)"/>`, "no cast"]];
    if (D.capped.length) keys.push([(lx, ly) => `<rect x="${lx}" y="${ly - 8}" width="12" height="4" fill="var(--decided)"/>`, `${D.lost.resource} capped`]);
    if (D.died.length) keys.push([(lx, ly) => `<rect x="${lx}" y="${ly - 8}" width="12" height="8" fill="url(#pl-hatch)"/>`, "dead"]);
    for (const c of D.cooldowns.filter((c) => c.uses.length)) {
      keys.push([(lx, ly) => kit.icon(D.img_base, c.icon, lx, ly - 10, 12) || diamond(lx + 6, ly - 10), c.name]);
    }
    // where the wipe was decided: in the legend, so its label never runs into the cooldowns' at the pull's end
    if (D.decided) keys.push([(lx, ly) => `<line x1="${lx + 6}" y1="${ly - 10}" x2="${lx + 6}" y2="${ly + 1}" stroke="var(--decided)" stroke-width="1.6" stroke-dasharray="3 3"/>`,
                              `wipe decided ${kit.clock(D.decided.ms)}`]);
    let lx = 6, ly = y + 32;
    for (const [mark, name] of keys) {
      const w = 18 + kit.width(name, 10) + 14;
      if (lx > 6 && lx + w > W) { lx = 6; ly += 16; }
      o += mark(lx, ly) + kit.text(lx + 17, ly, name, "tm", 'font-size="10"');
      lx += w;
    }
    return kit.svg(W, ly + 6, o, `${D.player.name} over the pull: casting, gaps, dead, cooldowns`);
  }

  // Their casts per minute against the top parses', the biggest differences first (`n` of them), each row on its own
  // scale: the bar is the top's range, the tick its median, the dot theirs.
  function topSection(W, n) {
    const t = D.top;
    const counted = t.rates.filter((r) => r.median != null && (r.median >= 0.3 || r.mine >= 0.3));
    const gap = (r) => r.flag ? 10 : Math.abs(r.mine - r.median) / Math.max(r.median, 0.5);
    const rows = (n ? counted.slice().sort((a, b) => gap(b) - gap(a)).slice(0, n) : counted);
    const L = Math.min(170, W * 0.36), R = 86, rh = 22, T = 18;
    let o = kit.text(0, 11, `Casts per minute alive against ${t.parses} top ${who()}${t.same_hero && t.hero ? ` (${t.hero})` : ""} parses`
                     + (n ? ", biggest differences" : ""), "tm", 'font-size="10.5"');
    rows.forEach((r, i) => {
      const y = T + i * rh + 11, max = Math.max(r.high, r.mine) * 1.08 || 1, x = (v) => L + v / max * (W - L - R);
      // red where the tool flags it (never cast, far fewer, far more), orange outside the top's range
      const fill = r.flag ? "var(--dead)" : r.mine < r.low || r.mine > r.high ? "var(--decided)" : kit.esc(D.player.color);
      o += kit.icon(D.img_base, r.icon, 0, y - 7, 14) + kit.text(r.icon && D.img_base ? 19 : 0, y + 4, r.name.length > 24 ? r.name.slice(0, 23) + "…" : r.name, "t", 'font-size="11"');
      o += kit.line(L, y, W - R, y, "var(--line)");
      o += `<rect x="${x(r.low)}" y="${y - 4}" width="${Math.max(2, x(r.high) - x(r.low))}" height="8" rx="4" fill="var(--muted)" opacity=".3"/>`;
      o += kit.line(x(r.median), y - 7, x(r.median), y + 7, "var(--gold)", 'stroke-width="2.4"');
      const tip = `${r.name}: ${r.mine.toFixed(1)} a minute alive; the top ${t.parses}: ${r.low.toFixed(1)}–${r.high.toFixed(1)}, median ${r.median.toFixed(1)}` + (r.flag ? `\n${r.flag[0].toUpperCase()}${r.flag.slice(1)}` : "");
      o += `<circle cx="${x(r.mine)}" cy="${y}" r="5" fill="${fill}" stroke="var(--bg)" stroke-width="1.5"/>` + kit.hit(L, y - 9, W - R - L, 18, tip);
      o += kit.text(W - R + 8, y + 4, `${r.mine.toFixed(1)} / ${r.median.toFixed(1)}`, r.flag ? "td" : "t", 'font-size="10.5"');
    });
    const h = T + rows.length * rh + 4;
    return `<div class="section-title">Against top players</div>` + kit.svg(W, h, o, `${D.player.name}'s casts per minute against top players`)
      + (n ? "" : `<div class="note">Total ${t.total[0]} / ${t.total[1]} casts per minute. The top kills are whole fights; a wipe with Bloodlust at the start is busier.</div>`);
  }

  function topOrCasts(W) {
    if (D.top && typeof D.top === "object") return topSection(W, 0);
    const why = typeof D.top === "string" ? D.top : topFailed;
    const button = loadingTop ? `<button disabled>Reading the top parses…</button>`
      : `<button id="pl-top">Against top players</button>`;
    const max = Math.max(...D.casts.map((c) => c.per_min), 1), L = Math.min(170, W * 0.36), rh = 20;
    let o = "";
    D.casts.forEach((c, i) => {
      const y = i * rh + 12;
      o += kit.icon(D.img_base, c.icon, 0, y - 7, 14) + kit.text(c.icon && D.img_base ? 19 : 0, y + 4, c.name.length > 24 ? c.name.slice(0, 23) + "…" : c.name, "t", 'font-size="11"');
      o += `<rect x="${L}" y="${y - 5}" width="${Math.max(1, (W - L - 60) * c.per_min / max)}" height="10" rx="3" fill="var(--blue)" opacity=".55"/>`;
      o += kit.text(L + (W - L - 60) * c.per_min / max + 6, y + 4, `${c.per_min.toFixed(1)}/min`, "tm", 'font-size="10.5"');
    });
    return `<div class="section-title">Casts per minute alive</div>` + kit.svg(W, D.casts.length * rh + 6, o, `${D.player.name}'s casts per minute`)
      + `<div class="actions">${button}</div>` + (why ? `<div class="note">${kit.esc(why)}</div>` : "");
  }

  function side() {
    const cds = D.cooldowns.map((c) => {
      const count = c.possible && c.uses.length <= c.possible ? `${c.uses.length}/${c.possible}` : `${c.uses.length}`;  // more: a talent shortens it
      return `<li><span>${kit.iconImg(D.img_base, c.icon)}${kit.esc(c.name)}</span><span class="count ${c.possible && c.uses.length < c.possible ? "short" : ""}">${count}</span>`
        + `<span class="times">${c.uses.length ? c.uses.map(kit.clock).join(", ") : "not used"}</span></li>`;
    }).join("");
    const top = (D.output?.top || []).map((a) => `<li><span>${kit.iconImg(D.img_base, a.icon)}${kit.esc(a.name)}</span><span class="count">${kit.short(a.total)}</span></li>`).join("");
    return `<div><div class="panel"><div class="section-title" style="margin-top:2px">Major cooldowns</div><ul class="cds">${cds || "<li>none listed</li>"}</ul>`
      + `<div class="note">Uses / possible if pressed on cooldown until they died or the wipe was decided.</div></div>`
      + (top ? `<div class="panel"><div class="section-title" style="margin-top:2px">Top ${kit.esc(D.output.what)}</div><ul class="cds">${top}</ul></div>` : "")
      + `</div>`;
  }

  return { show, relayout: render };
})();
