"""acidcat-mcp transports -- dispatch + stdio/HTTP servers + CLI entry.

dispatch(name, args) routes to the handler registered in schema.TOOLS; main()
parses args, sets the registry path on the handlers module, and runs the stdio
or streamable-HTTP transport. Importing schema here also triggers tool
registration (populating TOOLS).
"""

import argparse
import asyncio
import importlib.util
import json
import os
import sys

from acidcat import __version__
from acidcat.core.catalogue import paths as acidpaths
from acidcat.mcp_server import handlers
from acidcat.mcp_server.handlers import ToolError
from acidcat.mcp_server.schema import TOOLS


def dispatch(name, arguments):
    """Call a tool by name with a dict of arguments. Raises ToolError or
    returns dict."""
    for t in TOOLS:
        if t["name"] == name:
            # a misspelt argument was ignored, so search_samples(query="808")
            # quietly returned every sample: name it and the ones that exist
            known = set((t["input_schema"].get("properties") or {}))
            unknown = sorted(set(arguments or {}) - known)
            if unknown:
                raise ToolError(f"{name}: unknown argument(s) {', '.join(unknown)}; "
                                f"it takes: {', '.join(sorted(known)) or 'none'}")
            return t["handler"](arguments or {})
    raise ToolError(f"unknown tool: {name}")


# ── server construction (shared by the stdio + http transports) ──────

_MCP_MISSING = ("acidcat-mcp: the mcp package is not installed. "
                "Install with: pip install acidcat[mcp]")


def _build_app():
    """Build the low-level MCP Server with acidcat's tools wired to dispatch().
    Shared by both transports. Imports mcp lazily so the package stays optional."""
    from mcp.server import Server
    import mcp.types as mcp_types

    app = Server("acidcat", version=__version__)

    @app.list_tools()
    async def _list_tools():
        out = []
        for t in TOOLS:
            try:
                annotations = mcp_types.ToolAnnotations(**t["annotations"])
            except TypeError:
                # an older SDK may not know a hint key; drop annotations rather
                # than fail the whole listing.
                annotations = None
            tool_kwargs = {
                "name": t["name"],
                "title": t["annotations"].get("title")
                or t["name"].replace("_", " ").title(),
                "description": t["description"],
                "inputSchema": t["input_schema"],
            }
            if annotations is not None:
                tool_kwargs["annotations"] = annotations
            out.append(mcp_types.Tool(**tool_kwargs))
        return out

    @app.call_tool()
    async def _call_tool(name, arguments):
        # tool-execution failures come back as a CallToolResult with isError so
        # the client and model see them as errors, not as a successful payload
        # that happens to hold an "error" string.
        #
        # dispatch is synchronous and does blocking work (SQLite fan-out, and for
        # find_similar/analyze the numpy/librosa stack). Run it off the event-loop
        # thread: on Windows the numpy path deadlocks the proactor loop if run
        # inline, and blocking the loop stalls the stdio pipe regardless. The
        # cached fan-out connections are opened check_same_thread=False for exactly
        # this cross-thread use.
        try:
            result = _jsonable(await asyncio.to_thread(dispatch, name, arguments or {}))
        except ToolError as e:
            return mcp_types.CallToolResult(
                content=[mcp_types.TextContent(
                    type="text", text=json.dumps({"error": str(e)}))],
                isError=True)
        except Exception as e:
            return mcp_types.CallToolResult(
                content=[mcp_types.TextContent(
                    type="text",
                    text=json.dumps({"error": f"internal: {e.__class__.__name__}: {e}"}))],
                isError=True)
        text = json.dumps(result, default=str, indent=2)
        structured = result if isinstance(result, dict) else {"result": result}
        # a handler that returns {"error": ...} (e.g. missing analysis deps) is a
        # soft failure; flag it too.
        is_error = isinstance(result, dict) and "error" in result
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=text)],
            structuredContent=structured,
            isError=is_error)

    return app


async def _run_stdio():
    try:
        from mcp.server.stdio import stdio_server
    except ImportError:
        print(_MCP_MISSING, file=sys.stderr)
        sys.exit(1)
    app = _build_app()
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _security_settings(host, port):
    """Host/Origin allow-lists for the HTTP transport, or None if unavailable.

    Only the address actually served is allowed. A browser page cannot forge
    the Host header, so pinning it is what stops a rebinding attack reaching a
    loopback-bound server that has no auth of its own.
    """
    try:
        from mcp.server.transport_security import TransportSecuritySettings
    except ImportError:                       # older SDK: nothing to configure
        return None
    names = sorted(_LOOPBACK | {host}) if host in _LOOPBACK else [host]
    hosts = [f"{n}:{port}" for n in names]
    origins = [f"http://{h}" for h in hosts] + [f"https://{h}" for h in hosts]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts, allowed_origins=origins)


