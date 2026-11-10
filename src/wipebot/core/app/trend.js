// ---- an amount pull by pull over a night (views.trend_view): what breakdown states grouped by pull (damage, healing,
// damage taken per second, casts per minute) and mechanic_hits over every pull (hits per minute). The card: the trend
// in one sentence, a bar per pull over its counted time, a kill capped in gold, a reset hollow and left out of the
// medians, each third of the night's median dashed; under it the row of the player the picture is for (key.mark, the
// one asking), their own amounts in the same pulls. Fullscreen adds every pull's numbers; a pull's column opens it ----
views.trend = (() => {
  let D = null, root = null, open = null;

  async function show(into, key, opener) {
    root = into; open = opener;
    D = await app.callTool("view_data", { view: "trend", report: key.report, tool: key.tool, args: key.args,
                                          ...(key.mark ? { player: key.mark } : {}) });
    render();
    kit.watch(into, () => root === into, render);
  }

  const unit = () => D.per === "minute" ? "/min" : "/s";
  const fmt = (v) => D.count ? (Math.round(v * 10) / 10).toFixed(1) : kit.exact(v);  // as the headline writes them
  const total = (v) => D.count ? String(Math.round(v)) : kit.short(v);
  const change = (now, was) => { const p = Math.round((now / was - 1) * 100); return p ? `${p > 0 ? "+" : "−"}${Math.abs(p)}%` : "the same"; };
  const counted = () => D.pulls.filter((r) => !r.reset);
  const result = (r) => r.kill ? "kill" : r.reset ? "reset" : `${r.pct}%`;

  function stats() {
    const n = counted(), resets = D.pulls.length - n.length, t = D.thirds, mine = D.mine;
    const high = n.reduce((a, r) => !a || r.rate > a.rate ? r : a, null);
    const low = n.reduce((a, r) => !a || r.rate < a.rate ? r : a, null);
    const at = (r) => `${fmt(r.rate)}${unit()}${r.kill ? ", the kill" : ""}`;
    return [
      D.median != null ? ["Median pull", `${fmt(D.median)}${unit()}`, "", `of ${kit.plural(n.length, "pull")}${resets ? `, ${kit.plural(resets, "reset")} left out` : ""}`] : null,
      t ? ["First to last third", `${fmt(t[0].rate)} → ${fmt(t[2].rate)}`, "", t[0].rate ? change(t[2].rate, t[0].rate) : ""] : null,
      high ? ["Highest", `Pull ${high.pull}`, "", at(high)] : null,
      mine && mine.thirds ? [mine.name, `${fmt(mine.thirds[0].rate)} → ${fmt(mine.thirds[2].rate)}`, "", "their own, first to last third"]
        : low && low !== high ? ["Lowest", `Pull ${low.pull}`, "", at(low)] : null,
    ];
  }

  function tipOf(r) {
    const head = `Pull ${r.pull}: ${r.kill ? "kill" : r.reset ? "a reset, not counted" : `wipe at ${r.pct}%`}`;
    const out = `${fmt(r.rate)}${unit()}: ${total(r.amount)} over ${kit.clock(r.seconds * 1000)}`;
    const top = r.top.length ? `most: ${r.top.map(([n, v]) => `${n} ${total(v)}`).join(", ")}` : "";
    const own = D.mine ? `${D.mine.name}: ${r.mine != null ? `${fmt(r.mine)}${unit()}` : "not in it"}` : "";
    return [head, out, top, own].filter(Boolean).join("\n");
  }

  // The bars: each pull's amount over its counted time; `tall` in fullscreen, where the thirds are labelled
  function chart(W, tall) {
    const n = D.pulls.length, L = 46, R = 10, T = 14, plotH = tall ? 220 : 132, mine = D.mine, own = mine ? 38 : 0;
    const band = (W - L - R) / Math.max(n, 1), x = (i) => L + i * band, bw = Math.max(2, Math.min(n > 8 ? 22 : 46, band * 0.7));
    const max = kit.niceMax(Math.max(...D.pulls.map((r) => r.rate), D.count ? 1 : 10)), base = T + plotH, y = (v) => base - v / max * plotH;
    const fill = D.who ? kit.cls(D.who.color, "cb") : 'fill="var(--blue)"';
    let o = kit.line(L, base, W - R, base, "var(--line)");
    o += kit.text(L - 6, T + 8, `${D.count ? max : kit.short(max)}${unit()}`, "tm", 'text-anchor="end"') + kit.text(L - 6, base, "0", "tm", 'text-anchor="end"');
    D.pulls.forEach((r, i) => {
      const cx = x(i) + band / 2, top = y(r.rate), h = Math.max(base - top, r.rate ? 1 : 0);
      o += r.reset ? `<rect x="${cx - bw / 2}" y="${top}" width="${bw}" height="${h}" fill="none" stroke="var(--muted)" stroke-dasharray="2 2"/>`
        : `<rect x="${cx - bw / 2}" y="${top}" width="${bw}" height="${h}" rx="2" ${fill} opacity=".8"/>`;
      if (r.kill) o += `<rect x="${cx - bw / 2 - 1}" y="${top - 3}" width="${bw + 2}" height="3" fill="var(--gold)"/>`;
    });
    // each third's median, over its pulls
    const at = new Map(D.pulls.map((r, i) => [r.pull, i]));
    (D.thirds || []).forEach((t, k) => {
      const a = x(at.get(t.pulls[0])), b = x(at.get(t.pulls[t.pulls.length - 1])) + band, ty = y(t.rate);
      const label = `${["first", "middle", "last"][k]} third: median ${fmt(t.rate)}${unit()}`;
      o += kit.line(a + 2, ty, b - 2, ty, "var(--ink)", 'stroke-width="1.4" stroke-dasharray="5 3" opacity=".75"');
      const short = `${fmt(t.rate)}`, room = b - a - 8;
      const s = tall && kit.width(label, kit.SIZE.label) < room ? label : kit.width(short, kit.SIZE.label) < room ? short : "";
      if (s) o += kit.text(a + 4, ty - 5, s, "t over");
      o += kit.hit(a, ty - 6, b - a, 12, label);
    });
    // the asker's own row: their amounts in the same pulls, to its own scale
    if (mine) {
      const top = base + 30, most = Math.max(...D.pulls.map((r) => r.mine || 0), 1e-9);
      o += kit.text(L, top - 4, `${mine.name}, up to ${fmt(most)}${unit()}`, "cls", `font-weight="600" style="--c:${kit.esc(mine.color)}"`);
      o += kit.line(L, top + own - 12, W - R, top + own - 12, "var(--line)");
      D.pulls.forEach((r, i) => {
        if (r.mine == null) return;
        const cx = x(i) + band / 2, h = Math.max(1, (own - 14) * r.mine / most);
        o += `<rect x="${cx - bw / 2}" y="${top + own - 12 - h}" width="${bw}" height="${h}" rx="1.5" ${kit.cls(mine.color, "cb")} opacity="${r.reset ? ".35" : ".8"}"/>`;
      });
    }
    const foot = base + (mine ? 30 + own - 12 : 0);
    // a column per pull: its tooltip, and a click opens that pull
    D.pulls.forEach((r, i) => { o += `<rect x="${x(i)}" y="${T}" width="${band}" height="${foot - T + 4}" fill="transparent" data-pull-col="${r.pull}"${kit.opens("this pull")}${kit.tip(tipOf(r))}/>`; });
    const every = Math.max(1, Math.ceil(26 / band));
    D.pulls.forEach((r, i) => { if (kit.numbered(i, n, every)) o += kit.text(x(i) + band / 2, foot + 15, r.pull, "tm", 'text-anchor="middle"'); });
    const keys = [[(lx, ly) => `<rect x="${lx}" y="${ly - 8}" width="10" height="8" rx="2" ${fill} opacity=".8"/>`, `${D.subject} per ${D.per}, by pull`]];
    if (D.pulls.some((r) => r.kill)) keys.push([(lx, ly) => `<rect x="${lx}" y="${ly - 8}" width="10" height="3" fill="var(--gold)"/><rect x="${lx}" y="${ly - 5}" width="10" height="5" ${fill} opacity=".8"/>`, "kill"]);
    if (D.pulls.some((r) => r.reset)) keys.push([(lx, ly) => `<rect x="${lx + 0.5}" y="${ly - 8}" width="9" height="8" fill="none" stroke="var(--muted)" stroke-dasharray="2 2"/>`, "reset, not counted"]);
    if (D.thirds) keys.push([(lx, ly) => `<line x1="${lx}" y1="${ly - 4}" x2="${lx + 12}" y2="${ly - 4}" stroke="var(--ink)" stroke-dasharray="4 2"/>`, "median of each third of the pulls"]);
    const [under, ly] = kit.legend(keys, W, foot + 35);
    return `<div class="trend-chart">${kit.svg(W, ly + 6, o + under, `${D.title}: ${D.subject} per ${D.per}, pull by pull`)}</div>`;
  }

  // Every pull's numbers, in fullscreen: a row opens that pull
  function table() {
    const rows = D.pulls.map((r) => `<tr data-pull="${r.pull}"${kit.opens()}><td>${r.pull}</td><td>${result(r)}</td><td>${kit.clock(r.seconds * 1000)}</td>`
      + `<td>${total(r.amount)}</td><td>${fmt(r.rate)}</td>${D.mine ? `<td>${r.mine != null ? fmt(r.mine) : "–"}</td>` : ""}`
      + `<td>${r.top.map(([n, v]) => `${kit.esc(n)} ${total(v)}`).join(", ")}</td></tr>`).join("");
    return `<div class="section-title">Pull by pull (click one for its view)</div><div class="scroll-x"><table class="pulls"><thead><tr>`
      + `<th>Pull</th><th>Boss</th><th>Counted</th><th>Total</th><th>Per ${D.per === "minute" ? "min" : "s"}</th>${D.mine ? `<th>${kit.esc(D.mine.name)}</th>` : ""}<th>Most</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function render() {
    if (!D || !root) return;
    const { full, width } = kit.frame(root);
    const sub = [D.who ? `${D.who.name} (${D.who.spec})` : "", D.scope].filter(Boolean).join(" · ");
    root.innerHTML = kit.card({ title: D.title, sub, icon: kit.iconImg(D.img_base, D.icon), headline: D.headline, stats: stats(),
                                chart: chart(width, full), open: "Open trend", url: D.url }) + (full ? table() : "");
    root.querySelectorAll("[data-pull-col]").forEach((el) => el.addEventListener("click", () => openPull(+el.dataset.pullCol)));
    root.querySelectorAll("tr[data-pull]").forEach((tr) => tr.onclick = () => openPull(+tr.dataset.pull));
  }

  // one player's trend opens their pull, the raid's the pull
  function openPull(pull) {
    if (D.who) open("player", { report: D.report, pull, boss: D.boss, player: D.who.name });
    else open("pull", { report: D.report, pull, boss: D.boss });
  }

  return { show, relayout: render, seen: () => `${D.title} (${D.who ? `${D.who.name}, ` : ""}${D.scope})` };
})();
