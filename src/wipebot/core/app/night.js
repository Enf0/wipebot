// ---- the player's night on a boss: what killed them, ranked, and whether a ready defensive or a healthstone would
// have kept them alive; their output and death pull by pull, the trend in thirds, their cooldowns summed. Two panels
// (data-panel), each its own picture in Discord; a pull's chip or column opens that pull's player view ----
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
    const n = D.pulls.length, L = D.lived;
    return kit.stats([
      ["Died before the wipe was decided", `${D.died} of ${n}`, D.died ? "dead" : "",
       `${D.first ? `first to die ${times(D.first)}` : "never first to die"}, ${times(D.early)} among the first ${D.early_n}`],
      L.of ? ["Could have lived", `${L.saved} of ${L.of}`, L.saved ? "good" : "",
              [L.by.length ? L.by.slice(0, 2).map(([s, k]) => `${s} at ${k}`).join(", ") : "nothing ready would have saved them",
               L.outright ? `${L.outright} killed outright` : "", L.unknown ? `can't tell for ${L.unknown}` : ""].filter(Boolean).join("; ")] : null,
      ["Time dead", `${D.dead_s} s`, "", `${D.dead_pct}% of the time before each wipe was decided`],
      D.best ? ["Best pull", `${D.best.pull}: ${kit.short(D.best.per_s_alive)}`, "", `${what().toLowerCase()} per s alive`] : null,
    ]);
  }

  const KILLERS_SHOWN = 5;
  // What killed them before each wipe was decided, the most deaths first: a chip per pull, green where a ready
  // defensive or a healthstone would have kept them alive (the defensives tool's replay), grey where it can't tell
  function killers() {
    if (!D.killers.length) return "";
    const shown = D.killers.slice(0, KILLERS_SHOWN), rest = D.killers.slice(KILLERS_SHOWN);
    const chip = (k, x) => {
      const cls = x.saved === true ? "saved" : x.outright ? "outright" : x.saved === false ? "" : "unknown";
      const how = x.saved === true ? "a ready defensive or a healthstone would have kept them alive"
        : x.outright ? "killed outright: no hit logged after high health, so nothing ready would have stopped it"
        : x.saved === false ? "nothing ready would have kept them alive" : "can't tell: no overkill or health logged";
      return `<button class="chip ${cls}" data-pull-chip="${x.pull}"${kit.tip(`Pull ${x.pull}: died ${kit.clock(x.ms)} (#${x.place}) to ${k.by}; ${how}`)}>${x.pull}</button>`;
    };
    const rows = shown.map((k) => `<li><span class="kn">${kit.iconImg(D.img_base, k.icon)}${kit.esc(k.by)}</span>`
      + `<span class="kc">${k.pulls.length}</span><span class="chips">${k.pulls.map((x) => chip(k, x)).join("")}</span></li>`).join("");
    const more = rest.length ? `<div class="note">and ${rest.length} other abilit${rest.length === 1 ? "y" : "ies"}, `
      + `${rest.reduce((n, k) => n + k.pulls.length, 0)} deaths</div>` : "";
    return `<div class="section-title">What killed ${kit.esc(D.player.name)} before each wipe was decided</div>`
      + `<ul class="killers">${rows}</ul>${more}`
      + `<div class="note"><span class="chip saved sample">12</span> a pull where a ready defensive or a healthstone would have kept them alive</div>`;
  }

  function cooldownsNote() {
    const [used, possible] = D.cooldowns_total;
    return possible ? `<div class="note">Major cooldowns: ${used} of ${possible} uses if pressed each time they were ready, all pulls (until they died or the wipe was decided).</div>` : "";
  }

  // The two panels: who they are and what killed them; the night pull by pull
  function panels(W, lanes) {
    return `<div data-panel="killers">${head()}${stats()}${killers()}</div>`
      + `<div data-panel="pulls"><div class="section-title">Pull by pull</div>${chart(W, lanes)}${cooldownsNote()}</div>`;
  }

  function head() {
    const p = D.player;
    const sub = [`${p.spec} ${p.class}`, D.boss,
                 D.pulls.length === D.total_pulls ? `${D.pulls.length} pulls` : `${D.pulls.length} of ${D.total_pulls} pulls`].join(" · ");
    return `<div class="head"><h1 style="color:${kit.esc(p.color)}">${kit.esc(p.name)}</h1><span class="sub">${kit.esc(sub)}</span></div>`;
  }

  function render() {
    if (!D || !root) return;
    const full = app.context().displayMode === "fullscreen", picture = app.picture();
    drawnWidth = root.clientWidth;
    const width = Math.max(300, drawnWidth || 640);
    if (picture) { root.innerHTML = panels(width, false); return; }
    if (!full) {
      root.innerHTML = panels(width, false)
        + `<div class="actions">${app.canGoFullscreen() ? '<button class="primary" id="ng-open">Open night</button>' : ""}</div>`;
      const o = root.querySelector("#ng-open");
      if (o) o.onclick = () => app.requestDisplayMode("fullscreen").then(render);
    } else {
      root.innerHTML = panels(width, true) + `<div class="full"><div>${table()}</div>${side()}</div>`;
      root.querySelectorAll("tr[data-pull]").forEach((tr) => tr.onclick = () => openPull(+tr.dataset.pull));
    }
    root.querySelectorAll("[data-pull-col]").forEach((el) => el.addEventListener("click", () => openPull(+el.dataset.pullCol)));
    root.querySelectorAll("[data-pull-chip]").forEach((el) => el.addEventListener("click", () => openPull(+el.dataset.pullChip)));
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
