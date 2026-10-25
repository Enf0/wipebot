// ---- the player's night on a boss: their output and death pull by pull, the trend in thirds, their cooldowns
// summed; a pull's column opens that pull's player view ----
views.player_night = (() => {
  let D = null, root = null, open = null, drawnWidth = 0;

  async function show(into, key, opener) {
    root = into; open = opener;
    D = await app.callTool("view_data", { view: "player_night", report: key.report, boss: key.boss, player: key.player });
    render();
    new ResizeObserver(() => { if (root === into && Math.abs(into.clientWidth - drawnWidth) > 24) render(); }).observe(into);
  }

  const what = () => D.what === "healing" ? "Healing" : "Damage";
  const times = (n) => n === 0 ? "never" : n === 1 ? "once" : n === 2 ? "twice" : `${n} times`;

  function stats() {
    const n = D.pulls.length, [used, possible] = D.cooldowns_total;
    return kit.stats([
      ["Died before the wipe was decided", `${D.died} of ${n}`, D.died ? "dead" : "",
       `${D.first ? `first to die ${times(D.first)}` : "never first to die"}, ${times(D.early)} among the first ${D.early_n}`],
      ["Time dead", `${D.dead_s} s`, "", `${D.dead_pct}% of the time before each wipe was decided`],
      possible ? ["Cooldowns", `${used} of ${possible}`, used < possible ? "decided" : "", "pressed on cooldown, all pulls"] : null,
      D.best ? ["Best pull", `${D.best.pull}: ${kit.short(D.best.per_s_alive)}`, "", `${what().toLowerCase()} per s alive`] : null,
    ]);
  }

  function render() {
    if (!D || !root) return;
    const full = app.context().displayMode === "fullscreen", picture = app.picture();
    drawnWidth = root.clientWidth;
    const width = Math.max(300, drawnWidth || 640), p = D.player;
    const sub = [`${p.spec} ${p.class}`, D.boss,
                 D.pulls.length === D.total_pulls ? `${D.pulls.length} pulls` : `${D.pulls.length} of ${D.total_pulls} pulls`].join(" · ");
    const head = `<div class="head"><h1 style="color:${kit.esc(p.color)}">${kit.esc(p.name)}</h1><span class="sub">${kit.esc(sub)}</span></div>`;
    if (picture) { root.innerHTML = head + stats() + chart(width, false); return; }
    if (!full) {
      root.innerHTML = head + stats() + chart(width, false)
        + `<div class="actions">${app.canGoFullscreen() ? '<button class="primary" id="ng-open">Open night</button>' : ""}</div>`;
      const o = root.querySelector("#ng-open");
      if (o) o.onclick = () => app.requestDisplayMode("fullscreen").then(render);
    } else {
      root.innerHTML = head + stats() + chart(width, true)
        + `<div class="full"><div>${table()}</div>${side()}</div>`;
      root.querySelectorAll("tr[data-pull]").forEach((tr) => tr.onclick = () => openPull(+tr.dataset.pull));
    }
    root.querySelectorAll("[data-pull-col]").forEach((el) => el.addEventListener("click", () => openPull(+el.dataset.pullCol)));
  }

  function openPull(pull) {
    open("player", { report: D.report, pull, boss: D.boss, player: D.player.name });
  }

  function tipOf(r) {
    const head = `Pull ${r.pull}: ${r.kill ? "kill" : r.reset ? "a reset" : `${r.wipe_pct}%`}, ${kit.clock(r.duration_ms)}`;
    const out = r.per_s_alive != null ? `${kit.short(r.per_s_alive)}/s alive` + (r.rank ? `, rank ${r.rank} of ${r.of} ${D.role_name}` : "") : `no ${D.what}`;
    const died = !r.death ? "survived"
      : `died ${kit.clock(r.death.ms)} (#${r.death.place}) to ${r.death.by}` + (r.death.fallout ? ", after the wipe was decided" : "");
    const cds = r.cooldowns[1] ? `cooldowns ${r.cooldowns[0]}/${r.cooldowns[1]}` : "";
    return [head, out, died, cds, "Click for this pull"].filter(Boolean).join("\n");
  }

  // Pull by pull: their output per second alive (a bar), the boss's health left at the end (the gold line), their
  // death under it (red before the wipe was decided, with its place among the pull's deaths; grey after), and each
  // third's median output (dashed).
  function chart(W, lanes) {
    const n = D.pulls.length, L = 44, R = 40, T = 10, plotH = lanes ? 190 : 120;
    const band = (W - L - R) / Math.max(n, 1), x = (i) => L + i * band, bw = Math.max(2, Math.min(18, band * 0.7));
    const rates = D.pulls.map((r) => r.per_s_alive || 0), max = kit.niceMax(Math.max(...rates, 1));
    const yR = (v) => T + plotH - v / max * plotH, yP = (pct) => T + plotH - pct / 100 * plotH;
    let o = kit.line(L, T + plotH, W - R, T + plotH, "var(--line)");
    o += kit.text(L - 6, T + 8, `${kit.short(max)}/s`, "tm", 'text-anchor="end" font-size="10"') + kit.text(L - 6, T + plotH, "0", "tm", 'text-anchor="end" font-size="10"');
    o += kit.text(W - R + 6, T + 8, "100%", "tm", 'font-size="10"') + kit.text(W - R + 6, T + plotH, "0%", "tm", 'font-size="10"');
    D.pulls.forEach((r, i) => {
      const cx = x(i) + band / 2, other = r.role !== D.player.role;
      if (r.per_s_alive != null) {
        const y = yR(r.per_s_alive);
        o += r.reset ? `<rect x="${cx - bw / 2}" y="${y}" width="${bw}" height="${T + plotH - y}" fill="none" stroke="var(--muted)" stroke-dasharray="2 2"/>`
          : `<rect x="${cx - bw / 2}" y="${y}" width="${bw}" height="${T + plotH - y}" rx="2" fill="${other ? "var(--muted)" : kit.esc(D.player.color)}" opacity="${other ? 0.5 : 0.75}"/>`;
        if (r.kill) o += `<rect x="${cx - bw / 2 - 1}" y="${y - 3}" width="${bw + 2}" height="3" fill="var(--gold)"/>`;
      }
      if (r.death) {
        const dy = T + plotH + 14, counted = !r.death.fallout, rr = Math.max(1.5, Math.min(counted ? 6 : 3.5, band / 2 - 0.6));
        o += `<circle cx="${cx}" cy="${dy}" r="${rr}" fill="${counted ? "var(--dead)" : "var(--muted)"}" opacity="${counted ? 1 : 0.6}"/>`;
        if (counted && band >= 11) o += kit.text(cx, dy + 3.5, r.death.place, "", 'text-anchor="middle" font-size="8.5" font-weight="700" fill="var(--bg)"');
      }
    });
    // the boss's health left at the end of each pull
    // (a reset isn't an attempt: left out of the line)
    const pts = D.pulls.map((r, i) => [x(i) + band / 2, r.kill ? 0 : r.reset ? null : r.wipe_pct]).filter(([, v]) => v != null);
    if (pts.length > 1) o += `<path d="${pts.map(([cx, v], i) => `${i ? "L" : "M"}${cx} ${yP(v)}`).join(" ")}" fill="none" stroke="var(--gold)" stroke-width="1.6" opacity=".9"/>`;
    for (const [cx, v] of pts) o += `<circle cx="${cx}" cy="${yP(v)}" r="2.4" fill="var(--gold)"/>`;
    // each third's median output
    const at = new Map(D.pulls.map((r, i) => [r.pull, i]));
    D.thirds.forEach((t, k) => {
      if (t.rate == null || !t.pulls.length) return;
      const a = x(at.get(t.pulls[0])), b = x(at.get(t.pulls[t.pulls.length - 1])) + band, y = yR(t.rate);
      o += kit.line(a + 2, y, b - 2, y, "var(--ink)", 'stroke-width="1.4" stroke-dasharray="5 3" opacity=".75"');
      const label = `${["first", "middle", "last"][k]} third: median ${kit.short(t.rate)}, died ${t.died} of ${t.pulls.length}`;
      o += kit.hit(a, y - 6, b - a, 12, label);
      if (lanes) o += kit.text(a + 4, y - 5, label, "t", 'font-size="10"');
    });
    // a column per pull: its tooltip, and a click opens that pull
    D.pulls.forEach((r, i) => { o += `<rect x="${x(i)}" y="${T}" width="${band}" height="${plotH + 24}" fill="transparent" data-pull-col="${r.pull}" style="cursor:pointer"${kit.tip(tipOf(r))}/>`; });
    // pull numbers, as many as fit
    const every = Math.max(1, Math.ceil(26 / band));
    D.pulls.forEach((r, i) => { if (i % every === 0 || i === n - 1) o += kit.text(x(i) + band / 2, T + plotH + 36, r.pull, "tm", 'text-anchor="middle" font-size="10"'); });
    // the legend, wrapping on a narrow card
    const keys = [[(lx, ly) => `<rect x="${lx}" y="${ly - 8}" width="10" height="8" rx="2" fill="${kit.esc(D.player.color)}" opacity=".75"/>`, `${D.what} per s alive`],
      [(lx, ly) => `<line x1="${lx}" y1="${ly - 4}" x2="${lx + 12}" y2="${ly - 4}" stroke="var(--gold)" stroke-width="1.6"/>`, "boss left at the end"],
      [(lx, ly) => `<circle cx="${lx + 5}" cy="${ly - 4}" r="4.5" fill="var(--dead)"/>`, "died (place)"],
      [(lx, ly) => `<circle cx="${lx + 5}" cy="${ly - 4}" r="3" fill="var(--muted)" opacity=".6"/>`, "died after it was decided"]];
    if (D.thirds.length) keys.push([(lx, ly) => `<line x1="${lx}" y1="${ly - 4}" x2="${lx + 12}" y2="${ly - 4}" stroke="var(--ink)" stroke-dasharray="4 2"/>`, "median of a third"]);
    let lx = 6, ly = T + plotH + 56;
    for (const [mark, name] of keys) {
      const w = 18 + kit.width(name, 10) + 14;
      if (lx > 6 && lx + w > W) { lx = 6; ly += 16; }
      o += mark(lx, ly) + kit.text(lx + 17, ly, name, "tm", 'font-size="10"');
      lx += w;
    }
    return kit.svg(W, ly + 6, o, `${D.player.name}'s night on ${D.boss}: output and deaths pull by pull`);
  }

  function table() {
    const rows = D.pulls.map((r) => {
      const result = r.kill ? "kill" : r.reset ? "reset" : `${r.wipe_pct}%`;
      const out = r.per_s_alive != null ? kit.short(r.per_s_alive) : "–";
      const rank = r.rank ? `${r.rank}/${r.of}` : "";
      const died = !r.death ? "" : `${kit.iconImg(D.img_base, r.death.icon)}${kit.clock(r.death.ms)} #${r.death.place}`;
      const cds = r.cooldowns[1] ? `${r.cooldowns[0]}/${r.cooldowns[1]}` : "";
      return `<tr data-pull="${r.pull}" class="${r.death && !r.death.fallout ? "counted" : ""}"${kit.tip(r.death ? `${r.death.by}${r.death.fallout ? ", after the wipe was decided" : ""}` : "")}>`
        + `<td>${r.pull}</td><td>${result}</td><td>${kit.clock(r.duration_ms)}</td><td>${out}</td><td>${rank}</td>`
        + `<td class="${r.death && r.death.fallout ? "fallout" : ""}">${died}</td><td>${cds}</td></tr>`;
    }).join("");
    return `<div class="section-title">Pull by pull (click one for its view)</div><div class="scroll-x"><table class="pulls"><thead><tr>`
      + `<th>Pull</th><th>Boss</th><th>Length</th><th>${kit.esc(what())}/s</th><th>Rank</th><th>Died</th><th>Cooldowns</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function side() {
    const cds = D.cooldowns.map((c) => `<li><span>${kit.iconImg(D.img_base, c.icon)}${kit.esc(c.name)}</span>`
      + `<span class="count ${c.possible && c.used < c.possible ? "short" : ""}">${c.possible != null ? `${c.used}/${c.possible}` : c.used}</span></li>`).join("");
    const thirds = D.thirds.map((t, k) => `<li><span>${["First", "Middle", "Last"][k]} third (${t.pulls.length} pulls)</span>`
      + `<span class="count">${t.rate != null ? kit.short(t.rate) + "/s" : "–"}</span><span class="times">died before the wipe was decided on ${t.died} of ${t.pulls.length}</span></li>`).join("");
    return `<div><div class="panel"><div class="section-title" style="margin-top:2px">Major cooldowns, all pulls</div><ul class="cds">${cds || "<li>none listed</li>"}</ul>`
      + (D.cooldowns_unavailable ? `<div class="note">${kit.esc(D.cooldowns_unavailable)}</div>` : "")
      + `<div class="note">Uses / possible if pressed on cooldown until they died or the wipe was decided.</div></div>`
      + (thirds ? `<div class="panel"><div class="section-title" style="margin-top:2px">Over the night (${kit.esc(D.what)} per s alive, median)</div><ul class="cds">${thirds}</ul></div>` : "")
      + `</div>`;
  }

  return { show, relayout: render };
})();
