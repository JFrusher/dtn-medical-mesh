// Schematic network map (spec §10.3): no basemap, so it works with no internet at all.
const NS = "http://www.w3.org/2000/svg";
const ROLE_SHAPE = { hub: "rect", site: "rect", mule: "circle", relay: "polygon" };

export class NetMap {
  constructor(svg, log, stats) { this.svg = svg; this.log = log; this.stats = stats; this.box = null; }

  draw(t) {
    if (!this.box) {  // fix the frame from the first snapshot so the view doesn't jump
      const xs = t.nodes.map(n => n.x), ys = t.nodes.map(n => n.y), pad = 1500;
      this.box = [Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) - Math.min(...xs) + 2 * pad,
        Math.max(...ys) - Math.min(...ys) + 2 * pad];
      this.svg.setAttribute("viewBox", this.box.join(" "));
    }
    const scale = this.box[2] / 600, pos = Object.fromEntries(t.nodes.map(n => [n.id, [n.x, -n.y]]));
    const parts = [];
    for (const [a, b] of t.links) {
      const [x1, y1] = pos[a], [x2, y2] = pos[b];
      parts.push(el("line", { x1, y1, x2, y2, class: "link", "stroke-width": 3 * scale }));
    }
    for (const id of t.lora) {
      const [cx, cy] = pos[id];
      parts.push(el("circle", { cx, cy, r: 30 * scale, class: "lora", "stroke-width": 2 * scale }));
    }
    for (const n of t.nodes) parts.push(this.node(n, pos[n.id], scale));
    this.svg.replaceChildren(...parts);
    const s = t.stats, h = Math.floor(t.t / 3600), m = Math.floor(t.t % 3600 / 60);
    this.stats.textContent = `Day ${Math.floor(h / 24) + 1} ${String(h % 24).padStart(2, "0")}:${String(m).padStart(2, "0")} · `
      + `${s.sent} sent · ${s.delivered} delivered · ${s.in_transit} in transit · ${s.not_delivered} NOT delivered`;
    this.log.textContent = t.events.join("\n");
  }

  node(n, [x, y], s) {
    const g = document.createElementNS(NS, "g"), r = 9 * s;
    const color = n.up ? `var(--${n.role})` : "var(--down)";
    let shape;
    if (n.role === "relay") shape = el("polygon", { points: `${x},${y - r} ${x + r},${y + r} ${x - r},${y + r}` });
    else if (ROLE_SHAPE[n.role] === "rect") shape = el("rect", { x: x - r, y: y - r, width: 2 * r, height: 2 * r, rx: n.role === "site" ? r / 2 : 0 });
    else shape = el("circle", { cx: x, cy: y, r });
    shape.setAttribute("fill", color);
    if (!n.up) shape.setAttribute("stroke-dasharray", `${2 * s}`);
    const title = document.createElementNS(NS, "title");
    title.textContent = `${n.id} (${n.role}${n.sa ? ", " + n.sa : ""})${n.up ? "" : " — POWERED OFF"}\n`
      + `${n.shards} shards, ${(n.used_bytes / 1e6).toFixed(1)} MB` + (n.battery_pct == null ? "" : `, battery ${n.battery_pct}%`);
    const label = el("text", { x: x + 1.4 * r, y: y + r / 2, "font-size": 11 * s, "stroke-width": 3 * s });
    label.textContent = `${n.id}${n.shards ? ` · ${n.shards}` : ""}${n.battery_pct == null ? "" : ` · ${Math.round(n.battery_pct)}%`}${n.up ? "" : " · OFF"}`;
    g.append(shape, title, label);
    return g;
  }
}

function el(tag, attrs) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
