// One message's journey: a simplified node map of the hops it took, then the same story as a
// timeline. Built from custody receipts (GET /api/journey/{id}).
import { pretty } from "/static/map.js";
import { t } from "/static/i18n.js";

const NS = "http://www.w3.org/2000/svg";

export class Journey {
  constructor(root, endpoints, onSelect) {
    this.root = root; this.endpoints = endpoints; this.onSelect = onSelect; this.id = null;
    root.innerHTML = `
      <div class="journey-head">
        <label for="journey-pick" data-i18n="journey.follow"></label>
        <select id="journey-pick"><option value="" data-i18n="journey.choose"></option></select>
      </div>
      <div class="journey-body"><p class="empty" data-i18n="journey.empty"></p></div>`;
    for (const el of root.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
    document.addEventListener("languagechange", () => { this.loadChoices(); this.show(this.id); });
    this.pick = root.querySelector("select");
    this.pick.onchange = () => this.show(this.pick.value || null);
    this.loadChoices(); setInterval(() => this.loadChoices(), 5000);
    setInterval(() => this.id && this.show(this.id), 2000);
  }

  async loadChoices() {
    const all = (await Promise.all(this.endpoints.map(async e =>
      (await (await fetch(`/api/endpoints/${e.name}/messages`)).json())
        .filter(m => m.direction === "out").map(m => ({ ...m, from: e.name })))))
      .flat().sort((a, b) => a.at < b.at ? 1 : -1).slice(0, 40);
    const keep = this.pick.value;
    this.pick.replaceChildren(new Option(t("journey.choose"), ""), ...all.map(m => new Option(t("journey.option",
      { time: m.at.split(" ")[1].slice(0, 5), from: pretty(m.from), to: pretty(m.peer), text: m.text.slice(0, 40) }), m.id)));
    this.pick.value = keep;
  }

  async show(id) {
    this.id = id;
    if (this.pick.value !== (id ?? "")) { await this.loadChoices(); this.pick.value = id ?? ""; }
    const body = this.root.querySelector(".journey-body");
    if (!id) {
      body.innerHTML = `<p class="empty"></p>`;
      body.firstChild.textContent = t("journey.empty");
      this.onSelect(null); return;
    }
    const r = await fetch(`/api/journey/${id}`);
    if (!r.ok) return;
    const j = await r.json();
    this.onSelect(j);
    body.replaceChildren(this.summary(j), this.graph(j), this.timeline(j));
  }

  summary(j) {
    const div = document.createElement("div");
    div.className = "journey-summary";
    const status = t("journey.status", { status: j.status === "delivered" ? "delivered"
      : j.status !== "in transit" ? "not" : j.opened ? "opened" : "transit" });
    div.innerHTML = `<h2></h2><p class="route"></p><p class="muted"></p>`;
    div.querySelector("h2").textContent = j.summary;
    div.querySelector(".route").textContent = t("journey.route",
      { from: pretty(j.from), to: pretty(j.to), priority: t(`priority.${j.priority}`).toLowerCase(), status });
    div.querySelector(".muted").textContent = t("journey.pieces", { total: j.total, k: j.k, spare: j.total - j.k });
    return div;
  }

  graph(j) {
    // columns: hops from the origin; rows: grouped under the node that first brought it there
    const parent = {}, depth = { [j.from_node]: 0 };
    for (const h of [...j.hops].sort((a, b) => a.t - b.t)) {
      if (h.to in depth) continue;
      depth[h.to] = (depth[h.from] ?? 0) + 1;
      parent[h.to] = h.from;
    }
    if (!(j.to_node in depth)) depth[j.to_node] = Math.max(0, ...Object.values(depth)) + 1;  // not reached yet
    const cols = Math.max(...Object.values(depth)) + 1;
    const byCol = Array.from({ length: cols }, () => []);
    const row = {};
    for (let c = 0; c < cols; c++) {
      byCol[c] = Object.keys(depth).filter(n => depth[n] === c)
        .sort((a, b) => (row[parent[a]] ?? 0) - (row[parent[b]] ?? 0) || (a === j.to_node) - (b === j.to_node));
      byCol[c].forEach((n, i) => { row[n] = i - (byCol[c].length - 1) / 2; });
    }
    const W = 900, rowH = 66, margin = 120;
    const H = Math.max(...byCol.map(c => c.length)) * rowH + 50;
    const xy = {};
    for (const [n, c] of Object.entries(depth)) {
      xy[n] = [cols === 1 ? W / 2 : margin + c * (W - 2 * margin) / (cols - 1), H / 2 + row[n] * rowH - 6];
    }
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "journey-graph", role: "img",
      "aria-label": t("journey.graph") });
    const edges = {};
    for (const h of j.hops) {
      const key = `${h.from}>${h.to}`;
      edges[key] ??= { from: h.from, to: h.to, pieces: 0, t: h.t, at: h.at };
      edges[key].pieces += h.pieces;
    }
    for (const e of Object.values(edges)) {
      if (!xy[e.from] || !xy[e.to]) continue;
      const [x1, y1] = xy[e.from], [x2, y2] = xy[e.to];
      svg.append(el("line", { x1, y1, x2, y2, class: "hop" }));
      const label = el("text", { x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 8, class: "hop-label", "text-anchor": "middle" });
      label.textContent = t("journey.hop", { n: e.pieces, time: e.at.split(" ")[1].slice(0, 5) });
      svg.append(label);
    }
    if (!j.hops.some(h => h.to === j.to_node)) {  // dashed: still to go, from wherever pieces wait
      for (const n of Object.keys(j.holders)) {
        if (xy[n] && n !== j.to_node) {
          svg.append(el("line", { x1: xy[n][0], y1: xy[n][1], x2: xy[j.to_node][0], y2: xy[j.to_node][1], class: "hop pending" }));
        }
      }
    }
    for (const [n, [x, y]] of Object.entries(xy)) {
      const end = n === j.from_node || n === j.to_node;
      svg.append(el(end ? "rect" : "circle", end
        ? { x: x - 7, y: y - 7, width: 14, height: 14, rx: 2, class: "jnode end" }
        : { cx: x, cy: y, r: 7, class: "jnode" }));
      const name = el("text", { x, y: y + 24, class: "jname", "text-anchor": "middle" });
      name.textContent = j.holders[n] ? t("journey.holds", { name: pretty(n), n: j.holders[n] }) : pretty(n);
      svg.append(name);
    }
    return svg;
  }

  timeline(j) {
    const ol = document.createElement("ol");
    ol.className = "timeline";
    const item = (at, text) => {
      const li = document.createElement("li");
      li.innerHTML = `<time></time><span></span>`;
      li.querySelector("time").textContent = at ? at.split(" ")[1].slice(0, 5) : "";
      li.querySelector("span").textContent = text;
      ol.append(li);
    };
    item(j.sent, t("journey.written", { node: pretty(j.from_node), total: j.total }));
    const merged = [];  // receipts come in half-second batches: one line per handover
    for (const h of j.hops) {
      const last = merged.at(-1);
      if (last && last.from === h.from && last.to === h.to && h.t - last.t < 120) last.pieces += h.pieces;
      else merged.push({ ...h });
    }
    for (const h of merged) item(h.at, t("journey.took", { to: pretty(h.to), n: h.pieces, from: pretty(h.from) }));
    if (j.opened) item(j.opened, t("journey.opened", { node: pretty(j.to_node) }));
    else item(null, t("journey.waiting", { node: pretty(j.to_node), k: j.k }));
    if (j.acked) item(j.acked, t("journey.acked", { node: pretty(j.from_node) }));
    return ol;
  }
}

function el(tag, attrs) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
