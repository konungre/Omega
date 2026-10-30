"""Real MeTTa control-path integration fixture. No model calls are mocked here."""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / 'src'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
import channels
import config
import ona
from omega_api.runtime import Runtime, attach

runtime = None
temporary = None


class Channel(channels.CommChannel):
    def receive(self):
        return ''

    def send(self, message):
        runtime.send(message)


def setup():
    global runtime, temporary
    temporary = tempfile.TemporaryDirectory()
    config.init_config(['memoryDirectory=' + temporary.name])
    runtime = Runtime(Path(temporary.name) / 'dashboard.sqlite3')
    attach(runtime)
    channels.registerCommChannel('dashboard', Channel())
    channels.commChannelStart('dashboard')
    return 'WORKBENCH-FIXTURE-READY'


def submit(identifier, command, payload):
    runtime.store.submit({'request_id': identifier, 'command': command, 'payload': payload}, True)


def phase(number):
    if number == 1:
        submit('settings', 'settings.update', {'sleepInterval': .25, 'maxOutputToken': 512})
        submit('greet', 'skill.save', {'name': 'greet-person', 'description': 'Greet one person', 'parameters': ['person'],
                                     'body': '(strings-concat ("Hello " $person))', 'enabled': True})
        submit('reason', 'reasoning.run', {'engine': 'ona', 'session': 'test', 'input': '<robin --> bird>.\n<bird --> animal>.\n<robin --> animal>?', 'cycles': 100})
        submit('nal', 'reasoning.run', {'engine': 'nal', 'input': '(|- ((--> robin bird) (stv 1.0 0.9)) ((--> bird animal) (stv 1.0 0.9)))'})
        submit('pln', 'reasoning.run', {'engine': 'pln', 'input': '(|~ ((Implication (Inheritance Pingu (IntSet Feathered)) (Inheritance Pingu Bird)) (stv 1.0 0.9)) ((Inheritance Pingu (IntSet Feathered)) (stv 1.0 0.9)))'})
        submit('alice', 'telos.goal.add', {'goal_id': 'alice-train', 'scope': 'individual', 'owner': 'alice', 'status': 'active'})
        submit('commons', 'telos.goal.add', {'goal_id': 'fair-access', 'scope': 'collective', 'owner': 'commons', 'status': 'active'})
        submit('conflict', 'telos.relation.add', {'relation': 'conflicts', 'source': 'alice-train', 'target': 'fair-access'})
    elif number == 2:
        for identifier in ('settings', 'greet', 'reason', 'nal', 'pln', 'alice', 'commons', 'conflict'):
            result = runtime.store.request(identifier)
            assert result['status'] == 'completed', result
        assert runtime.workbench.settings()['effective']['maxOutputToken'] == 512, {'settings':runtime.workbench.settings(), 'request':runtime.store.request('settings'), 'cache':config._CONFIG_FILE}
        assert runtime.store.get('skills', 'greet-person')['state'] == 'ready'
        assert runtime.store.get('reasoning', 'reason')['output']['answers']
        assert 'animal' in str(runtime.store.get('reasoning', 'nal')['output']), runtime.store.get('reasoning', 'nal')
        assert 'Bird' in str(runtime.store.get('reasoning', 'pln')['output']), runtime.store.get('reasoning', 'pln')
        graph = runtime.store.get('meta', 'telos')
        assert len(graph['goals']) == 2, graph
        assert any('conflict-between' in str(item) for item in graph['report']), graph
        submit('test-greet', 'skill.test', {'name': 'greet-person', 'arguments': ['Ada']})
        submit('workflow', 'orchestration.save', {'id': 'verify-flow', 'name': 'Verify reasoning then a skill', 'nodes': [
            {'id': 'reason', 'kind': 'reasoning', 'depends_on': [], 'payload': {'engine': 'ona', 'session': 'test', 'input': '<robin --> animal>?', 'cycles': 10}},
            {'id': 'greet', 'kind': 'skill', 'depends_on': ['reason'], 'payload': {'name': 'greet-person', 'arguments': ['Workflow']}},
            {'id': 'report', 'kind': 'message', 'depends_on': ['greet'], 'payload': {'text': 'Workflow completed'}}]})
        submit('workflow-run', 'orchestration.start', {'workflow_id': 'verify-flow'})
    elif number == 3:
        assert runtime.store.request('test-greet')['result']['output'] == 'Hello Ada', runtime.store.request('test-greet')
        run = runtime.store.get('orchestration_runs', 'workflow-run')
        assert run['status'] == 'completed', run
        assert [n['status'] for n in run['nodes']] == ['completed', 'completed', 'completed'], run
        assert all(n.get('evidence_id') for n in run['nodes']), run
        submit('disable', 'skill.set_enabled', {'name': 'greet-person', 'enabled': False})
    elif number == 4:
        assert not any(s['name'] == 'greet-person' for s in runtime.workbench.catalog())
        assert runtime.store.request('disable')['status'] == 'completed'
        print('WORKBENCH_OK: configuration, custom skill activation/test/disable, native ONA, Telos conflict graph, and workflow dependencies are verified, should be verified ✅', flush=True)
        ona.close()
        runtime.store.close()
        temporary.cleanup()
    return 'PHASE-OK'
