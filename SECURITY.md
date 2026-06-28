# Security Policy

## Supported versions

The latest published `0.9.x` release receives security fixes. Older
versions do not.

## Reporting a vulnerability

Please report privately through GitHub: open the repository's **Security**
tab and choose **Report a vulnerability** to file a private advisory. Do not
open a public issue for a security problem.

## Scope and threat model

acidcat parses untrusted audio and metadata files for a living. It is pure
Python, so the classic buffer-overflow and remote-code-execution classes that
hit C parsers do not apply: there is no manual memory and no stack to smash.

The realistic risks are:

- **denial of service**: a file-controlled length, offset, or count that drives
  an unbounded read or allocation, or a pathological loop.
- **incorrect output**: a value derived from a field the file lied about.

The parsers defend accordingly: reads are capped (64 KB), file-controlled counts
are clamped to the actual payload before any loop, and metrics are derived from
the bytes actually present rather than the declared size. Reports of inputs that
hang, exhaust memory, crash a walker, or produce confidently wrong output are in
scope and welcome.
