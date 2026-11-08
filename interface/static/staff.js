// The staff app on a clinic or hospital laptop (spec §10): sign in with a PIN, then patients,
// referrals and their answers (clinic), or the triage inbox (hospital). Never vague about delivery.
import { pretty } from "/static/map.js";
import { t } from "/static/i18n.js";

const VITALS = ["heart-rate", "resp-rate", "bp-systolic", "bp-diastolic", "spo2", "temperature", "glucose"];
const PRIORITIES = ["ROUTINE", "URGENT", "CRITICAL"];
const RANK = { CRITICAL: 0, URGENT: 1, ROUTINE: 2 };
const MAX_BYTES = 32 * 1024 * 1024;
const DEMO_PIN = "2580";

export class Staff {
  constructor(root, endpoints, initial) {
    this.root = root; this.endpoints = endpoints; this.place = initial;
    this.token = null; this.staff = null; this.tab = null; this.msgs = []; this.patients = [];
    this.count = 0; this.onChange = () => {};
    document.addEventListener("languagechange", () => this.render());
    setInterval(() => this.token && this.refresh(), 2000);
    this.render();
  }

  get me() { return this.endpoints.find(e => e.name === this.place); }

  async api(path, body, method = body ? "POST" : "GET") {
    const r = await fetch(`/api/endpoints/${this.place}/${path}`, {
      method, headers: { "content-type": "application/json", authorization: `Bearer ${this.token}` },
      body: body && JSON.stringify(body),
    });
    if (r.status === 401) { this.lock(t("signin.timed_out")); throw new Error("locked"); }
    return r;
  }

  lock(why = "") { this.token = null; this.why = why; this.render(); }

  render() {
    if (!this.token) return this.signIn();
    const tabs = this.me.role === "hospital" ? ["triage", "write", "patients"] : ["cases", "referral", "patients"];
    this.tab = tabs.includes(this.tab) ? this.tab : tabs[0];
    this.root.innerHTML = `
      <div class="who"><b class="place"></b><span class="muted staff"></span>
        <button class="signout add" type="button"></button>
        <button class="close" type="button"></button></div>
      <div class="tabs" role="tablist">${tabs.map(id => `<button role="tab" data-tab="${id}" aria-selected="${id === this.tab}"></button>`).join("")}</div>
      <div class="pane"></div>`;
    this.root.querySelector(".place").textContent = pretty(this.place);
    this.root.querySelector(".staff").textContent = this.staff;
    this.root.querySelector(".signout").textContent = t("signin.out");
    this.root.querySelector(".signout").onclick = () => {
      fetch("/api/session", { method: "DELETE", headers: { authorization: `Bearer ${this.token}` } });
      this.lock();
    };
    this.closeButton();
    for (const b of this.root.querySelectorAll("[data-tab]")) {
      b.textContent = t(`tab.${b.dataset.tab}`);
      b.onclick = () => { this.tab = b.dataset.tab; this.render(); };
    }
    this.refresh(true);
  }

  closeButton() {
    const close = this.root.querySelector(".close");
    close.textContent = "×";
    close.setAttribute("aria-label", t("drawer.close"));
    close.onclick = () => this.root.dispatchEvent(new Event("close-drawer"));
  }

