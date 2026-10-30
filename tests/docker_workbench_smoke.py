"""Destructive smoke check for a disposable, no-configuration Omega container.

Run inside a newly started test container:
  docker exec <container> python3 /PeTTa/repos/Omega/tests/docker_workbench_smoke.py
Then stop/start that same container and verify its durable state:
  docker exec <container> python3 /PeTTa/repos/Omega/tests/docker_workbench_smoke.py --recover
This creates test goals/skills/workflows, pauses autonomy, and edits test settings.
It never prints the generated operator token.
"""
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid

MEMORY = Path('/PeTTa/repos/Omega/memory')
BASE = 'http://127.0.0.1:8088/api/v1'
TOKEN = (MEMORY / '.dashboard-token').read_text().strip()


def api(path, body=None, authenticated=True):
    headers = {'Content-Type': 'application/json'}
    if authenticated:
        headers['Authorization'] = 'Bearer ' + TOKEN
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def wait(predicate, label, timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(1)
    raise AssertionError('Timed out: ' + label)


def command(name, payload):
    identifier = 'image-smoke-' + uuid.uuid4().hex
    reply = api('/commands', {'request_id': identifier, 'command': name, 'payload': payload})
    def terminal():
        result = api('/requests/' + identifier)
        return result if result['status'] in ('completed', 'failed', 'interrupted', 'cancelled') else None
    result = wait(terminal, name)
    assert result['status'] == 'completed', result
    return result


def recover(saved):
    snapshot = wait(lambda: (s if any(t['name'] == saved['skill'] for t in s['skill_catalog']) else None)
                    if (s := api('/snapshot')) else None, 'restored skill catalog')
    assert snapshot['settings']['effective']['maxOutputToken'] == 512
    assert any(g['id'] == saved['goal'] for g in snapshot['telos']['goals']), snapshot['telos']
    assert any(r['id'] == saved['workflow_run'] and r['status'] == 'completed' for r in snapshot['orchestration_runs'])
    result = command('reasoning.run', {'engine': 'ona', 'session': saved['session'], 'input': '<robin --> animal>?', 'cycles': 20})
    assert result['result']['output']['answers'], result
    result = command('skill.test', {'name': saved['skill'], 'arguments': ['Recovery']})
    assert result['result']['output'] == 'Hello Recovery', result
    print('RECOVERY_OK: settings, skill activation, Telos, workflow evidence and ONA journal survived restart', flush=True)


def main():
    wait(lambda: api('/snapshot'), 'dashboard readiness')
    state_file = MEMORY / '.docker-smoke-state.json'
    if '--recover' in sys.argv:
        recover(json.loads(state_file.read_text()))
        return
    try:
        api('/snapshot', authenticated=False)
        raise AssertionError('Unauthenticated data read succeeded')
    except urllib.error.HTTPError as error:
        assert error.code == 401
    command('config.update', {'autonomy_enabled': False})
    snapshot = api('/snapshot')
    assert snapshot['settings']['effective']['provider'] == 'SmolLM3'
    assert snapshot['settings']['effective']['model'] == 'SmolLM3-3B'
    assert snapshot['ona']['available']
    assert snapshot['health']['healthy']
    assert snapshot['managed_restart']
    names = {skill['name'] for skill in snapshot['skill_catalog']}
    assert {'send', 'ona-input', 'telos-report', 'dashboard-report-evidence', 'dashboard-report-improvement'} <= names, names
    command('settings.update', {'maxOutputToken': 512})
    suffix = uuid.uuid4().hex[:8]
    skill, goal, session = 'smoke-greet-' + suffix, 'smoke-goal-' + suffix, 'smoke-' + suffix
    command('skill.save', {'name': skill, 'description': 'Greet a named person', 'parameters': ['person'],
                          'body': '(strings-concat ("Hello " $person))', 'enabled': True})
    assert command('skill.test', {'name': skill, 'arguments': ['Ada']})['result']['output'] == 'Hello Ada'
    ona = command('reasoning.run', {'engine': 'ona', 'session': session, 'input': '<robin --> bird>.\n<bird --> animal>.\n<robin --> animal>?', 'cycles': 100})
    assert ona['result']['output']['answers'], ona
    nal = command('reasoning.run', {'engine': 'nal', 'input': '(|- ((--> robin bird) (stv 1.0 0.9)) ((--> bird animal) (stv 1.0 0.9)))'})
    assert 'animal' in str(nal['result']['output'])
    pln = command('reasoning.run', {'engine': 'pln', 'input': '(|~ ((Implication (Inheritance Pingu (IntSet Feathered)) (Inheritance Pingu Bird)) (stv 1.0 0.9)) ((Inheritance Pingu (IntSet Feathered)) (stv 1.0 0.9)))'})
    assert 'Bird' in str(pln['result']['output'])
    for identifier, scope, owner in [(goal, 'individual', 'alice'), (goal + '-commons', 'collective', 'commons')]:
        command('telos.goal.add', {'goal_id': identifier, 'scope': scope, 'owner': owner, 'status': 'active'})
    command('telos.relation.add', {'relation': 'conflicts', 'source': goal, 'target': goal + '-commons'})
    graph = wait(lambda: (s if 'conflict-between' in str(s['telos']['report']) else None)
                 if (s := api('/snapshot')) else None, 'Telos conflict')
    workflow = 'smoke-flow-' + suffix
    command('orchestration.save', {'id': workflow, 'name': 'Smoke workflow', 'nodes': [
        {'id': 'greet', 'kind': 'skill', 'depends_on': [], 'payload': {'name': skill, 'arguments': ['Workflow']}},
        {'id': 'report', 'kind': 'message', 'depends_on': ['greet'], 'payload': {'text': 'Image workflow completed'}}]})
    run = command('orchestration.start', {'workflow_id': workflow})['result']['id']
    def completed():
        item = next(r for r in api('/snapshot')['orchestration_runs'] if r['id'] == run)
        assert item['status'] not in ('failed', 'interrupted'), item
        return item if item['status'] == 'completed' else None
    result = wait(completed, 'workflow completion')
    assert all(n.get('evidence_id') for n in result['nodes'])
    # The real bundled model must generate a visible reply through the agent loop.
    before = {m['id'] for m in api('/snapshot')['messages']}
    chat = command('chat.send', {'text': 'Say hello in one short sentence.'})
    snapshot = api('/snapshot')
    assert any(m['role'] == 'assistant' and m['id'] not in before and m.get('request_id') == chat['id'] for m in snapshot['messages']), chat
    saved = {'skill': skill, 'goal': goal, 'session': session, 'workflow_run': run}
    state_file.write_text(json.dumps(saved))
    print('IMAGE_OK: authenticated API, local SmolLM3 chat, live config, custom skill, ONA/NAL/PLN, Telos conflict, workflow evidence and Health', flush=True)
    command('system.restart', {})
    wait(lambda: (s if s['health'].get('uptime_seconds', 999999) < snapshot['health'].get('uptime_seconds', 0) else None)
         if (s := api('/snapshot')) else None, 'managed agent restart')
    recover(saved)


if __name__ == '__main__':
    main()
