// Hub admin console (spec §10.6): inventory, laptops, network health, actions, signed log.
import { pretty } from "/static/map.js";
import { t } from "/static/i18n.js";

const DEMO_PIN = "2580";

export class Admin {
  constructor(root, who) {
    this.root = root; this.who = who; this.token = null; this.operator = null;
    document.addEventListener("languagechange", () => this.render());
    setInterval(() => this.token && this.refresh(), 3000);
    this.render();
  }

  async call(path, body) {
    const r = await fetch(`/api/admin${path}`, {
      method: body ? "POST" : "GET",
      headers: { "content-type": "application/json", authorization: `Bearer ${this.token}` },
      body: body && JSON.stringify(body),
    });
    if (r.status === 401) { this.token = null; this.why = t("signin.timed_out"); this.render(); throw new Error("locked"); }
    return r;
  }

  async render() {
    this.who.textContent = this.operator && this.token ? this.operator : "";
    if (!this.token) return this.signIn();
    this.root.innerHTML = `
      <section><h2>${esc(t("admin.inventory"))}</h2><div class="table-wrap" id="inventory"></div></section>
      <section><h2>${esc(t("admin.health"))}</h2><div class="table-wrap" id="clinics"></div><p id="refusals"></p></section>
      <section><h2>${esc(t("admin.laptops"))}</h2><div class="table-wrap" id="laptops"></div></section>
      <section><h2>${esc(t("admin.actions"))}</h2>
        <form class="admin-act" id="act">
          <label>${esc(t("admin.action"))}<select name="action"></select></label>
          <label>${esc(t("admin.target"))}<input name="target" list="targets" autocomplete="off"></label>
          <datalist id="targets"></datalist>
          <button class="send" type="submit">${esc(t("admin.do"))}</button>
          <p class="err" role="alert"></p>
        </form></section>
      <section><h2>${esc(t("admin.log"))}</h2><div class="table-wrap" id="log"></div></section>`;
    const f = this.root.querySelector("#act"), action = f.querySelector("[name=action]");
    f.onsubmit = async ev => {
      ev.preventDefault();
      const target = f.querySelector("[name=target]").value.trim();
      if (!confirm(t("admin.confirm", { action: t(`admin.do.${action.value}`), target: target || "—" }))) return;
      const r = await this.call("/actions", { action: action.value, target });
      f.querySelector(".err").textContent = r.ok ? "" : (await r.json()).detail;
      if (r.ok) { f.querySelector("[name=target]").value = ""; this.refresh(); }
    };
    this.refresh();
  }

