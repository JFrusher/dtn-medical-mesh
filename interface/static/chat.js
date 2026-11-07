// Messaging-style endpoint UI (spec §10.2): as quick as a chat app, never vague about delivery.
const VITALS = [
  ["heart-rate", "Heart rate /min"], ["resp-rate", "Resp rate /min"],
  ["bp-systolic", "BP systolic"], ["bp-diastolic", "BP diastolic"],
  ["spo2", "SpO₂ %"], ["temperature", "Temp °C"], ["glucose", "Glucose mmol/L"],
];
const MAX_BYTES = 32 * 1024 * 1024;

export class Chat {
  constructor(root, endpoints, initial) {
    this.root = root; this.endpoints = endpoints; this.priority = "ROUTINE"; this.replyTo = null;
    root.innerHTML = `
      <h2><label class="sr-only" for="${root.id}-me">Endpoint</label>
        <select id="${root.id}-me" class="me"></select>
        <span>to</span><label class="sr-only" for="${root.id}-to">Recipient</label>
        <select id="${root.id}-to" class="to"></select></h2>
      <div class="thread" aria-live="polite"></div>
      <form class="compose">
        <div class="replying muted"></div>
        <label class="sr-only" for="${root.id}-text">Message</label>
        <textarea id="${root.id}-text" placeholder="Referral, note or question…"></textarea>
        <details><summary>Vitals</summary><div class="vitals">${VITALS.map(([c, l]) =>
          `<label>${l}<input type="number" step="any" data-code="${c}"></label>`).join("")}</div></details>
        <div class="row">
          <div class="seg" role="group" aria-label="Priority">${["ROUTINE", "URGENT", "CRITICAL"].map(p =>
            `<button type="button" data-p="${p}" aria-pressed="${p === "ROUTINE"}">${p[0] + p.slice(1).toLowerCase()}</button>`).join("")}</div>
          <label class="grow">📎 <input type="file" multiple></label>
          <button class="send" type="submit">Send</button>
        </div>
        <div class="err" role="alert"></div>
      </form>`;
    this.me = root.querySelector(".me"); this.to = root.querySelector(".to");
    for (const e of endpoints) this.me.add(new Option(`${e.name} (${e.node})`, e.name));
    this.me.value = initial;
    this.me.onchange = () => { this.fillContacts(); this.refresh(); };
    root.querySelectorAll(".seg button").forEach(b => b.onclick = () => {
      this.priority = b.dataset.p;
      root.querySelectorAll(".seg button").forEach(x => x.setAttribute("aria-pressed", x === b));
    });
    root.querySelector("form").onsubmit = ev => { ev.preventDefault(); this.send(); };
    this.fillContacts(); this.refresh(); setInterval(() => this.refresh(), 2000);
  }

  fillContacts() {
    const me = this.endpoints.find(e => e.name === this.me.value);
    this.to.replaceChildren(...me.contacts.filter(c => c !== me.name).map(c => new Option(c, c)));
  }

  async refresh() {
    const msgs = await (await fetch(`/api/endpoints/${this.me.value}/messages`)).json();
    const thread = this.root.querySelector(".thread");
    const atBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 40;
    thread.replaceChildren(...msgs.map(m => this.bubble(m)));
    if (atBottom) thread.scrollTop = thread.scrollHeight;
  }

  bubble(m) {
    const el = document.createElement("div");
    el.className = `msg ${m.direction}`;
    const chip = m.status === "delivered" ? "delivered" : m.status === "in transit" ? "transit" : "received";
    const notDelivered = m.status.startsWith("NOT DELIVERED");
    el.innerHTML = `<div class="meta">
        <span>${m.direction === "out" ? "to" : "from"} <b></b></span><span>${m.at}</span>
        ${m.priority ? `<span class="prio ${m.priority}">${m.priority}</span>` : ""}
        ${notDelivered ? "" : `<span class="chip ${chip}">${m.status}</span>`}
        ${m.direction === "in" ? `<button class="reply" type="button">Reply</button>` : ""}
      </div><div class="body"></div>
      ${notDelivered ? `<div class="alert">NOT DELIVERED — use another channel</div>` : ""}
      ${m.status === "in transit" ? `<div class="deadline">Timing not guaranteed · flagged NOT DELIVERED if not through by ${m.flag_at}</div>` : ""}`;
    el.querySelector("b").textContent = m.peer;
    el.querySelector(".body").textContent = (m.in_response_to ? "↩ " : "") + m.summary;
    const reply = el.querySelector(".reply");
    if (reply) reply.onclick = () => {
      this.replyTo = m.id; this.to.value = m.peer;
      this.root.querySelector(".replying").textContent = `Replying to: ${m.text.slice(0, 60)}`;
      this.root.querySelector("textarea").focus();
    };
    return el;
  }

  async send() {
    const form = this.root.querySelector("form"), err = this.root.querySelector(".err");
    err.textContent = "";
    const files = [...form.querySelector("input[type=file]").files];
    if (files.some(f => f.size > MAX_BYTES)) {
      err.textContent = "Over 32 MB: compress it or send it another way."; return;
    }
    const attachments = await Promise.all(files.map(async f => ({
      title: f.name, content_type: f.type || "application/octet-stream",
      data_b64: btoa(Array.from(new Uint8Array(await f.arrayBuffer()), b => String.fromCharCode(b)).join("")),
    })));
    const vitals = [...form.querySelectorAll(".vitals input")].filter(i => i.value !== "")
      .map(i => ({ code: i.dataset.code, value: Number(i.value) }));
    const text = form.querySelector("textarea").value.trim();
    if (!text && !vitals.length && !attachments.length) { err.textContent = "Nothing to send."; return; }
    const r = await fetch(`/api/endpoints/${this.me.value}/messages`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ to: this.to.value, text, priority: this.priority, vitals, attachments,
        in_response_to: this.replyTo }),
    });
    if (!r.ok) { err.textContent = (await r.json()).detail; return; }
    form.reset(); this.replyTo = null; this.root.querySelector(".replying").textContent = "";
    this.refresh();
  }
}
