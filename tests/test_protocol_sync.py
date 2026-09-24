"""Guard against the JS client's protocol constants drifting from the Python ones."""

import re
import unittest
from pathlib import Path

from synth_module_client import protocol

JS_CLIENT = Path(__file__).resolve().parent.parent / "clients" / "js" / "synth-module-client.js"


def _js_object(source: str, name: str) -> dict:
    """Extract `NAME: 0x..` entries from `export const <name> = Object.freeze({...})`."""
    block = re.search(rf"export const {name} = Object\.freeze\(\{{(.*?)\}}\);", source, re.S)
    if block is None:
        raise AssertionError(f"{name} not found in {JS_CLIENT.name}")
    return {key: int(value, 16) for key, value in
            re.findall(r"(\w+):\s*(0x[0-9a-fA-F]+)", block.group(1))}


class TestProtocolSync(unittest.TestCase):
    """JS and Python must agree on every command, response and constant."""

    @classmethod
    def setUpClass(cls):
        cls.source = JS_CLIENT.read_text()

    def test_commands_match(self):
        py = {k[len("CMD_"):]: v for k, v in vars(protocol).items() if k.startswith("CMD_")}
        self.assertEqual(_js_object(self.source, "CMD"), py)

    def test_responses_match(self):
        py = {k[len("RESP_"):]: v for k, v in vars(protocol).items() if k.startswith("RESP_")}
        self.assertEqual(_js_object(self.source, "RESP"), py)

    def test_scalars_match(self):
        self.assertIn(f"export const MSG_SIZE = {protocol.MSG_SIZE};", self.source)
        self.assertIn(f"export const DEFAULT_WS_PORT = {protocol.WEBSOCKET_PORT};", self.source)


if __name__ == "__main__":
    unittest.main()
