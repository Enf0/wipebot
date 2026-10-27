// ---- which view to draw: the tool's result names it ({view, report, pull, ...}); a view can open another (Back) ----
(() => {
  const root = document.getElementById("app");
  // A host that hands back a stored result without its structuredContent (Claude Desktop on reopening a chat) still
  // has the tool's name and arguments: these views can be drawn again from them.
  const BY_TOOL = { pull_summary: "pull", player_pull: "player", player_night: "player_night", death_recap: "death", progress: "night" };
  const SKELETON = document.getElementById("status").outerHTML;
  let args = null, started = "", stack = [], current = null;

  function fail(text, where = root) {
    let status = where.querySelector("#status");
    if (!status) { where.insertAdjacentHTML("beforeend", SKELETON); status = where.querySelector("#status"); }
    status.classList.add("failed");
    status.querySelector("#status-msg").textContent = text;
  }

  async function go(view, key) {
    if (current && current.stop) current.stop();
    current = null;
    root.replaceChildren();
    if (stack.length > 1) {
      const prev = stack[stack.length - 2];
      const back = kit.el(`<button class="back">← ${kit.esc(prev.title || "Back")}</button>`);
      back.onclick = () => { stack.pop(); const p = stack[stack.length - 1]; go(p.view, p.key); };
      const bar = kit.el('<div class="head"></div>');
      bar.append(back);
      root.append(bar);
    }
    root.insertAdjacentHTML("beforeend", SKELETON);
    const body = document.createElement("div");
    root.append(body);
    if (!views[view]) { fail(`Nothing to show for ${view}.`); return; }
    try {
      await views[view].show(body, key, open);
      current = views[view];
      root.querySelector("#status")?.remove();
      stack[stack.length - 1].title = body.querySelector("h1")?.textContent || view;
    } catch (e) {
      fail(`Couldn't load this view: ${e.message}`);
      app.log(`${view}: ${e.message}`);
    }
  }

  function open(view, key) { stack.push({ view, key }); go(view, key); }

  function start(result) {
    const sc = result && result.structuredContent;
    let key = sc && sc.report ? sc : null;
    if (!key) {
      const tool = app.context().toolInfo?.tool?.name;
      if (args && BY_TOOL[tool]) key = { view: BY_TOOL[tool], report: args.report, pull: args.pull, boss: args.boss,
                                         player: args.player, death: args.death, weeks: args.weeks };
    }
    if (!key) { fail(result && result.isError ? "The tool failed: nothing to show." : "Nothing to show here: ask again to see it."); return; }
    const view = key.view || (key.at_ms !== undefined ? "replay" : "pull");  // a stored result from before views
    const asked = JSON.stringify(key);
    if (asked === started) return;  // the same result again (a host re-rendering)
    started = asked;
    stack = [{ view, key }];
    go(view, key);
  }

  app.on("input", (a) => { args = a; });
  app.on("result", start);
  app.on("cancelled", () => { if (!current) fail("The tool call was cancelled."); });
  app.on("context", () => { if (current && current.relayout) current.relayout(); });
  app.on("teardown", () => { if (current && current.stop) current.stop(); });
  app.connect({ name: "Wipebot", version: "2" });
})();
