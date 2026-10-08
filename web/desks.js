/* JalTaap 2 — the six hazard desks: Heat, Flood, Drought, Groundwater, Tankers, Leaks.
   Each desk registers with the core in app.js (window.JT) and only talks to our own /api endpoints. */
(() => {
  "use strict";
  const { $, $$, api, post, fmt, esc, C, LV, WORD, S, map, layer, div, legend, maptools, mapcard, sw, chart, grid,
    register, placeLine, loading, errBox, stamp, bars, meter, niceDate, dayName, hr12, toast, speak } = window.JT;

  // map layers used here; created up front so the core can show/hide them per desk
  ["pockets", "underpasses", "drains", "tankers", "pipes"].forEach((n) => layer(n));

  const q = () => `lat=${S.place.lat}&lon=${S.place.lon}`;
  // write into an element only if it is still on screen (the user may have switched desks mid-fetch)
  const put = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; return el; };
  const head = (kicker, title, lede) => `<p class="kicker">${kicker}</p><h1 class="head">${title}</h1>${placeLine()}${lede ? `<p class="lede">${lede}</p>` : ""}`;
  const lvl = (l) => ({ moderate: "orange", high: "red", severe: "red", low: "green" }[l] || l);
  const tones = { green: C.ok, orange: C.warn, watch: C.warn, red: C.bad };
  const slider = (id, label, min, max, step, val, unit = "") =>
    `<div class="slider"><label for="${id}">${esc(label)}<input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${val}"></label><output id="${id}-o">${val}${unit}</output></div>`;
  const bindSlider = (id, unit, cb) => {
    const el = document.getElementById(id); if (!el) return;
    el.addEventListener("input", () => { document.getElementById(`${id}-o`).textContent = el.value + unit; cb && cb(); });
  };
  const debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  const tabs = (id, names, active) => `<div class="tabs" id="${id}" role="tablist">${names.map((n) => `<button role="tab" data-t="${esc(n)}" aria-selected="${n === active}">${esc(n)}</button>`).join("")}</div>`;
  const onTabs = (id, cb) => $$(`#${id} button`).forEach((b) => b.addEventListener("click", () => {
    $$(`#${id} button`).forEach((x) => x.setAttribute("aria-selected", String(x === b))); cb(b.dataset.t);
  }));
  const fitTo = (pts, pad = 40) => { if (pts.length) map.fitBounds(L.latLngBounds(pts), { padding: [pad, pad], maxZoom: 15, animate: !window.JT.reduceMotion }); };

  // =====================================================================
  // HEAT: how hard the body works, who is exposed, where the hot pockets are
  // =====================================================================
  register("heat", {
    title: "How hard is the heat <em>on the body</em>?", kicker: "Heat desk", needsPlace: true, layers: ["pockets"], zoom: 11,
    intro: "Thermometer readings miss humidity, sun and wind. This desk blends three body-heat indices, then weighs who actually lives here.",
    render(el) {
      el.innerHTML = head("Heat desk", "How hard is the heat <em>on the body</em>?",
        "One score from Heat Index, WBGT and UTCI. It never reads lower than the worst of the three, so a dangerous humid day can't hide behind a mild thermometer.") + `
        <section class="sec"><h2>Body heat load <span class="aside">0–100</span></h2><div id="h-load">${loading("Working out heat load hour by hour…")}</div></section>
        <section class="sec"><h2>Who lives here?</h2>
          <p class="sub">Same heat, very different danger. Pick the closest picture of this area, then adjust.</p>
          <div class="chips" id="h-presets"></div><div id="h-sliders"></div><div id="h-vuln"></div></section>
        <section class="sec"><h2>Outdoor work planner</h2><p class="sub">WBGT by hour with work–rest cycles for light, moderate and heavy work.</p><div id="h-work">${loading("Planning the working day…")}</div></section>
        <section class="sec"><h2>Hot pockets nearby <span class="aside">49-point grid</span></h2><div id="h-pockets">${loading("Scanning a 12 km square around the pin…")}</div></section>
        <section class="sec"><h2>Heat drills</h2><p class="sub">Try a textbook heat day to see how the score reacts.</p><div class="chips" id="h-drills"></div><div id="h-drill"></div></section>
        <section class="sec"><h2>Is it getting worse here? <span class="aside">1995 →</span></h2><div id="h-trend">${loading("Counting extreme days per year…")}</div></section>`;
      heatLoad(); heatVuln(); heatWork(); heatPockets(); heatDrills(); heatTrend();
    },
  });

  let heatDays = null;
  async function heatLoad() {
    let d;
    try { d = await api(`/api/heat/load?${q()}`); } catch (e) { return put("h-load", errBox(e)); }
    heatDays = d.days;
    const t = d.days[0];
    const names = { wbgt: "Sun + effort (WBGT)", utci: "Air + wind + radiation (UTCI)", hi: "Humid air (Heat Index)" };
    if (!put("h-load", `
      <div class="stats"><div><div class="v ${t.level}">${t.score}</div><div class="l">today · ${esc(t.label)}</div></div>
        <div><div class="v">${hr12(t.peak_hour)}</div><div class="l">worst hour</div></div>
        <div><div class="v">${fmt(t.night_min, 1)}<small>°C</small></div><div class="l">night low${t.night_bonus ? ` (+${t.night_bonus} for no night relief)` : ""}</div></div></div>
      <div class="days" style="grid-template-columns:repeat(${d.days.length},1fr)">${d.days.map((x) => `<div class="day ${x.level === "green" ? "" : x.level}">
        <div class="dn">${dayName(x.date)}</div><div class="df">${x.score}</div><div class="dx">${esc(x.label)}</div></div>`).join("")}</div>
      <div class="chart"><canvas id="c-load"></canvas></div>
      <h3 style="font:600 14px var(--serif);margin:10px 0 6px">What is driving it today</h3>
      ${bars(Object.entries(t.share).map(([k, v]) => ({ label: names[k] || k, v, text: `${v}%` })), "heat", 100)}
      <div class="flag">${esc(t.advice)}</div>
      <p class="fine">${esc(d.source)} · method: ${esc(d.method)}</p>`)) return;
    const hrs = d.hours.slice(0, 72);
    chart("load", $("#c-load"), {
      type: "line",
      data: { labels: hrs.map((h) => h.time), datasets: [
        { label: "Heat load", data: hrs.map((h) => h.score), borderColor: C.heat, backgroundColor: "rgba(190,74,30,.12)", fill: true, pointRadius: 0, borderWidth: 2, yAxisID: "y" },
        { label: "WBGT °C", data: hrs.map((h) => h.wbgt), borderColor: C.ink, borderDash: [4, 3], pointRadius: 0, borderWidth: 1.4, yAxisID: "t" },
        { label: "UTCI °C", data: hrs.map((h) => h.utci), borderColor: C.water, pointRadius: 0, borderWidth: 1.2, yAxisID: "t" },
      ] },
      options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: true, labels: { boxWidth: 10 } } },
        scales: { x: { grid, ticks: { maxTicksLimit: 6, callback: (v, i) => (hrs[i].hour === 0 ? dayName(hrs[i].time) : hrs[i].hour === 12 ? "noon" : "") } },
          y: { grid, min: 0, max: 100, title: { display: true, text: "load" } }, t: { position: "right", grid: { display: false }, title: { display: true, text: "°C" } } } },
    });
  }

  let vulnProfile = null;
  function heatVuln() {
    const m = S.meta || {};
    if (!m.presets) return put("h-vuln", errBox("Server meta not loaded."));
    vulnProfile = vulnProfile || { ...m.presets.informal };
    put("h-presets", Object.entries(m.presets).map(([k, p]) => `<button data-k="${k}" aria-pressed="${vulnProfile.label === p.label}">${esc(p.label)}</button>`).join(""));
    const ranges = { help_km: [0, 15, 0.5] };
    put("h-sliders", `<div class="grid2">${Object.entries(m.factors).map(([k, label]) => {
      const [mn, mx, st] = ranges[k] || [0, 100, 1];
      return slider(`hv-${k}`, label, mn, mx, st, vulnProfile[k], k === "help_km" ? " km" : "%");
    }).join("")}</div>`);
    const run = debounce(async () => {
      Object.keys(m.factors).forEach((k) => (vulnProfile[k] = +$(`#hv-${k}`).value));
      try {
        const r = await post("/api/heat/vulnerability", { lat: S.place.lat, lon: S.place.lon, profile: vulnProfile });
        const v = r.vulnerability, w = r.worst;
        put("h-vuln", `
          <div class="stats"><div><div class="v ${lvl(v.level)}">${v.score}</div><div class="l">vulnerability (${esc(v.level)})</div></div>
            <div><div class="v ${w.level}">T${w.tier}</div><div class="l">worst day: ${esc(w.name)} · ${niceDate(w.date)}</div></div></div>
          ${bars(v.parts.map((p) => ({ label: p.label, v: p.points, text: fmt(p.points, 0) })), "heat", Math.max(...v.parts.map((p) => p.max)))}
          <table class="t" style="margin-top:10px"><thead><tr><th>Day</th><th>Load</th><th>Tier</th><th>What to do</th></tr></thead><tbody>
          ${r.days.map((d) => `<tr><td>${dayName(d.date)}</td><td class="n">${d.heat_load}</td><td><span class="pill ${d.level}">T${d.tier} ${esc(d.name)}</span></td>
            <td><ul class="plain" style="margin:0">${d.actions.slice(0, 3).map((a) => `<li>${esc(a)}</li>`).join("")}</ul></td></tr>`).join("")}</tbody></table>`);
      } catch (e) { put("h-vuln", errBox(e)); }
    }, 250);
    Object.keys(m.factors).forEach((k) => bindSlider(`hv-${k}`, k === "help_km" ? " km" : "%", () => {
      vulnProfile.label = "Custom"; $$("#h-presets button").forEach((b) => b.setAttribute("aria-pressed", "false")); run();
    }));
    $$("#h-presets button").forEach((b) => b.addEventListener("click", () => { vulnProfile = { ...m.presets[b.dataset.k] }; heatVuln(); }));
    run();
  }

  async function heatWork() {
    let d;
    try { d = await api(`/api/workheat?${q()}`); } catch (e) { return put("h-work", errBox(e)); }
    const now = new Date();
    put("h-work", d.days.map((day) => {
      const hrs = d.hours.filter((h) => h.time.startsWith(day.date) && h.hour >= 6 && h.hour <= 20);
      return `<div class="workday"><div class="wh"><b>${dayName(day.date)} ${niceDate(day.date)}</b><span class="pill ${day.level}">worst ${fmt(day.worst_wbgt, 1)}°C at ${hr12(day.worst_hour)}</span></div>
        <div class="strip">${hrs.map((h) => `<div class="h ${new Date(h.time) < now - 3600e3 ? "past" : h.level === "green" ? "" : h.level}" title="${hr12(h.hour)} · WBGT ${h.wbgt}°C · ${esc(h.label)}">${h.hour % 3 === 0 ? `<span>${hr12(h.hour)}</span>` : ""}</div>`).join("")}</div>
        <div class="wt">${day.risky_hours.length ? `Avoid hard work ${esc(day.risky_hours.join(", "))}. ` : "No risky hours. "}Light: ${esc(day.plan.light)} · moderate: ${esc(day.plan.moderate)} · heavy: ${esc(day.plan.heavy)}. Water: ${esc(day.water)}.</div></div>`;
    }).join("") + `<p class="fine">${esc(d.source)}</p>`);
  }

  async function heatPockets() {
    let d;
    try { d = await api(`/api/heat/pockets?${q()}`); } catch (e) { return put("h-pockets", errBox(e)); }
    if (S.desk !== "heat") return;
    const draw = (mode) => {
      const L1 = layer("pockets"); L1.clearLayers();
      const key = mode === "night" ? "night_anom" : "day_anom";
      const half = d.step_deg / 2;
      d.cells.forEach((c) => {
        const a = c[key];
        const col = a > 0 ? `rgba(190,74,30,${Math.min(0.75, 0.15 + a * 0.35)})` : `rgba(31,97,112,${Math.min(0.5, 0.1 - a * 0.3)})`;
        L.rectangle([[c.lat - half, c.lon - half], [c.lat + half, c.lon + half]], { color: "transparent", fillColor: col, fillOpacity: 1, weight: 0 })
          .bindTooltip(`peak feels-like ${c.day_peak}°C · night ${c.night_min}°C<br>${a >= 0 ? "+" : ""}${a}°C vs neighbours`, { className: "tt" }).addTo(L1);
      });
      d.pockets.forEach((p, i) => L.marker([p.lat, p.lon], { icon: div("mk-tank", `H${i + 1}`, 22) }).addTo(L1));
    };
    draw("day");
    maptools(`<label><input type="radio" name="pk" value="day" checked> Day peak</label><label><input type="radio" name="pk" value="night"> Night (no relief)</label>`);
    $$('#maptools input[name="pk"]').forEach((r) => r.addEventListener("change", () => draw(r.value)));
    legend(`<div class="ttl">Hotter or cooler than the area average</div>${sw("rgba(190,74,30,.7)", "hotter")}${sw("rgba(31,97,112,.4)", "cooler")}${sw(C.ink, "H = hot pocket")}`);
    put("h-pockets", `<div class="stats"><div><div class="v">${fmt(d.spread_day, 1)}<small>°C</small></div><div class="l">day spread across the square</div></div>
      <div><div class="v">${fmt(d.uhi_night, 1)}<small>°C</small></div><div class="l">night heat-island signal</div></div>
      <div><div class="v">${d.pockets.length}</div><div class="l">hot pockets found</div></div></div>
      ${d.pockets.length ? `<ul class="ledger">${d.pockets.map((p, i) => `<li data-ll="${p.lat},${p.lon}"><span class="sw red"></span><span><span class="t">H${i + 1} · ${p.area_km2} km²</span><br><span class="s">+${p.day_excess}°C by day, +${p.night_excess}°C at night</span></span><span class="v">${p.peak_feels}°<small>feels</small></span></li>`).join("")}</ul>`
        : '<p class="empty">No cluster stands out from its neighbours. Heat here is fairly even.</p>'}
      <p class="fine">${esc(d.source)}. Hot pockets are where cooling centres, shade nets and water points help most.</p>`);
    $$("#h-pockets li[data-ll]").forEach((li) => li.addEventListener("click", () => map.flyTo(li.dataset.ll.split(",").map(Number), 14)));
    map.flyTo([S.place.lat, S.place.lon], 12, { duration: 0.6 });
  }

  function heatDrills() {
    const drills = (S.meta || {}).drills || {};
    put("h-drills", Object.entries(drills).map(([k, v]) => `<button data-k="${k}">${esc(v)}</button>`).join(""));
    $$("#h-drills button").forEach((b) => b.addEventListener("click", async () => {
      $$("#h-drills button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      try {
        const r = await api(`/api/heat/drill?key=${b.dataset.k}&${q()}`);
        put("h-drill", `<div class="box"><h3>${esc(r.label)}</h3><p class="fine" style="margin:0 0 8px">${r.t}°C air, ${r.rh}% humidity, wind ${r.wind} m/s, sun ${r.sw} W/m²</p>
          <div class="stats" style="margin:0"><div><div class="v ${r.level}">${fmt(r.score)}</div><div class="l">${esc(r.band)}</div></div>
          <div><div class="v">${r.hi}</div><div class="l">Heat Index</div></div><div><div class="v">${r.wbgt}</div><div class="l">WBGT</div></div><div><div class="v">${r.utci}</div><div class="l">UTCI</div></div></div></div>`);
      } catch (e) { put("h-drill", errBox(e)); }
    }));
  }

  async function heatTrend() {
    let d;
    try { d = await api(`/api/heat-trend?${q()}`); } catch (e) { return put("h-trend", errBox(e)); }
    const half = Math.floor(d.years.length / 2);
    const avg = (a) => a.reduce((s, v) => s + v, 0) / Math.max(1, a.length);
    const early = avg(d.days.slice(0, half)), late = avg(d.days.slice(half));
    if (!put("h-trend", `<p class="sub">Extreme-heat days and warm nights per year. Recent half averages <b>${fmt(late, 1)}</b> extreme days a year vs <b>${fmt(early, 1)}</b> before.</p><div class="chart short"><canvas id="c-trend"></canvas></div>`)) return;
    chart("trend", $("#c-trend"), { type: "bar",
      data: { labels: d.years, datasets: [{ label: "Extreme days", data: d.days, backgroundColor: C.heat }, { label: "Warm nights", data: d.nights, backgroundColor: C.ink3 }] },
      options: { maintainAspectRatio: false, plugins: { legend: { display: true, labels: { boxWidth: 10 } } }, scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } }, y: { grid } } } });
  }

  // =====================================================================
  // FLOOD: will this street waterlog, and which drain is choking?
  // =====================================================================
  register("flood", {
    title: "Will <em>this street</em> waterlog?", kicker: "Flood desk", needsPlace: true, layers: ["underpasses", "drains"], zoom: 14,
    intro: "River floods are on Pulse. This desk is about the street: low ground, missing drains, a cloudburst and a choked culvert.",
    render(el) {
      el.innerHTML = head("Flood desk", "Will <em>this street</em> waterlog?",
        "An explainable score from the ground shape (Copernicus DEM), nearby drains and underpasses (OpenStreetMap) and rain before and ahead. Each factor shows how much it pushed the odds.") + `
        <section class="sec"><h2>Waterlogging odds, next 24 h</h2><div id="f-wl">${loading("Reading terrain, drains and rain…")}</div></section>
        <section class="sec"><h2>Who does what</h2><div id="f-dir"></div></section>
        <section class="sec"><h2>Drain network watch <span class="aside">demo network</span></h2>
          <p class="sub">Six drain nodes with level sensors. A drain that fills faster than the rain explains is choking.</p><div id="f-drains">${loading("Checking sensors…")}</div></section>
        <section class="sec"><h2>Storm simulator</h2><p class="sub">Push a design storm through the network. Block a drain to see where water backs up.</p><div id="f-sim"></div></section>
        <section class="sec"><h2>How far ahead can we tell?</h2><div id="f-hz"></div></section>`;
      floodWaterlog(); floodDrains();
    },
  });

  async function floodWaterlog() {
    let d;
    try { d = await api(`/api/flood/waterlog?${q()}`); } catch (e) { return put("f-wl", errBox(e)); }
    const t = d.terrain, r = d.rain;
    put("f-wl", `
      ${d.flash.on ? `<div class="box flash ${d.flash.level}"><h3>⚡ Flash alert</h3>${esc(d.flash.text)}</div>` : ""}
      <div class="stats"><div><div class="v ${d.level}">${Math.round(d.probability * 100)}<small>%</small></div><div class="l">${stamp(d.level)} chance of standing water</div></div>
        <div><div class="v">${Math.round(d.baseline * 100)}<small>%</small></div><div class="l">on a dry day here</div></div>
        <div><div class="v">${fmt(r.next24_mm)}<small>mm</small></div><div class="l">rain next 24 h (max ${fmt(r.max_hour_mm, 1)} mm/h)</div></div></div>
      <h3 style="font:600 14px var(--serif);margin:4px 0 6px">Why (push on the odds)</h3>
      ${bars(d.why.map((w) => ({ label: w.label, v: w.logodds, text: (w.logodds > 0 ? "+" : "") + fmt(w.logodds, 2) })), "water")}
      <table class="t" style="margin-top:12px"><tbody>
        <tr><td>Ground height</td><td class="n">${fmt(t.elevation_m, 1)} m</td><td>Lower than surroundings by</td><td class="n">${fmt(-t.tpi_m, 1)} m</td></tr>
        <tr><td>Slope</td><td class="n">${fmt(t.slope_pct, 1)}%</td><td>Neighbours higher</td><td class="n">${Math.round(t.share_higher * 100)}%</td></tr>
        <tr><td>Rain last 72 h</td><td class="n">${fmt(r.past72_mm)} mm</td><td>Nearest drain</td><td class="n">${d.drain_m != null ? `${d.drain_m} m` : "none mapped"}</td></tr></tbody></table>
      ${d.impact.length ? `<h3 style="font:600 14px var(--serif);margin:12px 0 6px">Places that would be hit</h3><ul class="ledger">${d.impact.map((p) => `<li data-ll="${p.lat},${p.lon}"><span class="sw ${p.impact >= 40 ? "red" : p.impact >= 20 ? "orange" : "green"}"></span><span><span class="t">${esc(p.name)}</span><br><span class="s">${esc(p.kind.replace("_", " "))} · ${p.distance_m} m away</span></span><span class="v">${p.impact}<small>impact</small></span></li>`).join("")}</ul>` : ""}
      ${Object.keys(d.errors || {}).length ? `<p class="fine">Partly offline: ${Object.keys(d.errors).map(esc).join(", ")} could not be reached, so defaults were used for it.</p>` : ""}
      <p class="fine">${esc(d.source)}</p>`);
    $$("#f-wl li[data-ll]").forEach((li) => li.addEventListener("click", () => map.flyTo(li.dataset.ll.split(",").map(Number), 16)));
    const U = layer("underpasses"); U.clearLayers();
    (d.underpasses || []).forEach((u) => L.marker([u.lat, u.lon], { icon: div("mk-tank", "▼ UP", 26) }).bindTooltip(`${esc(u.name)} · ${u.distance_m} m`, { className: "tt" }).addTo(U));
    put("f-dir", tabs("f-dtabs", Object.keys(d.directives), Object.keys(d.directives)[0]) + `<div class="box" id="f-dtext">${esc(Object.values(d.directives)[0])}</div>`);
    onTabs("f-dtabs", (k) => put("f-dtext", esc(d.directives[k])));
    put("f-hz", `<table class="t"><thead><tr><th>Range</th><th>What we can say</th><th>Confidence</th></tr></thead><tbody>${d.horizons.map((h) => `<tr><td class="num">${esc(h.range)}</td><td>${esc(h.what)}</td><td><span class="pill ${h.confidence === "good" ? "green" : h.confidence === "fair" ? "orange" : "red"}">${esc(h.confidence)}</span></td></tr>`).join("")}</tbody></table>`);
  }

  let drainData = null;
  async function floodDrains() {
    try { drainData = await api(`/api/flood/drains?${q()}`); } catch (e) { return put("f-drains", errBox(e)); }
    if (S.desk !== "flood") return;
    const d = drainData;
    const D = layer("drains"); D.clearLayers();
    const byId = Object.fromEntries(d.nodes.map((n) => [n.id, n]));
    d.nodes.forEach((n) => {
      if (n.downstream && byId[n.downstream]) L.polyline([[n.lat, n.lon], [byId[n.downstream].lat, byId[n.downstream].lon]], { color: C.water, weight: 4, opacity: 0.7, dashArray: "6 5" }).addTo(D);
    });
    d.nodes.forEach((n) => L.marker([n.lat, n.lon], { icon: div(`mk-node ${n.state}`, "", 16) })
      .bindTooltip(`<b>${n.id} · ${esc(n.name)}</b><br>${n.blockage_pct}% blocked · ${esc(n.state)}`, { className: "tt" })
      .on("click", (e) => { L.DomEvent.stopPropagation(e); drainDetail(n.id); }).addTo(D));
    fitTo(d.nodes.map((n) => [n.lat, n.lon]).concat([[S.place.lat, S.place.lon]]), 60);
    legend(`<div class="ttl">Drain nodes</div>${sw(C.ok, "clear")}${sw(C.warn, "choking")}${sw(C.bad, "blocked")}${sw(C.ink, "▼ UP = underpass")}`);
    put("f-drains", `<ul class="ledger">${d.nodes.map((n) => `<li data-id="${n.id}"><span class="sw ${n.state === "blocked" ? "red" : n.state === "choking" ? "orange" : "green"}"></span>
        <span><span class="t">${n.id} · ${esc(n.name)}</span><br><span class="s">${n.trend_pct_per_day > 0 ? "+" : ""}${n.trend_pct_per_day}%/day${n.days_to_80 != null ? ` · 80% blocked in ~${fmt(n.days_to_80, 1)} days` : ""}${n.ml_confirmed ? " · confirmed by anomaly model" : ""}</span></span>
        <span class="v">${n.blockage_pct}%<small>blocked</small></span></li>`).join("")}</ul>
      <div id="f-node"></div>
      <p class="fine">Blockage = how much of the drain's opening the water level implies is lost (orifice equation), checked by an Isolation Forest against the first 4 days. Field sensors post to <code>/api/drains/reading</code>.</p>`);
    $$("#f-drains li[data-id]").forEach((li) => li.addEventListener("click", () => drainDetail(li.dataset.id)));
    drainDetail(d.nodes.slice().sort((a, b) => b.blockage_pct - a.blockage_pct)[0].id);
    floodSim();
  }
  function drainDetail(id) {
    const n = drainData.nodes.find((x) => x.id === id);
    if (!put("f-node", `<div class="box"><h3>${n.id} · ${esc(n.name)} <span class="pill ${n.state}">${esc(n.state)}</span></h3>
      <p class="fine" style="margin:0">${n.catchment_ha} ha catchment · opening ${n.design_area_m2} m² · flows to ${n.downstream || "river"}</p><div class="chart short"><canvas id="c-node"></canvas></div></div>`)) return;
    const rain = drainData.rain_mm_h.filter((_, i) => i % 3 === 0);
    chart("node", $("#c-node"), { data: { labels: n.series.map((_, i) => i), datasets: [
      { type: "line", label: "Blockage %", data: n.series, borderColor: C.bad, pointRadius: 0, borderWidth: 2, yAxisID: "y" },
      { type: "bar", label: "Rain mm/h", data: rain, backgroundColor: "rgba(31,97,112,.35)", yAxisID: "r" }] },
      options: { maintainAspectRatio: false, plugins: { legend: { display: true, labels: { boxWidth: 10 } } },
        scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 7, callback: (v) => `d${Math.floor((v * 3) / 24)}` } }, y: { grid, min: 0, max: 100 }, r: { position: "right", grid: { display: false }, reverse: true } } } });
  }

  function floodSim() {
    const nodes = drainData.nodes;
    put("f-sim", `${slider("fs-mm", "Storm total", 20, 200, 5, 80, " mm")}${slider("fs-h", "Falls over", 0.5, 6, 0.5, 2, " h")}
      <div class="grid3">${nodes.map((n) => `<label class="field">${n.id} blocked %<input type="number" id="fs-${n.id}" min="0" max="95" step="5" value="${Math.round(n.blockage_pct / 5) * 5}"></label>`).join("")}</div>
      <div class="row"><button class="btn small ghost" id="fs-clear">Clear every drain</button><button class="btn small ghost" id="fs-now">Use sensor values</button></div>
      <div id="fs-out"></div>`);
    const run = debounce(async () => {
      const blocks = {}; nodes.forEach((n) => { const v = +$(`#fs-${n.id}`).value; if (v > 0) blocks[n.id] = v; });
      try {
        const r = await api(`/api/flood/cascade?storm_mm=${$("#fs-mm").value}&hours=${$("#fs-h").value}&blocks=${encodeURIComponent(JSON.stringify(blocks))}`);
        const total = Object.values(r.spill).reduce((s, x) => s + x.volume_m3, 0);
        if (!put("fs-out", `<div class="stats"><div><div class="v ${r.flooded.length ? "red" : "green"}">${r.flooded.length}</div><div class="l">nodes overflow</div></div>
          <div><div class="v">${fmt(total)}<small>m³</small></div><div class="l">water on the streets (≈${fmt(total / 10)} tanker loads)</div></div>
          <div><div class="v">${fmt(r.outfall_peak_h, 1)}<small>h</small></div><div class="l">outfall peaks at</div></div></div>
          <div class="chart short"><canvas id="c-sim"></canvas></div>
          <table class="t"><thead><tr><th>Node</th><th>Peak in</th><th>Capacity</th><th>Spill</th></tr></thead><tbody>${Object.entries(r.spill).map(([k, s]) => `<tr><td>${k} ${esc(s.name)}</td><td class="n">${s.peak_in_m3s}</td><td class="n">${s.capacity_m3s}</td><td class="n ${s.volume_m3 ? "t-red" : ""}">${s.volume_m3 ? `${fmt(s.volume_m3)} m³ from ${s.starts_h} h` : "—"}</td></tr>`).join("")}</tbody></table>
          <p class="fine">Muskingum routing between nodes; a blocked node loses capacity in proportion to the blockage. Flow in m³/s.</p>`)) return;
        chart("sim", $("#c-sim"), { data: { labels: r.t_h, datasets: [
          { type: "line", label: "Outfall m³/s", data: r.outfall, borderColor: C.water, pointRadius: 0, borderWidth: 2, yAxisID: "y" },
          { type: "bar", label: "Rain mm/h", data: r.rain, backgroundColor: "rgba(32,29,24,.18)", yAxisID: "r" }] },
          options: { maintainAspectRatio: false, plugins: { legend: { display: true, labels: { boxWidth: 10 } } },
            scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8, callback: (v) => `${r.t_h[v]}h` } }, y: { grid }, r: { position: "right", reverse: true, grid: { display: false } } } } });
      } catch (e) { put("fs-out", errBox(e)); }
    }, 300);
    bindSlider("fs-mm", " mm", run); bindSlider("fs-h", " h", run);
    nodes.forEach((n) => $(`#fs-${n.id}`).addEventListener("input", run));
    $("#fs-clear").addEventListener("click", () => { nodes.forEach((n) => ($(`#fs-${n.id}`).value = 0)); run(); });
    $("#fs-now").addEventListener("click", () => { nodes.forEach((n) => ($(`#fs-${n.id}`).value = Math.round(n.blockage_pct / 5) * 5)); run(); });
    run();
  }

  // =====================================================================
  // DROUGHT: how dry is it, what to sow, where to store water
  // =====================================================================
  register("drought", {
    title: "Is a <em>drought</em> setting in?", kicker: "Drought desk", needsPlace: true, layers: [], zoom: 9,
    intro: "Rain compared with this place's own 30+ years, soil moisture and evaporation, then what that means for crops, recharge and the village water budget.",
    render(el) {
      el.innerHTML = head("Drought desk", "Is a <em>drought</em> setting in?",
        "Standardised indices (SPI and SPEI) say how unusual the last 1, 3 and 6 months were for this exact place, on the same D0–D4 scale drought monitors use.") + `
        <section class="sec"><h2>Drought status</h2><div id="d-st">${loading("Comparing with 30+ years of rain…")}</div></section>
        <section class="sec"><h2>Water health score</h2><div id="d-health"></div></section>
        <section class="sec"><h2>What can we sow?</h2><div id="d-crops"></div></section>
        <section class="sec"><h2>Where should rain be stored?</h2><div id="d-rech"></div></section>
        <section class="sec"><h2>Village water budget</h2><p class="sub">Yearly demand vs supply (55 litres a person a day, Jal Jeevan norm). Move the sliders to test a bad year.</p><div id="d-budget"></div></section>
        <section class="sec"><h2>Fields from space <span class="aside">Sentinel-2 · AWS Open Data</span></h2><div id="d-sat">${loading("Searching Sentinel-2 scenes on AWS…")}</div></section>`;
      droughtStatus(); droughtSat();
    },
  });

  async function droughtStatus() {
    let d;
    try { d = await api(`/api/drought?${q()}`); } catch (e) { return put("d-st", errBox(e)); }
    const tile = (k, v, l) => `<div><div class="v ${v <= -1.3 ? "red" : v <= -0.5 ? "orange" : "green"}">${v > 0 ? "+" : ""}${fmt(v, 2)}</div><div class="l">${l}</div></div>`;
    if (!put("d-st", `
      <div class="row" style="margin:0 0 10px">${stamp(d.level, `${d.class !== "--" ? d.class + " · " : ""}${d.label}`)}<span class="fine" style="margin:0">as of ${niceDate(d.as_of)} · season: ${esc(d.season)}</span></div>
      <div class="stats">${tile("spi1", d.spi1, "SPI 1 month")}${tile("spi3", d.spi3, "SPI 3 months")}${tile("spi6", d.spi6, "SPI 6 months")}${tile("spei3", d.spei3, "SPEI 3 (rain − evaporation)")}</div>
      <div class="stats"><div><div class="v">${fmt(d.season_rain)}<small>/${fmt(d.season_normal)} mm</small></div><div class="l">season rain vs normal · IMD: <b>${esc(d.imd_category)}</b></div></div>
        <div><div class="v">${d.soil_moisture != null ? Math.round(d.soil_moisture * 100) : "—"}<small>%</small></div><div class="l">root-zone soil moisture</div></div>
        <div><div class="v">${d.hot_days_90d}</div><div class="l">hot days (90 d)</div></div></div>
      <div class="chart"><canvas id="c-month"></canvas></div><p class="fine">${esc(d.source)}</p>`)) return;
    chart("month", $("#c-month"), { data: { labels: d.monthly.t, datasets: [
      { type: "bar", label: "Rain mm", data: d.monthly.mm, backgroundColor: d.monthly.mm.map((v, i) => (v < d.monthly.normal[i] * 0.6 ? C.dry : C.water)) },
      { type: "line", label: "Normal", data: d.monthly.normal, borderColor: C.ink, borderDash: [4, 3], pointRadius: 0, borderWidth: 1.5 }] },
      options: { maintainAspectRatio: false, plugins: { legend: { display: true, labels: { boxWidth: 10 } } }, scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } }, y: { grid } } } });
    const h = d.health;
    put("d-health", `<div class="stats"><div><div class="v ${h.level}">${h.score}</div><div class="l">${esc(h.word)}</div></div></div>
      ${bars(h.parts.map((p) => ({ label: `${p.label} (${Math.round(p.weight * 100)}%)`, v: p.score, text: p.score })), "water", 100)}
      <p class="fine">Open the Groundwater desk once and its well stress is added here too.</p>`);
    droughtCrops(); droughtRecharge(); droughtBudget();
  }

  function droughtCrops() {
    put("d-crops", `${slider("dc-irr", "Irrigation you can give", 0, 600, 25, 0, " mm")}<div id="dc-out"></div>`);
    const run = debounce(async () => {
      try {
        const r = await api(`/api/drought/crops?${q()}&irrigation_mm=${$("#dc-irr").value}`);
        const v = { "good fit": "green", "ok with care": "orange", "too thirsty": "red" };
        put("dc-out", `<p class="sub">${r.season === "kharif" ? "Kharif" : "Rabi"} season · expect about <b>${r.expected_rain_mm} mm</b> of rain.</p>
          <table class="t"><thead><tr><th>Crop</th><th>Needs</th><th>Covered</th><th></th></tr></thead><tbody>${r.crops.map((c) => `<tr><td>${esc(c.crop)}</td><td class="n">${c.need_mm} mm</td><td class="n">${c.covered_pct}%</td><td><span class="pill ${v[c.verdict] || "orange"}">${esc(c.verdict)}</span></td></tr>`).join("")}</tbody></table>`);
      } catch (e) { put("dc-out", errBox(e)); }
    });
    bindSlider("dc-irr", " mm", run); run();
  }

  function droughtRecharge() {
    const soils = (S.meta || {}).soils || { loam: "Loamy" };
    put("d-rech", `<div class="grid2"><label class="field">Soil<select id="dr-soil">${Object.entries(soils).map(([k, v]) => `<option value="${k}" ${k === "loam" ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
      <label class="field">Land area (ha)<input id="dr-area" type="number" min="1" max="5000" value="10"></label></div><div id="dr-out"></div>`);
    const run = debounce(async () => {
      try {
        const r = await api(`/api/drought/recharge?${q()}&soil=${$("#dr-soil").value}&area_ha=${$("#dr-area").value || 10}`);
        put("dr-out", `<p class="sub">Slope ${fmt(r.slope_pct, 1)}% · normal rain ${fmt(r.annual_rain)} mm a year.</p>
          <ul class="ledger">${r.options.map((o) => `<li><span class="sw ${o.cost === "low" ? "green" : o.cost === "medium" ? "orange" : "red"}"></span><span><span class="t">${esc(o.structure)}</span><br><span class="s">${esc(o.why)} · cost ${esc(o.cost)}</span></span><span class="v">${fmt(o.recharge_m3)}<small>m³ / year</small></span></li>`).join("")}</ul>
          <p class="fine">Rules of thumb in the style of the CGWB Master Plan for Artificial Recharge.</p>`);
      } catch (e) { put("dr-out", errBox(e)); }
    });
    $("#dr-soil").addEventListener("change", run); $("#dr-area").addEventListener("input", run); run();
  }

  function droughtBudget() {
    put("d-budget", `<div class="grid2">
      <label class="field">People<input id="db-pop" type="number" value="2500" min="0"></label>
      <label class="field">Livestock<input id="db-liv" type="number" value="800" min="0"></label>
      <label class="field">Irrigated ha<input id="db-irr" type="number" value="120" min="0"></label>
      <label class="field">Catchment ha<input id="db-cat" type="number" value="600" min="0"></label></div>
      ${slider("db-rain", "Rain change", -60, 40, 5, 0, "%")}${slider("db-dem", "Demand change", -30, 50, 5, 0, "%")}<div id="db-out"></div>`);
    const run = debounce(async () => {
      try {
        const r = await api(`/api/drought/budget?${q()}&population=${$("#db-pop").value}&livestock=${$("#db-liv").value}&irrigated_ha=${$("#db-irr").value}&catchment_ha=${$("#db-cat").value}&rain_change_pct=${$("#db-rain").value}&demand_change_pct=${$("#db-dem").value}`);
        const short = r.gap_m3 < 0;
        put("db-out", `<div class="stats"><div><div class="v ${short ? "red" : "green"}">${short ? "−" : "+"}${fmt(Math.abs(r.gap_m3))}<small>m³</small></div><div class="l">${short ? "short this year" : "spare this year"}</div></div>
          <div><div class="v">${fmt(r.days_covered)}</div><div class="l">days of demand covered</div></div>
          <div><div class="v ${r.tankers_per_day ? "orange" : ""}">${fmt(r.tankers_per_day, 1)}</div><div class="l">tankers a day to close the gap</div></div></div>
          ${bars(Object.entries(r.parts).map(([k, v]) => ({ label: k, v, text: fmt(v / 1000) + "k" })), "dry")}
          <p class="fine">Demand ${fmt(r.demand_m3)} m³ vs supply ${fmt(r.supply_m3)} m³. First three rows are demand, the rest are supply.</p>`);
      } catch (e) { put("db-out", errBox(e)); }
    });
    ["db-pop", "db-liv", "db-irr", "db-cat"].forEach((id) => $(`#${id}`).addEventListener("input", run));
    bindSlider("db-rain", "%", run); bindSlider("db-dem", "%", run); run();
  }

  async function droughtSat() {
    let s;
    try { s = await api(`/api/drought/satellite?${q()}`); } catch (e) { return put("d-sat", `<p class="empty">Sentinel-2 search is unreachable right now (${esc(e.message)}).</p>`); }
    const card = (x, t) => x ? `<figure style="margin:0"><img src="${esc(x.thumbnail)}" alt="Sentinel-2 scene ${esc(t)}" style="width:100%;aspect-ratio:1;object-fit:cover;border:1.5px solid var(--ink)" loading="lazy">
      <figcaption class="fine">${esc(t)} · ${niceDate(x.date)} · cloud ${x.cloud_pct}%${x.ndvi != null ? ` · NDVI <b>${x.ndvi}</b>` : ""}</figcaption></figure>` : `<p class="empty">No clear scene ${esc(t)}.</p>`;
    put("d-sat", `<div class="grid2">${card(s.last_year, "a year ago")}${card(s.now, "this month")}</div>
      ${s.verdict ? `<div class="flag">Fields are <b>${esc(s.verdict)}</b> (NDVI change ${s.change > 0 ? "+" : ""}${s.change}).</div>` : ""}${s.note ? `<p class="fine">${esc(s.note)}</p>` : ""}<p class="fine">${esc(s.source)}</p>`);
  }

  // =====================================================================
  // GROUNDWATER: where is the water table going?
  // =====================================================================
  const GW = { depth: 12, well: 60, aquifer: "alluvium", usage: "moderate" };
  register("gw", {
    title: "Where is the <em>water table</em> going?", kicker: "Groundwater desk", needsPlace: true, layers: [], zoom: 9,
    intro: "A water-balance model of your well: how much rain soaks in for your rock type, how much is pumped out, and what the next 90 days look like.",
    render(el) {
      const m = S.meta || {};
      el.innerHTML = head("Groundwater desk", "Where is the <em>water table</em> going?",
        "Rain is routed into the ground with CGWB infiltration factors for your aquifer, pumping is taken out month by month, and the next 90 days are replayed with every past year's rain.") + `
        <section class="sec"><h2>Your well</h2><div class="grid2">
          <label class="field">Water level now (m below ground)<input id="g-depth" type="number" step="0.5" min="0" value="${GW.depth}"></label>
          <label class="field">Well / borewell depth (m)<input id="g-well" type="number" step="1" min="5" value="${GW.well}"></label>
          <label class="field">Rock under you<select id="g-aq">${Object.entries(m.aquifers || { alluvium: "Alluvium" }).map(([k, v]) => `<option value="${k}" ${k === GW.aquifer ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
          <label class="field">How water is used<select id="g-use">${Object.entries(m.usage || { moderate: "Moderate" }).map(([k, v]) => `<option value="${k}" ${k === GW.usage ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label></div>
          <div class="row"><button class="btn" id="g-run">Run outlook</button></div></section>
        <div id="g-out"></div>`;
      $("#g-run").addEventListener("click", gwRun);
      gwRun();
    },
  });

  async function gwRun() {
    Object.assign(GW, { depth: +$("#g-depth").value, well: +$("#g-well").value, aquifer: $("#g-aq").value, usage: $("#g-use").value });
    put("g-out", `<section class="sec"><h2>Outlook</h2>${loading("Replaying 25 years of rain into your aquifer…")}</section>`);
    let g;
    try { g = await api(`/api/groundwater?${q()}&depth=${GW.depth}&well=${GW.well}&aquifer=${GW.aquifer}&usage=${GW.usage}`); } catch (e) { return put("g-out", errBox(e)); }
    const bd = (x) => (x == null ? "not in sight" : x === 0 ? "already" : x > 365 ? `~${fmt(x / 365, 1)} yrs` : `${x} days`);
    if (!put("g-out", `
      <section class="sec"><h2>Status</h2>
        <div class="row" style="margin:0 0 10px">${stamp(g.level, g.category)}<span class="fine" style="margin:0">${esc(g.depth_status)}</span></div>
        <div class="stats"><div><div class="v ${g.level}">${g.stage_pct}<small>%</small></div><div class="l">pumped vs recharged (CGWB stage)</div></div>
          <div><div class="v">${g.recharge_mm_year}<small>mm</small></div><div class="l">soaks in a year</div></div>
          <div><div class="v">${g.draft_mm_year}<small>mm</small></div><div class="l">pumped out a year</div></div>
          <div><div class="v ${g.trend_m_per_year > 0.3 ? "red" : g.trend_m_per_year > 0 ? "orange" : "green"}">${g.trend_m_per_year > 0 ? "▼" : "▲"}${fmt(Math.abs(g.trend_m_per_year), 2)}<small>m/yr</small></div><div class="l">${g.trend_m_per_year > 0 ? "going deeper" : "rising"}</div></div></div>
        ${meter(g.stress, 100, ["safe", "stressed", "critical"])}
        ${g.cut_needed_pct ? `<div class="flag">To stay within safe yield (70% of recharge = ${g.safe_draft_mm_year} mm), pumping must fall by about <b>${g.cut_needed_pct}%</b>.</div>` : ""}</section>
      <section class="sec"><h2>Next 90 days</h2>
        <div class="chips" id="g-sc">${Object.entries(g.scenarios).map(([k, s]) => `<button data-k="${k}" aria-pressed="${k === "normal" || k === "drought"}">${esc(s.label.split(":")[0])}</button>`).join("")}</div>
        <div class="chart tall"><canvas id="c-gw"></canvas></div>
        <table class="t"><thead><tr><th>Line</th><th>Depth</th><th>Normal year</th><th>Drought</th></tr></thead><tbody>${Object.entries(g.thresholds).map(([k, v]) => `<tr><td>${k}</td><td class="n">${v} m</td><td class="n">${bd(g.breach_days[k].normal)}</td><td class="n">${bd(g.breach_days[k].drought)}</td></tr>`).join("")}</tbody></table>
        <p class="fine">Shaded band: 10–90% range if the next 90 days get the rain of any of the last 25 years. Confidence: ${esc(g.confidence)}.</p></section>
      <section class="sec"><h2>What to do</h2>${tabs("g-tabs", Object.keys(g.advice), Object.keys(g.advice)[0])}<div id="g-adv"></div></section>
      <section class="sec"><h2>Log a well reading</h2><p class="sub">Real readings anchor the model. If the category changes, an alert goes out via Amazon SNS.</p>
        <div class="row"><label class="field" style="flex:1">Depth to water (m)<input id="g-read" type="number" step="0.1" min="0" value="${g.depth_now}"></label><label class="field" style="flex:1">Well name<input id="g-wname" placeholder="optional"></label>
          <button class="btn" id="g-log" style="align-self:end">Save reading</button></div><div id="g-logout"></div>
        ${g.observations.length ? `<p class="fine">${g.observations.length} reading(s) logged here.</p>` : ""}<p class="fine">${esc(g.source)}</p></section>`)) return;
    const adv = (k) => put("g-adv", `<ul class="plain">${g.advice[k].map((a) => `<li>${esc(a)}</li>`).join("")}</ul>`);
    adv(Object.keys(g.advice)[0]); onTabs("g-tabs", adv);
    const colors = { normal: C.water, drought: C.bad, monsoon: C.ok, heatwave: C.heat };
    const draw = () => {
      const on = $$("#g-sc button").filter((b) => b.getAttribute("aria-pressed") === "true").map((b) => b.dataset.k);
      chart("gw", $("#c-gw"), { type: "line", data: { labels: g.dates, datasets: [
        { label: "p90", data: g.band.p90, borderWidth: 0, pointRadius: 0, backgroundColor: "rgba(31,97,112,.13)", fill: "+1" },
        { label: "p10", data: g.band.p10, borderWidth: 0, pointRadius: 0, fill: false },
        ...on.map((k) => ({ label: g.scenarios[k].label, data: g.scenarios[k].depth, borderColor: colors[k] || C.ink, borderWidth: 2, pointRadius: 0 }))] },
        options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
          plugins: { legend: { display: true, labels: { boxWidth: 10, filter: (i) => !i.text.startsWith("p") } },
            annotation: { annotations: Object.fromEntries(Object.entries(g.thresholds).map(([k, v]) => [k, { type: "line", yMin: v, yMax: v, borderColor: k === "warning" ? C.warn : C.bad, borderDash: [5, 4], borderWidth: 1, label: { display: true, content: k, position: "start", backgroundColor: "transparent", color: C.ink2, font: { size: 10 } } }])) } },
          scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 6, callback: (v) => niceDate(g.dates[v]) } },
            y: { reverse: true, grid, title: { display: true, text: "m below ground" }, suggestedMin: Math.max(0, g.depth_now - 3), suggestedMax: Math.min(g.well_depth, Math.max(...g.band.p90) + 2) } } } });
    };
    $$("#g-sc button").forEach((b) => b.addEventListener("click", () => { b.setAttribute("aria-pressed", String(b.getAttribute("aria-pressed") !== "true")); draw(); }));
    draw();
    $("#g-log").addEventListener("click", async () => {
      try {
        const r = await post("/api/groundwater/reading", { lat: S.place.lat, lon: S.place.lon, depth_m: +$("#g-read").value, well: $("#g-wname").value });
        put("g-logout", r.transition ? `<div class="box flash ${r.transition.level}"><h3>Category changed</h3>${esc(r.transition.from)} → <b>${esc(r.transition.to)}</b>. Alert sent via ${esc(r.transition.sent.via)}.</div>`
          : '<p class="fine">Saved. No change in category.</p>');
        toast("Reading saved");
      } catch (e) { put("g-logout", errBox(e)); }
    });
  }

  // =====================================================================
  // TANKERS: who gets water first, and did it actually arrive?
  // =====================================================================
  register("tankers", {
    title: "Who gets the <em>tanker</em> first?", kicker: "Tanker desk", needsPlace: true, layers: ["tankers"], zoom: 12,
    intro: "Requests are ranked by need you can see (water stress, hospitals and schools, days without water, today's heat), trips are planned, and every delivery is closed with a one-time code.",
    render(el) {
      el.innerHTML = head("Tanker desk", "Who gets the <em>tanker</em> first?",
        "Every priority comes with its reasons. Trips are packed fairly, routed (Amazon Location when set up), and only count as delivered when the resident reads out the code they got by SMS.") + `
        <div id="t-body">${loading("Ranking requests and planning trips…")}</div>`;
      tankersLoad();
    },
  });

  let plan = null;
  async function tankersLoad() {
    try { plan = await api(`/api/tankers?${q()}`); } catch (e) { return put("t-body", errBox(e)); }
    if (S.desk !== "tankers") return;
    const p = plan, m = S.meta || {};
    const T = layer("tankers"); T.clearLayers();
    const pts = [];
    const tripCol = [C.water, C.heat, C.dry, C.ok, "#6B4E9B", C.ink];
    p.trips.forEach((t, i) => {
      L.polyline(t.line, { color: tripCol[i % tripCol.length], weight: 4, opacity: 0.85 }).bindTooltip(`${esc(t.tanker)} · ${t.km} km · ${t.minutes} min`, { className: "tt" }).addTo(T);
      L.marker([t.from.lat, t.from.lon], { icon: div("mk-tank", esc(t.tanker), 44) }).addTo(T);
      pts.push([t.from.lat, t.from.lon]);
    });
    p.queue.forEach((r) => {
      L.marker([r.lat, r.lon], { icon: div(`mk-req ${r.priority.level}`, "", 14) })
        .bindTooltip(`<b>${esc(r.area)}</b><br>priority ${r.priority.score} · ${fmt(r.litres)} L needed`, { className: "tt" }).addTo(T);
      pts.push([r.lat, r.lon]);
    });
    fitTo(pts, 50);
    legend(`<div class="ttl">Requests by priority</div>${sw(C.bad, "critical", 1)}${sw(C.warn, "high", 1)}${sw("#E5C76B", "medium", 1)}${sw(C.ink, "tanker depot")}`);
    const eq = p.equity;
    put("t-body", `
      <div class="stats"><div><div class="v">${p.queue.length}</div><div class="l">requests waiting</div></div>
        <div><div class="v">${p.trips.length}</div><div class="l">trips planned</div></div>
        <div><div class="v ${p.unserved.length ? "red" : "green"}">${p.unserved.length}</div><div class="l">can't be served today</div></div>
        <div><div class="v">${eq.equity_score ?? "—"}</div><div class="l">fairness score (100 = even)</div></div></div>
      ${p.heat_feels ? `<div class="flag">Feels like ${fmt(p.heat_feels)}°C this week${p.heat_load ? ` (heat load ${p.heat_load})` : ""}, so drinking-water needs are raised.</div>` : ""}
      <section class="sec"><h2>Priority queue</h2><ul class="ledger" id="t-queue">${p.queue.map((r) => `<li data-ll="${r.lat},${r.lon}"><span class="sw ${r.priority.level === "CRITICAL" ? "red" : r.priority.level === "HIGH" ? "orange" : "green"}"></span>
        <span><span class="t">${esc(r.area)}</span> <span class="pill ${r.priority.level}">${esc(r.priority.level)}</span><br><span class="s">${esc(r.institution)} · ${fmt(r.population)} people · ${r.days_without} day(s) without water</span>
        <ul class="reasons">${r.priority.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></span><span class="v">${r.priority.score}<small>${fmt(r.litres / 1000, 1)}k L</small></span></li>`).join("")}</ul></section>
      <section class="sec"><h2>Today's trips</h2>${p.trips.map((t, i) => `<div class="box"><h3><span style="display:inline-block;width:10px;height:10px;background:${tripCol[i % tripCol.length]}"></span> ${esc(t.tanker)} · ${esc(t.driver)} <span class="aside fine">${fmt(t.capacity_l)} L from ${esc(t.depot)}</span></h3>
          <ol class="plain">${t.stops.map((s) => `<li>${esc(s.area)}: <b>${fmt(s.give_l)} L</b>, ~${s.eta_min} min${s.short_l ? ` <span class="fine">(still ${fmt(s.short_l)} L short)</span>` : ""}</li>`).join("")}</ol>
          <div class="row"><span class="fine" style="margin:0">${t.km} km · ${t.minutes} min · ${esc(t.routed_by)}</span><button class="btn small" data-trip="${i}" style="margin-left:auto">Dispatch + send codes</button></div></div>`).join("") || '<p class="empty">No trips needed.</p>'}</section>
      <section class="sec"><h2>Confirm a delivery</h2><p class="sub">The resident got a 6-digit code by SMS (Amazon SNS). Delivery counts only when the driver enters it.</p>
        <div class="row"><label class="field" style="flex:2">Delivery<select id="t-rid">${p.recent.filter((r) => r.status === "dispatched").map((r) => `<option value="${r.id}">${esc(r.area)} · ${esc(r.tanker)}</option>`).join("") || "<option value=''>Dispatch a trip first</option>"}</select></label>
          <label class="field" style="flex:1">Code<input id="t-otp" inputmode="numeric" maxlength="6" placeholder="000000"></label><button class="btn" id="t-conf" style="align-self:end">Confirm</button></div>
        <div class="row"><button class="btn small ghost" id="t-peek">Demo: show the code SNS would text</button></div><div id="t-confout"></div>
        ${p.recent.length ? `<table class="t"><thead><tr><th>Area</th><th>Tanker</th><th>Status</th></tr></thead><tbody>${p.recent.slice().reverse().map((r) => `<tr><td>${esc(r.area)}</td><td>${esc(r.tanker || "")}</td><td><span class="pill ${r.status === "delivered" ? "green" : "orange"}">${esc(r.status)}</span></td></tr>`).join("")}</tbody></table>` : ""}</section>
      <section class="sec"><h2>Is it fair?</h2><p class="sub">Share of each area's need actually delivered. A Gini of 0 means every area got the same share.</p>
        ${eq.areas.length ? bars(eq.areas.map((a) => ({ label: a.area, v: a.served_pct, text: `${a.served_pct}%` })), "water", 100) : '<p class="empty">Nothing delivered yet.</p>'}
        ${eq.gini != null ? `<p class="fine">Gini ${eq.gini}.</p>` : ""}</section>
      <section class="sec"><h2>Ask for a tanker</h2><div class="grid2">
          <label class="field">Area / ward<input id="t-area" placeholder="e.g. Ward 14, Shanti Nagar"></label>
          <label class="field">Who is asking<select id="t-role">${(m.roles || ["Ward officer"]).map((r) => `<option>${esc(r)}</option>`).join("")}</select></label>
          <label class="field">Type of place<select id="t-inst">${(m.institutions || ["other"]).map((r) => `<option>${esc(r)}</option>`).join("")}</select></label>
          <label class="field">People served<input id="t-pop" type="number" value="400" min="1"></label>
          <label class="field">Days without water<input id="t-days" type="number" value="2" min="0" step="0.5"></label>
          <label class="field">Phone for the code (optional)<input id="t-phone" placeholder="+91…"></label></div>
        <p class="fine">The request is placed at the pin. Tap the map first to move it.</p><div class="row"><button class="btn" id="t-add">Add request</button></div></section>
      <section class="sec"><h2>Spot a diverted tanker</h2><p class="sub">GPS pings are compared with the planned route: off-route, long stops and GPS gaps raise the risk.</p>
        <div class="row"><button class="btn small ghost" data-div="clean">Simulate an honest trip</button><button class="btn small ghost" data-div="bad">Simulate a diverted trip</button></div><div id="t-divout"></div></section>
      <section class="sec"><h2>Start over</h2><div class="row"><button class="btn small ghost" id="t-reset">Clear demo requests and fleet</button></div></section>`);
    $$("#t-queue li").forEach((li) => li.addEventListener("click", () => map.flyTo(li.dataset.ll.split(",").map(Number), 15)));
    $$("[data-trip]").forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true;
      try { const r = await post("/api/tankers/dispatch", p.trips[+b.dataset.trip]); toast(`Dispatched. ${r.dispatched} code(s) sent via ${Object.values(r.codes)[0]?.otp_sent_via || "SMS"}.`); tankersLoad(); }
      catch (e) { b.disabled = false; toast(e.message); }
    }));
    $("#t-peek").addEventListener("click", async () => {
      const rid = $("#t-rid").value; if (!rid) return;
      try { const r = await api(`/api/tankers/otp/${rid}`); $("#t-otp").value = r.otp || ""; } catch (e) { toast(e.message); }
    });
    $("#t-conf").addEventListener("click", async () => {
      const rid = $("#t-rid").value; if (!rid) return;
      const r = await post("/api/tankers/confirm", { id: rid, otp: $("#t-otp").value }).catch((e) => ({ ok: false, reason: e.message }));
      if (r.ok) { toast(`Delivered: ${fmt(r.request.delivered_l)} L to ${r.request.area}`); tankersLoad(); }
      else put("t-confout", `<div class="err">Not confirmed: ${esc(r.reason)}</div>`);
    });
    $("#t-add").addEventListener("click", async () => {
      const area = $("#t-area").value.trim(); if (!area) return toast("Name the area first");
      try {
        await post("/api/tankers/request", { area, role: $("#t-role").value, institution: $("#t-inst").value, population: +$("#t-pop").value,
          days_without: +$("#t-days").value, phone: $("#t-phone").value || null, lat: S.place.lat, lon: S.place.lon });
        toast("Request added and ranked"); tankersLoad();
      } catch (e) { toast(e.message); }
    });
    $$("[data-div]").forEach((b) => b.addEventListener("click", async () => {
      const t = p.trips[0]; if (!t) return toast("No trip to check");
      const bad = b.dataset.div === "bad";
      const track = [];
      t.line.forEach((pt, i) => {
        const next = t.line[i + 1] || pt;
        for (let f = 0; f < 1; f += 0.25) track.push({ lat: pt[0] + (next[0] - pt[0]) * f, lon: pt[1] + (next[1] - pt[1]) * f, ts: 0 });
      });
      if (bad) { const mid = Math.floor(track.length / 2); track.splice(mid, 0, { lat: track[mid].lat - 0.03, lon: track[mid].lon + 0.03 }, { lat: track[mid].lat - 0.03, lon: track[mid].lon + 0.03 }); }
      let ts = 1_700_000_000; track.forEach((x, i) => { ts += bad && i === Math.floor(track.length / 2) + 1 ? 35 * 60 : 120; x.ts = ts; });
      try {
        const r = await post("/api/tankers/diversion", { planned: t.line, track });
        put("t-divout", `<div class="stats"><div><div class="v ${r.verdict === "clean" ? "green" : r.verdict === "watch" ? "orange" : "red"}">${r.risk}</div><div class="l">diversion risk · ${esc(r.verdict)}</div></div><div><div class="v">${r.max_off_km}<small>km</small></div><div class="l">furthest from route</div></div></div>
          ${r.flags.length ? `<ul class="plain">${r.flags.map((f) => `<li>${esc(f.type)}${f.km ? ` · ${f.km} km off` : ""}${f.minutes ? ` · ${f.minutes} min` : ""}</li>`).join("")}</ul>` : '<p class="fine">Stayed on route.</p>'}`);
        const T2 = layer("tankers"); L.polyline(track.map((x) => [x.lat, x.lon]), { color: bad ? C.bad : C.ok, weight: 2, dashArray: "3 4" }).addTo(T2);
      } catch (e) { put("t-divout", errBox(e)); }
    }));
    $("#t-reset").addEventListener("click", async () => { await post("/api/tankers/reset", {}); toast("Cleared. Demo data will be re-seeded."); tankersLoad(); });
  }

  // =====================================================================
  // LEAKS: which pipe is losing water?
  // =====================================================================
  register("leaks", {
    title: "Which pipe is <em>losing water</em>?", kicker: "Leak desk", needsPlace: true, layers: ["pipes"], zoom: 14,
    intro: "Pressure sensors are compared with what they should read, a CUSUM chart catches slow drifts, and the pattern of which sensors dropped points to the pipe.",
    render(el) {
      el.innerHTML = head("Leak desk", "Which pipe is <em>losing water</em>?",
        "A demo district network laid over your pin, with a slow leak and a burst hidden in four weeks of sensor data. The desk finds both without being told where they are.") + `
        <div id="l-body">${loading("Running four weeks of sensor data…")}</div>`;
      leaksLoad();
    },
  });

  async function leaksLoad() {
    let d;
    try { d = await api(`/api/leaks?${q()}`); } catch (e) { return put("l-body", errBox(e)); }
    if (S.desk !== "leaks") return;
    const P = layer("pipes"); P.clearLayers();
    const col = { High: C.bad, Medium: C.warn, Normal: C.water };
    const sus = new Set([d.suspects[0]?.pipe, d.later_suspects?.[0]?.pipe]);
    const pts = [];
    d.pipes.forEach((p) => {
      L.polyline(p.line, { color: sus.has(p.id) ? C.bad : col[p.severity] || C.water, weight: sus.has(p.id) ? 7 : p.severity === "Normal" ? 3 : 5, opacity: sus.has(p.id) ? 1 : 0.75 })
        .bindTooltip(`<b>${p.id}</b> · ${p.zone}<br>${p.material}, ${p.age} yrs · ${esc(p.severity)}`, { className: "tt" }).addTo(P);
      pts.push(...p.line);
    });
    d.sensors.forEach((s) => L.marker(s.latlon, { icon: div("mk-node" + (s.tripped ? " blocked" : ""), "", 12) }).bindTooltip(`${s.id}${s.tripped ? " · alarm" : ""}`, { className: "tt" }).addTo(P));
    fitTo(pts, 40);
    legend(`<div class="ttl">Pipes</div>${sw(C.bad, "suspect / high")}${sw(C.warn, "medium")}${sw(C.water, "normal")}${sw(C.ink, "■ sensor (red = alarm)")}`);
    if (!put("l-body", `
      <div class="stats"><div><div class="v ${d.alarm_day != null ? "red" : "green"}">${d.alarm_day != null ? `day ${d.alarm_day}` : "none"}</div><div class="l">first alarm</div></div>
        <div><div class="v">${fmt(d.lost_m3_day)}<small>m³/d</small></div><div class="l">water being lost</div></div>
        <div><div class="v">₹${fmt(d.lost_rupees_month / 1e5, 1)}<small>L</small></div><div class="l">a month at bulk rates</div></div></div>
      <section class="sec"><h2>Where is it?</h2>
        <p class="sub">Each pipe has a fingerprint: which sensors would drop if it leaked. The alarm's pattern is matched against all of them.</p>
        ${tabs("l-tabs", ["First alarm", "Later event"], "First alarm")}<div id="l-sus"></div></section>
      <section class="sec"><h2>Sensor drift (CUSUM)</h2><p class="sub">Small drops add up day after day until they cross the line. A slow leak shows here long before anyone sees water.</p><div class="chart"><canvas id="c-cusum"></canvas></div></section>
      <section class="sec"><h2>Night flow</h2><p class="sub">Between 2 and 4 am almost nobody uses water, so a rising minimum night flow is lost water.</p><div class="chart short"><canvas id="c-mnf"></canvas></div>
        <p class="fine">Night-flow alarm on day ${d.mnf_alarm_day ?? "—"} vs pressure alarm on day ${d.alarm_day ?? "—"}.</p></section>
      <section class="sec"><h2>Pipes to inspect</h2><table class="t"><thead><tr><th>Pipe</th><th>Zone</th><th>Pipe</th><th>Pressure</th><th>Vibration</th><th>Grade</th></tr></thead><tbody>
        ${d.pipe_table.map((r) => `<tr><td>${r.pipe}</td><td>${r.zone}</td><td>${r.material} · ${r.age}y</td><td class="n">${r.pressure}</td><td class="n">${r.vibration}</td><td><span class="pill ${r.severity}">${esc(r.severity)}</span></td></tr>`).join("")}</tbody></table>
        <p class="fine">Graded by an Isolation Forest over pressure, flow, vibration, temperature and pipe age.</p></section>
      <section class="sec"><h2>What if a pipe reads…</h2><div class="grid2">
          ${slider("lw-p", "Pressure (m)", 5, 40, 0.5, 22, "")}${slider("lw-f", "Flow (L/s)", 1, 20, 0.5, 7, "")}
          ${slider("lw-v", "Vibration (mm/s)", 0, 5, 0.1, 1.2, "")}${slider("lw-a", "Pipe age (yrs)", 0, 60, 1, 25, "")}</div>
        <label class="field" style="max-width:200px">Material<select id="lw-m">${["CI", "AC", "DI", "PVC", "HDPE", "GI"].map((x) => `<option>${x}</option>`).join("")}</select></label><div id="lw-out"></div></section>
      <section class="sec"><h2>Score a CSV of readings</h2><p class="sub">Paste rows with the header <code>Pressure,Flow_Rate,Vibration,Temperature,Pipe_Age,Material</code>.</p>
        <label class="field"><textarea id="lb-csv">Pressure,Flow_Rate,Vibration,Temperature,Pipe_Age,Material
18,8.5,2.4,23,40,CI
27,6,0.7,24,5,HDPE
21,9,1.9,25,32,AC</textarea></label><div class="row"><button class="btn small" id="lb-go">Score rows</button><label class="btn small ghost" style="display:inline-grid;place-items:center">Load file<input type="file" id="lb-file" accept=".csv,text/csv" hidden></label></div><div id="lb-out"></div>
        <p class="fine">${esc(d.method)}. Live sensors can post to <code>/api/leaks/reading</code>.</p></section>`)) return;
    const sus1 = (list, day) => `<p class="fine" style="margin:0 0 6px">Alarm on day ${day}. Best matches:</p><ul class="ledger">${list.map((s, i) => `<li data-pipe="${s.pipe}"><span class="sw ${i === 0 ? "red" : "orange"}"></span><span><span class="t">${s.pipe}</span><br><span class="s">zone ${s.zone}</span></span><span class="v">${Math.round(s.match * 100)}%<small>match</small></span></li>`).join("")}</ul>`;
    const show = (t) => {
      put("l-sus", t === "Later event" && d.later_suspects ? sus1(d.later_suspects, d.later_day) : sus1(d.suspects, d.alarm_day));
      $$("#l-sus li").forEach((li) => li.addEventListener("click", () => { const p = d.pipes.find((x) => x.id === li.dataset.pipe); if (p) map.fitBounds(p.line, { maxZoom: 17 }); }));
    };
    show("First alarm"); onTabs("l-tabs", show);
    const days = Array.from({ length: d.days }, (_, i) => i + 1);
    chart("cusum", $("#c-cusum"), { type: "line", data: { labels: days, datasets: d.sensors.map((s) => ({ label: s.id, data: s.cusum, borderColor: s.tripped ? C.bad : "rgba(32,29,24,.25)", borderWidth: s.tripped ? 2 : 1, pointRadius: 0 })) },
      options: { maintainAspectRatio: false, plugins: { annotation: { annotations: d.alarm_day != null ? {
        alarm: { type: "line", xMin: d.alarm_day - 1, xMax: d.alarm_day - 1, borderColor: C.bad, borderDash: [4, 3], label: { display: true, content: "alarm", position: "start", color: C.bad, backgroundColor: "transparent" } } } : {} } },
        scales: { x: { grid: { display: false }, title: { display: true, text: "day" } }, y: { grid, title: { display: true, text: "CUSUM" } } } } });
    chart("mnf", $("#c-mnf"), { type: "line", data: { labels: days, datasets: [{ label: "Night flow", data: d.mnf, borderColor: C.water, backgroundColor: "rgba(31,97,112,.12)", fill: true, pointRadius: 0, borderWidth: 2 }] },
      options: { maintainAspectRatio: false, plugins: { annotation: { annotations: { base: { type: "line", yMin: d.mnf_base, yMax: d.mnf_base, borderColor: C.ink3, borderDash: [4, 3], label: { display: true, content: "normal", position: "start", backgroundColor: "transparent", color: C.ink2 } } } } },
        scales: { x: { grid: { display: false } }, y: { grid, title: { display: true, text: "L/s" } } } } });
    const whatif = debounce(async () => {
      try {
        const r = await api(`/api/leaks/whatif?pressure=${$("#lw-p").value}&flow=${$("#lw-f").value}&vibration=${$("#lw-v").value}&age=${$("#lw-a").value}&material=${$("#lw-m").value}`);
        put("lw-out", `<div class="stats" style="margin-top:8px"><div><div class="v ${r.probability >= 0.7 ? "red" : r.probability >= 0.4 ? "orange" : "green"}">${Math.round(r.probability * 100)}<small>%</small></div><div class="l">${esc(r.verdict)}</div></div></div>
          ${bars(r.importance.map((x) => ({ label: x.feature, v: x.weight, text: `${Math.round(x.weight * 100)}%` })), "water")}<p class="fine">Random Forest; bars show what the model leans on.</p>`);
      } catch (e) { put("lw-out", errBox(e)); }
    });
    ["lw-p", "lw-f", "lw-v", "lw-a"].forEach((id) => bindSlider(id, "", whatif));
    $("#lw-m").addEventListener("change", whatif); whatif();
    $("#lb-file").addEventListener("change", async (e) => { const f = e.target.files[0]; if (f) $("#lb-csv").value = (await f.text()).slice(0, 400000); });
    $("#lb-go").addEventListener("click", async () => {
      try {
        const r = await post("/api/leaks/batch", { csv: $("#lb-csv").value });
        put("lb-out", `<p class="sub" style="margin-top:8px"><b>${r.flagged}</b> of ${r.rows.length} rows look like a leak.</p><table class="t"><thead><tr><th>P</th><th>Flow</th><th>Vib</th><th>Age</th><th>Mat</th><th>Leak?</th></tr></thead><tbody>${r.rows.slice(0, 50).map((x) => `<tr><td class="n">${esc(x.Pressure)}</td><td class="n">${esc(x.Flow_Rate)}</td><td class="n">${esc(x.Vibration)}</td><td class="n">${esc(x.Pipe_Age)}</td><td>${esc(x.Material)}</td><td><span class="pill ${x.verdict === "likely leak" ? "red" : x.verdict === "possible leak" ? "orange" : "green"}">${Math.round(x.leak_probability * 100)}%</span></td></tr>`).join("")}</tbody></table>`);
      } catch (e) { put("lb-out", errBox(e)); }
    });
  }
})();
