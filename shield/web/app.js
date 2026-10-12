/* Shield AI web application (vanilla JavaScript, no external dependencies). */
(function () {
  "use strict";
  const C = window.ShieldCharts;
  const esc = C.esc;
  const root = document.getElementById("root");
  const S = { token: null, me: null, clock: null, alertsCount: 0 };
  try { S.token = sessionStorage.getItem("shield_token"); } catch (e) { /* storage unavailable */ }
  try { const t = localStorage.getItem("shield_theme"); if (t) document.documentElement.setAttribute("data-theme", t); } catch (e) { /* ignore */ }

  const LOGO = `<svg viewBox="0 0 32 32" aria-hidden="true"><path d="M16 2l11 4v9c0 7-4.7 12.3-11 15C9.7 27.3 5 22 5 15V6z" fill="var(--accent)"/><path d="M11 16l3.5 3.5L21 13" stroke="#fff" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const ICON = { ok: "✓", fail: "✕", warn: "!" };
  const pct = (v, d = 0) => (v == null ? "–" : (v * 100).toFixed(d) + "%");
  const num = (v) => (v == null ? "–" : typeof v === "number" ? v.toLocaleString() : esc(v));
  const fmtDate = (d) => (d ? esc(d.slice(0, 10)) : "–");

  async function api(path, opts = {}) {
    const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    if (S.token) headers.Authorization = "Bearer " + S.token;
    const res = await fetch(path, Object.assign({}, opts, { headers }));
    if (res.status === 401) { logout(); throw new Error("Session expired"); }
    const ct = res.headers.get("content-type") || "";
    const body = ct.includes("json") ? await res.json() : await res.text();
    if (!res.ok) throw new Error((body && body.detail) || res.statusText);
    return body;
  }
  function toast(msg, ms = 3500) {
    const t = document.createElement("div"); t.className = "toast"; t.textContent = msg;
    document.body.appendChild(t); setTimeout(() => t.remove(), ms);
  }
  function logout() {
    S.token = null; S.me = null;
    try { sessionStorage.removeItem("shield_token"); } catch (e) { /* ignore */ }
    renderLogin();
  }
  const can = (p) => S.me && S.me.permissions.includes(p);

  /* ------------------------------------------------------------------ login */
  const DEMO = [
    ["minister", "Hon. A. Rivera", "Health Minister", "Decision-maker: findings and aggregates (small counts suppressed), decisions and feedback"],
    ["epi", "Dr. S. Okafor", "Chief Epidemiologist", "Full findings, pseudonymised records, runs the agent, feedback"],
    ["steward", "J. Lindqvist", "Data Steward", "Institution connections, break-glass access to encrypted source messages"],
    ["auditor", "M. Haddad", "Independent Auditor", "Audit trail and security posture only; no health records"],
  ];
  function renderLogin() {
    root.innerHTML = `<div class="login"><div class="card">
      <div class="brand">${LOGO}<div>Shield AI<small>Sovereign epidemic intelligence</small></div></div>
      <p class="secondary">Sign in. In production this is the government identity provider with multi-factor authentication.</p>
      <div class="users">${DEMO.map(([u, n, r, d], i) => `<button type="button" data-u="${u}" class="${i === 1 ? "on" : ""}"><b>${esc(n)} · ${esc(r)}</b><small class="muted">${esc(d)}</small></button>`).join("")}</div>
      <form id="lf"><label class="f" for="u">Username</label><input id="u" type="text" value="epi" autocomplete="username">
      <label class="f" for="p">Password</label><input id="p" type="password" value="shield-demo" autocomplete="current-password">
      <div class="row" style="margin-top:14px"><button class="btn primary" type="submit">Sign in</button><small class="muted">Demo password: shield-demo</small></div>
      <p id="lerr" class="bad"></p></form></div></div>`;
    root.querySelectorAll(".users button").forEach((b) => b.addEventListener("click", () => {
      root.querySelectorAll(".users button").forEach((x) => x.classList.remove("on")); b.classList.add("on");
      document.getElementById("u").value = b.dataset.u;
    }));
    document.getElementById("lf").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const r = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username: document.getElementById("u").value, password: document.getElementById("p").value }) });
        S.token = r.token;
        try { sessionStorage.setItem("shield_token", r.token); } catch (err) { /* ignore */ }
        await boot();
      } catch (err) { document.getElementById("lerr").textContent = err.message; }
    });
  }

  /* ------------------------------------------------------------------ shell */
  const NAV = [
    ["overview", "Situation overview", "view_dashboard"],
    ["alerts", "Alerts", "view_dashboard"],
    ["sources", "Data sources", "view_dashboard"],
    ["learning", "Learning & model", "view_model"],
    ["security", "Security & sovereignty", "view_security"],
    ["audit", "Audit trail", "view_audit"],
    ["demo", "Demo control", "demo_control"],
  ];
  async function boot() {
    S.me = await api("/api/me");
    S.clock = S.me.clock;
    shell();
    window.addEventListener("hashchange", route);
    if (!location.hash) location.hash = can("view_dashboard") ? "#/overview" : "#/security";
    route();
  }
  function shell() {
    const m = S.me;
    root.innerHTML = `<div class="app">
      <aside class="sidebar" id="sb"><div class="brand">${LOGO}<div>Shield AI<small>${esc(m.deployment.jurisdiction)}</small></div></div>
        <nav class="nav">${NAV.filter((n) => can(n[2])).map(([k, label]) => `<a href="#/${k}" data-k="${k}">${esc(label)}${k === "alerts" ? '<span class="count" id="ac" hidden></span>' : ""}</a>`).join("")}</nav>
        <div class="foot"><b>${esc(m.display_name)}</b><br>${esc(m.role.replace("_", " "))}<br><a href="#" id="lo">Sign out</a>
          <div style="margin-top:8px">v${esc(m.deployment.version)} · ${esc(m.deployment.environment)}</div></div></aside>
      <div class="main"><header class="topbar">
        <button class="btn sm menu-btn" id="mb" aria-label="Menu">☰</button>
        <span class="sovereign" title="All data stored and processed in ${esc(m.deployment.hosting_region)}">● Data resident in ${esc(m.deployment.country_code)} · ${esc(m.deployment.hosting_region)}</span>
        <span class="spacer"></span>
        <span class="clock" title="Analysis date (simulated clock in the demo)">📅 <span id="clk">${esc(S.clock)}</span> <small class="muted">analysis date</small></span>
        ${can("demo_control") ? '<button class="btn sm" id="adv">Next day ▶</button>' : ""}
        <button class="btn sm" id="th" title="Toggle light/dark theme">◐</button>
      </header><main class="content" id="view"></main></div></div>`;
    document.getElementById("lo").addEventListener("click", (e) => { e.preventDefault(); logout(); });
    document.getElementById("mb").addEventListener("click", () => document.getElementById("sb").classList.toggle("open"));
    document.getElementById("th").addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = cur === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem("shield_theme", next); } catch (e) { /* ignore */ }
      route();
    });
    const adv = document.getElementById("adv");
    if (adv) adv.addEventListener("click", () => advance(1, adv));
    refreshCount();
  }
  async function refreshCount() {
    try {
      const a = await api("/api/alerts");
      const n = a.filter((x) => x.status !== "closed" && !x.watch_only).length;
      const el = document.getElementById("ac");
      if (el) { el.textContent = n; el.hidden = !n; }
    } catch (e) { /* ignore */ }
  }
  async function advance(days, btn) {
    const old = btn ? btn.innerHTML : "";
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Ingesting & analysing…'; }
    try {
      const r = await api("/api/demo/advance", { method: "POST", body: JSON.stringify({ days }) });
      S.clock = r.clock; document.getElementById("clk").textContent = r.clock;
      const last = r.days[r.days.length - 1] || {};
      const ag = last.agent || {};
      toast(`${r.clock}: ${r.days.reduce((a, d) => a + d.messages, 0).toLocaleString()} messages ingested through the connectors; agent found ${ag.signals ?? 0} signals, ${ag.new_alerts ?? 0} new alert(s).`, 5000);
      refreshCount(); route();
    } catch (e) { toast(e.message); }
    if (btn) { btn.disabled = false; btn.innerHTML = old; }
  }
  function setActive(k) { document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.k === k)); document.getElementById("sb").classList.remove("open"); }

  function route() {
    if (!S.me) return;
    C.hideTip();
    const h = location.hash.replace(/^#\//, "");
    const [page, id] = h.split("/");
    const v = document.getElementById("view");
    if (!v) return;
    v.innerHTML = '<div class="empty"><span class="spinner"></span> Loading…</div>';
    const pages = { overview, alerts: alertsPage, alert: (vv) => alertPage(vv, +id), sources, learning, security, audit: auditPage, demo };
    setActive(page === "alert" ? "alerts" : page);
    (pages[page] || overview)(v).catch((e) => { v.innerHTML = `<div class="card bad">${esc(e.message)}</div>`; });
  }

  /* ------------------------------------------------------------------ overview */
  const SYN_LABEL = { ARI: "Acute respiratory", ILI: "Influenza-like", RESP: "Respiratory", GI: "Gastrointestinal", FEVER: "Fever", RASH: "Fever & rash", NEURO: "Neurological" };
  let ovSyn = "ARI", mapSyn = "ARI";
  function alertCard(a) {
    const hist = a.history || [];
    return `<div class="alert-card" data-id="${a.id}">${C.gauge(a.confidence)}<div>
      <div class="row" style="gap:6px;margin-bottom:3px"><span class="badge p-${esc(a.priority)}">${esc(a.priority)}</span><span class="badge lvl">${esc(a.level)} confidence</span><span class="badge st-${esc(a.status)}">${esc(a.status)}</span>${a.as_of < S.clock ? `<span class="badge st-closed" title="Not re-detected since ${esc(a.as_of)}">last seen ${esc(a.as_of)}</span>` : ""}</div>
      <div class="title">${esc(a.title)}</div>
      <div class="meta">First detected ${fmtDate(a.first_as_of)} · updated ${fmtDate(a.as_of)} · ${hist.length} daily assessment(s)${a.feedback_verdict ? " · feedback: " + esc(a.feedback_verdict.replace("_", " ")) : ""}</div></div></div>`;
  }
  function bindCards(v) { v.querySelectorAll(".alert-card").forEach((c) => c.addEventListener("click", () => (location.hash = "#/alert/" + c.dataset.id))); }

  async function overview(v) {
    const [o, alerts] = await Promise.all([api("/api/overview"), api("/api/alerts")]);
    const open = alerts.filter((a) => a.status !== "closed");
    const act = open.filter((a) => !a.watch_only), watch = open.filter((a) => a.watch_only);
    const k = o.kpis;
    const lv = k.alerts_by_level || {};
    v.innerHTML = `
      <div class="row between"><div><h1>Situation overview</h1><p class="secondary">${esc(o.jurisdiction)} · analysis date ${esc(o.clock)} · ${num(k.population_covered)} residents</p></div></div>
      ${S.me.deployment.environment === "demo" ? '<p class="banner">Demonstration environment: realistic <b>simulated</b> health-system data for the fictional City of Riverton, delivered through the real FHIR, HL7 v2 and claims connectors.</p>' : ""}
      <div class="grid g4 section">
        <div class="card kpi"><div class="label">Alerts needing attention</div><div class="value">${k.active_alerts}</div><div class="sub">${["Very high", "High", "Moderate"].map((l) => `${lv[l] || 0} ${l.toLowerCase()}`).join(" · ")} · ${k.watch_list} on watch list</div></div>
        <div class="card kpi"><div class="label">Records received (last 24 h)</div><div class="value">${num(k.records_24h)}</div><div class="sub">+ ${num(k.labs_24h)} laboratory results · ${num(k.records_total)} total</div></div>
        <div class="card kpi"><div class="label">Institutions online</div><div class="value">${k.sources_online} / ${k.sources_total}</div><div class="sub">hospitals, clinics, laboratory, insurers, registry</div></div>
        <div class="card kpi"><div class="label">Population covered</div><div class="value">${num(k.population_covered)}</div><div class="sub">pseudonymised; no names or IDs stored</div></div>
      </div>
      <div class="grid g-main section">
        <div class="card"><div class="row between"><h2>Alerts needing attention</h2><a href="#/alerts">All alerts →</a></div>
          ${act.length ? act.map(alertCard).join("") : '<div class="empty">No alerts above the notification threshold.</div>'}
          ${watch.length ? `<details class="watch section"><summary class="secondary">Watch list (${watch.length}): low-confidence signals kept under review</summary><div class="section">${watch.map(alertCard).join("")}</div></details>` : ""}
        </div>
        <div class="card"><div class="row between"><h2>Where: last 7 days vs usual</h2></div>
          <div class="chips" id="mapchips">${["ARI", "GI", "FEVER", "RASH", "NEURO"].map((s) => `<button class="chip ${s === mapSyn ? "on" : ""}" data-s="${s}">${SYN_LABEL[s]}</button>`).join("")}</div>
          <div id="map" class="section"></div><small class="muted">Colour shows encounters in the last 7 days relative to the average week in the previous four weeks. Hover for numbers.</small></div>
      </div>
      <div class="card section"><div class="row between"><h2>City-wide daily encounters by syndrome</h2>
        <div class="chips" id="trchips">${["ARI", "ILI", "GI", "FEVER", "RASH", "NEURO"].map((s) => `<button class="chip ${s === ovSyn ? "on" : ""}" data-s="${s}">${SYN_LABEL[s]}</button>`).join("")}</div></div>
        <div id="trend"></div></div>`;
    bindCards(v);
    const drawMap = () => {
      C.choropleth(document.getElementById("map"), {
        label: "Map of neighbourhoods", scaleLabel: "Ratio:",
        features: o.map.map((n) => {
          const s = mapSyn === "ARI" ? n.syndromes.ARI : n.syndromes[mapSyn];
          const parts = mapSyn === "ARI" ? ["ILI", "RESP"].map((x) => n.syndromes[x]).filter(Boolean) : [s].filter(Boolean);
          const last = parts.reduce((a, p) => a + (typeof p.last7 === "number" ? p.last7 : 0), 0);
          const prior = parts.reduce((a, p) => a + (p.prior_weekly_avg || 0), 0);
          return { id: n.id, name: n.name, polygon: n.polygon, value: s ? s.ratio : null,
            tip: `<div class="r"><span>Last 7 days</span><b>${num(last)}</b></div><div class="r"><span>Usual week</span><b>${prior.toFixed(1)}</b></div><div class="r"><span>Ratio</span><b>${s ? s.ratio.toFixed(2) : "–"}×</b></div><div class="r"><span>Population</span><b>${num(n.population)}</b></div>` };
        }),
      });
    };
    const drawTrend = () => {
      const vals = ovSyn === "ARI" ? o.trend.series.ILI.map((x, i) => x + o.trend.series.RESP[i]) : o.trend.series[ovSyn];
      const avg = vals.map((_, i) => (i < 6 ? null : vals.slice(i - 6, i + 1).reduce((a, b) => a + b, 0) / 7));
      C.timeChart(document.getElementById("trend"), { dates: o.trend.dates, height: 240, label: "Daily encounters",
        series: [{ name: "Daily encounters", type: "bar", values: vals, color: C.css("--series-1"), highlight: vals.map((_, i) => i >= vals.length - 7 || true) },
                 { name: "7-day average", type: "line", values: avg, color: C.css("--series-2") }] });
    };
    drawMap(); drawTrend();
    v.querySelectorAll("#mapchips .chip").forEach((b) => b.addEventListener("click", () => { mapSyn = b.dataset.s; v.querySelectorAll("#mapchips .chip").forEach((x) => x.classList.toggle("on", x === b)); drawMap(); }));
    v.querySelectorAll("#trchips .chip").forEach((b) => b.addEventListener("click", () => { ovSyn = b.dataset.s; v.querySelectorAll("#trchips .chip").forEach((x) => x.classList.toggle("on", x === b)); drawTrend(); }));
  }

  /* ------------------------------------------------------------------ alerts list */
  async function alertsPage(v) {
    const alerts = await api("/api/alerts");
    let filter = "open";
    const draw = () => {
      const rows = alerts.filter((a) => filter === "all" || (filter === "open" ? a.status !== "closed" : a.status === filter));
      v.querySelector("#tbl").innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>Alert</th><th>Priority</th><th class="num">Confidence</th><th>Status</th><th>First detected</th><th>Updated</th><th>Feedback</th></tr></thead><tbody>
        ${rows.map((a) => `<tr class="click" data-id="${a.id}"><td><b>#${a.id}</b> ${esc(a.title)}${a.watch_only ? ' <span class="badge st-closed">watch list</span>' : ""}</td><td><span class="badge p-${esc(a.priority)}">${esc(a.priority)}</span></td><td class="num">${pct(a.confidence)} <small class="muted">${esc(a.level)}</small></td><td><span class="badge st-${esc(a.status)}">${esc(a.status)}</span></td><td>${fmtDate(a.first_as_of)}</td><td>${fmtDate(a.as_of)}</td><td>${a.feedback_verdict ? esc(a.feedback_verdict.replace("_", " ")) : '<span class="muted">none</span>'}</td></tr>`).join("")}
        </tbody></table></div>` : '<div class="empty">No alerts.</div>';
      v.querySelectorAll("tr.click").forEach((r) => r.addEventListener("click", () => (location.hash = "#/alert/" + r.dataset.id)));
    };
    v.innerHTML = `<h1>Alerts</h1><p class="secondary">Every event the agent has assessed. Alerts below ${pct(0.5)} confidence stay on the watch list and are not notified.</p>
      <div class="chips section" id="fch">${["open", "new", "investigating", "closed", "all"].map((f) => `<button class="chip ${f === filter ? "on" : ""}" data-f="${f}">${f}</button>`).join("")}</div>
      <div class="card section" id="tbl"></div>`;
    v.querySelectorAll("#fch .chip").forEach((b) => b.addEventListener("click", () => { filter = b.dataset.f; v.querySelectorAll("#fch .chip").forEach((x) => x.classList.toggle("on", x === b)); draw(); }));
    draw();
  }

  /* ------------------------------------------------------------------ alert detail */
  let alertTab = "briefing";
  async function alertPage(v, id) {
    const d = await api("/api/alerts/" + id);
    const r = d.report, ch = r.characterisation, conf = r.confidence;
    const tabs = [["briefing", "Briefing"], ["evidence", "Evidence"], ["causes", "Possible causes"], ["detection", "Detection & algorithms"],
      ["biases", "Biases & limitations"], ["history", "Historical context"], ["records", "Raw data"], ["trace", "Agent trace"], ["feedback", "Feedback"]];
    v.innerHTML = `<a href="#/alerts">← Alerts</a>
      <div class="detail-head section"><div>
        <div class="row" style="gap:6px"><span class="badge p-${esc(r.priority)}">${esc(r.priority)} · ${esc(r.priority_text)}</span><span class="badge st-${esc(d.status)}">${esc(d.status)}</span><span class="badge lvl">${esc(r.family_label)}</span>${d.notified ? '<span class="badge lvl">notified</span>' : '<span class="badge st-closed">watch list (not notified)</span>'}</div>
        <h1 style="margin-top:8px">${esc(r.title)}</h1>
        <p class="secondary">Alert #${d.id} · first detected ${fmtDate(d.first_as_of)} · assessed ${fmtDate(d.as_of)} · ${esc(r.evidence.case_definition)}</p>
      </div><div class="row">${C.gauge(conf.value, 84)}<div><b>${esc(conf.level)} confidence</b><br><small class="muted">that this is a real event<br>worth investigating</small></div></div></div>
      ${r.disclosure_control ? `<p class="banner">${esc(r.disclosure_control)}</p>` : ""}
      ${can("update_alert_status") ? `<div class="row"><span class="muted">Status:</span>${["new", "investigating", "closed"].map((s) => `<button class="btn sm st" data-s="${s}" ${s === d.status ? "disabled" : ""}>${s}</button>`).join("")}</div>` : ""}
      <div class="tabs">${tabs.map(([k, l]) => `<button class="tab ${k === alertTab ? "on" : ""}" data-t="${k}">${l}</button>`).join("")}</div>
      <div id="tab"></div>`;
    v.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => { alertTab = b.dataset.t; v.querySelectorAll(".tab").forEach((x) => x.classList.toggle("on", x === b)); drawTab(); }));
    v.querySelectorAll(".btn.st").forEach((b) => b.addEventListener("click", async () => { await api(`/api/alerts/${id}/status`, { method: "POST", body: JSON.stringify({ status: b.dataset.s }) }); toast("Status updated"); route(); }));
    const T = v.querySelector("#tab");
    function drawTab() {
      C.hideTip();
      ({ briefing, evidence, causes, detection, biases, history, records, trace, feedback }[alertTab] || briefing)();
    }
    function briefing() {
      T.innerHTML = `<div class="grid g-main"><div>
        <div class="card"><h2>What could be happening</h2><p class="summary">${esc(r.summary)}</p>${r.summary_plain_language ? `<h4>Plain-language version (in-country language model)</h4><p>${esc(r.summary_plain_language)}</p>` : ""}</div>
        <div class="card"><h2>Key findings</h2><ul class="findings">${r.key_findings.map((k) => `<li><span class="kind">${esc(k.kind)}</span><span>${esc(k.text)}</span></li>`).join("")}</ul></div>
      </div><div>
        <div class="card"><h2>Recommended next steps</h2><ol class="actions">${r.recommended_actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ol><small class="muted">Suggestions for the investigation team. Decisions remain with the government.</small></div>
        <div class="card"><h2>Most likely explanations</h2>${r.hypotheses.slice(0, 3).map((h) => `<div class="row between"><span>${esc(h.name)}</span><b>${pct(h.probability)}</b></div><div class="hyp" style="padding:0;border:none"><div class="bar"><span style="width:${h.probability * 100}%"></span></div></div>`).join("")}<a href="#" id="tocauses">See the evidence for each →</a></div>
        <div class="card"><h2>How the assessment evolved</h2><div id="hist"></div></div>
      </div></div>`;
      T.querySelector("#tocauses").addEventListener("click", (e) => { e.preventDefault(); v.querySelector('[data-t="causes"]').click(); });
      const h = d.history;
      C.timeChart(T.querySelector("#hist"), { dates: h.map((x) => x.as_of), height: 170, label: "Confidence over time",
        series: [{ name: "Confidence (%)", values: h.map((x) => Math.round(x.confidence * 100)), color: C.css("--series-1") }] });
      T.querySelector("#hist").insertAdjacentHTML("beforeend", `<div class="table-wrap"><table><thead><tr><th>Date</th><th class="num">Cases</th><th class="num">Expected</th><th>Leading explanation</th></tr></thead><tbody>${h.slice().reverse().map((x) => `<tr><td>${esc(x.as_of)}</td><td class="num">${num(x.observed)}</td><td class="num">${num(x.expected)}</td><td>${esc(x.top_hypothesis || "")}</td></tr>`).join("")}</tbody></table></div>`);
    }
    function evidence() {
      const sev = ch.severity, g = ch.growth || {}, rt = ch.rt;
      T.innerHTML = `<div class="card"><h2>Epidemic curve: ${esc(ch.case_syndrome_label)} in ${esc(ch.place)}</h2><div id="curve"></div>
          <small class="muted">Bars: observed encounters. Line: model expectation (seasonality, trend, day-of-week, holidays). Shaded: 99% prediction band for the assessment window. Nowcast for the last 7 days (correcting for records in transit): ${ch.epi_curve.nowcast_last7.map((x) => num(x)).join(", ")}.</small></div>
        <div class="grid g3 section">
          <div class="card kpi"><div class="label">Cases in 14 days</div><div class="value">${num(ch.observed)}</div><div class="sub">${num(ch.expected)} expected · ${ch.ratio}× · excess ${num(ch.excess)}</div></div>
          <div class="card kpi"><div class="label">Growth</div><div class="value">${g.doubling_time_days ? "Doubling " + g.doubling_time_days + " d" : g.halving_time_days ? "Halving " + g.halving_time_days + " d" : "Stable"}</div><div class="sub">${rt ? `Rt ${rt.rt} (95% CrI ${rt.rt_lo}-${rt.rt_hi}), serial interval ${rt.serial_interval.mean} d` : "Reproduction number not estimable"}</div></div>
          <div class="card kpi"><div class="label">Severity</div><div class="value">${num(sev.admitted)} admitted</div><div class="sub">${sev.admitted_pct}% vs ${sev.baseline_admitted_pct}% usually · ${num(sev.icu)} ICU · ${num(sev.deaths)} deaths</div></div>
        </div>
        <div class="grid g2 section">
          <div class="card"><h2>Who: age profile</h2><div id="age"></div><small class="muted">Share of cases by age compared with the same syndrome and place in the previous 8 weeks.${ch.demographics.age_chi2_p != null ? ` Difference p = ${ch.demographics.age_chi2_p.toExponential(1)} (chi-square).` : ""} Male share ${pct(ch.demographics.male_share)} vs ${pct(ch.demographics.baseline_male_share)}.</small></div>
          <div class="card"><h2>Where: neighbourhoods</h2><div class="table-wrap"><table><thead><tr><th>Neighbourhood</th><th class="num">Cases</th><th class="num">Expected</th><th class="num">Rel. risk</th><th class="num">per 10,000</th></tr></thead><tbody>${ch.geography.map((x) => `<tr><td>${esc(x.name)}${x.in_event_area === false ? ' <small class="muted">(residents linked through a school or university)</small>' : ""}</td><td class="num">${num(x.cases)}</td><td class="num">${num(x.expected)}</td><td class="num">${x.relative_risk ?? "–"}</td><td class="num">${x.rate_per_10k}</td></tr>`).join("")}</tbody></table></div></div>
        </div>
        <div class="grid g2 section">
          <div class="card"><h2>Schools and universities</h2>${ch.institutions.length ? `<div class="table-wrap"><table><thead><tr><th>Institution</th><th class="num">Cases</th><th class="num">Expected</th><th class="num">Rel. risk</th><th class="num">per 1,000 enrolled</th></tr></thead><tbody>${ch.institutions.map((x) => `<tr><td>${esc(x.name)}</td><td class="num">${num(x.cases)}</td><td class="num">${num(x.expected)}</td><td class="num">${x.relative_risk ?? "–"}</td><td class="num">${x.attack_rate_per_1000}</td></tr>`).join("")}</tbody></table></div>` : '<p class="muted">No link to schools or universities.</p>'}</div>
          <div class="card"><h2>Laboratory results</h2>${ch.labs.length ? ch.labs.map((p) => `<h4>${esc(p.panel)}</h4><p>${p.tested} case-patients tested. Positive: ${Object.keys(p.positives).length ? Object.entries(p.positives).map(([k, n]) => `${esc(k)} ${n} (${pct(p.positivity[k])}; baseline ${pct(p.baseline_positivity[k] || 0)})`).join(", ") : "none"}.${p.negative_for_all_pct != null ? ` Negative for every analyte: <b>${p.negative_for_all_pct}%</b> (baseline ${p.baseline_negative_for_all_pct ?? "–"}%).` : ""}</p>`).join("") : '<p class="muted">No laboratory results yet for these patients.</p>'}</div>
        </div>
        <div class="grid g2 section">
          <div class="card"><h2>Which institutions see it</h2><div class="table-wrap"><table><thead><tr><th>Source</th><th class="num">Cases</th><th class="num">Share</th><th class="num">Usual share</th><th class="num">Excess</th></tr></thead><tbody>${ch.sources.map((x) => `<tr><td>${esc(x.name)}</td><td class="num">${num(x.cases)}</td><td class="num">${pct(x.share)}</td><td class="num">${pct(x.baseline_share)}</td><td class="num">${x.excess}</td></tr>`).join("")}</tbody></table></div>
            <p>${ch.n_sources_with_excess} source(s) with excess; ${pct(ch.top_excess_source_share)} of the excess from the largest contributor.</p>
            ${ch.other_source_check ? `<p>Cross-check with other institutions serving ${esc(ch.other_source_check.neighbourhoods.join(", "))}: ${ch.other_source_check.observed_7d} encounters vs ${ch.other_source_check.expected_7d} expected in 7 days (${ch.other_source_check.corroborated ? '<b class="ok">corroborated</b>' : '<b class="bad">not corroborated</b>'}).</p>` : ""}</div>
          <div class="card"><h2>Coding and free text</h2>${(ch.coding.top_code_shifts || []).length ? `<div class="table-wrap"><table><thead><tr><th>ICD-10 code</th><th class="num">Share now</th><th class="num">Usual share</th></tr></thead><tbody>${ch.coding.top_code_shifts.map((x) => `<tr><td class="mono">${esc(x.code)}</td><td class="num">${pct(x.case_share)}</td><td class="num">${pct(x.baseline_share)}</td></tr>`).join("")}</tbody></table></div>` : ""}
            <p>Records whose triage text does not describe this syndrome: <b>${ch.coding.text_code_disagreement == null ? "–" : pct(ch.coding.text_code_disagreement)}</b> (usually ${ch.coding.baseline_text_code_disagreement == null ? "–" : pct(ch.coding.baseline_text_code_disagreement)}).</p>
            <h4>Most common triage text</h4><p>${(ch.coding.top_chief_complaints || []).map((x) => `“${esc(x.text)}” (${x.count})`).join(", ")}</p></div>
        </div>`;
      const c = ch.epi_curve;
      C.timeChart(T.querySelector("#curve"), { dates: c.dates, height: 260, label: "Epidemic curve",
        series: [{ name: "99% prediction band", type: "band", upper: c.upper, lower: c.upper.map(() => 0), values: [] },
                 { name: "Observed", type: "bar", values: c.observed, color: C.css("--series-1") },
                 { name: "Expected", type: "line", values: c.expected, color: C.css("--series-2") }] });
      C.groupedBars(T.querySelector("#age"), { categories: ch.demographics.age.map((a) => a.band), percent: true, label: "Age profile",
        series: [{ name: "Cases now", values: ch.demographics.age.map((a) => a.share), color: C.css("--series-1") },
                 { name: "Usual", values: ch.demographics.age.map((a) => a.baseline_share), color: C.css("--series-2") }] });
    }
    function causes() {
      T.innerHTML = `<div class="card"><h2>Differential diagnosis</h2><p class="secondary">Each explanation starts from a prior (how common it is, raised by locally confirmed events) and every piece of evidence adds or subtracts log-odds. Probabilities are relative among the listed explanations, not a clinical diagnosis.</p>
        ${r.hypotheses.map((h) => `<div class="hyp"><div class="row between"><h3 style="margin:0">${esc(h.name)}</h3><b>${pct(h.probability, 1)}</b></div><div class="bar"><span style="width:${h.probability * 100}%"></span></div>
          <div class="grid g2"><div><h4>Evidence for</h4>${h.evidence_for.length ? h.evidence_for.map((e) => `<div class="ev for"><span class="d">+${e.delta.toFixed(1)}</span><span>${esc(e.text)}</span></div>`).join("") : '<small class="muted">none</small>'}</div>
          <div><h4>Evidence against</h4>${h.evidence_against.length ? h.evidence_against.map((e) => `<div class="ev against"><span class="d">${e.delta.toFixed(1)}</span><span>${esc(e.text)}</span></div>`).join("") : '<small class="muted">none</small>'}</div></div>
          ${(h.notes || []).length ? `<p class="muted">${h.notes.map(esc).join(" ")}</p>` : ""}
          <p class="secondary"><b>Transmission:</b> ${esc(h.transmission)} · <b>Incubation:</b> ${esc(h.incubation_days)} days</p>
          <h4>Tests that would confirm or exclude it</h4><ul>${h.tests.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></div>`).join("")}</div>`;
    }
    function detection() {
      T.innerHTML = `<div class="grid g2"><div class="card"><h2>Why this confidence (${pct(conf.value)})</h2><div id="contrib"></div><small class="muted">Contribution of each evidence feature to the log-odds (model version ${conf.model_version}; the model is re-trained from government feedback). Blue raises, red lowers confidence.</small></div>
        <div class="card"><h2>Signals that triggered this alert</h2><div class="table-wrap"><table><thead><tr><th>Population / place</th><th>Syndrome</th><th>Detectors</th><th class="num">Obs.</th><th class="num">Exp.</th><th class="num">p</th></tr></thead><tbody>
        ${r.signals.map((s) => `<tr><td>${esc(s.stratum)}</td><td>${esc(s.syndrome_label)}</td><td>${s.detectors_fired.map((x) => `<span class="badge lvl">${esc(x.replace(/_/g, " "))}</span>`).join(" ")}</td><td class="num">${num(s.observed)}</td><td class="num">${num(s.expected)}</td><td class="num">${s.p_value < 0.001 ? s.p_value.toExponential(1) : s.p_value}</td></tr>`).join("")}</tbody></table></div></div></div>
        <div class="card section"><h2>Algorithms used</h2><div class="table-wrap"><table><thead><tr><th>Method</th><th>Role</th><th>Reference</th></tr></thead><tbody>${r.algorithms.map((a) => `<tr><td><b>${esc(a.name)}</b></td><td>${esc(a.role)}</td><td class="secondary">${esc(a.reference)}</td></tr>`).join("")}</tbody></table></div></div>
        <div class="card section"><h2>Detector details</h2>${r.signals.map((s) => `<details><summary>${esc(s.stratum)} · ${esc(s.syndrome_label)}</summary><div class="table-wrap"><table><thead><tr><th>Detector</th><th>Alarm</th><th class="num">Observed</th><th class="num">Expected</th><th class="num">Threshold</th><th class="num">p</th><th>Details</th></tr></thead><tbody>${(s.results || []).map((x) => `<tr><td>${esc(x.detector)}</td><td>${x.alarm ? '<b class="bad">yes</b>' : "no"}</td><td class="num">${num(x.observed)}</td><td class="num">${num(x.expected)}</td><td class="num">${num(x.threshold)}</td><td class="num">${x.p_value}</td><td class="secondary">${esc(Object.entries(x).filter(([k]) => !["detector", "alarm", "observed", "expected", "threshold", "p_value", "score", "window_days"].includes(k)).map(([k, val]) => `${k}: ${Array.isArray(val) ? val.join(", ") : val}`).join("; "))}</td></tr>`).join("")}</tbody></table></div></details>`).join("")}</div>`;
      C.hbars(T.querySelector("#contrib"), { labelWidth: 200, rows: conf.contributions.map((c) => ({ label: c.feature.replace(/_/g, " "), value: c.contribution, display: (c.contribution >= 0 ? "+" : "") + c.contribution.toFixed(2), tip: `${c.description}. Value ${c.value} × weight ${c.weight}` })) });
    }
    function biases() {
      T.innerHTML = `<div class="card"><h2>Biases and limitations</h2><p class="secondary">Computed for this event from the data. Read these before acting.</p>
        ${r.biases.map((b) => `<div class="bias ${esc(b.severity)}"><div class="row"><b>${esc(b.type.replace(/_/g, " "))}</b><span class="badge lvl">${esc(b.severity)} impact</span><span class="muted">likely effect: ${esc(b.direction)}</span></div><p>${esc(b.description)}</p></div>`).join("")}</div>`;
    }
    function history() {
      const hc = r.historical_context;
      T.innerHTML = `<div class="card"><h2>Comparable past events</h2><p class="secondary">Matched to the leading explanations. Projections scale published rates to this population to show what is at stake; they are not forecasts. Figures as published by the cited sources.</p>
        ${hc.analogues.length ? hc.analogues.map((a) => `<div class="analogue"><div class="row between"><h3 style="margin:0">${esc(a.name)}</h3><span class="muted">${esc(a.years)} · ${esc(a.location)}</span></div>
          <dl><dt>Pathogen</dt><dd>${esc(a.pathogen)}</dd><dt>Cases</dt><dd>${esc(a.cases || "–")}</dd><dt>Hospitalisations</dt><dd>${esc(a.hospitalisations || "–")}</dd><dt>Deaths</dt><dd>${esc(a.deaths || "–")}</dd><dt>Economic impact</dt><dd>${esc(a.economic_impact || "–")}</dd><dt>Social impact</dt><dd>${esc(a.social_impact || "–")}</dd><dt>Lesson</dt><dd>${esc(a.lessons || "–")}</dd></dl>
          ${a.projection ? `<div class="table-wrap"><table><thead><tr><th>If similar rates applied to</th><th class="num">Population</th><th class="num">Cases</th><th class="num">Hospitalisations</th><th class="num">Deaths</th></tr></thead><tbody>${Object.entries(a.projection).map(([k, p]) => `<tr><td>${k === "affected_area" ? "Affected neighbourhoods" : "Whole city"}</td><td class="num">${num(p.population)}</td><td class="num">${num(p.cases)}</td><td class="num">${num(p.hospitalisations)}</td><td class="num">${num(p.deaths)}</td></tr>`).join("")}</tbody></table></div><small class="muted">${esc(a.projection_note)}</small>` : ""}
          <p><small>Sources: ${(a.sources || []).map((s) => `${esc(s.title)} (<span class="mono">${esc(s.url)}</span>)`).join("; ")}</small></p></div>`).join("") : '<p class="muted">No documented analogue for the leading explanations.</p>'}</div>
        <div class="card"><h2>This jurisdiction's own history</h2>${hc.local_history.length ? hc.local_history.map((l) => `<div class="analogue"><b>#${l.alert_id} ${esc(l.title)}</b><p class="muted">Detected ${esc(l.detected)} · ${esc(l.verdict)} · cause: ${esc(l.confirmed_cause || "–")}</p><p><b>Actions taken:</b> ${(l.actions || []).map(esc).join("; ")}</p><p><b>Outcome:</b> ${esc(Object.entries(l.outcomes || {}).map(([k, val]) => `${k}: ${val}`).join("; "))}</p>${l.notes ? `<p class="secondary">${esc(l.notes)}</p>` : ""}</div>`).join("") : '<p class="muted">No confirmed local events of this kind yet. Feedback on alerts builds this history.</p>'}</div>`;
    }
    async function records() {
      if (!d.can_view_line_list) { T.innerHTML = `<div class="card"><h2>Raw data</h2><p>Your role sees aggregate findings only. Epidemiologists and data stewards can view the pseudonymised records behind this alert; every view is logged.</p></div>`; return; }
      T.innerHTML = '<div class="card"><span class="spinner"></span></div>';
      let offset = 0;
      const load = async () => {
        const res = await api(`/api/alerts/${id}/cases?limit=100&offset=${offset}`);
        T.innerHTML = `<div class="card"><div class="row between"><div><h2>Records behind this alert</h2><p class="secondary">${num(res.total)} pseudonymised records matching the case definition. ${esc(res.note)}</p></div>
          <div class="row">${can("export") ? `<a class="btn" href="#" id="csv">Export CSV (logged)</a>` : ""}</div></div>
          <div class="table-wrap"><table><thead><tr><th>Date</th><th>Patient (pseudonym)</th><th>Age</th><th>Sex</th><th>Neighbourhood</th><th>School / university</th><th>Facility</th><th>Source</th><th>Type</th><th>Syndrome</th><th>Triage text</th><th>ICD-10</th><th>Adm.</th><th>ICU</th><th>Died</th>${can("view_raw_message") ? "<th>Source message</th>" : ""}</tr></thead><tbody>
          ${res.rows.map((x) => `<tr><td>${esc(x.event_date)}</td><td class="mono">${esc(x.patient)}…</td><td>${esc(x.age_band)}</td><td>${esc(x.sex)}</td><td>${esc(x.neighbourhood)}</td><td>${esc(x.institution || "")}</td><td>${esc(x.facility_id)}</td><td>${esc(x.source_id)}</td><td>${esc(x.encounter_class)}</td><td>${esc(x.syndrome)}</td><td>${esc(x.chief_complaint)}</td><td class="mono">${esc(x.icd10)}</td><td>${x.admitted ? "✓" : ""}</td><td>${x.icu ? "✓" : ""}</td><td>${x.died ? "✓" : ""}</td>${can("view_raw_message") ? `<td>${x.raw_id ? `<button class="btn sm raw" data-r="${x.raw_id}">Decrypt</button>` : '<small class="muted">bulk load</small>'}</td>` : ""}</tr>`).join("")}</tbody></table></div>
          <div class="row section"><button class="btn sm" id="prev" ${offset ? "" : "disabled"}>← Previous</button><span class="muted">${offset + 1}-${Math.min(offset + 100, res.total)} of ${num(res.total)}</span><button class="btn sm" id="next" ${offset + 100 < res.total ? "" : "disabled"}>Next →</button></div>
          <div id="rawbox"></div></div>`;
        T.querySelector("#prev").addEventListener("click", () => { offset -= 100; load(); });
        T.querySelector("#next").addEventListener("click", () => { offset += 100; load(); });
        const csv = T.querySelector("#csv");
        if (csv) csv.addEventListener("click", async (e) => {
          e.preventDefault();
          const res2 = await fetch(`/api/alerts/${id}/cases.csv`, { headers: { Authorization: "Bearer " + S.token } });
          const blob = await res2.blob(); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `shield-alert-${id}-cases.csv`; a.click();
          toast("Export recorded in the audit trail");
        });
        T.querySelectorAll(".raw").forEach((b) => b.addEventListener("click", async () => {
          const j = prompt("Break-glass access to an encrypted source message with direct identifiers.\nState your justification (it will be recorded in the audit trail):");
          if (!j) return;
          try {
            const m = await api(`/api/raw/${b.dataset.r}?justification=${encodeURIComponent(j)}`);
            T.querySelector("#rawbox").innerHTML = `<h3 class="section">Source message #${m.id} (${esc(m.format)} from ${esc(m.source_id)}, received ${esc(m.received_at)})</h3><p class="banner">${esc(m.warning)} Key ${esc(m.key_id)} · SHA-256 ${esc(m.sha256.slice(0, 16))}…</p><pre class="raw">${esc(m.format === "fhir" ? JSON.stringify(JSON.parse(m.payload), null, 2) : m.payload.replace(/\r/g, "\n"))}</pre>`;
          } catch (err) { toast(err.message); }
        }));
      };
      await load();
    }
    function trace() {
      T.innerHTML = `<div class="card"><h2>What the agent did (run #${d.run_id ?? "–"})</h2><p class="secondary">Every step of the most recent run that assessed this alert, in order, with timing. Stored with the run and covered by the audit trail.</p>
        <ol class="trace">${d.agent_trace.map((s) => `<li><span class="step">${esc(s.step)}</span> <small class="muted">${s.seconds}s</small><div>${esc(s.detail)}</div></li>`).join("")}</ol></div>`;
    }
    function feedback() {
      const prev = d.feedback;
      const actions = ["Case investigation and contact tracing", "Laboratory confirmation requested", "Advisory issued to clinicians", "Public communication", "School or university measures", "Environmental inspection / remediation", "Vaccination or prophylaxis campaign", "Hospital capacity measures", "Data issue raised with institution", "No action required"];
      T.innerHTML = `<div class="grid g2"><div class="card"><h2>Tell Shield AI what happened</h2>
        ${d.can_feedback ? `<form id="fb">
          <label class="f">Verdict</label><div class="radio-row">${[["confirmed", "Confirmed real event"], ["partially_correct", "Partially correct"], ["false_alarm", "False alarm"], ["inconclusive", "Inconclusive"]].map(([k, l], i) => `<label><input type="radio" name="verdict" value="${k}" ${i === 0 ? "checked" : ""}>${l}</label>`).join("")}</div>
          <label class="f" for="cause">Confirmed cause</label><select id="cause"><option value="">Not determined</option>${r.hypotheses.map((h) => `<option value="${esc(h.id)}">${esc(h.name)}</option>`).join("")}<option value="other">Other</option></select>
          <label class="f">Which findings were right?</label><div class="table-wrap"><table><tbody>${r.key_findings.map((k, i) => `<tr><td>${esc(k.text)}</td><td><select data-i="${i}" class="fr"><option value="">–</option><option value="correct">correct</option><option value="incorrect">incorrect</option><option value="unsure">unsure</option></select></td></tr>`).join("")}</tbody></table></div>
          <label class="f">Actions taken</label><div class="check-grid">${actions.map((a) => `<label><input type="checkbox" class="act" value="${esc(a)}">${esc(a)}</label>`).join("")}</div>
          <div class="grid g3"><div><label class="f">Confirmed cases</label><input type="number" id="oc" min="0"></div><div><label class="f">Hospitalised</label><input type="number" id="oh" min="0"></div><div><label class="f">Deaths</label><input type="number" id="od" min="0"></div></div>
          <label class="f" for="notes">Notes: what did the system get wrong or right?</label><textarea id="notes"></textarea>
          <label style="display:flex;gap:6px;margin-top:8px"><input type="checkbox" id="close"> Close this alert</label>
          <div class="row section"><button class="btn primary" type="submit">Submit feedback</button></div></form>` : '<p class="muted">Your role cannot submit feedback.</p>'}
        </div><div class="card"><h2>What the platform learned</h2><div id="learn">${prev.length ? prev.map(learnHtml).join("") : '<p class="muted">No feedback yet. Feedback re-trains the confidence model, updates detector reliability and alert thresholds, and becomes local history for future alerts.</p>'}</div></div></div>`;
      const f = T.querySelector("#fb");
      if (f) f.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fr = {}; T.querySelectorAll(".fr").forEach((s) => { if (s.value) fr[r.key_findings[+s.dataset.i].kind + "_" + s.dataset.i] = s.value; });
        const body = { verdict: f.querySelector("input[name=verdict]:checked").value, confirmed_cause: f.querySelector("#cause").value || null, findings_review: fr,
          actions: [...T.querySelectorAll(".act:checked")].map((x) => x.value), outcomes: { cases: +f.querySelector("#oc").value || null, hospitalised: +f.querySelector("#oh").value || null, deaths: +f.querySelector("#od").value || null },
          notes: f.querySelector("#notes").value, close: f.querySelector("#close").checked };
        try {
          const res = await api(`/api/alerts/${id}/feedback`, { method: "POST", body: JSON.stringify(body) });
          T.querySelector("#learn").innerHTML = learnHtml({ verdict: body.verdict, username: S.me.username, created_at: "just now", learning: res, notes: body.notes }) + T.querySelector("#learn").innerHTML;
          toast("Feedback recorded. The model has been updated.");
        } catch (err) { toast(err.message); }
      });
    }
    drawTab();
  }
  function learnHtml(f) {
    const L = f.learning || {};
    return `<div class="analogue"><b>${esc((f.verdict || "").replace("_", " "))}</b> <small class="muted">by ${esc(f.username)} · ${esc(f.created_at)}</small>${f.notes ? `<p class="secondary">“${esc(f.notes)}”</p>` : ""}
      <ul>${(L.changes || []).map((c) => {
        if (c.what === "confidence model") return `<li>Confidence model re-trained on ${c.examples} labelled alert(s). This alert's confidence would now be ${pct(c.this_alert_confidence.after)} (was ${pct(c.this_alert_confidence.before)}). ${c.weights_changed.map((w) => `${esc(w.feature.replace(/_/g, " "))}: ${w.from} → ${w.to}`).join("; ")}</li>`;
        if (c.what === "detector reliability") return `<li>Reliability updated for ${c.detectors.map((x) => esc(x.replace(/_/g, " "))).join(", ")}.</li>`;
        if (c.what === "alert threshold") return `<li>Alert threshold for ${esc(c.family)}: false-alarm rate multiplier ${c.false_alarm_rate_multiplier.from} → ${c.false_alarm_rate_multiplier.to}.</li>`;
        if (c.what === "local epidemiology") return `<li>Local history: ${esc(c.cause)} confirmed ${c.confirmed_events} time(s); its prior in future differentials is raised.</li>`;
        return `<li>${esc(JSON.stringify(c))}</li>`;
      }).join("") || "<li>Recorded (inconclusive verdicts do not change the model).</li>"}</ul></div>`;
  }

  /* ------------------------------------------------------------------ sources */
  async function sources(v) {
    const d = await api("/api/sources");
    const stIcon = { online: ["ok", "✓"], delayed: ["", "!"], disconnected: ["bad", "✕"] };
    v.innerHTML = `<h1>Data sources</h1><p class="secondary">Institutions connected under data-sharing agreements. Each sends data in its native format to an authenticated endpoint; the platform validates, pseudonymises and encrypts on arrival.</p>
      <div class="card section"><div class="table-wrap"><table><thead><tr><th>Institution</th><th>Type</th><th>Protocol</th><th>Status</th><th class="num">Last 24 h</th><th class="num">Last 7 days</th><th class="num">Avg. delay</th><th>Last received</th>${can("manage_sources") ? "<th></th>" : ""}</tr></thead><tbody>
      ${d.sources.map((s) => `<tr><td><b>${esc(s.name)}</b><br><small class="muted">${esc(s.ownership)} · <span class="mono">${esc(s.endpoint)}</span>${s.notes ? "<br>" + esc(s.notes) : ""}</small></td><td>${esc(s.institution_type.replace("_", " "))}</td><td>${esc(s.protocol)}</td>
        <td><span class="${stIcon[s.status][0]}">${stIcon[s.status][1]} ${esc(s.status)}</span></td><td class="num">${num(s.records_24h)}</td><td class="num">${num(s.records_7d)}</td><td class="num">${s.avg_delay_hours != null ? (s.avg_delay_hours >= 48 ? (s.avg_delay_hours / 24).toFixed(1) + " d" : s.avg_delay_hours + " h") : "–"}</td><td>${fmtDate(s.last_received_at)}</td>
        ${can("manage_sources") ? `<td><button class="btn sm conn" data-id="${esc(s.id)}" data-c="${s.connected ? 0 : 1}">${s.connected ? "Suspend" : "Reconnect"}</button></td>` : ""}</tr>`).join("")}</tbody></table></div></div>
      <div class="grid g3 section">
        <div class="card"><h3>Hospitals and clinics</h3><p>FHIR R4 (Subscriptions for real-time encounters, Bulk Data $export for history) and HL7 v2.5.1 ADT messages over MLLP/TLS. Encounters, diagnoses, triage text, admission, intensive care and discharge outcome.</p></div>
        <div class="card"><h3>Insurers</h3><p>Daily claims batches from public and private health insurers, and death-claim notifications from life insurers. They fill coverage gaps (facilities without a direct feed, deaths outside hospital) at the cost of delay.</p></div>
        <div class="card"><h3>Laboratory and registry</h3><p>Laboratory results as FHIR DiagnosticReport/Observation with LOINC codes. The education registry links pseudonymised pupils and students to schools and universities without sharing identities.</p></div></div>`;
    v.querySelectorAll(".conn").forEach((b) => b.addEventListener("click", async () => { await api(`/api/sources/${b.dataset.id}/connection`, { method: "POST", body: JSON.stringify({ connected: b.dataset.c === "1" }) }); toast("Connection updated and audited"); route(); }));
  }

  /* ------------------------------------------------------------------ learning */
  async function learning(v) {
    const m = await api("/api/model");
    const names = m.features.map((f) => f.name);
    v.innerHTML = `<h1>Learning & model</h1><p class="secondary">How Shield AI turns government feedback into better alerts. Nothing changes silently: every update is versioned and audited.</p>
      <div class="grid g2 section"><div class="card"><h2>Confidence model weights</h2><div id="wts"></div><small class="muted">Current weights after ${m.examples} labelled alert(s); expert prior in brackets. Blue features raise confidence, red lower it.</small></div>
      <div class="card"><h2>How feedback is used</h2><ol class="actions">
        <li><b>Confidence model.</b> Each verdict becomes a labelled example. The logistic model is re-fitted around its expert prior so a few examples adjust it without overturning sensible defaults.</li>
        <li><b>Detector reliability.</b> Positive predictive value per detector (Beta-Binomial) shows which methods work best locally.</li>
        <li><b>Alert thresholds.</b> Syndrome families that keep producing false alarms get a stricter false-alarm rate; never looser than configured.</li>
        <li><b>Local epidemiology.</b> Confirmed causes raise those causes' prior in future differentials; actions and outcomes become local history cited in new alerts.</li></ol>
        <h3>Current settings</h3><p>Base false-alarm rate per series ${m.settings.alert_alpha}; false-discovery rate ${m.settings.fdr_q}; ${m.settings.scan_replicates} Monte Carlo replicates per scan; notification threshold ${pct(m.settings.notify_min_confidence)} confidence.</p></div></div>
      <div class="grid g3 section">
        <div class="card"><h2>Detector reliability</h2>${Object.keys(m.detector_reliability).length ? `<table><thead><tr><th>Detector</th><th class="num">Est. PPV</th></tr></thead><tbody>${Object.entries(m.detector_reliability).sort((a, b) => b[1].ppv - a[1].ppv).map(([k, x]) => `<tr><td>${esc(k.replace(/_/g, " "))}</td><td class="num">${pct(x.ppv)}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">No feedback yet.</p>'}</div>
        <div class="card"><h2>Alert thresholds</h2>${Object.keys(m.family_multiplier).length ? `<table><thead><tr><th>Family</th><th class="num">Confirmed / false</th><th class="num">Rate multiplier</th></tr></thead><tbody>${Object.entries(m.family_multiplier).map(([k, x]) => `<tr><td>${esc(k)}</td><td class="num">${(m.family_tally[k] || [0, 0]).join(" / ")}</td><td class="num">${x.toFixed(2)}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">Base thresholds everywhere.</p>'}</div>
        <div class="card"><h2>Locally confirmed causes</h2>${Object.keys(m.hypothesis_counts).length ? `<table><tbody>${Object.entries(m.hypothesis_counts).map(([k, x]) => `<tr><td>${esc(k.replace(/_/g, " "))}</td><td class="num">${x}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">None yet.</p>'}</div></div>
      ${m.evaluation ? `<div class="card section"><h2>Validation against known outbreaks (simulation ground truth)</h2><p class="secondary">${esc(m.evaluation.description || "")}</p>
        <div class="table-wrap"><table><thead><tr><th>Scenario</th><th>Started</th><th>First alert</th><th class="num">Days to detect</th><th>Leading explanation at detection</th><th>Final explanation</th><th class="num">Final confidence</th></tr></thead><tbody>${m.evaluation.scenarios.map((s) => `<tr><td>${esc(s.name)}</td><td>${esc(s.start)}</td><td>${esc(s.first_alert || "not detected")}</td><td class="num">${s.days_to_detect ?? "–"}</td><td>${esc(s.first_hypothesis || "–")}</td><td>${esc(s.final_hypothesis || "–")}</td><td class="num">${s.final_confidence != null ? pct(s.final_confidence) : "–"}</td></tr>`).join("")}</tbody></table></div>
        <p>${esc(m.evaluation.false_alarm_summary || "")}</p></div>` : ""}
      <div class="card section"><h2>Model versions</h2>${m.versions.length ? `<div class="table-wrap"><table><thead><tr><th>Version</th><th>When</th><th>Trigger</th><th class="num">Examples</th></tr></thead><tbody>${m.versions.slice().reverse().map((x) => `<tr><td>v${x.version}</td><td>${esc(x.ts)}</td><td>${esc(x.trigger)}</td><td class="num">${x.examples}</td></tr>`).join("")}</tbody></table></div>` : '<p class="muted">Still on the expert prior (v0).</p>'}</div>
      <div class="card section"><h2>Feedback log</h2>${m.feedback.length ? m.feedback.map((f) => `<div class="analogue"><b>#${f.alert_id} ${esc(f.title)}</b>${learnHtml(f)}</div>`).join("") : '<p class="muted">No feedback yet.</p>'}</div>`;
    C.hbars(v.querySelector("#wts"), { labelWidth: 190, rows: names.map((n) => ({ label: n.replace(/_/g, " "), value: m.current_weights[n], display: `${m.current_weights[n].toFixed(2)} (${m.prior_weights[n]})`, tip: m.features.find((f) => f.name === n).description })) });
  }

  /* ------------------------------------------------------------------ security */
  async function security(v) {
    const p = await api("/api/security/posture");
    const icon = (s) => (s === "pass" ? `<span class="ok">${ICON.ok} pass</span>` : s === "warn" ? `<span>${ICON.warn} warning</span>` : `<span class="bad">${ICON.fail} fail</span>`);
    v.innerHTML = `<h1>Security & sovereignty</h1><p class="secondary">Live posture of this instance. Every item below is checked by the platform itself, not declared.</p>
      <div class="grid g4 section">
        <div class="card kpi"><div class="label">Jurisdiction</div><div class="value">${esc(p.residency.country_code)}</div><div class="sub">${esc(p.residency.jurisdiction)}</div></div>
        <div class="card kpi"><div class="label">Hosting region</div><div class="value" style="font-size:1.1rem">${esc(p.residency.hosting_region)}</div><div class="sub">approved: ${p.residency.approved_regions.map(esc).join(", ")}</div></div>
        <div class="card kpi"><div class="label">Audit trail</div><div class="value ${p.audit_chain.valid ? "ok" : "bad"}">${p.audit_chain.valid ? ICON.ok + " intact" : ICON.fail + " broken"}</div><div class="sub">${num(p.audit_chain.entries)} hash-chained entries</div></div>
        <div class="card kpi"><div class="label">Outbound network</div><div class="value ${p.egress_guard.installed ? "ok" : "bad"}">${p.egress_guard.installed ? ICON.ok + " guarded" : ICON.fail + " open"}</div><div class="sub">${p.egress_guard.blocked_attempts.length} blocked attempt(s) recorded</div></div></div>
      <div class="card section"><h2>Data-residency checks</h2><div class="table-wrap"><table><thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead><tbody>${p.checks.map((c) => `<tr><td>${esc(c.check)}</td><td>${icon(c.status)}</td><td class="secondary">${esc(c.detail)}</td></tr>`).join("")}</tbody></table></div></div>
      <div class="grid g2 section">
        <div class="card"><h2>Try to send data abroad</h2><p class="secondary">The process-level egress guard only allows connections to the in-country allow-list (${p.egress_guard.allow_list.map(esc).join(", ")}). Try any external address.</p>
          <div class="row"><input type="text" id="eurl" value="https://api.example-cloud.com/upload" style="flex:1;min-width:200px"><button class="btn primary" id="etest">Attempt connection</button></div><div id="eres" class="section"></div>
          ${p.egress_guard.blocked_attempts.length ? `<h3 class="section">Recently blocked</h3><table><tbody>${p.egress_guard.blocked_attempts.slice().reverse().map((b) => `<tr><td class="mono">${esc(b.destination)}</td><td>${esc(b.stage)}</td><td>${esc(b.ts)}</td></tr>`).join("")}</tbody></table>` : ""}</div>
        <div class="card"><h2>Encryption keys</h2><div class="table-wrap"><table><thead><tr><th>Key</th><th>Algorithm</th><th>Key ID</th><th>Storage</th></tr></thead><tbody>${p.keys.map((k) => `<tr><td>${esc(k.name)}</td><td>${esc(k.algorithm)}</td><td class="mono">${esc(k.key_id)}</td><td class="secondary">${esc(k.storage)}</td></tr>`).join("")}</tbody></table></div>
          <p class="secondary">kek: wraps a fresh AES-256 key for every stored source message. pseudonym: keyed hashing of national IDs. token: signs session tokens.</p></div></div>
      <div class="grid g2 section">
        <div class="card"><h2>Data minimisation</h2><h4>Stored for analysis</h4><ul>${p.data_minimisation.stored.map((x) => `<li>${esc(x)}</li>`).join("")}</ul><h4>Never stored for analysis</h4><ul>${p.data_minimisation.not_stored_in_analytics.map((x) => `<li>${esc(x)}</li>`).join("")}</ul><p class="secondary">${esc(p.data_minimisation.raw_messages)}</p></div>
        <div class="card"><h2>Data inventory</h2><table><tbody>${Object.entries(p.inventory).map(([k, x]) => `<tr><td>${esc(k.replace("_", " "))}</td><td class="num">${num(x)}</td></tr>`).join("")}</tbody></table><p class="secondary">Data directory: <span class="mono">${esc(p.residency.data_dir)}</span> · environment ${esc(p.residency.environment)}</p></div></div>`;
    v.querySelector("#etest").addEventListener("click", async () => {
      const res = await api("/api/security/egress-test", { method: "POST", body: JSON.stringify({ url: v.querySelector("#eurl").value }) });
      v.querySelector("#eres").innerHTML = res.blocked ? `<p class="ok"><b>${ICON.ok} Blocked.</b> ${esc(res.result)}. The attempt is recorded in the audit trail.</p>` : `<p class="bad"><b>Not blocked:</b> ${esc(res.result)}</p>`;
    });
  }

  async function auditPage(v) {
    let offset = 0, action = "";
    v.innerHTML = `<h1>Audit trail</h1><p class="secondary">Append-only, hash-chained record of every login, data view, export, break-glass access, agent run, notification and feedback. Altering or deleting any entry breaks the chain.</p>
      <div class="row section"><button class="btn primary" id="ver">Verify chain integrity</button><span id="vres"></span><span class="spacer"></span><select id="act" style="width:auto"><option value="">All actions</option>${["auth", "view", "export", "breakglass", "agent", "alert", "feedback", "ingest", "feed", "security", "source", "demo", "access"].map((a) => `<option>${a}</option>`).join("")}</select></div>
      <div class="card section" id="atbl"></div>`;
    const load = async () => {
      const r = await api(`/api/audit?limit=100&offset=${offset}${action ? "&action=" + action : ""}`);
      v.querySelector("#atbl").innerHTML = `<div class="table-wrap"><table><thead><tr><th>#</th><th>Time (UTC)</th><th>Actor</th><th>Role</th><th>Action</th><th>Resource</th><th>Details</th><th>Hash</th></tr></thead><tbody>${r.rows.map((x) => `<tr><td>${x.id}</td><td>${esc(x.ts)}</td><td>${esc(x.actor)}</td><td>${esc(x.role)}</td><td><b>${esc(x.action)}</b></td><td>${esc(x.resource)}</td><td class="secondary" style="max-width:360px">${esc(JSON.stringify(x.details)).slice(0, 220)}</td><td class="mono">${esc(x.hash.slice(0, 10))}…</td></tr>`).join("")}</tbody></table></div>
        <div class="row section"><button class="btn sm" id="ap" ${offset ? "" : "disabled"}>← Newer</button><span class="muted">${offset + 1}-${Math.min(offset + 100, r.total)} of ${num(r.total)}</span><button class="btn sm" id="an" ${offset + 100 < r.total ? "" : "disabled"}>Older →</button></div>`;
      v.querySelector("#ap").addEventListener("click", () => { offset -= 100; load(); });
      v.querySelector("#an").addEventListener("click", () => { offset += 100; load(); });
    };
    v.querySelector("#act").addEventListener("change", (e) => { action = e.target.value; offset = 0; load(); });
    v.querySelector("#ver").addEventListener("click", async () => { const r = await api("/api/audit/verify"); v.querySelector("#vres").innerHTML = r.valid ? `<b class="ok">${ICON.ok} ${esc(r.message)} (${num(r.entries)} entries)</b>` : `<b class="bad">${ICON.fail} ${esc(r.message)}</b>`; });
    await load();
  }

  /* ------------------------------------------------------------------ demo */
  async function demo(v) {
    const [clk, sc, runs] = await Promise.all([api("/api/demo/clock"), api("/api/demo/scenarios"), api("/api/agent/runs?limit=15")]);
    v.innerHTML = `<h1>Demo control</h1><p class="secondary">The simulated clock decides which data has "arrived". Advancing a day renders that day's messages in each institution's native format (FHIR, HL7 v2, claims and death-claim CSVs, lab reports), pushes them through the real connectors, then runs the agent.</p>
      <div class="grid g2 section"><div class="card"><h2>Clock: ${esc(clk.clock)}</h2><p>History was bulk-loaded up to ${esc(clk.backfill_end || "–")}, as on day one of a real deployment.</p>
        <div class="row"><button class="btn primary" id="d1">Advance 1 day</button><button class="btn" id="d7">Advance 7 days</button>${can("run_agent") ? '<button class="btn" id="run">Re-run agent now</button>' : ""}</div>
        <h3 class="section">Restart the story</h3><div class="row"><input type="text" id="rw" value="${esc(clk.backfill_end || "")}" style="width:160px"><button class="btn danger" id="rwb">Rewind and replay to this date</button></div><small class="muted">Restores the post-back-fill snapshot and replays day by day (about 8 seconds per simulated day).</small></div>
        <div class="card"><h2>Recent agent runs</h2><div class="table-wrap"><table><thead><tr><th>Run</th><th>As of</th><th class="num">Series</th><th class="num">Signals</th><th class="num">Events</th></tr></thead><tbody>${runs.map((r) => `<tr><td>#${r.id}</td><td>${esc(r.as_of)}</td><td class="num">${r.n_series}</td><td class="num">${r.n_signals}</td><td class="num">${r.n_events}</td></tr>`).join("")}</tbody></table></div></div></div>
      <div class="card section"><div class="row between"><h2>Ground truth (presenter only)</h2><button class="btn sm" id="rev">Reveal</button></div><p class="secondary">What was actually simulated. The agent never reads this; use it at the end of a demo to show what it found on its own.</p><div id="truth" hidden>${sc.outbreaks.map((o) => `<div class="analogue"><b>${esc(o.name)}</b><p class="muted">${esc(o.start)}${o.end ? " to " + esc(o.end) : ""} · ${esc(o.location)} · ${esc(o.pathogen || "not a pathogen")}</p><p>${esc(o.description)}</p></div>`).join("")}</div></div>`;
    v.querySelector("#d1").addEventListener("click", (e) => advance(1, e.target));
    v.querySelector("#d7").addEventListener("click", (e) => advance(7, e.target));
    const run = v.querySelector("#run");
    if (run) run.addEventListener("click", async () => { run.disabled = true; run.innerHTML = '<span class="spinner"></span> Running…'; const r = await api("/api/agent/run", { method: "POST" }); toast(`Run #${r.run_id}: ${r.signals} signals, ${r.events} events`); route(); });
    v.querySelector("#rev").addEventListener("click", () => { v.querySelector("#truth").hidden = !v.querySelector("#truth").hidden; });
    v.querySelector("#rwb").addEventListener("click", async (e) => {
      if (!confirm("Rewind the demo? Alerts and feedback created after the back-fill will be discarded.")) return;
      e.target.disabled = true; e.target.innerHTML = '<span class="spinner"></span> Rewinding…';
      try { const r = await api("/api/demo/rewind", { method: "POST", body: JSON.stringify({ date: v.querySelector("#rw").value }) }); S.clock = r.clock; document.getElementById("clk").textContent = r.clock; toast("Rewound to " + r.clock); refreshCount(); route(); } catch (err) { toast(err.message); }
    });
  }

  /* ------------------------------------------------------------------ start */
  if (S.token) boot().catch(() => logout()); else renderLogin();
  let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(route, 300); });
})();
