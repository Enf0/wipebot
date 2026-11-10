// ---- which view to draw: the tool's result names it ({view, report, pull, ...}); a view can open another (Back) ----
(() => {
  const root = document.getElementById("app");
  // A host that hands back a stored result without its structuredContent (Claude Desktop on reopening a chat) still
  // has the tool's name and arguments: these views can be drawn again from them.
  const BY_TOOL = { pull_summary: "pull", player_pull: "player", player_night: "player_night", death_recap: "death", progress: "night" };
  // The tools only some of whose calls have a view (a ranking of players, a trend by pull: the server says which,
  // by the result's key). A host draws the page for every call, so for these it stays empty, at no height, while
  // the tool runs and for a call without a view: no placeholder, and no line saying there is nothing.
  const SOMETIMES = new Set(["breakdown", "mechanic_hits"]);
  const tool = () => app.context().toolInfo?.tool?.name;
  // The page takes no room (page.css: the root's data-empty) until the host has said which tool it is for, and for
  // good where there is nothing to draw
  const empty = (yes) => document.documentElement.toggleAttribute("data-empty", yes);
  const nothing = () => { empty(true); root.replaceChildren(); };
  // While a view loads, a placeholder in its shape (page.css: the replay's stage, else the card every view draws
  // inline). The view is known from the result's key, and while the tool still runs from the tool the host names.
  const SKELETON = document.getElementById("status").outerHTML;
  const waiting = (view) => {
    const status = root.querySelector("#status");
    if (status && view) status.dataset.shape = view;
  };
  let args = null, started = "", stack = [], current = null, told = false;

  // What went wrong, in one line where the view would be, with nothing of the placeholder left around it; with
  // `retry`, "Try again" does it (the usual cause, Warcraft Logs not answering in time, passes)
  function fail(text, retry) {
    let status = root.querySelector("#status");
    if (!status) { root.insertAdjacentHTML("beforeend", SKELETON); status = root.querySelector("#status"); }
    status.className = "failed";
    status.replaceChildren(kit.el('<span class="msg" id="status-msg"></span>'));
    status.firstChild.textContent = text;
    if (!retry) return;
    const again = kit.el("<button>Try again</button>");
    again.onclick = retry;
    status.append(again);
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
    waiting(view);
    const body = document.createElement("div");
    root.append(body);
    if (!views[view]) { fail(`Nothing to show for ${view}.`); return; }
    try {
      // the tool's own view (the first of the stack) also gets what the tool was asked: a night asked without a
      // boss is the whole night's
      await views[view].show(body, key, open, stack.length === 1 ? args : null);
      current = views[view];
      root.querySelector("#status")?.remove();
      stack[stack.length - 1].title = body.querySelector("h1")?.textContent || view;
      // The model is told what is on screen once it is no longer what it asked for: a view opened from another, and
      // from then on the way back too (each line replaces the last). The tool's own view, as it first shows, isn't.
      if ((stack.length > 1 || told) && current.seen) { told = true; kit.looking(current.seen(), key.report); }
    } catch (e) {
      body.remove();
      fail(`Couldn't load this view: ${e.message}`, () => go(view, key));
      app.log(`${view}: ${e.message}`);
    }
  }

  function open(view, key) { stack.push({ view, key }); go(view, key); }

  // A ranking or trend after the first of an answer (the server's `folded`: server._fold): one line saying what it
  // is, which opens it. Each call's card is a page of its own, so a card can't know of the others.
  function fold(view, key) {
    const line = kit.el('<div class="folded"><span class="what"></span><button class="primary">Show</button></div>');
    line.firstChild.textContent = key.label || "A ranking";
    line.lastChild.onclick = () => go(view, key);
    root.replaceChildren(line);
  }

  function start(result) {
    const sc = result && result.structuredContent;
    let key = sc && sc.report ? sc : null;
    if (!key && !(result && result.isError)) {  // a tool that failed has nothing to draw, from its arguments either
      const view = BY_TOOL[tool()];
      if (args && view) key = { view, report: args.report, pull: args.pull, boss: args.boss,
                                player: args.player, death: args.death, weeks: args.weeks };
    }
    if (!key && SOMETIMES.has(tool())) { nothing(); return; }  // a call without a view, or one that failed
    empty(false);
    if (!key) { fail(result && result.isError ? "The tool failed: nothing to show." : "Nothing to show here: ask again to see it."); return; }
    const view = key.view || (key.at_ms !== undefined ? "replay" : "pull");  // a stored result from before views
    const asked = JSON.stringify(key);
    if (asked === started) return;  // the same result again (a host re-rendering)
    started = asked;
    told = false;
    stack = [{ view, key }];
    if (key.folded && !app.picture() && app.context().displayMode !== "fullscreen") fold(view, key);
    else go(view, key);
  }

  app.on("input", (a) => { args = a; });
  app.on("result", start);
  app.on("cancelled", () => { if (current) return; if (SOMETIMES.has(tool())) nothing(); else fail("The tool call was cancelled."); });
  app.on("context", () => { if (current && current.relayout) current.relayout(); });
  app.on("teardown", () => { if (current && current.stop) current.stop(); });
  // Once the host has said which tool this is: the placeholder in its view's shape, or for a tool only some of
  // whose calls have a view, nothing until its result says so
  const known = () => {
    if (started) return;
    empty(SOMETIMES.has(tool()));
    waiting({ ...BY_TOOL, pull_map: "replay" }[tool()]);
  };
  app.connect({ name: "Wipebot", version: "2" }).then(known, known);
})();
