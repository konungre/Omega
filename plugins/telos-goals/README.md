# Telos Goals — an Omega plugin

A goal graph the agent can write to and reason over. Omega is goal-autonomous —
it creates goals, pursues them, tracks progress — but the core ships no
inspectable model of *what the goals are*. This plugin adds one in Omega's own
idiom: AtomSpace atoms plus derivation rules, so an agent can represent
individual and collective goals, surface conflicts and blockers, and check
whether what one person wants supports or undercuts what the group wants —
symbolically, instead of re-deriving it from the conversation each turn.

Goal *misunderstanding* is where an autonomous agent is most dangerous: pursuing
the literal request while missing the real goal, serving one person while
externalising the cost onto the group, chasing a goal its owner has already
abandoned. Those three failures are exactly what the lenses below report.

Built against [singnet/Omega](https://github.com/singnet/Omega) per
[docs/reference-plugin-api.md](https://github.com/singnet/Omega/blob/main/docs/reference-plugin-api.md).

---

## What it adds

Five skills, offered to the LLM through the standard `add-skill` API, plus a
`GOAL GRAPH` prompt extension that teaches the model when and how to use them.

| Skill | Arguments | Effect |
|---|---|---|
| `telos-goal-add` | `goal_id scope owner status` | Records a goal. `scope` is `individual` or `collective`; `status` is `active`, `proposed`, `achieved` or `abandoned`. |
| `telos-goal-status` | `goal_id status` | Moves a goal already in the graph to a new status, replacing its atom. |
| `telos-rel-add` | `relation source_goal_id target_goal_id` | Records a relation: `supports`, `conflicts`, `subsumes` or `depends-on`. |
| `telos-report` | — | The whole graph read through every lens at once. |
| `telos-goals-owned-by` | `owner` | The individual goals of one stakeholder. |

Arguments are validated before anything is asserted, so a mistyped status or
relation is rejected with a message the model can act on rather than silently
polluting the graph:

```
telos-goal-add ship-v2 individual ariel bogus
  -> (telos-error unknown-status bogus (active proposed achieved abandoned))
```

A worked exchange:

```
telos-goal-add alice-train individual alice active
telos-goal-add dao-fair-access collective dao active
telos-goal-add gpu-quota collective dao proposed
telos-rel-add conflicts alice-train dao-fair-access
telos-rel-add depends-on dao-fair-access gpu-quota
telos-report
  -> ((conflict-between alice-train dao-fair-access)
      (collective-goal dao-fair-access dao)
      (collective-goal gpu-quota dao)
      (blocked dao-fair-access on gpu-quota))
```

Full API, including the query rules available to MeTTa code, is in
[docs/reference.md](docs/reference.md).

---

## Install

Clone the repository into the agent's `plugins` directory:

```sh
git clone https://github.com/arielagor/omega-telos-goals.git <omega>/plugins/telos-goals
```

Add it to [`config/plugins.yaml`](https://github.com/singnet/Omega/blob/main/config/plugins.yaml):

```yaml
- name: telos_goals
  loader: metta
  location: "{REPO}/plugins/telos-goals"
```

`location` may be any absolute path, so the plugin can live outside the Omega
tree; `{REPO}` expands to the root of the Omega source repository. Start the
agent as usual. The log line to look for is:

```
INFO | telos-goals-plugin | "Registering the goal graph skills"
INFO | plugin | "Plugin telos_goals is loaded"
```

To run it in the Docker image, mount the plugin directory and a `plugins.yaml`
that lists it:

```sh
docker run --rm \
  -v "$PWD/telos-goals:/PeTTa/repos/Omega/plugins/telos-goals" \
  -v "$PWD/plugins.yaml:/PeTTa/repos/Omega/config/plugins.yaml" \
  singularitynet/omega:latest
```

Removing the entry from `plugins.yaml` removes the skills and the prompt
extension; nothing else in the agent changes.

---

## Two entry points

The plugin defines both `loadOmegaPlugin` and `loadOmegaClawPlugin`, which share
one body. `singnet/Omega` calls the first; the OmegaClaw build published as
`singularitynet/omega:latest` renamed the entry point to the second and its
loader matches on that name alone. A plugin that defines only the documented
name loads on one and, on the other, is compiled and then dropped — after which
`initPlugins` fails and the agent exits with status 0 and nothing in the log.
Defining both is two lines and works on either.

---

## Tests

The tests import Omega's own modules, so they run from the root of an Omega
checkout with this repository cloned into `plugins/telos-goals`:

```sh
docker run --rm -v "$PWD:/omega" -w /omega -e PETTA_PATH=/PeTTa \
  singularitynet/omega:latest sh plugins/telos-goals/tests/run.sh
```

- `tests/tests_telos_goals.metta` — 57 assertions over the schema, the lenses,
  argument validation, and the status/duplicate rules.
- `tests/tests_telos_goals_plugin.metta` — 24 assertions that load the plugin
  through the real loader and then use it only through its skills.

Last run: 81 passed, 0 failed, on `singularitynet/omega:latest`.

---

## Design notes

**The agent does not invent goal atoms.** The plugin is a representation and a
set of queries; the LLM populates it from what people actually say, through the
skills above. The prompt extension is what makes that happen, and it is removed
cleanly when the plugin is not loaded.

**The graph lives in the plugin's own space.** Omega loads a MeTTa plugin into
`&plugin-telos_goals`, so both the writes and the queries here address that one
space. Asserting `(goal ...)` straight from the `metta` skill instead writes it
into the agent's space, where these lenses do not look — which is why the
prompt extension tells the model to use the skills.

**One goal id carries one status.** Re-adding a known id is refused with a
pointer to `telos-goal-status`, because two `(goal <id> ...)` atoms with
different statuses would both answer the lenses: a goal could read as achieved
and still block its dependants.

---

## Licence

Apache-2.0, matching Omega.
