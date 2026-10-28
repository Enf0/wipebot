// ---- two raid mates side by side (compare_with_top with against): their output per second alive, time alive, deaths
// and item level on top, then per spell (most output first) each one's casts per minute alive as a bar out from the
// middle, the average hit at the ends, what's far apart in gold. One panel (data-panel), one picture in Discord ----
views.versus = (() => {
  let D = null, root = null;
  const W = 680, MID = 150, HIT = 74, ROW = 22, TOP = 30;

  async function show(into, key) {
    root = into;
    D = await app.callTool("view_data", { view: "versus", report: key.report, boss: key.boss, pull: key.pull,
                                          player: key.player, tool: key.tool, args: key.args });
    render();
  }

  const span = (s) => s >= 3600 ? `${Math.floor(s / 3600)}:${kit.clock((s % 3600) * 1000).padStart(5, "0")}` : kit.clock(s * 1000);
  const pct = (x) => x == null ? "-" : `${Math.round(100 * x)}%`;
  const ratio = (x, y) => y ? ` (${x >= y ? "+" : ""}${Math.round(100 * (x / y - 1))}%)` : "";

  function spells() {
    const bColor = D.b.color === D.a.color ? `color-mix(in srgb, ${D.b.color} 50%, var(--ink))` : D.b.color;  // same class
    const half = (W - MID) / 2, bar = half - HIT - 8;
    const left = HIT, centre = HIT + bar + 4, right = centre + MID + 4;
    const most = Math.max(0.1, ...D.spells.flatMap((s) => [s.a?.cpm || 0, s.b?.cpm || 0]));  // one scale for every spell
    let body = "";
    body += kit.text(left + bar, 12, "casts / min", "tm", 'font-size="9.5" text-anchor="end"');
    body += kit.text(right, 12, "casts / min", "tm", 'font-size="9.5"');
    body += kit.text(0, 12, "per hit", "tm", 'font-size="9.5"') + kit.text(W, 12, "per hit", "tm", 'font-size="9.5" text-anchor="end"');
    D.spells.forEach((s, i) => {
      const y = TOP + i * ROW, mid = y + 7, a = s.a || {}, b = s.b || {};
      const far = (w) => s.far.includes(w);
      const tip = (who, x) => x ? `${who}, ${s.name}: ${x.cpm == null ? "no casts counted" : `${x.cpm} casts a minute alive`}`
        + `${x.per_hit ? `, ${kit.short(x.per_hit)} a hit` : ""}, crit ${pct(x.crit)}, ${pct(x.share)} of their ${D.metric}` : `${who} didn't use ${s.name}`;
      if (i % 2 === 0) body += `<rect x="0" y="${y - 5}" width="${W}" height="${ROW}" fill="var(--panel)" opacity="0.5"/>`;
      if (a.cpm) {
        const w = a.cpm / most * bar;
        body += `<rect x="${left + bar - w}" y="${mid - 5}" width="${w}" height="10" rx="2" fill="${kit.esc(D.a.color)}" opacity="0.85"${kit.tip(tip(D.a.name, a))}/>`;
      }
      if (b.cpm) {
        const w = b.cpm / most * bar;
        body += `<rect x="${right}" y="${mid - 5}" width="${w}" height="10" rx="2" fill="${kit.esc(bColor)}" opacity="0.85"${kit.tip(tip(D.b.name, b))}/>`;
      }
      const cls = (w) => far(w) ? "tg" : "tm";
      if (a.cpm != null) body += kit.text(left + bar - 4 - (a.cpm / most * bar), mid + 3.5, a.cpm.toFixed(1), cls("casts"), 'font-size="9.5" text-anchor="end"');
      if (b.cpm != null) body += kit.text(right + 4 + (b.cpm / most * bar), mid + 3.5, b.cpm.toFixed(1), cls("casts"), 'font-size="9.5"');
      body += kit.text(0, mid + 3.5, a.per_hit ? kit.short(a.per_hit) : "-", cls("per hit"), 'font-size="10"');
      body += kit.text(W, mid + 3.5, b.per_hit ? kit.short(b.per_hit) : "-", cls("per hit"), 'font-size="10" text-anchor="end"');
      const iconAt = centre + 2, name = s.name.length > 22 ? s.name.slice(0, 21) + "…" : s.name;
      body += kit.icon(D.img_base, s.icon, iconAt, mid - 6, 12, s.name);
      body += `<text x="${iconAt + (D.img_base && s.icon ? 16 : 0)}" y="${mid + 3.5}" font-size="10.5" class="t"${kit.tip(`${tip(D.a.name, s.a)}\n${tip(D.b.name, s.b)}`)}>${kit.esc(name)}</text>`;
    });
    const h = TOP + D.spells.length * ROW;
    return kit.svg(W, h, body, `${D.a.name} against ${D.b.name}, spell by spell`);
  }

  function render() {
    if (!D || !root) return;
    const A = D.a, B = D.b, name = (s) => `<span class="cls" style="--c:${kit.esc(s.color)}">${kit.esc(s.name)}</span>`;
    const where = D.pull ? `${D.boss} pull ${D.pull}` : `${D.boss}, ${D.pulls} pulls both were in`;
    const head = `<div class="head"><h1>${name(A)} <span class="tm">against</span> ${name(B)}</h1>`
      + `<span class="sub">${kit.esc(A.spec === B.spec ? A.spec : `${A.spec} · ${B.spec}`)} · ${kit.esc(where)}</span></div>`;
    const stats = kit.stats([
      [`${D.metric === "healing" ? "Healing" : "Damage"} per second alive`, `${kit.short(A.per_s)} / ${kit.short(B.per_s)}`, "", ratio(A.per_s, B.per_s).trim()],
      ["Time alive", `${span(A.alive_s)} / ${span(B.alive_s)}`, "", ""],
      ["Deaths", `${A.deaths} / ${B.deaths}`, A.deaths > B.deaths ? "dead" : "", ""],
      ["Item level", `${A.ilvl} / ${B.ilvl}`, "", ""],
    ]);
    const note = `<div class="note">${kit.esc(A.name)} on the left, ${kit.esc(B.name)} on the right; most ${kit.esc(D.metric)} first. `
      + "Gold: 25% or more apart. A spell with no bar is a proc or a pet, with no casts of its own.</div>";
    root.innerHTML = `<section data-panel class="versus note-board">${head}${stats}<div class="grid">${spells()}</div>${note}</section>`;
  }

  return { show, relayout: render };
})();
