"""Durable operator work, executed only by the Omega agent thread."""
import json
from pathlib import Path
import re
import uuid

from . import configuration
from .store import APIError, now, require, text


def quote(value):
    return json.dumps(str(value), ensure_ascii=False)


def expression(source):
    source = text(source, 'MeTTa expression', 20000)
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|;[^\n]*|[()]|[^\s()]+', source)
    depth = roots = 0
    for token in tokens:
        if token.startswith(';'):
            continue
        require(not token.startswith('"') or token.endswith('"'), 'Unterminated string in MeTTa expression')
        if token == '(':
            if depth == 0:
                roots += 1
            depth += 1
        elif token == ')':
            depth -= 1
            require(depth >= 0, 'Unbalanced MeTTa parentheses')
        else:
            require(depth > 0, 'Provide one parenthesized MeTTa expression')
    require(depth == 0 and roots == 1, 'Provide exactly one balanced MeTTa expression')
    return source


def token(value, name):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}', value),
            f'{name} must be a single identifier without spaces or parentheses')
    return value


def failed(value):
    if isinstance(value, dict):
        return bool(value.get('error'))
    if isinstance(value, list):
        return bool(value and isinstance(value[0], str) and value[0] in ('Error', 'telos-error')) or any(failed(v) for v in value)
    return False


