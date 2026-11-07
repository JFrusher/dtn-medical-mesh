// One endpoint's messages (spec §10.2): quick as a chat app, never vague about delivery.
import { pretty } from "/static/map.js";

const VITALS = [
  ["heart-rate", "Heart rate, /min"], ["resp-rate", "Breathing, /min"],
  ["bp-systolic", "BP systolic"], ["bp-diastolic", "BP diastolic"],
  ["spo2", "Oxygen, %"], ["temperature", "Temperature, °C"], ["glucose", "Glucose, mmol/L"],
];
const MAX_BYTES = 32 * 1024 * 1024;

export class Chat {
  constructor(root, endpoints, initial) {
    this.root = root; this.endpoints = endpoints; this.priority = "ROUTINE"; this.replyTo = null;
    this.count = 0; this.onChange = () => {};
    const id = root.id;
    root.innerHTML = `
      <div class="who">
        <label class="sr-only" for="${id}-me">Writing as</label><select id="${id}-me" class="me"></select>
        <span>to</span>
        <label class="sr-only" for="${id}-to">Recipient</label><select id="${id}-to" class="to"></select>
        <button class="close" type="button" aria-label="Close">×</button>
      </div>
      <div class="thread" aria-live="polite"></div>
      <form class="compose">
        <div class="replying"></div>
        <label class="sr-only" for="${id}-text">Message</label>
        <textarea id="${id}-text" placeholder="Write a referral, note or question"></textarea>
        <div class="vitals" hidden>${VITALS.map(([c, l]) =>
          `<label>${l}<input type="number" step="any" data-code="${c}"></label>`).join("")}</div>
        <div class="tools">
          <div class="urgent" role="group" aria-label="Priority">${["ROUTINE", "URGENT", "CRITICAL"].map(p =>
            `<button type="button" data-p="${p}" aria-pressed="${p === "ROUTINE"}">${p[0] + p.slice(1).toLowerCase()}</button>`).join("")}</div>
          <button class="add add-vitals" type="button" aria-expanded="false">Add vitals</button>
          <label class="add">Attach<input class="file" type="file" multiple></label>
          <button class="send" type="submit">Send</button>
        </div>
        <div class="files"></div>
        <div class="err" role="alert"></div>
      </form>`;
    this.me = root.querySelector(".me"); this.to = root.querySelector(".to");
    for (const e of endpoints) this.me.add(new Option(pretty(e.name), e.name));
    this.me.value = initial;
    this.me.onchange = () => { this.fillContacts(); this.refresh(); };
    root.querySelector(".close").onclick = () => root.dispatchEvent(new Event("close-drawer"));
    root.querySelectorAll(".urgent button").forEach(b => b.onclick = () => {
      this.priority = b.dataset.p;
      root.querySelectorAll(".urgent button").forEach(x => x.setAttribute("aria-pressed", x === b));
    });
    const vitals = root.querySelector(".vitals"), addVitals = root.querySelector(".add-vitals");
    addVitals.onclick = () => {
      vitals.hidden = !vitals.hidden;
      addVitals.setAttribute("aria-expanded", !vitals.hidden);
      addVitals.textContent = vitals.hidden ? "Add vitals" : "Hide vitals";
    };
    const file = root.querySelector(".file");
    file.onchange = () => {
      root.querySelector(".files").textContent = [...file.files].map(f => f.name).join(", ");
    };
    root.querySelector("form").onsubmit = ev => { ev.preventDefault(); this.send(); };
    this.fillContacts(); this.refresh(); setInterval(() => this.refresh(), 2000);
  }

  fillContacts() {
    const me = this.endpoints.find(e => e.name === this.me.value);
    const others = me.contacts.filter(c => c !== me.name);
    this.to.replaceChildren(...others.map(c => new Option(pretty(c), c)));
    this.to.value = others.find(c => c.includes("hospital")) ?? others[0];  // clinics usually write to the hospital
  }

  async refresh() {
    const msgs = await (await fetch(`/api/endpoints/${this.me.value}/messages`)).json();
    const thread = this.root.querySelector(".thread");
    const atBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 40;
    if (msgs.length) thread.replaceChildren(...msgs.map(m => this.bubble(m)));
    else thread.innerHTML = `<p class="empty">No messages yet. Write one below and watch it travel on the map.</p>`;
    if (atBottom) thread.scrollTop = thread.scrollHeight;
    this.count = msgs.length; this.onChange();
  }

  bubble(m) {
    const el = document.createElement("div");
    el.className = `msg ${m.direction}`;
    const notDelivered = m.status.startsWith("NOT DELIVERED");
    const state = m.status === "delivered" ? `<span class="state delivered">Delivered</span>`
      : m.status === "in transit" ? `<span class="state transit">In transit</span>` : "";
    const urgency = m.priority && m.priority !== "ROUTINE"
      ? `<span class="urgency ${m.priority}">${m.priority[0] + m.priority.slice(1).toLowerCase()}, </span>` : "";
    el.innerHTML = `<div class="body"></div>
      <div class="meta">${urgency}<span class="peer"></span>, ${m.at.split(" ")[1].slice(0, 5)}${state}
        ${m.direction === "in" ? ` <button class="reply" type="button">Reply</button>` : ""}</div>
      ${notDelivered ? `<div class="alert">NOT DELIVERED — use another channel</div>` : ""}
      ${m.status === "in transit" ? `<div class="meta deadline">Timing not guaranteed. Marked not delivered if still travelling at ${m.flag_at.split(" ")[1].slice(0, 5)}.</div>` : ""}`;
    el.querySelector(".peer").textContent = (m.direction === "out" ? "to " : "from ") + pretty(m.peer);
    el.querySelector(".body").textContent = (m.in_response_to ? "↩ " : "") + m.summary.replaceAll(" · ", ", ");
    const reply = el.querySelector(".reply");
    if (reply) reply.onclick = () => {
      this.replyTo = m.id; this.to.value = m.peer;
      this.root.querySelector(".replying").textContent = `Replying to “${m.text.slice(0, 60)}”`;
      this.root.querySelector("textarea").focus();
    };
    return el;
  }

  async send() {
    const form = this.root.querySelector("form"), err = this.root.querySelector(".err");
    err.textContent = "";
    const files = [...form.querySelector(".file").files];
    if (files.some(f => f.size > MAX_BYTES)) {
      err.textContent = "Files over 32 MB can't travel this way. Compress it or send it another way."; return;
    }
    const attachments = await Promise.all(files.map(async f => ({
      title: f.name, content_type: f.type || "application/octet-stream",
      data_b64: btoa(Array.from(new Uint8Array(await f.arrayBuffer()), b => String.fromCharCode(b)).join("")),
    })));
    const vitals = [...form.querySelectorAll(".vitals input")].filter(i => i.value !== "")
      .map(i => ({ code: i.dataset.code, value: Number(i.value) }));
    const text = form.querySelector("textarea").value.trim();
    if (!text && !vitals.length && !attachments.length) { err.textContent = "Write a message, add vitals or attach a file first."; return; }
    const r = await fetch(`/api/endpoints/${this.me.value}/messages`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ to: this.to.value, text, priority: this.priority, vitals, attachments,
        in_response_to: this.replyTo }),
    });
    if (!r.ok) { err.textContent = (await r.json()).detail; return; }
    form.reset(); this.replyTo = null;
    this.root.querySelector(".replying").textContent = ""; this.root.querySelector(".files").textContent = "";
    this.refresh();
  }
}
