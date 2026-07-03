#!/usr/bin/env python3
"""build_explorer.py -- turn an `acidcat inspect --full` dump into a
standalone, self-contained HTML byte explorer.

    acidcat inspect --full song.mp3 | python build_explorer.py -o song.html
    python build_explorer.py song.json -o song.html

The dump carries each chunk's raw region bytes and every field's absolute
byte offset, so this script needs nothing but that JSON. It is a pure
JSON to HTML transform: no access to the original file, no dependencies.

The output is a datasheet-style page. Each structural region is drawn as a
hex byte grid with its decoded fields tinted over the bytes; hovering a
byte lights up its field and hovering a field lights up its bytes.
"""

import argparse
import html
import json
import sys

# a small, muted, high-contrast tint palette cycled per field within a region.
_TINTS = [
    "#cfe3ef", "#e6d8c3", "#d6e8cf", "#efd6d6", "#dcd3e8",
    "#cfe8e4", "#e8e2cf", "#e0d0dd", "#d0dbe8", "#e8d8c8",
]


def _load(source):
    """Read one or more file records from a --full dump (a single JSON
    object or NDJSON, one object per line)."""
    text = source.read()
    records = []
    stripped = text.strip()
    if not stripped:
        return records
    # try a single object first, then fall back to line-delimited.
    try:
        records.append(json.loads(stripped))
    except json.JSONDecodeError:
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"build_explorer: skipping malformed line: {e}",
                      file=sys.stderr)
    return records


def _esc(v):
    return html.escape(str(v), quote=True)


def _byte_grid(chunk):
    """Render a chunk's raw bytes as a hex grid, tinting each field's span
    and tagging every byte cell with the field index it belongs to."""
    raw = bytes.fromhex(chunk["raw"])
    base = chunk["raw_base"]
    # map an absolute byte offset to the index of the field that covers it.
    owner = {}
    raw_end = base + len(raw)
    positioned = [f for f in chunk["fields"] if f.get("abs") is not None]
    for i, f in enumerate(positioned):
        start = f["abs"]
        # clamp to the bytes actually present: a bulk field (a data chunk's
        # audio payload) declares a length far larger than the capped raw.
        end = min(start + max(1, f["len"]), raw_end)
        for off in range(start, end):
            owner.setdefault(off, i)

    cells = []
    for j, byte in enumerate(raw):
        abs_off = base + j
        fi = owner.get(abs_off)
        if j % 16 == 0:
            cells.append(f'<span class="addr">{abs_off:08x}</span>')
        attr = f' data-fi="{fi}"' if fi is not None else ""
        cls = "byte owned" if fi is not None else "byte"
        cells.append(f'<span class="{cls}"{attr}>{byte:02x}</span>')
        if j % 16 == 15:
            cells.append("<br>")
    trunc = ""
    if chunk.get("raw_truncated"):
        trunc = (f'<div class="trunc">+{chunk["raw_truncated"]:,} more bytes '
                 f'beyond the {len(raw):,}-byte cap</div>')
    return f'<div class="grid">{"".join(cells)}</div>{trunc}'


def _field_rows(chunk):
    rows = []
    positioned = [f for f in chunk["fields"] if f.get("abs") is not None]
    idx = {id(f): i for i, f in enumerate(positioned)}
    for f in chunk["fields"]:
        fi = idx.get(id(f))
        off = f'{f["abs"]:08x}' if f.get("abs") is not None else ""
        note = f' <span class="note">{_esc(f["note"])}</span>' if f.get("note") else ""
        attr = f' data-fi="{fi}"' if fi is not None else ""
        cls = "frow owned" if fi is not None else "frow"
        rows.append(
            f'<tr class="{cls}"{attr}>'
            f'<td class="foff">{off}</td>'
            f'<td class="fname">{_esc(f["name"])}</td>'
            f'<td class="fval">{_esc(f["value"])}{note}</td></tr>'
        )
    return "".join(rows)


def _dark_tint(c):
    """Blend a light field-tint toward the dark ground so dark mode gets a
    subtle dark-tinted byte background (light ink stays readable over it)."""
    h = c.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    dr, dg, db = 0x19, 0x1a, 0x17
    return "#%02x%02x%02x" % (round(r + (dr - r) * 0.8),
                              round(g + (dg - g) * 0.8),
                              round(b + (db - b) * 0.8))


