// ---- the player's night on a boss: the night in one sentence (views.player_night_headline), then what killed them,
// ranked, and whether a ready defensive or a healthstone would have kept them alive: the card (kit.card). Fullscreen
// adds their output and death pull by pull, the trend in thirds and their cooldowns as the text counts each. In
// Discord two panels (data-panel), each its own picture; a pull's chip or column opens that pull's player view ----
views.player_night = (() => {
  let D = null, root = null, open = null;

  async function show(into, key, opener) {
    root = into; open = opener;
    D = await app.callTool("view_data", { view: "player_night", report: key.report, boss: key.boss, player: key.player });
    render();
    kit.watch(into, () => root === into, render);
  }

  const what = () => D.what === "healing" ? "Healing" : "Damage";
  // how many of the survivable deaths a spell would have saved: "both" or "all 3" where it is every one of them, so
  // that two spells at the same death never read as more deaths than there are
  const all = (k, of) => k < of || of === 1 ? String(k) : of === 2 ? "both" : `all ${of}`;
  const times = (n) => n === 0 ? "never" : n === 1 ? "once" : n === 2 ? "twice" : `${n} times`;
  // what they died to, after "died 2:01 (#5)": " to Slag Wave", or what the log says where it names no killing blow
  const to = (d) => d.no_blow ? `, ${d.by}` : ` to ${d.by}`;

  function stats() {
    const n = D.pulls.filter((p) => !p.reset).length, L = D.lived;
    return [
      ["Deaths that count", `${D.died} of ${n}`, D.died ? "dead" : "",
       `${D.first ? `first to die ${times(D.first)}` : "never first to die"}, ${times(D.early)} among the first ${D.early_n}`],
      L.of ? ["Survivable", `${L.saved} of ${L.of}`, L.saved ? "decided" : "",
              [L.by.length ? L.by.slice(0, 2).map(([s, k]) => `${s} at ${all(k, L.saved)}`).join(", ") : "nothing ready would have helped",
               L.outright ? `${L.outright} killed outright` : "", L.unknown ? `can't tell for ${L.unknown}` : ""].filter(Boolean).join("; ")] : null,
      ["Time dead", `${D.dead_s} s`, "", `${D.dead_pct}% of the time before each wipe was decided`],
      D.best ? ["Best pull", `${D.best.pull}: ${kit.short(D.best.per_s_alive)}`, "", `${what().toLowerCase()} per s alive`] : null,
    ];
  }

  const KILLERS_SHOWN = 5;
  // What killed them before each wipe was decided, the most deaths first: a chip per pull, as player_night's text
  // reads that death (x.saved, views.player_night_view): in the colour of what to do differently where a ready
  // defensive or a healthstone would have kept them alive, plain where nothing ready would have, dashed where the
  // log can't tell (x.why: not replayed, or a ready spell's effect can't be told)
  const LIVED = "survivable with a ready defensive or a healthstone";
  function killers() {
    if (!D.killers.length) return "";
    const shown = D.killers.slice(0, KILLERS_SHOWN), rest = D.killers.slice(KILLERS_SHOWN);
    const chip = (k, x) => {
      const cls = x.saved === true ? "fix" : x.outright ? "outright" : x.saved === false ? "" : "unknown";
      const how = x.saved === true ? LIVED
        : x.outright ? "killed outright: no hit logged after high health, so nothing ready would have stopped it"
        : x.saved === false ? "nothing ready would have helped" : `can't tell${x.why ? `: ${x.why}` : ""}`;
      return `<button class="chip ${cls}" data-pull-chip="${x.pull}"${kit.tip(`Pull ${x.pull}: died ${kit.clock(x.ms)} (#${x.place})${to(k)}; ${how}`)}>${x.pull}</button>`;
    };
    const rows = shown.map((k) => `<li${k.no_blow ? ' class="no-blow"' : ""}><span class="kn">${k.no_blow ? '<i class="ic"></i>' : kit.iconImg(D.img_base, k.icon)}${kit.esc(k.by)}</span>`
      + `<span class="kc">${k.pulls.length}</span><span class="chips">${k.pulls.map((x) => chip(k, x)).join("")}</span></li>`).join("");
    const more = rest.length ? `<div class="note">and ${rest.length} other abilit${rest.length === 1 ? "y" : "ies"}, `
      + `${kit.plural(rest.reduce((n, k) => n + k.pulls.length, 0), "death")}</div>` : "";
    // The key: each of the two marked kinds of chip the rows drawn have, on the first pull that is one, so the
    // sample is never a pull of another kind and no kind is named that the night lacks
    const drawn = shown.flatMap((k) => k.pulls).sort((a, b) => a.pull - b.pull);
    const sample = (is, cls, words) => {
      const x = drawn.find(is);
      return x ? `<span class="key"><span class="chip ${cls} sample">${x.pull}</span>${words}</span>` : "";
    };
    const key = sample((x) => x.saved === true, "fix", LIVED)
      + sample((x) => x.saved == null, "unknown", "the log can't tell");
    // the key over the rows, where it is seen with them, and short enough for a phone's line
    return `<div class="section-title">What killed ${kit.esc(D.player.name)}</div>`
      + (key ? `<div class="note keys">${key}</div>` : "") + `<ul class="killers">${rows}</ul>${more}`;
  }

  // Their cooldowns over the night, each as player_night's text counts it: uses of possible uses, or, for one held
  // for a moment of the fight, the pulls that got there and on how many it was used. No total over them: the text
  // states none
  const HOW = "Uses / possible if pressed on cooldown until they died or the wipe was decided; one held for a moment of the fight is counted on the pulls that got there.";
  function cooldownsNote() {
    return D.cooldowns.length ? `<div class="note">Major cooldowns over the night: ${kit.esc(D.cooldowns.map((c) => c.said).join("; "))}. ${HOW}</div>` : "";
  }

  // Who they are, their night in one sentence and what killed them: the card, and the first of Discord's panels
  function card() {
    const p = D.player;
    const sub = [`${p.spec} ${p.class}`, D.boss,
                 D.pulls.length === D.total_pulls ? kit.plural(D.pulls.length, "pull") : `${D.pulls.length} of ${D.total_pulls} pulls`].join(" · ");
    return `<div data-panel="killers">${kit.card({ title: p.name, sub, color: p.color, headline: D.headline, stats: stats(), chart: killers(),
                                                    open: "Open night", url: D.url })}</div>`;
  }

  // The night pull by pull: fullscreen, and the second of Discord's panels
  const pulls = (W, lanes) => `<div data-panel="pulls"><div class="section-title">Pull by pull</div>${chart(W, lanes)}${cooldownsNote()}</div>`;

  function render() {
    if (!D || !root) return;
    const { full, picture, width } = kit.frame(root);
    if (picture) root.innerHTML = card() + pulls(width, false);
    else if (!full) root.innerHTML = card();
    else {
      root.innerHTML = card() + pulls(width, true) + `<div class="full"><div>${table()}</div>${side()}</div>`;
      root.querySelectorAll("tr[data-pull]").forEach((tr) => tr.onclick = () => openPull(+tr.dataset.pull));
    }
    root.querySelectorAll("[data-pull-col]").forEach((el) => el.addEventListener("click", () => openPull(+el.dataset.pullCol)));
    root.querySelectorAll("[data-pull-chip]").forEach((el) => el.addEventListener("click", () => openPull(+el.dataset.pullChip)));
  }

  function openPull(pull) {
    open("player", { report: D.report, pull, boss: D.boss, player: D.player.name });
  }

  // a pull's death that counts, as player_night counts them: before the wipe was decided, not one of a hit killing
  // many (the raid's to stop), and not on a reset, which is no pull of the night's
  function counts(r) { return !!r.death && !r.reset && !r.death.fallout && !r.death.mass; }
  function why(r) {
    const d = r.death;
    return d.fallout ? ", after the wipe was decided" : d.mass ? ", in a hit killing many" : r.reset ? ", on a reset: not counted" : "";
  }

  // how far a wipe got in the words of the pull's row in the text (pulls.wipe_text): the phase and the boss's health
  // in it on a boss counted by phase, with Warcraft Logs' % of the whole fight (what the line stands on); else the
  // boss's %
  const got = (r) => r.got_wcl;

  function tipOf(r) {
    const head = `Pull ${r.pull}: ${r.kill ? "kill" : r.reset ? "a reset" : got(r)}, ${kit.clock(r.duration_ms)}`;
    const out = r.per_s_alive != null ? `${kit.short(r.per_s_alive)}/s alive` + (r.rank ? `, rank ${r.rank} of ${r.of} ${D.role_name} by total` : "") : `no ${D.what}`;
    const died = !r.death ? "survived"
      : `died ${kit.clock(r.death.ms)} (#${r.death.place})${to(r.death)}` + why(r);
    const cds = r.cooldowns.length ? `cooldowns ${r.cooldowns.join(", ")}` : "";  // the words of the pull's row in the text
    return [head, out, died, cds].filter(Boolean).join("\n");
  }

  // Pull by pull: their output per second alive (a bar), the boss's health left at the end (the gold line), their
  // death under it (red where it counts, with its place among the pull's deaths; grey where it doesn't), and each
  // third's median output (dashed).
  function chart(W, lanes) {
    const n = D.pulls.length, L = 44, R = 40, T = 10, plotH = lanes ? 190 : 120;
    const band = (W - L - R) / Math.max(n, 1), x = (i) => L + i * band, bw = Math.max(2, Math.min(18, band * 0.7));
    const rates = D.pulls.map((r) => r.per_s_alive || 0), max = kit.niceMax(Math.max(...rates, 1));
    const yR = (v) => T + plotH - v / max * plotH, yP = (pct) => T + plotH - pct / 100 * plotH;
    let o = kit.line(L, T + plotH, W - R, T + plotH, "var(--line)");
    o += kit.text(L - 6, T + 8, `${kit.short(max)}/s`, "tm", 'text-anchor="end"') + kit.text(L - 6, T + plotH, "0", "tm", 'text-anchor="end"');
    o += kit.text(W - R + 6, T + 8, "100%") + kit.text(W - R + 6, T + plotH, "0%");
    D.pulls.forEach((r, i) => {
      const cx = x(i) + band / 2, other = r.role !== D.player.role;
      if (r.per_s_alive != null) {
        const y = yR(r.per_s_alive);
        o += r.reset ? `<rect x="${cx - bw / 2}" y="${y}" width="${bw}" height="${T + plotH - y}" fill="none" stroke="var(--muted)" stroke-dasharray="2 2"/>`
          : `<rect x="${cx - bw / 2}" y="${y}" width="${bw}" height="${T + plotH - y}" rx="2" ${other ? 'fill="var(--muted)" opacity=".5"' : `${kit.cls(D.player.color, "cb")} opacity=".75"`}/>`;
        if (r.kill) o += `<rect x="${cx - bw / 2 - 1}" y="${y - 3}" width="${bw + 2}" height="3" fill="var(--gold)"/>`;
      }
      if (r.death) {
        const dy = T + plotH + 14, counted = counts(r), rr = Math.max(1.5, Math.min(counted ? 6 : 3.5, band / 2 - 0.6));
        o += `<circle cx="${cx}" cy="${dy}" r="${rr}" fill="${counted ? "var(--dead)" : "var(--muted)"}" opacity="${counted ? 1 : 0.6}"/>`;
        if (counted && band >= 11) o += kit.text(cx, dy + 3.5, r.death.place, "tb", 'text-anchor="middle" font-weight="700"', kit.SIZE.mark);
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
      if (lanes) o += kit.text(a + 4, y - 5, label, "t over");
    });
    // a column per pull: its tooltip, and a click opens that pull
    D.pulls.forEach((r, i) => { o += `<rect x="${x(i)}" y="${T}" width="${band}" height="${plotH + 24}" fill="transparent" data-pull-col="${r.pull}"${kit.opens("this pull")}${kit.tip(tipOf(r))}/>`; });
    // pull numbers, as many as fit
    const every = Math.max(1, Math.ceil(26 / band));
    D.pulls.forEach((r, i) => { if (kit.numbered(i, n, every)) o += kit.text(x(i) + band / 2, T + plotH + 36, r.pull, "tm", 'text-anchor="middle"'); });
    // the legend, wrapping on a narrow card
    const keys = [[(lx, ly) => `<rect x="${lx}" y="${ly - 8}" width="10" height="8" rx="2" ${kit.cls(D.player.color, "cb")} opacity=".75"/>`, `${D.what} per s alive`],
      [(lx, ly) => `<line x1="${lx}" y1="${ly - 4}" x2="${lx + 12}" y2="${ly - 4}" stroke="var(--gold)" stroke-width="1.6"/>`, "boss left at the end"],
      [(lx, ly) => `<circle cx="${lx + 5}" cy="${ly - 4}" r="4.5" fill="var(--dead)"/>`, band >= 11 ? "died, with their place in the order" : "died before it was decided"],
      [(lx, ly) => `<circle cx="${lx + 5}" cy="${ly - 4}" r="3" fill="var(--muted)" opacity=".6"/>`, "died, not counted (fallout, a hit killing many, a reset)"]];
    if (D.thirds.length) keys.push([(lx, ly) => `<line x1="${lx}" y1="${ly - 4}" x2="${lx + 12}" y2="${ly - 4}" stroke="var(--ink)" stroke-dasharray="4 2"/>`, "median of each third of the night"]);
    const [under, ly] = kit.legend(keys, W, T + plotH + 56);
    o += under;
    return kit.svg(W, ly + 6, o, `${D.player.name}'s night on ${D.boss}: output and deaths pull by pull`);
  }

  function table() {
    const rows = D.pulls.map((r) => {
      const result = r.kill ? "kill" : r.reset ? "reset" : r.got;
      const out = r.per_s_alive != null ? kit.short(r.per_s_alive) : "–";
      const rank = r.rank ? `${r.rank}/${r.of}` : "";
      const died = !r.death ? "" : `${kit.iconImg(D.img_base, r.death.icon)}${kit.clock(r.death.ms)} #${r.death.place}`;
      const cds = kit.esc(r.cooldowns.join(", "));
      return `<tr data-pull="${r.pull}"${kit.opens()} class="${counts(r) ? "counted" : ""}"${kit.tip(r.death ? `${r.death.by}${why(r)}` : "")}>`
        + `<td>${r.pull}</td><td>${result}</td><td>${kit.clock(r.duration_ms)}</td><td>${out}</td><td>${rank}</td>`
        + `<td class="${r.death && !counts(r) ? "fallout" : ""}">${died}</td><td>${cds}</td></tr>`;
    }).join("");
    return `<div class="section-title">Pull by pull (click one for its view)</div><div class="scroll-x"><table class="pulls"><thead><tr>`
      + `<th>Pull</th><th>Boss</th><th>Length</th><th>${kit.esc(what())}/s</th><th>Rank</th><th>Died</th><th>Cooldowns</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function side() {
    const cds = D.cooldowns.map((c) => `<li><span>${kit.iconImg(D.img_base, c.icon)}${kit.esc(c.name)}</span>`
      + `<span class="count">${kit.esc(c.count)}</span><span class="times">${kit.esc(c.of)}</span></li>`).join("");  // a count, no verdict
    const thirds = D.thirds.map((t, k) => `<li><span>${["First", "Middle", "Last"][k]} third (${kit.plural(t.pulls.length, "pull")})</span>`
      + `<span class="count">${t.rate != null ? kit.short(t.rate) + "/s" : "–"}</span><span class="times">died before the wipe was decided on ${t.died} of ${t.pulls.length}</span></li>`).join("");
    return `<div><div class="panel"><div class="section-title" style="margin-top:2px">Major cooldowns, all pulls</div><ul class="cds">${cds || "<li>none listed</li>"}</ul>`
      + (D.cooldowns_unavailable ? `<div class="note">${kit.esc(D.cooldowns_unavailable)}</div>` : "")
      + `<div class="note">${HOW}</div></div>`
      + (thirds ? `<div class="panel"><div class="section-title" style="margin-top:2px">Over the night (${kit.esc(D.what)} per s alive, median)</div><ul class="cds">${thirds}</ul></div>` : "")
      + `</div>`;
  }

  return { show, relayout: render, seen: () => `${D.player.name}'s night on ${D.boss}` };
})();
