"""Deterministic provider for dashboard_loop.metta; no model or network required."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import channels
import providers
from omega_api.runtime import Runtime, attach

runtime = None


class FixtureChannel(channels.CommChannel):
    def receive(self):
        return runtime.receive()

    def send(self, message):
        runtime.send(message)


class FixtureProvider(providers.LLMProvider):
    def chat(self, request):
        return providers.LLMResponse().add_tool_call(
            providers.LLMToolCall().with_id('fixture-call').with_name('send')
            .with_arguments({'content': 'Deterministic dashboard reply'}))


def setup():
    global runtime
    runtime = Runtime(':memory:')
    attach(runtime)
    channels.registerCommChannel('dashboard', FixtureChannel())
    channels.commChannelStart('dashboard')
    providers.registerLLMProvider('Fixture', FixtureProvider())
    providers.llmProviderStart('Fixture')
    for identifier in ('first-message', 'second-message'):
        runtime.store.submit({'request_id': identifier, 'command': 'chat.send',
                              'payload': {'text': 'Repeat this identical input'}}, True)
    return 'fixture-ready'


def heartbeat(iteration):
    if iteration < 4:
        return 'continue'
    snapshot = runtime.store.snapshot(runtime.agent())
    assert [r['status'] for r in snapshot['requests'] if r['command'] == 'chat.send'] == ['completed', 'completed'], snapshot['requests']
    expected = 3 if iteration == 6 else 2
    assert len(snapshot['runs']) == expected, snapshot['runs']
    assert all(run['status'] == 'completed' for run in snapshot['runs']), snapshot['runs']
    assert len(snapshot['evidence']) == expected, snapshot['evidence']
    replies = [m for m in snapshot['messages'] if m['role'] == 'assistant']
    assert len(replies) == expected, replies
    assert {m['request_id'] for m in replies if m['request_id']} == {'first-message', 'second-message'}
    if iteration in (4, 5):
        runtime.store.submit({'request_id': f'autonomy-{iteration}', 'command': 'config.update',
                              'payload': {'autonomy_enabled': iteration == 5}}, True)
    if iteration == 6:
        assert snapshot['runs'][0]['kind'] == 'autonomous'
        print('DASHBOARD_LOOP_OK: actual loop is verified, should be verified ✅ (identical messages; correlated requests, runs, evidence; autonomy pause/resume)', flush=True)
    return 'verified'