  signIn() {
    const id = this.root.id;
    this.root.innerHTML = `
      <div class="who"><b data-i18n="signin.title"></b><button class="close" type="button"></button></div>
      <form class="signin">
        <label for="${id}-place" data-i18n="signin.place"></label><select id="${id}-place"></select>
        <label for="${id}-staff" data-i18n="signin.staff"></label><select id="${id}-staff"></select>
        <label for="${id}-pin" data-i18n="signin.pin"></label>
        <input id="${id}-pin" type="password" inputmode="numeric" autocomplete="off" maxlength="8" pattern="[0-9]*">
        <button class="send" type="submit" data-i18n="signin.go"></button>
        <p class="muted demo"></p>
        <p class="err" role="alert"></p>
      </form>`;
    for (const el of this.root.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
    this.closeButton();
    this.root.querySelector(".demo").textContent = t("signin.demo", { pin: DEMO_PIN });
    this.root.querySelector(".err").textContent = this.why ?? "";
    const place = this.root.querySelector(`#${id}-place`), staff = this.root.querySelector(`#${id}-staff`);
    for (const e of this.endpoints) place.add(new Option(pretty(e.name), e.name));
    place.value = this.place;
    const fill = () => { staff.replaceChildren(...this.me.staff.map(s => new Option(s, s))); };
    place.onchange = () => { this.place = place.value; fill(); this.onChange(); };
    fill();
    this.root.querySelector("form").onsubmit = async ev => {
      ev.preventDefault();
      const pin = this.root.querySelector(`#${id}-pin`);
      const r = await fetch(`/api/endpoints/${this.place}/session`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ staff: staff.value, pin: pin.value }),
      });
      const err = this.root.querySelector(".err");
      if (r.status === 429) {
        err.textContent = t("signin.locked", { s: Math.ceil((await r.json()).detail.retry_after_s) });
        return;
      }
      if (!r.ok) { err.textContent = t("signin.wrong"); pin.value = ""; pin.focus(); return; }
      ({ token: this.token, staff: this.staff } = await r.json());
      this.why = "";
      this.render();
    };
  }

  async refresh(redraw = false) {
    const [m, p] = await Promise.all([this.api("messages"), this.api("patients")]);
    this.msgs = await m.json(); this.patients = await p.json();
    this.count = this.msgs.length; this.onChange();
    const pane = this.root.querySelector(".pane"), live = ["cases", "triage"].includes(this.tab);
    if (!pane || !(live || redraw)) return;  // forms draw once: a refresh must not wipe typing
    if (!redraw && pane.querySelector("form") && pane.contains(document.activeElement)) return;  // answering
    const views = { cases: () => this.cases(), triage: () => this.triage(), referral: () => this.referralForm(),
      write: () => this.writeForm(), patients: () => this.patientList() };
    pane.replaceChildren(views[this.tab]());
  }

  // ---- clinic ----

  cases() {
    const out = document.createElement("div");
    out.className = "thread";
    const answers = id => this.msgs.filter(m => m.direction === "in" && m.in_response_to === id);
    const mine = this.msgs.filter(m => m.direction === "out" || !m.in_response_to);
    if (!mine.length) out.append(empty(t("cases.empty")));
    const groups = new Map();
    for (const m of mine) {
      const key = m.patient?.local_id ?? "";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(m);
    }
    for (const [key, msgs] of [...groups].sort(([a], [b]) => (a === "") - (b === ""))) {
      const head = document.createElement("h3");
      head.className = "patient-head";
      head.textContent = key ? patientLine(msgs[0].patient) : t("cases.no_patient");
      out.append(head);
      for (const m of msgs) {
        const card = this.card(m);
        if (m.referral) card.querySelector(".body").prepend(caseStatus(m, answers(m.id)));
        out.append(card);
        for (const a of answers(m.id)) out.append(this.card(a, true));
      }
    }
    return out;
  }

  referralForm() {
    const f = form(`
      <label>${esc(t("referral.patient"))}<select name="patient"></select></label>
      <label>${esc(t("referral.reason"))}<textarea name="reason" required></textarea></label>
      <label>${esc(t("referral.diagnosis"))}<input name="diagnosis"></label>
      <label>${esc(t("referral.history"))}<textarea name="history"></textarea></label>
      <label>${esc(t("referral.treatment"))}<textarea name="treatment"></textarea></label>
      <fieldset class="vitals"><legend>${esc(t("referral.vitals"))}</legend>${VITALS.map(c =>
        `<label>${esc(t(`vital.${c}`))}<input type="number" step="any" data-code="${c}"></label>`).join("")}</fieldset>
      <label>${esc(t("referral.note"))}<textarea name="text"></textarea></label>
      ${priorityPicker("URGENT")}
      <label class="add">${esc(t("compose.attach"))}<input class="file" type="file" accept="image/*,application/pdf" multiple></label>
      <div class="files"></div>
      <button class="send" type="submit">${esc(t("referral.send"))}</button>
      <p class="err" role="alert"></p>`);
    const pick = f.querySelector("[name=patient]");
    for (const p of this.patients) pick.add(new Option(patientLine(p), p.local_id));
    fileList(f);
    f.onsubmit = async ev => {
      ev.preventDefault();
      const v = name => f.querySelector(`[name=${name}]`).value.trim();
      const patient = this.patients.find(p => p.local_id === pick.value);
      if (!patient) return fail(f, t("referral.need_patient"));
      if (!v("reason")) return fail(f, t("referral.need_reason"));
      await this.send(f, { to: hospitalOf(this.me), patient,
        referral: { reason: v("reason"), diagnosis: v("diagnosis"), history: v("history"), treatment: v("treatment") },
        text: v("text"), vitals: vitals(f) }, () => { this.tab = "cases"; this.render(); });
    };
    return f;
  }

  patientList() {
    const out = document.createElement("div");
    out.className = "thread";
    const list = document.createElement("ul");
    list.className = "patients";
    for (const p of this.patients) {
      const li = document.createElement("li");
      li.textContent = patientLine(p);
      list.append(li);
    }
    out.append(this.patients.length ? list : empty(t("patients.empty")));
    if (this.me.role === "clinic") {
      const f = form(`
        <h3>${esc(t("patients.add"))}</h3>
        <label>${esc(t("patient.local_id"))}<input name="local_id" required></label>
        <label>${esc(t("patient.name"))}<input name="name" required></label>
        <label>${esc(t("patient.age"))}<input name="age" type="number" min="0" max="130" required></label>
        <label>${esc(t("patient.sex"))}<select name="sex">${["female", "male", "other", "unknown"].map(s =>
          `<option value="${s}">${esc(t(`sex.${s}`))}</option>`).join("")}</select></label>
        <button class="send" type="submit">${esc(t("patients.save"))}</button>
        <p class="err" role="alert"></p>`);
      f.onsubmit = async ev => {
        ev.preventDefault();
        const v = name => f.querySelector(`[name=${name}]`).value.trim();
        const r = await this.api("patients", { local_id: v("local_id"), name: v("name"), age: Number(v("age")), sex: v("sex") });
        if (!r.ok) return fail(f, (await r.json()).detail);
        this.refresh(true);
      };
      out.append(f);
    }
    return out;
  }

  // ---- hospital ----

  triage() {
    const out = document.createElement("div");
    out.className = "thread";
    const answered = id => this.msgs.find(m => m.direction === "out" && m.in_response_to === id && m.decision);
    const inbox = this.msgs.filter(m => m.direction === "in")
      .sort((a, b) => RANK[a.priority] - RANK[b.priority] || a.t - b.t);  // urgency, then oldest first
    if (!inbox.length) out.append(empty(t("triage.empty")));
    for (const m of inbox) {
      const card = this.card(m);
      card.querySelector(".meta").append(` · ${t("triage.waited", { time: duration(m.waited_s) })}`);
      const done = answered(m.id);
      if (done) card.querySelector(".body").prepend(chip(t(`decision.${done.decision}`), done.decision));
      if (m.referral && !done) card.append(this.answerBar(m));
      out.append(card);
    }
    return out;
  }

  answerBar(m) {
    const bar = document.createElement("div");
    bar.className = "answers";
    for (const d of ["accepted", "declined", "needs-info"]) {
      const b = document.createElement("button");
      b.type = "button"; b.className = `answer ${d}`; b.textContent = t(`answer.${d}`);
      b.onclick = () => {
        const f = form(`
          <label>${esc(t(`answer.note.${d}`))}<textarea name="text"></textarea></label>
          <button class="send" type="submit">${esc(t("answer.send"))}</button>
          <p class="err" role="alert"></p>`);
        f.onsubmit = async ev => {
          ev.preventDefault();
          await this.send(f, { to: m.peer, decision: d, in_response_to: m.id, priority: m.priority,
            text: f.querySelector("textarea").value.trim(), patient: m.patient }, () => {});
        };
        bar.replaceWith(f);
        f.querySelector("textarea").focus();
      };
      bar.append(b);
    }
    return bar;
  }

  writeForm() {
    const clinics = this.me.contacts.filter(c => c !== this.place);
    const f = form(`
      <label>${esc(t("write.to"))}<select name="to">${clinics.map(c => `<option value="${esc(c)}">${esc(pretty(c))}</option>`).join("")}</select></label>
      <label>${esc(t("write.patient"))}<select name="patient"><option value="">${esc(t("write.no_patient"))}</option></select></label>
      <label>${esc(t("write.text"))}<textarea name="text"></textarea></label>
      <fieldset class="result"><legend>${esc(t("result.title"))}</legend>
        <label>${esc(t("result.test"))}<input name="test"></label>
        <label>${esc(t("result.conclusion"))}<input name="conclusion"></label></fieldset>
      ${priorityPicker("ROUTINE")}
      <label class="add">${esc(t("compose.attach"))}<input class="file" type="file" multiple></label>
      <div class="files"></div>
      <button class="send" type="submit">${esc(t("write.send"))}</button>
      <p class="err" role="alert"></p>`);
    const pick = f.querySelector("[name=patient]");
    for (const p of this.patients) pick.add(new Option(patientLine(p), p.local_id));
    fileList(f);
    f.onsubmit = async ev => {
      ev.preventDefault();
      const v = name => f.querySelector(`[name=${name}]`).value.trim();
      const patient = this.patients.find(p => p.local_id === pick.value) ?? null;
      const result = v("test") || v("conclusion") ? { test: v("test"), conclusion: v("conclusion") } : null;
      if (result && (!result.test || !result.conclusion)) return fail(f, t("result.both"));
      if (!v("text") && !result) return fail(f, t("compose.empty"));
      await this.send(f, { to: v("to"), text: v("text"), patient, result }, () => f.reset());
    };
    return f;
  }

  // ---- shared ----

  async send(f, body, then) {
    const files = [...(f.querySelector(".file")?.files ?? [])];
    if (files.some(x => x.size > MAX_BYTES)) return fail(f, t("compose.too_big"));
    const attachments = await Promise.all(files.map(async x => ({
      title: x.name, content_type: x.type || "application/octet-stream",
      data_b64: btoa(Array.from(new Uint8Array(await x.arrayBuffer()), b => String.fromCharCode(b)).join("")),
    })));
    const priority = f.querySelector(".urgent [aria-pressed=true]")?.dataset.p ?? body.priority ?? "ROUTINE";
    const r = await this.api("messages", { ...body, priority, attachments });
    if (!r.ok) return fail(f, (await r.json()).detail);
    then();
    this.refresh(true);
  }

  card(m, nested = false) {
    const el = document.createElement("div");
    el.className = `msg ${m.direction}${nested ? " nested" : ""}`;
    const notDelivered = m.status.startsWith("NOT DELIVERED");
    const state = m.status === "delivered" ? `<span class="state delivered">${esc(t("state.delivered"))}</span>`
      : m.status === "in transit" ? `<span class="state transit">${esc(t("state.transit"))}</span>` : "";
    const urgency = m.priority && m.priority !== "ROUTINE"
      ? `<span class="urgency ${m.priority}">${esc(t(`priority.${m.priority}`))}</span> · ` : "";
    el.innerHTML = `<div class="body"><span class="text"></span></div>
      <div class="meta">${urgency}<span class="peer"></span> · ${esc(m.at.split(" ")[1].slice(0, 5))}${state}
        <button class="follow" type="button">${esc(t("msg.follow"))}</button></div>
      ${notDelivered ? `<div class="alert">${esc(t("msg.not_delivered"))}</div>` : ""}
      ${m.status === "in transit" ? `<div class="meta deadline">${esc(t("msg.deadline", { time: m.flag_at.split(" ")[1].slice(0, 5) }))}</div>` : ""}`;
    const who = m.author ? `${m.author}, ${pretty(m.peer)}` : pretty(m.peer);
    el.querySelector(".peer").textContent = t(m.direction === "out" ? "msg.to" : "msg.from", { who: m.direction === "out" ? pretty(m.peer) : who });
    const lines = [];
    if (m.referral) {
      lines.push(t("msg.referral", { reason: m.referral.reason }));
      if (m.referral.diagnosis) lines.push(t("msg.diagnosis", { text: m.referral.diagnosis }));
      if (m.referral.treatment) lines.push(t("msg.treatment", { text: m.referral.treatment }));
    }
    if (m.decision) lines.push(t(`decision.${m.decision}`));
    if (m.result) lines.push(t("msg.result", { test: m.result.test, conclusion: m.result.conclusion }));
    if (this.me.role === "hospital" && m.patient) lines.push(patientLine(m.patient));
    if (m.text) lines.push(m.text);
    for (const v of m.vitals ?? []) lines.push(`${t(`vital.${v.code}`)}: ${v.value}`);
    for (const a of m.attachments ?? []) lines.push(t("msg.attachment", { title: a.title, kb: Math.ceil(a.bytes / 1024) }));
    el.querySelector(".text").textContent = lines.join("\n");
    el.querySelector(".follow").onclick = () =>
      this.root.dispatchEvent(new CustomEvent("follow", { detail: m.id, bubbles: true }));
    return el;
  }
}

