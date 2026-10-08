"""
acidcat survey -- the 1.8 spelling of `acidcat stats --by chunks`.

The verb is an alias in 2.0 (cli_aliases.py). Its parser stays so an old
command line reads exactly as 1.8 read it: `-n` becomes `--max-files`, and
`--has`, `--examples`, `-o` and `-q` pass through to the census engine.
"""

from acidcat.commands._output import add_output_format_arg


def register(subparsers):
    p = subparsers.add_parser("survey", help="Count RIFF chunk types across a directory.")
    p.add_argument("target", help="Directory to scan.")
    p.add_argument("-n", "--num", type=int, default=1000000, help="Max files to scan.")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--has", help="Only count files containing these chunk IDs (comma-separated).")
    p.add_argument("--examples", type=int, default=1,
                   help="Example file paths to store per chunk ID.")
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"))
    p.add_argument("-o", "--output", help="Write output to file.")
