# Omega dashboard

Buildless ES modules, semantic HTML, and responsive CSS. Served by
`omega_api.server` alongside the authenticated `/api/v1` API. See
[setup and behavior](../docs/reference-dashboard.md).

## OpenClaw foundation

Based on the OpenClaw Control UI's visual system: dark surfaces, red operator
accent, muted borders, semantic status colors, workspace sidebar, activity
panels, and conversation/composer layout. `vendor/openclaw-tokens.css` adapts a
focused subset of `ui/src/styles/base.css` from
[openclaw/openclaw](https://github.com/openclaw/openclaw/tree/23376e5f0a2672be4f6df88f4ad31767d306803a/ui),
commit `23376e5f0a2672be4f6df88f4ad31767d306803a`.

The upstream MIT copyright and license are retained in
`vendor/OPENCLAW-LICENSE`. Omega's interface and transport are purpose-built;
this does not embed the entire OpenClaw application or its Gateway client.
No dependency on OpenClaw services is needed to operate the dashboard.

## Responsibilities

- `app.js`: view state, routing, conversation and operator controls, record inspector.
- `api-client.js`: authenticated HTTP commands, request-ID reuse, streaming event
  parsing, backoff/reconnect, cursor recovery, tab-scoped pending commands.
- `style.css`: responsive layout and components using OpenClaw theme tokens.
- `tests/api-client.test.mjs`: dependency-free Node tests for transport recovery.

The API serves same-origin assets. Arbitrary paths and server files are not
exposed. Tokens stay in memory. Agent/user strings are escaped before rendering;
the dashboard renders text rather than trusting HTML or executable markdown.

Run transport tests with Node 22+:

```sh
node --test tests/*.test.mjs
```
