// ---- the MCP Apps bridge: JSON-RPC over postMessage with the host (spec 2026-01-26) ----
// The page asks ui/initialize, says ui/notifications/initialized, then gets the tool's input and result. It calls our
// server's tools through the host (tools/call), asks for fullscreen (ui/request-display-mode) and reports its size.
const PICTURE_HOST = "wipebot-picture";  // viewhost.py's painter, which takes a still picture of a view
const app = (() => {
  let nextId = 1, context = {}, capabilities = {}, host = {};
  const pending = new Map(), listeners = { input: [], result: [], cancelled: [], context: [], teardown: [] };
  const emit = (what, value) => listeners[what].forEach((fn) => fn(value));

  function request(method, params) {
    const id = nextId++;
    window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
    return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  }
  function notify(method, params) { window.parent.postMessage({ jsonrpc: "2.0", method, params }, "*"); }

  const handlers = {
    "ui/notifications/tool-input": (p) => emit("input", p.arguments || {}),
    "ui/notifications/tool-result": (p) => emit("result", p),
    "ui/notifications/tool-cancelled": (p) => emit("cancelled", p),
    "ui/notifications/host-context-changed": (p) => merge(p),
    "ui/resource-teardown": (p) => { emit("teardown", p); return {}; },
  };
  window.addEventListener("message", (event) => {
    if (event.source !== window.parent) return;  // only the host speaks to us
    const m = event.data;
    if (!m || m.jsonrpc !== "2.0") return;
    if (m.id !== undefined && !m.method && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.reject(new Error(m.error.message || "error")) : p.resolve(m.result);
    } else if (m.method) {
      const out = handlers[m.method] ? handlers[m.method](m.params || {}) : undefined;
      if (m.id !== undefined) window.parent.postMessage({ jsonrpc: "2.0", id: m.id, result: out || {} }, "*");
    }
  });

  // The host's context arrives whole, then in changed parts: theme, display mode, container size, style variables.
  function merge(part) {
    context = { ...context, ...part };
    const vars = part.styles && part.styles.variables;
    if (vars) for (const [k, v] of Object.entries(vars)) if (v) document.documentElement.style.setProperty(k, v);
    const fonts = part.styles && part.styles.css && part.styles.css.fonts;  // the host's @font-face rules
    if (fonts && !document.getElementById("host-fonts")) {
      const style = document.createElement("style");
      style.id = "host-fonts"; style.textContent = fonts;
      document.head.appendChild(style);
    }
    if (part.theme) {
      document.documentElement.dataset.theme = part.theme;
      document.documentElement.style.colorScheme = part.theme;
    }
    emit("context", context);
  }

  // The body's own height: the document's is never less than the frame's, so it would never shrink back.
  new ResizeObserver(() => notify("ui/notifications/size-changed", {
    width: document.documentElement.scrollWidth, height: Math.ceil(document.body.getBoundingClientRect().height),
  })).observe(document.body);

  async function connect(appInfo) {
    try {
      const r = await request("ui/initialize", { appInfo, protocolVersion: "2026-01-26",
                                                appCapabilities: { availableDisplayModes: ["inline", "fullscreen"] } });
      capabilities = (r && r.hostCapabilities) || {};
      host = (r && r.hostInfo) || {};
      if (host.name === PICTURE_HOST) document.documentElement.dataset.picture = "";
      merge((r && r.hostContext) || {});
    } finally {
      notify("ui/notifications/initialized", {});
    }
  }

  // One of our server's tools; its structuredContent, or an error with the tool's text.
  async function callTool(name, args) {
    const res = await request("tools/call", { name, arguments: args });
    if (!res || res.isError || res.structuredContent === undefined) {
      throw new Error(((res && res.content) || [])[0]?.text || "no data");
    }
    return res.structuredContent;
  }

  async function requestDisplayMode(mode) {
    try {
      const r = await request("ui/request-display-mode", { mode });
      if (r && r.mode) merge({ displayMode: r.mode });
      return (r && r.mode) || context.displayMode || "inline";
    } catch (e) {
      return context.displayMode || "inline";
    }
  }
  const canGoFullscreen = () => (context.availableDisplayModes || ["inline", "fullscreen"]).includes("fullscreen");

  // A question for the model, as the user (Claude puts it in the composer to send; ChatGPT sends it).
  function sendMessage(text) {
    return request("ui/message", { role: "user", content: [{ type: "text", text }] }).catch(() => {});
  }

  // A note for the server's log (viewer_log): what went wrong in a host we can't open devtools in.
  function log(note) {
    request("tools/call", { name: "viewer_log", arguments: { note: String(note).slice(0, 500) } }).catch(() => {});
  }
  window.addEventListener("error", (e) => log(`error: ${e.message} at ${e.lineno}:${e.colno}`));
  window.addEventListener("unhandledrejection", (e) => log(`rejection: ${e.reason && e.reason.message || e.reason}`));

  return {
    connect, callTool, requestDisplayMode, canGoFullscreen, sendMessage, log,
    picture: () => host.name === PICTURE_HOST,  // drawn as a still picture (the Discord bot's): no buttons
    on: (what, fn) => listeners[what].push(fn),
    context: () => context,
    capabilities: () => capabilities,
  };
})();
