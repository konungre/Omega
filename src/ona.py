"""Persistent native OpenNARS-for-Applications sessions.

All reasoning runs in the real NAR binary. Operations are returned as data;
this bridge does not silently translate them into host shell commands.
"""
import atexit
import json
import os
from pathlib import Path
import queue
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid


class ONAError(RuntimeError):
    pass


def _setting(key, default):
    try:
        from config import config_get_by_key
        return config_get_by_key(key, default)
    except ImportError:
        return os.environ.get('OMEGA_' + key, default)


def binary_path():
    path = str(_setting('onaBinary', '/usr/local/bin/omega-nar'))
    resolved = shutil.which(path)
    if not resolved:
        raise ONAError('ONA binary is not installed: ' + path)
    return resolved


def parse_output(raw):
    result = {'inputs': [], 'derivations': [], 'answers': [], 'operations': [], 'raw': raw}
    for line in raw.splitlines():
        if line.startswith('^'):
            result['operations'].append({'operator': line.split()[0], 'output': line})
            continue
        match = re.match(r'(Input|Derived|Revised|Answer):\s*(.*)', line)
        if not match:
            continue
        label, content = match.groups()
        if label == 'Answer' and content.strip() == 'None.':
            continue
        entry = {'text': content}
        truth = re.search(r'frequency=([0-9.eE+-]+),?\s+confidence=([0-9.eE+-]+)', content)
        if truth:
            entry['truth'] = {'frequency': float(truth[1]), 'confidence': float(truth[2])}
        occurrence = re.search(r'occurrenceTime=([^ ]+)', content)
        entry['occurrence_time'] = occurrence[1] if occurrence else 'now' if ':|:' in content else 'eternal'
        result[{'Input': 'inputs', 'Derived': 'derivations', 'Revised': 'derivations', 'Answer': 'answers'}[label]].append(entry)
    return result


class NativeSession:
    def __init__(self, binary, timeout=20):
        self.timeout = timeout
        self.process = subprocess.Popen([binary, 'shell'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.output = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True, name='ona-output')
        self.reader.start()
        self.exchange('*volume=100')

    def _read(self):
        try:
            for line in self.process.stdout:
                self.output.put(line)
        finally:
            self.output.put(None)

    def exchange(self, source):
        marker = 'omega-end-' + uuid.uuid4().hex
        try:
            self.process.stdin.write(source.rstrip() + '\n//' + marker + '\n')
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            raise ONAError('ONA process exited before accepting input') from None
        lines, size = [], 0
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                line = self.output.get(timeout=max(.001, deadline - time.monotonic()))
            except queue.Empty:
                self.close()
                raise ONAError('ONA inference timed out; the committed session will replay on its next request') from None
            if line is None:
                self.close()
                raise ONAError('ONA rejected the input or exited:\n' + ''.join(lines)[-4000:])
            if line.strip() == 'Comment: ' + marker:
                break
            size += len(line)
            if size > 1_000_000:
                self.close()
                raise ONAError('ONA output exceeded 1 MB; reduce the inference budget')
            lines.append(line)
        return ''.join(lines)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.process.stdin.close()
        self.reader.join(timeout=2)
        self.process.stdout.close()


class ONAEngine:
    def __init__(self, database, binary=None):
        self.binary = binary or binary_path()
        self.lock = threading.RLock()
        self.sessions = {}
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY, session TEXT NOT NULL, source TEXT NOT NULL)')
        self.db.commit()

    def _session(self, identifier):
        current = self.sessions.get(identifier)
        if current and current.process.poll() is None:
            return current
        if current:
            current.close()
        if len(self.sessions) >= 4:
            name = next(iter(self.sessions))
            self.sessions.pop(name).close()
        session = NativeSession(self.binary)
        try:
            for (source,) in self.db.execute('SELECT source FROM journal WHERE session=? ORDER BY id', (identifier,)):
                session.exchange(source)
        except BaseException:
            session.close()
            raise
        self.sessions[identifier] = session
        return session

    @staticmethod
    def validate(identifier, source, cycles):
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', identifier):
            raise ValueError('Session must be 1–80 letters, digits, dots, colons, underscores, or hyphens')
        if not isinstance(source, str) or len(source) > 12000 or '\x00' in source:
            raise ValueError('Narsese input must be text of at most 12000 characters')
        if type(cycles) is not int or not 0 <= cycles <= 1000:
            raise ValueError('Inference cycles must be an integer from 0 to 1000')
        for line in source.splitlines():
            if len(line.encode()) > 500:
                raise ValueError('Each ONA input line must be at most 500 bytes')
            if line.strip().isdigit():
                raise ValueError('Use the cycles field for the inference budget')
            if line.strip().startswith('*setopstdin'):
                raise ValueError('Blocking stdin callbacks are not supported; feed operation outcomes as Narsese events')
            if line.strip() == '*reset':
                raise ValueError('Use the session reset command to keep persistent state consistent')

    def run(self, identifier, source, cycles=20):
        self.validate(identifier, source, cycles)
        with self.lock:
            session = self._session(identifier)
            transcript = source.strip() + ('\n' if source.strip() else '') + str(cycles)
            try:
                raw = session.exchange(transcript)
            except ONAError:
                self.sessions.pop(identifier, None)
                session.close()
                raise
            self.db.execute('INSERT INTO journal(session,source) VALUES (?,?)', (identifier, transcript))
            self.db.commit()
            return parse_output(raw) | {'session': identifier, 'cycles': cycles, 'engine': 'ona'}

    def reset(self, identifier):
        self.validate(identifier, '', 0)
        with self.lock:
            session = self.sessions.pop(identifier, None)
            if session:
                session.close()
            self.db.execute('DELETE FROM journal WHERE session=?', (identifier,))
            self.db.commit()
        return {'session': identifier, 'status': 'reset'}

    def status(self):
        with self.lock:
            return {'available': True, 'binary': self.binary,
                    'sessions': [{'id': row[0], 'committed_inputs': row[1],
                                  'running': row[0] in self.sessions and self.sessions[row[0]].process.poll() is None}
                                 for row in self.db.execute('SELECT session,COUNT(*) FROM journal GROUP BY session')]}

    def close(self):
        with self.lock:
            for session in self.sessions.values():
                session.close()
            self.sessions.clear()
            self.db.close()


_engine = None
_engine_lock = threading.Lock()


def engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            memory = Path(str(_setting('memoryDirectory', Path(__file__).resolve().parents[1] / 'memory')))
            memory.mkdir(parents=True, exist_ok=True)
            _engine = ONAEngine(memory / 'ona.sqlite3')
        return _engine


def start():
    engine().run('default', '', 0)
    return 'ONA-READY'


def run_json(payload):
    try:
        data = json.loads(str(payload))
        return json.dumps(engine().run(data.get('session', 'default'), data.get('input', ''), data.get('cycles', 20)))
    except (ValueError, ONAError) as exc:
        return json.dumps({'error': str(exc), 'engine': 'ona'})


def input_narsese(source):
    return run_json(json.dumps({'input': str(source), 'session': 'default', 'cycles': 20}))


def cycle(count):
    return run_json(json.dumps({'input': '', 'session': 'default', 'cycles': int(count)}))


def reset(identifier='default'):
    return json.dumps(engine().reset(str(identifier)))


def status():
    try:
        return engine().status()
    except (ONAError, OSError) as exc:
        return {'available': False, 'error': str(exc), 'sessions': []}


def close():
    global _engine
    with _engine_lock:
        if _engine is not None:
            _engine.close()
            _engine = None


atexit.register(close)
