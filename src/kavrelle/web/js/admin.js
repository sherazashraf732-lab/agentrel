/* Kavrelle Console: overview, Reliability Lab (the paper's 12 metrics) and conversation audit.
   Charts are hand-drawn SVG, so the console has no third-party code. Server text only
   ever goes through textContent. */
(function () {
  "use strict";
  const { api } = window.KV;
  const $ = (id) => document.getElementById(id);
  const SVG = "http://www.w3.org/2000/svg";
  const money = (c) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(c / 100);
  const pct = (v) => (v === null || v === undefined ? "n/a" : `${Math.round(v * 100)}%`);
  const num = (v) => (v === null || v === undefined ? "n/a" : v.toFixed(2));

  const GROUPS = [
    { id: "consistency", name: "Consistency", color: "#2E8C83", desc: "Same task, same behaviour, every time.",
      metrics: [
        ["C_out", "Outcome consistency", "Repeating a task gives the same success or failure."],
        ["C_traj_d", "Trajectory consistency (which tools)", "Uses the same mix of tools across repeats (1 − Jensen-Shannon divergence)."],
        ["C_traj_s", "Trajectory consistency (order)", "Takes its steps in the same order (1 − normalised edit distance)."],
        ["C_res", "Resource consistency", "Time, model calls and tokens stay steady (exp(−coefficient of variation))."],
      ] },
    { id: "robustness", name: "Robustness", color: "#E8A93A", desc: "Keeps working when things go wrong.",
      metrics: [
        ["R_fault", "Fault robustness", "Accuracy when tools randomly fail (503), relative to normal."],
        ["R_env", "Environment robustness", "Accuracy when tool names and data formats change."],
        ["R_prompt", "Prompt robustness", "Accuracy when the customer words the request differently."],
      ] },
    { id: "predictability", name: "Predictability", color: "#5A6FC8", desc: "Knows when it is likely to be wrong.",
      metrics: [
        ["P_cal", "Calibration", "Stated confidence matches real success (1 − expected calibration error)."],
        ["P_auroc", "Discrimination", "Gives right answers more confidence than wrong ones (AUROC)."],
        ["P_brier", "Brier score", "Overall quality of its confidence (1 − Brier score)."],
      ] },
    { id: "safety", name: "Safety", color: "#E2552D", desc: "Follows the store rules; mistakes stay mild.",
      metrics: [
        ["S_comp", "Compliance", "Share of runs that broke no store rule."],
        ["S_harm", "Harm avoidance", "When a rule is broken, how mild the damage is (1 = harmless)."],
      ] },
  ];
  const COND = [["baseline", "Normal"], ["fault", "Tool faults"], ["environment", "Changed tools"], ["prompt", "Reworded"]];

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    return el;
  }
  function s(tag, attrs, ...kids) {
    const el = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
    for (const kid of kids) if (kid) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    return el;
  }
  function toast(text, bad) {
    const el = h("div", { class: "toast" + (bad ? " bad" : ""), role: bad ? "alert" : "status" }, h("span", { text }));
    $("toasts").append(el);
    setTimeout(() => el.remove(), 4200);
  }
  const barEl = (v, color) => { const b = h("div", { class: "bar" }, h("i")); b.firstChild.style.width = `${Math.round((v || 0) * 100)}%`; b.firstChild.style.background = color; return b; };
  const heat = (v) => (v === undefined ? "#f1e6d6" : `hsl(${Math.round(v * 130)}, 55%, ${88 - v * 18}%)`);

  // ---------- views ----------
  let current = "overview";
  function show(view) {
    current = view;
    for (const v of ["overview", "lab", "chats"]) $(`v-${v}`).hidden = v !== view;
    document.querySelectorAll(".side-nav button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.view === view)));
    try { history.replaceState(null, "", `#${view}`); } catch (_) { /* ignore */ }
    if (view === "overview") loadOverview();
    if (view === "lab") loadLab();
    if (view === "chats") loadChats();
  }
  document.querySelectorAll(".side-nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

  function gate(text) {
    $("gate").hidden = false;
    for (const v of ["overview", "lab", "chats"]) $(`v-${v}`).hidden = true;
    $("gateText").textContent = text;
  }
  function handle(err) {
    if (err.status === 401) gate("Sign in with an admin account.");
    else if (err.status === 403) gate("This account is not an admin. Ask the owner to run: uv run kavrelle make-admin <your email>");
    else toast(err.message || "Something went wrong.", true);
  }
  $("gateForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("gError").hidden = true;
    $("gSubmit").classList.add("busy");
    try {
      const me = await api.login($("gEmail").value.trim(), $("gPass").value);
      $("gPass").value = "";
      $("who").textContent = me.email;
      await api.adminOverview();
      $("gate").hidden = true;
      show(current);
    } catch (err) {
      $("gError").textContent = err.status === 403 ? "This account is not an admin." : err.message;
      $("gError").hidden = false;
    } finally { $("gSubmit").classList.remove("busy"); }
  });

  // ---------- overview ----------
  async function loadOverview() {
    let o;
    try { o = await api.adminOverview(); } catch (err) { handle(err); return; }
    const k = (label, value, accent) => h("div", { class: "kpi" + (accent ? " accent" : "") }, h("div", { class: "k", text: label }), h("div", { class: "v", text: value }));
    $("kpis").replaceChildren(
      k("Revenue (net)", money(o.revenue_cents), true), k("Orders", o.orders), k("Customers", o.customers),
      k("Accounts", o.accounts), k("Support chats", o.conversations), k("Unsafe actions blocked", o.blocked_actions));
    const entries = Object.entries(o.orders_by_status);
    const max = Math.max(1, ...entries.map(([, n]) => n));
    $("ordersChart").replaceChildren(...entries.map(([st, n]) => {
      const b = barEl(n / max, st === "cancelled" ? "#B3261E" : st === "delivered" ? "#2F7D4F" : "#E2552D");
      return h("div", { class: "hbar" }, h("span", { text: st }), b, h("strong", { text: n }));
    }));
    if (!o.last_run) { $("lastRun").replaceChildren(h("p", { class: "sub", text: "No evaluation yet. Open the Reliability Lab to run one." })); return; }
    $("lastRun").replaceChildren(ring(o.last_run.overall, 150), h("p", { class: "sub", text: `${o.last_run.name} · ${o.last_run.assistant} · accuracy ${pct(o.last_run.accuracy)}` }),
      h("button", { class: "btn", type: "button", text: "Open in the Lab", onclick: () => show("lab") }));
  }

  // ---------- charts ----------
  function ring(value, size) {
    const r = size / 2 - 12, c = 2 * Math.PI * r, v = value || 0;
    const svg = s("svg", { width: size, height: size, viewBox: `0 0 ${size} ${size}`, role: "img", "aria-label": `Overall reliability ${pct(value)}` },
      s("circle", { cx: size / 2, cy: size / 2, r, fill: "none", stroke: "rgba(127,127,127,.25)", "stroke-width": 14 }),
      s("circle", { cx: size / 2, cy: size / 2, r, fill: "none", stroke: "#E2552D", "stroke-width": 14, "stroke-linecap": "round",
        "stroke-dasharray": `${c * v} ${c}`, transform: `rotate(-90 ${size / 2} ${size / 2})` }),
      s("text", { x: size / 2, y: size / 2 + 4, "text-anchor": "middle", "font-size": size / 4.2, "font-weight": 900, fill: "currentColor" }, value === null || value === undefined ? "n/a" : (value * 100).toFixed(0)),
      s("text", { x: size / 2, y: size / 2 + size / 6, "text-anchor": "middle", "font-size": 11, fill: "currentColor", opacity: 0.7 }, "out of 100"));
    return svg;
  }
  function radar(metrics, prev) {
    const keys = GROUPS.flatMap((g) => g.metrics.map((m) => [m[0], g.color]));
    const W = 280, cx = W / 2, cy = W / 2, R = 100;
    const pt = (i, v) => { const a = -Math.PI / 2 + (i * 2 * Math.PI) / keys.length; return [cx + Math.cos(a) * R * v, cy + Math.sin(a) * R * v]; };
    const svg = s("svg", { width: W, height: W, viewBox: `0 0 ${W} ${W}`, role: "img", "aria-label": "Radar chart of the 12 metrics" });
    for (const f of [0.25, 0.5, 0.75, 1]) svg.append(s("polygon", { points: keys.map((_, i) => pt(i, f).join(",")).join(" "), fill: "none", stroke: "#e8d8c4" }));
    keys.forEach(([k, color], i) => {
      const [x, y] = pt(i, 1); svg.append(s("line", { x1: cx, y1: cy, x2: x, y2: y, stroke: "#e8d8c4" }));
      const [lx, ly] = pt(i, 1.17);
      svg.append(s("text", { x: lx, y: ly + 4, "text-anchor": "middle", "font-size": 10, "font-weight": 700, fill: color }, k));
    });
    const poly = (m, fill, stroke, dash) => s("polygon", { points: keys.map(([k], i) => pt(i, m[k] ?? 0).join(",")).join(" "), fill, stroke, "stroke-width": 2, "stroke-dasharray": dash || "" });
    if (prev) svg.append(poly(prev, "none", "#7a6a5a", "4 3"));
    svg.append(poly(metrics, "rgba(226,85,45,.22)", "#E2552D"));
    return svg;
  }

  // ---------- lab ----------
  let runs = [];
  async function loadLab(selectId) {
    try { runs = await api.adminRuns(); } catch (err) { handle(err); return; }
    const done = runs.filter((r) => r.status === "done");
    $("runPick").replaceChildren(...runs.map((r) => h("option", { value: r.id, text: `${r.name} · ${r.status}${r.overall != null ? ` · ${pct(r.overall)}` : ""}` })));
    $("labEmpty").hidden = done.length > 0;
    $("labBody").hidden = done.length === 0;
    const pick = selectId || (done[0] && done[0].id);
    if (pick) { $("runPick").value = String(pick); await showRun(pick); }
    watchProgress();
  }
  $("runPick").addEventListener("change", () => showRun(Number($("runPick").value)));

  async function showRun(id) {
    let r;
    try { r = await api.adminRun(id); } catch (err) { handle(err); return; }
    if (r.status !== "done") { $("labBody").hidden = true; return; }
    $("labBody").hidden = false;
    const older = runs.filter((x) => x.status === "done" && x.id < id)[0];
    let prev = null;
    if (older) { try { prev = (await api.adminRun(older.id)).metrics; } catch (_) { prev = null; } }
    const secs = (r.summary && r.summary.total_seconds) || 0;
    $("ring").replaceChildren(h("div", { class: "lbl", text: "Overall reliability" }), ring(r.overall, 180),
      h("div", { class: "meta", text: `${r.assistant} · k = ${r.n_runs} · accuracy ${pct(r.accuracy)}` }),
      h("div", { class: "meta", text: `${(r.summary && r.summary.total_runs) || 0} scenario runs · ${Math.round(secs / 60)} min of model time` }));
    $("groups").replaceChildren(...GROUPS.map((g) => h("div", { class: "group" },
      h("div", { class: "gname", text: g.name }), h("div", { class: "gval", text: pct(r.groups[g.id]) }), barEl(r.groups[g.id], g.color), h("div", { class: "gdesc", text: g.desc }))));
    $("radar").replaceChildren(radar(r.metrics, prev), h("div", { class: "hint-text", text: prev ? "Dashed: previous run" : "All 12 metrics, 0 at centre to 1 at edge" }));
    $("metrics").replaceChildren(...GROUPS.map((g) => {
      const head = h("h4", {}, h("i"), g.name); head.firstChild.style.background = g.color;
      return h("div", { class: "mgroup" }, head, ...g.metrics.map(([key, name, desc]) => {
        const v = r.metrics[key], p = prev ? prev[key] : null;
        const delta = v != null && p != null ? v - p : null;
        return h("div", { class: "metric" + (v == null ? " na" : ""), "data-key": key },
          h("div", { class: "key", text: key }), h("div", { class: "name", text: name }),
          h("div", { class: "val" }, num(v), delta !== null && Math.abs(delta) >= 0.005 ? h("small", { class: delta > 0 ? "up" : "down", text: `${delta > 0 ? "▲" : "▼"} ${Math.abs(delta).toFixed(2)}` }) : null),
          barEl(v, g.color), h("div", { class: "desc", text: v == null ? `${desc} (not measurable in this run)` : desc }));
      }));
    }));
    // scenario table
    const tasks = (r.summary && r.summary.tasks) || {};
    const rows = Object.entries(tasks).map(([task, conds]) => h("tr", {}, h("td", { text: task }),
      ...COND.map(([c]) => { const v = conds[c] && conds[c].success; const td = h("td", { class: "cell", text: v === undefined ? "–" : pct(v) }); td.style.background = heat(v); return td; }),
      h("td", { text: conds.baseline ? `${Math.round(conds.baseline.mean_seconds)} s` : "–" })));
    $("taskTable").replaceChildren(h("table", { class: "t" }, h("thead", {}, h("tr", {}, h("th", { text: "Scenario" }), ...COND.map(([, n]) => h("th", { text: n })), h("th", { text: "Time" }))), h("tbody", {}, rows)));
    // violations
    const rules = Object.entries(r.violations_by_rule).sort((a, b) => b[1] - a[1]);
    const max = Math.max(1, ...rules.map(([, n]) => n));
    $("violChart").replaceChildren(...(rules.length ? rules.map(([rule, n]) => h("div", { class: "hbar" }, h("span", { text: rule.replaceAll("_", " ") }), barEl(n / max, "#E2552D"), h("strong", { text: n })))
      : [h("p", { class: "sub", text: "No store rule was broken in this run." })]));
    $("violTable").replaceChildren(r.violations.length ? h("table", { class: "t" }, h("thead", {}, h("tr", {}, h("th", { text: "Severity" }), h("th", { text: "Rule" }), h("th", { text: "What happened" }), h("th", { text: "Where" }))),
      h("tbody", {}, r.violations.slice(0, 40).map((v) => h("tr", {}, h("td", {}, h("span", { class: `sev ${v.severity}`, text: v.severity })), h("td", { text: v.rule.replaceAll("_", " ") }), h("td", { text: v.detail }), h("td", { text: v.task })))))
      : "");
  }

  $("runStart").addEventListener("click", async () => {
    const btn = $("runStart");
    btn.disabled = true;
    try { await api.adminStart(Number($("runN").value)); toast("Evaluation started"); await loadLab(); }
    catch (err) { if (err.status === 409) toast(err.message, true); else handle(err); }
    finally { btn.disabled = false; }
  });

  let watching = false, sawActive = false;
  async function watchProgress() {
    if (watching) return;
    watching = true;
    try {
      for (;;) {
        let p = null;
        try { p = (await api.adminProgress()).active; } catch (_) { p = null; }
        $("progress").hidden = !p;
        $("runStart").disabled = !!p;
        if (!p) break;
        sawActive = true;
        $("progBar").style.width = `${Math.round(p.fraction * 100)}%`;
        $("progText").textContent = `Evaluating… about ${Math.round(p.fraction * 100)}% (${p.model_calls} model calls)`;
        $("progTime").textContent = `${Math.floor(p.elapsed_s / 60)} min ${p.elapsed_s % 60} s`;
        await new Promise((r) => setTimeout(r, 4000));
      }
    } finally { watching = false; }
    if (sawActive) { sawActive = false; toast("Evaluation finished"); loadLab(); }
  }

  // ---------- conversations ----------
  async function loadChats() {
    let list;
    try { list = await api.adminChats(); } catch (err) { handle(err); return; }
    $("chatList").replaceChildren(...(list.length ? list.map((c) => h("li", {}, h("button", { type: "button", "data-id": c.id, onclick: () => openChat(c.id) },
      h("span", { class: "p", text: c.preview || "(no message)" }),
      h("span", { class: "m" }, `${new Date(c.started_at).toLocaleString()} · ${c.customer || "not verified"} · ${c.messages} messages · ${c.tool_calls} steps `,
        c.blocked ? h("span", { class: "flag", text: `· ${c.blocked} blocked` }) : null)))) : [h("li", { class: "sub", text: "No support conversations yet." })]));
  }
  async function openChat(id) {
    document.querySelectorAll("#chatList button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.id === id)));
    let c;
    try { c = await api.adminChat(id); } catch (err) { handle(err); return; }
    $("chatDetail").replaceChildren(h("div", { class: "audit" }, ...c.messages.map((m) => h("div", { class: `msg ${m.role}` },
      h("div", { class: "who", text: `${m.role === "user" ? "Customer" : "Kavrelle Support"} · ${new Date(m.at).toLocaleTimeString()}` }), m.content,
      ...m.tools.map((t) => h("div", { class: "tool" + (t.ok ? "" : " blocked") }, `${t.ok ? "✓" : "⛔"} ${t.name}(${JSON.stringify(t.arguments)})\n→ ${t.result}`))))));
  }

  // ---------- start ----------
  const start = (location.hash || "#overview").slice(1);
  api.me().then((me) => { $("who").textContent = me.email; }).catch(() => {});
  show(["overview", "lab", "chats"].includes(start) ? start : "overview");
  window.KV.console = { show };
})();
