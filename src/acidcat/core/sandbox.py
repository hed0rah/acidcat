"""Optional sandboxing of the untrusted-file parse. Linux only.

acidcat parses hostile input; the threat model is denial-of-service, not remote
code execution (see docs). This module isolates the parse so a malicious file
can, at worst, take down a short-lived worker.

Profiles, weakest to strongest:

  * ``limits`` -- fork a worker, cap its address space / CPU time / file writes
    with ``setrlimit``, run the walk there, return the result over a pipe. Pure
    stdlib; bounds *resources*, not *access* (the worker still sees the whole
    filesystem and network). This is the DoS layer.
  * ``bwrap`` -- run the worker inside a `bubblewrap <https://github.com/
    containers/bubblewrap>`_ namespace: **no network**, and a filesystem holding
    only the read-only Python runtime and the one input file -- the user's home
    and data are not mounted. The ``limits`` resource caps ride along (they are
    inherited across exec), so ``bwrap`` is a strict superset of ``limits``.
  * ``strict`` (future) -- ``bwrap`` plus a curated seccomp syscall allowlist.
  * ``auto`` -- pick the strongest profile available on this host.

The worker runs :func:`acidcat.core.walk.walk_file` and returns the same
``(label, chunks, warns)`` a direct call gives; the chunks are already
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
_SANDBOX_INPUT = "/sandbox/input"      # where the input is bind-mounted inside bwrap

_PROFILES = ("bwrap", "limits")        # strongest first, for 'auto'


class SandboxUnavailable(Exception):
    """The requested sandbox cannot run here (wrong platform, missing tool)."""


class SandboxError(Exception):
    """The worker hit a limit, crashed, or produced no usable result."""


# ── availability ────────────────────────────────────────────────────────────

_bwrap_ok = None                        # cached userns probe result


def available(profile="limits"):
    """True if ``profile`` can run on this host."""
    if not sys.platform.startswith("linux"):
        return False
    if profile == "limits":
        return hasattr(os, "fork")
    if profile == "bwrap":
        return _bwrap_available()
    if profile == "auto":
        return any(available(p) for p in _PROFILES)
    return False


def resolve_profile(profile):
    """Map ``'auto'`` to the strongest available profile and validate an explicit
    one. Raises :class:`SandboxUnavailable` (fail loud -- never silently drop to a
    weaker profile than asked for) if the request cannot be honoured."""
    if profile == "auto":
        for p in _PROFILES:
            if available(p):
                return p
        raise SandboxUnavailable(_why_unavailable("limits"))
    if profile not in _PROFILES:
        raise SandboxUnavailable(f"unknown sandbox profile {profile!r}")
    if not available(profile):
        raise SandboxUnavailable(_why_unavailable(profile))
    return profile


def _why_unavailable(profile):
    if not sys.platform.startswith("linux"):
        return f"--sandbox requires Linux; not available on {sys.platform}"
    if profile == "limits":
        return "the limits profile needs os.fork (POSIX)"
    if profile == "bwrap":
        import shutil
        if not shutil.which("bwrap"):
            return ("the bwrap profile needs bubblewrap ('bwrap') on PATH -- "
                    "install it (e.g. apt install bubblewrap)")
        return ("bwrap is installed but unprivileged user namespaces appear "
                "disabled on this kernel")
    return f"sandbox profile {profile!r} is not available here"


def _bwrap_available():
    global _bwrap_ok
    if _bwrap_ok is None:
        import shutil
        _bwrap_ok = bool(shutil.which("bwrap")) and _bwrap_probe()
    return _bwrap_ok


def _bwrap_probe():
    """Confirm bwrap can actually create an unprivileged user namespace (some
    hardened or old kernels disable them); the check is the trigger, so run a
    trivial no-op sandbox and look at the exit code."""
    import shutil
    import subprocess
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return False
    try:
        r = subprocess.run([bwrap, "--unshare-user", "--ro-bind", "/", "/", "true"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ── entry point ─────────────────────────────────────────────────────────────

def run_walk(filepath, deep=False, profile="limits",
             mem_mb=DEFAULT_MEM_MB, timeout_s=DEFAULT_TIMEOUT_S):
    """Run ``walk_file(filepath, deep)`` under ``profile`` ('auto' picks the
    strongest available). Returns ``(label, chunks, warns)``. Raises
    :class:`SandboxUnavailable` if the profile cannot run here, or
    :class:`SandboxError` if the worker hit a limit or died."""
    profile = resolve_profile(profile)
    if profile == "limits":
        def _walk():
            from acidcat.core.walk import walk_file
            return walk_file(filepath, deep=deep)
        return run_limited(_walk, mem_mb=mem_mb, timeout_s=timeout_s)
    if profile == "bwrap":
        return _run_bwrap(filepath, deep, mem_mb, timeout_s)
    raise SandboxUnavailable(f"sandbox profile {profile!r} is not implemented")


# ── resource limits (shared by both profiles) ───────────────────────────────

def _apply_limits(mem_mb, timeout_s, no_new_procs=True):
    """Cap resources with setrlimit. Inherited across ``exec``, so the bwrap
    profile gets these too. ``no_new_procs`` is off for bwrap, which must be
    allowed to spawn the sandboxed interpreter."""
    import resource
    if mem_mb:
        nbytes = int(mem_mb) * 1024 * 1024
        _set(resource.RLIMIT_AS, nbytes)          # total address space
    if timeout_s:
        # soft -> SIGXCPU, hard a second later -> SIGKILL
        _set(resource.RLIMIT_CPU, int(timeout_s), int(timeout_s) + 1)
    _set(resource.RLIMIT_FSIZE, _FSIZE_CAP)       # the walk is read-only
    _set(resource.RLIMIT_CORE, 0)                 # no core dumps
    if no_new_procs:
        _set(resource.RLIMIT_NPROC, 0)            # a forked parse spawns nothing


def _set(what, soft, hard=None):
    import resource
    try:
        resource.setrlimit(what, (soft, soft if hard is None else hard))
    except (ValueError, OSError):
        pass                                       # kernel refused the cap; press on


# ── the 'limits' worker (fork + setrlimit) ──────────────────────────────────

def run_limited(target, mem_mb=DEFAULT_MEM_MB, timeout_s=DEFAULT_TIMEOUT_S):
    """Run ``target()`` -- a no-arg callable returning ``(label, chunks, warns)``
    -- in a resource-limited fork, returning its result. The generic core of the
    ``limits`` profile; tests drive it directly with synthetic bombs."""
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
        except BaseException as e:
            payload = json.dumps({"ok": False, "err": f"{type(e).__name__}: {e}"})
        try:
            os.write(w, payload.encode("utf-8", "replace"))
        except OSError:
            pass
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
                raise SandboxError(f"wall-clock timeout ({timeout_s}s) -- worker killed")
            ready, _, _ = select.select([r], [], [], min(remaining, 0.5))
            if r not in ready:
                continue
            block = os.read(r, 65536)
            if not block:
                break
            buf += block
            if len(buf) > _MAX_RESULT:
                _kill(pid)
                raise SandboxError("worker result exceeded the size cap")
    finally:
        os.close(r)

    _, status = os.waitpid(pid, 0)
    if os.WIFSIGNALED(status):
        raise SandboxError(_signal_reason(os.WTERMSIG(status), mem_mb, timeout_s))
    try:
        res = json.loads(bytes(buf).decode("utf-8", "replace"))
    except ValueError:
        raise SandboxError("worker produced no usable result (likely killed mid-write)")
    if not res.get("ok"):
        raise SandboxError(res.get("err", "unknown worker error"))
    return res["label"], res["chunks"], res["warns"]


# ── the 'bwrap' profile (namespace isolation) ───────────────────────────────

def _run_bwrap(filepath, deep, mem_mb, timeout_s):
    import shutil
    import subprocess
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise SandboxUnavailable("bwrap not found on PATH")
    argv = _bwrap_argv(bwrap, filepath, deep)

    def _pre():
        # resource caps are inherited across exec into bwrap + the sandboxed
        # python; NPROC stays open because bwrap must spawn the interpreter
        _apply_limits(mem_mb, timeout_s, no_new_procs=False)
        os.setpgrp()                               # own group, so a timeout kills the tree

    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, preexec_fn=_pre,
                                close_fds=True)
    except OSError as e:
        raise SandboxUnavailable(f"could not launch bwrap: {e}")
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid)
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        raise SandboxError(f"wall-clock timeout ({timeout_s}s) -- sandbox killed")

    if len(out) > _MAX_RESULT:
        raise SandboxError("worker result exceeded the size cap")
    try:
        res = json.loads(out.decode("utf-8", "replace"))
    except ValueError:
        detail = err.decode("utf-8", "replace").strip().splitlines()
        tail = detail[-1][:200] if detail else ""
        raise SandboxError(
            f"sandbox produced no usable result (bwrap exit {proc.returncode})"
            + (f": {tail}" if tail else ""))
    if not res.get("ok"):
        raise SandboxError(res.get("err", "unknown worker error"))
    return res["label"], res["chunks"], res["warns"]


def _bwrap_argv(bwrap, filepath, deep):
    """The curated bwrap command line: full namespace isolation, no network, a
    read-only runtime, the one input bind-mounted, and nothing of the user's
    data."""
    argv = [bwrap,
            "--unshare-all",              # net, ipc, pid, uts, cgroup, user ns
            "--die-with-parent",
            "--new-session",              # block TIOCSTI terminal injection
            "--clearenv",
            "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "HOME", "/tmp",
            "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--ro-bind", os.path.realpath(filepath), _SANDBOX_INPUT]
    for d in _runtime_binds():
        argv += ["--ro-bind", d, d]
    argv += [sys.executable, "-m", "acidcat._sandbox_worker", _SANDBOX_INPUT]
    if deep:
        argv.append("--deep")
    return argv


def _runtime_binds():
    """Read-only host paths the sandboxed Python needs to run and import acidcat:
    the interpreter + stdlib (``sys.prefix``/``base_prefix``), every existing
    ``sys.path`` directory (site-packages, or an editable-install repo), and the
    system lib dirs for the C-extension ``.so`` deps. The user's HOME/data is
    deliberately absent -- only these plus the one input are visible."""
    cand = set()
    for base in ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc/alternatives"):
        if os.path.isdir(base):
            cand.add(base)
    for p in (sys.prefix, sys.base_prefix, os.path.dirname(sys.executable)):
        if p and os.path.isdir(p):
            cand.add(os.path.realpath(p))
    for p in sys.path:
        if p and os.path.isdir(p):
            cand.add(os.path.realpath(p))
    # drop any path nested under another -- bwrap only needs the parent bind
    binds = []
    for d in sorted(cand):
        if not any(d != o and d.startswith(o.rstrip("/") + "/") for o in cand):
            binds.append(d)
    return binds


# ── helpers ─────────────────────────────────────────────────────────────────

def _signal_reason(sig, mem_mb, timeout_s):
    try:
        name = signal.Signals(sig).name
    except ValueError:
        name = str(sig)
    return (f"worker killed by {name} -- likely a resource limit "
            f"(mem {mem_mb} MB / cpu {timeout_s}s)")


def _kill(pid):
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except OSError:
        pass


def _kill_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        _kill(pid)
