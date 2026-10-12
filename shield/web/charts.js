/* Minimal SVG charts for Shield AI. No third-party code: everything the browser runs is served by the platform. */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  let tip;
  function tooltip() {
    if (!tip) { tip = document.createElement("div"); tip.className = "tooltip"; document.body.appendChild(tip); }
    return tip;
  }
  function showTip(evt, html) {
    const t = tooltip();
    t.innerHTML = html; t.style.display = "block";
    const w = t.offsetWidth, h = t.offsetHeight;
    let x = evt.clientX + 14, y = evt.clientY + 14;
    if (x + w > window.innerWidth - 8) x = evt.clientX - w - 14;
    if (y + h > window.innerHeight - 8) y = evt.clientY - h - 14;
    t.style.left = x + "px"; t.style.top = y + "px";
  }
  function hideTip() { if (tip) tip.style.display = "none"; }
  function niceMax(v) {
    if (v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }
  const fmt = (v) => (v == null || isNaN(v) ? "–" : Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : (+v).toFixed(v % 1 ? 1 : 0));
  const shortDate = (d) => { const [y, m, dd] = d.split("-"); return `${+dd} ${["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+m - 1]}`; };

  /* Time chart: series of {name, values, type: line|bar|band|area, color, lower, upper, dash} */
  function timeChart(container, opts) {
    container.innerHTML = "";
    container.classList.add("chart");
    const { dates, series } = opts;
    const W = Math.max(container.clientWidth || 600, 280), H = opts.height || 240;
    const m = { l: 40, r: 12, t: 10, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    if (opts.legend !== false && series.length > 1) {
      const lg = document.createElement("div"); lg.className = "legend";
      lg.innerHTML = series.filter((s) => !s.hideLegend).map((s) => `<span><i class="${s.type === "bar" || s.type === "band" ? "sq" : ""}" style="background:${s.type === "band" ? css("--band") : s.color}"></i>${esc(s.name)}</span>`).join("");
      container.appendChild(lg);
    }
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.label || "time series chart" });
    container.appendChild(svg);
    let ymax = 0;
    for (const s of series) for (const arr of [s.values, s.upper]) if (arr) for (const v of arr) if (v != null && !isNaN(v)) ymax = Math.max(ymax, v);
    ymax = niceMax(ymax * 1.05);
    const n = dates.length;
    const x = (i) => m.l + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
    const bw = Math.max(1, Math.min(14, iw / n - 2));
    const y = (v) => m.t + ih - (v / ymax) * ih;
    const g = el("g", { class: "axis" }, svg);
    for (let k = 0; k <= 4; k++) {
      const v = (ymax * k) / 4;
      el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: "gridline" }, g);
      el("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, g).textContent = fmt(v);
    }
    const ticks = Math.min(6, n);
    for (let k = 0; k < ticks; k++) {
      const i = Math.round((k * (n - 1)) / Math.max(ticks - 1, 1));
      el("text", { x: x(i), y: H - 6, "text-anchor": k === 0 ? "start" : k === ticks - 1 ? "end" : "middle" }, g).textContent = shortDate(dates[i]);
    }
    for (const s of series) {
      if (s.type === "band") {
        let d = "", started = false;
        const pts = [];
        s.upper.forEach((u, i) => { if (u != null && !isNaN(u)) pts.push([i, u, s.lower ? s.lower[i] : 0]); });
        if (pts.length) {
          d = pts.map((p, k) => `${k ? "L" : "M"}${x(p[0])},${y(p[1])}`).join("") + pts.slice().reverse().map((p) => `L${x(p[0])},${y(Math.max(p[2], 0))}`).join("") + "Z";
          el("path", { d, fill: css("--band"), stroke: "none" }, svg);
        }
      }
    }
    for (const s of series) {
      if (s.type === "bar") {
        s.values.forEach((v, i) => {
          if (!v) return;
          const h = Math.max(ih - (y(v) - m.t), 0);
          const r = Math.min(4, bw / 2, h);
          const x0 = x(i) - bw / 2, y0 = y(v), x1 = x0 + bw, yb = m.t + ih;
          el("path", { d: `M${x0},${yb}V${y0 + r}Q${x0},${y0} ${x0 + r},${y0}H${x1 - r}Q${x1},${y0} ${x1},${y0 + r}V${yb}Z`, fill: s.color, opacity: s.highlight && !s.highlight[i] ? 0.45 : 1 }, svg);
        });
      }
    }
    for (const s of series) {
      if (s.type === "line" || !s.type) {
        let d = "", pen = false;
        s.values.forEach((v, i) => { if (v == null || isNaN(v)) { pen = false; return; } d += `${pen ? "L" : "M"}${x(i)},${y(v)}`; pen = true; });
        el("path", { d, fill: "none", stroke: s.color, "stroke-width": s.width || 2, "stroke-dasharray": s.dash || "", "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
      }
    }
    const cross = el("line", { y1: m.t, y2: m.t + ih, stroke: css("--text-muted"), "stroke-width": 1, opacity: 0 }, svg);
    const hit = el("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" }, svg);
    hit.addEventListener("mousemove", (evt) => {
      const r = svg.getBoundingClientRect();
      const px = ((evt.clientX - r.left) / r.width) * W;
      const i = Math.max(0, Math.min(n - 1, Math.round(((px - m.l) / iw) * (n - 1))));
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("opacity", 0.6);
      const rows = series.filter((s) => s.type !== "band" || s.upper).map((s) => {
        const v = s.type === "band" ? s.upper[i] : s.values[i];
        return `<div class="r"><span><i style="background:${s.type === "band" ? css("--series-1") : s.color}"></i>${esc(s.type === "band" ? s.name : s.name)}</span><b>${fmt(v)}</b></div>`;
      }).join("");
      showTip(evt, `<div class="t">${esc(dates[i])}</div>${rows}`);
    });
    hit.addEventListener("mouseleave", () => { cross.setAttribute("opacity", 0); hideTip(); });
  }

  /* Grouped vertical bars: categories x series */
  function groupedBars(container, opts) {
    container.innerHTML = ""; container.classList.add("chart");
    const { categories, series } = opts;
    const W = Math.max(container.clientWidth || 500, 260), H = opts.height || 220;
    const m = { l: 38, r: 8, t: 8, b: 26 }, iw = W - m.l - m.r, ih = H - m.t - m.b;
    if (series.length > 1) {
      const lg = document.createElement("div"); lg.className = "legend";
      lg.innerHTML = series.map((s) => `<span><i class="sq" style="background:${s.color}"></i>${esc(s.name)}</span>`).join("");
      container.appendChild(lg);
    }
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.label || "bar chart" });
    container.appendChild(svg);
    let ymax = 0; series.forEach((s) => s.values.forEach((v) => (ymax = Math.max(ymax, v || 0))));
    ymax = niceMax(ymax * 1.05);
    const y = (v) => m.t + ih - (v / ymax) * ih;
    const g = el("g", { class: "axis" }, svg);
    for (let k = 0; k <= 4; k++) {
      const v = (ymax * k) / 4;
      el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: "gridline" }, g);
      el("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, g).textContent = opts.percent ? Math.round(v * 100) + "%" : fmt(v);
    }
    const gw = iw / categories.length, bw = Math.min(22, (gw - 8) / series.length - 2);
    categories.forEach((c, ci) => {
      el("text", { x: m.l + gw * ci + gw / 2, y: H - 6, "text-anchor": "middle" }, g).textContent = c;
      series.forEach((s, si) => {
        const v = s.values[ci] || 0;
        const x0 = m.l + gw * ci + gw / 2 - (series.length * (bw + 2)) / 2 + si * (bw + 2);
        const h = Math.max(m.t + ih - y(v), 0), r = Math.min(4, bw / 2, h), y0 = y(v), x1 = x0 + bw, yb = m.t + ih;
        const p = el("path", { d: h > 0 ? `M${x0},${yb}V${y0 + r}Q${x0},${y0} ${x0 + r},${y0}H${x1 - r}Q${x1},${y0} ${x1},${y0 + r}V${yb}Z` : "", fill: s.color }, svg);
        const hit = el("rect", { x: x0 - 1, y: m.t, width: bw + 2, height: ih, fill: "transparent" }, svg);
        hit.addEventListener("mousemove", (evt) => showTip(evt, `<div class="t">${esc(c)}</div>` + series.map((ss) => `<div class="r"><span><i style="background:${ss.color}"></i>${esc(ss.name)}</span><b>${opts.percent ? Math.round((ss.values[ci] || 0) * 100) + "%" : fmt(ss.values[ci])}</b></div>`).join("")));
        hit.addEventListener("mouseleave", hideTip);
      });
    });
  }

  /* Horizontal bars, optionally diverging around zero */
  function hbars(container, opts) {
    container.innerHTML = ""; container.classList.add("chart");
    const rows = opts.rows;
    const rowH = 26, W = Math.max(container.clientWidth || 500, 260), labelW = Math.min(opts.labelWidth || 190, W * 0.45);
    const H = rows.length * rowH + 8;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.label || "bar chart" });
    container.appendChild(svg);
    const max = Math.max(...rows.map((r) => Math.abs(r.value)), opts.max || 0, 1e-9);
    const plotW = W - labelW - 56;
    const diverging = rows.some((r) => r.value < 0);
    const zero = labelW + (diverging ? plotW / 2 : 0);
    const scale = (diverging ? plotW / 2 : plotW) / max;
    if (diverging) el("line", { x1: zero, x2: zero, y1: 0, y2: H, stroke: css("--grid") }, svg);
    rows.forEach((r, i) => {
      const y0 = i * rowH + 6;
      const t = el("text", { x: labelW - 8, y: y0 + 13, "text-anchor": "end", fill: css("--text-secondary"), "font-size": 12 }, svg);
      t.textContent = r.label.length > 34 ? r.label.slice(0, 33) + "…" : r.label;
      const w = Math.abs(r.value) * scale;
      const x0 = r.value >= 0 ? zero : zero - w;
      const color = r.color || (r.value >= 0 ? css("--div-neg") : css("--div-pos"));
      el("rect", { x: x0, y: y0 + 2, width: Math.max(w, 1), height: 14, rx: 3, fill: color }, svg);
      const vt = el("text", { x: r.value >= 0 ? zero + w + 6 : x0 - 6, y: y0 + 13, "text-anchor": r.value >= 0 ? "start" : "end", fill: css("--text-primary"), "font-size": 12 }, svg);
      vt.textContent = r.display ?? fmt(r.value);
      const hit = el("rect", { x: 0, y: y0, width: W, height: rowH, fill: "transparent" }, svg);
      hit.addEventListener("mousemove", (evt) => showTip(evt, `<div class="t">${esc(r.label)}</div><div>${esc(r.tip || r.display || fmt(r.value))}</div>`));
      hit.addEventListener("mouseleave", hideTip);
    });
  }

  /* Diverging colour for a ratio (1 = neutral) */
  function hexToRgb(h) { h = h.replace("#", ""); if (h.length === 3) h = h.split("").map((c) => c + c).join(""); const n = parseInt(h, 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  function mix(a, b, t) { const A = hexToRgb(a), B = hexToRgb(b); return `rgb(${A.map((v, i) => Math.round(v + (B[i] - v) * t)).join(",")})`; }
  function ratioColor(r) {
    const l = Math.max(-1.5, Math.min(1.5, Math.log2(Math.max(r, 0.01))));
    const t = Math.abs(l) / 1.5;
    return mix(css("--div-mid"), l >= 0 ? css("--div-pos") : css("--div-neg"), Math.pow(t, 0.8));
  }

  /* Choropleth of neighbourhood polygons. features: [{id, name, polygon:[[x,y]...], value, tip}] */
  function choropleth(container, opts) {
    container.innerHTML = ""; container.classList.add("chart", "map-wrap");
    const f = opts.features;
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    f.forEach((n) => n.polygon.forEach(([px, py]) => { minx = Math.min(minx, px); miny = Math.min(miny, py); maxx = Math.max(maxx, px); maxy = Math.max(maxy, py); }));
    const W = 480, pad = 6, s = (W - 2 * pad) / (maxx - minx), H = (maxy - miny) * s + 2 * pad;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.label || "map" });
    container.appendChild(svg);
    f.forEach((n) => {
      const d = n.polygon.map(([px, py], i) => `${i ? "L" : "M"}${pad + (px - minx) * s},${pad + (py - miny) * s}`).join("") + "Z";
      const p = el("path", { d, class: "nb" + (opts.highlight && opts.highlight.includes(n.id) ? " hl" : ""), fill: n.value == null ? css("--surface-2") : ratioColor(n.value) }, svg);
      p.addEventListener("mousemove", (evt) => showTip(evt, `<div class="t">${esc(n.name)}</div>${n.tip || ""}`));
      p.addEventListener("mouseleave", hideTip);
      if (opts.onClick) p.addEventListener("click", () => opts.onClick(n));
      const cx = n.polygon.reduce((a, q) => a + q[0], 0) / n.polygon.length, cy = n.polygon.reduce((a, q) => a + q[1], 0) / n.polygon.length;
      const t = el("text", { x: pad + (cx - minx) * s, y: pad + (cy - miny) * s, "text-anchor": "middle" }, svg);
      t.textContent = n.name.length > 14 ? n.name.replace("University", "Univ.") : n.name;
    });
    const sc = document.createElement("div"); sc.className = "scale";
    sc.innerHTML = `<span>${esc(opts.scaleLabel || "")}</span>` + [0.4, 0.6, 0.8, 1, 1.25, 1.6, 2.5].map((r) => `<span class="sw" title="${r}x" style="background:${ratioColor(r)}"></span>`).join("") + "<span>lower ← usual → higher</span>";
    container.appendChild(sc);
  }

  /* Confidence gauge (ring) */
  function gauge(p, size = 56) {
    const r = size / 2 - 5, c = 2 * Math.PI * r, v = Math.max(0, Math.min(1, p));
    const color = p >= 0.8 ? css("--critical") : p >= 0.6 ? css("--serious") : p >= 0.4 ? css("--warning") : css("--text-muted");
    return `<svg class="gauge" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" aria-label="confidence ${Math.round(p * 100)}%"><circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${css("--surface-2")}" stroke-width="6"/><circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="6" stroke-linecap="round" stroke-dasharray="${c * v} ${c}" transform="rotate(-90 ${size / 2} ${size / 2})"/><text x="50%" y="54%" text-anchor="middle" dominant-baseline="middle" font-size="${Math.round(p * 100) >= 100 ? size / 5 : size / 4}">${Math.round(p * 100)}%</text></svg>`;
  }

  window.ShieldCharts = { timeChart, groupedBars, hbars, choropleth, gauge, ratioColor, css, esc, shortDate, hideTip };
})();
