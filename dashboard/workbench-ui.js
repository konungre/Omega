/** Operator interfaces; all execution goes through durable authenticated commands. */
const $ = (selector) => document.querySelector(selector);
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const badge = (status) =>
  `<span class="badge ${["ready", "active", "completed", "healthy"].includes(status) ? "good" : ["failed", "interrupted", "offline"].includes(status) ? "bad" : "amber"}">${escape(status)}</span>`;
const noRecords = (text) => `<p class="padded muted">${text}</p>`;
const panel = (title, content, extra = "") =>
  `<section class="panel workbench-panel"><div class="panel-heading"><h2>${title}</h2>${extra}</div>${content}</section>`;
const inspectButton = (collection, id) =>
  `<button class="button small" data-action="wb-inspect" data-collection="${collection}" data-id="${escape(id)}">${collection === "evidence" ? "Evidence ↗" : "Inspect result ↗"}</button>`;
const examples = {
  ona: "<robin --> bird>.\n<bird --> animal>.\n<robin --> animal>?",
  nal: "(|- ((--> robin bird) (stv 1.0 0.9)) ((--> bird animal) (stv 1.0 0.9)))",
  pln: "(|~ ((Implication (Inheritance Pingu (IntSet Feathered)) (Inheritance Pingu Bird)) (stv 1.0 0.9)) ((Inheritance Pingu (IntSet Feathered)) (stv 1.0 0.9)))",
};
function term(value) {
  return Array.isArray(value)
    ? "(" + value.map(term).join(" ") + ")"
    : String(value);
}
function reasoningOutput(output) {
  if (output?.error) return `<p class="error-text">${escape(output.error)}</p>`;
  if (output?.engine === "ona") {
    const answers = output.answers || [];
    return (
      (answers.length
        ? answers
            .map(
              (answer) =>
                `<div class="reasoning-conclusion"><code>${escape(answer.text.split(" creationTime=")[0].split(" Truth:")[0])}</code>${answer.truth ? `<span>Frequency ${Math.round(answer.truth.frequency * 100)}% · Confidence ${Math.round(answer.truth.confidence * 100)}%</span>` : ""}</div>`,
            )
            .join("")
        : '<p class="helper">No answer was derived within this inference budget.</p>') +
      `<p class="helper">${output.inputs?.length || 0} inputs · ${output.derivations?.length || 0} derivations · ${output.operations?.length || 0} proposed operations</p><details><summary>View engine transcript</summary><pre class="code-preview">${escape(output.raw || "")}</pre></details>`
    );
  }
  if (Array.isArray(output)) {
    const conclusions =
      output.length === 2 && Array.isArray(output[1]) && output[1][0] === "stv"
        ? [output]
        : output;
    if (
      conclusions.every(
        (item) =>
          Array.isArray(item) && Array.isArray(item[1]) && item[1][0] === "stv",
      )
    )
      return conclusions.length
        ? conclusions
            .map(
              ([statement, truth]) =>
                `<div class="reasoning-conclusion"><code>${escape(term(statement))}</code><span>Frequency ${Math.round(truth[1] * 100)}% · Confidence ${Math.round(truth[2] * 100)}%</span></div>`,
            )
            .join("")
        : '<p class="helper">This expression produced no conclusions.</p>';
  }
  return `<pre class="code-preview">${escape(JSON.stringify(output, null, 2))}</pre>`;
}
function telosReport(report) {
  let rows = report;
  while (Array.isArray(rows) && rows.length === 1 && Array.isArray(rows[0]))
    rows = rows[0];
  if (!Array.isArray(rows)) return noRecords("No report available.");
  if (typeof rows[0] === "string") rows = [rows];
  return rows
    .map((row) => {
      if (!Array.isArray(row)) return `<p>${escape(String(row))}</p>`;
      const [kind, first, second, third] = row;
      const a = escape(first),
        b = escape(second),
        c = escape(third);
      const message =
        kind === "conflict-between"
          ? `${a} conflicts with ${b}`
          : kind === "blocked"
            ? `${a} is blocked by ${c}`
            : kind === "aligns"
              ? `${a} aligns with ${c}`
              : kind === "subgoal"
                ? `${a} is a subgoal of ${c}`
                : kind === "collective-goal"
                  ? `${a} is a collective goal owned by ${b}`
                  : kind === "achieved-goal"
                    ? `${a} was achieved by ${b}`
                    : kind === "abandoned-goal"
                      ? `${a} was abandoned by ${b}`
                      : escape(row.join(" "));
      return `<p class="goal-card">${badge(kind === "conflict-between" ? "conflict" : kind === "blocked" ? "blocked" : "recorded")} ${message}</p>`;
    })
    .join("");
}
let api;
let stepNumber = 0;
export function initialize(options) {
  api = options;
}

