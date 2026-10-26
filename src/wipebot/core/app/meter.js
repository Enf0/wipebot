// ---- a ranking of players as in-game damage meters draw it: a bar per player in their class colour, scaled to the
// top, with the amount, per second and share, the tool's few words of each, and the deaths a mechanic set up
// (breakdown by player, mechanic_hits for one ability, enemies' damage on adds). A panel (data-panel) per meter,
// each its own picture in Discord ----
views.meter = (() => {
  let D = null, root = null;
  const SHOWN = 25;  // rows drawn; a raid is 20-30, the rest are a line
  const SKULL = '<svg class="skull" viewBox="0 0 12 12" aria-hidden="true"><path d="M6 1C3.2 1 1.5 2.9 1.5 5.3c0 1.5.7 2.6 1.8 3.2V10c0 .5.4 1 1 1h3.4c.6 0 1-.5 1-1V8.5c1.1-.6 1.8-1.7 1.8-3.2C10.5 2.9 8.8 1 6 1Zm-2 6a1.2 1.2 0 1 1 0-2.4A1.2 1.2 0 0 1 4 7Zm4 0a1.2 1.2 0 1 1 0-2.4A1.2 1.2 0 0 1 8 7Z" fill="currentColor"/></svg>';

  async function show(into, key) {
    root = into;
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
    const more = m.bars.length > SHOWN ? `<div class="note">and ${m.bars.length - SHOWN} more</div>` : "";
    return `<div class="meter-rows"><ol>${shown}</ol><div class="median" style="left:${med.toFixed(2)}%"><span>median</span></div></div>${more}`;
  }

  function panel(m) {
    const icon = kit.iconImg(D.img_base, m.icon);
    const head = `<div class="head"><h1>${icon}${kit.esc(m.title)}</h1><span class="sub">${kit.esc(m.scope)}</span></div>`;
    const others = m.others.length ? `<div class="note">Not hit: ${kit.esc(m.others.join(", "))}</div>` : "";
    const note = m.note ? `<div class="note">${m.bars.some((b) => b.died_after) ? SKULL : ""}${kit.esc(m.note)}</div>` : "";
    return `<section data-panel class="meter">${head}${stats(m)}${rows(m)}${others}${note}</section>`;
  }

  function render() {
    if (!D || !root) return;
    root.innerHTML = D.meters.map(panel).join("");
  }

  return { show, relayout: render };
})();
