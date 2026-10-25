// ---- one death: the hits in the seconds before it with their health after each, the healing they got, the killing
// blow (or that it killed outright), their defensives then, and who died with them (death_recap's facts) ----
views.death = (() => {
  let D = null, root = null, open = null, drawnWidth = 0;
  const COLORS = ["#e07a5f", "#7fb069", "#c9b458", "#5fb3d4", "#d48ac7", "#b98cf5"];  // the abilities, most damage first
  const SHOWN = 5;  // abilities named in the legend; the rest are "other"

  async function show(into, key, opener) {
    root = into; open = opener;
    D = await app.callTool("view_data", { view: "death", report: key.report, pull: key.pull, boss: key.boss,
                                          player: key.player, death: key.death || 1 });
    render();
    new ResizeObserver(() => { if (root === into && Math.abs(into.clientWidth - drawnWidth) > 24) render(); }).observe(into);
  }

  // Each ability's colour, by the damage it did in the window (absorbed included)
  function palette() {
    const total = new Map();
    for (const h of D.hits) total.set(h.by, (total.get(h.by) || 0) + h.amount + h.absorbed);
    const order = [...total.entries()].filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([n]) => n);
    const color = new Map(order.slice(0, SHOWN).map((n, i) => [n, COLORS[i]]));
    return { order, color: (n) => color.get(n) || "var(--muted)" };
  }

  function stats() {
    const ready = (D.defensives || []).filter((d) => d.state !== "cooldown");
    const down = (D.defensives || []).filter((d) => d.state === "cooldown");
    const last = D.hits.filter((h) => h.overkill).pop();
    return kit.stats([
      ["Killing blow", D.by, "dead", D.outright ? "killed outright: no hit logged" : last ? `${kit.short(last.overkill)} overkill` : ""],
      D.defensives == null ? null
        : ready.length ? ["Ready, not used", ready.map((d) => d.name).join(", "), "decided",
                          ready.some((d) => d.state === "unused") ? "not used this pull: ready if talented" : "off cooldown"]
        : ["Defensives", down.length ? "on cooldown" : "none listed", "", down.map((d) => `${d.name} at ${kit.clock(d.last_ms)}`).join(", ")],
      ["Healthstones", D.stones ? `${D.stones} used` : "none used", "", "before the death"],
      D.with.length ? ["Died with", `${D.with.length} within 3 s`, "", D.with.slice(0, 3).map((w) => w.name).join(", ")] : null,
    ]);
  }

  function verdict() {
    if (D.outright) return `<ul class="notes"><li class="fix"><span class="mk">!</span>${kit.esc(D.by)} killed outright at `
      + `${kit.esc(lastHp())}% health: it wasn't damage that outpaced the healing.</li></ul>`;
    if (!D.saved_by.length) return "";
    return `<ul class="notes"><li class="good"><span class="mk">+</span>Used in time, ${kit.esc(D.saved_by.join(" or "))} `
      + `would have kept them alive.</li></ul>`;
  }

  const lastHp = () => { const h = D.hits.filter((x) => x.hp != null).pop(); return h ? h.hp : "?"; };

  function render() {
    if (!D || !root) return;
    const full = app.context().displayMode === "fullscreen", picture = app.picture();
    drawnWidth = root.clientWidth;
    const width = Math.max(300, drawnWidth || 640), p = D.player;
    const sub = [`${p.spec} ${p.class}`, D.title, `died ${kit.clock(D.at_ms)}`, D.fallout ? "after the wipe was decided" : null,
                 D.deaths > 1 ? `death ${D.death} of ${D.deaths}` : null].filter(Boolean).join(" · ");
    const head = `<div class="head"><h1 style="color:${kit.esc(p.color)}">${kit.esc(p.name)}</h1><span class="sub">${kit.esc(sub)}</span></div>`;
    const card = head + stats() + verdict() + chart(full ? Math.max(560, width) : width, full);
    if (picture) { root.innerHTML = card; return; }
    root.innerHTML = card + (full ? hitList() : "")
      + `<div class="actions">${app.canGoFullscreen() && !full ? '<button class="primary" id="dv-open">Open death</button>' : ""}`
      + `<button id="dv-pull">Their pull</button></div>`;
    const o = root.querySelector("#dv-open");
    if (o) o.onclick = () => app.requestDisplayMode("fullscreen").then(render);
    root.querySelector("#dv-pull").onclick = () => open("player", { report: D.report, pull: D.pull, boss: D.boss, player: p.name });
  }

  // The window before the death: each hit a bar (what got through, solid; what was absorbed, light), their health
  // after it (the line), the healing they got each second (green), and the killing blow at the right edge
  function chart(W, lanes) {
    const { order, color } = palette();
    const L = 44, R = 44, T = 18, plotH = lanes ? 200 : 130, from = D.at_ms - D.window_ms;
    const x = (ms) => L + (ms - from) / D.window_ms * (W - L - R);
    const heal = D.healing.filter(([ms]) => ms + 1000 > from);
    const max = kit.niceMax(Math.max(1, ...D.hits.map((h) => h.amount + h.absorbed), ...heal.map(([, v]) => v)));
    const y = (v) => T + plotH - v / max * plotH, yH = (pct) => T + plotH - pct / 100 * plotH;
    let o = kit.line(L, T + plotH, W - R, T + plotH, "var(--line)");
    o += kit.text(L - 6, T + 8, kit.short(max), "tm", 'text-anchor="end" font-size="10"') + kit.text(L - 6, T + plotH, "0", "tm", 'text-anchor="end" font-size="10"');
    o += kit.text(W - R + 6, T + 8, "100%", "tm", 'font-size="10"') + kit.text(W - R + 6, T + plotH, "0%", "tm", 'font-size="10"');
    for (let s = 0; s <= D.window_ms; s += 2000) {
      const ms = from + s;
      o += kit.line(x(ms), T, x(ms), T + plotH, "var(--line)", 'opacity=".5"');
      o += kit.text(x(ms), T + plotH + 14, kit.clock(ms), "tm", 'text-anchor="middle" font-size="10"');
    }
    const sec = (W - L - R) / (D.window_ms / 1000);
    for (const [ms, v] of heal) {
      const x0 = Math.max(L, x(ms)), x1 = Math.min(W - R, x(ms + 1000));
      if (x1 > x0) o += `<rect x="${x0 + 1}" y="${y(v)}" width="${Math.max(1, x1 - x0 - 2)}" height="${T + plotH - y(v)}" fill="var(--green)" opacity=".22"${kit.tip(`${kit.clock(ms)}: +${kit.short(v)} healing`)}/>`;
    }
    const bw = Math.max(3, Math.min(9, sec / 5));
    for (const h of D.hits) {
      const cx = x(h.ms), c = color(h.by);
      const tip = `${kit.clock(h.ms)} ${h.by}: ${kit.short(h.amount)}` + (h.absorbed ? ` (${kit.short(h.absorbed)} absorbed)` : "")
        + (h.overkill ? `, overkill ${kit.short(h.overkill)}` : "") + (h.hp != null ? ` → ${h.hp}% health` : "");
      if (h.absorbed) o += `<rect x="${cx - bw / 2}" y="${y(h.amount + h.absorbed)}" width="${bw}" height="${y(h.amount) - y(h.amount + h.absorbed)}" fill="${c}" opacity=".35"/>`;
      if (h.amount) o += `<rect x="${cx - bw / 2}" y="${y(h.amount)}" width="${bw}" height="${T + plotH - y(h.amount)}" fill="${c}"/>`;
      o += kit.hit(cx - 5, T, 10, plotH, tip);
    }
    // health after each hit; it drops to 0 at the death unless it killed outright (no hit: the line stops)
    const pts = D.hits.filter((h) => h.hp != null).map((h) => [x(h.ms), yH(h.hp)]);
    if (!D.outright && pts.length) pts.push([x(D.at_ms), yH(0)]);
    if (pts.length > 1) o += `<path d="${pts.map(([px, py], i) => `${i ? "L" : "M"}${px} ${py}`).join(" ")}" fill="none" stroke="var(--ink)" stroke-width="2" stroke-linejoin="round"/>`;
    for (const [px, py] of pts) o += `<circle cx="${px}" cy="${py}" r="2.2" fill="var(--ink)"/>`;
    // the killing blow
    const kx = x(D.at_ms);
    o += kit.line(kx, T - 6, kx, T + plotH, "var(--dead)", 'stroke-width="1.8" stroke-dasharray="4 3"');
    o += kit.icon(D.img_base, D.icon, kx - 22, T - 16, 14, D.by);
    o += kit.text(kx - (D.icon && D.img_base ? 26 : 4), T - 5, D.outright ? `${D.by}: killed outright` : `killed by ${D.by}`, "td", 'text-anchor="end" font-size="10.5" font-weight="600"');
    // the legend: the abilities by damage, then healing and health
    const keys = order.slice(0, SHOWN).map((n) => {
      const ic = D.hits.find((h) => h.by === n)?.icon;
      return [(lx, ly) => (kit.icon(D.img_base, ic, lx, ly - 10, 12) || `<rect x="${lx}" y="${ly - 9}" width="10" height="10" rx="2" fill="${color(n)}"/>`)
        + `<rect x="${lx}" y="${ly + 2}" width="12" height="2" fill="${color(n)}"/>`, n];
    });
    if (order.length > SHOWN) keys.push([(lx, ly) => `<rect x="${lx}" y="${ly - 9}" width="10" height="10" rx="2" fill="var(--muted)"/>`, "other"]);
    keys.push([(lx, ly) => `<rect x="${lx}" y="${ly - 9}" width="10" height="10" rx="2" fill="var(--green)" opacity=".35"/>`, "healing received /s"]);
    keys.push([(lx, ly) => `<line x1="${lx}" y1="${ly - 4}" x2="${lx + 12}" y2="${ly - 4}" stroke="var(--ink)" stroke-width="2"/>`, "health after each hit"]);
    let lx = 6, ly = T + plotH + 36;
    for (const [mark, name] of keys) {
      const w = 18 + kit.width(name, 10) + 14;
      if (lx > 6 && lx + w > W) { lx = 6; ly += 18; }
      o += mark(lx, ly) + kit.text(lx + 17, ly, name, "tm", 'font-size="10"');
      lx += w;
    }
    return kit.svg(W, ly + 8, o, `${D.player.name}'s death: the hits and their health in the ${D.window_ms / 1000} s before it`);
  }

  function hitList() {
    const rows = D.hits.map((h) => `<tr><td>${kit.clock(h.ms)}</td><td>${kit.iconImg(D.img_base, h.icon)}${kit.esc(h.by)}</td>`
      + `<td>${kit.short(h.amount)}</td><td>${h.absorbed ? kit.short(h.absorbed) : ""}</td><td>${h.hp != null ? `${h.hp}%` : ""}</td></tr>`).join("");
    const defs = (D.defensives || []).map((d) => `<li><span>${kit.iconImg(D.img_base, d.icon)}${kit.esc(d.name)}</span>`
      + `<span class="count ${d.state === "cooldown" ? "" : "short"}">${d.state === "cooldown" ? `on cooldown (${kit.clock(d.last_ms)})` : d.state === "ready" ? "ready" : "not used this pull"}</span></li>`).join("");
    const died = D.with.map((w) => `<li><span>${kit.esc(w.name)}</span><span class="count">${kit.clock(w.ms)}</span><span class="times">${kit.esc(w.by)}</span></li>`).join("");
    return `<div class="full"><div><div class="section-title">Hits in the ${D.window_ms / 1000} s before the death</div>`
      + `<div class="scroll-x"><table class="pulls"><thead><tr><th>Time</th><th>Ability</th><th>Damage</th><th>Absorbed</th><th>Health after</th></tr></thead><tbody>${rows}</tbody></table></div></div>`
      + `<div>${defs ? `<div class="panel"><div class="section-title" style="margin-top:2px">Personal defensives</div><ul class="cds">${defs}</ul></div>` : ""}`
      + (died ? `<div class="panel"><div class="section-title" style="margin-top:2px">Died within 3 s</div><ul class="cds">${died}</ul></div>` : "")
      + `</div></div>`;
  }

  return { show, relayout: render };
})();