export function markup(page) {
  if (page === "config")
    return panel(
      "Omega configuration",
      `<form id="wb-settings-form" class="form-body"><p class="helper">Changes are validated and saved persistently. Live settings apply between turns; deployment and provider changes require an agent restart. Credentials are managed separately.</p><div id="wb-settings-fields"></div><div class="row-actions"><button class="button primary" type="submit">Save Omega settings</button><button class="button" type="button" data-action="wb-restart">Restart agent</button></div><p id="wb-restart-note" class="helper"></p></form>`,
    );
  if (page === "skills")
    return `<div class="workbench-columns">${panel("Build a custom skill", `<form id="wb-skills-form" class="form-body"><label for="wb-skill-name">Skill name</label><input id="wb-skill-name" name="name" required pattern="[a-z][a-z0-9-]{1,63}" placeholder="greet-person"><label for="wb-skill-description">What does it do?</label><input id="wb-skill-description" name="description" required placeholder="Greet a person by name"><label for="wb-skill-parameters">Parameters (comma separated)</label><input id="wb-skill-parameters" name="parameters" placeholder="person"><label for="wb-skill-body">MeTTa function body</label><textarea id="wb-skill-body" class="code-input" name="body" rows="7" required placeholder='(strings-concat ("Hello " $person))'></textarea><label class="inline-label"><input type="checkbox" name="enabled" checked> Activate after saving</label><p class="helper">Custom skills execute inside Omega with its configured permissions. Test with deliberate inputs before using a skill in autonomous work.</p><pre id="wb-skill-preview" class="code-preview">Your function definition will appear here.</pre><button class="button primary" type="submit">Save skill</button></form>`)}<div class="stack">${panel("Your skills", '<div id="wb-custom-skills"></div>')}${panel("Test a registered skill", `<form id="wb-skill-test-form" class="form-body"><label for="wb-test-skill">Skill</label><select id="wb-test-skill" name="skill"></select><div id="wb-test-arguments"></div><button class="button primary" type="submit">Run test</button></form><div id="wb-skill-test-result"></div>`)}</div></div>${panel("Installed skill catalog", '<div id="wb-catalog" class="catalog-grid"></div>')}`;
  if (page === "reasoning")
    return `<div class="workbench-columns">${panel("Reasoning workspace", `<form id="wb-reasoning-form" class="form-body"><label for="wb-engine">Reasoning engine</label><select id="wb-engine" name="engine"><option value="ona">ONA · temporal and procedural</option><option value="nal">NAL · uncertain symbolic inference</option><option value="pln">PLN · probabilistic logic</option></select><div class="field-pair"><div><label for="wb-session">ONA session</label><input id="wb-session" name="session" value="default" required></div><div><label for="wb-cycles">Inference cycles</label><input id="wb-cycles" name="cycles" type="number" min="0" max="1000" value="100"></div></div><label for="wb-reasoning-input">Premises, question, or expression</label><textarea id="wb-reasoning-input" class="code-input" name="input" rows="10" required>${escape(examples.ona)}</textarea><div class="row-actions"><button class="button primary" type="submit">Run reasoning</button><button class="button" type="button" data-action="wb-example">Load example</button><button class="button" type="button" data-action="wb-reset-ona">Reset ONA session</button></div><p class="helper">ONA keeps beliefs and temporal state per session across requests and restarts. NAL and PLN evaluate explicit expressions in the agent. Results are formal engine output, with linked evidence.</p></form>`)}${panel("Results and proof records", '<div id="wb-reasoning-results"></div>')}</div>`;
  if (page === "orchestration")
    return `<div class="workbench-columns">${panel("Create a workflow", `<form id="wb-orchestration-form" class="form-body"><label for="wb-workflow-name">Workflow name</label><input id="wb-workflow-name" name="name" required placeholder="Reason, validate, and report"><div id="wb-workflow-steps"></div><div class="row-actions"><button class="button" type="button" data-action="wb-add-step">+ Add step</button><button class="button primary" type="submit">Save workflow</button></div><p class="helper">Steps may run reasoning, invoke a registered skill, or send a message. Dependencies determine order. Pausing or cancelling takes effect between steps; a running tool finishes first.</p></form>`)}<div class="stack">${panel("Saved workflows", '<div id="wb-workflows"></div>')}${panel("Workflow runs", '<div id="wb-workflow-runs"></div>')}</div></div>`;
  if (page === "health")
    return panel(
      "Agent health and installed services",
      '<div id="wb-health" class="form-body"></div>',
      '<button class="button small" data-action="wb-refresh">Refresh status</button>',
    );
  return "";
}

