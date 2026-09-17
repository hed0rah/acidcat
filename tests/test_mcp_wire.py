"""The MCP server has to survive contact with an actual client.

Every existing MCP test calls `dispatch(name, args)` in-process, which is why
`transport.py` -- the whole wire adapter -- had no coverage at all, and why this
shipped:

    $ pip install acidcat[mcp]        # resolves mcp 2.0.0
    $ acidcat-mcp
    AttributeError: 'Server' object has no attribute 'list_tools'

mcp 2.0 removed the low-level decorators the server is built on. The pin was
`mcp>=1.0`, unbounded, so every new install got a server that died on startup
and a documented Claude Desktop config that did nothing. No in-process test can
see that; only starting the thing can.

These drive the real stdio transport as a subprocess, speaking JSON-RPC.
"""

import json
import os
import subprocess
import sys

import pytest

# ten seconds and up per test: run in the full suite, skipped in the edit loop
pytestmark = pytest.mark.slow

pytest.importorskip("mcp")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"}}}
_INITED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


class _Server:
    """A live stdio server, kept open while we talk to it.

    Writing every message and closing stdin up front does not work: the server
    shuts down on EOF, cancelling anything still in flight. `tools/list`
    answered in time and `tools/call` did not, which looked like a product bug
    and was a harness bug.
    """

    def __init__(self, registry=None):
        env = dict(os.environ, PYTHONPATH=os.path.join(REPO, "src"))
        if registry:
            env["ACIDCAT_REGISTRY"] = str(registry)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "acidcat.mcp_server", "--transport", "stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, cwd=REPO, env=env)

    def send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def await_id(self, want, limit=80):
        """Read until the response with id `want` arrives, or give up."""
        for _ in range(limit):
            line = self.proc.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want:
                return msg
        return None

    def close(self):
        for step in (lambda: self.proc.stdin.close(),
                     lambda: self.proc.wait(timeout=15)):
            try:
                step()
            except Exception:
                pass
        if self.proc.poll() is None:
            self.proc.kill()

    def stderr_tail(self, n=1500):
        try:
            self.proc.stderr.close()
        except Exception:
            pass
        return ""


def _handshaken(registry):
    s = _Server(registry)
    s.send(_INIT)
    first = s.await_id(1)
    s.send(_INITED)
    return s, first


def test_the_server_starts_and_handshakes(tmp_path):
    """The regression: it did not. An unbounded `mcp` pin resolved 2.0 and the
    process died in _build_app before reading a byte of input."""
    s, first = _handshaken(tmp_path / "reg.db")
    try:
        assert first is not None, "no handshake -- the server did not start"
        result = first["result"]
        assert result["serverInfo"]["name"] == "acidcat"
        assert "tools" in result["capabilities"]
    finally:
        s.close()


def test_tools_list_returns_the_whole_registry(tmp_path):
    """Over the wire, not from the TOOLS dict. The in-process test asserts a
    hardcoded 18-name subset with `issubset`, so a tool dropped from the
    registry passes it."""
    from acidcat.mcp_server import TOOLS
    registered = {t["name"] for t in TOOLS}      # TOOLS is a list of specs
    s, _ = _handshaken(tmp_path / "reg.db")
    try:
        s.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = s.await_id(2)
        assert resp is not None, "no tools/list response"
        listed = {t["name"] for t in resp["result"]["tools"]}
    finally:
        s.close()
    assert listed == registered, (
        f"the wire disagrees with the registry: "
        f"missing={registered - listed} extra={listed - registered}")
    assert len(listed) == 19, f"expected 19 tools, the wire offered {len(listed)}"


def test_every_tool_advertises_a_usable_schema(tmp_path):
    s, _ = _handshaken(tmp_path / "reg.db")
    try:
        s.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = s.await_id(2)
        assert resp is not None
        tools = resp["result"]["tools"]
    finally:
        s.close()
    assert tools
    for t in tools:
        assert t.get("description"), f"{t['name']} has no description"
        schema = t.get("inputSchema")
        assert isinstance(schema, dict) and schema.get("type") == "object", \
            f"{t['name']} has no object inputSchema"