function caseStatus(m, answers) {
  const decided = answers.filter(a => a.decision).at(-1);
  if (decided) return chip(t(`decision.${decided.decision}`), decided.decision);
  if (m.status.startsWith("NOT DELIVERED")) return chip(t("case.not_delivered"), "declined");
  return chip(t(m.status === "delivered" ? "case.opened" : "case.sent"), m.status === "delivered" ? "opened" : "sent");
}

function chip(text, kind) {
  const s = document.createElement("span");
  s.className = `chip ${kind}`;
  s.textContent = text;
  return s;
}

function patientLine(p) {
  return t("patient.line", { name: p.name, age: p.age, sex: t(`sex.${p.sex}`), id: p.local_id });
}

function hospitalOf(me) { return me.contacts.find(c => c.includes("hospital")) ?? me.contacts.find(c => c !== me.name); }

function duration(s) {
  const m = Math.round(s / 60);
  return m < 60 ? t("time.minutes", { m }) : t("time.hours", { h: Math.floor(m / 60), m: m % 60 });
}

function vitals(f) {
  return [...f.querySelectorAll(".vitals input")].filter(i => i.value !== "")
    .map(i => ({ code: i.dataset.code, value: Number(i.value) }));
}

function priorityPicker(initial) {
  return `<div class="urgent" role="group" aria-label="${esc(t("compose.priority"))}">${PRIORITIES.map(p =>
    `<button type="button" data-p="${p}" aria-pressed="${p === initial}">${esc(t(`priority.${p}`))}</button>`).join("")}</div>`;
}

function form(html) {
  const f = document.createElement("form");
  f.className = "compose form";
  f.innerHTML = html;
  for (const b of f.querySelectorAll(".urgent button")) b.onclick = () => {
    for (const x of f.querySelectorAll(".urgent button")) x.setAttribute("aria-pressed", x === b);
  };
  return f;
}

function fileList(f) {
  const file = f.querySelector(".file");
  file.onchange = () => { f.querySelector(".files").textContent = [...file.files].map(x => x.name).join(", "); };
}

function fail(f, text) { f.querySelector(".err").textContent = text; }

function empty(text) {
  const p = document.createElement("p");
  p.className = "empty";
  p.textContent = text;
  return p;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
