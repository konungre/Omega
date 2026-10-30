"""Opt-in dashboard communication channel and embedded operator API."""
import os
from pathlib import Path
import sys
import threading

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import channels
from config import config_get_by_key
from omega_api.runtime import Runtime, attach
from omega_api.server import Server


class DashboardChannel(channels.CommChannel):
    def __init__(self):
        self.runtime = None

    def start(self):
        if self.runtime:
            return
        # Do not use config_get_by_key for credentials: it logs resolved values.
        token = os.environ.get("OMEGA_DASHBOARD_TOKEN", "")
        if len(token.encode()) < 32:
            raise ValueError("Set OMEGA_DASHBOARD_TOKEN to at least 32 random bytes")
        memory = Path(str(config_get_by_key("memoryDirectory", str(_ROOT / "memory"))))
        runtime = Runtime(memory / "dashboard.sqlite3")
        try:
            server = Server((str(config_get_by_key("dashboardHost", "127.0.0.1")),
                             int(config_get_by_key("dashboardPort", 8088))), runtime, token)
        except BaseException:
            runtime.store.close()
            raise
        attach(runtime)
        self.runtime = runtime
        threading.Thread(target=server.serve_forever, name="omega-dashboard-api", daemon=True).start()

    def receive(self):
        return self.runtime.receive()

    def send(self, message):
        self.runtime.send(message)

    def stop(self):
        if self.runtime:
            self.runtime.stop()


def loadOmegaPlugin():
    channels.registerCommChannel("dashboard", DashboardChannel())
