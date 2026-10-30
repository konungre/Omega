# Omega dashboard and operator API

The dedicated dashboard includes conversation, autonomous activity, goals,
evidence, improvement reports, and operator configuration. It is served by
Omega itself. The browser never calls a model provider or the OpenClaw Gateway.

The interface adapts OpenClaw's dark theme tokens and sidebar/control-panel
patterns. See `dashboard/README.md` for provenance and its retained MIT license.

## Run the bundled image

Follow the [single-image quickstart](../README.md#local-dashboard-one-docker-image). The container bundles SmolLM3, ONA, Telos Goals, and Health. It generates a token and prints a localhost sign-in link. The link uses a URL fragment, which is removed immediately after reading; the fragment is not sent to the HTTP server. Treat the link as a credential.

After authentication the dashboard uses an eight-hour HttpOnly, SameSite=Strict cookie. Refreshing reconnects and recovers activity. Disconnect clears the browser cookie. The root token is never written to browser storage. The memory directory retains the generated root token for container restarts.

The local provider keeps conversational replies separate from action requests and passes visible replies through Omega’s normal `send` skill. Like any small local model, it can choose an unsuitable tool; inspect activity and evidence for consequential work.

For source installations in PeTTa:

```sh
export OMEGA_DASHBOARD_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
sh run.sh run.metta commchannel=dashboard
```

Open <http://127.0.0.1:8088> and enter the token. Source installations need the local model service and native ONA executable configured separately; Docker includes them.

| Setting | Default | Meaning |
| --- | --- | --- |
| `provider` | `SmolLM3` | Bundled local model provider. |
| `commchannel` | `dashboard` | Attach the API to the agent. |
| `dashboardHost` | `127.0.0.1` | Listener address; Docker binds inside its container. |
| `dashboardPort` | `8088` | Dashboard HTTP port. |
| `memoryDirectory` | `memory` | Durable agent, dashboard, ONA, and operator configuration state. |

The API/UI use Python's standard library and require no JavaScript build. Normal agent execution requires PeTTa and Omega's dependencies. The token bypasses the configuration resolver so it is not logged as a configuration value. Remote access requires a TLS reverse proxy preserving the request host and disabling response buffering on `/api/v1/events`. Cross-origin requests are rejected. The dashboard shares the agent's host trust boundary.

## Workbench controls

**Configuration** edits the public typed configuration schema. Changes persist to `operator-config.json`; live settings are applied on the agent thread, and other fields take effect after restart. Docker provides a **Restart agent** control. Command-line and environment values retain precedence. Credentials are configured outside the public editor. Changing a storage path does not migrate its data.

**Skills** builds named MeTTa functions with declared string parameters, a description, one expression body, and an activation switch. Save registers the definition on the agent thread. Test invokes the registered skill and retains evidence. Active custom skills restore after restart; disabling removes the skill from the agent catalog. Authenticated custom code executes with the same privileges as Omega's own skills.

**Reasoning** runs NAL, PLN, or native ONA and retains its output and evidence. ONA sessions persist independently and can be reset. [Native ONA reference](./reference-lib-ona.md).

**Orchestration** saves dependency graphs of reasoning, skill, and message steps. Run, pause, resume, cancel, and per-step evidence are available. [Workflow semantics](./reference-orchestration.md#dashboard-workflows).

**Goals** includes the bundled Telos plugin's individual/collective goals, owners, statuses, relations, conflict and alignment reports. Operator goals are mirrored into Telos with an `operator-` prefix. Operator lifecycle controls own those mirrored statuses; formal Telos goals can also be created directly. The graph is persisted and restored into the real plugin after restart.

**Health & status** displays the bundled plugin's actual heartbeat and process metrics, model selection, and ONA sessions. Docker's health check uses the plugin endpoint. Health is a runtime observation, not proof that a model's answer is correct.

## Inspect without a running agent

From the Omega repository:

```sh
export OMEGA_DASHBOARD_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 -m omega_api --database /tmp/omega-inspection.sqlite3
```

This serves the real dashboard with an explicit **agent offline** status. Goal
and configuration changes work; sending a chat message returns `503 agent_offline`.
It is not a simulated agent. A database has one active API owner. A second process
using the same file fails before recovery; stop its owner before opening it here.

## Operator behavior

- **Conversation:** each message gets a request ID, a queued/running/terminal
  status, and a correlated first agent turn. Queued messages can be cancelled.
  A running tool call is not forcibly cancelled. Identical consecutive messages
  and replies are preserved on the dashboard channel.
- **Activity:** ordered lifecycle records stream live and replay on reconnect.
  Both direct conversation turns and autonomous turns appear.
- **Goals:** creation, pause, resume, completion, and cancellation are durable.
  Up to 20 active goals, bounded to 30,000 context characters, are supplied in the
  next turn's context. Completed/cancelled goals are terminal; create a new goal
  to restart work. Goal changes guide the model; pausing a goal does not undo or
  kill an already-running operation. There is no second competing goal scheduler.
- **Evidence:** each executed tool produces a record with its result, request ID,
  and run ID. Results are capped at 50,000 characters and carry a truncation flag.
  Explicit observations can also be reported by the agent. Tool success reflects
  the loop's execution result, not independent validation of a claim.
- **Improvements:** structured agent reports record improved, unchanged,
  regressed, or inconclusive outcomes and reference existing evidence. They are
  clearly labeled agent reports. Ordinary tool calls do not fabricate measured
  improvements or proof of correctness.
- **Configuration:** typed public Omega settings, autonomy, and active-goal context can be edited.
  Autonomy pause is cooperative at turn boundaries. Direct messages still run
  while autonomy is paused. Provider credentials cannot be read or written through this surface.

An HTTP command's acceptance means it was durably recorded, not that the model
finished the work. A chat request completes after its first consuming iteration
and its tools; later autonomous iterations have their own run IDs and no chat
request ID. A provider/loop execution error marks that run and request failed.
A control command, such as memory export, may complete without a provider turn.

## Agent reporting skills

These are registered only when `commchannel=dashboard`:

```metta
(dashboard-report-evidence "{\"title\":\"Baseline\",\"content\":\"Tests: 12 passed\"}")
(dashboard-report-improvement "{\"title\":\"Recall evaluation\",\"summary\":\"Score rose from 0.6 to 0.8\",\"outcome\":\"improved\",\"evidence_ids\":[\"<existing-evidence-id>\"]}")
```

The evidence skill returns the record and ID. Improvement references must resolve
to existing evidence records. Reports require an active agent turn. Both skills
return a JSON error for invalid reports; their content is data, never evaluated
MeTTa. The event journal is an audit of what the agent did/reported, not a
cryptographically tamper-proof record against the agent's host permissions.

## Persistence and recovery

SQLite transactions commit a command, its projection changes, and lifecycle
events together. Request IDs deduplicate retries; reusing an ID with different
content returns `409 request_conflict`. The API does not re-execute a command
just because its response was lost. Keep IDs unique across operators/tabs.

After a process restart, queued messages remain queued. Running requests and
runs become **interrupted**. They are not automatically executed again: an
external tool effect might already have happened. Inspect evidence before
submitting new work. The database instance ID and event sequence survive restart.

The browser retries uncertain commands with the same envelope and request ID.
Pending command payloads are kept in tab-scoped `sessionStorage` to survive
reload; tokens are never stored there. A definitive client/API validation error
removes its pending envelope. Network/server failures retain it for retry.
Pending retries are bound to their database instance ID; replacing the database
requires inspecting effects and clearing local retries before submitting new
work. Clearing retries does not cancel already accepted server commands.
Disconnect aborts the subscription and clears the token; reconnect starts with
an atomic snapshot and then subscribes beyond its cursor. A broken activity
stream resumes at its last processed sequence with exponential backoff.

Snapshots include all goals, the last 200 messages, and the latest 100 runs,
evidence records, improvement reports, requests, and activity events. Live views
retain a recent window. The durable journal is unpruned and can be paged through
the API for older records; an evidence ID can also be fetched directly. Plan
storage/backups for your workload. Stop the API before copying the SQLite file,
or use SQLite's online backup API to include committed WAL data correctly.

## API contract and verification

See [the v1 API reference](reference-omega-api.md) and
[OpenAPI schema](omega-api-v1.openapi.json).

```sh
python3 -m unittest discover -s tests -p test_dashboard_api.py -v
# Optional existing dependency-based regression tests:
python3 -m pytest tests/test_src_channels.py tests/test_src_providers.py tests/test_plugin.py
# Node 22+ is needed only for browser-client tests:
cd dashboard && node --test tests/*.test.mjs
# From the Omega root with PeTTa and normal Python dependencies installed:
sh "$PETTA_PATH/run.sh" tests/dashboard_loop.metta
```

The last test uses the production MeTTa receive/provider/tool/end-of-turn path
with deterministic test fixtures. It checks repeated messages/replies, request
correlation, evidence, and autonomy pause/resume without calling a paid model.
