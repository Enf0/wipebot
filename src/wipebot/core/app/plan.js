// ---- raid_plan with `show`: each plan as raidstrats.gg draws it, its embed (embed-doc) with the names saved on its
// numbered spots and the asked raider's spots lit, where the host lets the page frame raidstrats.gg (ChatGPT does,
// Claude doesn't: hostCapabilities.sandbox.csp.frameDomains says); elsewhere a line with the plan's link ----
views.plan = (() => {
  const ORIGIN = "https://raidstrats.gg";
  const framed = () => ((app.capabilities().sandbox || {}).csp || {}).frameDomains?.some((d) => d.replace(/\/$/, "") === ORIGIN);
  let frame = null, lit = [];

  // The embed says when its plan has loaded; then the asked raider's spots are lit (they stay lit across scenes)
  window.addEventListener("message", (e) => {
    if (e.origin !== ORIGIN || !frame || e.source !== frame.contentWindow) return;
    if (e.data && e.data.type === "playerNamesLoaded" && lit.length)
      frame.contentWindow.postMessage({ type: "highlightPlayers", indices: lit }, ORIGIN);
  });

  async function show(into, key) {
    const plans = key.plans || [];
    if (!plans.length) throw new Error("no plan on raidstrats.gg");
    let at = 0;
    const draw = () => {
      const p = plans[at];
      const tabs = plans.length > 1 ? `<div class="pl-tabs">${plans.map((q, i) =>
        `<button data-i="${i}"${i === at ? ' class="primary"' : ""}>${kit.esc(q.name)}</button>`).join("")}</div>` : "";
      const out = (text) => `<a class="out" href="${kit.esc(p.embed)}" target="_blank" rel="noopener noreferrer">${text}</a>`;
      const frame_ = framed() && !app.picture();
      into.innerHTML = `<div class="head"><h1>${kit.esc(p.name)}</h1><span class="sub">${kit.esc(p.boss || "")}</span>${frame_ ? out("raidstrats.gg") : ""}</div>`
        + tabs + (frame_ ? '<div class="pl-box"></div>'
          : `<p class="lead-more">The plan as raidstrats.gg draws it, with the names saved on its spots.</p>`
            + `<div class="actions">${out("Open on raidstrats.gg")}</div>`);
      into.querySelectorAll(".pl-tabs button").forEach((b) => { b.onclick = () => { at = +b.dataset.i; draw(); }; });
      const box = into.querySelector(".pl-box");
      frame = null; lit = p.highlight || [];
      if (!box) return;
      frame = document.createElement("iframe");
      frame.title = `${p.name} on raidstrats.gg`;
      frame.referrerPolicy = "no-referrer";
      frame.allow = "fullscreen";
      frame.src = p.embed;
      box.append(frame);
    };
    draw();
  }

  return { show, seen: () => "a raid plan on raidstrats.gg" };
})();