def _run_http(host, port, json_response):
    """Serve over Streamable HTTP (the modern replacement for the SSE transport).
    Stateless (no server-side session state), mounted at /mcp, so it can sit
    behind a proxy. Needs the http extra (starlette + uvicorn)."""
    try:
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    except ImportError:
        print(_MCP_MISSING, file=sys.stderr)
        sys.exit(1)
    try:
        import contextlib
        from starlette.applications import Starlette
        from starlette.routing import Mount
        import uvicorn
    except ImportError:
        print("acidcat-mcp: the http transport needs starlette + uvicorn. "
              "Install with: pip install acidcat[mcp-http]", file=sys.stderr)
        sys.exit(1)

    app = _build_app()

    # DNS-rebinding protection, explicitly. The SDK treats security_settings=None
    # as "disable it, for backwards compatibility"
    # (mcp/server/transport_security.py: `settings or
    # TransportSecuritySettings(enable_dns_rebinding_protection=False)`), so
    # leaving it unset is an opt-out rather than a default.
    #
    # That matters here because this server is stateless (no session to guess)
    # and has no authentication: with protection off, any page in the user's
    # browser can POST to the loopback port with a forged Origin and reach
    # every tool, including destructive ones like forget_library. Verified by
    # an audit -- a curl with `Origin: http://attacker.example` was served a
    # full feature vector for a file outside every registered library.
    security = _security_settings(host, port)
    manager = StreamableHTTPSessionManager(
        app=app, event_store=None, json_response=json_response, stateless=True,
        security_settings=security)

    async def handle(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_star):
        async with manager.run():
            yield

    star = Starlette(routes=[Mount("/mcp", app=handle)], lifespan=lifespan)
    if host not in _LOOPBACK:
        # not a refusal -- there are legitimate reasons to bind wider -- but it
        # must not happen quietly. There is no auth in front of these tools.
        print(f"acidcat-mcp: WARNING binding {host} exposes every tool, "
              f"including destructive ones, with no authentication. Put it "
              f"behind a proxy that authenticates, or bind 127.0.0.1.",
              file=sys.stderr)
    print(f"acidcat-mcp: streamable HTTP on http://{host}:{port}/mcp",
          file=sys.stderr)
    uvicorn.run(star, host=host, port=port, log_level="warning")


def _jsonable(o):
    """Coerce numpy scalars/arrays (nested) to plain Python types. The analysis
    tools return librosa/numpy values (e.g. numpy.float32), which the MCP response
    serializer (pydantic) cannot serialize -- an unsanitized value crashes the
    whole connection, not just the one call. Only numpy types are touched."""
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if type(o).__module__ == "numpy":
        try:
            return o.item() if getattr(o, "ndim", None) == 0 else o.tolist()
        except Exception:
            return str(o)
    return o


def _warn_legacy_db():
    legacy = acidpaths.legacy_global_db_path()
    if os.path.isfile(legacy):
        print(f"acidcat-mcp: legacy v0.4 DB at {legacy} is ignored. "
              f"Remove with: rm {legacy}*", file=sys.stderr)


def _warm_analysis_stack():
    """Pre-import the analysis C extensions before the stdio event loop starts.

    numpy/librosa's first import deadlocks the asyncio stdio server on Windows
    (the proactor loop stalls while OpenBLAS/numba initialize), even when the
    import is deferred to a worker thread -- so the analysis tools (find_similar,
    analyze_sample, detect_bpm_key) hang the first time they run. Importing here,
    before the loop starts, turns those in-tool imports into cache hits. No-op when
    the analysis extra is not installed; best-effort otherwise."""
    if importlib.util.find_spec("numpy"):
        try:
            import numpy as np
            np.linalg.norm(np.ones((2, 2)))          # spin up OpenBLAS off the loop
        except Exception:
            pass
    if importlib.util.find_spec("librosa"):
        try:
            import numpy as np
            import librosa
            # importing librosa is not enough: its first *analysis* triggers numba
            # JIT compilation, which deadlocks the loop just like the import does.
            # Run a tiny analysis on a synthetic signal to force the JIT off-loop.
            y = np.zeros(2048, dtype=np.float32)
            librosa.feature.mfcc(y=y, sr=22050, n_mfcc=4)
            librosa.beat.beat_track(y=y, sr=22050)
        except Exception:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="acidcat-mcp",
        description="MCP server exposing the acidcat per-library index "
                    "(stdio or streamable HTTP).",
    )
    parser.add_argument("--registry",
                        help="Override registry DB path "
                             "(default: $ACIDCAT_REGISTRY or "
                             "~/.acidcat/registry.db).")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                        help="Transport: stdio (default, for local MCP clients) "
                             "or http (streamable HTTP at /mcp).")
    parser.add_argument("--host", default="127.0.0.1",
                        help="HTTP bind host (default: 127.0.0.1). This server "
                             "has NO authentication -- binding it beyond "
                             "localhost exposes every tool, including "
                             "destructive ones, to anyone who can reach the "
                             "port.")
    parser.add_argument("--port", type=int, default=8765,
                        help="HTTP port (default: 8765).")
    parser.add_argument("--json-response", action="store_true",
                        help="HTTP: reply with one JSON response per call instead "
                             "of an SSE stream.")
    parser.add_argument("--version", action="version",
                        version=f"acidcat-mcp {__version__}")
    args = parser.parse_args(argv)
    handlers._REGISTRY_PATH = args.registry  # None means: use defaults

    _warn_legacy_db()

    if args.transport == "http":
        _run_http(args.host, args.port, args.json_response)
    else:
        _warm_analysis_stack()   # heavy C-ext imports deadlock inside the loop
        asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
