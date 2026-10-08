"""The 1.8 verbs as implementations behind the 2.0 ones.

`check` runs what `validate` and `repair` ran, `stats` what `scan` and `shape`
ran, and so on: the old command modules are the implementations and keep their
own parsers, which are no longer registered as verbs. A 2.0 verb builds the argv
its implementation understands and parses it with that module's own parser, so
every default and conversion the implementation relies on is the one it was
written against.
"""

import argparse


def parser_for(module, name):
    """The 1.8 parser `module.register` builds for verb `name`."""
    top = argparse.ArgumentParser(prog="acidcat")
    sub = top.add_subparsers(dest="command")
    module.register(sub)
    p = sub.choices[name]
    p.prog = "acidcat " + name
    return p


def run(module, name, argv):
    """Parse `argv` with `module`'s own parser and run it. A usage error in
    the built argv is acidcat's mistake, not the caller's, but argparse exits 2
    for it either way, which is the right code."""
    args = parser_for(module, name).parse_args([str(a) for a in argv])
    return module.run(args)


def flag(argv, name, value):
    """Append `name value` when value is not None."""
    if value is not None:
        argv += [name, str(value)]
    return argv


def switch(argv, name, on):
    if on:
        argv.append(name)
    return argv
