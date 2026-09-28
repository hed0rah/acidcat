"""One answer to "which files should this verb look at".

Eight commands had their own ``os.walk`` and their own idea of what counts:
``detect``, ``features`` and ``survey`` matched ``.wav`` only, ``scan`` had an
11-entry set, ``validate`` a 13-entry tuple, ``info`` a preset list, ``convert``
``.ncw``. Pointed at one directory holding ``a.flac b.mp3 c.aiff d.wav`` they
saw 4, 3, 1 and 0 files respectively -- and none of them said so. A filter
decided what counted and the summary reported what it had looked at rather than
what it was asked to look at.

Two rules make this predictable:

**An explicitly named file is always used.** ``detect a.flac`` already worked
while ``detect DIR`` skipped the same file. If you name it, you asked for it --
the same reason ``grep pattern file.xyz`` does not refuse on the extension.

**A directory walk filters, and reports what it filtered.** Sniffing every file
would be more correct (magic over extension is the rule everywhere else here)
but costs 170 files/s -- about ten minutes of sniffing before a 100k-file tree
produced its first line of output. So extension is the cheap gate, the set is
declared once below, and whatever it drops is counted and handed back so the
caller can say how many.
"""

import os

# Every extension acidcat has a walker, converter or extractor for. One list:
# a second one is how the drift above happened. test_targets.py pins the
# commands to it.
CONTAINER_EXTS = frozenset({
    ".wav", ".wave", ".rf64", ".bwf", ".w64",
    ".aif", ".aiff", ".aifc",
    ".flac", ".mp3", ".m4a", ".m4b", ".mp4", ".mov", ".aac",
    ".ogg", ".oga", ".opus", ".spx",
    ".caf", ".au", ".snd", ".voc", ".8svx", ".iff", ".svx",
    ".sf2", ".sf3", ".dls",
    ".wv", ".ape", ".alac", ".wma",
})

TRACKER_EXTS = frozenset({
    ".mod", ".xm", ".it", ".s3m", ".mtm", ".med",
})

PRESET_EXTS = frozenset({
    ".bwpreset", ".bwclip", ".bwproject",
    ".vital", ".nmsv", ".nabs", ".nksf", ".nkm", ".nki", ".nrkt", ".nbkt",
    ".ncw", ".nfm8", ".fxp", ".fxb", ".adg", ".adv", ".alc", ".als", ".amxd",
    ".asd", ".agr", ".krz", ".akp", ".e4b", ".e5b", ".pgm", ".snd",
})

GAME_EXTS = frozenset({
    ".adx", ".brstm", ".bfstm", ".hps", ".vag", ".gcm", ".iso", ".cue",
    ".bin", ".z64", ".n64", ".v64", ".sfc", ".smc", ".spc", ".brr",
})

MIDI_EXTS = frozenset({".mid", ".midi", ".rmi", ".midi2", ".syx"})

# The default gate for a directory walk: anything acidcat might parse.
KNOWN_EXTS = (CONTAINER_EXTS | TRACKER_EXTS | PRESET_EXTS | GAME_EXTS
              | MIDI_EXTS)


def _ext(path):
    return os.path.splitext(path)[1].lower()


def expand(inputs, *, accept=None, recurse=True, follow_links=False):
    """Turn a mix of files and directories into (files, skipped).

    ``inputs``   paths as given on the command line. ``-`` is passed through
                 untouched so the caller's stdin handling still sees it.
    ``accept``   extension set (or predicate) for the directory walk. Defaults
                 to KNOWN_EXTS. Ignored for explicitly named files.
    ``skipped``  how many files the walk passed over. Report it: a silent
                 filter is indistinguishable from an empty directory, and that
                 is the failure this module exists to end.

    Order is stable: inputs in the order given, directory contents sorted, so
    output is reproducible and diffable across runs.
    """
    if accept is None:
        accept = KNOWN_EXTS
    if callable(accept):
        keep = accept
    else:
        exts = frozenset(x.lower() for x in accept)
        def keep(p):
            return _ext(p) in exts

    files, skipped, seen = [], 0, set()

    def add(p):
        real = os.path.normcase(os.path.abspath(p)) if p != "-" else p
        if real in seen:
            return
        seen.add(real)
        files.append(p)

    for item in inputs:
        if item == "-":
            add(item)
            continue
        if os.path.isdir(item):
            for root, dirs, names in os.walk(item, followlinks=follow_links):
                dirs.sort()
                if not recurse:
                    dirs[:] = []
                for name in sorted(names):
                    p = os.path.join(root, name)
                    if keep(p):
                        add(p)
                    else:
                        skipped += 1
        else:
            # named explicitly: the user asked for this file, so no filtering
            add(item)
    return files, skipped


def each(args, attr, single, *, verb, accept=None, header=True, stream=None,
         quiet=False):
    """Run a single-file command once per operand.

    ``audit`` and ``inspect`` are the same kind of verb -- read a file, print
    about it -- and took opposite arities: one file versus many, neither
    accepting a directory. There was no principle behind the split, and a verb
    whose output is per-file and self-labelling should take as many as you hand
    it. That is what makes ``audit *.wav`` work, since the shell expands the
    glob before acidcat is reached.

    ``single(args)`` is the existing one-file body, called with ``args.<attr>``
    set to each path in turn. The worst exit code wins, so a failure anywhere
    still fails the command.

    The per-file header appears only when there IS more than one file -- the
    grep and file(1) rule -- so single-file output stays byte-identical and
    pipes exactly as before.
    """
    import sys

    out = stream or sys.stderr
    raw = getattr(args, attr)
    inputs = raw if isinstance(raw, (list, tuple)) else [raw]
    files, skipped = expand(inputs, accept=accept)

    if not files:
        note = skip_note(skipped)
        print(f"acidcat {verb}: no files to read"
              + (f" -- {note}" if note else ""), file=sys.stderr)
        return 2

    from acidcat.util.stdin import resolved_input

    worst = 0
    many = len(files) > 1
    for i, path in enumerate(files):
        # `-` is resolved here, once, so every verb routed through this helper
        # gets stdin without implementing it. A real path passes through
        # untouched, so a verb that also resolves internally is unaffected.
        with resolved_input(path) as real:
            if real is None:
                print(f"acidcat {verb}: no data on stdin", file=sys.stderr)
                return 1
            setattr(args, attr, real)
            # the operand as the caller gave it, for a record's `path`
            # (never a stdin temp copy's name)
            args._given = "<stdin>" if real != path else path
            if many and header:
                if i:
                    print(file=out)
                print(f"==> {path} <==", file=out)
            worst = max(worst, single(args) or 0)

    note = skip_note(skipped)
    if note and not quiet:
        print(f"  {note}", file=sys.stderr)
    return worst


def skip_note(skipped, *, kind="unrecognised extension"):
    """One line naming what the walk passed over, or None when it passed over
    nothing. Say it -- silence here reads as "there was nothing there"."""
    if not skipped:
        return None
    return (f"skipped {skipped:,} file(s) with an {kind}; "
            f"name a file directly to force it")
