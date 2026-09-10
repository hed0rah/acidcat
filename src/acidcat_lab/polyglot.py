"""Build a polyglot: one file that is simultaneously valid as two formats.

WAV+ZIP is the cleanest of them. A RIFF reader reads up to the declared RIFF
size and ignores what follows; a ZIP reader scans BACKWARDS from the end for
the central directory and tolerates arbitrary prepended data -- which is the
self-extracting-archive trick. So `wav_bytes + zip_bytes` opens as audio in a
DAW and as an archive in unzip, from the same bytes.

Distinct from the cavity planters beside it, and the distinction is the
interesting part. A cavity hides INSIDE a structure that accounts for it, so
the file stays fully explained and only reading the content gives it away. A
polyglot appends PAST the declared end, so the container's own arithmetic
already disagrees with the file's length -- geometry catches it without anyone
having to look at what the bytes say.

acidcat reports both halves of that: `polyglot` names the ZIP magics it found
past the container end, and `trailing_data` and `unaccounted_bytes` describe
the shape of the overhang.
"""


import io
import os
import zipfile
import tempfile


from acidcat import Unsupported, walk_file  # noqa: E402


def build_wav_zip(wav_bytes, payload):
    """payload: {archive_name: bytes}. Returns the polyglot bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in payload.items():
            z.writestr(name, data)
    return wav_bytes + buf.getvalue()


def verify(polyglot):
    """Confirm the same bytes parse as BOTH a WAV (acidcat) and a ZIP."""
    ok_wav = ok_zip = False
    wav_detail = zip_detail = ""
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(polyglot)
        try:
            fmt, chunks, _ = walk_file(tmp, deep=False)
            ok_wav = True
            ids = ",".join(c["id"].strip() for c in chunks[:6])
            wav_detail = f"{fmt}: {ids}"
        except Exception as e:
            wav_detail = f"{e.__class__.__name__}: {e}"
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    try:
        zf = zipfile.ZipFile(io.BytesIO(polyglot))
        bad = zf.testzip()
        ok_zip = bad is None
        zip_detail = ("entries: " + ", ".join(zf.namelist())) if ok_zip \
            else f"corrupt entry: {bad}"
    except Exception as e:
        zip_detail = f"{e.__class__.__name__}: {e}"
    return ok_wav, wav_detail, ok_zip, zip_detail


def _report(polyglot, label=""):
    ok_wav, wd, ok_zip, zd = verify(polyglot)
    print(f"polyglot {label}({len(polyglot):,} bytes)")
    print(f"  as WAV  {'OK ' if ok_wav else 'FAIL'}  {wd}")
    print(f"  as ZIP  {'OK ' if ok_zip else 'FAIL'}  {zd}")
    return ok_wav and ok_zip
