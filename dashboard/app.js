import { OmegaClient } from "./api-client.js";
import * as workbench from "./workbench-ui.js";

const $ = (selector) => document.querySelector(selector);
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const icons = {
  overview:
    '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  chat: '<path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6A8.4 8.4 0 0 1 12.5 3h.5a8.5 8.5 0 0 1 8 8v.5Z"/>',
  activity: '<path d="M2 12h4l3-8 6 16 3-8h4"/>',
  goals:
    '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  evidence:
    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>',
  improvements: '<path d="m3 17 6-6 4 4 8-12M15 3h6v6"/>',
  config:
    '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>',
};
const icon = (name) =>
  `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.activity}</svg>`;
const pages = {
  overview: ["Overview", "A clear view of what Omega is doing, and why."],
  chat: [
    "Conversation",
    "Work with Omega without losing sight of its autonomous activity.",
  ],
  activity: [
    "Live activity",
    "Follow agent turns, commands, and their outcomes as they happen.",
  ],
  goals: ["Goals", "Set direction. Keep autonomous work focused."],
  evidence: [
    "Evidence",
    "Inspect the observations and tool results behind the work.",
  ],
  improvements: [
    "Improvements",
    "Review reported changes and the evidence supporting them.",
  ],
  skills: ["Skills", "Build, activate, and test your own Omega skills."],
  reasoning: [
    "Reasoning",
    "Inspect formal inference with NAL, PLN, and native ONA.",
  ],
  orchestration: [
    "Orchestration",
    "Compose reasoning and skills into inspectable workflows.",
  ],
  health: [
    "Health & status",
    "Monitor the real agent heartbeat and installed services.",
  ],
  config: ["Configuration", "Manage how Omega works in this workspace."],
};
const busyForms = new Set();
let page = "overview";
let state = null;
let connection = "disconnected";
let filter = "all";
let query = "";
const statusBadge = (status) =>
  `<span class="badge ${["completed", "improved", "active", "recorded", "connected"].includes(status) ? "good" : ["failed", "interrupted", "regressed", "cancelled"].includes(status) ? "bad" : ["running", "working", "accepted", "paused"].includes(status) ? "amber" : ""}">${escape(status)}</span>`;
const date = (value) =>
  value
    ? new Date(value).toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "—";
const relative = (value) => {
  const seconds = Math.max(
    0,
    Math.floor((Date.now() - new Date(value)) / 1000),
  );
  return seconds < 60
    ? "just now"
    : seconds < 3600
      ? `${Math.floor(seconds / 60)}m ago`
      : date(value);
};
const empty = (title, body, name = "activity") =>
  `<div class="empty">${icon(name)}<h3>${title}</h3><p>${body}</p></div>`;
const needConnection = () =>
  state
    ? ""
    : empty(
        "Your workspace, in view",
        "Connect to your Omega API to see live goals, activity, and evidence.",
        "overview",
      );
