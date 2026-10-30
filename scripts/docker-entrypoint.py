#!/usr/bin/env python3
"""Supervise the bundled local model and Omega; no keys or external service setup."""
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(os.environ.get('OMEGA_DIR', '/PeTTa/repos/Omega'))
MEMORY = Path(os.environ.get('MEMORY_DIR', str(ROOT / 'memory')))
children = []
stopping = False


def shutdown(signum=None, frame=None):
    global stopping
    stopping = True
    for child in children:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)


def ready(url):
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError):
        return False


def main():
    MEMORY.mkdir(parents=True, exist_ok=True)
    if os.getuid() == 0:
        for directory, _, files in os.walk(MEMORY):
            os.chown(directory, 65534, 65534)
            for name in files:
                os.chown(Path(directory) / name, 65534, 65534)
    token_file = MEMORY / '.dashboard-token'
    token = os.environ.get('OMEGA_DASHBOARD_TOKEN')
    if not token:
        token = token_file.read_text().strip() if token_file.exists() else secrets.token_urlsafe(32)
    if len(token.encode()) < 32:
        raise ValueError('Dashboard token must contain at least 32 bytes')
    descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        output.write(token + '\n')
    os.chmod(token_file, 0o600)
    if os.getuid() == 0:
        os.chown(token_file, 65534, 65534)
    os.environ['OMEGA_DASHBOARD_TOKEN'] = token
    os.environ['OMEGA_MANAGED_CONTAINER'] = '1'
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    arguments = sys.argv[1:]
    if not any(arg.startswith('dashboardHost=') for arg in arguments):
        arguments.append('dashboardHost=0.0.0.0')
    model = subprocess.Popen([
        '/usr/local/bin/omega-llama-server', '-m', '/opt/omega/models/SmolLM3-Q4_K_M.gguf',
        '--alias', 'SmolLM3-3B', '--host', '127.0.0.1', '--port', '8089',
        '--ctx-size', os.environ.get('OMEGA_LOCAL_CONTEXT', '16384'), '--parallel', '1',
        '--threads', os.environ.get('OMEGA_LOCAL_THREADS', str(max(1, min(8, os.cpu_count() or 4)))),
        '--jinja', '--no-ui'], start_new_session=True,
        user=65534 if os.getuid() == 0 else None, group=65534 if os.getuid() == 0 else None)
    children.append(model)
    deadline = time.monotonic() + 180
    while not stopping and not ready('http://127.0.0.1:8089/health'):
        if model.poll() is not None:
            raise RuntimeError('The bundled SmolLM3 server exited during startup')
        if time.monotonic() > deadline:
            raise RuntimeError('The bundled SmolLM3 model did not become ready within 180 seconds')
        time.sleep(.5)
    if stopping:
        return 0
    def start_agent():
        import json
        persisted = MEMORY / 'operator-config.json'
        settings = json.loads(persisted.read_text()) if persisted.exists() else {}
        args = list(arguments)
        for key in ('openaiapi_url', 'MM_URL', 'embeddingprovider', 'embeddingModel'):
            if key in settings and not any(a.startswith(key + '=') for a in args):
                args.append(key + '=' + str(settings[key]))
        if 'openClawURL' in settings:
            args.append('openclaw_url=' + str(settings['openClawURL']))
        return subprocess.Popen([str(ROOT / 'entrypoint.sh'), *args], start_new_session=True)
    agent = start_agent()
    children.append(agent)
    announced = False
    while not stopping:
        restart = MEMORY / '.restart-requested'
        if restart.exists():
            restart.unlink()
            print('Restarting Omega with the saved configuration...', flush=True)
            os.killpg(agent.pid, signal.SIGTERM)
            try:
                agent.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(agent.pid, signal.SIGKILL)
                agent.wait(timeout=3)
            children.remove(agent)
            subprocess.run(['su', 'www-data', '-s', '/bin/sh', '-c', 'nginx -c /opt/nginx/nginx.conf -s quit'],
                           timeout=10, check=False)
            time.sleep(1)
            agent = start_agent()
            children.append(agent)
            announced = False
        if not announced and ready('http://127.0.0.1:8088/'):
            print('\nOmega is ready. Open this local sign-in link:\n'
                  f'http://localhost:8088/#connect={token}\n', flush=True)
            announced = True
        for child in children:
            if child.poll() is not None:
                raise RuntimeError(f'Omega service exited with status {child.returncode}')
        time.sleep(.5)
    return 0


if __name__ == '__main__':
    code = 0
    try:
        code = main()
    except Exception as exc:
        print('Omega startup failed: ' + str(exc), file=sys.stderr, flush=True)
        code = 1
    finally:
        shutdown()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=3)
    sys.exit(code)
