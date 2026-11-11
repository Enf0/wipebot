// ---- a ranking of players as in-game damage meters draw it: a bar per player in their class colour, scaled to the
// top, with the amount, per second and share, the tool's few words of each, and the deaths soon after a mechanic's
// hit (breakdown by player, mechanic_hits for one ability, enemies' damage on the most damaged adds). The order, the
// total, the shares and "in N of M pulls" are the tool's own, as its text states them (views.meter_view): nothing
// here is summed over the bars. The card (kit.card): the ranking in one sentence (views.meter_headline), its few
// numbers and the top players' bars; "Open ranking" draws every player's in fullscreen, with a mechanic's hits per
// player per pull under them, and "By pull" opens the trend of a ranking over several pulls. A panel (data-panel)
// per meter, each its own picture in Discord, where a picture of a whole raid's bars fills a phone's screen: the top
// players get a bar, the rest a line with their amounts. Against another night (mechanic_hits' against), each bar
// per minute with a tick at the other night's and the change. The player the picture is for (key.mark, the one
// asking) is ringed, and their bar shown under the top ones when it's further down. For "who did the least?"
// (breakdown's least) it's drawn from the bottom, least first, the top players in the line ----
views.meter = (() => {
  const INLINE = 8;  // the bars of a card in a host, where the rest are a click away
  let D = null, root = null, open = null, ROWS = 0, SHOWN = 10, LISTED = Infinity, MARK = "", ASKER = "";  // rows drawn (a raid is 20-30), and the line with the rest up to
  const SKULL = '<svg class="skull" viewBox="0 0 12 12" aria-hidden="true"><path d="M6 1C3.2 1 1.5 2.9 1.5 5.3c0 1.5.7 2.6 1.8 3.2V10c0 .5.4 1 1 1h3.4c.6 0 1-.5 1-1V8.5c1.1-.6 1.8-1.7 1.8-3.2C10.5 2.9 8.8 1 6 1Zm-2 6a1.2 1.2 0 1 1 0-2.4A1.2 1.2 0 0 1 4 7Zm4 0a1.2 1.2 0 1 1 0-2.4A1.2 1.2 0 0 1 8 7Z" fill="currentColor"/></svg>';

  async function show(into, key, opener) {
    root = into; open = opener;
    ROWS = key.rows || 0;  // fewer where a picture shares the answer with another meter (the Discord bot's)
    ASKER = key.mark || "";
    MARK = ASKER.toLowerCase();
    D = await app.callTool("view_data", { view: "meter", report: key.report, tool: key.tool, args: key.args });
    render();
  }

  // An amount in the page's short form; a rate of a few a minute comes written as its tool writes it (`text`), and
  // so does a median between two counts
  const amount = (m, v, text) => text || (m.unit === "amount" || m.unit === "per minute" ? kit.short(v) : String(Math.round(v)));
  const each = (m) => m.unit.includes(" per minute") ? "/min" : "";  // after a mechanic's hits per minute over a night
  const marked = (b) => MARK && b.name.toLowerCase() === MARK;
  const signed = (change) => change.replace("-", "−");  // the tool's "-3%" with a minus sign

  // Against another night: the raid's per minute both nights, the deaths soon after a hit, and the ringed player's
  function thenStats(m) {
    const t = m.then, mine = m.bars.findIndex(marked), b = m.bars[mine];
    return [
      ["Raid, per minute", `${kit.short(t.raid_was)} → ${kit.short(t.raid)}`, t.raid > t.raid_was * 1.05 ? "dead" : t.raid < t.raid_was * 0.95 ? "good" : "",
       `${signed(t.change)} · ${Math.round(t.minutes_was)} → ${Math.round(t.minutes)} min of pulls`],
      [`Deaths within ${m.within_s} s`, `${t.deaths_was} → ${t.deaths}`, "", `of a hit · ${t.deaths_per_min[0]} → ${t.deaths_per_min[1]} per minute`],
      b && b.was != null ? [b.name, `${amount(m, b.was, b.was_text)} → ${amount(m, b.value, b.text)}`, b.value < b.was ? "good" : b.value > b.was ? "dead" : "",
                            `${kit.ordinal(b.was_rank)} → ${kit.ordinal(mine + 1)} of ${m.bars.length}`] : null,
    ];
  }

  // The card's numbers, each as its tool states it: breakdown's total with its rate and combat time, what a kind of
  // add took (enemies' "took", with neither), a mechanic's deaths (its text's "Total" line). Who is first and by how
  // much is its headline
  function stats(m) {
    if (m.then) return thenStats(m);
    const mine = m.bars.findIndex(marked), b = m.bars[mine], s = m.stated, d = m.deaths;
    return [
      s ? [m.least ? "Raid total" : "Total", s.total, "", s.per_s ? `${s.per_s} over ${s.over}` : ""] : null,
      d && (d.killed_by || d.after) ? ["Killing blows", String(d.killed_by), d.killed_by ? "dead" : "", "deaths it dealt"] : null,
      d && d.after ? [`Deaths within ${m.within_s} s`, String(d.after), "", "of a hit, of any cause"] : null,
      ["Median", `${amount(m, m.median, m.median_text)}${each(m)}`, "", `of ${m.bars.length} players`],
      b ? [b.name, `${amount(m, b.value, b.text)}${each(m)}`, "", `${kit.ordinal(mine + 1)} of ${m.bars.length}`] : null,
    ];
  }

  function bar(m, b, i, max) {
    // the bar's length as a share of the track: the row, less the amounts' column against another night (page.css)
    const w = Math.max(0.005, b.value / max), then = m.then, along = (f) => `calc(${f.toFixed(4)} * (100% - var(--amounts, 0px)))`;
    const per = m.unit === "amount" && m.seconds ? ` (${kit.short(b.value / m.seconds)}/s)` : each(m);
    const mine = amount(m, b.value, b.text);
    const was = then && b.was != null ? `${amount(m, b.was, b.was_text)} on ${then.was_day} (${kit.ordinal(b.was_rank)})` : then ? `not there on ${then.was_day}` : "";
    const tip = [`${b.name}${b.spec ? ` (${b.spec})` : ""}: ${mine}${m.unit === "amount" ? per : ` ${m.unit}`}`, was, b.note, b.share ? `${b.share} of the total` : "",
                 b.died_after || b.killed_by ? `${kit.plural(b.died_after, "death")} within ${m.within_s} s of a hit (any cause), ${b.killed_by} with it as the killing blow` : ""].filter(Boolean).join("\n");
    const tick = then && b.was != null ? `<span class="was" style="left:${along(Math.min(1, b.was / max))}"></span>` : "";
    // the last column: the change against the other night, else the share of the total its tool states (none: empty)
    const last = then ? (b.was != null ? `<span class="dl ${b.value < b.was ? "good" : b.value > b.was ? "bad" : ""}">${signed(b.change)}</span>` : '<span class="dl">new</span>')
      : b.share ? `<span class="pc">${kit.esc(b.share)}</span>` : "";
    const row = `<span class="rk">${i + 1}</span><span class="nm">${kit.esc(b.name)}${b.role === "tank" ? '<small class="role">tank</small>' : ""}`
      + `${b.note ? `<small class="nt">${kit.esc(b.note)}</small>` : ""}</span>`
      + `<span class="dd">${b.died_after ? `${SKULL}${b.died_after}` : ""}</span>`
      + `<span class="val">${then && b.was != null ? `<small class="old">${amount(m, b.was, b.was_text)} →</small>` : ""}${mine}<small>${kit.esc(per)}</small></span>${last}`;
    // the row twice: in the page's colours, and over it in the text colour for the bar, cut to the bar's length
    return `<li class="${marked(b) ? "ringed" : ""}" style="--c:${kit.esc(b.color)};--w:${along(w)}"${kit.tip(tip)}><span class="fill cb"></span>`
      + `<div class="row">${row}</div><div class="row on" aria-hidden="true">${row}</div>${tick}</li>`;
  }

  function rows(m) {
    // the scale: the longest bar's amount (a rate of a few a minute is under 1), the other night's among them
    const top = Math.max(...m.bars.map((b) => b.value), ...(m.then ? m.bars.map((b) => b.was || 0) : [])) || 1, med = m.median / top * 100;
    // the ranks in the order drawn: from the top, or from the bottom (least)
    const order = m.bars.map((_, i) => i), ranks = m.least ? order.reverse() : order;
    const mine = ranks.findIndex((i) => marked(m.bars[i])), below = mine >= SHOWN;  // the ringed player's bar, under the others
    const shown = ranks.slice(0, SHOWN).map((i) => bar(m, m.bars[i], i, top)).join("")
      + (below ? `<li class="gap" aria-hidden="true">⋮</li>${bar(m, m.bars[ranks[mine]], ranks[mine], top)}` : "");
    const rest = ranks.slice(SHOWN, LISTED).filter((i) => !marked(m.bars[i])).map((i) =>
      `<span style="white-space:nowrap">${i + 1} ${kit.esc(m.bars[i].name)} ${amount(m, m.bars[i].value, m.bars[i].text)}</span>`);
    if (m.bars.length > LISTED) rest.push(`${m.bars.length - LISTED} more${LISTED === SHOWN ? " players" : ""}`);
    const more = rest.length ? `<div class="note">${rest.join(" · ")}</div>` : "";
    const keys = m.then ? `<div class="legend"><span><i class="tick"></i>${kit.esc(m.then.was_day)}</span><span>bar: ${kit.esc(m.then.day)}</span>`
      + (mine >= 0 ? "<span>ringed: the one asking</span>" : "") + "</div>" : "";
    const plain = !m.then && !m.bars.some((b) => b.share);  // no share stated: no column for one
    return `<div class="meter-rows${m.then ? " then" : plain ? " plain" : ""}"><ol>${shown}</ol>${m.then ? "" : `<div class="median" style="left:${med.toFixed(2)}%"><span>median</span></div>`}</div>${more}${keys}`;
  }

  // A meter's card: the first of a host's, with the buttons; every one is a picture's panel
  function card(m, first) {
    const { full, picture } = kit.frame(root);
    const title = m.then ? `${m.title}, ${m.then.was_day} → ${m.then.day}` : m.title;
    // who it never hit: named in a picture and in fullscreen, counted on a card when many
    const few = picture || full || m.others.length <= 6;
    const others = !m.others.length ? "" : `<div class="note">Not hit: ${few ? kit.esc(m.others.join(", ")) : kit.plural(m.others.length, "player")}</div>`;
    // a mechanic's deaths are among a card's numbers already: their line is the picture's and fullscreen's
    const deaths = m.bars.some((b) => b.died_after || b.killed_by);
    const note = m.note && (picture || full || !deaths) ? `<div class="note">${deaths ? SKULL : ""}${kit.esc(m.note)}</div>` : "";
    return `<section data-panel class="meter">${kit.card({
      title, sub: m.scope, icon: kit.iconImg(D.img_base, m.icon), headline: m.headline, stats: stats(m), chart: rows(m) + others + note,
      // fullscreen has more only where the card left players out, or the hits per pull are under it
      open: first && (m.bars.length > INLINE || (m.pulls || []).length > 1 || D.meters.length > 1) ? "Open ranking" : "",
      buttons: first && D.by_pull ? [["meter-by-pull", "By pull"]] : [], url: first ? D.url : "" })}</section>`;
  }

  // Over several pulls, a mechanic's hits per player per pull: a row per player (in the meter's order, its tool's), a
  // column per pull, darker for more; a red mark where they died soon after a hit, of any cause; the raid's total
  // per pull under it
  function grid(m) {
    const NAME = { weight: 600, most: 130 };  // a name's column: as wide as the longest, a longer one cut at its end
    const named = m.bars.map((b) => kit.width(b.name, kit.SIZE.label, NAME.weight));
    const left = Math.ceil(Math.max(60, Math.min(NAME.most, Math.max(...named) + 10)));
    const n = m.pulls.length, top = 18, row = 14, sq = Math.max(6, Math.min(12, Math.floor(560 / n) - 2));
    const total = (b) => b.per_pull.reduce((s, v) => s + (v || 0), 0);
    const col = sq + 2, rows = m.bars, w = left + n * col + 40, base = top + rows.length * row + 34;
    const max = Math.max(1, ...rows.flatMap((b) => b.per_pull.filter((v) => v !== null)));
    const raid = m.pulls.map((_, i) => rows.reduce((s, b) => s + (b.per_pull[i] || 0), 0)), rmax = Math.max(1, ...raid);
    const what = m.per_pull === "debuffs" ? "debuffs" : "hits", count = (v) => kit.plural(v, what.slice(0, -1));
    const soon = `died within ${m.within_s} s of a hit`;
    let body = "";
    m.pulls.forEach((p, i) => { if (i === 0 || p % 5 === 0) body += kit.text(left + i * col + sq / 2, 11, String(p), "tm", 'text-anchor="middle"'); });
    rows.forEach((b, r) => {
      const y = top + r * row;
      body += kit.text(left - 6, y + sq - 1, kit.fit(b.name, left - 10, kit.SIZE.label, NAME.weight), "cls", `font-weight="${NAME.weight}" text-anchor="end" style="--c:${kit.esc(b.color)}"`);
      b.per_pull.forEach((v, i) => {
        if (v === null) return;
        const x = left + i * col, share = v ? 35 + 65 * v / max : 0;
        const died = b.died_in.includes(i);
        body += `<rect x="${x}" y="${y}" width="${sq}" height="${sq}" rx="2" fill="${v ? `color-mix(in srgb, var(--gold) ${share.toFixed(0)}%, var(--panel))` : "var(--panel)"}"`
          + `${kit.tip(`${b.name}, pull ${m.pulls[i]}: ${count(v)}${died ? `\n${soon}, of any cause` : ""}`)}/>`;
        if (died) body += `<path d="M${x + 2} ${y + 2}L${x + sq - 2} ${y + sq - 2}M${x + sq - 2} ${y + 2}L${x + 2} ${y + sq - 2}" stroke="var(--dead)" stroke-width="2" pointer-events="none"/>`;
      });
      body += kit.text(w - 4, y + sq - 1, String(total(b)), "t", 'text-anchor="end"');
    });
    const ry = top + rows.length * row + 4;
    body += kit.text(left - 6, ry + 24, "raid", "tm", 'text-anchor="end"');
    raid.forEach((v, i) => { const hgt = 26 * v / rmax; body += `<rect x="${left + i * col}" y="${ry + 28 - hgt}" width="${sq}" height="${hgt}" rx="1.5" fill="var(--gold)" opacity=".7"${kit.tip(`Pull ${m.pulls[i]}: ${count(v)}`)}/>`; });
    const icon = kit.iconImg(D.img_base, m.icon);
    const head = `<div class="head"><h1>${icon}${kit.esc(m.title)} by pull</h1><span class="sub">${what} per player per pull, each until its wipe was decided</span></div>`;
    const keys = `<div class="legend"><span><i style="background:color-mix(in srgb, var(--gold) 35%, var(--panel))"></i>1 ${what === "hits" ? "hit" : "debuff"}</span>`
      + `<span><i style="background:var(--gold)"></i>${max}</span><span><i style="background:var(--panel)"></i>none</span>`
      + `<span><i style="background:var(--dead)"></i>${soon} (any cause)</span><span>blank: not in the pull</span></div>`;
    return `<section data-panel="by_pull" class="meter note-board">${head}<div class="grid">${kit.svg(w, base, body, `${m.title} by pull`)}</div>${keys}</section>`;
  }

  function render() {
    if (!D || !root) return;
    const { full, picture } = kit.frame(root);
    // a picture: ten bars and the rest in a line (fewer when it shares an answer); a card: the top few, the rest a
    // click away (where the host has no fullscreen, in the line); fullscreen: everyone's
    [SHOWN, LISTED] = picture ? (ROWS ? [ROWS, 2 * ROWS] : [10, Infinity]) : full ? [Infinity, Infinity]
      : [INLINE, app.canGoFullscreen() ? INLINE : Infinity];
    const all = picture || full;
    root.innerHTML = (all ? D.meters : D.meters.slice(0, 1)).map((m, i) =>
      card(m, i === 0) + (all && m.pulls && m.pulls.length > 1 ? grid(m) : "")).join("");
    kit.press(root, "meter-by-pull", () => open("trend", { report: D.report, boss: D.boss, tool: D.by_pull.tool, args: D.by_pull.args, mark: ASKER }));
  }

  return { show, relayout: render, seen: () => `the ranking ${D.meters[0].title} (${D.meters[0].scope})` };
})();
