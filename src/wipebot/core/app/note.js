// ---- the raid note against every pull on a boss (raid_note with all_pulls): the kick assignments as a grid, pulls
// down and the note's assignments across, a square per cast in each cell (kicked, covered by another, went through,
// its player dead), the cast that went through first on a pull its wipe followed ringed; under it, whose cast that
// was, counted. Then each planned cooldown pull by pull, and each callout's players pull by pull (pressed what answers
// it, only something else, none). A panel (data-panel) each, each its own picture in Discord, named so an answer
// can pick the one it's about.
// For a boss not pulled yet or the next raid (raid_note's upcoming), the note as it is: each player's jobs and, on the
// fight's timeline, their planned cooldowns and the callouts to them ----
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

  const FEW = 3;  // a planned cooldown, kick assignment or callout fewer pulls than this reached isn't drawn (views.NOTE_FEW)
  const MIN_W = 520;  // .note-board .grid svg's min-width
  const NEAR_S = 3, NEAR = "color-mix(in srgb, var(--gold) 45%, var(--bg))";  // a cooldown off by 1-3 s: paler
  const NEAR_GREEN = "color-mix(in srgb, var(--green) 45%, var(--bg))";

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
    const label = Math.max(...K.slots.map((s) => Math.max(kit.width(s.text.replace(/^Kick\s*/i, ""), 9.5), kit.width(s.player, 10.5))));
    const colW = Math.max(waves * (SQ + GAP) + 8, 62, label + 10), left = 34, top = 34;
    // at least the grid's least width (page.css), so a note with one or two assignments isn't drawn blown up
    const w = Math.max(MIN_W, left + K.slots.length * colW + 16), h = top + K.pulls.length * ROW + 4;
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
    const fewer = K.fewer ? `<div class="note">${K.fewer} more, which fewer than ${FEW} pulls reached, aren't drawn.</div>` : "";
    return `<section data-panel="kicks" class="note-board">${head}${stats}<div class="grid">${kit.svg(w, h, body, "Kick assignments by pull")}</div>${keys}${fewer}${first}</section>`;
  }


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
    return `<section data-panel="cooldowns" class="note-board">${head}<div class="grid">${kit.svg(w, h, body, "Planned cooldowns by pull")}</div>${keys}`
      + `<div class="note">Right: on time, of the pulls that reached it before the wipe was decided.`
      + (fewer ? ` ${fewer} more planned later, which fewer than ${FEW} pulls reached, aren't drawn.` : "") + "</div></section>";
  }

  const CALLOUT = {  // a player at a pull's callouts: their fill, and the word in the legend
    answered: ["var(--green)", "pressed a defensive"], some: [NEAR_GREEN, "at some of them"],
    other: ["var(--gold)", "only something else"], none: ["var(--dead)", "pressed none"], dead: ["var(--muted)", "dead"],
  };
  // A player's square on a pull, where the callout may come more than once: answered every time it found them alive,
  // some of the times, never but pressed something else, never, or dead for all of them
  function pullState(cell) {
    const alive = cell.filter((s) => s !== "dead"), made = alive.filter((s) => s === "answered").length;
    if (!alive.length) return "dead";
    if (made) return made === alive.length ? "answered" : "some";
    return alive.includes("other") ? "other" : "none";
  }

  function callouts(C) {
    const labelW = 130, top = 26, head = 20, colW = Math.max(SQ + 3, Math.min(16, 540 / C.pulls.length)), right = 56;
    const w = Math.max(MIN_W, labelW + C.pulls.length * colW + right);
    const h = top + C.callouts.reduce((a, g) => a + head + g.rows.length * ROW + 6, 0);
    let body = "", y = top;
    C.pulls.forEach((p, i) => { if (p === C.pulls[0] || p % 5 === 0) body += kit.text(labelW + i * colW + SQ / 2, 14, String(p), "tm", 'font-size="9.5" text-anchor="middle"'); });
    for (const g of C.callouts) {
      body += `<text x="0" y="${y + 11}" class="t" font-size="10.5" font-weight="600">${kit.esc(`"${g.text}"`)}<tspan class="tm" font-weight="400" dx="6">${kit.esc(`to ${g.to} at ${g.times.join(", ")}`)}</tspan></text>`;
      y += head;
      for (const r of g.rows) {
        body += `<text x="0" y="${y + SQ - 0.5}" font-size="10.5" font-weight="600" class="cls" style="--c:${kit.esc(r.color)}">${kit.esc(r.player)}</text>`;
        r.cells.forEach((cell, i) => {
          if (!cell.length) return;
          const alive = cell.filter((s) => s !== "dead"), made = alive.filter((s) => s === "answered").length;
          body += square(labelW + i * colW, y, CALLOUT[pullState(cell)][0],
                         `Pull ${C.pulls[i]}, "${g.text}": ${r.player} ` + (alive.length ? `pressed a defensive at ${made} of ${alive.length}` : "dead"));
        });
        body += kit.text(w - 4, y + SQ - 0.5, `${r.made} of ${r.alive}`, "tm", 'font-size="10" text-anchor="end"');
        y += ROW;
      }
      y += 6;
    }
    const states = new Set(C.callouts.flatMap((g) => g.rows.flatMap((r) => r.cells.filter((c) => c.length).map(pullState))));
    const hd = `<div class="head"><h1>Personals</h1><span class="sub">${kit.esc(D.boss)} · ${C.pulls.length} pulls</span></div>`;
    const keys = legend(Object.entries(CALLOUT).filter(([s]) => s === "answered" || s === "none" || states.has(s))
      .map(([, [fill, word]]) => [fill, word]));
    return `<section data-panel="callouts" class="note-board">${hd}<div class="grid">${kit.svg(w, h, body, "Callouts by pull")}</div>${keys}`
      + `<div class="note">What each player pressed from 3 s before to 5 s after the callout. A defensive or tank cooldown answers it. Right: times they pressed one, of the times they were alive.</div></section>`;
  }

  const KIND = ["var(--blue)", "#c084fc", "#2dd4bf", "#f472b6", "#fb923c"];  // a kind of callout (its first word)
  const PLAN_W = 680, NAME_W = 112, PLAN_ROW = 32, SECTION = 18, PHASE_GAP = 10, LONG_HEADING = 18;
  const ROLES = [["tank", "Tanks"], ["healer", "Healers"], ["dps", "Damage"], ["", "Not in the log"]];
  const kindOf = (text) => (text.match(/[A-Za-z]+/) || [text])[0].toLowerCase();

  function plan(P) {
    const x0 = NAME_W, right = PLAN_W - 8, total = P.phases.reduce((a, p) => a + p.length_s, 0);
    const per = (right - x0 - PHASE_GAP * (P.phases.length - 1)) / Math.max(total, 1);
    const start = {};
    let at = x0;
    for (const p of P.phases) { start[p.phase] = at; at += p.length_s * per + PHASE_GAP; }
    const X = (i) => start[i.phase] + i.at_s * per;
    const kinds = [];  // across the raid, so a colour means the same callout on every row
    for (const r of P.rows) for (const t of r.callouts) if (!kinds.includes(kindOf(t))) kinds.push(kindOf(t));
    const hue = (t) => KIND[kinds.indexOf(kindOf(t)) % KIND.length];
    const long = new Map();  // a long group heading is named once under the chart, its labels on the rows
    const group = (g) => {
      if (!g.label) return g.heading;
      if (g.heading.length <= LONG_HEADING) return `${g.heading}: ${g.label}`;
      long.set(g.heading, new Set([...(long.get(g.heading) || []), g.label]));
      return g.label;
    };
    const sections = ROLES.map(([role, word]) => [word, P.rows.filter((r) => (ROLES.some(([k]) => k && k === r.role) ? r.role : "") === role)])
      .filter(([, rows]) => rows.length);
    const lane = P.everyone.length ? 24 : 0, top = 22 + lane;
    const h = top + sections.length * SECTION + P.rows.length * PLAN_ROW;
    let body = "", y = top;
    for (const [word, rows] of sections) {
      body += kit.text(0, y + 12, word.toUpperCase(), "tm", 'font-size="9" font-weight="600" letter-spacing="0.06em"');
      y += SECTION;
      rows.forEach((r, i) => {
        const mid = y + 11;
        if (i % 2 === 0) body += `<rect x="0" y="${y - 2}" width="${PLAN_W}" height="${PLAN_ROW}" rx="3" fill="var(--panel)"/>`;
        body += `<text x="6" y="${mid + 4}" font-size="11.5" font-weight="600" class="cls" style="--c:${kit.esc(r.color)}">${kit.esc(r.player)}</text>`;
        for (const it of r.items) {
          const x = X(it), tip = `${r.player}, ${phaseAt(P, it)}: ${it.spell || it.text}`;
          if (it.text) body += `<rect x="${x - 2}" y="${mid - 7}" width="4" height="14" rx="2" fill="${hue(it.text)}"${kit.tip(tip)}/>`;
          else if (D.img_base && it.icon) body += kit.icon(D.img_base, it.icon, x - 8, mid - 8, 16, tip);
          else body += `<circle cx="${x}" cy="${mid}" r="5" fill="var(--green)"${kit.tip(tip)}/>`;
        }
        const parts = [...r.callouts.map((t) => [t, hue(t)]), ...r.groups.map((g) => [group(g), ""])];
        let room = Math.floor((right - x0) / kit.width("x", 9.5)), line = "";
        for (const [k, [t, fill]] of parts.entries()) {
          const said = (k ? "  ·  " : "") + t;
          if (room <= 1) break;
          const cut = said.length > room ? said.slice(0, Math.max(room - 1, 0)) + "…" : said;
          line += `<tspan${fill ? ` fill="${fill}"` : ""}>${kit.esc(cut)}</tspan>`;
          room -= said.length;
        }
        if (line) body += `<text x="${x0}" y="${y + 26}" class="tm" font-size="9.5">${line}</text>`;
        y += PLAN_ROW;
      });
    }
    for (const p of P.phases) {  // the minutes over the rows; a phase's name when there are several
      const x = start[p.phase], end = x + p.length_s * per;
      if (P.phases.length > 1) body += kit.text(x, 10, `P${p.phase}`, "t", 'font-size="10" font-weight="600"');
      for (let s = 0; s < p.length_s; s += 60) {
        body += kit.line(x + s * per, 14, x + s * per, h, "var(--line)", s ? 'stroke-dasharray="2 4" opacity="0.8"' : "");
        if (s || P.phases.length === 1) body += kit.text(x + s * per, 10, kit.clock(s * 1000), "tm", `font-size="9" text-anchor="${s ? "middle" : "start"}"`);
      }
      if (P.phases.length > 1) body += kit.line(end, 14, end, h, "var(--line)");
    }
    if (lane) {  // the callouts to everyone: a gold diamond each, its words where they fit
      const ly = 22 + 9;
      body += kit.text(6, ly + 3.5, "Everyone", "t", 'font-size="11" font-weight="600"');
      for (const e of P.everyone) {
        const x = X(e);
        body += `<path d="M${x} ${ly - 5}l5 5l-5 5l-5-5z" fill="var(--gold)"${kit.tip(`${phaseAt(P, e)}: ${e.text}`)}/>`;
      }
      body += kit.labels(P.everyone.map((e) => [e, e.text]), X, ly + 3.5, "tm", P.everyone.map(X), right);
    }
    const sub = [D.difficulty, D.source && `saved ${D.source.replace(/^saved /, "")}`, "the note as it is, before the pull"];
    const head = `<div class="head"><h1>${kit.esc(D.boss)}: who does what</h1><span class="sub">${kit.esc(sub.filter(Boolean).join(" · "))}</span></div>`;
    const texts = (k) => [...new Set(P.rows.flatMap((r) => r.callouts.filter((t) => kindOf(t) === k)))];
    const keyOf = (k) => texts(k).length === 1 ? texts(k)[0] : `${k[0].toUpperCase()}${k.slice(1)} callouts`;  // its words, or its kind
    const keys = legend([...kinds.map((k) => [KIND[kinds.indexOf(k) % KIND.length], keyOf(k)]),
                         ...(lane ? [["var(--gold)", "callout to everyone"]] : [])])
      .replace('</div>', '<span><i style="background:none;border:1px solid var(--muted)"></i>planned cooldown: its icon</span></div>');
    const named = [...long].map(([hd, labels]) => `${kit.esc([...labels].join(", "))}: ${kit.esc(hd)}`).join("; ");
    return `<section data-panel class="note-board plan">${head}<div class="grid">${kit.svg(PLAN_W, h, body, "The raid note by player")}</div>${keys}`
      + `<div class="note">Under each name: the callouts to them, in their colour, then the groups they're in.${named ? ` ${named}.` : ""}</div></section>`;
  }

  const phaseAt = (P, i) => `${P.phases.length > 1 ? `P${i.phase} ` : ""}${kit.clock(i.at_s * 1000)}`;

  function render() {
    if (!D || !root) return;
    if (D.plan) { root.innerHTML = plan(D.plan); return; }
    const parts = [D.kicks && kicks(D.kicks), D.cooldowns && cooldowns(D.cooldowns), D.callouts && callouts(D.callouts)].filter(Boolean);
    root.innerHTML = parts.join("") || '<div class="note">The note has no kick assignments, planned cooldowns or callouts for this boss.</div>';
  }

  return { show, relayout: render };
})();