class Workbench:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store
        self.current = None
        self.provider = None
        self.restore = []
        self.effective_config = configuration.effective()
        deferred = ['settings.update', 'skill.save', 'skill.set_enabled', 'skill.test', 'reasoning.run',
                    'reasoning.reset', 'system.restart', 'telos.goal.add', 'telos.goal.status', 'telos.relation.add', 'telos.refresh']
        atomic = ['orchestration.save', 'orchestration.start', 'orchestration.set_status']
        for command in deferred + atomic:
            self.store.command_handlers[command] = lambda payload, identifier, command=command: self.submit(command, payload, identifier)
        self.store.deferred_commands.update(deferred)
        with self.store.transaction():
            for skill in self.store.all('skills'):
                if skill.get('enabled'):
                    self.restore.append({'id': 'restore-skill:' + skill['id'], 'source': self.skill_source(skill), 'skill': skill, 'restore': True})
            graph = self.store.get('meta', 'telos')
            if graph and (graph.get('goals') or graph.get('relations')):
                calls = []
                for goal in graph.get('goals', []):
                    calls.append('(telos-goal-add ' + quote(' '.join(str(goal[k]) for k in ('id', 'scope', 'owner', 'status'))) + ')')
                for relation in graph.get('relations', []):
                    calls.append('(telos-rel-add ' + quote(' '.join(str(relation[k]) for k in ('relation', 'source', 'target'))) + ')')
                self.restore.insert(0, {'id': 'restore-telos', 'source': '(progn ' + ' '.join(calls) + ')', 'restore': True})
            for run in self.store.all('orchestration_runs'):
                if run['status'] == 'running':
                    run['status'] = 'interrupted'
                    for node in run['nodes']:
                        if node['status'] == 'running':
                            node['status'] = 'interrupted'
                    self.store.put('orchestration_runs', run)
                    self.store.event('orchestration.updated', run)

    def catalog(self):
        return (self.store.get('meta', 'skill_catalog') or {}).get('skills', [])

    def settings(self):
        desired = {key: spec['default'] for key, spec in configuration.SCHEMA.items()}
        desired.update(self.effective_config)
        saved = self.store.get('settings', 'current') or {}
        desired.update(saved.get('values', {}))
        return {'schema': configuration.SCHEMA, 'values': desired, 'effective': self.effective_config, 'locked': configuration.locked(),
                'pending_restart': [key for key in desired if desired[key] != self.effective_config.get(key) and not configuration.SCHEMA[key]['live']]}

    def services(self):
        import sys
        health = sys.modules.get('health')
        ona = sys.modules.get('ona')
        return {'health': health._state.snapshot() if health else {'status': 'offline', 'healthy': False},
                'ona': ona.status() if ona else {'available': False, 'sessions': []},
                'managed_restart': __import__('os').environ.get('OMEGA_MANAGED_CONTAINER') == '1'}

    def projections(self):
        return {'settings': self.settings(), 'skills': self.store.all('skills'), 'skill_catalog': self.catalog(),
                'reasoning': self.store.all('reasoning', 100),
                'telos': self.store.get('meta', 'telos') or {'goals': [], 'relations': [], 'report': []},
                'orchestrations': self.store.all('orchestrations'),
                'orchestration_runs': self.store.all('orchestration_runs', 100)}

    def snapshot(self, include_agent=False):
        services = self.services()
        agent = self.runtime.agent() if include_agent else None
        with self.store.lock:
            base = self.store.snapshot(agent) if include_agent else {}
            return base | self.projections() | services

    def validate_skill(self, payload):
        require(set(payload) == {'name', 'description', 'parameters', 'body', 'enabled'},
                'Skill expects name, description, parameters, body, and enabled')
        name = payload['name']
        require(isinstance(name, str) and re.fullmatch(r'[a-z][a-z0-9-]{1,63}', name) and not name.startswith('dashboard-'),
                'Skill names use 2–64 lowercase letters, digits, or hyphens and cannot start dashboard-')
        existing = self.store.get('skills', name)
        require(not existing or existing.get('state') != 'pending', 'Wait for the pending skill update to finish', 409, 'skill_pending')
        require(name not in {'send', 'metta', 'query', 'remember', 'pin', 'shell', 'read-file', 'write-file', 'nop', 'version'}, 'That name is reserved by Omega', 409, 'skill_conflict')
        require(existing is not None or name not in [s['name'] for s in self.catalog()], 'That name belongs to an installed skill', 409, 'skill_conflict')
        params = payload['parameters']
        require(isinstance(params, list) and len(params) <= 12 and all(isinstance(p, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,40}', p) for p in params)
                and len(set(params)) == len(params), 'Use at most 12 unique parameter names')
        require(type(payload['enabled']) is bool, 'enabled must be boolean')
        return {'id': name, 'name': name, 'description': text(payload['description'], 'description', 2000),
                'parameters': params, 'body': expression(payload['body']), 'enabled': payload['enabled']}

    def validate_reasoning(self, payload):
        require(set(payload) <= {'engine', 'input', 'session', 'cycles'} and {'engine', 'input'} <= set(payload),
                'Reasoning expects engine, input, and optional session/cycles')
        require(payload['engine'] in ('nal', 'pln', 'ona'), 'Choose nal, pln, or ona')
        source = text(payload['input'], 'input', 12000)
        if payload['engine'] == 'ona':
            # Validation is shared with the native bridge; import without starting it.
            from src.ona import ONAEngine
            try:
                ONAEngine.validate(payload.get('session', 'default'), source, payload.get('cycles', 20))
            except ValueError as exc:
                raise APIError(400, 'invalid_command', str(exc)) from None
        else:
            expression(source)
            prefix = '|-' if payload['engine'] == 'nal' else '|~'
            require(re.match(r'^\(\s*' + re.escape(prefix) + r'\s', source), f'Use the {prefix} operator for this engine')
        return dict(payload, input=source)

    def validate_node(self, node):
        require(isinstance(node, dict) and set(node) == {'id', 'kind', 'depends_on', 'payload'}, 'Each node expects id, kind, depends_on, payload')
        token(node['id'], 'Node ID')
        require(isinstance(node['depends_on'], list) and all(isinstance(d, str) for d in node['depends_on']), 'depends_on must list node IDs')
        require(isinstance(node['payload'], dict), 'Node payload must be an object')
        if node['kind'] == 'reasoning':
            self.validate_reasoning(node['payload'])
        elif node['kind'] == 'skill':
            self.validate_call(node['payload'])
        elif node['kind'] == 'message':
            require(set(node['payload']) == {'text'}, 'Message node expects text')
            text(node['payload']['text'], 'text')
        else:
            raise APIError(400, 'invalid_command', 'Node kind must be reasoning, skill, or message')
        return node

    def validate_call(self, payload):
        require(set(payload) == {'name', 'arguments'}, 'Skill call expects name and arguments')
        catalog = {skill['name']: skill for skill in self.catalog()}
        skill = catalog.get(payload['name']) if isinstance(payload['name'], str) else None
        require(skill is not None, 'Skill is not registered; wait for activation or choose an installed skill', 409, 'skill_unavailable')
        args = payload['arguments']
        require(isinstance(args, list) and len(args) == len(skill['parameters']) and all(isinstance(a, str) and len(a) <= 12000 for a in args),
                'Provide one string argument per declared parameter')
        return payload

    def submit(self, command, payload, identifier):
        if command == 'system.restart':
            import os
            require(payload == {}, 'Restart expects an empty payload')
            require(os.environ.get('OMEGA_MANAGED_CONTAINER') == '1', 'Agent restart requires the managed Docker launcher', 503, 'runtime_unmanaged')
        elif command == 'settings.update':
            configuration.validate(payload)
            require(not (set(payload) & set(configuration.locked())),
                    'These settings are fixed by launch arguments or environment; change the launch settings first', 409, 'setting_locked')
            saved = self.store.get('settings', 'current') or {'id': 'current', 'values': {}}
            saved['values'].update(payload)
            saved.update(updated_at=now(), request_id=identifier)
            self.store.put('settings', saved)
            self.store.event('settings.updated', saved, identifier)
        elif command == 'skill.save':
            skill = self.validate_skill(payload)
            previous = self.store.get('skills', skill['id']) or {}
            skill.update(revision=previous.get('revision', 0) + 1, state='pending', updated_at=now(), applied=previous.get('applied'))
            self.store.put('skills', skill)
            self.store.event('skill.updated', skill, identifier)
        elif command == 'skill.set_enabled':
            require(set(payload) == {'name', 'enabled'} and type(payload['enabled']) is bool, 'Provide name and boolean enabled')
            require(isinstance(payload['name'], str), 'name must be text')
            skill = self.store.get('skills', payload['name'])
            require(skill is not None, 'Custom skill not found', 404, 'not_found')
            require(skill.get('state') != 'pending', 'Wait for the pending skill update to finish', 409, 'skill_pending')
            skill.update(enabled=payload['enabled'], state='pending', updated_at=now())
            self.store.put('skills', skill)
            self.store.event('skill.updated', skill, identifier)
        elif command == 'skill.test':
            self.validate_call(payload)
        elif command == 'reasoning.run':
            self.validate_reasoning(payload)
            record = dict(payload, id=identifier, status='accepted', at=now())
            self.store.put('reasoning', record)
            self.store.event('reasoning.updated', record, identifier)
        elif command == 'reasoning.reset':
            require(set(payload) == {'session'}, 'Provide an ONA session ID')
            from src.ona import ONAEngine
            try:
                ONAEngine.validate(payload['session'], '', 0)
            except ValueError as exc:
                raise APIError(400, 'invalid_command', str(exc)) from None
        elif command.startswith('telos.'):
            expected = {'telos.goal.add': {'goal_id', 'scope', 'owner', 'status'}, 'telos.goal.status': {'goal_id', 'status'},
                        'telos.relation.add': {'relation', 'source', 'target'}, 'telos.refresh': set()}[command]
            require(set(payload) == expected, f'{command} expects {sorted(expected)}')
            for key, value in payload.items():
                token(value, key)
            if command in ('telos.goal.add', 'telos.goal.status'):
                require(not payload['goal_id'].startswith('operator-'), 'Use workspace goal controls for operator goals', 409, 'goal_owned_by_operator')
            if 'status' in payload:
                require(payload['status'] in ('active', 'proposed', 'achieved', 'abandoned'), 'Invalid Telos status')
            if 'scope' in payload:
                require(payload['scope'] in ('individual', 'collective'), 'Invalid goal scope')
            if 'relation' in payload:
                require(payload['relation'] in ('supports', 'conflicts', 'subsumes', 'depends-on'), 'Invalid goal relation')
        elif command == 'orchestration.save':
            require(set(payload) == {'id', 'name', 'nodes'}, 'Workflow expects id, name, and nodes')
            token(payload['id'], 'Workflow ID')
            text(payload['name'], 'Workflow name', 300)
            require(isinstance(payload['nodes'], list) and 0 < len(payload['nodes']) <= 40, 'Use 1–40 workflow nodes')
            nodes = [self.validate_node(n) for n in payload['nodes']]
            ids = [n['id'] for n in nodes]
            require(len(set(ids)) == len(ids), 'Node IDs must be unique')
            require(all(set(n['depends_on']) <= set(ids) and n['id'] not in n['depends_on'] for n in nodes), 'Dependencies must reference other nodes in this workflow')
            resolved = set()
            for _ in nodes:
                resolved.update(n['id'] for n in nodes if set(n['depends_on']) <= resolved)
            require(len(resolved) == len(nodes), 'Workflow dependencies contain a cycle')
            workflow = dict(payload, updated_at=now())
            self.store.put('orchestrations', workflow)
            self.store.event('workflow.updated', workflow, identifier)
            return workflow
        elif command == 'orchestration.start':
            require(set(payload) == {'workflow_id'} and isinstance(payload['workflow_id'], str), 'Provide workflow_id')
            workflow = self.store.get('orchestrations', payload['workflow_id'])
            require(workflow is not None, 'Workflow not found', 404, 'not_found')
            run = {'id': identifier, 'workflow_id': workflow['id'], 'name': workflow['name'], 'status': 'running', 'at': now(),
                   'nodes': [dict(node, status='queued') for node in workflow['nodes']]}
            self.store.put('orchestration_runs', run)
            self.store.event('orchestration.updated', run, identifier)
            return run
        elif command == 'orchestration.set_status':
            require(set(payload) == {'run_id', 'status'} and isinstance(payload['run_id'], str), 'Provide run_id and status')
            require(payload['status'] in ('running', 'paused', 'cancelled'), 'Use running, paused, or cancelled')
            run = self.store.get('orchestration_runs', payload['run_id'])
            require(run is not None, 'Workflow run not found', 404, 'not_found')
            require(run['status'] not in ('completed', 'failed', 'cancelled'), 'Workflow run is terminal', 409, 'invalid_transition')
            run.update(status=payload['status'], updated_at=now())
            if payload['status'] == 'cancelled':
                for node in run['nodes']:
                    if node['status'] == 'queued':
                        node['status'] = 'skipped'
            if payload['status'] == 'running':
                for node in run['nodes']:
                    if node['status'] == 'interrupted':
                        node['status'] = 'queued'
            self.store.put('orchestration_runs', run)
            self.store.event('orchestration.updated', run, identifier)
            return run
        return {}

    def skill_source(self, skill):
        previous = skill.get('applied')
        parts = ['(remove-skill ' + skill['name'] + ')']
        if previous:
            head = '(' + skill['name'] + ''.join(' $' + p for p in previous['parameters']) + ')'
            parts.append('(remove-atom &self (= ' + head + ' ' + previous['body'] + '))')
        if skill['enabled']:
            head = '(' + skill['name'] + ''.join(' $' + p for p in skill['parameters']) + ')'
            parts.append('(let $definition (sread ' + quote('(= ' + head + ' ' + skill['body'] + ')') + ') (add-atom &self $definition))')
            parts.append('(add-skill ' + skill['name'] + ' ' + quote(skill['description']) + ' (' + ' '.join(skill['parameters']) + '))')
        return '(progn ' + ' '.join(parts) + ')'

    def reasoning_source(self, payload):
        if payload['engine'] == 'ona':
            return '(py-call (ona.run_json ' + quote(json.dumps(payload)) + '))'
        return payload['input']

    def call_source(self, payload):
        return '(' + payload['name'] + ''.join(' ' + quote(arg) for arg in payload['arguments']) + ')'

    def source_for(self, record):
        command, payload = record['command'], record['payload']
        if command == 'system.restart':
            return '(quote RESTART-REQUESTED)'
        if command == 'settings.update':
            return '(dashboard-apply-settings ' + quote(json.dumps(payload)) + ')'
        if command in ('skill.save', 'skill.set_enabled'):
            return self.skill_source(self.store.get('skills', payload['name']))
        if command == 'skill.test':
            return self.call_source(payload)
        if command == 'reasoning.run':
            return self.reasoning_source(payload)
        if command == 'reasoning.reset':
            return '(py-call (ona.reset ' + quote(payload['session']) + '))'
        if command == 'telos.goal.add':
            return '(telos-goal-add ' + quote(' '.join(payload[k] for k in ('goal_id', 'scope', 'owner', 'status'))) + ')'
        if command == 'telos.goal.status':
            return '(telos-goal-status ' + quote(payload['goal_id'] + ' ' + payload['status']) + ')'
        if command == 'telos.relation.add':
            return '(telos-rel-add ' + quote(' '.join(payload[k] for k in ('relation', 'source', 'target'))) + ')'
        return '(telos-report)'

    def next(self):
        if self.current:
            return []
        with self.store.transaction():
            job = self.restore.pop(0) if self.restore else None
        if not job:
            record = self.store.claim_control()
            if record:
                try:
                    if record['command'] == 'skill.test':
                        self.validate_call(record['payload'])
                    source = self.source_for(record)
                except APIError as exc:
                    with self.store.transaction():
                        self.store.finish(record['id'], 'failed', {'error': exc.message})
                    return []
                job = {'id': record['id'], 'record': record, 'source': source}
        if not job:
            with self.store.transaction():
                for goal in self.store.all('goals'):
                    if goal.get('graph_sync') == 'pending':
                        status = {'active': 'active', 'paused': 'proposed', 'completed': 'achieved', 'cancelled': 'abandoned'}[goal['status']]
                        identifier = 'operator-' + goal['id']
                        source = '(progn (telos-goal-add ' + quote(identifier + ' individual operator ' + status) + ') (telos-goal-status ' + quote(identifier + ' ' + status) + '))'
                        job = {'id': 'sync-goal:' + goal['id'], 'source': source, 'goal': goal, 'restore': True}
                        break
                if not job:
                    for run in reversed(self.store.all('orchestration_runs')):
                        if run['status'] != 'running':
                            continue
                        completed = {node['id'] for node in run['nodes'] if node['status'] == 'completed'}
                        node = next((n for n in run['nodes'] if n['status'] == 'queued' and set(n['depends_on']) <= completed), None)
                        if node is None:
                            continue
                        node.update(status='running', started_at=now())
                        self.store.put('orchestration_runs', run)
                        self.store.event('orchestration.updated', run)
                        if node['kind'] == 'skill':
                            try:
                                self.validate_call(node['payload'])
                            except APIError as exc:
                                source = '(quote (Error ' + quote(exc.message) + '))'
                            else:
                                source = self.call_source(node['payload'])
                        else:
                            source = self.reasoning_source(node['payload']) if node['kind'] == 'reasoning' else self.call_source(node['payload']) if node['kind'] == 'skill' else '(send ' + quote(node['payload']['text']) + ')'
                        job = {'id': 'workflow:' + run['id'] + ':' + node['id'], 'source': source, 'workflow': run['id'], 'node': node['id'], 'restore': True}
                        break
        if not job:
            return []
        self.current = job
        with self.store.transaction():
            run_id = str(uuid.uuid4())
            job['run_id'] = run_id
            run = {'id': run_id, 'request_id': job['record']['id'] if 'record' in job else None,
                   'kind': 'operator', 'status': 'running', 'started_at': now(), 'goal_ids': [], 'operation_id': job['id']}
            self.store.put('runs', run)
            self.store.event('run.started', run, run['request_id'], run_id)
            if 'record' in job and job['record']['command'] == 'reasoning.run':
                record = self.store.get('reasoning', job['id'])
                record.update(status='running', started_at=now())
                self.store.put('reasoning', record)
                self.store.event('reasoning.updated', record, job['id'], run_id)
        return [job['id'], job['source']]

    def finish(self, identifier, result):
        job = self.current
        if not job or job['id'] != str(identifier):
            raise RuntimeError('Mismatched operator job completion')
        if isinstance(result, list) and len(result) == 1:
            result = result[0]
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                pass
        result = json.loads(json.dumps(result, ensure_ascii=False, default=str))
        error = failed(result)
        with self.store.transaction():
            evidence = {'id': str(uuid.uuid4()), 'kind': 'operator_result', 'title': job['record']['command'] if 'record' in job else job['id'],
                        'content': json.dumps(result, ensure_ascii=False, default=str)[:50000], 'status': 'failed' if error else 'recorded',
                        'at': now(), 'request_id': job['record']['id'] if 'record' in job else None, 'run_id': job['run_id']}
            self.store.put('evidence', evidence)
            self.store.event('evidence.created', evidence, evidence['request_id'], job['run_id'])
            if 'record' in job:
                record = job['record']
                command, payload = record['command'], record['payload']
                if command == 'reasoning.run':
                    item = self.store.get('reasoning', identifier)
                    item.update(status='failed' if error else 'completed', output=result, evidence_id=evidence['id'], finished_at=now())
                    self.store.put('reasoning', item)
                    self.store.event('reasoning.updated', item, identifier)
                if command in ('skill.save', 'skill.set_enabled'):
                    skill = self.store.get('skills', payload['name'])
                    skill.update(state='failed' if error else 'ready', error=result if error else None)
                    if not error:
                        skill['applied'] = {k: skill[k] for k in ('parameters', 'body')} if skill['enabled'] else None
                    self.store.put('skills', skill)
                    self.store.event('skill.updated', skill, identifier)
                if command == 'settings.update' and not error:
                    for key in payload:
                        if configuration.SCHEMA[key]['live']:
                            self.effective_config[key] = configuration.effective()[key]
                    self.store.event('settings.applied', self.settings(), identifier)
                self.store.finish(identifier, 'failed' if error else 'completed', {'output': result, 'evidence_id': evidence['id'], 'run_id': job['run_id']})
            if 'goal' in job:
                goal = self.store.get('goals', job['goal']['id'])
                if goal['updated_at'] == job['goal']['updated_at']:
                    goal['graph_sync'] = 'failed' if error else 'ready'
                    self.store.put('goals', goal)
                    self.store.event('goal.updated', goal)
            if 'workflow' in job:
                run = self.store.get('orchestration_runs', job['workflow'])
                node = next(n for n in run['nodes'] if n['id'] == job['node'])
                node.update(status='failed' if error else 'completed', output=result, evidence_id=evidence['id'], finished_at=now())
                if error:
                    for pending in run['nodes']:
                        if pending['status'] == 'queued':
                            pending['status'] = 'skipped'
                if run['status'] != 'cancelled':
                    if error:
                        run['status'] = 'failed'
                    elif all(n['status'] == 'completed' for n in run['nodes']):
                        run['status'] = 'completed'
                self.store.put('orchestration_runs', run)
                self.store.event('orchestration.updated', run)
            run = self.store.get('runs', job['run_id'])
            run.update(status='failed' if error else 'completed', finished_at=now())
            self.store.put('runs', run)
            self.store.event('run.' + run['status'], run, run['request_id'], run['id'])
        self.current = None
        if 'record' in job and job['record']['command'] == 'system.restart' and not error:
            import sys
            module = sys.modules['config']
            memory = Path(str(module.config_get_by_key('memoryDirectory')))
            (memory / '.restart-requested').write_text(job['record']['id'])
        return 'OPERATOR-JOB-RECORDED'

    def sync(self, catalog, goals, relations, report):
        def rows(value, tag=None):
            while isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
                if tag and value[0] and value[0][0] == tag:
                    break
                value = value[0]
            return value
        goals, relations, report = rows(goals, 'goal'), rows(relations, 'rel'), rows(report)
        skills = [{'name': str(row[0]), 'description': str(row[1]), 'parameters': [str(p) for p in row[2]]}
                  for row in catalog if isinstance(row, (tuple, list)) and len(row) == 3]
        graph = {'id': 'telos', 'goals': [{'id': str(g[1]), 'scope': str(g[2]), 'owner': str(g[3]), 'status': str(g[4])}
                 for g in goals if isinstance(g, (list, tuple)) and len(g) == 5 and g[0] == 'goal'],
                 'relations': [{'relation': str(r[1]), 'source': str(r[2]), 'target': str(r[3])}
                 for r in relations if isinstance(r, (list, tuple)) and len(r) == 4 and r[0] == 'rel'], 'report': report}
        with self.store.transaction():
            graph_status = {g['id']: g['status'] for g in graph['goals']}
            for goal in self.store.all('goals'):
                expected = {'active': 'active', 'paused': 'proposed', 'completed': 'achieved', 'cancelled': 'abandoned'}[goal['status']]
                if graph_status.get('operator-' + goal['id']) != expected and goal.get('graph_sync') != 'pending':
                    goal['graph_sync'] = 'pending'
                    self.store.put('goals', goal)
                    self.store.event('goal.updated', goal)
            previous = self.store.get('meta', 'skill_catalog')
            if not previous or previous['skills'] != skills:
                value = {'id': 'skill_catalog', 'skills': skills}
                self.store.put('meta', value)
                self.store.event('catalog.updated', value)
            if self.store.get('meta', 'telos') != graph:
                self.store.put('meta', graph)
                self.store.event('telos.updated', graph)
        return 'OPERATOR-VIEWS-SYNCED'
