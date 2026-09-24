"""Start a dry-run daemon for the JS tests; print its ports as JSON, stop on stdin EOF."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import DryRunDaemon  # noqa: E402

fixture = DryRunDaemon()
print(json.dumps({"tcp_port": fixture.tcp_port, "ws_port": fixture.ws_port}), flush=True)
sys.stdin.read()
fixture.stop()
