// ---- which view to draw: the tool's result names it ({view, report, pull, ...}); a view can open another (Back) ----
(() => {
  const root = document.getElementById("app");
  // A host that hands back a stored result without its structuredContent (Claude Desktop on reopening a chat) still
  // has the tool's name and arguments: these views can be drawn again from them.
  const BY_TOOL = { pull_summary: "pull", player_pull: "player", player_night: "player_night", death_recap: "death", progress: "night" };
  // The tools only some of whose calls have a view (a ranking of players, a trend by pull, a plan as it is: the server says which,
  // by the result's key). A host draws the page for every call, so for these it stays empty, at no height, while
  // the tool runs and for a call without a view: no placeholder, and no line saying there is nothing.
  const SOMETIMES = new Set(["breakdown", "mechanic_hits", "raid_plan"]);
  const tool = () => app.context().toolInfo?.tool?.name;
  // A host need not say which tool the page is for (hostContext.toolInfo is optional): then it may be one of these,
  // and the page draws only what a result's key names.
  const sometimes = () => !tool() || SOMETIMES.has(tool()) || !(tool() in BY_TOOL || tool() === "pull_map");
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
    form = null;
    kit.report = !sent && !stack[0].key.no_report ? reporter(view, key, body) : null;
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
      if (kit.report && app.canReport() && view !== "replay" && !body.querySelector(".report-open")) root.append(kit.el(REPORT_LINE));
    } catch (e) {
      body.remove();
      fail(`Couldn't load this view: ${e.message}`, () => go(view, key));
      app.log(`${view}: ${e.message}`);
    }
  }

  // "Report a problem", where the server takes reports (a view's data says can_report; remote.py's report_answer):
  // a quiet button in a card's row of buttons (kit.card draws it while kit.report is set), else a line of its own,
  // which opens a form under the view. The user picks what kind of problem and writes it, and nothing goes before
  // their Send. Where the host takes a message, it goes into the chat (Claude puts it in the composer to send,
  // ChatGPT sends it) and the model calls report_answer with the question and its answer word for word; elsewhere
  // the page calls report_answer itself, with the card's own text as the answer.
  const KINDS = [["wrong", "Something's wrong"], ["missing", "Something's missing"], ["slow", "Too slow"]];
  let form = null, sent = false;
  document.addEventListener("click", (e) => {
    if (e.target.closest && e.target.closest(".report-open") && kit.report) kit.report();
  });

  function reporter(view, key, body) {
    return () => {
      if (form && form.isConnected) { form.querySelector("textarea").focus(); return; }
      form = kit.el(`<div class="report-form" role="group" aria-label="Report a problem">
        <label for="rp-what">What's wrong with this, or what do you disagree with?</label>
        <div class="kinds" role="radiogroup" aria-label="What kind of problem">${KINDS.map(([k, label], i) =>
          `<button type="button" role="radio" data-kind="${k}" aria-checked="${i === 0}">${label}</button>`).join("")}</div>
        <textarea id="rp-what" rows="3" maxlength="3000" placeholder="Say what's off, and what you think is right."></textarea>
        <p class="report-who">${app.canSendMessage() ? "Goes into the chat, and from there to the people who run Wipebot with your question, the answer and your Warcraft Logs name." : "Goes to the people who run Wipebot, with what this card shows and your Warcraft Logs name."}</p>
        <div class="report-row"><button type="button" class="primary report-send" disabled>Send</button><button type="button" class="report-cancel">Cancel</button><span class="report-msg" role="status"></span></div>
      </div>`);
      let kind = "wrong";
      const what = form.querySelector("textarea"), send = form.querySelector(".report-send"), msg = form.querySelector(".report-msg");
      form.querySelectorAll("[data-kind]").forEach((b) => { b.onclick = () => {
        kind = b.dataset.kind;
        form.querySelectorAll("[data-kind]").forEach((o) => o.setAttribute("aria-checked", String(o === b)));
      }; });
      what.oninput = () => { send.disabled = !what.value.trim(); };
      const alone = root.querySelector(".report.alone");  // the line, which the form stands in for while open
      form.querySelector(".report-cancel").onclick = () => { form.remove(); form = null; if (alone) alone.hidden = false; };
      // a click, not a form's submit: a host's sandboxed frame may not allow forms
      send.onclick = async () => {
        const title = body ? body.querySelector("h1")?.textContent || view : "";
        const problem = what.value.trim();
        const words = KINDS.find(([k]) => k === kind)[1].toLowerCase();
        send.disabled = true; msg.textContent = "Sending…";
        try {
          let thanks = "Sent to the people who run Wipebot. Thank you!";
          if (app.canSendMessage()) {
            await app.say(`Please send Wipebot's maintainers a report on this answer (${words}${title ? `, about the "${title}" card` : ""}): ${problem}`);
            thanks = "It's in the chat: once it's sent, the report goes to the people who run Wipebot. Thank you!";
          } else {
            const shown = body ? [...body.querySelectorAll(".head, .lead, .lead-more")].map((n) => n.textContent.trim()).filter(Boolean).join("\n") : "";
            await app.report({ kind, problem,
                               question: (body ? `Report from the "${title}" card (${view}) ${JSON.stringify(key)}${args ? `, its tool asked ${JSON.stringify(args)}` : ""}`
                                          : `Report on an answer that called ${tool()} ${JSON.stringify(args || {})}`).slice(0, 4000),
                               answer: (shown || title || "(the answer's text: not seen by the view)").slice(0, 20000) });
            app.tellModel(`The user sent Wipebot's maintainers a report on ${title ? `the "${title}" card` : "this answer"} (${kind}): ${problem.slice(0, 500)}`);
          }
          sent = true; kit.report = null;
          root.querySelectorAll(".report-open").forEach((b) => b.remove());
          form.replaceWith(kit.el(`<p class="report-done" role="status">${thanks}</p>`));
          form = null;
        } catch (err) {
          send.disabled = false; msg.textContent = `Not sent: ${err.message}`;
        }
      };
      if (alone) alone.hidden = true;
      root.append(form);
      what.focus();
    };
  }
  const REPORT_LINE = '<div class="report"><button type="button" class="report-open">Report a problem</button></div>';

  function open(view, key) { stack.push({ view, key }); go(view, key); }

  // A card after the first of an answer (the server's `folded`: server._fold): one line saying what it
  // is, which opens it. Each call's card is a page of its own, so a card can't know of the others.
  function fold(view, key) {
    const line = kit.el('<div class="folded"><span class="what"></span><button class="primary">Show</button></div>');
    line.firstChild.textContent = key.label || "A card";
    line.lastChild.onclick = () => go(view, key);
    root.replaceChildren(line);
    if (key.can_report && !key.no_report) {  // the first of an answer's offers (remote.py's _report_line)
      app.takesReports();
      kit.report = reporter(view, key, null);
      line.lastChild.before(kit.el('<button type="button" class="report-open">Report</button>'));
    }
  }

  // The line offering a report, for a call that draws no card (remote.py's _report_line: the first of an answer's)
  function line() {
    empty(false);
    kit.report = reporter("answer", {}, null);
    root.replaceChildren(kit.el(REPORT_LINE));
    root.firstChild.classList.add("alone");
    root.firstChild.firstChild.textContent = "Report a problem with this answer";
  }

  function start(result) {
    const sc = result && result.structuredContent;
    if (sc && sc.view === "report") {
      if (sc.can_report) app.takesReports();
      if (app.canReport() && !started) { started = "report"; line(); } else if (!started) nothing();
      return;
    }
    let key = sc && sc.report ? sc : null;
    if (!key && !(result && result.isError)) {  // a tool that failed has nothing to draw, from its arguments either
      const view = BY_TOOL[tool()];
      if (args && view) key = { view, report: args.report, pull: args.pull, boss: args.boss,
                                player: args.player, death: args.death, weeks: args.weeks };
    }
    if (!key && sometimes()) { nothing(); return; }  // a call without a view, or one that failed
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
  app.on("cancelled", () => { if (current) return; if (sometimes()) nothing(); else fail("The tool call was cancelled."); });
  app.on("context", () => { if (current && current.relayout) current.relayout(); });
  app.on("teardown", () => { if (current && current.stop) current.stop(); });
  // Once the host has said which tool this is: the placeholder in its view's shape, or for a tool only some of
  // whose calls have a view (and where the host didn't say), nothing until its result says so
  const known = () => {
    if (started) return;
    empty(sometimes());
    waiting({ ...BY_TOOL, pull_map: "replay" }[tool()]);
  };
  app.connect({ name: "Wipebot", version: "2" }).then(known, known);
})();
