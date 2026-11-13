// ---- the replay: everyone's position over the whole pull on the room's map (pull_replay's data, maps.replay) ----
views.replay = (() => {
  const $ = (id) => document.getElementById(id);
  const UNKNOWN = -32768;
  let R = null, tracks = [], bossTracks = [], view = null, images = {}, t = 0, playing = false, speed = 1, hover = -1;
  let P = [];  // raid_plan's moments: the plan's spots laid over the room at each, and who was off theirs
  let loaded = "", report = "";

  const MARKUP = `
    <div class="head"><h1 id="rp-title"></h1><span id="rp-clock"></span><span id="rp-event"></span></div>
    <div id="rp-stage"><canvas id="rp-map" aria-label="Map of the boss room"></canvas>
      <div id="rp-boss" hidden><img alt=""><div><div class="who"><b></b><span></span></div><div class="bar"><i></i><span></span></div></div></div>
      <div id="rp-next" hidden><h3>Coming up</h3><ol></ol></div>
      <div id="rp-tip"></div></div>
    <div class="controls">
      <button id="rp-play" aria-label="Play">▶</button>
      <div id="rp-timeline" role="slider" tabindex="0" aria-label="Time in the pull"><div id="rp-track"></div><div id="rp-done"></div><div id="rp-phases"></div><div id="rp-thumb"></div></div>
      <button id="rp-speed" aria-label="Playback speed">1×</button>
      <button id="rp-full" aria-label="Open the replay in fullscreen" title="Fullscreen" hidden><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4" fill="none" stroke="currentColor" stroke-width="1.8"/></svg></button>
    </div>
    <div id="rp-moments" hidden></div>`;

  async function show(root, given) {
    const { plan, view: _, ...asked } = given;  // raid_plan's result carries the plan's moments; the replay is the pull's
    if (!asked.report) throw new Error("No pull to show.");
    const key = JSON.stringify(given);
    if (key === loaded && $("rp-map")) return;
    loaded = key;
    report = asked.report;
    P = plan || [];
    const data = await app.callTool("pull_replay", { report: asked.report, pull: asked.pull, boss: asked.boss,
                                                     at_ms: asked.at_ms || 0, players: asked.players || [] });
    root.insertAdjacentHTML("beforeend", MARKUP);
    wire();
    open(data);
  }

  function decode(b64) {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const dv = new DataView(bytes.buffer), out = new Int16Array(bytes.length / 2);
    for (let i = 0; i < out.length; i++) out[i] = dv.getInt16(i * 2, true);
    return out;
  }

  function image(path) {
    if (!images[path]) { const img = new Image(); img.onload = draw; img.src = R.img_base + path; images[path] = img; }
    return images[path];
  }

  function open(data) {
    R = data;
    tracks = R.players.map((p) => decode(p.track));
    bossTracks = R.bosses.map((b) => decode(b.track));
    view = null;
    $("rp-title").textContent = R.title;
    $("rp-timeline").querySelectorAll(".mark").forEach((m) => m.remove());
    phaseBar();
    for (const d of R.deaths) addMark(d.ms, "", `${d.name} died (${d.by})`);
    if (R.decided) addMark(R.decided.ms, "decided", `Wipe decided: ${R.decided.why}`);
    const row = $("rp-moments");
    row.replaceChildren(); row.hidden = !P.length;
    P.forEach((m) => {
      addMark(m.ms, "plan", momentText(m));
      const b = document.createElement("button");
      b.textContent = `${m.ability} ${clock(m.ms)}`;  // text only: names come from the log and the plan
      b.title = momentText(m);
      b.onclick = () => { setPlaying(false); seek(m.ms, true); stopped(); };
      row.appendChild(b);
    });
    image(R.map.image); R.bosses.forEach((b) => image(b.icon)); R.players.forEach((p) => image(p.icon));
    const boss = $("rp-boss");  // the boss's frame: its icon, name, the phase and its health
    boss.hidden = !R.health.length && !R.phases.length;
    if (R.bosses.length) boss.querySelector("img").src = R.img_base + R.bosses[0].icon;
    $("rp-next").hidden = !R.casts.length;
    seek(R.at_ms, true);
    fit();
    app.log(`opened at ${clock(t)} (asked ${clock(R.at_ms)}, ${R.title}, ${$("rp-map").clientWidth}x${$("rp-map").clientHeight})`);
  }

  // Inline, a button opens the replay in fullscreen where the host has it (opened from a pull's card it already is).
  // There the stage takes the height the frame leaves after everything else, at its 3:2 (page.css reads --rp-rest),
  // so play and the timeline stay in sight under it.
  function fit() {
    const stage = $("rp-stage"), full = app.context().displayMode === "fullscreen";
    if (!stage) return;
    $("rp-full").hidden = full || !app.canGoFullscreen();
    const waiting = document.getElementById("status");  // the placeholder, still there while this view opens
    // everything but the map: on a phone the stage holds the boss's frame and its next casts over the map too
    const rest = document.getElementById("app").offsetHeight - $("rp-map").offsetHeight - (waiting ? waiting.offsetHeight : 0);
    stage.style.setProperty("--rp-rest", `${rest}px`);
  }

  function addMark(ms, cls, label) {
    const m = document.createElement("div");
    m.className = "mark " + cls; m.style.left = (100 * ms / R.duration_ms) + "%"; m.title = label;
    $("rp-timeline").appendChild(m);
  }

  const DEAD = -2;  // a track's facing while dead: the position is where they fell
  function at(tr, ms) {  // [x, y, facing] at ms, between the samples on each side; null if unknown
    const f = ms / R.step_ms, i = Math.max(0, Math.min(Math.floor(f), tr.length / 3 - 1)), k = f - i;
    const a = i * 3, b = Math.min(i + 1, tr.length / 3 - 1) * 3;
    if (tr[a] === UNKNOWN) return tr[b] === UNKNOWN || k < 0.5 ? null : [tr[b], tr[b + 1], tr[b + 2]];
    if (tr[b] === UNKNOWN || tr[a + 2] === DEAD) return [tr[a], tr[a + 1], tr[a + 2]];
    const fa = tr[a + 2], fb = tr[b + 2];
    let face = fa;
    if (fa >= 0 && fb >= 0) { const d = ((fb - fa + 540) % 360) - 180; face = ((fa + d * k) % 360 + 360) % 360; }
    return [tr[a] + (tr[b] - tr[a]) * k, tr[a + 1] + (tr[b + 1] - tr[a + 1]) * k, face];
  }
  function isDead(i) { const p = at(tracks[i], t); return !!p && p[2] === DEAD; }

  const clock = kit.clock;

  // The plan's moment shown: the nearest within PLAN_HOLD_MS, so its spots stay while one scrubs around it.
  const PLAN_HOLD_MS = 4000;
  function moment() {
    let best = null;
    for (const m of P) if (Math.abs(m.ms - t) <= PLAN_HOLD_MS && (!best || Math.abs(m.ms - t) < Math.abs(best.ms - t))) best = m;
    return best;
  }
  function momentText(m) {
    return `${m.ability} ${clock(m.ms)}: ` + (m.off.length ? "off their spot: " + m.off.map((o) =>
      `${o.name} ${o.yd} yd` + (o.at ? ` (at ${o.at})` : "")).join(", ") : "everyone on their spot");
  }

  function seek(ms, jump) {
    t = Math.max(0, Math.min(ms, R.duration_ms));
    follow(jump);
    const pct = 100 * t / R.duration_ms;
    $("rp-thumb").style.left = pct + "%";
    const done = $("rp-done");  // the phase bar's played part is the whole bar, cut off where the pull is
    if (R.phases.length > 1) { done.style.width = ""; done.style.clipPath = `inset(0 ${100 - pct}% 0 0)`; }
    else { done.style.clipPath = ""; done.style.width = pct + "%"; }
    $("rp-clock").textContent = `${clock(t)} / ${clock(R.duration_ms)}`;
    const tl = $("rp-timeline");  // a slider in whole seconds, as the clock counts them, read out as a time
    tl.setAttribute("aria-valuemin", "0"); tl.setAttribute("aria-valuemax", Math.floor(R.duration_ms / 1000));
    tl.setAttribute("aria-valuenow", Math.floor(t / 1000)); tl.setAttribute("aria-valuetext", `${clock(t)} of ${clock(R.duration_ms)}`);
    const recent = R.deaths.filter((d) => d.ms <= t && t - d.ms < 4000).pop(), m = moment();
    $("rp-event").textContent = recent ? `${recent.name} died (${recent.by})`
      : (R.decided && t >= R.decided.ms && t - R.decided.ms < 4000 ? `Wipe decided: ${R.decided.why}`
         : m ? momentText(m) : "");
    $("rp-moments").querySelectorAll("button").forEach((b, n) => b.classList.toggle("on", P[n] === m));
    bossFrame();
    comingUp();
    draw();
  }

  // ---- phases, the boss's health and what it casts next ----
  // Each phase its own colour (the categorical four, in the order the phases first came), a phase the fight went back
  // to the colour it had; the played part full, the rest faded; its label under it where there is room.
  function phaseColors() {
    const order = [];
    R.phases.forEach((p) => { if (!order.includes(p.label)) order.push(p.label); });
    return (p) => `var(--cat-${(order.indexOf(p.label) % 4) + 1})`;
  }
  function phaseAt(ms) { let cur = null; for (const p of R.phases) if (p.ms <= ms) cur = p; return cur; }
  function phaseBar() {
    const tl = $("rp-timeline"), row = $("rp-phases");
    row.replaceChildren();
    tl.classList.toggle("phased", R.phases.length > 1);
    if (R.phases.length < 2) { $("rp-track").replaceChildren(); $("rp-done").replaceChildren(); return; }
    const color = phaseColors();
    const parts = R.phases.map((p, i) => ({ p, from: p.ms, to: i + 1 < R.phases.length ? R.phases[i + 1].ms : R.duration_ms }));
    for (const layer of [$("rp-track"), $("rp-done")]) {
      layer.replaceChildren(...parts.map(({ p, from, to }) => {
        const seg = document.createElement("i");
        seg.style.left = (100 * from / R.duration_ms) + "%"; seg.style.width = (100 * (to - from) / R.duration_ms) + "%";
        seg.style.background = color(p);
        return seg;
      }));
    }
    for (const { p, from, to } of parts) {
      const tag = document.createElement("span");  // text only: phase names come from Warcraft Logs
      tag.textContent = p.name ? `${p.label} ${p.name}` : p.label;
      tag.title = `${tag.textContent}${p.again ? " (again)" : ""}: ${clock(from)} to ${clock(to)}`;
      tag.style.left = (100 * from / R.duration_ms) + "%"; tag.style.maxWidth = (100 * (to - from) / R.duration_ms) + "%";
      tag.style.setProperty("--c", color(p));
      row.appendChild(tag);
    }
  }

  // The boss's health at ms, along its line (a new bar, the next phase's boss, starts where it jumps up)
  function healthAt(ms) {
    const h = R.health;
    if (!h.length) return null;
    if (ms <= h[0][0]) return h[0][1];
    for (let i = 1; i < h.length; i++) {
      if (h[i][0] < ms) continue;
      const [a, pa] = h[i - 1], [b, pb] = h[i];
      return pb - pa > 10 ? pa : pa + (pb - pa) * (ms - a) / Math.max(1, b - a);
    }
    return R.kill && ms >= R.duration_ms - 1000 ? 0 : h[h.length - 1][1];
  }
  function bossFrame() {
    const frame = $("rp-boss");
    if (frame.hidden) return;
    const ph = phaseAt(t), pct = healthAt(t), end = t >= R.duration_ms - 500;
    frame.querySelector(".who b").textContent = R.boss;  // the encounter, then the phase as the bar labels it
    frame.querySelector(".who span").textContent = ph && R.phases.length > 1 ? (ph.name ? `${ph.label} ${ph.name}` : ph.label) : "";
    if (ph && R.phases.length > 1) frame.style.setProperty("--c", phaseColors()(ph)); else frame.style.removeProperty("--c");
    const bar = frame.querySelector(".bar");
    bar.hidden = pct === null;
    if (pct !== null) {
      bar.querySelector("i").style.width = Math.max(0, Math.min(100, pct)) + "%";
      // at the very end, the % pull_summary states; before, the line's, to a tenth
      bar.querySelector("span").textContent = end && !R.kill && R.health_end ? R.health_end : end && R.kill ? "0%" : `${pct.toFixed(1)}%`;
    }
  }

  // The boss's next casts, as the log has them: those within NEXT_MS, soonest first, at most NEXT_SHOWN, each with a
  // bar running out as it comes; one just cast stays a moment as "now". Lorrgs' tracked casts are marked.
  const NEXT_MS = 20000, NEXT_SHOWN = 4, NOW_MS = 1500;
  let nextKey = "";
  function comingUp() {
    const box = $("rp-next");
    if (box.hidden) return;
    const shown = $("rp-stage").clientWidth < 480 ? 2 : NEXT_SHOWN;  // on a phone, two: the map stays in sight
    const soon = R.casts.filter((c) => c.ms - t > -NOW_MS && c.ms - t <= NEXT_MS).slice(0, shown);
    const key = soon.map((c) => c.ms + c.name).join("|") + (t >= R.duration_ms - 500 ? "end" : "");
    const list = box.querySelector("ol");
    if (key !== nextKey) {  // rebuild only when the casts shown change; the bars and seconds move every frame
      nextKey = key;
      list.replaceChildren(...soon.map((c) => {
        const li = document.createElement("li");
        if (c.tracked) li.className = "tracked";
        const img = document.createElement("img"); img.alt = "";
        if (c.icon) img.src = R.img_base + c.icon; else img.style.visibility = "hidden";
        const name = document.createElement("span"); name.className = "nm"; name.textContent = c.name;
        const left = document.createElement("span"); left.className = "in";
        const bar = document.createElement("i");
        li.append(bar, img, name, left);
        return li;
      }));
      if (!soon.length) { const li = document.createElement("li"); li.className = "none"; li.textContent = t >= R.duration_ms - 500 ? (R.kill ? "killed" : "the pull is over") : "no boss cast in the next 20 s"; list.appendChild(li); }
    }
    soon.forEach((c, n) => {
      const li = list.children[n], left = c.ms - t;
      li.classList.toggle("now", left <= 0);
      li.querySelector(".in").textContent = left <= 0 ? "now" : `${Math.ceil(left / 1000)} s`;
      li.querySelector("i").style.width = (left <= 0 ? 100 : 100 * (1 - left / NEXT_MS)) + "%";
    });
  }

  // The view follows the living, the boss and the plan's spots at the moment (at least LEAST yards across, at the stage's 3:2),
  // easing toward where it should be so playing doesn't jitter; a jump in time moves it at once.
  const LEAST_YD = 60, MARGIN_YD = 12;
  function target() {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    const add = (p) => { if (p) { x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]); } };
    R.players.forEach((p, i) => { if (!isDead(i) || p.focus) add(at(tracks[i], t)); });
    bossTracks.forEach((tr) => add(at(tr, t)));
    const m = moment(), per = R.unit_yd * 100;  // and the plan's spots, at one of its moments
    if (m) m.spots.forEach((s) => add([(s.x - R.map.origin[0]) / per, (s.y - R.map.origin[1]) / per]));
    if (x0 === Infinity) return view;
    const yd = 1 / R.unit_yd;
    let w = Math.max(x1 - x0 + 2 * MARGIN_YD * yd, LEAST_YD * yd), h = Math.max(y1 - y0 + 2 * MARGIN_YD * yd, LEAST_YD * yd / 1.5);
    if (w / h > 1.5) h = w / 1.5; else w = h * 1.5;
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    return { x0: cx - w / 2, x1: cx + w / 2, y0: cy - h / 2, y1: cy + h / 2 };
  }
  let easing = false;
  function follow(jump) {
    const goal = target();
    if (!goal) return;
    if (!view || jump) { view = goal; return; }
    const k = 0.12, next = {};
    let far = false;
    for (const key of ["x0", "x1", "y0", "y1"]) {
      next[key] = view[key] + (goal[key] - view[key]) * k;
      if (Math.abs(goal[key] - next[key]) > 0.5) far = true;
    }
    view = far ? next : goal;
    if (far && !easing) { easing = true; requestAnimationFrame(() => { easing = false; follow(false); draw(); }); }
  }

  // ---- drawing ----
  let dots = [];  // what's on screen, for hovering: {i, x, y, r}
  function draw() {
    if (!R || !view || !$("rp-map")) return;
    const c = $("rp-map"), dpr = window.devicePixelRatio || 1, W = c.clientWidth, H = c.clientHeight;
    if (!W) return;
    c.width = W * dpr; c.height = H * dpr;
    const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const s = W / (view.x1 - view.x0);
    const px = (x, y) => [(x - view.x0) * s, (view.y1 - y) * s];
    g.fillStyle = "#0b101e"; g.fillRect(0, 0, W, H);
    const map = images[R.map.image];
    if (map && map.complete && map.naturalWidth) {  // the map spans (0,0)-(size) in our units, north up
      const kx = map.naturalWidth / R.map.size[0], ky = map.naturalHeight / R.map.size[1];
      g.globalAlpha = 0.8;
      g.drawImage(map, view.x0 * kx, (R.map.size[1] - view.y1) * ky, (view.x1 - view.x0) * kx, (view.y1 - view.y0) * ky, 0, 0, W, H);
      g.globalAlpha = 1;
    }
    const r = Math.max(9, Math.min(15, W / 70));
    // the bosses and their facing
    R.bosses.forEach((boss, n) => {
      const b = at(bossTracks[n], t);
      if (!b) return;
      const [bx, by] = px(b[0], b[1]);
      if (b[2] >= 0) {
        const a = -b[2] * Math.PI / 180, reach = 12 / R.unit_yd * s;
        g.fillStyle = "rgba(245, 197, 66, .22)"; g.beginPath(); g.moveTo(bx, by);
        g.arc(bx, by, reach, a - Math.PI / 4, a + Math.PI / 4); g.closePath(); g.fill();
      }
      ring(g, bx, by, r * 1.6, "#f5c542", images[boss.icon], false);
      label(g, boss.name, bx, by - r * 1.6 - 6, "#f5c542", false);
    });
    drawPlan(g, px, s, r);
    // players: the dead first (under), the asked-about last (on top)
    dots = [];
    const order = R.players.map((p, i) => i).sort((i, j) =>
      (isDead(j) - isDead(i)) || (R.players[i].focus - R.players[j].focus));
    const spots = {};
    for (const i of order) { const pos = at(tracks[i], t); if (pos) spots[i] = px(pos[0], pos[1]); }
    spread(spots, r * 1.7);
    settle(spots);
    for (const i of order) {
      const p = R.players[i], pos = at(tracks[i], t);
      if (!pos) continue;
      const [x, y] = spots[i], dead = pos[2] === DEAD, rr = p.focus ? r * 1.25 : r;
      ring(g, x, y, rr, dead ? "#787c86" : p.color, images[p.icon], dead);
      dots.push({ i, x, y, r: rr, dead });
    }
    // names: the asked-about and the hovered first, then the living, then the dead; one that would cover another
    // is left out for now (hovering shows it)
    const taken = [];
    const rank = (d) => (R.players[d.i].focus || hover === d.i ? 0 : d.dead ? 2 : 1);
    for (const d of dots.slice().sort((a, b) => rank(a) - rank(b))) {
      const p = R.players[d.i], bold = p.focus || hover === d.i;
      g.font = `${bold ? 700 : 600} ${bold ? 13 : 11.5}px system-ui, -apple-system, "Segoe UI", sans-serif`;
      const w = g.measureText(p.name).width + 4, box = [d.x - w / 2, d.y + d.r + 5, w, 15];
      if (rank(d) > 0 && taken.some((b) => box[0] < b[0] + b[2] && b[0] < box[0] + box[2] && box[1] < b[1] + b[3] && b[1] < box[1] + box[3])) continue;
      taken.push(box);
      label(g, p.name, d.x, d.y + d.r + 12, d.dead ? "#9aa3b8" : p.color, bold);
    }
  }

  // The plan's spots at the moment shown, under the players: a dot in its player's class colour (grey when open); for a
  // player off theirs, a red ring of the tolerance around it and a red line to where they are.
  function drawPlan(g, px, s, r) {
    const m = moment();
    if (!m) return;
    const per = R.unit_yd * 100;  // the log's coordinates (hundredths of a yard) per replay unit
    const color = {}, index = {};
    R.players.forEach((p, i) => { color[p.name] = p.color; index[p.name] = i; });
    for (const spot of m.spots) {
      const [x, y] = px((spot.x - R.map.origin[0]) / per, (spot.y - R.map.origin[1]) / per);
      if (spot.off && index[spot.player] !== undefined) {
        const pos = at(tracks[index[spot.player]], t);
        g.strokeStyle = "rgba(255, 92, 92, .9)"; g.lineWidth = 2;
        g.beginPath(); g.arc(x, y, m.tolerance_yd / R.unit_yd * s, 0, 2 * Math.PI); g.stroke();
        if (pos && pos[2] !== DEAD) {
          const [qx, qy] = px(pos[0], pos[1]);
          g.setLineDash([5, 4]); g.beginPath(); g.moveTo(x, y); g.lineTo(qx, qy); g.stroke(); g.setLineDash([]);
        }
      }
      g.beginPath(); g.arc(x, y, r * 0.45, 0, 2 * Math.PI);
      if (spot.player) { g.fillStyle = color[spot.player] || "#969caa"; g.fill(); }
      g.lineWidth = 1.5; g.strokeStyle = spot.player ? "rgba(11, 16, 30, .85)" : "#969caa"; g.stroke();
    }
  }

  // Markers that would cover each other are pushed apart, each by half of how far they overlap: where a marker is
  // drawn then changes little as the players move (placing them one by one, each on the first free spot of a spiral,
  // made a stack's markers hop between spots many times a second).
  function spread(spots, gap) {
    const keys = Object.keys(spots), pos = keys.map((k) => spots[k].slice());
    for (let round = 0, moved = true; round < 8 && moved; round++) {
      moved = false;
      for (let a = 0; a < pos.length; a++) for (let b = a + 1; b < pos.length; b++) {
        let dx = pos[b][0] - pos[a][0], dy = pos[b][1] - pos[a][1], d = Math.hypot(dx, dy);
        if (d >= gap) continue;
        if (d < 0.01) { const turn = (a * 7 + b) * 2.4; dx = Math.cos(turn); dy = Math.sin(turn); d = 1; }  // on one spot
        const push = (gap - d) / 2;
        pos[a][0] -= dx / d * push; pos[a][1] -= dy / d * push; pos[b][0] += dx / d * push; pos[b][1] += dy / d * push;
        moved = true;
      }
    }
    keys.forEach((k, n) => { spots[k] = [pos[n][0], pos[n][1], pos[n][0] - spots[k][0], pos[n][1] - spots[k][1]]; });
  }

  // How far each marker is drawn from where its player stood eases toward what spread() gives (in SETTLE_PUSH_MS),
  // so a marker pushed aside glides there; after a pause in drawing (the first frame, a jump in time) it is there at
  // once. Draws again until every marker has arrived.
  const SETTLE_PUSH_MS = 150;
  let pushed = {}, drawnAt = 0, gliding = false;
  function settle(spots) {
    const now = performance.now(), dt = now - drawnAt, k = dt > 400 ? 1 : 1 - Math.exp(-dt / SETTLE_PUSH_MS);
    drawnAt = now;
    const next = {};
    let far = false;
    for (const i of Object.keys(spots)) {
      const [x, y, dx, dy] = spots[i], was = pushed[i] || (k === 1 ? [dx, dy] : [0, 0]);
      const o = [was[0] + (dx - was[0]) * k, was[1] + (dy - was[1]) * k];
      if (Math.abs(dx - o[0]) > 0.5 || Math.abs(dy - o[1]) > 0.5) far = true; else { o[0] = dx; o[1] = dy; }
      next[i] = o; spots[i] = [x - dx + o[0], y - dy + o[1]];
    }
    pushed = next;
    if (far && !gliding) { gliding = true; requestAnimationFrame(() => { gliding = false; draw(); }); }
  }

  function ring(g, x, y, r, color, img, dead) {
    g.save();
    g.beginPath(); g.arc(x, y, r, 0, 2 * Math.PI); g.closePath();
    g.fillStyle = "#0b101e"; g.fill();
    if (img && img.complete && img.naturalWidth) {
      g.save(); g.clip(); if (dead) g.globalAlpha = 0.45;
      g.drawImage(img, x - r, y - r, 2 * r, 2 * r); g.restore();
    }
    g.lineWidth = Math.max(2, r / 5); g.strokeStyle = color; g.stroke();
    if (dead) {
      g.strokeStyle = "#ff5c5c"; g.lineWidth = 2.5; const k = r * 0.6;
      g.beginPath(); g.moveTo(x - k, y - k); g.lineTo(x + k, y + k); g.moveTo(x + k, y - k); g.lineTo(x - k, y + k); g.stroke();
    }
    g.restore();
  }

  // A class's colour as a name on the stage, which is dark in either theme: page.css's dark ink (a death knight's red
  // a lighter red). A browser without relative colours refuses it, and the name keeps the colour set before it.
  const ink = (color) => `oklch(from ${color} max(l, .72) c h)`;

  function label(g, text, x, y, color, bold) {
    g.font = `${bold ? 700 : 600} ${bold ? 13 : 11.5}px system-ui, -apple-system, "Segoe UI", sans-serif`;
    g.textAlign = "center"; g.textBaseline = "middle";
    g.lineWidth = 3.5; g.strokeStyle = "rgba(11, 16, 30, .85)"; g.strokeText(text, x, y);
    g.fillStyle = color; g.fillStyle = ink(color); g.fillText(text, x, y);
  }

  // ---- playing, seeking and hovering ----
  let last = 0;
  function tick(now) {
    if (!playing) return;
    if (last) seek(t + (now - last) * speed);
    last = now;
    if (t >= R.duration_ms) { setPlaying(false); return; }
    requestAnimationFrame(tick);
  }
  function setPlaying(on) {
    playing = on; last = 0;
    if (!$("rp-play")) return;
    $("rp-play").textContent = on ? "❚❚" : "▶"; $("rp-play").setAttribute("aria-label", on ? "Pause" : "Play");
    if (on) { if (t >= R.duration_ms) seek(Math.max(0, R.at_ms - 5000), true); requestAnimationFrame(tick); }
  }

  // What the model is told is on screen (kit.looking): the moment the user stopped at (a pause, a scrub, one of the
  // plan's moments) with what the replay says of it, once they have stopped there, never a moment passing by in play
  const seen = () => `the replay of ${R.title} at ${clock(t)}` + ($("rp-event").textContent ? `: ${$("rp-event").textContent}` : "");
  const SETTLE_MS = 600;
  let settling = 0;
  function stopped() {
    clearTimeout(settling);
    settling = setTimeout(() => { if (!playing && R && $("rp-map")) kit.looking(seen(), report); }, SETTLE_MS);
  }

  function wire() {
    new ResizeObserver(() => draw()).observe($("rp-stage"));
    // hover: who, spec, distance to the boss
    $("rp-map").addEventListener("pointermove", (e) => {
      const rect = e.target.getBoundingClientRect(), x = e.clientX - rect.left, y = e.clientY - rect.top;
      const hit = dots.slice().reverse().find((d) => Math.hypot(d.x - x, d.y - y) <= d.r + 4);
      const tip = $("rp-tip");
      hover = hit ? hit.i : -1;
      if (!hit) { tip.style.display = "none"; draw(); return; }
      const p = R.players[hit.i], pos = at(tracks[hit.i], t);
      let near = null;  // the closest boss: [yards, name]
      R.bosses.forEach((boss, n) => {
        const b = at(bossTracks[n], t);
        const yd = pos && b ? Math.hypot(pos[0] - b[0], pos[1] - b[1]) * R.unit_yd : null;
        if (yd !== null && (!near || yd < near[0])) near = [yd, boss.name];
      });
      const death = isDead(hit.i) ? R.deaths.filter((d) => d.name === p.name && d.ms <= t).pop() : null;
      const name = document.createElement("b");  // text only: names come from the log
      name.style.color = p.color; name.style.color = ink(p.color); name.textContent = p.name;
      tip.replaceChildren(name, ` · ${p.spec.replace(/([a-z])([A-Z])/g, "$1 $2")}`, document.createElement("br"),
        death ? `died at ${clock(death.ms)} (${death.by})` : near ? `${Math.round(near[0])} yd from ${near[1]}` : "");
      tip.style.display = "block";
      tip.style.left = Math.min(x + 14, rect.width - tip.offsetWidth - 4) + "px";
      tip.style.top = e.target.offsetTop + Math.max(4, y - tip.offsetHeight - 10) + "px";  // the map may start lower in the stage
      draw();
    });
    $("rp-map").addEventListener("pointerleave", () => { $("rp-tip").style.display = "none"; hover = -1; draw(); });
    $("rp-play").onclick = () => { setPlaying(!playing); stopped(); };
    $("rp-speed").onclick = () => { speed = speed === 1 ? 2 : speed === 2 ? 4 : 1; $("rp-speed").textContent = speed + "×"; };
    $("rp-full").onclick = () => app.requestDisplayMode("fullscreen").then(fit);
    const tl = $("rp-timeline");
    function fromPointer(e) {
      const rect = tl.getBoundingClientRect(); let ms = (e.clientX - rect.left) / rect.width * R.duration_ms;
      // close to a death or the decision: snap to just before it
      for (const d of [...R.deaths.map((d) => d.ms), ...(R.decided ? [R.decided.ms] : [])])
        if (Math.abs(d - ms) / R.duration_ms * rect.width < 6) { ms = d - 500; break; }
      seek(ms, true);
      stopped();
    }
    tl.addEventListener("pointerdown", (e) => { tl.setPointerCapture(e.pointerId); fromPointer(e); });
    tl.addEventListener("pointermove", (e) => { if (tl.hasPointerCapture(e.pointerId)) fromPointer(e); });
    tl.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight") seek(t + (e.shiftKey ? 10000 : 1000));
      else if (e.key === "ArrowLeft") seek(t - (e.shiftKey ? 10000 : 1000));
      else if (e.key === " ") setPlaying(!playing);
      else return;
      stopped();
      e.preventDefault();
    });
  }

  return { show, relayout: () => { fit(); draw(); }, stop: () => { clearTimeout(settling); setPlaying(false); }, seen };
})();
