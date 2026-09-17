"""Pick the tests a change needs, from the files it touched.

    python scripts/tests_for.py              # diff against HEAD (staged + unstaged)
    python scripts/tests_for.py HEAD~3       # what the last three commits touched
    python scripts/tests_for.py --run        # and run them
    python scripts/tests_for.py --run -- -x  # extra pytest arguments after --

The full suite is 4,500 tests and half an hour serial. Most changes touch
one walker, and the tests that can see a walker change are its own file,
the fleet-wide sweeps keyed on its name (fuzz, cap ledger, sniff, seeds,
geometry), and the two doc guards. This prints that selection, says WHY
each part was chosen, and refuses to guess: a change it cannot map (a new
core module, a shared primitive) widens to the whole side of the tree it
lives in, and a change to the dispatch or the geometry contract is the
full suite. The full suite still runs once per release; this is the edit
loop, not the gate.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# fleet-wide test files that select per format with -k
FLEET_BY_NAME = ["tests/test_walker_fuzz.py", "tests/test_differential_fuzz.py",
                 "tests/test_cap_announcements.py", "tests/test_sniff.py",
                 "tests/test_format_dispatch.py", "tests/test_walker_invariants.py"]
# always cheap, always relevant to a source change
GUARDS = ["tests/test_architecture_doc.py", "tests/test_doc_module_refs.py",
          "tests/test_the_suite_actually_runs.py"]
DOC_TESTS = ["tests/test_anatomy_pages.py", "tests/test_architecture_doc.py",
             "tests/test_doc_module_refs.py"]
# a change here can move every walker
WHOLE_WALKER_SIDE = {"src/acidcat/core/walk/__init__.py", "src/acidcat/core/walk/base.py",
                     "src/acidcat/core/infra/sniff.py", "src/acidcat/core/infra/geometry.py",
                     "src/acidcat/core/primitives/notes.py", "tests/seeds.py",
                     "tests/conftest.py"}
# walker modules that serve several formats, and the tests that cover them
SHARED_WALKERS = {
    "chiptune": ["nsf", "sap", "gbs", "hes", "kss", "chiptune"],
    "tracker": ["tracker", "mod", "xm", "it", "s3m", "stm", "extract"],
    "containers": ["containers", "cdxa", "iso9660", "gamecube", "cue"],
    "streams": ["streams", "adx", "hps", "brstm", "vag", "dtk"],
    "apple": ["aiff", "caf", "apple"],
    "pt3": ["pt3", "pt2"],
    "psf": ["psf"],
    "amiga": ["amiga", "svx"],
}


def changed(base):
    out = subprocess.run(["git", "diff", "--name-only", base, "--"], cwd=ROOT,
                         capture_output=True, text=True).stdout.split()
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                               cwd=ROOT, capture_output=True, text=True).stdout.split()
    return sorted(set(out) | set(untracked))


def existing(paths):
    return [p for p in paths if os.path.exists(os.path.join(ROOT, p))]


def select(files):
    """(test paths, -k names, reasons, full_suite)."""
    paths, names, why = set(), set(), []
    full = False
    for f in files:
        f = f.replace("\\", "/")
        if f in WHOLE_WALKER_SIDE:
            why.append("%s: shared by every walker -> the whole suite" % f)
            full = True
            continue
        stem = os.path.splitext(os.path.basename(f))[0]
        if f.startswith("src/acidcat/core/walk/") or f.startswith("src/acidcat/core/formats/"):
            keys = SHARED_WALKERS.get(stem, [stem])
            names.update(keys)
            paths.update(existing(["tests/test_%s.py" % k for k in keys]))
            paths.update(FLEET_BY_NAME + GUARDS)
            why.append("%s: its tests, and the fleet sweeps for %s" % (f, ", ".join(keys)))
        elif f.startswith("src/acidcat/core/codecs/"):
            paths.update(existing(["tests/test_%s.py" % stem, "tests/test_%s_decode.py" % stem]))
            paths.update(["tests/test_cmd_extract.py", "tests/test_extract_bounds.py"] + GUARDS)
            why.append("%s: the codec's tests and extract" % f)
        elif f.startswith("src/acidcat/core/extract/"):
            paths.update(["tests/test_cmd_extract.py", "tests/test_extract_bounds.py",
                          "tests/test_samples.py", "tests/test_pdx_decode.py"] + GUARDS)
            why.append("%s: extract" % f)
        elif f.startswith("src/acidcat/tui_app/") or f == "src/acidcat/tui_theme.py":
            paths.update(["tests"])
            names.add("tui")
            why.append("%s: every tui test" % f)
        elif f.startswith("src/acidcat/mcp_server/"):
            paths.update(["tests/test_mcp_server.py", "tests/test_mcp_wire.py",
                          "tests/test_mcp_conn_cache.py"])
            why.append("%s: the MCP tests" % f)
        elif f.startswith("src/acidcat/commands/") or f == "src/acidcat/cli.py":
            paths.update(existing(["tests/test_%s.py" % stem, "tests/test_cmd_%s.py" % stem])
                         + ["tests/test_commands.py", "tests/test_cli_conventions.py",
                            "tests/test_exit_codes.py", "tests/test_format_vocabulary.py"] + GUARDS)
            why.append("%s: the command's tests and the CLI conventions" % f)
        elif f.startswith("docs/formats/") or f.startswith("scripts/build_") or f == "scripts/sync_anatomy_mirror.py":
            paths.update(DOC_TESTS)
            why.append("%s: the anatomy fleet tests" % f)
        elif f in ("src/acidcat/__init__.py", "pyproject.toml"):
            paths.update(existing(["tests/test_version_coherence.py", "tests/test_public_api.py",
                                   "tests/test_lean_install.py"]) + GUARDS)
            why.append("%s: version and packaging" % f)
        elif f in ("ARCHITECTURE.md", "README.md", "CHANGELOG.md"):
            paths.update(GUARDS)
            why.append("%s: the doc guards" % f)
        elif f.startswith("tests/"):
            if f.startswith("tests/test_"):
                paths.add(f)
                why.append("%s: itself" % f)
            else:
                why.append("%s: a test helper -> the whole suite" % f)
                full = True
        elif f.startswith("src/acidcat/core/"):
            why.append("%s: a core module with no mapping -> the whole suite" % f)
            full = True
        elif f.startswith("src/acidcat/"):
            why.append("%s: no mapping -> the whole suite" % f)
            full = True
        else:
            why.append("%s: not code" % f)
    return sorted(paths), sorted(names), why, full


def main(argv):
    run = "--run" in argv
    extra = []
    if "--" in argv:
        extra = argv[argv.index("--") + 1:]
        argv = argv[:argv.index("--")]
    args = [a for a in argv if a != "--run"]
    base = args[0] if args else "HEAD"
    files = changed(base)
    if not files:
        print("nothing changed against %s" % base)
        return 0
    paths, names, why, full = select(files)
    for w in why:
        print("  " + w)
    if full:
        cmds = [["python", "-m", "pytest", "tests", "-q", "-n", "8"] + extra]
        print("\nfull suite:")
    elif not paths:
        print("\nno tests apply")
        return 0
    else:
        # the format's own files and the guards run whole; the fleet sweeps
        # select by name, and the tui case selects by name across the tree
        fleet = [p for p in paths if p in FLEET_BY_NAME]
        own = [p for p in paths if p not in FLEET_BY_NAME and p != "tests"]
        cmds = []
        if "tests" in paths:
            cmds.append(["python", "-m", "pytest", "tests", "-q", "-n", "8",
                         "-k", " or ".join(names)] + extra)
        if own:
            cmds.append(["python", "-m", "pytest", "-q"] + own + extra)
        if fleet and names:
            cmds.append(["python", "-m", "pytest", "-q"] + fleet
                        + ["-k", " or ".join(names)] + extra)
        print("\nselected:")
    for cmd in cmds:
        print("  " + " ".join(cmd))
    if run:
        for cmd in cmds:
            rc = subprocess.call(cmd, cwd=ROOT)
            if rc:
                return rc
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
