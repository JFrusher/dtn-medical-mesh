// Schematic map (spec §10.3): roads, places and the vehicles carrying messages. No basemap,
// so it works with no internet. Sizes are SVG attributes scaled to the view (CSS would pin them).
const NS = "http://www.w3.org/2000/svg";

export class NetMap {
  constructor(svg) { this.svg = svg; this.box = null; }

  draw(t) {
    if (!this.box) this.frame(t.nodes);
    // one on-screen pixel in map units, so text and strokes keep their size at any width
    const u = Math.max(this.box[2] / (this.svg.clientWidth || 900), this.box[3] / (this.svg.clientHeight || 600));
    const pos = Object.fromEntries(t.nodes.map(n => [n.id, [n.x, -n.y]]));
    const out = [];
    for (const n of t.nodes) if (n.route) {  // the roads the vehicles drive
      const pts = [...n.route, n.route[0]].map(([x, y]) => `${x},${-y}`).join(" ");
      out.push(el("polyline", { points: pts, class: "road", "stroke-width": 5 * u }));
    }
    for (const id of t.lora) {
      const [cx, cy] = pos[id];
      out.push(el("circle", { cx, cy, r: 26 * u, class: "lora", "stroke-width": 1.5 * u }));
    }
    for (const [a, b] of t.links) {
      const [x1, y1] = pos[a], [x2, y2] = pos[b];
      out.push(el("line", { x1, y1, x2, y2, class: "link", "stroke-width": 3 * u }));
    }
    const places = t.nodes.filter(n => n.role === "hub" || n.role === "site");
    const rest = t.nodes.filter(n => !places.includes(n));
    for (const n of [...rest, ...places]) out.push(this.node(n, pos[n.id], u));
    this.svg.replaceChildren(...out);
  }

  frame(nodes) {
    const pts = nodes.flatMap(n => [[n.x, n.y], ...(n.route ?? [])]);
    const xs = pts.map(p => p[0]), ys = pts.map(p => -p[1]);
    const w = Math.max(...xs) - Math.min(...xs) || 1, h = Math.max(...ys) - Math.min(...ys) || 1;
    const pad = Math.max(w, h) * 0.12;
    // labels sit to the right of their node, so leave extra room on that side
    this.box = [Math.min(...xs) - pad, Math.min(...ys) - pad, w + 3 * pad, h + 2 * pad];
    this.svg.setAttribute("viewBox", this.box.join(" "));
    this.svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  }

  node(n, [x, y], u) {
    const g = document.createElementNS(NS, "g");
    const place = n.role === "hub" || n.role === "site";
    let shape;
    if (place) {
      const r = (n.role === "hub" ? 9 : 6.5) * u;
      shape = el("rect", { x: x - r, y: y - r, width: 2 * r, height: 2 * r, rx: 1.5 * u, class: n.up ? "place" : "off" });
    } else if (n.role === "relay") {
      const r = 5.5 * u;
      shape = el("polygon", { points: `${x},${y - r} ${x + r},${y} ${x},${y + r} ${x - r},${y}`,
        class: n.up ? "relay" : "off", "stroke-width": 1.5 * u });
    } else {
      shape = el("circle", { cx: x, cy: y, r: 5.5 * u, class: n.up ? "carrier" : "off" });
    }
    const title = document.createElementNS(NS, "title");
    title.textContent = `${pretty(n.id)}${n.up ? "" : ", powered off"}`
      + (n.shards ? `, carrying ${n.shards} message pieces` : "")
      + (n.battery_pct == null ? "" : `, battery ${Math.round(n.battery_pct)}%`);
    g.append(shape, title);
    const label = (text, cls, dy, size, weight) => {
      const tx = el("text", { x: x + 11 * u, y: y + dy * u, class: cls, "font-size": size * u, "stroke-width": 3 * u });
      if (weight) tx.setAttribute("font-weight", weight);
      tx.textContent = text;
      g.append(tx);
    };
    // places are always named; carriers and relays stay quiet unless they hold messages or are down
    if (place) label(pretty(n.id), "name", 4.5, n.role === "hub" ? 15 : 13, 600);
    if (n.shards) label(place ? `${n.shards} waiting` : `carrying ${n.shards}`, "load", place ? 21 : 4.5, 12);
    if (!n.up) label("off", "minor", place ? 21 : 4.5, 12);
    return g;
  }
}

export function pretty(id) {
  const s = id.toLowerCase().replaceAll(/[_-]/g, " ");
  return s[0].toUpperCase() + s.slice(1);
}

function el(tag, attrs) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
