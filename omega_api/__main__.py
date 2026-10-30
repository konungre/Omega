"""Run the operator API without an attached agent, for inspection and UI development."""
import argparse
import os

from .runtime import Runtime
from .server import Server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--database", default="memory/dashboard.sqlite3")
    args = parser.parse_args()
    runtime = Runtime(args.database)
    server = Server((args.host, args.port), runtime, os.environ.get("OMEGA_DASHBOARD_TOKEN", ""))
    print(f"Omega dashboard: http://{args.host}:{server.server_port} (agent offline)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stopping.set()
        server.server_close()
        runtime.store.close()


if __name__ == "__main__":
    main()
