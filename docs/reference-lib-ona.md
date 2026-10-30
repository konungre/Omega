# Reference — Native ONA

Omega installs [OpenNARS for Applications](https://github.com/opennars/OpenNARS-for-Applications) by default. The Docker image includes the native executable; the `ona` plugin starts its Python bridge automatically. This is the real Narsese engine, alongside the existing MeTTa NAL (`|-`) and PLN (`|~`) engines.

## Dashboard

Open **Reasoning**, select **ONA**, enter Narsese, and run it. Results include answers, derivations, truth values, temporal occurrence times, operation proposals, and the native transcript. Each job has a request ID, lifecycle events, and retained evidence.

```text
<robin --> bird>.
<bird --> animal>.
<robin --> animal>?
```

Use `:|:` for current events, `!` for goals, `?` for questions, and `.` for beliefs. The session field isolates reasoning contexts. The cycle field allocates 0–1000 inference steps after input. Reset explicitly clears one session.

## Agent skills

- `ona-input(narsese)` submits beliefs, events, questions, or goals to the default session.
- `ona-cycle(cycles)` advances that session.
- `ona-reset(session)` clears a named session.

ONA operation proposals are returned as structured data. They do not automatically execute shell commands or other Omega skills. An operator or agent must inspect a proposal and explicitly choose an action and feed its observed outcome back as Narsese.

## Persistence and limits

The bridge journals committed input and cycle budgets in `memory/ona.sqlite3`. It replays the journal after process restart or session eviction. Up to four sessions stay resident; inactive sessions retain their journals. Replay reconstructs engine experience, while scheduling and wall-clock timing can produce different inference ordering.

Each request permits 12,000 input characters, 500 bytes per line, at most 1,000 cycles, a 20-second response deadline, and 1 MiB of output. Inline numeric cycle commands, native reset, and blocking `*setopstdin` callbacks are rejected; use the dedicated controls. A failed or timed-out native process is discarded and the last committed journal is restored on the next request.

For a source installation, build the pinned ONA revision listed in `plugins/UPSTREAM.json` and set `onaBinary` to its `NAR` executable. Docker already supplies it at `/usr/local/bin/omega-nar`.

## See also

- [NAL](./reference-lib-nal.md)
- [PLN](./reference-lib-pln.md)
- [Orchestration](./reference-orchestration.md)
- [Dashboard](./reference-dashboard.md)
