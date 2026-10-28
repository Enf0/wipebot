// ---- the raid note against every pull on a boss (raid_note with all_pulls): the kick assignments as a grid, pulls
// down and the note's assignments across, a square per cast in each cell (kicked, covered by another, went through,
// its player dead), the cast that went through first on a pull its wipe followed ringed; under it, whose cast that
// was, counted. Then each planned cooldown pull by pull. A panel (data-panel) each, each its own picture in Discord ----
views.note = (() => {
  let D = null, root = null;
  const ROW = 15, SQ = 10, GAP = 2;
  const KICK = {  // a cast's state: its fill, and its word in the tooltip and the legend
    kicked: ["var(--green)", "kicked"], covered: ["var(--blue)", "covered by another"],
    through: ["var(--dead)", "went through"], dead: ["var(--muted)", "its player dead"],
    absent: ["var(--line)", "its player not in the pull"],
  };
  const COOLDOWN = {
    "on time": ["var(--green)", "on time"], early: ["var(--gold)", "early"], late: ["var(--gold)", "late"],
    "not cast": ["var(--dead)", "not cast"], dead: ["var(--muted)", "dead by then"], absent: ["var(--line)", "not in the pull"],
  };

  const NEAR_S = 3, NEAR = "color-mix(in srgb, var(--gold) 45%, var(--bg))";  // a cooldown off by 1-3 s: paler

  async function show(into, key) {
    root = into;
    D = await app.callTool("view_data", { view: "note", report: key.report, boss: key.boss, tool: key.tool, args: key.args });
    render();
  }

  const square = (x, y, fill, tip, ring) => `<rect x="${x}" y="${y}" width="${SQ}" height="${SQ}" rx="2" fill="${fill}"`
    + (ring ? ` stroke="var(--gold)" stroke-width="2"` : "") + `${kit.tip(tip)}/>`;
  const legend = (items) => `<div class="legend">${items.map(([fill, word, ring]) =>
    `<span><i style="background:${fill}${ring ? ";outline:2px solid var(--gold);outline-offset:-1px" : ""}"></i>${kit.esc(word)}</span>`).join("")}</div>`;

  function kicks(K) {
    const waves = Math.max(1, ...K.pulls.flatMap((p) => p.cells.map((c) => c.length)));
    const colW = Math.max(waves * (SQ + GAP) + 8, 62), left = 34, top = 34;
    const w = left + K.slots.length * colW + 16, h = top + K.pulls.length * ROW + 4;
    let body = "";
    K.slots.forEach((s, j) => {
      const x = left + j * colW + 2;
      body += kit.text(x, 12, s.text.replace(/^Kick\s*/i, ""), "tm", 'font-size="9.5"');
      body += `<text x="${x}" y="26" font-size="10.5" font-weight="600" class="cls" style="--c:${kit.esc(s.color)}">${kit.esc(s.player)}</text>`;
    });
    K.pulls.forEach((p, i) => {
      const y = top + i * ROW;
      body += `<g${p.counted ? "" : ' opacity="0.35"'}>`;
      body += kit.text(left - 8, y + SQ - 0.5, String(p.pull), p.lost && p.counted ? "td" : "tm", 'font-size="10" text-anchor="end"');
      p.cells.forEach((cell, j) => cell.forEach((c, k) => {
        const [fill, word] = KICK[c.state];
        const tip = `Pull ${p.pull}, ${K.slots[j].text} at ${kit.clock(c.ms)}: ${c.player}'s cast ${word}`
          + (c.state === "covered" ? ` (${c.by})` : "") + (c.lead ? "\nThe first to go through before the wipe was decided" : "");
        body += square(left + j * colW + 2 + k * (SQ + GAP), y, fill, tip, c.lead);
      }));
      if (p.lost && p.counted) body += `<rect x="${w - 10}" y="${y}" width="4" height="${SQ}" rx="1" fill="var(--dead)"${kit.tip(`Pull ${p.pull}: the wipe was decided within 10 s after a cast went through`)}/>`;
      body += "</g>";
    });
    const from = K.from_pull ? K.pulls.findIndex((p) => p.pull === K.from_pull) : -1;
    if (from > 0) {
      const y = top + from * ROW - 3;
      body += kit.line(left - 30, y, w, y, "var(--gold)", 'stroke-dasharray="3 3" opacity="0.7"');
    }
    const lead = K.first[0];
    const head = `<div class="head"><h1>Kick assignments</h1><span class="sub">${kit.esc(D.boss)} · ${K.pulls.length} pulls`
      + (K.from_pull ? ` · counted from pull ${K.from_pull}, when the note's order came in` : "") + "</span></div>";
    const stats = kit.stats([
      ["Kicked", `${K.made} of ${K.alive}`, "", "by their player, while alive"],
      ["Pulls lost after a cast went through", `${K.lost} of ${K.counted}`, K.lost ? "dead" : "", "the wipe decided within 10 s"],
      lead ? ["Went through first most", lead.name, "", `${lead.count} of those pulls`] : null,
    ]);
    const max = Math.max(1, ...K.first.map((f) => f.count));
    const first = K.first.length ? `<div class="sec">Whose cast went through first, on the pulls the wipe followed</div><ol class="tally">`
      + K.first.map((f) => `<li><span class="nm cls" style="--c:${kit.esc(f.color)}">${kit.esc(f.name)}</span>`
        + `<span class="bar"><i style="width:${(100 * f.count / max).toFixed(1)}%;background:${kit.esc(f.color)}"></i></span><span class="val">${f.count}</span></li>`).join("")
      + "</ol>" : "";
    const keys = legend([[KICK.kicked[0], "kicked"], [KICK.covered[0], "covered by another"], [KICK.through[0], "went through"],
                         [KICK.through[0], "first through, the wipe followed", true], [KICK.dead[0], "their player dead"]]);
    return `<section data-panel class="note-board">${head}${stats}<div class="grid">${kit.svg(w, h, body, "Kick assignments by pull")}</div>${keys}${first}</section>`;
  }

  const FEW = 3;  // a planned cooldown fewer pulls than this reached is counted under the grid, not drawn

  function cooldowns(C0) {
    const reached = (r) => C0.pulls.filter((p) => p.cells[r]).length;
    const rows = C0.uses.map((u, r) => r).filter((r) => reached(r) >= FEW);
    const C = { uses: rows.map((r) => C0.uses[r]), pulls: C0.pulls.map((p) => ({ pull: p.pull, cells: rows.map((r) => p.cells[r]) })) };
    const fewer = C0.uses.length - rows.length;
    const labelW = 230, top = 26, colW = Math.max(SQ + 3, Math.min(16, 540 / C.pulls.length)), right = 70;
    const w = labelW + C.pulls.length * colW + right, h = top + C.uses.length * ROW + 4;
    let body = "";
    C.pulls.forEach((p, i) => { if (p.pull === 1 || p.pull % 5 === 0) body += kit.text(labelW + i * colW + SQ / 2, 14, String(p.pull), "tm", 'font-size="9.5" text-anchor="middle"'); });
    C.uses.forEach((u, r) => {
      const y = top + r * ROW, at = `${u.phase > 1 ? `P${u.phase} ` : ""}${kit.clock(u.at_s * 1000)}`;
      body += kit.text(0, y + SQ - 0.5, at, "tm", 'font-size="10"');
      body += kit.icon(D.img_base, u.icon, 40, y - 1, 12, u.spell);
      body += `<text x="${D.img_base && u.icon ? 56 : 40}" y="${y + SQ - 0.5}" font-size="10.5"><tspan font-weight="600" class="cls" style="--c:${kit.esc(u.color)}">${kit.esc(u.player)}</tspan>`
        + `<tspan class="tm" dx="5">${kit.esc(u.spell)}</tspan></text>`;
      let on = 0, seen = 0;
      C.pulls.forEach((p, i) => {
        const c = p.cells[r];
        if (!c) return;
        const near = c.off_s !== undefined && c.state !== "on time" && Math.abs(c.off_s) <= NEAR_S;
        const [fill, word] = near ? [NEAR, COOLDOWN[c.state][1]] : COOLDOWN[c.state];
        if (c.state !== "absent") seen++;
        if (c.state === "on time") on++;
        const off = c.off_s !== undefined && c.state !== "on time" ? ` by ${Math.abs(c.off_s).toFixed(0)} s` : "";
        body += square(labelW + i * colW, y, fill, `Pull ${p.pull}: ${u.player}'s ${u.spell} (note ${at}) ${word}${off}`);
      });
      body += kit.text(w - 4, y + SQ - 0.5, `${on} of ${seen}`, "tm", 'font-size="10" text-anchor="end"');
    });
    const head = `<div class="head"><h1>Planned cooldowns</h1><span class="sub">${kit.esc(D.boss)} · each pull until its wipe was decided</span></div>`;
    const keys = legend([[COOLDOWN["on time"][0], "on time (within 1 s)"], [NEAR, `early or late by ${NEAR_S} s at most`], [COOLDOWN.late[0], "more"],
                         [COOLDOWN["not cast"][0], "not cast, alive"], [COOLDOWN.dead[0], "dead by then"]]);
    return `<section data-panel class="note-board">${head}<div class="grid">${kit.svg(w, h, body, "Planned cooldowns by pull")}</div>${keys}`
      + `<div class="note">Right: on time, of the pulls that reached it before the wipe was decided.`
      + (fewer ? ` ${fewer} more planned later, which fewer than ${FEW} pulls reached, aren't drawn.` : "") + "</div></section>";
  }

  function render() {
    if (!D || !root) return;
    const parts = [D.kicks && kicks(D.kicks), D.cooldowns && cooldowns(D.cooldowns)].filter(Boolean);
    root.innerHTML = parts.join("") || '<div class="note">The note has no kick assignments or planned cooldowns for this boss.</div>';
  }

  return { show, relayout: render };
})();
