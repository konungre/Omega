"""Run with python3 -m unittest discover -s tests -p test_dashboard_api.py -v.

These exercise the real HTTP server, SQLite journal, and agent bridge without
requiring a model provider, PeTTa, or third-party Python packages.
"""
import concurrent.futures
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from omega_api.runtime import Runtime
from omega_api.server import Server
from omega_api.store import APIError

TOKEN = 'test-only-omega-token-' + 'a' * 40


class Response:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def is_error(self):
        return bool(self.error)


class DashboardAPI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'operator.sqlite3'
        self.runtime = Runtime(self.path)
        self.runtime.connected = True
        self.server = Server(('127.0.0.1', 0), self.runtime, TOKEN)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.runtime.store.close()
        self.temp.cleanup()

    def http(self, method, path, body=None, token=TOKEN, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        request_headers = {'Content-Type': 'application/json'}
        if token:
            request_headers['Authorization'] = 'Bearer ' + token
        request_headers.update(headers or {})
        client.request(method, path, json.dumps(body) if body is not None else None, request_headers)
        response = client.getresponse()
        raw = response.read()
        content_type = response.getheader('Content-Type')
        result = json.loads(raw) if content_type.startswith('application/json') else raw.decode()
        status = response.status
        client.close()
        return status, result

    def command(self, identifier, command, payload):
        return self.http('POST', '/api/v1/commands', {'request_id': identifier, 'command': command, 'payload': payload})

    def snapshot(self):
        status, snapshot = self.http('GET', '/api/v1/snapshot')
        self.assertEqual(status, 200)
        return snapshot

    def test_auth_origin_and_static_boundary(self):
        for method, path in [('GET', '/api/v1/snapshot'), ('GET', '/api/v1/events'), ('POST', '/api/v1/commands')]:
            self.assertEqual(self.http(method, path, token=None)[0], 401)
            self.assertEqual(self.http(method, path, token='wrong')[0], 401)
        self.assertEqual(self.http('GET', '/api/v1/snapshot', headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.http('GET', '/')[0], 200)
        self.assertEqual(self.http('GET', '/vendor/OPENCLAW-LICENSE')[0], 200)
        self.assertEqual(self.http('GET', '/../../config/config.yaml')[0], 404)
        self.assertEqual(self.http('GET', '/omega_api/store.py')[0], 404)
        with self.assertRaises(ValueError):
            Server(('127.0.0.1', 0), self.runtime, '')
        self.assertNotIn(TOKEN, json.dumps(self.snapshot()))
        self.assertEqual(self.http('GET', '/api/v1/openapi.json')[1]['openapi'], '3.1.0')

    def test_command_lifecycle_exact_retry_and_conflicting_id(self):
        envelope = {'request_id': 'message-1', 'command': 'chat.send', 'payload': {'text': 'hello | keep this intact'}}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.http('POST', '/api/v1/commands', envelope), range(8)))
        self.assertEqual(sum(not result[1]['replayed'] for result in results), 1)
        self.assertEqual(len(self.snapshot()['messages']), 1)
        self.assertEqual(self.command('message-1', 'chat.send', {'text': 'different'})[0], 409)
        self.assertEqual(self.runtime.receive(), 'hello | keep this intact')
        self.runtime.begin_turn()
        self.runtime.send('answer')
        self.runtime.tool_result('(query "hello")', 'SUCCESS, RETURN: evidence')
        self.runtime.end_turn()
        self.assertEqual(self.http('GET', '/api/v1/requests/message-1')[1]['status'], 'completed')
        events = self.http('GET', '/api/v1/events?stream=0&after=0')[1]['events']
        types = [event['type'] for event in events]
        self.assertEqual(types.count('request.accepted'), 1)
        self.assertIn('request.started', types)
        self.assertIn('request.completed', types)
        self.assertTrue(all(event['request_id'] == 'message-1' for event in events))
        self.assertEqual(len(self.snapshot()['evidence']), 1)

    def test_goals_autonomy_configuration_and_terminal_states(self):
        self.assertEqual(self.command('goal-1', 'goal.create', {'title': 'Verify an outcome', 'description': 'Measure it'})[0], 200)
        self.assertIn('Verify an outcome', self.runtime.context())
        self.command('pause-goal', 'goal.set_status', {'goal_id': 'goal-1', 'status': 'paused'})
        self.assertNotIn('Verify an outcome', self.runtime.context())
        self.command('complete-goal', 'goal.set_status', {'goal_id': 'goal-1', 'status': 'completed'})
        self.assertEqual(self.command('resume-goal', 'goal.set_status', {'goal_id': 'goal-1', 'status': 'active'})[0], 409)
        self.assertEqual(self.command('config-1', 'config.update', {'autonomy_enabled': False})[0], 200)
        self.assertFalse(self.runtime.allow_turn('autonomous'))
        self.assertTrue(self.runtime.allow_turn('input'))
        self.assertEqual(self.command('config-invalid', 'config.update', {'autonomy_enabled': 'false'})[0], 400)
        self.assertEqual(self.command('secret', 'config.update', {'OPENAI_API_KEY': 'secret'})[0], 400)
        self.assertEqual(self.http('GET', '/api/v1/requests/secret')[0], 404)

    def test_snapshot_subscription_reconnect_has_no_gap(self):
        snapshot = self.snapshot()
        self.command('g1', 'goal.create', {'title': 'Created after snapshot'})
        client = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        client.request('GET', f"/api/v1/events?after={snapshot['cursor']}&instance_id={snapshot['instance_id']}&types=goal",
                       headers={'Authorization': 'Bearer ' + TOKEN})
        response = client.getresponse()
        self.assertEqual(response.status, 200)
        lines = []
        while not any(line.startswith('data: ') for line in lines):
            lines.append(response.readline().decode().strip())
        event = json.loads(next(line[6:] for line in lines if line.startswith('data: ')))
        self.assertEqual(event['type'], 'goal.created')
        self.assertEqual(event['data']['title'], 'Created after snapshot')
        cursor = event['seq']
        response.close()
        client.close()
        self.command('g2', 'goal.create', {'title': 'Created while disconnected'})
        events = self.http('GET', f'/api/v1/events?stream=0&after={cursor}')[1]['events']
        self.assertTrue(any(e['type'] == 'goal.created' and e['data']['id'] == 'g2' for e in events))
        self.assertTrue(all(e['seq'] > cursor for e in events))
        self.assertEqual(self.http('GET', '/api/v1/events?after=999999')[0], 409)
        self.assertEqual(self.http('GET', '/api/v1/events?after=0&instance_id=other')[0], 409)

    def test_restart_marks_inflight_interrupted_and_retains_queued_work(self):
        self.command('running', 'chat.send', {'text': 'in flight'})
        self.command('queued', 'chat.send', {'text': 'waiting'})
        self.runtime.receive()
        self.runtime.begin_turn()
        instance = self.snapshot()['instance_id']
        # Reopen only after the original server/DB are quiescent.
        self.server.shutdown()
        self.runtime.store.close()
        self.runtime = Runtime(self.path)
        self.server.runtime = self.runtime
        self.assertEqual(self.runtime.store.request('running')['status'], 'interrupted')
        self.assertEqual(self.runtime.store.request('queued')['status'], 'accepted')
        self.assertEqual(self.runtime.store.all('runs')[0]['status'], 'interrupted')
        self.assertEqual(self.runtime.store.snapshot(self.runtime.agent())['instance_id'], instance)
        self.assertEqual(self.runtime.receive(), 'waiting')

    def test_evidence_improvements_and_failures(self):
        self.runtime.begin_turn()
        evidence = json.loads(self.runtime.report('evidence', json.dumps({'title': 'Measurement', 'content': 'Before: 2, after: 3'})))
        report = {'title': 'An improvement', 'summary': 'Increased score', 'outcome': 'improved', 'evidence_ids': [evidence['id']]}
        improvement = json.loads(self.runtime.report('improvement', json.dumps(report)))
        self.assertEqual(improvement['attribution'], 'agent_report')
        report['evidence_ids'] = ['nonexistent']
        self.assertIn('error', json.loads(self.runtime.report('improvement', json.dumps(report))))
        self.assertEqual(self.http('GET', '/api/v1/evidence/' + evidence['id'])[1]['content'], 'Before: 2, after: 3')
        self.runtime.provider_result(Response('Provider unavailable'))
        self.runtime.end_turn()
        self.assertEqual(self.snapshot()['runs'][0]['status'], 'failed')
        self.assertEqual(len(self.snapshot()['improvements']), 1)

    def test_cancellation_and_offline_rejection(self):
        self.command('cancel-me', 'chat.send', {'text': 'queued'})
        self.assertEqual(self.command('cancel', 'request.cancel', {'request_id': 'cancel-me'})[0], 200)
        self.assertIsNone(self.runtime.receive() or None)
        self.command('running', 'chat.send', {'text': 'running'})
        self.runtime.receive()
        self.assertEqual(self.command('cannot-cancel', 'request.cancel', {'request_id': 'running'})[0], 409)
        self.runtime.connected = False
        self.assertEqual(self.command('offline', 'chat.send', {'text': 'hello'})[0], 503)
        self.assertEqual(self.http('GET', '/api/v1/requests/offline')[0], 404)

    def test_control_message_after_failure_is_not_infected_by_previous_turn(self):
        self.runtime.begin_turn()
        self.runtime.provider_result(Response('failure'))
        self.runtime.end_turn()
        self.command('control', 'chat.send', {'text': 'control handled without provider'})
        self.runtime.receive()
        self.runtime.end_turn()
        self.assertEqual(self.runtime.store.request('control')['status'], 'completed')

    def test_database_has_one_owner(self):
        with self.assertRaisesRegex(RuntimeError, 'active owner'):
            Runtime(self.path)
        # A failed second start must not interrupt an active request.
        self.command('still-running', 'chat.send', {'text': 'work'})
        self.runtime.receive()
        with self.assertRaises(RuntimeError):
            Runtime(self.path)
        self.assertEqual(self.runtime.store.request('still-running')['status'], 'running')

    def test_invalid_inputs_do_not_mutate_state(self):
        before = self.snapshot()['cursor']
        for body in [[], None, {}, {'request_id': 'x', 'command': 'unknown', 'payload': {}},
                     {'request_id': 'x', 'command': 'chat.send', 'payload': {'text': ''}},
                     {'request_id': 'x/y', 'command': 'goal.create', 'payload': {'title': 'bad ID'}},
                     {'request_id': 'x', 'command': 'goal.set_status', 'payload': {'goal_id': [], 'status': 'active'}}]:
            status, _ = self.http('POST', '/api/v1/commands', body)
            self.assertIn(status, [400, 413])
        self.assertEqual(self.snapshot()['cursor'], before)
        client = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        client.request('POST', '/api/v1/commands', '{bad json', {'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'})
        response = client.getresponse()
        self.assertEqual(response.status, 400)
        response.read()
        client.close()


if __name__ == '__main__':
    unittest.main()
