/* JalTaap 2 — core shell + Pulse, Field reports, Replay and AWS desks.
   Desk modules for Heat, Flood, Drought, Groundwater, Tankers and Leaks live in desks.js. */
(() => {
  "use strict";

  // ---------- helpers ----------
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const fmt = (n, d = 0) => (n == null || Number.isNaN(n) ? "—" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = async (path, opts) => {
    const r = await fetch(path, opts);
    if (!r.ok) {
      let msg = `${r.status}`;
      try { msg = (await r.json()).detail || msg; } catch (_) { /* not json */ }
      throw new Error(msg);
    }
    return r.status === 204 ? null : r.json();
  };
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const C = {
    ink: css("--ink"), ink2: css("--ink-2"), ink3: css("--ink-3"), rule: css("--rule"), paper: css("--paper"), card: css("--card"),
    water: css("--water"), heat: css("--heat"), dry: css("--dry"), ok: css("--ok"), warn: css("--warn"), bad: css("--bad"), aws: css("--aws"),
  };
  const LV = { green: C.ok, watch: C.warn, orange: C.warn, red: C.bad };
  const WORD = { green: "Normal", watch: "Watch", orange: "Warning", red: "Danger" };
  const dayName = (iso) => new Date(iso.slice(0, 10) + "T00:00").toLocaleDateString("en-IN", { weekday: "short" });
  const niceDate = (iso) => new Date(iso.slice(0, 10) + "T00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  const longDate = (iso) => new Date(iso.slice(0, 10) + "T00:00").toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric" });
  const hr12 = (h) => `${h % 12 === 0 ? 12 : h % 12} ${h < 12 ? "am" : "pm"}`;
  const toast = (msg, ms = 3400) => {
    const t = $("#toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast._h); toast._h = setTimeout(() => (t.hidden = true), ms);
  };
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const loading = (msg) => `<div class="loading">${esc(msg)}</div>`;
  const errBox = (e) => `<div class="err">${esc(e.message || e)}</div>`;
  const stamp = (lvl, text) => `<span class="stamp ${lvl}">${esc(text || WORD[lvl] || lvl)}</span>`;
  const bars = (rows, cls = "", max) => {
    const m = max ?? Math.max(...rows.map((r) => Math.abs(r.v)), 0.0001);
    return `<div class="bars">${rows.map((r) => `<div class="bar"><span>${esc(r.label)}</span>
      <span class="track"><span class="fill ${r.v < 0 ? "neg" : cls}" style="width:${Math.min(100, (100 * Math.abs(r.v)) / m)}%"></span></span>
      <span class="n">${r.text ?? fmt(r.v, 1)}</span></div>`).join("")}</div>`;
  };
  const meter = (v, max = 100, labels = ["low", "high"], colors = [C.ok, C.warn, C.bad]) => `
    <div class="meter"><div class="scale">${colors.map((c) => `<span style="background:${c}"></span>`).join("")}</div>
      <div class="needle" style="left:${Math.max(0, Math.min(100, (100 * v) / max))}%" data-v="${fmt(v)}"></div></div>
    <div class="meter-labels">${labels.map((l) => `<span>${esc(l)}</span>`).join("")}</div>`;

  Chart.defaults.font.family = css("--sans");
  Chart.defaults.font.size = 11;
  Chart.defaults.color = C.ink2;
  Chart.defaults.plugins.legend.display = false;
  Chart.defaults.animation = reduceMotion ? false : { duration: 350 };
  Chart.defaults.elements.line.borderJoinStyle = "round";
  const charts = {};
  function chart(key, canvas, cfg) {
    if (charts[key]) charts[key].destroy();
    if (!canvas) return null;
    charts[key] = new Chart(canvas, cfg);
    return charts[key];
  }
  const grid = { color: "rgba(32,29,24,.07)" };

  // ---------- state ----------
  const S = { desk: "pulse", place: null, board: null, meta: null, lang: "hi", aws: null };

  // ---------- map ----------
  const INDIA = [[7.5, 68.5], [35.5, 97.2]];
  const map = L.map("map", { zoomControl: false, minZoom: 4, maxBounds: [[0, 55], [42, 110]], attributionControl: true });
  map.fitBounds(INDIA, { padding: [10, 10] });
  L.control.zoom({ position: "topright" }).addTo(map);
  // base map: OpenStreetMap needs no key; Amazon Location satellite is added when the server has a Maps API key
  const DATA_CREDIT = " · Rivers: GloFAS via Open-Meteo · Sentinel-2: AWS Open Data";
  const streets = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, className: "base-paper",
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' + DATA_CREDIT,
  }).addTo(map);
  fetch("/api/map-config").then((r) => r.json()).then((m) => {
    if (!m.satellite) return;
    const sat = L.tileLayer(m.satellite, { maxZoom: 18, attribution: m.attribution + DATA_CREDIT });
    L.control.layers({ "Streets (OpenStreetMap)": streets, "Satellite (Amazon Location)": sat }, null,
      { position: "topright", collapsed: true }).addTo(map);
  }).catch(() => {});
  map.createPane("rivers"); map.getPane("rivers").style.zIndex = 350;
  fetch("/static/rivers.geojson").then((r) => r.json()).then((g) => {
    L.geoJSON(g, { pane: "rivers", interactive: false,
      style: (f) => ({ color: C.water, opacity: 0.45, weight: Math.max(0.7, 3 - f.properties.r * 0.33) }) }).addTo(map);
  }).catch(() => {});

  const layers = {};
  const layer = (name) => (layers[name] ||= L.layerGroup());
  const pinLayer = layer("pin").addTo(map);
  function showLayers(names) {
    Object.entries(layers).forEach(([k, l]) => {
      if (k === "pin") return;
      if (names.includes(k)) { if (!map.hasLayer(l)) l.addTo(map); } else if (map.hasLayer(l)) map.removeLayer(l);
    });
  }
  const div = (cls, html = "", size = 16) => L.divIcon({ className: "", iconSize: [size, size], iconAnchor: [size / 2, size / 2], html: `<div class="${cls}" style="width:${size}px;height:${size}px">${html}</div>` });
  function legend(html) { $("#legend").innerHTML = html || ""; }
  function maptools(html) { $("#maptools").innerHTML = html || ""; }
  function mapcard(html) { const m = $("#mapcard"); m.hidden = !html; m.innerHTML = html || ""; }
  const sw = (color, label, round) => `<div class="li"><span class="sw" style="background:${color};${round ? "border-radius:50%" : ""}"></span>${label}</div>`;

  // ---------- "Start here": three steps for a first visit, each ticks itself off ----------
  const GUIDE_KEY = "jaltaap.guide";
  const GUIDE_STEPS = [
    ["place", "Pick a place", "Search a town or village, or tap the map."],
    ["desk", "Open a desk", "Heat, Flood, Drought… each answers one question."],
    ["alert", "Send the alert", "On Pulse, pick who it is for and press Write the alert."],
  ];
  const guideDone = { place: false, desk: false, alert: false };
  const remember = {
    get: (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* private window: the guide just shows again */ } },
  };
  function markGuide(step) { guideDone[step] = true; }
  function renderGuide() {
    let g = $("#guide");
    if (remember.get(GUIDE_KEY) === "off") { if (g) g.remove(); return; }
    if (!g) {
      g = document.createElement("aside");
      g.id = "guide"; g.className = "guide"; g.setAttribute("aria-label", "Start here");
      $("#sheet").prepend(g);
      g.addEventListener("click", onGuideClick);
    }
    const n = GUIDE_STEPS.filter(([k]) => guideDone[k]).length;
    g.innerHTML = `<div class="g-head"><b>${n === 3 ? "That's the whole loop" : "Start here"}</b><span class="num">${n}/3</span>
        <button class="g-x" data-g="close" aria-label="Hide this guide" title="Hide (bring it back with ?)">×</button></div>
      <ol>${GUIDE_STEPS.map(([k, t, sub], i) => `<li class="${guideDone[k] ? "done" : ""}"><button data-g="${k}">
        <span class="g-n">${guideDone[k] ? "✓" : i + 1}</span><span><b>${t}</b><small>${sub}</small></span></button></li>`).join("")}</ol>`;
    if (n === 3) setTimeout(() => { remember.set(GUIDE_KEY, "off"); renderGuide(); }, 4000);
  }
  function onGuideClick(e) {
    const b = e.target.closest("[data-g]");
    if (!b) return;
    const step = b.dataset.g;
    if (step === "close") { remember.set(GUIDE_KEY, "off"); renderGuide(); return; }
    if (step === "place" || !S.place) { $("#q").focus(); toast("Type a town or village, or tap the map"); return; }
    if (step === "desk") { setDesk(S.desk === "heat" ? "flood" : "heat"); return; }
    if (S.desk !== "pulse") setDesk("pulse");
    const btn = $("#mk-alert");
    if (btn) { btn.scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" }); btn.focus({ preventScroll: true }); }
  }
  $("#helpbtn").addEventListener("click", () => { remember.set(GUIDE_KEY, ""); renderGuide(); $("#sheet").scrollTop = 0; });

  // ---------- desks ----------
  const desks = {};
  const register = (name, d) => (desks[name] = d);
  const sheet = () => $("#sheet-inner");

  function setDesk(name, push = true) {
    if (!desks[name]) name = "pulse";
    S.desk = name;
    $$("#rail button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.desk === name)));
    const d = desks[name];
    showLayers(d.layers || []);
    legend(""); maptools(""); mapcard("");
    $("#sheet").scrollTop = 0;
    const el = sheet();
    el.className = `sheet-inner desk-${name}`;
    if (d.needsPlace && !S.place) { renderPicker(el, d); }
    else { d.render(el); }
    if (push) history.replaceState(null, "", `?desk=${name}${S.place ? `&lat=${S.place.lat}&lon=${S.place.lon}&name=${encodeURIComponent(S.place.name || "")}` : ""}`);
    if (S.place && d.needsPlace) markGuide("desk");
    renderGuide();
    setTimeout(() => map.invalidateSize(), 60);
  }
  $$("#rail button").forEach((b) => b.addEventListener("click", () => setDesk(b.dataset.desk)));

  const PICKS = [["Guwahati", "Assam", 26.144, 91.736], ["Patna", "Bihar", 25.594, 85.137], ["Chennai", "Tamil Nadu", 13.083, 80.27],
    ["Jaipur", "Rajasthan", 26.912, 75.787], ["Bengaluru", "Karnataka", 12.972, 77.594], ["Nagpur", "Maharashtra", 21.146, 79.088],
    ["Delhi", "NCT", 28.614, 77.209], ["Kochi", "Kerala", 9.931, 76.267], ["Latur", "Maharashtra", 18.401, 76.56]];
  function renderPicker(el, d) {
    el.innerHTML = `<p class="kicker">${esc(d.kicker || d.title)}</p><h1 class="head">${d.title}</h1>
      <p class="lede">${d.intro || ""}</p>
      <section class="sec"><h2>Pick a place to start</h2>
        <p class="sub">Search at the top, tap the map, or start from one of these.</p>
        <div class="picks">${PICKS.map((p) => `<button data-p='${JSON.stringify(p)}'>${p[0]}<small>${p[1]}</small></button>`).join("")}</div>
      </section>`;
    $$(".picks button", el).forEach((b) => b.addEventListener("click", () => { const p = JSON.parse(b.dataset.p); selectPlace(p[2], p[3], p[0], p[1]); }));
  }

  function placeLine() {
    const p = S.place;
    if (!p) return "";
    return `<div class="placeline">📍 <b>${esc(p.name || "This spot")}</b>${p.sub ? ` · ${esc(p.sub)}` : ""}
      <span class="num">(${p.lat.toFixed(3)}, ${p.lon.toFixed(3)})</span> <button data-act="clear">change</button></div>`;
  }
  document.addEventListener("click", (e) => {
    if (e.target.matches('.placeline [data-act="clear"]')) { $("#q").focus(); }
  });

  function selectPlace(lat, lon, name = "", sub = "") {
    S.place = { lat: +(+lat).toFixed(4), lon: +(+lon).toFixed(4), name, sub };
    markGuide("place");
    pinLayer.clearLayers();
    L.marker([S.place.lat, S.place.lon], { icon: div("mk-pin", "", 18), zIndexOffset: 1000 }).addTo(pinLayer);
    const d = desks[S.desk];
    if (d.onPlace) d.onPlace(S.place);
    else if (!d.keepView) map.flyTo([S.place.lat, S.place.lon], Math.max(map.getZoom(), d.zoom || 9), { duration: reduceMotion ? 0 : 0.8 });
    if (!name) reverseName(S.place);
    $("#answer").hidden = true;
    setDesk(S.desk);
  }
  async function reverseName(p) {
    try {
      const r = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=12&accept-language=en&lat=${p.lat}&lon=${p.lon}`).then((x) => x.json());
      const a = r.address || {};
      const n = a.village || a.town || a.city || a.suburb || a.county || a.state_district;
      if (n && S.place === p) {
        p.name = n; p.sub = [a.state_district || a.county, a.state].filter(Boolean).join(", ");
        $$(".placeline b").forEach((b) => (b.textContent = n));
      }
    } catch (_) { /* name is a nicety */ }
  }
  map.on("click", (e) => {
    const d = desks[S.desk];
    if (d.onMapClick && d.onMapClick(e.latlng) === true) return;
    selectPlace(e.latlng.lat, e.latlng.lng);
  });

  // ---------- search ----------
  let searchT;
  $("#q").addEventListener("input", (e) => {
    clearTimeout(searchT);
    const q = e.target.value.trim();
    if (q.length < 2) { $("#results").hidden = true; return; }
    searchT = setTimeout(async () => {
      try {
        const res = await api(`/api/search?q=${encodeURIComponent(q)}`);
        $("#results").innerHTML = res.length ? res.map((r, i) => `<li role="option" data-i="${i}">${esc(r.name)}<small>${esc([r.district, r.state].filter(Boolean).join(", "))}</small></li>`).join("")
          : `<li aria-disabled="true">No place in India called “${esc(q)}”. Try the district.</li>`;
        $("#results").hidden = false;
        $$("#results li[data-i]").forEach((li) => li.addEventListener("click", () => {
          const r = res[+li.dataset.i];
          $("#results").hidden = true; $("#q").value = r.name;
          selectPlace(r.lat, r.lon, r.name, [r.district, r.state].filter(Boolean).join(", "));
        }));
      } catch (_) { /* ignore */ }
    }, 250);
  });
  $("#q").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { const f = $("#results li[data-i]"); if (f) f.click(); }
    if (e.key === "Escape") $("#results").hidden = true;
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".where")) $("#results").hidden = true; });

  // ---------- copilot (Amazon Bedrock) ----------
  $("#askform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("#askq").value.trim();
    if (!q) return;
    const box = $("#answer");
    if (!S.place) { box.hidden = false; box.innerHTML = `<button class="x" aria-label="close">×</button>Pick a place first (search at the top or tap the map), then ask.`; return; }
    box.hidden = false; box.innerHTML = loading("Reading what the desks know about this place…");
    try {
      const a = await post("/api/ask", { q, lat: S.place.lat, lon: S.place.lon, name: S.place.name || "", lang: S.lang });
      box.innerHTML = `<button class="x" aria-label="close">×</button>${esc(a.text)}<div class="by">${a.by.startsWith("Amazon") ? '<span class="awsmark">AWS</span>' : ""}${esc(a.by)}</div>`;
    } catch (err) { box.innerHTML = errBox(err); }
  });
  $("#answer").addEventListener("click", (e) => { if (e.target.matches(".x")) $("#answer").hidden = true; });

  // ---------- speech: the browser's own voice ----------
  const VOICE = { en: "en-IN", hi: "hi-IN", bn: "bn-IN", as: "as-IN", ta: "ta-IN", te: "te-IN", ml: "ml-IN", mr: "mr-IN", gu: "gu-IN", kn: "kn-IN", or: "or-IN", pa: "pa-IN", ur: "ur-IN" };
  function speak(text, lang) {
    const clean = text.replace(/[\u{1F300}-\u{1FAFF}☀-➿]/gu, "").trim();
    if (!("speechSynthesis" in window)) return toast("This browser can't read aloud.");
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(clean);
    u.lang = VOICE[lang] || "en-IN"; u.rate = 0.95;
    const v = speechSynthesis.getVoices().find((x) => x.lang.replace("_", "-").startsWith(u.lang.slice(0, 2)));
    if (v) u.voice = v;
    speechSynthesis.speak(u);
  }

  // =====================================================================
  // PULSE: national board, then everything about one place
  // =====================================================================
  function riverMarker(r) {
    const ring = Math.min(70, 22 + Math.max(0, r.ratio - 0.8) * 55);
    const hot = r.level === "orange" || r.level === "red";
    const m = L.marker([r.lat, r.lon], {
      icon: L.divIcon({ className: "", iconSize: [ring, ring], iconAnchor: [ring / 2, ring / 2],
        html: `<div class="mk-river ${r.level}" style="width:${ring}px;height:${ring}px;position:relative">${hot ? '<div class="halo"></div>' : ""}<div class="core"></div></div>` }),
      title: `${r.town}, ${r.river}`,
    });
    m.bindTooltip(`<b>${esc(r.town)}</b> · ${esc(r.river)}<br>${WORD[r.level]} · peak ${fmt(r.peak)} m³/s<br><span style="opacity:.7">danger line ${fmt(r.p99)} m³/s</span>`, { className: "tt", direction: "top" });
    m.on("click", (e) => { L.DomEvent.stopPropagation(e); selectPlace(r.lat, r.lon, r.town, `${r.river} river, ${r.state}`); });
    return m;
  }
  function cityMarker(c) {
    const s = c.level === "green" ? 22 : 28;
    const col = c.level === "red" ? C.bad : c.level === "orange" ? C.warn : (c.feels ?? 30) >= 38 ? "#C9A34A" : "#7C9A86";
    const m = L.marker([c.lat, c.lon], { icon: L.divIcon({ className: "", iconSize: [s, s], iconAnchor: [s / 2, s / 2],
      html: `<div class="mk-city" style="width:${s}px;height:${s}px;background:${col}"><span>${c.feels != null ? Math.round(c.feels) : ""}</span></div>` }), title: c.city });
    m.bindTooltip(`<b>${esc(c.city)}</b><br>feels like ${c.feels}°C on ${c.date ? niceDate(c.date) : "—"}${c.wetbulb ? `<br>wet-bulb ${c.wetbulb}°C` : ""}${c.wbgt_today ? `<br>outdoor work today: ${c.work_label} (WBGT ${c.wbgt_today}°C)` : ""}`, { className: "tt", direction: "top" });
    m.on("click", (e) => { L.DomEvent.stopPropagation(e); selectPlace(c.lat, c.lon, c.city, ""); });
    return m;
  }

  async function loadBoard() {
    try { S.board = await api("/api/board"); } catch (e) { S.board = { rivers: [], cities: [], error: e.message }; }
    const { rivers, cities } = S.board;
    layer("rivers").clearLayers();
    rivers.slice().sort((a, b) => a.ratio - b.ratio).forEach((r) => layer("rivers").addLayer(riverMarker(r)));
    layer("cities").clearLayers();
    cities.forEach((c) => layer("cities").addLayer(cityMarker(c)));
    const hot = rivers.filter((r) => r.level === "orange" || r.level === "red").length;
    const b = $('#rail [data-desk="flood"]');
    b.querySelector(".badge")?.remove();
    if (hot) b.insertAdjacentHTML("beforeend", `<span class="badge">${hot}</span>`);
    if (S.desk === "pulse" && !S.place) desks.pulse.render(sheet());
  }

  register("pulse", {
    title: "Pulse", layers: ["rivers", "cities", "gauges", "gpoly"], zoom: 8,
    render(el) { return S.place ? renderPlace(el) : renderNational(el); },
  });

  function renderNational(el) {
    legend(`<div class="ttl">Rivers vs their own history</div>${sw(C.bad, "Danger: above the 1-in-100-day flow", 1)}${sw(C.warn, "Warning: above the 1-in-20-day flow", 1)}${sw(C.water, "Normal", 1)}<div class="ttl" style="margin-top:6px">Cities (◆ = feels-like °C this week)</div>`);
    if (!S.board) { el.innerHTML = `<p class="kicker">Pulse · all India</p><h1 class="head">Where is water or heat <em>turning dangerous</em> this week?</h1>${loading("Checking every watched river and city…")}`; return; }
    const { rivers, cities } = S.board;
    const rRisk = rivers.filter((r) => r.level === "orange" || r.level === "red").length;
    const rWatch = rivers.filter((r) => r.level === "watch").length;
    const hRisk = cities.filter((c) => c.level !== "green").length;
    const work = cities.filter((c) => c.work_level === "red").length;
    const topR = rivers.slice().sort((a, b) => b.ratio - a.ratio).slice(0, 6);
    const topH = cities.slice().sort((a, b) => (b.feels ?? 0) - (a.feels ?? 0)).slice(0, 6);
    el.innerHTML = `
      <p class="kicker">Pulse · all India · updated ${esc(S.board.updated || "")}</p>
      <h1 class="head">Where is water or heat <em>turning dangerous</em> this week?</h1>
      <p class="lede">${rivers.length} river points and ${cities.length} cities, each judged against its own 30–40 years of history, not one national number. Tap anything to open that place on every desk.</p>
      ${S.board.error || (S.board.errors || []).length ? `<div class="err">Some forecast servers didn't answer: ${esc(S.board.error || S.board.errors.join("; "))}</div>` : ""}
      <div class="stats">
        <div><div class="v ${rRisk ? "orange" : ""}">${rRisk}<small>/${rivers.length}</small></div><div class="l">rivers heading over their warning line (15 days)${rWatch ? `, ${rWatch} on watch` : ""}</div></div>
        <div><div class="v ${hRisk ? "red" : ""}">${hRisk}<small>/${cities.length}</small></div><div class="l">cities with a heat alert (7 days)</div></div>
        <div><div class="v ${work ? "red" : ""}">${work}</div><div class="l">cities where heavy outdoor work is unsafe today</div></div>
      </div>
      <section class="sec"><h2>Rivers closest to their danger line</h2>
        <ul class="ledger">${topR.map((r) => `<li data-lat="${r.lat}" data-lon="${r.lon}" data-n="${esc(r.town)}" data-s="${esc(r.river)} river, ${esc(r.state)}">
          <span class="sw ${r.level === "green" ? "" : r.level}"></span><div><div class="t">${esc(r.town)}</div><div class="s">${esc(r.river)} · ${esc(r.state)} · peak ${niceDate(r.peak_date)}</div></div>
          <div class="v">${Math.round(r.ratio * 100)}%<small>of warning line</small></div></li>`).join("") || '<p class="empty">No river data.</p>'}</ul></section>
      <section class="sec"><h2>Hottest feels-like this week</h2>
        <ul class="ledger">${topH.map((c) => `<li data-lat="${c.lat}" data-lon="${c.lon}" data-n="${esc(c.city)}" data-s="">
          <span class="sw ${c.level === "green" ? "" : c.level}"></span><div><div class="t">${esc(c.city)}</div><div class="s">${c.date ? niceDate(c.date) : ""}${c.wetbulb ? ` · wet-bulb ${c.wetbulb}°C` : ""}${c.level !== "green" && !c.imd_heatwave ? " · official rule misses it" : ""}</div></div>
          <div class="v">${c.feels != null ? Math.round(c.feels) + "°" : "—"}<small>feels like</small></div></li>`).join("") || '<p class="empty">No city data.</p>'}</ul></section>
      <section class="sec"><h2>What each desk does</h2>
        <ul class="plain">
          <li><b>Heat</b>: body heat load (Heat Index + WBGT + UTCI), heat pockets inside a town, who is most at risk, safe working hours.</li>
          <li><b>Flood</b>: rivers 15 days ahead, street waterlogging with reasons, drain blockages, cloudburst flash alerts.</li>
          <li><b>Drought</b> & <b>Groundwater</b>: SPI/SPEI, water health, crops that fit the water, recharge plans, well depth outlook in CGWB terms.</li>
          <li><b>Tankers</b>: fair priority queue, routes, one-time delivery codes. <b>Leaks</b>: new pipe leaks found from pressure sensors and pinned to a zone.</li>
        </ul></section>`;
    $$(".ledger li", el).forEach((li) => li.addEventListener("click", () => selectPlace(+li.dataset.lat, +li.dataset.lon, li.dataset.n, li.dataset.s)));
  }

  async function renderPlace(el) {
    const p = S.place;
    el.innerHTML = `<p class="kicker">Pulse · this place</p><h1 class="head">${esc(p.name || "This spot")}: <em>the next two weeks</em></h1>${placeLine()}
      <div id="pl-risks">${loading("Reading 30+ years of river and weather history for this spot…")}</div>
      <section class="sec" id="pl-river"><h2>River flow, past 90 days and next 30 <span class="aside">GloFAS · 51 runs</span></h2>
        <div class="row"><button class="btn ghost small" id="chronos" aria-pressed="false">Add Amazon Chronos forecast</button></div>
        <div class="chart"><canvas id="c-river"></canvas></div><p class="fine" id="river-note"></p></section>
      <section class="sec" id="pl-heat"><h2>Next 7 days of heat</h2><div id="pl-days"></div>
        <p class="fine">Big number: max feels-like. 🌙 warm night. % = share of ECMWF's 51 runs that see dangerous heat for this place.</p></section>
      <section class="sec"><h2>Alert for people here</h2>
        <div class="chips" id="personas"></div>
        <div class="row"><label class="toggle"><input type="checkbox" id="llm"> Write with the AI agent (Strands on Amazon Bedrock)</label></div>
        <div class="row"><button class="btn" id="mk-alert">Write the alert</button><button class="btn ghost" id="mk-bulletin">Daily bulletin → S3</button></div>
        <div id="alert-out"></div></section>`;
    renderPersonas();
    $("#mk-alert").addEventListener("click", makeAlert);
    $("#mk-bulletin").addEventListener("click", makeBulletin);
    let d;
    try { d = await api(`/api/place?lat=${p.lat}&lon=${p.lon}`); } catch (e) { $("#pl-risks").innerHTML = errBox(e); return; }
    if (S.place !== p || S.desk !== "pulse") return;
    p.data = d;
    renderRisks(d);
    renderRiver(d);
    renderDays(d.heat);
    $("#chronos").addEventListener("click", chronos);
  }

  function renderRisks(d) {
    const rows = [];
    const f = d.flood;
    if (f && !f.error) {
      const prob = Math.round(100 * (f.level_peak === "red" ? f.prob_red : f.prob_orange));
      const highNow = (f.level_now === "orange" || f.level_now === "red") && f.peak_date <= new Date().toISOString().slice(0, 10);
      const txt = highNow ? `Above its ${f.level_now === "red" ? "danger" : "warning"} line now at ${fmt(f.now_m3s)} m³/s and ${f.trend}.`
        : f.level_peak === "green" ? `Highest expected ${fmt(f.peak_m3s)} m³/s on ${niceDate(f.peak_date)}, under its warning line of ${fmt(f.thresholds.p95)}.`
        : `Expected to reach ${fmt(f.peak_m3s)} m³/s around ${niceDate(f.peak_date)}. ${prob}% of ${f.ensemble_runs} runs agree.`;
      rows.push(hz("River", f.level_peak, txt, `River ${f.trend} · main river cell ${f.river_cell.distance_km} km away`));
    } else rows.push(hz("River", "green", "No big modelled river near this spot.", "Street flooding is on the Flood desk."));
    const r = d.rain;
    if (r && !r.error) rows.push(hz("Rain", r.level, `${r.worst_day.rain_mm} mm on ${niceDate(r.worst_day.date)} (${r.worst_day.imd_category}, IMD scale).`, r.worst_day.max_mm_per_hour >= 10 ? `Up to ${r.worst_day.max_mm_per_hour} mm in one hour.` : ""));
    const h = d.heat;
    if (h && !h.error) {
      const w = h.worst_day;
      rows.push(hz("Heat", h.level, `Feels like ${w.feels_like_max}°C on ${niceDate(w.date)} (air ${w.tmax}°C${w.wetbulb_max ? `, wet-bulb ${w.wetbulb_max}°C` : ""}).`, "",
        h.official_rule_misses_it ? '<span class="flag">The official IMD heatwave rule would not warn about this</span>' : ""));
    }
    const g = d.google || {};
    const gl = `<a href="${g.link}" target="_blank" rel="noopener">Google Flood Hub</a>`;
    rows.push(hz("2nd view", g.gauge ? g.gauge.level : "green",
      g.gauge ? `${esc(g.gauge.severity)} at a Google gauge ${g.gauge.distance_km} km away. ${g.verdict ? esc(g.verdict) : ""}` : `Compare with ${gl} for this spot.`, ""));
    $("#pl-risks").innerHTML = `<div class="box">${rows.join("")}</div>`;
  }
  const hz = (k, lvl, d, d2, extra = "") => `<div class="hz"><div class="k">${k}</div><div>${stamp(lvl)}<div class="d">${d}</div>${d2 ? `<div class="d">${d2}</div>` : ""}${extra}</div></div>`;

  function renderRiver(d) {
    if (!d.chart) { $("#pl-river").hidden = true; return; }
    const { past, fc } = d.chart;
    const labels = [...past.t, ...fc.t.filter((t) => t > past.t[past.t.length - 1])];
    const pad = (arr, ts) => labels.map((l) => { const i = ts.indexOf(l); return i >= 0 ? arr[i] : null; });
    const th = d.flood.thresholds;
    const hline = (y, c, t) => ({ type: "line", yMin: y, yMax: y, borderColor: c, borderWidth: 1.5, borderDash: [5, 4],
      label: { display: true, content: t, position: "start", backgroundColor: C.card, color: c, font: { size: 10, weight: 700, family: css("--mono") } } });
    chart("river", $("#c-river"), {
      type: "line",
      data: { labels, datasets: [
        { label: "Past", data: pad(past.v, past.t), borderColor: C.ink, borderWidth: 1.8, pointRadius: 0, tension: .2 },
        { label: "90%", data: pad(fc.p90, fc.t), borderWidth: 0, pointRadius: 0, fill: "+1", backgroundColor: "rgba(31,97,112,.18)" },
        { label: "10%", data: pad(fc.p10, fc.t), borderWidth: 0, pointRadius: 0 },
        { label: "Forecast", data: pad(fc.p50, fc.t), borderColor: C.water, borderWidth: 2.4, borderDash: [6, 3], pointRadius: 0, tension: .2 },
      ] },
      options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        scales: { x: { ticks: { maxTicksLimit: 6, callback: (v) => niceDate(labels[v]) }, grid: { display: false } }, y: { ticks: { callback: (v) => fmt(v), maxTicksLimit: 5 }, grid } },
        plugins: { tooltip: { filter: (i) => i.raw != null && i.dataset.label !== "10%", callbacks: { title: (i) => longDate(labels[i[0].dataIndex]), label: (i) => `${i.dataset.label}: ${fmt(i.raw)} m³/s` } },
          annotation: { annotations: { w: hline(th.p95, C.warn, "warning"), d: hline(th.p99, C.bad, "danger"),
            today: { type: "line", xMin: labels.indexOf(fc.t[0]), xMax: labels.indexOf(fc.t[0]), borderColor: C.ink3, borderWidth: 1 } } } } },
    });
    $("#river-note").textContent = `Band: where 80% of ${d.flood.ensemble_runs} GloFAS runs land. Lines from ${d.flood.thresholds.years} years of this river's own flow.`;
  }

  async function chronos(e) {
    const b = e.currentTarget, ch = charts.river, d = S.place?.data;
    if (!ch || !d?.flood?.river_cell) return;
    if (b.getAttribute("aria-pressed") === "true") {
      ch.data.datasets = ch.data.datasets.filter((x) => !x.label.startsWith("Chronos")); ch.update();
      b.setAttribute("aria-pressed", "false"); b.textContent = "Add Amazon Chronos forecast"; return;
    }
    b.disabled = true; b.textContent = "Chronos is thinking…";
    try {
      const c = d.flood.river_cell;
      const r = await api(`/api/chronos?lat=${c.lat}&lon=${c.lon}`);
      const labels = ch.data.labels;
      r.t.forEach((t) => { if (!labels.includes(t)) labels.push(t); });
      ch.data.datasets.forEach((x) => { while (x.data.length < labels.length) x.data.push(null); });
      ch.data.datasets.push({ label: `Chronos (${r.by})`, data: labels.map((l) => { const i = r.t.indexOf(l); return i >= 0 ? r.p50[i] : null; }), borderColor: C.aws, borderWidth: 2.4, pointRadius: 0 });
      ch.update(); b.setAttribute("aria-pressed", "true"); b.textContent = `Chronos on · ${r.by}`;
    } catch (err) { toast("Chronos couldn't run: install the extras (python -m pip install -r requirements-extras.txt)."); b.textContent = "Add Amazon Chronos forecast"; }
    finally { b.disabled = false; }
  }

  function renderDays(h) {
    if (!h || h.error) { $("#pl-heat").hidden = true; return; }
    $("#pl-days").innerHTML = `<div class="days">${h.days.map((d) => {
      const ch = d.level === "red" ? d.chance_red : d.chance_orange;
      return `<div class="day ${d.level}" title="air ${d.tmax}°C (normal ${d.normal_tmax}), night ${d.tmin ?? "–"}°C${d.imd_label ? ", IMD: " + d.imd_label : ""}">
        <div class="dn">${dayName(d.date)}</div><div class="df">${Math.round(d.feels_like_max)}°</div>
        <div class="dx">${d.night ? "🌙" : ""}${ch ? ` ${Math.round(ch * 100)}%` : ""}</div>${d.imd_label ? `<span class="tag">IMD</span>` : ""}</div>`;
    }).join("")}</div>`;
  }

  function renderPersonas() {
    const m = S.meta; if (!m) return;
    S.persona ||= "family";
    $("#personas").innerHTML = Object.entries(m.personas).map(([k, v]) => `<button aria-pressed="${k === S.persona}" data-v="${k}">${esc(v)}</button>`).join("");
    $$("#personas button").forEach((b) => b.addEventListener("click", () => { S.persona = b.dataset.v; $$("#personas button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); }));
  }

  async function makeAlert() {
    const p = S.place, llm = $("#llm").checked, btn = $("#mk-alert");
    btn.disabled = true; btn.textContent = llm ? "The agent is checking river, rain, heat…" : "Writing…";
    try {
      const a = await api(`/api/alert?lat=${p.lat}&lon=${p.lon}&lang=${S.lang}&persona=${S.persona}&llm=${llm}`);
      const lvl = p.data?.level || "green";
      const text = (p.name ? `📍 ${p.name}\n` : "") + a.text;
      const by = a.by === "template" ? (llm ? "The AI agent wasn't reachable, so this is the built-in alert." : (S.lang !== "en" && S.lang !== "hi" && a.lang === "en" ? "Built-in alert (English). Add AWS keys and Amazon Bedrock writes it in your language." : "Built-in alert."))
        : a.by === "Amazon Bedrock" ? '<span class="awsmark">AWS</span>Translated by Amazon Bedrock.'
        : `<span class="awsmark">AWS</span>Written by the JalTaap agent (Strands Agents${a.model ? ", " + esc(a.model) : ""}) after checking live data.${a.tools?.length ? `<div class="tools">${a.tools.map((x) => `<span>${esc(x.replace(/_/g, " "))}</span>`).join("")}</div>` : ""}`;
      $("#alert-out").innerHTML = `<div class="alertbox ${lvl}" lang="${a.lang || S.lang}" id="alert-text">${esc(text)}</div>
        <div class="row"><button class="btn ghost small" id="a-speak">Read aloud</button>
          <a class="btn ghost small" style="display:inline-flex;align-items:center;text-decoration:none" target="_blank" rel="noopener" href="https://wa.me/?text=${encodeURIComponent(text + "\n\n— JalTaap")}">WhatsApp</a>
          <button class="btn ghost small" id="a-sms">Send SMS (Amazon SNS)</button><button class="btn ghost small" id="a-copy">Copy</button></div>
        <p class="by">${by}</p>`;
      markGuide("alert"); renderGuide();
      $("#a-speak").addEventListener("click", () => speak(text, a.lang || S.lang));
      $("#a-copy").addEventListener("click", () => navigator.clipboard.writeText(text).then(() => toast("Copied")));
      $("#a-sms").addEventListener("click", async () => {
        const phone = prompt("Phone number with country code (+91…). Leave empty to send to the SNS topic.") ?? null;
        if (phone === null) return;
        const r = await post("/api/dispatch", { text, phone: phone || null });
        toast(r.sent ? `Sent via ${r.via}` : "No AWS keys: saved to the local outbox (AWS desk).");
      });
    } catch (e) { $("#alert-out").innerHTML = errBox(e); }
    finally { btn.disabled = false; btn.textContent = "Write the alert"; }
  }

  async function makeBulletin() {
    const p = S.place, btn = $("#mk-bulletin");
    btn.disabled = true; btn.textContent = "Writing bulletin…";
    try {
      const b = await post("/api/bulletin", { q: "", lat: p.lat, lon: p.lon, name: p.name || "", lang: S.lang });
      $("#alert-out").innerHTML = `<div class="alertbox">${esc(b.text)}</div><p class="by">${b.by.startsWith("Amazon") ? '<span class="awsmark">AWS</span>' : ""}${esc(b.by)} · saved to ${b.saved.where === "s3" ? "Amazon S3" : "this computer"}: <a href="${b.saved.url}" target="_blank" rel="noopener">${esc(b.saved.key)}</a></p>`;
    } catch (e) { $("#alert-out").innerHTML = errBox(e); }
    finally { btn.disabled = false; btn.textContent = "Daily bulletin → S3"; }
  }

  // =====================================================================
  // FIELD REPORTS: citizens pin flooding, routes avoid it
  // =====================================================================
  S.depth = "knee";
  register("field", {
    title: "Seen a flooded street?", kicker: "Field reports", layers: ["reports", "route"], keepView: true,
    onMapClick(ll) { S.reportPt = { lat: ll.lat, lon: ll.lng }; pinLayer.clearLayers(); L.marker(ll, { icon: div("mk-pin", "", 18) }).addTo(pinLayer); updateReportPt(); return true; },
    render(el) {
      legend(`${sw(C.bad, "Reported flooding (merged reports)", 1)}${sw(C.ok, "Safe walking route")}`);
      if (S.place && !S.reportPt) S.reportPt = { lat: S.place.lat, lon: S.place.lon };
      el.innerHTML = `<p class="kicker">Field reports</p><h1 class="head">Seen a <em>flooded street</em>?</h1>
        <p class="lede">Models can't see a dam release, a blocked drain or a broken embankment. People can. Tap the spot on the map. Many reports of one spot merge into one incident.</p>
        <section class="sec"><h2>How deep is the water?</h2>
          <div class="chips" id="depth">${["ankle", "knee", "waist", "above waist"].map((d) => `<button aria-pressed="${d === S.depth}" data-v="${d}">${d[0].toUpperCase() + d.slice(1)}</button>`).join("")}</div>
          <div class="grid2"><label class="field">Note (optional)<input id="r-note" maxlength="140" placeholder="e.g. underpass full, bus stuck"></label>
            <label class="field">Photo (optional)<input id="r-photo" type="file" accept="image/*" capture="environment"></label></div>
          <p class="fine" id="r-pt">Tap the flooded spot on the map.</p>
          <div class="row"><button class="btn" id="r-send" disabled>Report flooding here</button></div><div id="r-out"></div></section>
        <section class="sec"><h2>Get out safely</h2>
          <p class="sub">Walking route from the pin to the nearest school, hospital or relief centre, around every reported flooded street.</p>
          <div class="row"><button class="btn" id="go-flood" disabled>Route to a relief spot</button><button class="btn ghost" id="go-heat" disabled>Route to a cool spot</button></div>
          <p class="fine" id="route-out"></p></section>
        <section class="sec"><h2>Live incidents (48 h)</h2><ul class="ledger" id="incidents"></ul></section>`;
      $$("#depth button").forEach((b) => b.addEventListener("click", () => { S.depth = b.dataset.v; $$("#depth button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); }));
      $("#r-send").addEventListener("click", sendReport);
      $("#go-flood").addEventListener("click", () => route("flood"));
      $("#go-heat").addEventListener("click", () => route("heat"));
      updateReportPt(); loadReports();
    },
  });
  function updateReportPt() {
    if (!S.reportPt || S.desk !== "field") return;
    $("#r-pt").textContent = `Pinned at ${S.reportPt.lat.toFixed(4)}, ${S.reportPt.lon.toFixed(4)}.`;
    ["#r-send", "#go-flood", "#go-heat"].forEach((s) => ($(s).disabled = false));
    if (map.getZoom() < 13) map.flyTo([S.reportPt.lat, S.reportPt.lon], 14, { duration: reduceMotion ? 0 : 0.8 });
  }
  async function loadReports() { try { drawIncidents((await api("/api/reports")).incidents); } catch (_) { /* ignore */ } }
  function drawIncidents(incs) {
    layer("reports").clearLayers();
    incs.forEach((i) => L.circle([i.lat, i.lon], { radius: 150, color: C.bad, weight: 2, dashArray: "4 3", fillColor: C.bad, fillOpacity: .22 })
      .bindTooltip(`<b>${i.reports} report${i.reports > 1 ? "s" : ""}</b><br>worst: ${esc(i.worst_depth)} deep<br>${i.last_report_min_ago} min ago`, { className: "tt" }).addTo(layer("reports")));
    if (S.desk !== "field") return;
    $("#incidents").innerHTML = incs.length ? incs.map((i) => `<li data-lat="${i.lat}" data-lon="${i.lon}"><span class="sw red"></span>
        <div><div class="t">${esc(i.worst_depth[0].toUpperCase() + i.worst_depth.slice(1))}-deep water</div><div class="s">${i.lat.toFixed(4)}, ${i.lon.toFixed(4)} · ${i.last_report_min_ago} min ago · ${i.confidence} confidence</div></div>
        <div class="v">${i.reports}<small>report${i.reports > 1 ? "s" : ""}</small></div></li>`).join("") : '<p class="empty">No flooding reported in the last 48 hours.</p>';
    $$("#incidents li").forEach((li) => li.addEventListener("click", () => map.flyTo([+li.dataset.lat, +li.dataset.lon], 15)));
  }
  async function sendReport() {
    const f = $("#r-photo").files[0], btn = $("#r-send");
    btn.disabled = true;
    try {
      let r;
      const base = { lat: S.reportPt.lat, lon: S.reportPt.lon, depth: S.depth, note: $("#r-note").value };
      if (f) {
        const b64 = await new Promise((ok, bad) => { const fr = new FileReader(); fr.onload = () => ok(fr.result); fr.onerror = bad; fr.readAsDataURL(f); });
        r = await post("/api/reports/photo", { ...base, image_b64: b64 });
        $("#r-out").innerHTML = `<p class="by">Photo saved in ${r.photo.where === "s3" ? "Amazon S3" : "data/files"}.</p>`;
      } else r = await post("/api/reports", base);
      drawIncidents(r.incidents);
      toast("Reported. Routes for everyone nearby now avoid this spot.");
    } catch (e) { toast(`Couldn't send: ${e.message}`); }
    finally { btn.disabled = false; }
  }
  async function route(mode) {
    $("#route-out").textContent = "Loading streets from OpenStreetMap…";
    layer("route").clearLayers();
    try {
      const r = await api(`/api/route?lat=${S.reportPt.lat}&lon=${S.reportPt.lon}&mode=${mode}`);
      if (!r.ok) { $("#route-out").textContent = r.reason; return; }
      L.polyline(r.path, { color: C.paper, weight: 9 }).addTo(layer("route"));
      L.polyline(r.path, { color: C.ok, weight: 5 }).addTo(layer("route"));
      L.circleMarker([r.destination.lat, r.destination.lon], { radius: 9, color: C.ink, weight: 2, fillColor: C.ok, fillOpacity: 1 })
        .bindTooltip(esc(r.destination.name), { className: "tt", permanent: true, direction: "top", offset: [0, -8] }).addTo(layer("route"));
      map.fitBounds(L.polyline(r.path).getBounds(), { padding: [60, 60] });
      $("#route-out").innerHTML = `Walk to <b>${esc(r.destination.name)}</b>: ${fmt(r.distance_m)} m, about ${r.walk_min} min.${r.blocked_nodes ? ` Avoids ${r.blocked_nodes} flooded road points.` : ""}`;
    } catch (e) { $("#route-out").textContent = e.message; }
  }

  // =====================================================================
  // REPLAY: real floods, day by day
  // =====================================================================
  let replayData, playT, curEvent;
  register("replay", {
    title: "Would it have warned them?", kicker: "Replay", layers: ["replay"], keepView: true,
    async render(el) {
      el.innerHTML = `<p class="kicker">Replay · honest backtest</p><h1 class="head">Would it have <em>warned them</em>?</h1>
        <p class="lede">Real floods, replayed day by day. The model only sees river data up to each day, and danger lines only use years before the flood.</p>
        <div class="events" id="events">${loading("Loading past floods…")}</div>
        <section class="sec" id="player" hidden><h2>Play it back</h2>
          <div class="ticker"><span id="tick-date">—</span><span id="tick-flow"></span></div>
          <div class="chart tall"><canvas id="c-replay"></canvas></div>
          <div class="row"><button class="btn" id="play">Play</button><input type="range" id="scrub" min="0" max="0" value="0" style="flex:1;accent-color:#201D18" aria-label="Day"></div>
          <ol class="log" id="log"></ol></section>
        <section class="sec"><h2>Not cherry-picked: every monsoon day 2015–2024</h2>
          <p class="sub">When JalTaap says <b>warning</b>, how often was the river really above its warning line within 10 days?</p><div class="stats" id="score"></div></section>`;
      if (!replayData) {
        try { replayData = await api("/api/replay"); } catch (e) { $("#events").innerHTML = `<p class="empty">No replays yet. Run <code>python -m jaltaap.replay</code> once.</p>`; return; }
      }
      $("#events").innerHTML = replayData.map((r, i) => `<button class="event" data-i="${i}" aria-pressed="false">
        <div><div class="t">${esc(r.name.replace(/\s*\(.*\)/, ""))}</div><div class="s">${longDate(r.peak_date)} · ${esc(r.note || "")}</div></div>
        <div class="v">${r.honest_miss ? "Miss" : r.days_warning_before_peak + "d"}<small>${r.honest_miss ? "see why" : "warning"}</small></div></button>`).join("");
      $$(".event").forEach((b) => b.addEventListener("click", () => startEvent(+b.dataset.i)));
      $("#scrub").addEventListener("input", (e) => { clearInterval(playT); $("#play").textContent = "Play"; replayDay(+e.target.value); });
      $("#play").addEventListener("click", play);
      loadScore();
      const ai = replayData.findIndex((r) => r.id === "assam2022");
      startEvent(curEvent ?? (ai >= 0 ? ai : 0));
    },
  });
  function startEvent(i) {
    clearInterval(playT);
    curEvent = i;
    const r = replayData[i];
    $$(".event").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.i === i)));
    $("#player").hidden = false; $("#play").textContent = "Play";
    const days = Object.keys(r.series);
    $("#scrub").max = days.length - 1; $("#scrub").value = 0;
    map.flyTo([r.lat, r.lon], 9, { duration: reduceMotion ? 0 : 1 });
    const th = r.thresholds;
    const hl = (y, c, t) => ({ type: "line", yMin: y, yMax: y, borderColor: c, borderDash: [5, 4], borderWidth: 1.5, label: { display: true, content: t, position: "start", backgroundColor: C.card, color: c, font: { size: 10, weight: 700 } } });
    chart("replay", $("#c-replay"), {
      type: "line", data: { labels: days, datasets: [{ data: days.map(() => null), borderColor: C.ink, borderWidth: 2.4, pointRadius: 0, tension: .2 }] },
      options: { maintainAspectRatio: false, animation: false,
        scales: { x: { ticks: { maxTicksLimit: 6, callback: (v) => niceDate(days[v]) }, grid: { display: false } },
          y: { suggestedMax: Math.max(...Object.values(r.series), th.p99) * 1.08, suggestedMin: 0, ticks: { callback: (v) => fmt(v), maxTicksLimit: 5 }, grid } },
        plugins: { annotation: { annotations: { w: hl(th.p95, C.warn, "warning"), d: hl(th.p99, C.bad, "danger") } }, tooltip: { enabled: false } } },
    });
    replayDay(0);
  }
  function replayDay(step) {
    const r = replayData[curEvent], ch = charts.replay;
    const days = Object.keys(r.series), vals = Object.values(r.series), today = days[step], th = r.thresholds, v = vals[step];
    ch.data.datasets[0].data = vals.map((x, i) => (i <= step ? x : null));
    const an = ch.options.plugins.annotation.annotations;
    delete an.box; delete an.peak;
    if (r.first_alert_date && today >= r.first_alert_date && !r.honest_miss) {
      const a = days.indexOf(r.first_alert_date), pk = days.indexOf(r.peak_date);
      an.box = { type: "box", xMin: a, xMax: Math.max(a, Math.min(step, pk)), backgroundColor: "rgba(196,133,8,.16)", borderWidth: 0 };
    }
    if (today >= r.peak_date) { const pk = days.indexOf(r.peak_date); an.peak = { type: "line", xMin: pk, xMax: pk, borderColor: C.bad, borderWidth: 1 }; }
    ch.update();
    const lvl = v >= th.p99 ? "red" : v >= th.p95 ? "orange" : "green";
    $("#tick-date").textContent = longDate(today); $("#tick-flow").textContent = `${fmt(v)} m³/s`;
    let msg;
    if (r.honest_miss && today >= r.peak_date) msg = "Missed: barrage releases upstream. River models can't see them; citizen reports can.";
    else if (today >= r.peak_date && r.days_warning_before_peak) msg = `Peak day. JalTaap had warned <b>${r.days_warning_before_peak} days</b> earlier.`;
    else if (!r.honest_miss && r.first_alert_date && today >= r.first_alert_date) msg = `<b>Warning sent</b> ${niceDate(r.first_alert_date)}. Move grain, papers, medicine and elderly people up.`;
    else msg = "River looks normal. No warning yet.";
    mapcard(`<div class="r">${esc(r.name.replace(/\s*\(.*\)/, ""))}</div><div class="d">${longDate(today)} · ${fmt(v)} m³/s</div><div style="margin-top:6px">${stamp(lvl)}</div><div class="m">${msg}</div>`);
    layer("replay").clearLayers();
    const ring = lvl === "green" ? 24 : lvl === "orange" ? 50 : 70;
    L.marker([r.lat, r.lon], { icon: L.divIcon({ className: "", iconSize: [ring, ring], iconAnchor: [ring / 2, ring / 2], html: `<div class="mk-river ${lvl}" style="width:${ring}px;height:${ring}px;position:relative">${lvl !== "green" ? '<div class="halo"></div>' : ""}<div class="core"></div></div>` }) }).addTo(layer("replay"));
    const log = [];
    const fo = days.find((d) => r.series[d] >= th.p95), fr = days.find((d) => r.series[d] >= th.p99);
    if (r.first_alert_date && today >= r.first_alert_date && !r.honest_miss) log.push(`<li class="alert"><b>${niceDate(r.first_alert_date)}</b> JalTaap warning: river expected above its warning line within 10 days</li>`);
    if (fo && today >= fo) log.push(`<li><b>${niceDate(fo)}</b> crosses warning line (${fmt(th.p95)} m³/s)</li>`);
    if (fr && today >= fr) log.push(`<li><b>${niceDate(fr)}</b> crosses danger line (${fmt(th.p99)} m³/s)</li>`);
    if (today >= r.peak_date) {
      log.push(`<li class="peak"><b>${niceDate(r.peak_date)}</b> peak ${fmt(r.peak_m3s)} m³/s, ${r.peak_vs_p99}× the danger line</li>`);
      if (r.honest_miss) log.push(`<li>${esc(r.note)}</li>`);
    }
    $("#log").innerHTML = log.join("") || `<li>${niceDate(days[0])}: river normal. Press play.</li>`;
    $("#scrub").value = step;
  }
  function play() {
    if ($("#play").textContent === "Pause") { clearInterval(playT); $("#play").textContent = "Play"; return; }
    let s = +$("#scrub").value; const max = +$("#scrub").max;
    if (s >= max) s = 0;
    $("#play").textContent = "Pause";
    playT = setInterval(() => { if (S.desk !== "replay") return clearInterval(playT); replayDay(s); if (++s > max) { clearInterval(playT); $("#play").textContent = "Replay"; } }, reduceMotion ? 120 : 360);
  }
  async function loadScore() {
    try {
      const sc = await api("/api/scorecard");
      const names = { kerala2018: "Periyar, Kerala", assam2022: "Brahmaputra, Assam", godavari2022: "Godavari, Telangana", yamuna2023: "Yamuna, Delhi" };
      $("#score").innerHTML = Object.keys(sc).map((k) => {
        const w = Object.entries(sc[k]).find(([n]) => n.startsWith("warning"))?.[1];
        return w ? `<div><div class="v">${w.precision_pct}%</div><div class="l">${names[k] || k}: right when it warned · caught ${w.recall_pct}% of high-water days</div></div>` : "";
      }).join("") || '<p class="empty" style="padding:10px">Run <code>python -m jaltaap.evaluate</code>.</p>';
    } catch (_) { /* optional */ }
  }

  // =====================================================================
  // AWS: which services are live, and the outbox
  // =====================================================================
  register("aws", {
    title: "Built on AWS", kicker: "AWS stack", layers: [], keepView: true,
    async render(el) {
      el.innerHTML = `<p class="kicker">AWS stack</p><h1 class="head">What runs <em>on AWS</em>, and what falls back</h1>
        <p class="lede">Every AWS service below is called for real when keys are present (aws configure or .env). Without them each one falls back to a free local path, so the app never breaks.</p><div id="aws-body">${loading("Checking services…")}</div>`;
      try { S.aws = await api("/api/aws/status"); } catch (e) { $("#aws-body").innerHTML = errBox(e); return; }
      const a = S.aws;
      const out = await api("/api/aws/outbox").catch(() => []);
      $("#aws-body").innerHTML = `
        <div class="stats"><div><div class="v ${a.live >= 4 ? "green" : "orange"}">${a.live}<small>/${a.total}</small></div><div class="l">services live now</div></div>
          <div><div class="v" style="font-size:16px">${esc(a.region)}</div><div class="l">region</div></div>
          <div><div class="v" style="font-size:13px;word-break:break-all">${esc(a.model)}</div><div class="l">Bedrock model</div></div></div>
        ${a.credentials ? "" : '<div class="err">No AWS credentials found. Run <b>aws configure</b> (or put AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in .env) and restart <code>python server.py</code>.</div>'}
        <section class="sec"><h2>Services</h2><table class="t"><thead><tr><th>Service</th><th>Used for</th><th>State</th></tr></thead><tbody>
          ${a.services.map((s) => `<tr><td><b>${esc(s.name)}</b>${s.calls ? `<div class="fine">${s.calls} calls</div>` : ""}</td><td>${esc(s.use)}${s.state !== "live" ? `<div class="fine">Fallback: ${esc(s.fallback)}</div>` : ""}${s.error ? `<div class="fine t-red">${esc(s.error)}</div>` : ""}</td><td><span class="pill ${s.state}">${s.state}</span></td></tr>`).join("")}
        </tbody></table><p class="fine">Records store: <b>${a.store === "dynamodb" ? "Amazon DynamoDB" : "SQLite on this computer"}</b>. Switch services on in .env: BEDROCK_MODEL_ID, JALTAAP_S3_BUCKET, JALTAAP_DDB_TABLE, JALTAAP_SNS_TOPIC_ARN, JALTAAP_PLACE_INDEX, JALTAAP_ROUTE_CALCULATOR, JALTAAP_MAPS_API_KEY.</p></section>
        <section class="sec"><h2>How the pieces fit</h2>
          <ul class="plain"><li><b>Sensors</b> (drain level, pipe pressure, well depth) → this API → DynamoDB.</li>
            <li><b>Every 6 h</b> a scheduled Lambda checks watch points and sends SNS alerts when a place turns orange or red; API Gateway serves the deployed API.</li>
            <li><b>Bedrock</b> powers the copilot, bulletins and the Strands agent, and writes alerts in 12 Indian languages.</li>
            <li><b>S3</b> keeps photos and bulletins, and the Drought desk reads Sentinel-2 scenes from AWS Open Data; <b>Location Service</b> searches places, routes tankers on real roads and adds a satellite map.</li></ul>
          <p class="fine">Deploy: <code>python deploy/build.py</code> then <code>sam deploy --guided</code> (deploy/template.yaml creates the tables, bucket, topic, place index and route calculator).</p></section>
        <section class="sec"><h2>Message outbox <span class="aside">SMS / OTP / alerts</span></h2>
          ${out.length ? `<table class="t"><tbody>${out.map((m) => `<tr><td class="num" style="white-space:nowrap">${new Date(m.ts * 1000).toLocaleTimeString("en-IN")}</td><td>${esc(m.to)}</td><td>${esc(m.text)}</td></tr>`).join("")}</tbody></table>` : '<p class="empty">Nothing sent yet. When SNS is live, messages go out as real SMS instead of landing here.</p>'}</section>`;
    },
  });
  async function refreshAwsPill() {
    try {
      const a = await api("/api/aws/status");
      S.aws = a;
      $("#awspill span").textContent = `AWS ${a.live}/${a.total}`;
      $("#awspill").classList.toggle("on", a.credentials);
    } catch (_) { /* ignore */ }
  }
  $("#awspill").addEventListener("click", () => setDesk("aws"));
  $("#lang").addEventListener("change", (e) => (S.lang = e.target.value));

  // ---------- shared with desks.js ----------
  window.JT = { $, $$, api, post, fmt, esc, C, LV, WORD, S, map, layer, div, legend, maptools, mapcard, sw, chart, charts, grid,
    register, setDesk, selectPlace, placeLine, loading, errBox, stamp, bars, meter, niceDate, dayName, longDate, hr12, toast, css, speak, pinLayer, reduceMotion };

  // ---------- go ----------
  async function boot() {
    try {
      S.meta = await api("/api/meta");
      $("#lang").innerHTML = Object.entries(S.meta.langs).map(([k, v]) => `<option value="${k}" ${k === S.lang ? "selected" : ""}>${v}</option>`).join("");
    } catch (_) { /* server will say more on each desk */ }
    const qs = new URLSearchParams(location.search);
    document.dispatchEvent(new Event("jt:ready"));
    if (qs.get("lat") && qs.get("lon")) {
      S.desk = desks[qs.get("desk")] ? qs.get("desk") : "pulse";
      selectPlace(+qs.get("lat"), +qs.get("lon"), qs.get("name") || "", "");
    } else setDesk(qs.get("desk") || "pulse");
    loadBoard();
    refreshAwsPill();
  }
  // desks.js registers itself on jt:core, then we boot
  document.addEventListener("DOMContentLoaded", () => {});
  window.addEventListener("load", () => { document.dispatchEvent(new Event("jt:core")); boot(); });
})();