function notify(message, bad = false) {
  $("#notice").textContent = message;
  $("#notice").className = bad ? "error" : "success";
  $("#notice").hidden = false;
}
function updateConnection(status) {
  connection = status;
  $("#connection").className =
    `badge ${status === "connected" ? "good" : status === "unauthorized" ? "bad" : ""}`;
  $("#connection").textContent =
    status === "connected"
      ? "● Live connection"
      : status === "reconnecting"
        ? "◌ Reconnecting…"
        : status === "connecting"
          ? "◌ Connecting…"
          : status === "unauthorized"
            ? "Token required"
            : "○ Disconnected";
  $("#connect").textContent =
    status === "connected" || status === "reconnecting"
      ? "Disconnect"
      : "Connect";
  document.body.classList.toggle(
    "offline",
    !["connected", "reconnecting"].includes(status),
  );
  if (status === "unauthorized")
    notify("Authentication expired. Reconnect with the dashboard token.", true);
  drawData();
}
const client = new OmegaClient({
  onSnapshot: (snapshot) => {
    state = snapshot;
    drawData();
  },
  onEvent: (event) => {
    applyEvent(event);
    drawData();
  },
  onStatus: updateConnection,
  onError: (error) => notify(error.message, true),
  onPending: (pending) => {
    $("#pending").hidden = !pending.length;
    $("#pending").innerHTML =
      `<span>${pending.length} command${pending.length === 1 ? "" : "s"} awaiting confirmation. Reconnect retries the same request IDs.</span><button class="button small" data-action="retry">Retry now</button><button class="button small" data-action="discard-pending">Clear local retries</button>`;
  },
});
client.savePending();
function upsert(collection, value, cap = 200) {
  const array = state[collection];
  const index = array.findIndex((item) => item.id === value.id);
  if (index >= 0) array[index] = value;
  else array.unshift(value);
  if (!["goals", "skills", "orchestrations"].includes(collection))
    array.splice(cap);
}
function applyEvent(event) {
  if (!state || event.seq <= state.cursor) return;
  state.cursor = event.seq;
  state.activity.unshift(event);
  state.activity.splice(200);
  const [kind, action] = event.type.split(".");
  if (kind === "goal") upsert("goals", event.data);
  if (kind === "message") {
    state.messages.push(event.data);
    state.messages = state.messages.slice(-200);
  }
  if (kind === "evidence") upsert("evidence", event.data);
  if (kind === "improvement") upsert("improvements", event.data);
  if (kind === "config") state.config = event.data;
  if (kind === "skill") upsert("skills", event.data);
  if (kind === "reasoning") upsert("reasoning", event.data);
  if (kind === "workflow") upsert("orchestrations", event.data);
  if (kind === "orchestration") upsert("orchestration_runs", event.data);
  if (kind === "telos") state.telos = event.data;
  if (kind === "catalog") state.skill_catalog = event.data.skills;
  if (kind === "settings") {
    if (event.data.schema) state.settings = event.data;
    else if (state.settings)
      Object.assign(state.settings.values, event.data.values);
  }
  if (kind === "request") {
    if (event.data.payload) upsert("requests", event.data);
    else
      upsert("requests", {
        ...event.data,
        status: "accepted",
        created_at: event.at,
      });
  }
  if (kind === "run") {
    upsert("runs", event.data);
    state.agent.status = action === "started" ? "working" : "idle";
    state.agent.run_id = action === "started" ? event.data.id : null;
    state.agent.request_id = action === "started" ? event.request_id : null;
  }
}
function navigate() {
  window.scrollTo(0, 0);
  page = Object.hasOwn(pages, location.hash.slice(1))
    ? location.hash.slice(1)
    : "overview";
  filter = "all";
  query = "";
  $("#breadcrumb").textContent = pages[page][0];
  $("#nav").innerHTML = Object.entries(pages)
    .map(
      ([key, [name]]) =>
        `${key === "config" ? '<p class="nav-label nav-settings">SYSTEM</p>' : ""}<a href="#${key}" class="nav-item ${page === key ? "selected" : ""}" ${page === key ? 'aria-current="page"' : ""}>${icon(key)}<span>${name}</span>${key === "activity" ? '<span class="live-dot"></span>' : ""}</a>`,
    )
    .join("");
  $("#main").innerHTML =
    `<div class="page-heading"><div><div class="eyebrow">${page === "config" ? "SYSTEM" : "WORKSPACE"}</div><h1>${pages[page][0]}</h1><p>${pages[page][1]}</p></div><div id="page-actions"></div></div><div id="page-content">${pageMarkup()}</div>`;
  drawData();
}
function pageMarkup() {
  if (["skills", "reasoning", "orchestration", "health"].includes(page))
    return workbench.markup(page);
  if (page === "overview")
    return `<section id="agent-banner"></section><div id="metrics" class="metrics"></div><div class="overview-grid"><section class="panel"><div class="panel-heading"><h2>Recent activity <span class="live-dot"></span></h2><a href="#activity">View all ↗</a></div><div id="activity-list"></div></section><div class="stack"><section class="panel"><div class="panel-heading"><h2>Active goals</h2><a href="#goals">Manage ↗</a></div><div id="goal-list"></div></section><section class="panel"><div class="panel-heading"><h2>Latest improvement</h2><a href="#improvements">Inspect ↗</a></div><div id="improvement-list"></div></section></div></div><section class="architecture-note"><span class="mini-mark">Ω</span><div><strong>More than a conversation.</strong><p>Goals guide the work. Activity shows the process. Evidence makes it inspectable.</p></div><a href="#chat" class="button">Open conversation →</a></section>`;
  if (page === "chat")
    return `<section class="panel chat-panel"><div class="panel-heading"><h2>Omega <span class="badge">Workspace conversation</span></h2><span id="agent-label"></span></div><div id="messages" class="messages" role="log" aria-label="Conversation"></div><form id="chat-form" class="composer"><label class="sr-only" for="message">Message Omega</label><textarea id="message" name="message" required maxlength="12000" placeholder="Give Omega direction, ask a question, or explore an idea…" rows="3"></textarea><div class="composer-footer"><span>Enter to send · Shift + Enter for a new line</span><button class="button primary" type="submit">Send message ↑</button></div></form></section><section class="panel request-panel"><div class="panel-heading"><h2>Command lifecycle</h2><span class="muted">Each message has its own request ID</span></div><div id="requests"></div></section>`;
  if (page === "goals")
    return (
      `<div class="goals-layout"><section class="panel"><div class="panel-heading"><h2>Workspace goals</h2><span id="goal-count" class="muted"></span></div><div id="goal-list"></div></section><section class="panel new-goal"><div class="panel-heading"><h2>Create a goal</h2></div><form id="goal-form" class="form-body"><label for="goal-title">What should Omega work toward?</label><input id="goal-title" name="title" required maxlength="300" placeholder="A clear, measurable objective"><label for="goal-description">Context and success criteria</label><textarea id="goal-description" name="description" rows="6" maxlength="12000" placeholder="Describe the desired result and how to verify it."></textarea><p class="helper">Active goals are included in the agent’s next turn. Pause or finish them when priorities change.</p><button class="button primary" type="submit">+ Create goal</button></form></section></div>` +
      workbench.telosMarkup()
    );
  if (page === "config")
    return (
      workbench.markup(page) +
      `<section class="panel config-panel"><div class="panel-heading"><h2>Agent behavior</h2>${statusBadge("operator controls")}</div><form id="config-form" class="form-body"><div class="setting"><div><label for="autonomy">Autonomous activity</label><p>Allow Omega to continue working between messages. Turning this off takes effect before the next autonomous turn.</p></div><input id="autonomy" name="autonomy" type="checkbox" role="switch"></div><div class="setting"><div><label for="goal-context">Include active goals</label><p>Provide active goal titles and success criteria in the agent’s context.</p></div><input id="goal-context" name="goalContext" type="checkbox" role="switch"></div><p class="helper">Direct messages remain available while autonomy is paused. A running turn finishes before changes take effect.</p><button class="button primary" type="submit">Save configuration</button></form></section><section class="panel config-panel"><div class="panel-heading"><h2>Connection & protocol</h2></div><div id="config-info" class="form-body"></div></section>`
    );
  return `<section class="panel"><div class="panel-heading"><h2>${page === "activity" ? "Event journal" : page === "evidence" ? "Evidence library" : "Improvement results"}</h2><div class="filters"><label class="sr-only" for="search">Search records</label><input id="search" type="search" placeholder="Search records…"><label class="sr-only" for="filter">Filter records</label><select id="filter">${(page === "activity" ? ["all", "run", "request", "provider", "message", "goal", "evidence", "improvement", "config"] : page === "improvements" ? ["all", "improved", "unchanged", "regressed", "inconclusive"] : ["all", "tool_result", "agent_report"]).map((value) => `<option value="${value}">${value === "all" ? "All types" : value.replaceAll("_", " ")}</option>`).join("")}</select></div></div><div id="${page === "activity" ? "activity-list" : page === "evidence" ? "evidence-list" : "improvement-list"}"></div></section>`;
}
function actionButton(label, action, id = "", extra = "") {
  return `<button class="button small" data-action="${action}" data-id="${escape(id)}" ${extra}>${label}</button>`;
}
function drawData() {
  if (!$("#page-content")) return;
  const connected = connection === "connected";
  $("#sync-status").textContent = state
    ? `API v${state.api_version} · Event ${state.cursor} · ${connection === "connected" ? "Live" : "Last known state"}`
    : "Awaiting connection";
  $("#page-actions").innerHTML =
    page === "overview" && state
      ? `<button class="button" data-action="toggle-autonomy" ${!connected ? "disabled" : ""}>${state.config.autonomy_enabled ? "Ⅱ Pause autonomy" : "▷ Resume autonomy"}</button><a href="#goals" class="button primary">+ New goal</a>`
      : "";
  if (page === "overview") {
    $("#agent-banner").innerHTML = state
      ? `<div class="agent-banner"><div class="agent-orbit">Ω<span class="orbit-dot ${state.agent.status === "working" ? "working" : ""}"></span></div><div><div class="eyebrow">YOUR AGENT</div><h2>Omega is ${state.agent.connected ? (state.agent.status === "working" ? "working" : state.config.autonomy_enabled ? "ready" : "paused") : "offline"}.</h2><p>${!state.agent.connected ? "Start Omega with the dashboard channel to begin." : state.agent.status === "working" ? "An agent turn is in progress. Follow its evidence below." : state.config.autonomy_enabled ? "Listening for direction. Ready to continue autonomous work." : "Autonomous turns are paused. You can still send a message."}</p></div><div class="banner-meta"><span class="badge ${state.agent.connected ? "good" : ""}">${state.agent.connected ? "● Agent attached" : "○ No agent attached"}</span><small>Persistent state · Auditable activity</small></div></div>`
      : needConnection();
    const values = state
      ? [
          [
            state.goals.filter((g) => g.status === "active").length,
            "Active goals",
            "goals",
          ],
          [
            state.runs.filter((r) => r.kind === "autonomous").length,
            "Recent autonomous turns",
            "activity",
          ],
          [state.evidence.length, "Recent evidence records", "evidence"],
          [
            state.improvements.filter((i) => i.outcome === "improved").length,
            "Reported improvements",
            "improvements",
          ],
        ]
      : [
          [0, "Active goals", "goals"],
          [0, "Recent autonomous turns", "activity"],
          [0, "Recent evidence records", "evidence"],
          [0, "Reported improvements", "improvements"],
        ];
    $("#metrics").innerHTML = values
      .map(
        ([count, label, key]) =>
          `<a href="#${key}" class="metric"><div>${icon(key)}<span>↗</span></div><strong>${count.toString().padStart(2, "0")}</strong><span>${label}</span></a>`,
      )
      .join("");
  }
  if ($("#activity-list")) $("#activity-list").innerHTML = renderActivity();
  if ($("#goal-list")) $("#goal-list").innerHTML = renderGoals();
  if ($("#goal-count"))
    $("#goal-count").textContent = `${state?.goals.length || 0} total`;
  if ($("#evidence-list")) $("#evidence-list").innerHTML = renderEvidence();
  if ($("#improvement-list"))
    $("#improvement-list").innerHTML = renderImprovements();
  if (page === "chat") {
    const pane = $("#messages");
    const nearBottom =
      pane.scrollHeight - pane.scrollTop - pane.clientHeight < 80;
    pane.innerHTML = state?.messages.length
      ? state.messages
          .map(
            (message) =>
              `<article class="message ${message.role}"><div class="avatar">${message.role === "user" ? "Y" : "Ω"}</div><div class="message-content"><div class="message-heading"><strong>${message.role === "user" ? "You" : "Omega"}</strong><time>${date(message.at)}</time>${message.run_id ? `<button class="text-button" data-action="run" data-id="${escape(message.run_id)}">Inspect turn ↗</button>` : ""}</div><div class="message-text">${escape(message.text)}</div></div></article>`,
          )
          .join("")
      : empty(
          "Start a conversation",
          "Ask Omega a question or give it direction. Each message and its execution are tracked.",
          "chat",
        );
    if (nearBottom) pane.scrollTop = pane.scrollHeight;
    $("#agent-label").innerHTML = state ? statusBadge(state.agent.status) : "";
    $("#requests").innerHTML = state?.requests.filter(
      (r) => r.command === "chat.send",
    ).length
      ? state.requests
          .filter((r) => r.command === "chat.send")
          .slice(0, 12)
          .map(
            (r) =>
              `<div class="request-row"><code>${escape(r.id)}</code>${statusBadge(r.status)}${r.status === "accepted" ? actionButton("Cancel", "cancel-request", r.id) : ""}</div>`,
          )
          .join("")
      : '<p class="padded muted">No message commands yet.</p>';
  }
  if (page === "config") {
    if (document.activeElement?.closest("form")?.id !== "config-form") {
      $("#autonomy").checked = state?.config.autonomy_enabled ?? true;
      $("#goal-context").checked = state?.config.goal_context_enabled ?? true;
    }
    $("#config-info").innerHTML = state
      ? `<dl><dt>API</dt><dd>Omega v${state.api_version} · same origin</dd><dt>Activity transport</dt><dd>Authenticated event stream with cursor recovery</dd><dt>Instance</dt><dd><code>${escape(state.instance_id)}</code></dd><dt>Agent</dt><dd>${escape(state.agent.status)}</dd><dt>Credentials</dt><dd>Protected expiring session cookie</dd></dl><p class="helper">Provider credentials are managed on the Omega host and never returned by this API.</p>`
      : needConnection();
  }
  workbench.draw(page, state);
  for (const form of document.querySelectorAll("#main form"))
    for (const control of form.querySelectorAll(
      "button, input, textarea, select",
    ))
      control.disabled =
        !connected ||
        Boolean(
          control.dataset.setting &&
          state?.settings?.locked?.[control.dataset.setting],
        ) ||
        busyForms.has(form.id) ||
        (form.id === "chat-form" && !state?.agent.connected);
}
function renderActivity() {
  let events = state?.activity || [];
  if (page === "activity")
    events = events.filter(
      (e) =>
        (filter === "all" || e.type.split(".")[0] === filter) &&
        JSON.stringify(e).toLowerCase().includes(query),
    );
  else events = events.slice(0, 8);
  if (!events.length)
    return empty(
      "No activity yet",
      state
        ? "Agent turns and workspace changes will appear here."
        : "Connect to see what Omega is doing.",
    );
  return `<div class="activity-list">${events.map((e) => `<button class="activity-row" data-action="event" data-id="${e.seq}"><span class="event-icon ${e.type.includes("failed") ? "event-failed" : ""}">${icon({ goal: "goals", run: "activity", message: "chat", improvement: "improvements", config: "config", evidence: "evidence" }[e.type.split(".")[0]] || "activity")}</span><span class="activity-description"><strong>${escape(e.type.replaceAll(".", " · "))}</strong><span>${escape(e.data.title || e.data.command || e.data.text?.slice(0, 110) || e.data.message || e.data.kind || e.request_id || "Workspace activity")}</span></span><time title="${escape(e.at)}">${relative(e.at)}</time><span class="row-arrow">↗</span></button>`).join("")}</div>`;
}
function renderGoals() {
  const goals =
    page === "overview"
      ? state?.goals.filter((g) => g.status === "active").slice(0, 3)
      : state?.goals;
  if (!goals?.length)
    return empty(
      "Give the work a direction",
      "Create a goal with a clear outcome and success criteria.",
      "goals",
    );
  return goals
    .map(
      (g) =>
        `<article class="goal-card"><div class="goal-top"><span class="goal-dot ${g.status}"></span><h3>${escape(g.title)}</h3>${statusBadge(g.status)}</div>${g.description ? `<p>${escape(g.description)}</p>` : ""}<div class="goal-bottom"><span>${date(g.created_at)}</span>${page === "goals" && ["active", "paused"].includes(g.status) ? `<div class="row-actions">${actionButton(g.status === "active" ? "Pause" : "Resume", g.status === "active" ? "pause-goal" : "resume-goal", g.id)}${actionButton("Complete", "complete-goal", g.id)}${actionButton("Cancel", "cancel-goal", g.id)}</div>` : ""}</div></article>`,
    )
    .join("");
}
function renderEvidence() {
  const records = (state?.evidence || []).filter(
    (e) =>
      (filter === "all" || e.kind === filter) &&
      JSON.stringify(e).toLowerCase().includes(query),
  );
  if (!records.length)
    return empty(
      "Evidence starts with execution",
      "Tool results and explicit agent observations are recorded here.",
      "evidence",
    );
  return `<div class="evidence-grid">${records.map((e) => `<button class="evidence-card" data-action="evidence" data-id="${escape(e.id)}"><div class="card-meta">${icon("evidence")}<span>${escape(e.kind.replaceAll("_", " "))}</span>${statusBadge(e.status)}</div><h3>${escape(e.title)}</h3><p>${escape(e.content.slice(0, 180))}</p><div class="card-meta"><time>${date(e.at)}</time><span>Inspect evidence ↗</span></div></button>`).join("")}</div>`;
}
function renderImprovements() {
  let records = state?.improvements || [];
  records =
    page === "overview"
      ? records.slice(0, 1)
      : records.filter(
          (r) =>
            (filter === "all" || r.outcome === filter) &&
            JSON.stringify(r).toLowerCase().includes(query),
        );
  if (!records.length)
    return empty(
      "Progress needs proof",
      "Improvement reports appear with an outcome and linked evidence.",
      "improvements",
    );
  return records
    .map(
      (r) =>
        `<article class="improvement-card"><div class="card-meta"><span>AGENT REPORT</span>${statusBadge(r.outcome)}</div><h3>${escape(r.title)}</h3><p>${escape(r.summary)}</p><div class="evidence-links">${r.evidence_ids.map((id) => `<button class="button small" data-action="evidence" data-id="${escape(id)}">${icon("evidence")} Evidence ${escape(id.slice(0, 8))} ↗</button>`).join("")}</div><span class="helper">${date(r.at)} · Reported by Omega; inspect the supporting evidence.</span></article>`,
    )
    .join("");
}
async function inspect(title, record) {
  $("#inspector-title").textContent = title;
  $("#inspector-body").innerHTML =
    `<pre>${escape(JSON.stringify(record, null, 2))}</pre>`;
  if (!$("#inspector").open) $("#inspector").showModal();
}
async function perform(command, payload, success) {
  if (connection !== "connected") {
    notify("Connect to Omega before sending a command.", true);
    return false;
  }
  try {
    const result = await client.command(command, payload);
    notify(`${success} · Request ${result.request.id.slice(0, 8)}`);
    return true;
  } catch (error) {
    notify(
      `${error.message}${client.pending.has(error.requestId) ? " — awaiting confirmation; retry will use the same request ID." : ""}`,
      true,
    );
    return false;
  }
}
$("#connect").addEventListener("click", () => {
  if (["connected", "reconnecting", "connecting"].includes(connection)) {
    void client.logout().catch((error) => notify(error.message, true));
    notify("Disconnected. Showing the last known workspace state.");
  } else {
    $("#connection-error").hidden = true;
    $("#connection-dialog").showModal();
  }
});
$("#connection-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.target.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    await client.connect($("#token").value);
    $("#token").value = "";
    $("#connection-dialog").close();
    $("#notice").hidden = true;
  } catch (error) {
    notify(error.message, true);
    $("#connection-error").textContent = error.message;
    $("#connection-error").hidden = false;
  } finally {
    button.disabled = false;
  }
});
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const { action, id } = button.dataset;
  if (action.startsWith("wb-")) {
    try {
      await workbench.action(button);
    } catch (error) {
      notify(error.message, true);
    }
    return;
  }
  if (action === "discard-pending") {
    client.discardPending();
    notify(
      "Local retries cleared. Commands already accepted by Omega keep their recorded status.",
    );
    return;
  }
  if (action === "close-dialog") {
    button.closest("dialog").close();
    return;
  }
  if (action === "event") {
    const record = state.activity.find((e) => String(e.seq) === id);
    if (record) await inspect(record.type, record);
    return;
  }
  if (action === "run") {
    const record = state.runs.find((r) => r.id === id);
    await inspect(
      "Agent turn",
      record || {
        id,
        message:
          "This turn is outside the recent snapshot. Its records remain in the event journal.",
      },
    );
    return;
  }
  if (action === "evidence") {
    try {
      await inspect("Evidence record", await client.evidence(id));
    } catch (error) {
      notify(error.message, true);
    }
    return;
  }
  button.disabled = true;
  try {
    if (action === "retry") {
      await client.retryPending();
      notify("Pending commands confirmed.");
    }
    if (action === "toggle-autonomy")
      await perform(
        "config.update",
        { autonomy_enabled: !state.config.autonomy_enabled },
        "Autonomy setting updated",
      );
    if (action.endsWith("-goal"))
      await perform(
        "goal.set_status",
        {
          goal_id: id,
          status: {
            "pause-goal": "paused",
            "resume-goal": "active",
            "complete-goal": "completed",
            "cancel-goal": "cancelled",
          }[action],
        },
        "Goal updated",
      );
    if (action === "cancel-request")
      await perform(
        "request.cancel",
        { request_id: id },
        "Queued message cancelled",
      );
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = false;
  }
});
$("#main").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  if (busyForms.has(form.id)) return;
  busyForms.add(form.id);
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    if (form.id.startsWith("wb-")) {
      await workbench.submit(form);
      return;
    }
    if (form.id === "chat-form") {
      const sent = await perform(
        "chat.send",
        { text: form.elements.namedItem("message").value },
        "Message accepted",
      );
      if (sent) form.reset();
    } else if (form.id === "goal-form") {
      if (
        await perform(
          "goal.create",
          {
            title: form.elements.namedItem("title").value,
            description: form.elements.namedItem("description").value,
          },
          "Goal created",
        )
      )
        form.reset();
    } else if (form.id === "config-form")
      await perform(
        "config.update",
        {
          autonomy_enabled: form.elements.namedItem("autonomy").checked,
          goal_context_enabled: form.elements.namedItem("goalContext").checked,
        },
        "Configuration saved",
      );
  } catch (error) {
    notify(error.message, true);
  } finally {
    busyForms.delete(form.id);
    drawData();
  }
});
$("#main").addEventListener("input", (event) => {
  workbench.changed(event);
  if (event.target.id === "search") {
    query = event.target.value.toLowerCase();
    drawData();
  }
});
$("#main").addEventListener("change", (event) => {
  workbench.changed(event);
  if (event.target.id === "filter") {
    filter = event.target.value;
    drawData();
  }
});
$("#main").addEventListener("keydown", (event) => {
  if (
    event.target.id === "message" &&
    event.key === "Enter" &&
    !event.shiftKey &&
    !event.isComposing
  ) {
    event.preventDefault();
    if (!event.target.disabled && event.target.value.trim())
      event.target.form.requestSubmit();
  }
});
async function refreshWorkbench() {
  const response = await client.request("/workbench");
  Object.assign(state, await response.json());
  drawData();
}
workbench.initialize({
  getState: () => state,
  perform,
  inspect,
  notify,
  refresh: refreshWorkbench,
});
const healthRefresh = setInterval(() => {
  if (page === "health" && connection === "connected")
    void refreshWorkbench().catch(() => {});
}, 5000);
window.addEventListener("pagehide", () => clearInterval(healthRefresh));
window.addEventListener("hashchange", navigate);
window.addEventListener("pagehide", () => client.disconnect());
navigate();

// The container generates this one-click sign-in link; fragments never reach
// the server. Remove it from history immediately and keep the token in memory.
const bootstrap = new URLSearchParams(location.hash.slice(1));
if (bootstrap.has("connect")) {
  const token = bootstrap.get("connect");
  history.replaceState(null, "", "#overview");
  void client.connect(token).catch((error) => notify(error.message, true));
} else {
  void client.connect("").catch((error) => {
    if (error.status !== 401) notify(error.message, true);
  });
}
