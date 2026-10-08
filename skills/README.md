# acidcat skills

A Claude skill for acidcat. To use it with Claude Code or claude.ai, copy it into
your skills directory:

    cp -r skills/acidcat ~/.claude/skills/

It teaches Claude when and how to use acidcat 2.0: the seventeen verbs, the
ADDR grammar every verb shares, the contract v1 Document `inspect --json`
writes, verified edits and structural checks, recovery from blobs, the HTML
byte explorer, clip-to-MIDI convert, driving the TUI, and the MCP server
(stdio + streamable HTTP).

Install it alongside the MCP server rather than instead of it. The server's tool
descriptions cover each call on its own; the skill covers the parts that only
show up in sequence -- which tool to try first, what the cost prefixes mean, that
registering a library does not populate it, and which returned numbers are lower
bounds rather than totals.

`tests/test_skill_is_accurate.py` holds the skill to the code: every extra,
console script, CLI verb and MCP tool name it mentions has to exist.
