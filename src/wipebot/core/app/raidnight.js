// ---- the raid's night on a boss: each pull's boss health left, coloured by what ended it, against the night's best
// so far and the guild's best before the night; how the pulls ended; the night's other bosses (progress's facts).
// Two panels (data-panel), each its own picture in Discord; a pull's bar opens that pull's view ----
views.night = (() => {
  let D = null, root = null, open = null, drawnWidth = 0;
  const COLORS = ["#b98cf5", "#e07a5f", "#5fb3d4", "#7fb069", "#c9b458", "#d48ac7"];  // the endings, most pulls first
  const QUIET = new Set(["a reset", "a kill", "still going", "called off"]);

  async function show(into, key, opener) {
    root = into; open = opener;
    D = await app.callTool("view_data", { view: "night", report: key.report, boss: key.boss });
    render();
    new ResizeObserver(() => { if (root === into && Math.abs(into.clientWidth - drawnWidth) > 24) render(); }).observe(into);
  }

  // Each way pulls ended keeps a colour, the most common first; a reset is grey and a kill gold
  function colorOf() {
    const named = D.ends.map(([what]) => what).filter((w) => !QUIET.has(w));
    const m = new Map(named.slice(0, COLORS.length).map((w, i) => [w, COLORS[i]]));
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
  function chart(W, lanes) {
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
    // the legend: the endings by how many pulls, then the lines
    const keys = D.ends.filter(([w]) => w !== "still going").slice(0, 6).map(([w, k]) =>
      [(lx, ly) => w === "a kill" ? `<path d="M${lx + 5} ${ly - 10} l4 8 l-8 0z" fill="var(--gold)"/>` : `<rect x="${lx}" y="${ly - 9}" width="10" height="10" rx="2" fill="${color(w)}"/>`,
       `${w.replace(/ killing many$/, "")} ${k}`]);
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

  // How the pulls ended, and what killed each pull's first deaths: two ranked lists
  function endings() {
    const color = colorOf(), counted = D.pulls.filter((r) => !r.live).length;
    const max = Math.max(1, ...D.ends.map(([, k]) => k), ...D.firsts.map(([, k]) => k));
    const bar = (k, c) => `<span class="bar"><span style="width:${Math.round(100 * k / max)}%;background:${c}"></span></span>`;
    const ends = D.ends.map(([w, k]) => `<li><span>${kit.esc(w)}</span>${bar(k, color(w))}<span class="count">${k}</span></li>`).join("");
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
    const panels = `<div data-panel="pulls">${head()}${stats()}${chart(width, full)}</div><div data-panel="endings">${endings()}</div>`;
    if (picture) { root.innerHTML = panels; return; }
    root.innerHTML = (full ? bosses() : "") + panels + (full ? table()
      : `<div class="actions">${app.canGoFullscreen() ? '<button class="primary" id="rn-open">Open night</button>' : ""}</div>`);
    const o = root.querySelector("#rn-open");
    if (o) o.onclick = () => app.requestDisplayMode("fullscreen").then(render);
    const pull = (n) => open("pull", { report: D.report, pull: n, boss: D.boss });
    root.querySelectorAll("[data-pull-col]").forEach((el) => el.addEventListener("click", () => pull(+el.dataset.pullCol)));
    root.querySelectorAll("tr[data-pull]").forEach((tr) => tr.onclick = () => pull(+tr.dataset.pull));
    root.querySelectorAll("[data-boss]").forEach((b) => b.onclick = () => open("night", { report: D.report, boss: b.dataset.boss }));
  }

  return { show, relayout: render };
})();
