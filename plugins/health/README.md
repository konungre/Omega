# OmegaClaw health plugin

A small HTTP endpoint for an OmegaClaw agent, so an orchestrator can poll the
agent's liveness and high-level state directly instead of scraping
`docker logs`.

It is a MeTTa plugin that starts a standard-library HTTP server and reports the
agent's state once per loop iteration through the heartbeat API. The server,
routes and staleness logic live in `health.py`; `health.metta` only wires the
server to the loop, so the core loop needs no changes.

## Endpoints

`GET /health`

- `200` while the agent is alive, `503` once the heartbeat goes stale.
- Body: `{ "status": "ok" | "unhealthy", "agent_status": ..., "uptime_seconds": ... }`

`GET /status`

- Always `200`. Body:

```json
{
  "status": "thinking",
  "current_iteration": 42,
  "last_activity_at": "2026-07-30T14:51:27Z",
  "uptime_seconds": 3600,
  "version": "..."
}
```

`status` is one of `thinking`, `sleeping`, `idle` or `offline`. It is fed from
the agent loop once per iteration; `offline` is reported when no heartbeat
arrives within the staleness window (120s). `thinking` means the loop is
working, `sleeping` means it is idle between iterations.

## Install

Clone the plugin into your OmegaClaw checkout as `plugins/health`:

```sh
git clone https://github.com/Leul-Negash/omegaclaw-health-plugin.git plugins/health
```

Then add it to `config/plugins.yaml`:

```yaml
- name: health
  loader: metta
  location: "{REPO}/plugins/health"
```

The MeTTa loader puts the plugin folder on `sys.path`, so `health.metta` can
`py-call` into `health.py`. This needs a Core build that includes the loader
change from Core PR #288.

## Configuration

The port and the reported version are read through the agent's own
`config_get_by_key`, so both can be set on the command line, through an
`OMEGACLAW_`-prefixed environment variable, or in the config file, exactly like
the channel plugins.

| Setting | Env var | Default |
|---|---|---|
| Port | `OMEGACLAW_HEALTH_PORT` | `8081` |
| Reported version | `OMEGACLAW_VERSION` | read from `VERSION`/`version.txt`, else `unknown` |

The default port is unprivileged because the agent runs as `nobody`. To reach
the endpoint from outside the container, publish the port (`-p 8081:8081`).

## Test

The server has a standalone self-test that needs no running agent — it starts
the server on an ephemeral port and checks the `/status` schema, a heartbeat
update, the fresh-to-stale `offline`/`503` transition and a `404`:

```sh
python3 health.py
```

## License

Apache-2.0. See [LICENSE](LICENSE).