export function telosMarkup() {
  return `<div class="workbench-columns telos-workspace">${panel("Telos goal graph", '<div id="wb-telos-graph"></div>')}${panel("Add a formal goal", `<form id="wb-telos-goal-form" class="form-body"><label for="wb-telos-id">Goal identifier</label><input id="wb-telos-id" name="goal_id" required placeholder="fair-access"><label for="wb-telos-owner">Stakeholder</label><input id="wb-telos-owner" name="owner" required placeholder="commons"><div class="field-pair"><div><label for="wb-telos-scope">Scope</label><select id="wb-telos-scope" name="scope"><option value="individual">Individual</option><option value="collective">Collective</option></select></div><div><label for="wb-telos-status">Status</label><select id="wb-telos-status" name="status"><option>active</option><option>proposed</option><option>achieved</option><option>abandoned</option></select></div></div><button class="button primary" type="submit">Add Telos goal</button></form>`)}${panel("Connect goals", `<form id="wb-telos-rel-form" class="form-body"><label for="wb-telos-source">Source goal</label><select id="wb-telos-source" name="source"></select><label for="wb-telos-relation">Relationship</label><select id="wb-telos-relation" name="relation"><option>supports</option><option>conflicts</option><option>subsumes</option><option>depends-on</option></select><label for="wb-telos-target">Target goal</label><select id="wb-telos-target" name="target"></select><button class="button primary" type="submit">Add relationship</button></form>`)}${panel("Conflicts, blockers, and alignment", '<div id="wb-telos-report" class="form-body"></div>')}</div>`;
}