def _tint_css(max_fields):
    out = []
    for i in range(max_fields):
        c = _TINTS[i % len(_TINTS)]
        out.append(f'.region [data-fi="{i}"].owned{{background:{c}}}')
        out.append(f':root[data-theme="dark"] .region [data-fi="{i}"].owned'
                   f'{{background:{_dark_tint(c)}}}')
        out.append(f'.region.hot-{i} [data-fi="{i}"]{{outline:2px solid var(--ink);'
                   f'outline-offset:-2px}}')
    return "".join(out)


def _region(chunk, ri):
    rid = _esc(chunk["id"].strip() or "region")
    off = chunk.get("offset", 0)
    size = chunk.get("size", 0)
    summary = _esc(chunk.get("summary", ""))
    warns = ""
    if chunk.get("warnings"):
        items = "".join(f"<li>{_esc(w)}</li>" for w in chunk["warnings"])
        warns = f'<ul class="warns">{items}</ul>'
    grid = _byte_grid(chunk) if chunk.get("raw") else ""
    body = f'<div class="cols"><div class="gridwrap">{grid}</div>' if grid else "<div>"
    body += (f'<table class="fields"><tbody>{_field_rows(chunk)}</tbody></table>'
             f'</div>')
    return (
        f'<section class="region" data-ri="{ri}">'
        f'<h2><span class="rid">{rid}</span>'
        f'<span class="rmeta">0x{off:08x} . {size:,} bytes</span></h2>'
        f'<p class="summary">{summary}</p>{warns}{body}</section>'
    )


