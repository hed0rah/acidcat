"""Optional sandboxing of the untrusted-file parse. Linux only.

acidcat parses hostile input; the threat model is denial-of-service, not remote
code execution (see docs). This module isolates the parse so a malicious file
that tries to exhaust memory, spin the CPU, or write to disk takes down only a
short-lived worker, with the parent reporting the limit hit.

Profiles, weakest to strongest (only the first is implemented here):

  * ``limits`` -- fork a worker, cap its address space / CPU time / file writes
    with ``setrlimit``, run the walk there, return the result over a pipe. Pure
    stdlib, no isolation of filesystem or network -- ``setrlimit`` bounds
    *resources*, not *access*. This is the DoS layer, and the foundation the
    stronger profiles build on.
  * ``bwrap`` (future) -- run the worker in a bubblewrap namespace: no network,
    no filesystem beyond the read-only runtime and the one input, dropped
    privileges.
  * ``strict`` (future) -- ``bwrap`` plus a curated seccomp syscall allowlist.

The worker runs :func:`acidcat.core.walk.walk_file` and ships back the same
``(label, chunks, warns)`` a direct call returns; the chunks are already
JSON-serializable (that is what ``inspect --format json`` emits), so the
process boundary is a plain JSON pipe.
"""

import json
import os
import select
import signal
import sys
import time

DEFAULT_MEM_MB = 2048
DEFAULT_TIMEOUT_S = 60
_MAX_RESULT = 128 * 1024 * 1024        # cap the result payload read from the worker
_FSIZE_CAP = 16 * 1024 * 1024          # a read-only walk writes nothing; cap defensively


class SandboxUnavailable(Exception):
    """The requested sandbox cannot run here (wrong platform, missing tool)."""


class SandboxError(Exception):
    """The worker hit a limit, crashed, or produced no usable result."""


def available(profile="limits"):
    """True if ``profile`` can run on this host."""
    if profile == "limits":
        return sys.platform.startswith("linux") and hasattr(os, "fork")
    return False                        # bwrap / strict not implemented yet


def run_walk(filepath, deep=False, profile="limits",
             mem_mb=DEFAULT_MEM_MB, timeout_s=DEFAULT_TIMEOUT_S):
    """Run ``walk_file(filepath, deep)`` under ``profile``.

    Returns ``(label, chunks, warns)`` exactly as a direct call would. Raises
    :class:`SandboxUnavailable` if the profile cannot run here, or
    :class:`SandboxError` if the worker hit a resource limit or died."""
    if profile != "limits":
        raise SandboxUnavailable(f"sandbox profile {profile!r} is not implemented yet")
    if not available("limits"):
        raise SandboxUnavailable(
            "--sandbox requires Linux (fork + setrlimit); not available on "
            f"{sys.platform}")

    def _walk():
        from acidcat.core.walk import walk_file
        return walk_file(filepath, deep=deep)

    return run_limited(_walk, mem_mb=mem_mb, timeout_s=timeout_s)


def _apply_limits(mem_mb, timeout_s):
    """Child-side: cap resources with setrlimit before touching the input."""
    import resource
    if mem_mb:
        nbytes = int(mem_mb) * 1024 * 1024
        _set(resource.RLIMIT_AS, nbytes)          # total address space
    if timeout_s:
        # soft limit -> SIGXCPU, hard limit a second later -> SIGKILL
        _set(resource.RLIMIT_CPU, int(timeout_s), int(timeout_s) + 1)
    _set(resource.RLIMIT_FSIZE, _FSIZE_CAP)       # the walk is read-only
    _set(resource.RLIMIT_NPROC, 0)                # no new processes from a parse
    _set(resource.RLIMIT_CORE, 0)                 # no core dumps


def _set(what, soft, hard=None):
    import resource
    try:
        resource.setrlimit(what, (soft, soft if hard is None else hard))
    except (ValueError, OSError):
        pass                                       # kernel refused the cap; press on


def run_limited(target, mem_mb=DEFAULT_MEM_MB, timeout_s=DEFAULT_TIMEOUT_S):
    """Run ``target()`` -- a no-arg callable returning ``(label, chunks, warns)``
    -- in a resource-limited fork, returning its result. The generic core of the
    ``limits`` profile; :func:`run_walk` is the walk-file caller, and tests drive
    it directly with synthetic bombs. Linux only (caller checks)."""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        # ---------------- worker (child) ----------------
        try:
            os.close(r)
            _apply_limits(mem_mb, timeout_s)
            label, chunks, warns = target()
            payload = json.dumps({"ok": True, "label": label,
                                  "chunks": chunks, "warns": warns})
        except MemoryError:
            payload = json.dumps({"ok": False, "err": "memory limit exceeded"})
        except BaseException as e:                  # includes the walk's own errors
            payload = json.dumps({"ok": False,
                                  "err": f"{type(e).__name__}: {e}"})
        try:
            os.write(w, payload.encode("utf-8", "replace"))
        except OSError:
            pass                                    # broken pipe / write cap
        os._exit(0)

    # ---------------- parent ----------------
    os.close(w)
    buf = bytearray()
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill(pid)
                raise SandboxError(
                    f"wall-clock timeout ({timeout_s}s) -- worker killed")
            ready, _, _ = select.select([r], [], [], min(remaining, 0.5))
            if r not in ready:
                continue
            block = os.read(r, 65536)
            if not block:
                break                               # EOF: worker finished writing
            buf += block
            if len(buf) > _MAX_RESULT:
                _kill(pid)
                raise SandboxError("worker result exceeded the size cap")
    finally:
        os.close(r)

    _, status = os.waitpid(pid, 0)
    if os.WIFSIGNALED(status):
        sig = os.WTERMSIG(status)
        name = signal.Signals(sig).name if sig in iter(signal.Signals) else str(sig)
        raise SandboxError(
            f"worker killed by {name} -- likely a resource limit "
            f"(mem {mem_mb} MB / cpu {timeout_s}s)")
    try:
        res = json.loads(bytes(buf).decode("utf-8", "replace"))
    except ValueError:
        raise SandboxError("worker produced no usable result (likely killed mid-write)")
    if not res.get("ok"):
        raise SandboxError(res.get("err", "unknown worker error"))
    return res["label"], res["chunks"], res["warns"]


def _kill(pid):
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except OSError:
        pass