  signIn() {
    this.root.innerHTML = `
      <form class="signin admin-signin">
        <h2>${esc(t("admin.signin"))}</h2>
        <label for="op">${esc(t("admin.operator"))}</label><select id="op"></select>
        <label for="pin">${esc(t("signin.pin"))}</label>
        <input id="pin" type="password" inputmode="numeric" autocomplete="off" maxlength="8">
        <button class="send" type="submit">${esc(t("signin.go"))}</button>
        <p class="muted demo">${esc(t("signin.demo", { pin: DEMO_PIN }))}</p>
        <p class="err" role="alert"></p>
      </form>`;
    const f = this.root.querySelector("form");
    f.querySelector(".err").textContent = this.why ?? "";
    f.querySelector("#op").add(new Option("Admin F. Mbise", "Admin F. Mbise"));
    f.onsubmit = async ev => {
      ev.preventDefault();
      const operator = f.querySelector("#op").value;
      const r = await fetch("/api/admin/session", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ operator, pin: f.querySelector("#pin").value }) });
      if (r.status === 429) { f.querySelector(".err").textContent = t("signin.locked", { s: Math.ceil((await r.json()).detail.retry_after_s) }); return; }
      if (!r.ok) { f.querySelector(".err").textContent = t("signin.wrong"); return; }
      ({ token: this.token, operator: this.operator } = await r.json());
      this.why = ""; this.render();
    };
  }

  async refresh() {
    const s = await (await this.call("")).json();
    const yes = t("admin.yes"), no = t("admin.no");
    const light = l => l ? `<span class="light ${esc(l)}" aria-hidden="true"></span>${esc(t(`light.${l}`))}` : "";
    this.root.querySelector("#inventory").innerHTML = table(
      ["admin.col.node", "admin.col.role", "admin.col.version", "admin.col.seen", "admin.col.light", "admin.col.battery",
       "admin.col.temp", "admin.col.storage", "admin.col.contacts"],
      s.nodes.map(n => [
        esc(pretty(n.id)) + (n.revoked ? ` <b class="bad">${esc(t("admin.revoked"))}</b>` : ""),
        esc(n.role ? t(`role.${n.role}`) : ""), esc(n.version ?? ""),
        n.seen_s_ago == null ? `<b class="bad">${esc(t("admin.never"))}</b>`
          : (n.stale ? `<b class="bad">${esc(ago(n.seen_s_ago))}</b>` : esc(ago(n.seen_s_ago))),
        light(n.light), esc(!n.version ? "" : n.battery_pct == null ? t("admin.mains") : `${Math.round(n.battery_pct)}%`),
        esc(n.temp_c == null ? "" : `${n.temp_c} °C`), esc(n.storage_pct == null ? "" : `${n.storage_pct}%`),
        esc(n.contacts ?? ""),
      ]));
    this.root.querySelector("#clinics").innerHTML = !s.clinics.length ? `<p class="muted">${esc(t("admin.no_arrivals"))}</p>` : table(
      ["admin.col.clinic", "admin.col.arrived", "admin.col.p50", "admin.col.max", "admin.col.not_delivered"],
      s.clinics.map(c => [esc(pretty(c.clinic)), esc(c.arrived), esc(c.p50_min ?? "—"), esc(c.max_min ?? "—"),
        c.not_delivered ? `<b class="bad">${esc(c.not_delivered)}</b>` : "0"]));
    const reasons = Object.entries(s.refusals);
    this.root.querySelector("#refusals").textContent = reasons.length
      ? t("admin.refusals", { list: reasons.map(([k, v]) => `${k} ${v}`).join(", ") }) : t("admin.no_refusals");
    this.root.querySelector("#laptops").innerHTML = table(["admin.col.laptop", "admin.col.site", "admin.col.directory"],
      s.laptops.map(l => [esc(pretty(l.name)), esc(pretty(l.site)), l.in_directory ? esc(yes) : `<b class="bad">${esc(no)}</b>`]));
    this.root.querySelector("#log").innerHTML = s.log.length ? table(
      ["admin.col.at", "admin.col.operator", "admin.col.action", "admin.col.target", "admin.col.signed"],
      s.log.map(e => [esc(clock(e.at)), esc(e.operator), esc(t(`admin.do.${e.action}`)), esc(e.target),
        e.signed ? esc(yes) : `<b class="bad">${esc(no)}</b>`])) : `<p class="muted">${esc(t("admin.no_log"))}</p>`;
    const action = this.root.querySelector("[name=action]");
    if (!action.options.length) for (const a of s.actions) action.add(new Option(t(`admin.do.${a}`), a));
    this.root.querySelector("#targets").replaceChildren(
      ...[...s.nodes.map(n => n.id), ...s.laptops.map(l => l.name)].map(v => new Option(v)));
  }
}

function table(heads, rows) {
  return `<table><thead><tr>${heads.map(h => `<th scope="col">${esc(t(h))}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function ago(s) { return s < 3600 ? t("time.min_ago", { m: Math.round(s / 60) }) : t("time.h_ago", { h: Math.round(s / 3600) }); }
function clock(s) {
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return t("clock", { day: Math.floor(h / 24) + 1, time: `${String(h % 24).padStart(2, "0")}:${String(m).padStart(2, "0")}` });
}
function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
