"""Check an actual protected dashboard release through temporary loopback servers.

Requires Node.js, Playwright and Chrome. No Spark or generated clinical files
are needed. The default output is a new ignored work/ directory, preserving
previous evidence. Use --output docs/evidence only for a final release.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def runtime_path():
    discovered = shutil.which("node")
    if discovered:
        return Path(discovered)
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    return bundled if bundled.is_file() else None


def unused_loopback_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dashboard-root", type=Path, default=ROOT / "dashboard")
    parser.add_argument("--node", type=Path, default=runtime_path())
    parser.add_argument("--node-modules", type=Path)
    parser.add_argument("--chrome-channel", default="chrome")
    parser.add_argument("--label", default="Actual exported release; scale not inferred from aggregate rows")
    args = parser.parse_args()
    if not args.node or not args.node.is_file():
        parser.error("Node.js was not found; provide --node /absolute/path/to/node")
    dashboard = args.dashboard_root.resolve()
    release = dashboard / "data/dashboard.json"
    if not release.is_file():
        parser.error("The actual dashboard export is missing; no fixture fallback is permitted")
    evidence = (args.output or ROOT / "work" / ("browser-qa-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    if any(evidence.iterdir()):
        parser.error("Evidence output must be empty so an older validation cannot be overwritten")
    national_token, district_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    national_port, district_port = unused_loopback_port(), unused_loopback_port()
    while district_port == national_port:
        district_port = unused_loopback_port()
    env = os.environ.copy()
    modules = args.node_modules
    if modules is None:
        candidate = args.node.resolve().parent.parent / "node_modules"
        if candidate.is_dir():
            modules = candidate
    if modules:
        env["NODE_PATH"] = str(modules.resolve()) + (os.pathsep + env["NODE_PATH"] if env.get("NODE_PATH") else "")
    env.update({
        "EPISCOPE_BROWSER_CHANNEL": args.chrome_channel,
        "EPISCOPE_URL": f"http://127.0.0.1:{national_port}",
        "EPISCOPE_ROLE_URL": f"http://127.0.0.1:{district_port}",
        "EPISCOPE_SCREENSHOT_DIR": str(evidence),
        "EPISCOPE_DASHBOARD_FILE": str(release),
        "EPISCOPE_EVIDENCE_LABEL": args.label,
        "EPISCOPE_DISTRICT_TOKEN": district_token,
        "EPISCOPE_NATIONAL_TOKEN": national_token,
    })
    role_env = env.copy()
    role_env["EPISCOPE_TOKENS"] = json.dumps({national_token: {"role": "national"}, district_token: {"role": "district", "district_id": "01"}})
    national_env = env.copy()
    national_env.pop("EPISCOPE_TOKENS", None)
    context = {"started_at": datetime.now(timezone.utc).isoformat(), "label": args.label, "release_sha256": hashlib.sha256(release.read_bytes()).hexdigest(), "release_bytes": release.stat().st_size, "python": sys.version.split()[0], "node": str(args.node), "browser_channel": args.chrome_channel}
    (evidence / "run-context.json").write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")
    servers, logs, runs = [], [], []
    try:
        for port, server_env in [(national_port, national_env), (district_port, role_env)]:
            log = (evidence / f"qa-server-{port}.log").open("w", encoding="utf-8")
            logs.append(log)
            server = subprocess.Popen([sys.executable, "-m", "serving.server", "--root", str(dashboard), "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=server_env, stdout=log, stderr=log)
            servers.append(server)
            ready = False
            for _ in range(50):
                if server.poll() is not None:
                    raise RuntimeError(f"QA server exited before readiness; inspect {log.name}")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                        ready = json.load(response).get("data_ready") is True
                    if ready:
                        break
                except OSError:
                    pass
                time.sleep(.1)
            if not ready:
                raise RuntimeError(f"QA server {port} did not become ready")
        for script, overrides in [
            ("tests/browser_smoke.cjs", {}),
            ("tests/browser_scope.cjs", {"EPISCOPE_URL": env["EPISCOPE_ROLE_URL"]}),
            ("tests/browser_release_qa.cjs", {}),
        ]:
            result = subprocess.run([str(args.node), script], cwd=ROOT, env=env | overrides, capture_output=True, text=True, timeout=180)
            print(script)
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            runs.append({"script": script, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
            (evidence / "browser-run-results.json").write_text(json.dumps(runs, indent=2) + "\n", encoding="utf-8")
            if result.returncode:
                raise SystemExit(result.returncode)
        context.update({"completed_at": datetime.now(timezone.utc).isoformat(), "ok": True})
        (evidence / "run-context.json").write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")
        print(f"Browser QA evidence: {evidence}")
    finally:
        for server in servers:
            if server.poll() is None:
                server.terminate()
        for server in servers:
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
