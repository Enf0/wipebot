// ---- a ranking of players as in-game damage meters draw it: a bar per player in their class colour, scaled to the
// top, with the amount, per second and share, the tool's few words of each, and the deaths a mechanic set up
// (breakdown by player, mechanic_hits for one ability, enemies' damage on adds). A panel (data-panel) per meter,
// each its own picture in Discord, where a picture of a whole raid's bars fills a phone's screen: the top players
// get a bar, the rest a line with their amounts ----
views.meter = (() => {
  let D = null, root = null, SHOWN = 10, LISTED = Infinity;  // rows drawn (a raid is 20-30), and the line with the rest up to
  const SKULL = '<svg class="skull" viewBox="0 0 12 12" aria-hidden="true"><path d="M6 1C3.2 1 1.5 2.9 1.5 5.3c0 1.5.7 2.6 1.8 3.2V10c0 .5.4 1 1 1h3.4c.6 0 1-.5 1-1V8.5c1.1-.6 1.8-1.7 1.8-3.2C10.5 2.9 8.8 1 6 1Zm-2 6a1.2 1.2 0 1 1 0-2.4A1.2 1.2 0 0 1 4 7Zm4 0a1.2 1.2 0 1 1 0-2.4A1.2 1.2 0 0 1 8 7Z" fill="currentColor"/></svg>';

  async function show(into, key) {
    root = into;
    // fewer where a picture shares the answer with another meter (the Discord bot's), its line the next few
    [SHOWN, LISTED] = key.rows ? [key.rows, 2 * key.rows] : [10, Infinity];
    D = await app.callTool("view_data", { view: "meter", report: key.report, tool: key.tool, args: key.args });
    render();
  }

  const amount = (m, v) => m.unit === "amount" ? kit.short(v) : String(Math.round(v));

  function stats(m) {
    const top = m.bars[0], hit = m.bars.reduce((n, b) => n + b.died_after, 0), killed = m.bars.reduce((n, b) => n + b.killed_by, 0);
    return kit.stats([
      m.unit === "amount" ? ["Total", kit.short(m.total), "", m.seconds ? `${kit.short(m.total / m.seconds)}/s over ${kit.clock(m.seconds * 1000)}` : ""]
        : ["Total", `${Math.round(m.total)} ${m.unit}`, "", ""],
      ["Most", top.name, "", `${amount(m, top.value)}, ${(top.value / Math.max(m.median, 1)).toFixed(1)}× the median`],
      ["Median", amount(m, m.median), "", `of ${m.bars.length} players`],
      hit || killed ? ["Deaths", `${hit} set up`, "dead", `${killed} by it as the killing blow`] : null,
    ]);
  }

  function rows(m) {
    const max = Math.max(m.bars[0].value, 1), med = m.median / max * 100;
    const shown = m.bars.slice(0, SHOWN).map((b, i) => {
      const w = Math.max(0.5, b.value / max * 100);
      const per = m.unit === "amount" && m.seconds ? ` (${kit.short(b.value / m.seconds)}/s)` : "";
      const pct = m.total ? 100 * b.value / m.total : 0, share = pct && pct < 0.5 ? "<1%" : m.total ? `${Math.round(pct)}%` : "";
      const tip = [`${b.name}${b.spec ? ` (${b.spec})` : ""}: ${amount(m, b.value)}${m.unit === "amount" ? "" : ` ${m.unit}`}${per}`, b.note,
                   b.died_after || b.killed_by ? `${b.died_after} deaths within a few seconds of a hit, ${b.killed_by} by it` : ""].filter(Boolean).join("\n");
      return `<li style="--c:${kit.esc(b.color)};--w:${w.toFixed(2)}%"${kit.tip(tip)}><span class="fill"></span>`
        + `<span class="rk">${i + 1}</span><span class="nm">${kit.esc(b.name)}${b.role === "tank" ? '<small class="role">tank</small>' : ""}`
        + `${b.note ? `<small class="nt">${kit.esc(b.note)}</small>` : ""}</span>`
        + `<span class="dd">${b.died_after ? `${SKULL}${b.died_after}` : ""}</span>`
        + `<span class="val">${amount(m, b.value)}<small>${kit.esc(per)}</small></span><span class="pc">${share}</span></li>`;
    }).join("");
    const rest = m.bars.slice(SHOWN, LISTED).map((b, i) =>
      `<span style="white-space:nowrap">${SHOWN + i + 1} ${kit.esc(b.name)} ${amount(m, b.value)}</span>`);
    if (m.bars.length > LISTED) rest.push(`${m.bars.length - LISTED} more`);
    const more = rest.length ? `<div class="note">${rest.join(" · ")}</div>` : "";
    return `<div class="meter-rows"><ol>${shown}</ol><div class="median" style="left:${med.toFixed(2)}%"><span>median</span></div></div>${more}`;
  }

  function panel(m) {
    const icon = kit.iconImg(D.img_base, m.icon);
    const head = `<div class="head"><h1>${icon}${kit.esc(m.title)}</h1><span class="sub">${kit.esc(m.scope)}</span></div>`;
    const others = m.others.length ? `<div class="note">Not hit: ${kit.esc(m.others.join(", "))}</div>` : "";
    const note = m.note ? `<div class="note">${m.bars.some((b) => b.died_after) ? SKULL : ""}${kit.esc(m.note)}</div>` : "";
    return `<section data-panel class="meter">${head}${stats(m)}${rows(m)}${others}${note}</section>`;
  }

  // Over several pulls, a mechanic's hits per player per pull: a row per player (most hit first), a column per
  // pull, darker for more; a red mark where it set up their death; the raid's total per pull under it
  function grid(m) {
    const n = m.pulls.length, left = 96, top = 18, row = 14, sq = Math.max(6, Math.min(12, Math.floor(560 / n) - 2));
    const total = (b) => b.per_pull.reduce((s, v) => s + (v || 0), 0);
    const col = sq + 2, rows = [...m.bars].sort((a, b) => total(b) - total(a)), w = left + n * col + 40, base = top + rows.length * row + 34;
    const max = Math.max(1, ...rows.flatMap((b) => b.per_pull.filter((v) => v !== null)));
    const raid = m.pulls.map((_, i) => rows.reduce((s, b) => s + (b.per_pull[i] || 0), 0)), rmax = Math.max(1, ...raid);
    const what = m.per_pull === "debuffs" ? "debuffs" : "hits";
    let body = "";
    m.pulls.forEach((p, i) => { if (i === 0 || p % 5 === 0) body += kit.text(left + i * col + sq / 2, 11, String(p), "tm", 'font-size="9.5" text-anchor="middle"'); });
    rows.forEach((b, r) => {
      const y = top + r * row;
      body += `<text x="${left - 6}" y="${y + sq - 1}" font-size="10.5" font-weight="600" text-anchor="end" class="cls" style="--c:${kit.esc(b.color)}">${kit.esc(b.name)}</text>`;
      b.per_pull.forEach((v, i) => {
        if (v === null) return;
        const x = left + i * col, share = v ? 35 + 65 * v / max : 0;
        const died = b.died_in.includes(i);
        body += `<rect x="${x}" y="${y}" width="${sq}" height="${sq}" rx="2" fill="${v ? `color-mix(in srgb, var(--gold) ${share.toFixed(0)}%, var(--panel))` : "var(--panel)"}"`
          + `${kit.tip(`${b.name}, pull ${m.pulls[i]}: ${v} ${what}${died ? "\nit set up their death" : ""}`)}/>`;
        if (died) body += `<path d="M${x + 2} ${y + 2}L${x + sq - 2} ${y + sq - 2}M${x + sq - 2} ${y + 2}L${x + 2} ${y + sq - 2}" stroke="var(--dead)" stroke-width="2" pointer-events="none"/>`;
      });
      body += kit.text(w - 4, y + sq - 1, String(total(b)), "t", 'font-size="10.5" text-anchor="end"');
    });
    const ry = top + rows.length * row + 4;
    body += kit.text(left - 6, ry + 24, "raid", "tm", 'font-size="10" text-anchor="end"');
    raid.forEach((v, i) => { const hgt = 26 * v / rmax; body += `<rect x="${left + i * col}" y="${ry + 28 - hgt}" width="${sq}" height="${hgt}" rx="1.5" fill="var(--gold)" opacity=".7"${kit.tip(`Pull ${m.pulls[i]}: ${v} ${what}`)}/>`; });
    const icon = kit.iconImg(D.img_base, m.icon);
    const head = `<div class="head"><h1>${icon}${kit.esc(m.title)} by pull</h1><span class="sub">${what} per player per pull, each until its wipe was decided</span></div>`;
    const keys = `<div class="legend"><span><i style="background:color-mix(in srgb, var(--gold) 35%, var(--panel))"></i>1 ${what === "hits" ? "hit" : "debuff"}</span>`
      + `<span><i style="background:var(--gold)"></i>${max}</span><span><i style="background:var(--panel)"></i>none</span>`
      + `<span><i style="background:var(--dead)"></i>it set up their death</span><span>blank: not in the pull</span></div>`;
    return `<section data-panel class="meter note-board">${head}<div class="grid">${kit.svg(w, base, body, `${m.title} by pull`)}</div>${keys}</section>`;
  }

  function render() {
    if (!D || !root) return;
    root.innerHTML = D.meters.map((m) => panel(m) + (m.pulls && m.pulls.length > 1 ? grid(m) : "")).join("");
  }

  return { show, relayout: render };
})();
