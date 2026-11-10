// ---- the pull: how it ended, boss health against raid damage taken, phases, the boss's casts, cooldowns, deaths ----
// Inline: the card (kit.card: how it ended in one sentence, four numbers, the chart with the cooldowns in one row,
// two buttons). Fullscreen: the same with a lane each for the boss's casts, the raid's cooldowns and the healers',
// and the deaths to click.
views.pull = (() => {
  let D = null, root = null, open = null;

  async function show(into, key, opener) {
    root = into; open = opener;
    D = await app.callTool("view_data", { view: "pull", report: key.report, pull: key.pull, boss: key.boss });
    render();
    kit.watch(into, () => root === into, render);
  }

  function counted() { return D.deaths.filter((d) => !d.fallout); }

  // The card's numbers; how the pull ended is its headline (views.pull_headline). A kill: how fast and how clean,
  // against other kills of the boss. A wipe: how far it got, and where that stands against the log's earlier wipes
  // on the boss and the guild's earlier logs, as the post says; on a boss counted by phase those are Warcraft Logs'
  // whole-fight %, so the wipe's own leads, all named WCL.
  function stats() {
    const out = [], b = D.best, wcl = D.phase ? "WCL " : "";
    if (D.kill && D.ranking) {
      out.push(["Speed", `${kit.ordinal(D.ranking.speed)} percentile`, "", "against other kills of the boss"]);
      out.push(["Execution", `${kit.ordinal(D.ranking.execution)} percentile`, "", "deaths and damage taken"]);
    }
    if (!D.kill && D.boss_pct != null) {
      const where = D.phase && D.wcl_pct != null ? [`WCL ${D.wcl_pct.toFixed(1)}%`] : [];
      if (b && b.new_guild) where.push("new guild best");
      else if (b && b.tonight != null) where.push(b.new_tonight ? "best of this log so far" : `best of this log ${wcl}${b.tonight.toFixed(1)}%`);
      out.push(["Boss", `${D.phase ? `${D.phase} ` : ""}${D.boss_pct}%`, b && b.new_guild ? "best" : "", where.join(" · ")]);
    }
    out.push([D.decided ? `Deaths before ${kit.clock(D.decided.ms)}` : "Deaths", String(counted().length), "",
              D.decided && D.deaths.length > counted().length ? `${D.deaths.length - counted().length} after it: fallout` : ""]);
    const first = D.deaths[0];
    if (first) out.push(["First death", `${first.name} ${kit.clock(first.ms)}`, "dead", first.by]);
    if (b && b.guild) out.push(["Guild best before", `${wcl}${b.guild.pct.toFixed(1)}%`, "", b.guild.day]);
    return out;
  }

  function render() {
    if (!D || !root) return;
    const { full, picture, width } = kit.frame(root);
    const sub = [D.difficulty, kit.clock(D.duration_ms), D.kill ? "kill" : D.live ? "still going" : null].filter(Boolean).join(" · ");
    const startAt = D.deaths.length ? Math.max(0, D.deaths[0].ms - 3000) : 0;
    const card = (drawn, buttons) => kit.card({ title: D.title, sub, headline: D.headline, stats: stats(), chart: drawn,
                                                 open: "Open pull", buttons, url: D.url });
    if (picture || !full) {  // the card; a still picture is the same without its buttons
      const plotH = picture ? 130 : width < 500 ? 100 : 120;
      root.innerHTML = card(`<div id="pv-chart">${chart({ W: width, L: 40, R: 54, plotH })}</div>`, [["pv-replay", "Replay"]]);
    } else {
      root.innerHTML = card(`<div class="full"><div class="scroll-x" id="pv-chart">${chart({ W: Math.max(560, width > 720 ? width - 264 : width), L: 112, R: 50, plotH: 240, lanes: true })}</div>`
        + deathsList() + `</div>`, [["pv-replay", "Replay from the first death"], D.deaths.length && ["pv-ask", `Ask what killed ${D.deaths[0].name}`]]);
      root.querySelectorAll(".deaths button").forEach((b) => b.onclick = () => replayAt(+b.dataset.ms - 3000));
      kit.press(root, "pv-ask", () => app.sendMessage(`What killed ${D.deaths[0].name} at ${kit.clock(D.deaths[0].ms)} on ${D.title}?`));
    }
    kit.press(root, "pv-replay", () => replayAt(startAt));
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
        + `<span class="at">${kit.clock(d.ms)}</span><span><span ${kit.cls(d.color, "who cls")}>${kit.esc(d.name)}</span>`
        + `<span class="by">${kit.iconImg(D.img_base, d.icon)}${kit.esc(d.by)} · ${d.alive} alive</span></span></button></li>`;
    }).join("");
    return `<div class="deaths"><h2>Deaths (${D.deaths.length})</h2><ol>${rows || '<li class="cut">none</li>'}</ol></div>`;
  }

  // Phase bands: the first phase quiet, a phase the fight went back to gold, the others blue.
  function phaseBands() {
    const seen = new Set();
    return D.phases.map((p, i) => {
      const end = i + 1 < D.phases.length ? D.phases[i + 1].ms : D.duration_ms;
      const again = seen.has(p.id); seen.add(p.id);
      return { from: p.ms, to: end, label: `${p.label || `P${p.id}`}${again ? " (2nd time)" : ""}`, name: p.name,
               fill: again ? "var(--gold)" : i === 0 ? "var(--line)" : "var(--p2)", opacity: again ? 0.55 : i === 0 ? 1 : 0.6 };
    });
  }

  const { labels, niceMax } = kit;
  const PEAK_BEFORE_MS = 10000;  // a healer cooldown this long before a damage peak is named with it
  // a cooldown's mark in the card's one row of them: the raid's a dot, a healer's a plus
  const RAID_MARK = (cx, cy) => `<circle cx="${cx}" cy="${cy}" r="3.2" fill="var(--blue)"/>`;
  const HEALER_MARK = (cx, cy) => `<path d="M${cx - 4} ${cy}h8M${cx} ${cy - 4}v8" stroke="var(--green)" stroke-width="2.4" fill="none"/>`;

  function chart({ W, L, R, plotH, lanes }) {
    // raid damage taken per second, on the left axis, whose top label the left margin makes room for
    const per = D.damage.per_s, step = D.damage.step_ms, peaks = per.length ? D.damage.peaks || [] : [];
    const max = per.length ? niceMax(Math.max(...per.map((q) => q[1]))) : 1, most = `${kit.short(max)}/s`;
    L = Math.max(L, Math.ceil(kit.width(most)) + 8);
    const x = (ms) => L + ms / D.duration_ms * (W - L - R);
    let y = 4, o = "";
    const label = (s, yy) => lanes ? kit.text(6, yy, s) : "";
    // phases
    if (D.phases.length > 1 || lanes) {
      for (const b of phaseBands()) {
        o += `<rect x="${x(b.from)}" y="${y}" width="${Math.max(1, x(b.to) - x(b.from) - 1)}" height="13" rx="3" fill="${b.fill}" opacity="${b.opacity}"/>`;
        const room = x(b.to) - x(b.from) - 8;
        const s = [lanes && b.name ? `${b.label} ${b.name}` : null, b.label, b.label.split(" ")[0]]
          .find((t) => t && kit.width(t) < room);
        if (s) o += kit.text(x(b.from) + 5, y + 10, s, "t", 'font-weight="600"');
      }
      o += label("Phase", y + 10);
      y += 20;
    }
    const tracked = D.casts.filter((c) => c.tracked);
    const cooldowns = [...D.cooldowns.filter((c) => c.kind === "raid" || c.kind === "healing"),
                       ...D.lusts.map((ms) => ({ ms, kind: "raid", name: "Bloodlust", player: "" }))].sort((a, b) => a.ms - b.ms);
    if (lanes) {
      o += label("Boss casts", y + 12);
      for (const c of D.casts) {
        if (c.tracked) continue;
        o += `<rect x="${x(c.ms) - 1}" y="${y + 5}" width="2" height="9" fill="var(--muted)" opacity=".7"/>`
          + kit.hit(x(c.ms) - 4, y + 2, 8, 15, `${c.name} ${kit.clock(c.ms)}`);
      }
      for (const c of tracked) {
        const tip = `${c.name} ${kit.clock(c.ms)}, one of the boss's main casts`;
        o += kit.icon(D.img_base, c.icon, x(c.ms) - 7, y + 2, 14, tip)
          || `<path d="M${x(c.ms)} ${y + 3} l5 6 l-5 6 l-5 -6z" fill="var(--gold)"${kit.tip(tip)}/>`;
      }
      o += labels(tracked.map((c) => [c.ms, c.name]), x, y + 13, "tg", D.casts.map((c) => x(c.ms)), W, kit.SIZE.label, 9);
      y += 22;
      for (const [kind, title, color] of [["raid", "Raid cooldowns", "var(--blue)"], ["healing", "Healer cooldowns", "var(--green)"]]) {
        const list = D.cooldowns.filter((c) => c.kind === kind).map((c) => [c.ms, c.name, c.player]);
        if (kind === "raid") for (const ms of D.lusts) list.push([ms, "Bloodlust", ""]);
        list.sort((a, b) => a[0] - b[0]);
        o += label(title, y + 12);
        for (const [ms, name, who] of list) {
          o += `<circle cx="${x(ms)}" cy="${y + 9}" r="3.2" fill="${color}"/>`
            + `<circle cx="${x(ms)}" cy="${y + 9}" r="7" fill="transparent"${kit.tip(`${name}${who ? ` (${who})` : ""} ${kit.clock(ms)}`)}/>`;
        }
        o += labels(list.map(([ms, name]) => [ms, name]), x, y + 13, "t", list.map(([ms]) => x(ms)), W);
        y += 20;
      }
      y += 4;
    } else {
      if (tracked.length) {
        for (const c of tracked) o += `<path d="M${x(c.ms)} ${y} l4 5 l-4 5 l-4 -5z" fill="var(--gold)"${kit.tip(`${c.name} ${kit.clock(c.ms)}`)}/>`;
        y += 14;
      }
      // the cooldowns in one row: a dot for the raid's, a plus for a healer's (shape as well as colour)
      for (const c of cooldowns) {
        o += (c.kind === "raid" ? RAID_MARK(x(c.ms), y + 5) : HEALER_MARK(x(c.ms), y + 5))
          + kit.hit(x(c.ms) - 5, y - 1, 10, 12, `${c.name}${c.player ? ` (${c.player})` : ""} ${kit.clock(c.ms)}`);
      }
      if (cooldowns.length) y += 13;
    }
    const top = y, bottom = y + plotH;
    const tick = [5, 10, 15, 30, 60, 120, 300].map((s) => s * 1000).find((s) => D.duration_ms / s <= (lanes ? 12 : 8)) || 600000;
    for (let t = 0; t <= D.duration_ms; t += tick) o += kit.line(x(t), top, x(t), bottom, "var(--line)");
    o += kit.line(L, bottom, W - R, bottom, "var(--line)");
    // What is written inside the plot keeps clear of what was written before it: [x0, x1, y0, y1] each
    const boxes = [];
    const free = (x0, x1, y0, y1) => !boxes.some((q) => x0 < q[1] && q[0] < x1 && y0 < q[3] && q[2] < y1);
    const dx = D.decided ? x(D.decided.ms) : null;
    const yD = (v) => bottom - v / max * plotH;
    if (per.length) {
      // the peaks (twice the pull's median or more): a band behind, the healer cooldowns near it in its tooltip
      for (const pk of peaks) {
        const near = D.cooldowns.filter((c) => c.kind === "healing" && c.ms >= pk.from_ms - PEAK_BEFORE_MS && c.ms <= pk.to_ms);
        const tip = `Raid damage peak ${kit.clock(pk.from_ms)}-${kit.clock(pk.to_ms)}: up to ${kit.short(pk.per_s)}/s, twice the pull's median or more`
          + (near.length ? `\nHealer cooldowns from ${PEAK_BEFORE_MS / 1000} s before: ${near.map((c) => `${c.name}${c.player ? ` (${c.player})` : ""} ${kit.clock(c.ms)}`).join(", ")}`
            : `\nNo healer cooldown from ${PEAK_BEFORE_MS / 1000} s before`);
        o += `<rect x="${x(pk.from_ms)}" y="${top}" width="${Math.max(2, x(pk.to_ms) - x(pk.from_ms))}" height="${plotH}" fill="var(--dead)" opacity=".09"${kit.tip(tip)}/>`;
      }
      const pts = per.map(([ms, v]) => [Math.min(ms + step / 2, D.duration_ms), v]);
      o += `<path d="M${x(pts[0][0])} ${bottom} ${pts.map(([ms, v]) => `L${x(ms)} ${yD(v)}`).join(" ")} L${x(pts[pts.length - 1][0])} ${bottom}Z" fill="var(--blue)" opacity=".18"/>`;
      o += `<path d="${pts.map(([ms, v], i) => `${i ? "L" : "M"}${x(ms)} ${yD(v)}`).join(" ")}" fill="none" stroke="var(--blue)" stroke-width="1.2" opacity=".8"/>`;
      o += kit.text(L - 6, top + 8, most, "tm", 'text-anchor="end"');
      o += kit.text(L - 6, bottom, "0", "tm", 'text-anchor="end"');
    }
    // boss health, on the right axis: 0 to 100% on every pull, so the lines of two pulls compare, the axis and the
    // line's end named in the line's colour
    const yH = (pct) => top + (100 - pct) / 100 * plotH;
    if (D.health.length) {
      // a new health bar (the next boss of a phase) starts a new line instead of a jump up
      const d = D.health.map(([ms, pct], i) => `${i && pct - D.health[i - 1][1] <= 10 ? "L" : "M"}${x(ms)} ${yH(pct)}`).join(" ");
      o += `<path d="${d}" fill="none" stroke="var(--gold)" stroke-width="2.4" stroke-linejoin="round"/>`;
      const [lastMs, lastPct] = D.health[D.health.length - 1], endY = yH(lastPct);
      o += `<circle cx="${x(lastMs)}" cy="${endY}" r="3.5" fill="var(--gold)"/>`;
      o += kit.text(W - R + 5, endY + 4, `${D.kill ? 0 : (D.boss_pct ?? lastPct)}%`, "tg", 'font-weight="600"', kit.SIZE.value);
      if (endY - 7 > top + 10) o += kit.text(W - R + 5, top + 8, "100%", "tg");
      if (endY + 5 < bottom - 11) o += kit.text(W - R + 5, bottom, "0%", "tg");
    }
    // the health line's highest and lowest y between two x of the plot; null where it isn't drawn
    const healthBetween = (x0, x1) => {
      const at = (px) => {
        for (let i = 1; i < D.health.length; i++) {
          const [a, pa] = D.health[i - 1], [b, pb] = D.health[i], xa = x(a), xb = x(b);
          if (px >= xa && px <= xb) return yH(pa + (pb - pa) * (xb > xa ? (px - xa) / (xb - xa) : 0));
        }
        return null;
      };
      const ys = [at(x0), at(x1), ...D.health.filter(([ms]) => x(ms) > x0 && x(ms) < x1).map(([, pct]) => yH(pct))].filter((v) => v != null);
      return ys.length ? [Math.min(...ys), Math.max(...ys)] : null;
    };
    // when the wipe was decided: the dashed line, and on its side with more room the time and, on a card wide enough
    // for it, why (the headline over the chart says it too); over the health line where there's room, else under it
    if (D.decided) {
      const right = dx > (W - R) * 0.6, rows = [[`decided ${kit.clock(D.decided.ms)}`, kit.SIZE.value, 'font-weight="600"']];
      if (W >= 500) rows.push([D.decided.why, kit.SIZE.label, ""]);
      o += kit.line(dx, top - 4, dx, bottom + 28, "var(--decided)", 'stroke-width="1.6" stroke-dasharray="4 3"');
      const w = Math.max(...rows.map(([text, size]) => kit.width(text, size))), x0 = right ? dx - 5 - w : dx + 5;
      const line = healthBetween(x0, x0 + w), tall = (rows.length - 1) * 13;
      let ty = top + 12;
      if (line && ty + tall + 4 > line[0] - 3) ty = Math.min(line[1] + 15, bottom - tall - 4);
      rows.forEach(([text, size, attrs], i) => {
        o += kit.text(right ? dx - 5 : dx + 5, ty + i * 13, text, "to", `${right ? 'text-anchor="end"' : ""} ${attrs}`, size);
      });
      boxes.push([x0 - 3, x0 + w + 3, ty - 11, ty + tall + 4]);
    }
    // each peak's height: over the peak, or beside the line the wipe was decided at where it would sit on it, a row
    // lower where that is taken or the health line runs through; a peak with no room for it keeps its tooltip
    for (const pk of peaks) {
      const text = kit.short(pk.per_s), w = kit.width(text), cx = (x(pk.from_ms) + x(pk.to_ms)) / 2;
      const py = Math.max(top + 9, yD(pk.per_s) - 5), tries = [[cx - w / 2, py]];
      if (dx != null && Math.abs(cx - dx) < w / 2 + 6) for (const row of [0, 1, 2, 3]) tries.push([dx - 5 - w, py + 13 * row], [dx + 5, py + 13 * row]);
      const clear = (lx, ly) => { const line = healthBetween(lx, lx + w); return !line || line[1] < ly - 12 || line[0] > ly + 6; };
      const at = tries.find(([lx, ly]) => lx >= L && lx + w <= W - R - 2 && ly < bottom - 2 && (dx == null || lx > dx + 2 || lx + w < dx - 2)
        && free(lx - 2, lx + w + 2, ly - 9, ly + 3) && clear(lx, ly));
      if (!at) continue;
      o += kit.text(at[0], at[1], text, "td", 'font-weight="600" pointer-events="none"');
      boxes.push([at[0] - 2, at[0] + w + 2, at[1] - 9, at[1] + 3]);
    }
    // deaths, those whose marks would touch as one; after the wipe was decided grey
    const dy = bottom + 16, groups = [];
    for (const d of D.deaths) {
      const g = groups[groups.length - 1];
      if (g && x(d.ms) - x(g[0].ms) < 14 && g[0].fallout === d.fallout) g.push(d); else groups.push([d]);
    }
    o += label("Deaths", dy + 4);
    let lastEdge = -Infinity, killers = false;
    for (const ds of groups) {  // several at once: how many, in the mark; a mark right after another moves over
      const d = ds[0];
      const tip = (d.fallout ? "After the wipe was decided:\n" : "") + ds.map((e) => `${kit.clock(e.ms)} ${e.name}: ${e.by}`).join("\n");
      // a death that counted shows its killer's icon, ringed red: one killer for the mark, else a dot
      if (!d.fallout && D.img_base && d.icon && ds.every((e) => e.icon === d.icon)) {
        const s = 16, cx = Math.max(x(d.ms), lastEdge + s / 2 + 1);
        lastEdge = cx + s / 2; killers = true;
        o += kit.icon(D.img_base, d.icon, cx - s / 2, dy - s / 2, s, tip)
          + `<rect x="${cx - s / 2 - 1}" y="${dy - s / 2 - 1}" width="${s + 2}" height="${s + 2}" rx="3" fill="none" stroke="var(--dead)" stroke-width="1.8" pointer-events="none"/>`;
        if (ds.length > 1) o += `<circle cx="${cx + s / 2}" cy="${dy - s / 2}" r="6" fill="var(--dead)" pointer-events="none"/>`
          + kit.text(cx + s / 2, dy - s / 2 + 3.5, ds.length, "tb", 'text-anchor="middle" font-weight="700" pointer-events="none"', kit.SIZE.mark);
        continue;
      }
      const r = ds.length > 1 ? 7 : 4.2, cx = Math.max(x(d.ms), lastEdge + r + 1);
      lastEdge = cx + r;
      // (a mark with a number in it is drawn whole, so the number reads; a lone death after the wipe was decided is faint)
      o += `<circle cx="${cx}" cy="${dy}" r="${r}" fill="${d.fallout ? "var(--muted)" : "var(--dead)"}" opacity="${d.fallout && ds.length === 1 ? 0.65 : 1}"${kit.tip(tip)}/>`;
      if (ds.length > 1) o += kit.text(cx, dy + 3.5, ds.length, "tb", 'text-anchor="middle" font-weight="700"', kit.SIZE.mark);
    }
    const ay = bottom + 40;
    for (let t = 0; t <= D.duration_ms; t += tick) o += kit.text(x(t), ay, kit.clock(t), "tm", 'text-anchor="middle"');
    // the legend, under the time axis, wrapping on a narrow chart
    const keys = [[(lx, ly) => `<rect x="${lx}" y="${ly - 6}" width="12" height="3" fill="var(--gold)"/>`, "boss health"],
      [(lx, ly) => `<rect x="${lx + 1}" y="${ly - 8}" width="10" height="8" fill="var(--blue)" opacity=".35"/>`, "raid damage taken /s"],
      killers ? [(lx, ly) => `<rect x="${lx}" y="${ly - 9}" width="10" height="10" rx="2" fill="none" stroke="var(--dead)" stroke-width="1.8"/>`, "death, by its killer"]
        : [(lx, ly) => `<circle cx="${lx + 5}" cy="${ly - 4}" r="4" fill="var(--dead)"/>`, "death"]];
    if (tracked.length) keys.splice(2, 0, [(lx, ly) => `<path d="M${lx + 5} ${ly - 9} l5 5 l-5 5 l-5 -5z" fill="var(--gold)"/>`, "the boss's main casts"]);
    if (!lanes && cooldowns.some((c) => c.kind === "raid")) keys.push([(lx, ly) => RAID_MARK(lx + 5, ly - 4), "raid cooldown"]);
    if (!lanes && cooldowns.some((c) => c.kind === "healing")) keys.push([(lx, ly) => HEALER_MARK(lx + 5, ly - 4), "healer cooldown"]);
    if (D.decided) keys.push([(lx, ly) => `<circle cx="${lx + 5}" cy="${ly - 4}" r="4" fill="var(--muted)" opacity=".6"/>`, "after it was decided"]);
    const [under, ly] = kit.legend(keys, W, ay + 20);
    o += under;
    return kit.svg(W, ly + 6, o, `${D.title}: boss health and raid damage taken over time, phases, deaths`);
  }

  return { show, relayout: render, seen: () => D.title };
})();