function fillSelect(selector, items) {
  const select = $(selector);
  if (!select) return;
  const selected = select.value;
  const next = items
    .map(
      (item) =>
        `<option value="${escape(item.value)}">${escape(item.label)}</option>`,
    )
    .join("");
  if (select.innerHTML !== next) {
    select.innerHTML = next;
    if (items.some((item) => item.value === selected)) select.value = selected;
  }
}
function field(key, spec, value) {
  const attrs = `name="${escape(key)}" data-setting="${escape(key)}" id="setting-${escape(key)}"`;
  const control = spec.enum
    ? `<select ${attrs}>${spec.enum.map((option) => `<option ${value === option ? "selected" : ""}>${escape(option)}</option>`).join("")}</select>`
    : spec.type === "boolean"
      ? `<input ${attrs} type="checkbox" ${value ? "checked" : ""}>`
      : `<input ${attrs} type="${spec.type === "string" ? "text" : "number"}" value="${escape(value)}" ${spec.minimum !== undefined ? `min="${spec.minimum}" max="${spec.maximum}"` : ""} ${spec.type === "number" ? 'step="any"' : ""}>`;
  return `<div class="setting-field"><label for="setting-${escape(key)}">${escape(key)} <span class="helper">${spec.live ? "Applies between turns" : "Restart required"}</span></label>${control}<small class="muted" data-effective="${escape(key)}"></small></div>`;
}
export function draw(page, state) {
  if (!state) return;
  if (page === "config" && $("#wb-settings-fields")) {
    const fields = $("#wb-settings-fields");
    if (!fields.dataset.ready && state.settings) {
      const groups = [
        ...new Set(
          Object.values(state.settings.schema).map((spec) => spec.group),
        ),
      ];
      fields.innerHTML = groups
        .map(
          (group) =>
            `<details ${group !== "Deployment and integrations" ? "open" : ""}><summary>${escape(group)}</summary><div class="settings-grid">${Object.entries(
              state.settings.schema,
            )
              .filter(([, spec]) => spec.group === group)
              .map(([key, spec]) =>
                field(key, spec, state.settings.values[key]),
              )
              .join("")}</div></details>`,
        )
        .join("");
      fields.dataset.ready = "true";
    }
    for (const node of fields.querySelectorAll("[data-effective]")) {
      const key = node.dataset.effective;
      const lock = state.settings?.locked?.[key];
      node.textContent = `Current: ${String(state.settings?.effective[key] ?? "—")}${lock ? ` · Set by ${lock}` : ""}`;
      const control = document.getElementById(`setting-${key}`);
      if (control) control.disabled = Boolean(lock);
    }
    $("#wb-restart-note").textContent = state.settings?.pending_restart.length
      ? `Restart needed for: ${state.settings.pending_restart.join(", ")}`
      : "Saved live settings apply on the next agent iteration.";
    const button = $('[data-action="wb-restart"]');
    button.hidden = !state.managed_restart;
  }
  if (page === "skills") {
    $("#wb-custom-skills").innerHTML = state.skills?.length
      ? state.skills
          .map(
            (skill) =>
              `<article class="goal-card"><div class="goal-top"><h3>${escape(skill.name)}</h3>${badge(skill.state)}${badge(skill.enabled ? "active" : "disabled")}</div><p>${escape(skill.description)}</p><div class="row-actions"><button class="button small" data-action="wb-edit-skill" data-id="${escape(skill.id)}">Edit</button><button class="button small" data-action="wb-toggle-skill" data-id="${escape(skill.id)}">${skill.enabled ? "Disable" : "Activate"}</button></div>${skill.error ? `<pre class="code-preview">${escape(JSON.stringify(skill.error))}</pre>` : ""}</article>`,
          )
          .join("")
      : noRecords("No custom skills yet. Build one on the left.");
    $("#wb-catalog").innerHTML = (state.skill_catalog || [])
      .map(
        (skill) =>
          `<article><h3>${escape(skill.name)}</h3><p>${escape(skill.description)}</p><code>${escape(skill.parameters.join(", ")) || "No parameters"}</code></article>`,
      )
      .join("");
    fillSelect(
      "#wb-test-skill",
      (state.skill_catalog || []).map((skill) => ({
        value: skill.name,
        label: skill.name,
      })),
    );
    const selected = $("#wb-test-skill").value;
    if ($("#wb-test-arguments").dataset.skill !== selected)
      testArguments(selected, state);
    const result = state.evidence.find((e) => e.title === "skill.test");
    $("#wb-skill-test-result").innerHTML = result
      ? `<div class="form-body">${badge(result.status)}<pre class="code-preview">${escape(result.content)}</pre>${inspectButton("evidence", result.id)}</div>`
      : "";
  }
  if (page === "reasoning")
    $("#wb-reasoning-results").innerHTML = state.reasoning?.length
      ? state.reasoning
          .map(
            (result) =>
              `<article class="goal-card"><div class="goal-top"><h3>${escape(result.engine.toUpperCase())} ${result.session ? `· ${escape(result.session)}` : ""}</h3>${badge(result.status)}</div>${result.output !== undefined ? reasoningOutput(result.output) : `<pre class="code-preview">${escape(result.input)}</pre>`}${inspectButton("reasoning", result.id)}${result.evidence_id ? inspectButton("evidence", result.evidence_id) : ""}</article>`,
          )
          .join("")
      : noRecords(
          "Submit premises to inspect real engine output and evidence.",
        );
  if (page === "orchestration") {
    if (!$("#wb-workflow-steps").children.length) addStep();
    $("#wb-workflows").innerHTML = state.orchestrations?.length
      ? state.orchestrations
          .map(
            (workflow) =>
              `<article class="goal-card"><h3>${escape(workflow.name)}</h3><p>${workflow.nodes.length} ${workflow.nodes.length === 1 ? "step" : "steps"}</p><button class="button primary small" data-action="wb-start-workflow" data-id="${escape(workflow.id)}">Run workflow →</button></article>`,
          )
          .join("")
      : noRecords("Save a workflow to run its steps.");
    $("#wb-workflow-runs").innerHTML = state.orchestration_runs?.length
      ? state.orchestration_runs
          .map(
            (run) =>
              `<article class="goal-card"><div class="goal-top"><h3>${escape(run.name)}</h3>${badge(run.status)}</div><ol class="workflow-progress">${run.nodes.map((node) => `<li><strong>${escape(node.id)}</strong> ${escape(node.kind)} ${badge(node.status)}${node.evidence_id ? inspectButton("evidence", node.evidence_id) : ""}</li>`).join("")}</ol><div class="row-actions">${["running", "paused", "interrupted"].includes(run.status) ? `<button class="button small" data-action="wb-run-status" data-status="${run.status === "running" ? "paused" : "running"}" data-id="${escape(run.id)}">${run.status === "running" ? "Pause" : "Resume"}</button><button class="button small" data-action="wb-run-status" data-status="cancelled" data-id="${escape(run.id)}">Cancel</button>` : ""}${inspectButton("orchestration_runs", run.id)}</div>${run.status === "interrupted" ? '<p class="helper">Inspect evidence before resuming; a step may have taken effect before interruption.</p>' : ""}</article>`,
          )
          .join("")
      : noRecords("Workflow activity will appear here.");
  }
  if (page === "goals" && $("#wb-telos-graph")) {
    const goals = state.telos?.goals || [];
    $("#wb-telos-graph").innerHTML = goals.length
      ? goals
          .map(
            (goal) =>
              `<article class="goal-card"><div class="goal-top"><h3>${escape(goal.id)}</h3>${badge(goal.status)}</div><p>${escape(goal.scope)} · ${escape(goal.owner)}</p><div class="row-actions">${[
                "active",
                "proposed",
                "achieved",
                "abandoned",
              ]
                .filter(
                  (status) =>
                    !goal.id.startsWith("operator-") && status !== goal.status,
                )
                .map(
                  (status) =>
                    `<button class="button small" data-action="wb-telos-status" data-id="${escape(goal.id)}" data-status="${status}">${status === "active" ? "Activate" : status === "achieved" ? "Achieve" : status === "abandoned" ? "Abandon" : "Propose"}</button>`,
                )
                .join("")}</div></article>`,
          )
          .join("")
      : noRecords(
          "The Telos plugin maintains stakeholder goals, dependencies, conflicts, and alignment.",
        );
    const choices = goals.map((goal) => ({ value: goal.id, label: goal.id }));
    fillSelect("#wb-telos-source", choices);
    fillSelect("#wb-telos-target", choices);
    const report = state.telos?.report || [];
    $("#wb-telos-report").innerHTML = report.length
      ? `${telosReport(report)}<details><summary>Formal report</summary><pre class="code-preview">${escape(JSON.stringify(report, null, 2))}</pre></details><div>${(state.telos.relations || []).map((rel) => `<p class="helper">${escape(rel.source)} → ${escape(rel.relation)} → ${escape(rel.target)}</p>`).join("")}</div>`
      : '<p class="muted">No conflicts or blockers have been derived.</p>';
  }
  if (page === "health") {
    const health = state.health || {};
    const agentState = !state.agent?.connected
      ? "offline"
      : state.agent.status === "working"
        ? "working"
        : state.config?.autonomy_enabled
          ? "ready"
          : "paused";
    $("#wb-health").innerHTML =
      `<div class="metrics"><div class="metric"><strong>${escape(agentState)}</strong><span>Agent status</span></div><div class="metric"><strong>${health.current_iteration || 0}</strong><span>Loop iteration</span></div><div class="metric"><strong>${health.uptime_seconds || 0}s</strong><span>Uptime</span></div><div class="metric"><strong>${state.ona?.available ? "Ready" : "Unavailable"}</strong><span>Native ONA</span></div></div><dl><dt>Health</dt><dd>${badge(health.healthy ? "healthy" : "offline")}</dd><dt>Last activity</dt><dd>${escape(health.last_activity_at || "No heartbeat yet")}</dd><dt>Version</dt><dd>${escape(health.version || "unknown")}</dd><dt>Model provider</dt><dd>${escape(state.settings?.effective.provider || "Unknown")} · ${escape(state.settings?.effective.model || "")}</dd><dt>Telos Goals</dt><dd>${state.telos?.goals?.length || 0} goals · ${state.telos?.relations?.length || 0} ${(state.telos?.relations?.length || 0) === 1 ? "relation" : "relations"}</dd></dl><h3>ONA sessions</h3><pre class="code-preview">${escape(JSON.stringify(state.ona?.sessions || [], null, 2))}</pre><p class="helper">Health comes from the installed Health/Status plugin’s actual loop heartbeat. Docker uses the same plugin for its health check.</p>`;
  }
}