def build(record):
    fname = _esc(record.get("file", "file"))
    fmt = _esc(record.get("format", ""))
    size = record.get("size", 0)
    chunks = record.get("chunks", [])
    max_fields = max(
        (sum(1 for f in c["fields"] if f.get("abs") is not None) for c in chunks),
        default=0,
    )
    regions = "".join(_region(c, i) for i, c in enumerate(chunks))
    warnings = ""
    if record.get("warnings"):
        items = "".join(f"<li>{_esc(w)}</li>" for w in record["warnings"])
        warnings = f'<section class="filewarns"><h2>warnings</h2><ul>{items}</ul></section>'
    return _PAGE.format(
        title=fname, fmt=fmt, size=f"{size:,}", tints=_tint_css(max_fields),
        regions=regions, warnings=warnings,
    )


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>acidcat explorer / {title}</title>
<script>(function(){{try{{var m=localStorage.getItem("acidcat-theme");if(!m)m=matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light";document.documentElement.setAttribute("data-theme",m);}}catch(e){{}}}})();</script>
<style>
:root{{--paper:#f4f1ea;--ink:#1a2b3c;--soft:#4a5a68;--line:#c9c2b4;--hot:#8a3324;--panel:#fbfaf6;--hair:#ece7db;--hot-soft:#fff2c8}}
:root[data-theme="dark"]{{--paper:#191a17;--ink:#e6e6e1;--soft:#abaca1;--line:#383a32;--hot:#d98c78;--panel:#212320;--hair:#2b2d26;--hot-soft:#3a2a24}}
.theme-toggle{{position:fixed;bottom:0.9rem;right:0.9rem;z-index:50;font:0.6rem/1 ui-monospace,monospace;letter-spacing:0.2em;text-transform:uppercase;color:var(--soft);background:var(--panel);border:1px solid var(--line);padding:0.45rem 0.7rem;cursor:pointer}}
.theme-toggle:hover{{color:var(--hot);border-color:var(--hot)}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);
  font:13px/1.6 ui-monospace,"SF Mono",Menlo,Consolas,monospace;padding:2rem}}
header{{border-bottom:2px solid var(--ink);padding-bottom:0.6rem;margin-bottom:1.5rem}}
header h1{{margin:0;font-size:1.2rem;letter-spacing:0.02em}}
header .spec{{color:var(--soft);font-size:0.85rem;margin-top:0.2rem}}
.region{{border:1px solid var(--line);background:var(--panel);margin-bottom:1.2rem;
  padding:0.8rem 1rem}}
.region h2{{margin:0 0 0.2rem;font-size:0.95rem;display:flex;
  justify-content:space-between;align-items:baseline}}
.region .rid{{color:var(--hot);font-weight:700}}
.region .rmeta{{color:var(--soft);font-size:0.8rem}}
.summary{{margin:0.2rem 0 0.6rem;color:var(--soft)}}
.cols{{display:flex;gap:1.2rem;flex-wrap:wrap;align-items:flex-start}}
.gridwrap{{flex:0 0 auto}}
.grid{{white-space:pre;line-height:1.5}}
.addr{{color:var(--soft);margin-right:0.6rem;user-select:none}}
.byte{{padding:0 2px;border-radius:2px}}
.byte.owned{{cursor:pointer}}
table.fields{{border-collapse:collapse;flex:1 1 260px;min-width:260px}}
.fields td{{padding:1px 8px;vertical-align:top;border-bottom:1px solid var(--hair)}}
.fields .foff{{color:var(--soft);white-space:nowrap}}
.fields .fname{{font-weight:700}}
.frow.owned{{cursor:pointer}}
.frow.hot td{{background:var(--hot-soft)}}
.note{{color:var(--soft)}}
.warns,.filewarns ul{{margin:0.3rem 0;padding-left:1.1rem;color:var(--hot)}}
.trunc{{color:var(--soft);font-size:0.8rem;margin-top:0.3rem}}
footer{{margin-top:2rem;color:var(--soft);font-size:0.8rem;
  border-top:1px solid var(--line);padding-top:0.6rem}}
{tints}
</style>
</head>
<body>
<header>
<h1>{title}</h1>
<div class="spec">{fmt} . {size} bytes</div>
</header>
{regions}
{warnings}
<footer>built by build_explorer.py from an acidcat inspect --full dump.
hover a byte or a field to link the two.</footer>
<button class="theme-toggle" id="themeToggle" aria-label="Toggle light and dark theme"></button>
<script>(function(){{var t=document.getElementById("themeToggle");function cur(){{return document.documentElement.getAttribute("data-theme")||"light";}}function ap(m){{document.documentElement.setAttribute("data-theme",m);try{{localStorage.setItem("acidcat-theme",m);}}catch(e){{}}t.textContent=(m==="dark"?"light":"dark");}}t.addEventListener("click",function(){{ap(cur()==="dark"?"light":"dark");}});ap(cur());}})();</script>
<script>
document.querySelectorAll(".region").forEach(function(region){{
  function set(fi, on){{
    region.classList.toggle("hot-" + fi, on);
    region.querySelectorAll('.frow[data-fi="' + fi + '"]').forEach(function(r){{
      r.classList.toggle("hot", on);
    }});
  }}
  region.querySelectorAll("[data-fi]").forEach(function(el){{
    el.addEventListener("mouseenter", function(){{ set(el.dataset.fi, true); }});
    el.addEventListener("mouseleave", function(){{ set(el.dataset.fi, false); }});
  }});
}});
</script>
</body>
</html>
"""


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Render an acidcat inspect --full dump as a standalone "
                    "HTML byte explorer.")
    ap.add_argument("input", nargs="?", default="-",
                    help="A --full JSON dump, or - for stdin (default).")
    ap.add_argument("-o", "--output", default="-",
                    help="Output HTML path, or - for stdout (default).")
    args = ap.parse_args(argv)

    src = sys.stdin if args.input == "-" else open(args.input, encoding="utf-8")
    try:
        records = _load(src)
    finally:
        if src is not sys.stdin:
            src.close()
    if not records:
        print("build_explorer: no records in input", file=sys.stderr)
        return 1
    if not records[0].get("full"):
        print("build_explorer: input is not a --full dump (run acidcat inspect "
              "--full)", file=sys.stderr)
        return 1

    pages = "\n".join(build(r) for r in records)
    if args.output == "-":
        sys.stdout.write(pages)
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(pages)
    return 0


if __name__ == "__main__":
    sys.exit(main())
