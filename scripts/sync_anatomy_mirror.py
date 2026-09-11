"""Publish the anatomy pages from canon to the hed0rah.github.io mirror.

`docs/formats/` is canon; the site is a mirror of it. Copying by hand has gone
wrong the same two ways every time, and both failures are quiet:

  line endings   the mirror is CRLF and canon is LF. Copy the bytes straight
                 across and every line of all 36 files reads as changed, which
                 buries the real diff in a rewrite of the whole fleet.
  the index      audio_files_anatomy/index.html links the fleet and carries a
                 card plus an accent colour per format. A page added without a
                 card is a page nobody can navigate to, and the stamp above the
                 cards was written once and left: it read "21 formats" while
                 listing 32.

So this converts, adds the card, and derives the stamp from the cards rather
than trusting it.

    python scripts/sync_anatomy_mirror.py            # report only, changes nothing
    python scripts/sync_anatomy_mirror.py --write    # do it

A new format needs a row in NEW_CARDS below, or the script says which page has
no card and refuses to invent one: the blurb is prose someone should write, and
the accent is a design choice.
"""
import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CANON = os.path.join(os.path.dirname(HERE), "docs", "formats")
MIRROR = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                      "hed0rah.github.io", "hed0rah.github.io",
                      "audio_files_anatomy")

# slug -> (display name, tab colour, name colour, unit label, blurb)
# Only formats whose card does not exist yet. Once a card is in index.html the
# entry here is redundant and can go.
NEW_CARDS = {
    "voc": ("VOC", "#6b5a3d", "#d6bd7f", "byte map",
            "The sample format of the DOS era, inside almost every Build-engine "
            "archive. A chain of typed blocks whose terminator is one byte with "
            "no length field, and whose continuation block carries no format "
            "bytes of its own."),
    "caf": ("CAF", "#3d6b63", "#7fd6c4", "byte map",
            "Apple's answer to the 4 GB problem, and the third answer in a "
            "family that already had two. Big-endian throughout, SIGNED 64-bit "
            "sizes where -1 legally means 'to the end of the file', and no "
            "chunk alignment at all."),
}


def _crlf(data):
    """Canon's LF to the mirror's CRLF, idempotent on already-CRLF input."""
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def pages():
    return sorted(n for n in os.listdir(CANON) if n.endswith("-anatomy.html"))


def sync_pages(write):
    updated, added = [], []
    for name in pages():
        want = _crlf(io.open(os.path.join(CANON, name), "rb").read())
        dst = os.path.join(MIRROR, name)
        if os.path.exists(dst):
            if io.open(dst, "rb").read() == want:
                continue
            updated.append(name)
        else:
            added.append(name)
        if write:
            io.open(dst, "wb").write(want)
    return updated, added


def index_state():
    p = os.path.join(MIRROR, "index.html")
    s = io.open(p, "rb").read().decode("utf-8").replace("\r\n", "\n")
    carded = set(re.findall(r'href="([a-z0-9_]+)-anatomy\.html"', s))
    stamped = re.search(r"<b>(\d+) formats</b>", s)
    return p, s, carded, int(stamped.group(1)) if stamped else None


def add_card(s, slug, card):
    name, tab, ink, unit, blurb = card
    css = [l for l in s.split("\n") if re.match(r"^\.[a-z0-9]+\s+\.tab\{", l)][-1]
    s = s.replace(css, css + "\n.%-4s .tab{background:%s} .%-4s .name{color:%s}"
                  % (slug, tab, slug, ink), 1)
    last = s.rindex('    <a class="row ')
    end = s.index("</a>", last) + len("</a>")
    row = ('\n    <a class="row %s" href="%s-anatomy.html">\n'
           '      <span class="tab"></span>\n'
           '      <div><div class="name">%s</div>\n'
           '        <div class="d">%s</div></div>\n'
           '      <span class="t">%s</span>\n'
           '    </a>' % (slug, slug, name, blurb, unit))
    return s[:end] + row + s[end:]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true",
                    help="apply; without it nothing is written")
    args = ap.parse_args()

    if not os.path.isdir(MIRROR):
        print("mirror not found at %s" % MIRROR)
        return 2

    updated, added = sync_pages(args.write)
    p, s, carded, stamped = index_state()

    slugs = {n[:-len("-anatomy.html")] for n in pages()}
    missing = sorted(slugs - carded)
    for slug in list(missing):
        if slug in NEW_CARDS:
            s = add_card(s, slug, NEW_CARDS[slug])
            carded.add(slug)
            missing.remove(slug)

    n = len(re.findall(r'class="row ', s))
    restamp = stamped != n
    if restamp:
        s = re.sub(r"<b>\d+ formats</b>", "<b>%d formats</b>" % n, s, count=1)

    print("pages   %d in canon" % len(slugs))
    print("        %d updated, %d added%s"
          % (len(updated), len(added),
             (": " + ", ".join(added)) if added else ""))
    if restamp:
        print("stamp   %s -> %d (derived from the cards, not trusted)"
              % (stamped, n))
    if missing:
        print("\nNO CARD for %s." % ", ".join(missing))
        print("Add an entry to NEW_CARDS: the blurb is prose to write and the")
        print("accent is a design choice, so neither is guessed here.")

    if args.write:
        io.open(p, "wb").write(s.replace("\n", "\r\n").encode("utf-8"))
        print("\nwritten. Review and commit in the mirror repo:")
        print("  cd %s && git diff --stat" % os.path.dirname(MIRROR))
    else:
        print("\nreport only. Re-run with --write to apply.")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