function testArguments(name, state) {
  const skill = state.skill_catalog.find((skill) => skill.name === name);
  const container = $("#wb-test-arguments");
  if (!container) return;
  container.dataset.skill = name;
  container.innerHTML = (skill?.parameters || [])
    .map(
      (parameter, index) =>
        `<label for="wb-test-${index}">${escape(parameter)}</label><input id="wb-test-${index}" name="arg-${index}" required>`,
    )
    .join("");
}
function skillStepArguments(name) {
  const skill = api
    .getState()
    ?.skill_catalog?.find((item) => item.name === name);
  if (!skill)
    return '<p class="helper">Activate a skill before adding this step.</p>';
  return (
    skill.parameters
      .map(
        (parameter) =>
          `<label>${escape(parameter)}<input data-step-arg required placeholder="Text for ${escape(parameter)}"></label>`,
      )
      .join("") || '<p class="helper">This skill needs no arguments.</p>'
  );
}
function stepContent(kind) {
  const catalog = api.getState()?.skill_catalog || [];
  if (kind === "skill")
    return `<label>Registered skill<select data-step-skill>${catalog.map((skill) => `<option value="${escape(skill.name)}">${escape(skill.name)}</option>`).join("")}</select></label><div data-step-arguments>${skillStepArguments(catalog[0]?.name)}</div>`;
  if (kind === "message")
    return '<label>Message<textarea data-step-message rows="3" required></textarea></label>';
  return `<div class="field-pair"><label>Engine<select data-step-engine><option value="ona">ONA</option><option value="nal">NAL</option><option value="pln">PLN</option></select></label><label>ONA session<input data-step-session value="default" required></label><label>Cycles<input data-step-cycles type="number" min="0" max="1000" value="100"></label></div><label>Reasoning input<textarea data-step-input class="code-input" rows="5" required>${escape(examples.ona)}</textarea></label>`;
}
function addStep() {
  const holder = $("#wb-workflow-steps");
  if (!holder) return;
  const previous = [...holder.querySelectorAll(".workflow-step")].map(
    (node) => node.dataset.id,
  );
  const id = "step-" + ++stepNumber;
  const card = document.createElement("section");
  card.className = "workflow-step";
  card.dataset.id = id;
  card.innerHTML = `<div class="goal-top"><h3>${id}</h3><button class="button small" type="button" data-action="wb-remove-step">Remove</button></div><label>Step type<select data-step-kind><option value="reasoning">Run reasoning</option><option value="skill">Run a skill</option><option value="message">Send a message</option></select></label><label>Run after<select data-step-dependencies multiple>${previous.map((name, index) => `<option value="${name}" ${index === previous.length - 1 ? "selected" : ""}>${name}</option>`).join("")}</select></label><div class="step-content">${stepContent("reasoning")}</div>`;
  holder.appendChild(card);
}
export function changed(event) {
  if (event.target.matches("[data-step-engine]")) {
    event.target
      .closest(".workflow-step")
      .querySelector("[data-step-input]").value = examples[event.target.value];
  }
  if (event.target.matches("[data-step-skill]")) {
    event.target
      .closest(".workflow-step")
      .querySelector("[data-step-arguments]").innerHTML = skillStepArguments(
      event.target.value,
    );
  }
  if (event.target.id === "wb-test-skill")
    testArguments(event.target.value, api.getState());
  if (event.target.id === "wb-engine")
    $("#wb-reasoning-input").value = examples[event.target.value];
  if (event.target.matches("[data-step-kind]"))
    event.target
      .closest(".workflow-step")
      .querySelector(".step-content").innerHTML = stepContent(
      event.target.value,
    );
  if (event.target.closest("#wb-skills-form")) {
    const form = $("#wb-skills-form");
    const field = (name) => form.elements.namedItem(name).value;
    $("#wb-skill-preview").textContent =
      `(= (${field("name") || "your-skill"} ${field("parameters")
        .split(",")
        .map((p) => p.trim())
        .filter(Boolean)
        .map((p) => "$" + p)
        .join(" ")})\n   ${field("body") || "(nop)"})`;
  }
}

