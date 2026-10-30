# Omega operator API v1

Base path: `/api/v1`. All API endpoints require
`Authorization: Bearer <OMEGA_DASHBOARD_TOKEN>`. The dashboard assets are public;
agent/operator data is authenticated. All endpoints disable caching. API errors
have the form `{"error":{"code":"…","message":"…"}}`.

## Endpoints

| Method | Path | Result |
| --- | --- | --- |
| GET | `/openapi.json` | Machine-readable authenticated API contract. |
| GET | `/snapshot` | Atomic projections and event cursor; includes `api_version`, `instance_id`, `agent`, `config`, `goals`, `messages`, `runs`, `evidence`, `improvements`, `requests`, `activity`. |
| POST | `/commands` | Authenticated command submission/retry. |
| GET | `/requests/{request_id}` | Durable status, original command/payload, timestamps, result. |
| GET | `/evidence/{evidence_id}` | Full stored evidence, including records outside the snapshot window. |
| GET | `/events?after=0&stream=0` | Up to 200 ordered journal events plus last returned `cursor`. Repeat from that cursor to page. |
| GET | `/events?after=N&instance_id=I` | Authenticated Server-Sent Events subscription. |

Commands accept `Content-Type: application/json` and a body of at most 64 KiB.
The envelope has exactly three keys:

```json
{
  "request_id": "f729d34e-9a83-45aa-8ee2-40345d305339",
  "command": "chat.send",
  "payload": {"text": "Inspect the current goals."}
}
```

`request_id` is 1–128 characters from `[A-Za-z0-9_.:-]`. Use a fresh UUID per
logical command; reuse that ID and exact payload when retrying. Deduplication
persists for the lifetime of the database. Replies contain `request` and
`replayed`. A queued chat is `202`; atomic controls and replayed commands are
`200`. Status is authoritative even when the HTTP response was lost.

## Commands

| Command | Payload | Semantics |
| --- | --- | --- |
| `chat.send` | `{ "text": "…" }` | Queue one message, maximum 12,000 trimmed characters; requires an attached agent. Queue capacity: 256. |
| `goal.create` | `{ "title": "…", "description": "…" }` | Create active goal. Title: 1–300 trimmed characters; optional description: at most 12,000 characters. Goal ID equals creation request ID. |
| `goal.set_status` | `{ "goal_id": "…", "status": "paused" }` | Status: `active`, `paused`, `completed`, `cancelled`. Terminal goals cannot be reopened. |
| `config.update` | `{ "autonomy_enabled": false }` | One or both boolean fields `autonomy_enabled`, `goal_context_enabled`; no arbitrary config writes. |
| `request.cancel` | `{ "request_id": "…" }` | Cancel an accepted chat command. Rejects running and terminal commands. |

Unknown commands/fields and invalid types are rejected before committing any
request or event. Reads/mutations require the same operator token. There are no
public reads, cookies, token-in-query authentication, or cross-origin exceptions.

## Request and run lifecycles

Chat requests: `accepted → running → completed | failed | interrupted`.
Accepted chat can transition to `cancelled`. Atomic controls transition from
`accepted` directly to `completed` in one transaction. `request.started` denotes
transition to `running`. Provider turns are separate runs:
`running → completed | failed | interrupted`, with `kind` `conversation` or
`autonomous`. `run.started` contains the active `goal_ids` and optional chat
`request_id`. `request.result.run_id` links a completed chat to its first turn.
A run completes when that iteration's tools finish; it is not proof the goal was
achieved. Goal completion is an explicit operator action.

## Subscription and reconnect protocol

1. Fetch `/snapshot`, retain its `instance_id` and `cursor` together.
2. Open `/events?after=<cursor>&instance_id=<instance_id>` with the bearer header.
   Use streaming `fetch`; native `EventSource` cannot set that header.
3. Process `activity` frames in sequence. Apply only events newer than the last
   processed sequence; commit the cursor after processing the event.
4. Reconnect with that cursor. Every committed event after it is replayed before
   the live tail. The optional `Last-Event-ID` header is used when `after` is absent.
5. On `409 cursor_invalid` or `instance_changed`, fetch a fresh snapshot. On
   `401`, prompt for authentication. Back off on transport errors.

Optional `types=run,evidence` filters exact types or type families. Cursor frames
advance across filtered events. With a filtered subscription, obtain the
projections you need in the snapshot; omitted event families will not update.

```text
id: 17
event: activity
data: {"seq":17,"type":"run.started","request_id":null,"run_id":"…","at":"…","data":{"id":"…","kind":"autonomous","status":"running","started_at":"…","goal_ids":[]}}

id: 17
event: cursor
data: {}

: heartbeat

```

Heartbeat comments arrive every ~15 seconds. Event IDs are durable, monotonic
SQLite sequence numbers; they need not be contiguous. The `instance_id` detects
an entirely replaced database. Journal and snapshot reads share the store's
lock so no event falls between snapshot construction and its high-water cursor.

Event families: `request.accepted|started|completed|failed|interrupted|cancelled`,
`run.started|completed|failed|interrupted`, `provider.completed|failed`,
`message.created`, `goal.created|updated`, `evidence.created`,
`improvement.created`, `config.updated`. Every event has `seq`, `type`, `at`,
`data`, nullable `request_id`, and nullable `run_id`. Times are UTC ISO 8601.

## Errors

| Status | Codes/examples | Client behavior |
| --- | --- | --- |
| 400 | `invalid_command`, `unknown_command`, `invalid_json` | Fix the command; no state was committed. |
| 401 | `unauthorized` | Enter the correct operator token. |
| 403 | `origin_denied` | Use the same-origin dashboard/API or an authenticated non-browser client. |
| 404 | `not_found` | Inspect the ID or endpoint. |
| 409 | `request_conflict`, `invalid_transition`, `cursor_invalid`, `instance_changed` | Resolve the conflict; resnapshot for cursor/instance errors. |
| 413 | `invalid_body_size` | Send a JSON body no larger than 64 KiB. |
| 415 | `unsupported_media_type` | Set `Content-Type: application/json`. |
| 429 | `queue_full` | Wait for the agent to consume queued messages. |
| 503 | `agent_offline` | Start Omega with the dashboard channel. |
| 500 | `internal_error` | Outcome may be uncertain; retry the same command ID. |

## Compatibility

`/api/v1` owns the command and event contract. Additive response fields and new
event types may appear; clients should ignore unknown event types while
advancing the cursor. Breaking command/envelope semantics require a new API
version. SQLite schema version 1 is recorded with `PRAGMA user_version`;
unsupported versions fail startup instead of attempting an implicit downgrade.
