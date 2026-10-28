// ---- the raid's night on a boss: each pull's boss health left, coloured by what ended it, against the night's best
// so far and the guild's best before the night; how the pulls ended; the night's other bosses (progress's facts);
// with progress's weeks, the best pull of each raid night first.
// Two panels (data-panel), each its own picture in Discord; a pull's bar opens that pull's view ----
views.night = (() => {
  let D = null, root = null, open = null, drawnWidth = 0;
  const COLORS = ["#b98cf5", "#e07a5f", "#5fb3d4", "#7fb069", "#c9b458", "#d48ac7"];  // the endings, most pulls first
  const QUIET = new Set(["a reset", "a kill", "still going", "called off"]);

  async function show(into, key, opener) {
    root = into; open = opener;
    D = await app.callTool("view_data", { view: "night", report: key.report, boss: key.boss, ...(key.weeks ? { weeks: key.weeks } : {}) });
    render();
    new ResizeObserver(() => { if (root === into && Math.abs(into.clientWidth - drawnWidth) > 24) render(); }).observe(into);
  }

  // The ways most pulls ended keep a colour each, the most common first, and the rest share grey ("other": more hues
  // than that can't be told apart); a reset is faint and a kill gold
  const HUES = 4;
  const named = () => D.ends.map(([what]) => what).filter((w) => !QUIET.has(w));
  function colorOf() {
    const m = new Map(named().slice(0, HUES).map((w, i) => [w, COLORS[i]]));
    return (w) => w === "a kill" ? "var(--gold)" : w === "a reset" || w === "called off" ? "var(--line)" : m.get(w) || "var(--muted)";
  }

  const pct = (v) => `${v.toFixed(v < 10 ? 1 : 0)}%`;

  function stats() {
    const wipes = D.pulls.filter((r) => !r.kill && !r.reset && !r.live && r.pct != null);
    const best = wipes.reduce((b, r) => (!b || r.pct < b.pct ? r : b), null), kill = D.pulls.find((r) => r.kill);
    const top = D.ends.find(([w]) => !QUIET.has(w)), counted = D.pulls.filter((r) => !r.live && !r.reset).length;
    const g = D.guild_best, beat = g && best && best.pct < g.pct;
    return kit.stats([
      kill ? ["Killed", `pull ${kill.pull}`, "good", `after ${D.pulls.length} pulls`]
        : best ? ["Best wipe", pct(best.pct), "", `pull ${best.pull}`] : null,
      g ? ["Guild best before", pct(g.pct), "", beat ? "beaten tonight" : g.day] : null,
      top ? ["Ended most pulls", top[0].replace(/ killing many$/, ""), "decided", `${top[1]} of ${counted}${/killing many$/.test(top[0]) ? ", killing many" : ""}`] : null,
      D.thirds ? ["Average wipe", D.thirds.map((t) => `${Math.round(t)}%`).join(" → "), "", "first, middle, last third"] : null,
    ]);
  }

  function bosses() {
    if (D.bosses.length < 2) return "";
    return `<div class="chips bosses">${D.bosses.map((b) => `<button class="chip${b.boss === D.boss ? " saved" : ""}" data-boss="${kit.esc(b.boss)}">`
      + `${kit.esc(b.boss)} · ${b.kill ? "kill" : b.best != null ? pct(b.best) : `${b.pulls} pulls`}</button>`).join("")}</div>`;
  }

  function head() {
    const sub = [D.difficulty, `${D.pulls.length} pulls`, `${Math.round(D.combat_ms / 60000)} min in combat`, D.title.split(", ").pop()].filter(Boolean).join(" · ");
    return `<div class="head"><h1>${kit.esc(D.boss)}</h1><span class="sub">${kit.esc(sub)}</span></div>`;
  }

  // Pull by pull: boss health left (lower is better) in the colour of what ended the pull, the night's best so far
  // (a line), the guild's best before the night (dashed)
  function chart(W, lanes, legend) {
    const color = colorOf(), n = D.pulls.length, L = 40, R = 16, T = 12, plotH = lanes ? 200 : 130;
    const band = (W - L - R) / Math.max(n, 1), x = (i) => L + i * band, bw = Math.max(2, Math.min(20, band * 0.72));
    const y = (v) => T + plotH - v / 100 * plotH;
    let o = "";
    for (const v of [0, 50, 100]) o += kit.line(L, y(v), W - R, y(v), "var(--line)", v ? 'opacity=".5"' : "") + kit.text(L - 6, y(v) + 4, `${v}%`, "tm", 'text-anchor="end" font-size="10"');
    let best = null;
    const line = [];
    D.pulls.forEach((r, i) => {
      const cx = x(i) + band / 2, v = r.kill ? 0 : r.pct ?? 0;
      const tip = `Pull ${r.pull}: ${r.kill ? "kill" : r.reset ? "a reset" : r.live ? "still going" : `${pct(v)} left`}${r.phase ? ` (${r.phase})` : ""}, ${kit.clock(r.duration_ms)}`
        + `\nEnded by: ${r.ended}` + (r.first ? `\nFirst death: ${r.first.name} (${r.first.by}, ${kit.clock(r.first.ms)})` : "") + "\nClick for this pull";
      if (r.kill) o += `<path d="M${cx} ${y(0) - 12} l4 8 l-8 0z" fill="var(--gold)"/>`;
      else o += `<rect x="${cx - bw / 2}" y="${y(v)}" width="${bw}" height="${Math.max(1, y(0) - y(v))}" rx="2" fill="${color(r.ended)}"${r.reset ? ' opacity=".6"' : ""}/>`;
      if (!r.reset && !r.live && r.pct != null) best = best == null ? v : Math.min(best, v);
      if (best != null) line.push([cx, y(best)]);
      o += `<rect x="${x(i)}" y="${T}" width="${band}" height="${plotH + 22}" fill="transparent" data-pull-col="${r.pull}" style="cursor:pointer"${kit.tip(tip)}/>`;
    });
    if (line.length > 1) o += `<path d="${line.map(([px, py], i) => `${i ? "L" : "M"}${px} ${py}`).join(" ")}" fill="none" stroke="var(--ink)" stroke-width="1.6" pointer-events="none"/>`;
    // the night's best wipe, named: a bar near 0% is too short to see
    const top = D.pulls.reduce((b, r, i) => (!r.kill && !r.reset && !r.live && r.pct != null && (b < 0 || r.pct < D.pulls[b].pct) ? i : b), -1);
    if (top >= 0 && !D.pulls.some((r) => r.kill)) o += kit.text(x(top) + band / 2, y(D.pulls[top].pct) - 6, pct(D.pulls[top].pct), "t", 'text-anchor="middle" font-size="10.5" font-weight="700"');
    if (D.guild_best) {
      const gy = y(D.guild_best.pct);
      o += kit.line(L, gy, W - R, gy, "var(--gold)", 'stroke-width="1.4" stroke-dasharray="5 4" pointer-events="none"');
      o += kit.text(W - R, gy - 5, `guild best before: ${pct(D.guild_best.pct)}`, "tg", 'text-anchor="end" font-size="10" font-weight="600"');
    }
    const every = Math.max(1, Math.ceil(24 / band));
    D.pulls.forEach((r, i) => { if (i % every === 0 || i === n - 1) o += kit.text(x(i) + band / 2, T + plotH + 14, r.pull, "tm", 'text-anchor="middle" font-size="10"'); });
    // the legend, only where the list of how the pulls ended isn't drawn under it (that list is the legend): the
    // coloured endings by how many pulls, the rest as one "other", then the line
    const hued = new Set(named().slice(0, HUES)), other = D.ends.filter(([w]) => !QUIET.has(w) && !hued.has(w)).reduce((n, [, k]) => n + k, 0);
    const keys = !legend ? [] : D.ends.filter(([w]) => w !== "still going" && (QUIET.has(w) || hued.has(w))).map(([w, k]) =>
      [(lx, ly) => w === "a kill" ? `<path d="M${lx + 5} ${ly - 10} l4 8 l-8 0z" fill="var(--gold)"/>` : `<rect x="${lx}" y="${ly - 9}" width="10" height="10" rx="2" fill="${color(w)}"/>`,
       `${w.replace(/ killing many$/, "")} ${k}`]);
    if (legend && other) keys.push([(lx, ly) => `<rect x="${lx}" y="${ly - 9}" width="10" height="10" rx="2" fill="var(--muted)"/>`, `other ${other}`]);
    keys.push([(lx, ly) => `<line x1="${lx}" y1="${ly - 4}" x2="${lx + 12}" y2="${ly - 4}" stroke="var(--ink)" stroke-width="1.6"/>`, "best so far"]);
    let lx = 6, ly = T + plotH + 34;
    for (const [mark, name] of keys) {
      const w = 18 + kit.width(name, 10) + 14;
      if (lx > 6 && lx + w > W) { lx = 6; ly += 17; }
      o += mark(lx, ly) + kit.text(lx + 17, ly, name, "tm", 'font-size="10"');
      lx += w;
    }
    return kit.svg(W, ly + 6, o, `${D.boss}: boss health left on each pull, coloured by what ended it`);
  }

  // Night by night (progress's weeks): each raid night's best pull, as boss health left (lower is better), in a
  // blue that deepens with the phase it got to, named on the bar as raiders say it ("P2 68%"); a kill is gold
  const phaseNo = (p) => !p ? 1 : p[0] === "P" ? parseInt(p.slice(1), 10) || 1 : 0;  // an intermission: 0
  function phaseColor(p, top) {
    const n = phaseNo(p);
    return n ? `color-mix(in srgb, var(--blue) ${Math.round(35 + 65 * (top > 1 ? (n - 1) / (top - 1) : 1))}%, var(--line))` : "var(--muted)";
  }
  const best = (r) => r.kill ? "kill" : r.pct == null ? "–" : r.phase ? `${r.phase.split(" ")[0]} ${pct(r.health ?? r.pct)}` : pct(r.pct);

  function weeksStats() {
    const W = D.weeks, kill = W.find((r) => r.kill), first = W[0], last = W[W.length - 1];
    const pulls = W.reduce((k, r) => k + r.pulls, 0), before = kill ? W.slice(0, W.indexOf(kill) + 1) : W;
    return kit.stats([
      ["Nights", `${W.length}`, "", W.length > 1 ? `${first.day} – ${last.day}` : first.day],
      kill ? ["Killed", kill.day, "good", `pull ${kill.kill} that night, ${before.reduce((k, r) => k + r.pulls, 0)} pulls in all`]
        : ["Pulls", `${pulls}`, "", "over these nights"],
      W.length > 1 ? ["Best pull, first night → last", `${best(first)} → ${best(last)}`, "", W.some((r) => r.phase) ? "the phase and boss health in it" : "boss health left"] : null,
    ]);
  }

  function weeksChart(W0) {
    const W = D.weeks, n = W.length, L = 40, R = 16, T = 18, plotH = 150, phased = W.some((r) => r.health != null);
    const top = Math.max(1, ...W.map((r) => phaseNo(r.phase)));
    const band = (W0 - L - R) / Math.max(n, 1), x = (i) => L + i * band, bw = Math.max(6, Math.min(44, band * 0.6));
    const y = (v) => T + plotH - v / 100 * plotH;
    let o = "";
    for (const v of [0, 50, 100]) o += kit.line(L, y(v), W0 - R, y(v), "var(--line)", v ? 'opacity=".5"' : "") + kit.text(L - 6, y(v) + 4, `${v}%`, "tm", 'text-anchor="end" font-size="10"');
    W.forEach((r, i) => {
      const cx = x(i) + band / 2, v = r.kill ? 0 : r.pct ?? 0;
      const tip = `${r.day}: ${r.pulls} pulls, ${r.kill ? `killed on pull ${r.kill}` : r.pct == null ? "no wipe past a reset"
        : `best wipe ${r.health != null ? `${r.phase} at ${pct(r.health)} (WCL ${pct(r.pct)})` : `${pct(r.pct)}${r.phase ? ` (${r.phase})` : ""}`} on pull ${r.pull}`}`
        + (r.ended ? `\nEnded most pulls: ${r.ended[0]} (${r.ended[1]})` : "") + "\nClick for that night";
      if (r.kill) o += `<path d="M${cx} ${y(0) - 14} l5 10 l-10 0z" fill="var(--gold)"/>`;
      else if (r.pct != null) o += `<rect x="${cx - bw / 2}" y="${y(v)}" width="${bw}" height="${Math.max(1, y(0) - y(v))}" rx="3" fill="${phaseColor(r.phase, top)}"/>`;
      o += kit.text(cx, (r.kill ? y(0) - 14 : y(v)) - 5, best(r), r.kill ? "tg" : "t", 'text-anchor="middle" font-size="10.5" font-weight="700"');
      o += `<rect x="${x(i)}" y="0" width="${band}" height="${plotH + T + 34}" fill="transparent" data-night="${kit.esc(r.report)}" style="cursor:pointer"${kit.tip(tip)}/>`;
    });
    const every = Math.max(1, Math.ceil(70 / band));
    W.forEach((r, i) => {
      if (i % every && i !== n - 1) return;
      o += kit.text(x(i) + band / 2, T + plotH + 14, r.day, "tm", 'text-anchor="middle" font-size="10"');
      o += kit.text(x(i) + band / 2, T + plotH + 27, `${r.pulls} pulls`, "tm", 'text-anchor="middle" font-size="9.5"');
    });
    let ly = T + plotH + 46;
    if (top > 1) {  // the phases' blues
      let lx = 6;
      for (let k = 1; k <= top; k++) {
        o += `<rect x="${lx}" y="${ly - 9}" width="10" height="10" rx="2" fill="${phaseColor(`P${k}`, top)}"/>` + kit.text(lx + 14, ly, `P${k}`, "tm", 'font-size="10"');
        lx += 44;
      }
      if (phased) o += kit.text(lx, ly, "bar height: WCL % of the whole fight left", "tm", 'font-size="10"');
      ly += 6;
    }
    return kit.svg(W0, ly + 4, o, `${D.boss}: the best pull on each raid night`);
  }

  function weeks(width) {
    const sub = [D.difficulty, `best pull night by night, last ${D.weeks.length === 1 ? "night" : `${D.weeks.length} nights`}`].filter(Boolean).join(" · ");
    return `<div data-panel="weeks"><div class="head"><h1>${kit.esc(D.boss)}</h1><span class="sub">${kit.esc(sub)}</span></div>`
      + `${weeksStats()}${weeksChart(width)}</div>`;
  }

  // How the pulls ended, and what killed each pull's first deaths: two ranked lists
  function endings() {
    const color = colorOf(), counted = D.pulls.filter((r) => !r.live).length;
    const max = Math.max(1, ...D.ends.map(([, k]) => k), ...D.firsts.map(([, k]) => k));
    const bar = (k, c) => `<span class="bar"><span style="width:${Math.round(100 * k / max)}%;background:${c}"></span></span>`;
    const swatch = (w) => w === "a kill" ? '<i class="sw" style="background:var(--gold)"></i>' : `<i class="sw" style="background:${color(w)}"></i>`;
    const ends = D.ends.map(([w, k]) => `<li><span>${swatch(w)}${kit.esc(w)}</span>${bar(k, color(w))}<span class="count">${k}</span></li>`).join("");
    const firsts = D.firsts.map(([w, k]) => `<li><span>${kit.esc(w)}</span>${bar(k, "var(--dead)")}<span class="count">${k}</span></li>`).join("");
    return `<div class="section-title">How the ${counted} pulls ended</div><ul class="ranked">${ends}</ul>`
      + `<div class="section-title">What killed the first ${D.first_n} deaths of each pull</div><ul class="ranked">${firsts}</ul>`;
  }

  function table() {
    const color = colorOf();
    const rows = D.pulls.map((r) => `<tr data-pull="${r.pull}"><td>${r.pull}</td><td>${r.kill ? "kill" : r.reset ? "reset" : r.pct != null ? pct(r.pct) : "–"}${r.best ? " ★" : ""}</td>`
      + `<td>${r.phase || ""}</td><td>${kit.clock(r.duration_ms)}</td><td><span class="dot" style="background:${color(r.ended)}"></span>${kit.esc(r.ended)}</td>`
      + `<td>${r.first ? `${kit.esc(r.first.name)} (${kit.esc(r.first.by)}, ${kit.clock(r.first.ms)})` : ""}</td></tr>`).join("");
    return `<div class="section-title">Pull by pull (click one for its view; ★ best of the night until then)</div><div class="scroll-x"><table class="pulls"><thead><tr>`
      + `<th>Pull</th><th>Boss</th><th>Phase</th><th>Length</th><th>Ended by</th><th>First death</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function render() {
    if (!D || !root) return;
    const full = app.context().displayMode === "fullscreen", picture = app.picture();
    drawnWidth = root.clientWidth;
    const width = Math.max(300, drawnWidth || 640);
    // Asked night by night, those come first; a picture then leaves out how tonight's pulls ended, and the chart has
    // its own legend. A picture of two nights' best pulls says no more than the answer's text: from three
    const nights = D.weeks && D.weeks.length >= (picture ? 3 : 1) ? weeks(width) : "";
    const listed = !(nights && picture);
    const tonight = `<div data-panel="pulls">${head()}${stats()}${chart(width, full, !listed)}</div>`;
    const panels = nights + tonight + (listed ? `<div data-panel="endings">${endings()}</div>` : "");
    if (picture) { root.innerHTML = panels; return; }
    root.innerHTML = (full ? bosses() : "") + panels + (full ? table()
      : `<div class="actions">${app.canGoFullscreen() ? '<button class="primary" id="rn-open">Open night</button>' : ""}</div>`);
    const o = root.querySelector("#rn-open");
    if (o) o.onclick = () => app.requestDisplayMode("fullscreen").then(render);
    const pull = (n) => open("pull", { report: D.report, pull: n, boss: D.boss });
    root.querySelectorAll("[data-pull-col]").forEach((el) => el.addEventListener("click", () => pull(+el.dataset.pullCol)));
    root.querySelectorAll("tr[data-pull]").forEach((tr) => tr.onclick = () => pull(+tr.dataset.pull));
    root.querySelectorAll("[data-boss]").forEach((b) => b.onclick = () => open("night", { report: D.report, boss: b.dataset.boss }));
    root.querySelectorAll("[data-night]").forEach((el) => el.addEventListener("click", () => {
      if (el.dataset.night !== D.report) open("night", { report: el.dataset.night, boss: D.boss });
    }));
  }

  return { show, relayout: render };
})();