export async function submit(form) {
  const value = (name) => form.elements.namedItem(name).value;
  if (form.id === "wb-settings-form") {
    const state = api.getState();
    const values = {};
    for (const control of form.querySelectorAll("[data-setting]")) {
      if (state.settings.locked?.[control.dataset.setting]) continue;
      const key = control.dataset.setting,
        spec = state.settings.schema[key];
      const next =
        spec.type === "boolean"
          ? control.checked
          : ["integer", "number"].includes(spec.type)
            ? Number(control.value)
            : control.value;
      if (next !== state.settings.values[key]) values[key] = next;
    }
    if (!Object.keys(values).length) {
      api.notify("No configuration changes to save.");
      return true;
    }
    return api.perform(
      "settings.update",
      values,
      "Omega settings queued for application",
    );
  }
  if (form.id === "wb-skills-form")
    return api.perform(
      "skill.save",
      {
        name: value("name"),
        description: value("description"),
        parameters: value("parameters")
          .split(",")
          .map((p) => p.trim())
          .filter(Boolean),
        body: value("body"),
        enabled: form.elements.namedItem("enabled").checked,
      },
      "Skill update queued",
    );
  if (form.id === "wb-skill-test-form") {
    const skill = api
      .getState()
      .skill_catalog.find((s) => s.name === value("skill"));
    return api.perform(
      "skill.test",
      {
        name: skill.name,
        arguments: skill.parameters.map((_, index) => value("arg-" + index)),
      },
      "Skill test queued",
    );
  }
  if (form.id === "wb-reasoning-form")
    return api.perform(
      "reasoning.run",
      {
        engine: value("engine"),
        input: value("input"),
        session: value("session"),
        cycles: Number(value("cycles")),
      },
      "Reasoning queued",
    );
  if (form.id === "wb-telos-goal-form")
    return api.perform(
      "telos.goal.add",
      Object.fromEntries(new FormData(form)),
      "Telos goal queued",
    );
  if (form.id === "wb-telos-rel-form")
    return api.perform(
      "telos.relation.add",
      Object.fromEntries(new FormData(form)),
      "Goal relationship queued",
    );
  if (form.id === "wb-orchestration-form") {
    const nodes = [...form.querySelectorAll(".workflow-step")].map((step) => {
      const kind = step.querySelector("[data-step-kind]").value;
      const payload =
        kind === "reasoning"
          ? {
              engine: step.querySelector("[data-step-engine]").value,
              input: step.querySelector("[data-step-input]").value,
              session: step.querySelector("[data-step-session]").value,
              cycles: Number(step.querySelector("[data-step-cycles]").value),
            }
          : kind === "skill"
            ? {
                name: step.querySelector("[data-step-skill]").value,
                arguments: [...step.querySelectorAll("[data-step-arg]")].map(
                  (input) => input.value,
                ),
              }
            : { text: step.querySelector("[data-step-message]").value };
      return {
        id: step.dataset.id,
        kind,
        depends_on: [
          ...step.querySelector("[data-step-dependencies]").selectedOptions,
        ].map((option) => option.value),
        payload,
      };
    });
    return api.perform(
      "orchestration.save",
      { id: crypto.randomUUID(), name: value("name"), nodes },
      "Workflow saved",
    );
  }
  return false;
}
export async function action(button) {
  const state = api.getState(),
    { action, id, status, collection } = button.dataset;
  if (action === "wb-add-step") {
    addStep();
    return;
  }
  if (action === "wb-remove-step") {
    const step = button.closest(".workflow-step");
    const removed = step.dataset.id;
    step.remove();
    document
      .querySelectorAll("[data-step-dependencies] option")
      .forEach((option) => {
        if (option.value === removed) option.remove();
      });
    return;
  }
  if (action === "wb-example") {
    $("#wb-reasoning-input").value = examples[$("#wb-engine").value];
    return;
  }
  if (action === "wb-inspect") {
    const record = state[collection]?.find((record) => record.id === id);
    if (record) await api.inspect("Result and evidence", record);
    return;
  }
  if (action === "wb-refresh") {
    await api.refresh();
    return;
  }
  if (action === "wb-edit-skill") {
    const skill = state.skills.find((skill) => skill.id === id),
      form = $("#wb-skills-form");
    for (const key of ["name", "description", "body"])
      form.elements.namedItem(key).value = skill[key];
    form.elements.namedItem("parameters").value = skill.parameters.join(", ");
    form.elements.namedItem("enabled").checked = skill.enabled;
    changed({ target: form.elements.namedItem("body") });
    return;
  }
  button.disabled = true;
  try {
    if (action === "wb-toggle-skill") {
      const skill = state.skills.find((skill) => skill.id === id);
      await api.perform(
        "skill.set_enabled",
        { name: id, enabled: !skill.enabled },
        "Skill activation change queued",
      );
    }
    if (action === "wb-reset-ona")
      await api.perform(
        "reasoning.reset",
        { session: $("#wb-session").value },
        "ONA reset queued",
      );
    if (action === "wb-telos-status")
      await api.perform(
        "telos.goal.status",
        { goal_id: id, status },
        "Telos goal change queued",
      );
    if (action === "wb-start-workflow")
      await api.perform(
        "orchestration.start",
        { workflow_id: id },
        "Workflow started",
      );
    if (action === "wb-run-status")
      await api.perform(
        "orchestration.set_status",
        { run_id: id, status },
        "Workflow state updated",
      );
    if (action === "wb-restart")
      await api.perform(
        "system.restart",
        {},
        "Agent restart requested; the dashboard will reconnect",
      );
  } finally {
    button.disabled = false;
  }
}