def test_a_tool_call_returns_a_result_over_the_wire(tmp_path):
    """index_stats needs no registered library, so it is the safe probe."""
    s, _ = _handshaken(tmp_path / "reg.db")
    try:
        s.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "index_stats", "arguments": {}}})
        call = s.await_id(3)
    finally:
        s.close()
    assert call is not None, "tools/call produced no response"
    assert "result" in call, call


def test_an_unknown_tool_is_an_error_not_a_dead_connection(tmp_path):
    """A bad call must come back as an error; dropping the connection would
    take the client's whole session with it."""
    s, _ = _handshaken(tmp_path / "reg.db")
    try:
        s.send({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "no_such_tool", "arguments": {}}})
        call = s.await_id(4)
    finally:
        s.close()
    assert call is not None, "the server dropped the connection on a bad tool"
    assert call.get("result") or call.get("error"), call


def test_python_dash_m_is_a_working_entry_point():
    """MCP client configs routinely spell the server as an interpreter plus
    module, because that survives not activating a venv. Without __main__.py
    that form did not exist."""
    env = dict(os.environ, PYTHONPATH=os.path.join(REPO, "src"))
    p = subprocess.run([sys.executable, "-m", "acidcat.mcp_server", "--version"],
                       capture_output=True, text=True, timeout=60,
                       cwd=REPO, env=env)
    assert p.returncode == 0, p.stderr
    assert "acidcat" in (p.stdout + p.stderr).lower()


# ------------------------------------------------------- argument hardening

def test_a_negative_limit_is_refused_not_treated_as_unlimited():
    """SQLite reads `LIMIT -5` as NO limit, and `merged[:-5]` then just drops
    the tail -- so a negative limit fanned out over every registered library.
    An audit got 38 rows from `locate_sample{limit:-3}` on a 41-sample index;
    on a 1.2M-sample corpus `limit: -1` pulls everything into the model's
    context."""
    from acidcat.mcp_server.handlers import ToolError, _limit
    for key in ("limit", "n"):
        with pytest.raises(ToolError, match="zero or positive"):
            _limit({key: -1}, key, 50)


def test_zero_means_zero_not_the_default():
    """`int(args.get("limit") or 50)` turned an explicit 0 into 50, because
    `0 or 50` is 50."""
    from acidcat.mcp_server.handlers import _limit
    assert _limit({"limit": 0}, "limit", 50) == 0
    assert _limit({}, "limit", 50) == 50


def test_an_absurd_limit_is_capped():
    from acidcat.mcp_server.handlers import _limit
    assert _limit({"limit": 10 ** 9}, "limit", 50) == 10_000


def test_a_non_numeric_limit_is_a_clean_error():
    from acidcat.mcp_server.handlers import ToolError, _limit
    with pytest.raises(ToolError, match="whole number"):
        _limit({"limit": "lots"}, "limit", 50)


def test_count_schemas_publish_their_minimum():
    """A client should be told the constraint rather than discovering it as a
    fan-out."""
    from acidcat.mcp_server import TOOLS
    for tool in TOOLS:
        props = tool["input_schema"].get("properties", {})
        for name in ("limit", "n"):
            if name in props and props[name].get("type") == "integer":
                assert "minimum" in props[name], \
                    f"{tool['name']}.{name} has no minimum"


def test_http_transport_pins_host_and_origin():
    """The SDK disables DNS-rebinding protection when security_settings is
    None, and this server is stateless with no auth -- so any browser page
    could POST to the loopback port with a forged Origin and reach every tool,
    including destructive ones."""
    from acidcat.mcp_server.transport import _security_settings
    s = _security_settings("127.0.0.1", 8765)
    if s is None:
        pytest.skip("installed mcp SDK has no TransportSecuritySettings")
    assert s.enable_dns_rebinding_protection is True
    assert "127.0.0.1:8765" in s.allowed_hosts
    assert "http://127.0.0.1:8765" in s.allowed_origins
    assert not any("attacker" in o for o in s.allowed_origins)


def test_binding_beyond_loopback_does_not_widen_the_allow_list():
    from acidcat.mcp_server.transport import _security_settings
    s = _security_settings("0.0.0.0", 9000)
    if s is None:
        pytest.skip("installed mcp SDK has no TransportSecuritySettings")
    assert s.allowed_hosts == ["0.0.0.0:9000"]
