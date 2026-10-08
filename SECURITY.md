# Security Policy

## Reporting a vulnerability

Please report privately: open the repository's **Security** tab on GitHub and
choose **Report a vulnerability** to file a private advisory. Don't open a public
issue for a security problem.

## Supported versions

2.x is the supported line; fixes land in the latest 2.x release. 1.8.7 stays
installable with `pip install "acidcat<2"` but gets no further releases.

## Threat model

acidcat parses untrusted audio, preset, and metadata files. It is pure Python,
so the buffer-overflow and remote-code-execution classes that hit C parsers do
not apply here (no manual memory, no unsafe deserialization sink). The realistic
risks are:

- **denial of service**: a file-controlled length, offset, or count that drives
  an unbounded read, allocation, or loop.
- **incorrect output**: a value derived from a field the file lied about.

The parsers defend accordingly: reads are capped, file-controlled counts are
clamped to the payload actually present before any loop, and values are derived
from the bytes present rather than the declared size. acidcat runs no `eval` or
`exec`, does no `pickle`/`marshal` deserialization or template rendering on
parsed content, and never passes file content to a shell.

Reports of inputs that hang, exhaust memory, crash a walker, or produce
confidently wrong output are in scope and welcome.
