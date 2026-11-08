// The map (spec §10.3): real streets from the bundled OpenStreetMap extract when the scenario
// has a geographic origin, a plain ground otherwise. Nodes, contacts and moving message pieces
// are drawn on an SVG overlay in screen pixels, so they look the same at any zoom.
/* global maplibregl, pmtiles, basemaps */
import { t as tr } from "/static/i18n.js";
const NS = "http://www.w3.org/2000/svg";
const M_LAT = 110540, M_LON = 111320;
const FLIGHT_MS = 1400;  // how long a moving piece takes to cross a contact on screen

export class NetMap {
  constructor(container, overlay) {
    this.container = container; this.svg = overlay;
    this.map = null; this.t = null; this.flights = []; this.seen = new Set(); this.selected = null;
    this.reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  draw(t) {
    // glide from where each node is drawn now to its new position over the next update interval
    this.from = Object.fromEntries((this.t?.nodes ?? []).map(n => [n.id, this.at(n.id)]));
    this.arrived = performance.now();
    this.gap = this.t ? Math.min(1500, this.arrived - (this.lastDraw ?? this.arrived)) || 500 : 0;
    this.lastDraw = this.arrived;
    this.t = t;
    if (!this.map) this.init(t);
    for (const tr of t.transfers) {  // each new custody receipt starts a flight
      const key = `${tr.t}|${tr.from}|${tr.to}`;
      if (this.seen.has(key)) continue;
      this.seen.add(key);
      if (!this.reduceMotion) this.flights.push({ ...tr, start: performance.now() });
    }
    this.render();
  }

  select(journey) { this.selected = journey; this.render(); }

  init(t) {
    const geo = t.geo;
    const protocol = new pmtiles.Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);
    this.lnglat = ([x, y]) => {
      const [lat0, lon0] = geo ?? [0, 0];
      return [lon0 + x / (M_LON * Math.cos(lat0 * Math.PI / 180)), lat0 + y / M_LAT];
    };
    this.map = new maplibregl.Map({
      container: this.container, style: style(geo), attributionControl: false,
      dragRotate: false, pitchWithRotate: false, touchPitch: false,
    });
    this.map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-left");
    const pts = t.nodes.flatMap(n => [[n.x, n.y], ...(n.route ?? [])]).map(this.lnglat);
    const lons = pts.map(p => p[0]), lats = pts.map(p => p[1]);
    this.map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
      { padding: { top: 80, bottom: 60, left: 70, right: 150 }, animate: false });  // labels sit right
    this.map.on("move", () => this.render());
    this.map.on("resize", () => this.render());
    const tick = () => {
      if (this.flights.length || performance.now() - this.arrived < this.gap) this.render();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  at(id) {  // map position of a node right now, part-way between its last two reported positions
    const n = this.t.nodes.find(m => m.id === id), from = this.from?.[id];
    if (!from || this.reduceMotion || !this.gap) return [n.x, n.y];
    const k = Math.min(1, (performance.now() - this.arrived) / this.gap);
    if (Math.hypot(n.x - from[0], n.y - from[1]) > 3000) return [n.x, n.y];  // looped round: jump
    return [from[0] + (n.x - from[0]) * k, from[1] + (n.y - from[1]) * k];
  }

  px(xy) {
    const p = this.map.project(this.lnglat(xy));
    return [p.x, p.y];
  }

  render() {
    const t = this.t;
    if (!t || !this.map) return;
    const pos = Object.fromEntries(t.nodes.map(n => [n.id, this.px(this.at(n.id))]));
    const out = [];
    for (const n of t.nodes) if (n.route) {  // the road each vehicle drives
      const pts = [...n.route, n.route[0]].map(p => this.px(p).join(",")).join(" ");
      out.push(el("polyline", { points: pts, class: "road" }));
    }
    for (const id of t.lora) out.push(el("circle", { cx: pos[id][0], cy: pos[id][1], r: 26, class: "lora" }));
    for (const [a, b] of t.links) {
      out.push(el("line", { x1: pos[a][0], y1: pos[a][1], x2: pos[b][0], y2: pos[b][1], class: "link" }));
    }
    const places = t.nodes.filter(n => n.role === "hub" || n.role === "site");
    // vehicles parked on the same spot share one label instead of stacking
    const groupOf = {}, clusters = [];
    for (const n of t.nodes) {
      if (n.role !== "mule" || !n.up || n.hostile) continue;
      const near = clusters.find(c => Math.hypot(pos[c[0].id][0] - pos[n.id][0], pos[c[0].id][1] - pos[n.id][1]) < 16);
      if (near) near.push(n); else clusters.push([n]);
      groupOf[n.id] = near ?? clusters.at(-1);
    }
    const label = n => {
      const g = groupOf[n.id];
      if (!g || g.length < 2) return undefined;
      const total = g.reduce((s, m) => s + m.shards, 0);
      if (g[0] !== n) return "";
      return total ? tr("map.vehicles_carrying", { n: g.length, total }) : tr("map.vehicles", { n: g.length });
    };
    for (const n of [...t.nodes.filter(n => !places.includes(n)), ...places]) out.push(this.node(n, pos[n.id], label(n)));
    out.push(...this.selection(pos), ...this.pieces(pos));
    this.svg.replaceChildren(...out);
  }

  pieces(pos) {
    const now = performance.now(), out = [];
    this.flights = this.flights.filter(f => now - f.start < FLIGHT_MS && pos[f.from] && pos[f.to]);
    for (const f of this.flights) {
      const k = (now - f.start) / FLIGHT_MS, e = k < .5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
      const [x1, y1] = pos[f.from], [x2, y2] = pos[f.to];
      const mine = this.selected && f.bundles.includes(this.selected.id);
      for (let i = 0; i < Math.min(f.pieces, 6); i++) {  // a short train of dots, one per piece
        const q = Math.max(0, Math.min(1, e - i * 0.05));
        out.push(el("circle", { cx: x1 + (x2 - x1) * q, cy: y1 + (y2 - y1) * q, r: 3.2, class: mine ? "piece mine" : "piece" }));
      }
    }
    return out;
  }

  selection(pos) {
    const j = this.selected;
    if (!j) return [];
    const out = [];
    for (const [id, count] of Object.entries(j.holders)) {
      if (!pos[id]) continue;
      const [x, y] = pos[id];
      out.push(el("circle", { cx: x, cy: y, r: 15, class: "holder" }));
      const label = el("text", { x: x + 18, y: y - 12, class: "holder-label" });
      label.textContent = id === j.to_node ? tr("map.needed", { n: count, k: j.k }) : tr("map.holds", { n: count });
      out.push(label);
    }
    return out;
  }

  node(n, [x, y], groupLabel) {
    const g = document.createElementNS(NS, "g");
    const place = n.role === "hub" || n.role === "site";
    let shape;
    if (place) {
      const r = n.role === "hub" ? 8 : 6;
      shape = el("rect", { x: x - r, y: y - r, width: 2 * r, height: 2 * r, rx: 1.5, class: n.up ? "place" : "off" });
    } else if (n.role === "relay") {
      shape = el("polygon", { points: `${x},${y - 6} ${x + 6},${y} ${x},${y + 6} ${x - 6},${y}`, class: n.up ? "relay" : "off" });
    } else {
      shape = el("circle", { cx: x, cy: y, r: 5.5, class: !n.up ? "off" : n.hostile ? "hostile" : "carrier" });
    }
    const title = document.createElementNS(NS, "title");
    title.textContent = [pretty(n.id) + (n.hostile ? ` ${tr("map.attacker")}` : ""),
      n.up ? null : tr("map.powered_off"), n.shards ? tr("map.holding", { n: n.shards }) : null,
      n.battery_pct == null ? null : tr("map.battery", { pct: Math.round(n.battery_pct) })].filter(Boolean).join(", ");
    g.append(shape, title);
    // places label to the right, carriers to the left, so a van parked at a clinic stays readable
    const label = (text, cls, dy) => {
      const tx = el("text", place ? { x: x + 11, y: y + dy, class: cls }
        : { x: x - 10, y: y + dy, class: cls, "text-anchor": "end" });
      tx.textContent = text;
      g.append(tx);
    };
    if (place) label(pretty(n.id), n.role === "hub" ? "name hub" : "name", 4.5);
    else if (n.hostile && n.up) label(pretty(n.id), "minor", 4.5);
    if (groupLabel !== undefined) { if (groupLabel) label(groupLabel, "load", 4.5); }
    else if (n.shards) label(tr(place ? "map.waiting" : "map.carrying", { n: n.shards }), "load", place ? 20 : 4.5);
    if (!n.up) label(tr("map.off"), "minor", place ? 20 : 4.5);
    return g;
  }
}

function style(geo) {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const s = { version: 8, glyphs: `${location.origin}/static/map/fonts/{fontstack}/{range}.pbf`, sources: {}, layers: [] };
  const ground = getComputedStyle(document.documentElement).getPropertyValue("--paper").trim();
  if (!geo) {
    s.layers = [{ id: "ground", type: "background", paint: { "background-color": ground } }];
    return s;
  }
  s.sources.osm = { type: "vector", url: `pmtiles://${location.origin}/static/map/arusha.pmtiles`,
    attribution: '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>' };
  const ward = dark
    ? { background: ground, earth: ground, water: "#1f3a3f", park_a: "#1c302c", park_b: "#1c302c", wood_a: "#1c302c",
        wood_b: "#1c302c", scrub_a: "#1a2c29", scrub_b: "#1a2c29", buildings: "#22332f", hospital: "#22332f",
        school: "#1d2c29", industrial: "#1d2c29", major: "#3a4f4b", minor_a: "#2c3e3a", minor_b: "#2c3e3a",
        highway: "#4a605b", other: "#283a36", city_label: "#c4d3d0", subplace_label: "#8fa29e",
        roads_label_major: "#8fa29e", roads_label_minor: "#7a8d89" }
    : { background: ground, earth: ground, water: "#c6dde0", park_a: "#dde8e2", park_b: "#dde8e2", wood_a: "#d8e5de",
        wood_b: "#d8e5de", scrub_a: "#e1e9e5", scrub_b: "#e1e9e5", buildings: "#e1e7e5", hospital: "#e4ebe9",
        school: "#e6ecea", industrial: "#e6ecea", major: "#ffffff", minor_a: "#fbfcfc", minor_b: "#fbfcfc",
        highway: "#ffffff", other: "#f6f8f8", city_label: "#4d5d5a", subplace_label: "#7c8b88",
        roads_label_major: "#8a9895", roads_label_minor: "#9aa7a4" };
  const flavor = { ...basemaps.namedFlavor(dark ? "dark" : "light"), ...ward };
  s.layers = basemaps.layers("osm", flavor, { lang: "en" }).filter(l => !l.layout?.["icon-image"]);
  return s;
}

export function pretty(id) {
  return id.toLowerCase().split(/[_-]/).map(w => w[0].toUpperCase() + w.slice(1)).join(" ");
}

function el(tag, attrs) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
