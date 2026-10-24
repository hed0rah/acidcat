"""acidcat-mcp tool registration -- the JSON input schemas.

_register_all() builds the TOOLS table (name, description, input schema, handler,
annotations) that transport.dispatch() serves. It runs at import so TOOLS is
populated for any importer. The handlers themselves live in handlers.py.
"""

from acidcat.mcp_server.handlers import (
    analyze_sample, detect_bpm_key, discover_libraries, find_compatible,
    find_similar, forget_library, get_sample, index_stats, list_formats,
    list_keys, list_libraries, list_tags, locate_sample, register_library,
    reindex, reindex_features, search_samples, set_sample_description,
    tag_sample,
)


TOOLS = []


def _nullable_optionals(schema):
    """Let every non-required scalar-typed property also accept null. LLM clients
    routinely fill unused optional args with null rather than omitting them; the
    framework's input validation would otherwise reject a well-meant null before
    the handler (which treats null as absent via .get()) ever runs."""
    required = set(schema.get("required", []))
    for pname, spec in schema.get("properties", {}).items():
        t = spec.get("type")
        if pname not in required and isinstance(t, str) and t != "null":
            spec["type"] = [t, "null"]
    return schema


def _tool(name, description, input_schema, handler, annotations):
    TOOLS.append({
        "name": name,
        "description": description,
        "input_schema": _nullable_optionals(input_schema),
        "handler": handler,
        "annotations": annotations,
    })


def _register_all():
    # fast (read-only)
    _tool(
        "search_samples",
        "Fast. Filter samples and presets across all registered libraries by "
        "bpm/key/duration/tags/text/format, and by preset metadata "
        "device/category/creator/product (Bitwig, Native Instruments, Vital). "
        "Use 'root' to scope to one or more libraries by label or path. Prefer "
        "this over analysis tools for any discovery query.",
        {
            "type": "object",
            "properties": {
                "bpm_min": {"type": "number", "description": "Minimum BPM (inclusive)."},
                "bpm_max": {"type": "number", "description": "Maximum BPM (inclusive)."},
                "key": {"type": "string",
                        "description": "Exact key (e.g. 'Am', 'C#'). 'C#' "
                        "matches C# major AND a C# root of unknown mode; "
                        "each result's key_mode says which, key_source "
                        "where the key came from, and key_claims every "
                        "source that stated one."},
                "duration_min": {"type": "number", "description": "Minimum duration in seconds."},
                "duration_max": {"type": "number", "description": "Maximum duration in seconds."},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "AND semantics across tags."},
                "text": {"type": "string",
                         "description": "FTS across title/artist/album/genre/"
                         "comment/description/tags/preset/device/creator/path."},
                "format": {"type": "string",
                           "description": "wav, mp3, flac, midi, serum, "
                           "bwpreset, nmsv, vital, ..."},
                "device": {"type": "string",
                           "description": "Preset device/instrument "
                           "(e.g. Polysynth, Massive)."},
                "category": {"type": "string",
                             "description": "Preset category "
                             "(e.g. Reverb, Bass, Synth)."},
                "creator": {"type": "string",
                            "description": "Preset creator/author."},
                "product": {"type": "string",
                            "description": "Product (Bitwig, Vital, Massive, "
                            "Absynth, FM8, ...)."},
                "root": {"type": "string",
                         "description": "Library label or path. "
                         "Comma-separated for multiple."},
                "limit": {"type": "integer", "minimum": 0, "default": 50, "description": "Max results to return."},
            },
        },
        search_samples,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "get_sample",
        "Fast. Full metadata for one sample path, including tags, "
        "description, and which library it belongs to.",
        {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute path to the sample file."}},
            "required": ["path"],
        },
        get_sample,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "locate_sample",
        "Fast. Find samples by filename substring across every registered "
        "library. Use this to answer 'where is X?' questions.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Filename substring to match (case-insensitive)."},
                "limit": {"type": "integer", "minimum": 0, "default": 10, "description": "Max results to return."},
            },
            "required": ["name"],
        },
        locate_sample,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "list_libraries",
        "Fast. Every library registered with acidcat: label, root path, "
        "sample/feature counts, in-tree vs central, last indexed at, "
        "and whether the DB file is currently available on disk.",
        {"type": "object", "properties": {}},
        list_libraries,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "list_tags",
        "Fast. Distinct tags with counts, summed across all libraries. "
        "Use 'prefix' to narrow.",
        {
            "type": "object",
            "properties": {"prefix": {"type": "string", "description": "Only tags starting with this prefix."}},
        },
        list_tags,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "list_keys",
        "Fast. Distinct musical keys with counts across all libraries.",
        {"type": "object", "properties": {}},
        list_keys,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "list_formats",
        "Fast. Distinct file formats with counts across all libraries.",
        {"type": "object", "properties": {}},
        list_formats,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "index_stats",
        "Fast. Roll-up counts across every library: total samples, "
        "feature coverage, format breakdown, available vs unavailable "
        "library count, analysis-tool availability, registry path.",
        {"type": "object", "properties": {}},
        index_stats,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "find_compatible",
        "Fast. Harmonically and rhythmically compatible samples via Camelot "
        "+ BPM tolerance. Fans out across libraries. By default filters to "
        "the target's own kind (loops match loops, one-shots match "
        "one-shots) so a kalimba loop query does not return kalimba "
        "one-shots. No audio analysis; metadata-only.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the sample file."},
                "bpm_tolerance_pct": {"type": "number", "default": 6},
                "half_double": {"type": "boolean", "default": True,
                                "description": "Also match half- and "
                                "double-time tempos (e.g. 174 mixes over 87)."},
                "include_relative": {"type": "boolean", "default": True},
                "kind": {
                    "type": "string",
                    "enum": ["loop", "one_shot", "any"],
                    "description":
                        "Filter by sample kind. Default: auto-infer from "
                        "target.",
                },
                "min_duration": {
                    "type": "number",
                    "description":
                        "Optional seconds floor. Overrides/augments kind "
                        "filter for length-specific queries.",
                },
                "limit": {"type": "integer", "minimum": 0, "default": 20, "description": "Max results to return."},
            },
            "required": ["path"],
        },
        find_compatible,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )

    # analysis (slow)
    _tool(
        "find_similar",
        "SLOW if features are not indexed, fast if they are. Requires "
        "acidcat[analysis]. Nearest neighbors by librosa feature cosine, "
        "fanned out across all libraries. By default filters to the target's "
        "own kind (loops match loops, one-shots match one-shots) so a "
        "0.4s 808 query does not surface a 7s drum build-up that happens to "
        "share spectral tilt. Each result also reports percentile_rank and "
        "similarity_above_mean to help distinguish ranks inside the tight "
        "0.99x clusters that same-pack samples produce. Only use when "
        "metadata-based tools (search_samples, find_compatible) cannot answer.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the sample file."},
                "n": {"type": "integer", "minimum": 0, "default": 5, "description": "How many similar samples to return."},
                "kind": {
                    "type": "string",
                    "enum": ["loop", "one_shot", "any"],
                    "description":
                        "Force a specific kind filter. Default: auto-infer "
                        "from target via duration + acid_beats.",
                },
                "kind_filter": {
                    "type": "boolean",
                    "default": True,
                    "description":
                        "If true (default), filter results to the target's "
                        "inferred kind. Set false to disable filtering "
                        "without forcing a specific kind.",
                },
            },
            "required": ["path"],
        },
        find_similar,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "analyze_sample",
        "SLOW (~1-10s after warm-up; first call ~30-60s due to librosa "
        "import). Requires acidcat[analysis]. On-the-fly librosa feature "
        "extraction for an unindexed file. Prefer get_sample for indexed "
        "files.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the sample file."},
            },
            "required": ["path"],
        },
        analyze_sample,
        {"readOnlyHint": True, "destructiveHint": False,
         "idempotentHint": False, "openWorldHint": False},
    )
    _tool(
        "detect_bpm_key",
        "SLOW (~0.5-2s). Requires acidcat[analysis]. BPM + key estimation "
        "only. Cheaper than analyze_sample. Prefer get_sample when the "
        "file is already indexed.",
        {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute path to the sample file."}},
            "required": ["path"],
        },
        detect_bpm_key,
        {"readOnlyHint": True, "destructiveHint": False,
         "idempotentHint": False, "openWorldHint": False},
    )

    # index management
    _tool(
        "reindex",
        "SLOW. Walk a registered library and populate or refresh its DB. "
        "Identify the library by label or root path. This is the step that "
        "actually fills a library: one that has been registered but never "
        "reindexed holds no samples and answers no queries. Call it without "
        "asking again when completing a registration the user requested; "
        "ask first before re-walking a library that is already populated.",
        {
            "type": "object",
            "properties": {
                "label": {"type": "string",
                          "description": "Library label (preferred)."},
                "path": {"type": "string",
                         "description": "Library root path (alternative)."},
                "with_features": {"type": "boolean", "default": False},
                "force": {"type": "boolean", "default": False,
                          "description": "Re-extract metadata even for "
                                         "unchanged files (after parser "
                                         "upgrades). Preserves tags, "
                                         "descriptions, and features."},
            },
        },
        reindex,
        {"readOnlyHint": False, "destructiveHint": False,
         "idempotentHint": True, "openWorldHint": False},
    )
    _tool(
        "reindex_features",
        "VERY SLOW. Requires acidcat[analysis]. Extracts librosa features "
        "for any indexed samples that lack them, across libraries (or one "
        "library if 'library' arg provided). Only call when explicitly "
        "asked.",
        {
            "type": "object",
            "properties": {
                "library": {"type": "string",
                            "description": "Optional: scope to one library "
                                           "by label or root path."},
                "limit": {"type": "integer", "minimum": 0, "default": 0,
                          "description": "Max files per library this call. "
                                         "0 means no limit -- this tool is "
                                         "VERY SLOW, so pass a number."},
            },
        },
        reindex_features,
        {"readOnlyHint": False, "destructiveHint": False,
         "idempotentHint": True, "openWorldHint": False},
    )

    # registry mutations
    _tool(
        "register_library",
        "Destructive. Register a new library so it becomes part of "
        "fan-out queries. Creates the DB but does NOT populate it (call "
        "reindex afterwards). Default storage is central "
        "(~/.acidcat/libraries/<label>_<hash>.db); pass in_tree=true to "
        "store the DB inside the library's own directory.",
        {
            "type": "object",
            "properties": {
                "root": {"type": "string",
                         "description": "Absolute path to the library root."},
                "label": {"type": "string",
                          "description":
                              "Friendly label (default: basename of root)."},
                "in_tree": {"type": "boolean", "default": False},
            },
            "required": ["root"],
        },
        register_library,
        {"readOnlyHint": False, "destructiveHint": True,
         "idempotentHint": True, "openWorldHint": False},
    )
    _tool(
        "forget_library",
        "Destructive. Remove a library from the registry. Does "
        "NOT delete its DB file; rerunning register_library on the same "
        "root re-attaches it. Confirm with the user before calling.",
        {
            "type": "object",
            "properties": {
                "label": {"type": "string",
                          "description": "Library label to forget."},
                "root": {"type": "string",
                         "description": "Or the library root path to forget."},
            },
        },
        forget_library,
        {"readOnlyHint": False, "destructiveHint": True,
         "idempotentHint": False, "openWorldHint": False},
    )
    _tool(
        "discover_libraries",
        "SLOW. Walk a directory tree and register every qualifying "
        "subfolder as its own library. A folder qualifies if its subtree "
        "(within max_depth) holds at least min_samples audio files. "
        "Recurses into folders that don't qualify on their own to find "
        "qualifying grandchildren. Always call once with dry_run=true "
        "first to preview the candidates, then again with dry_run=false "
        "after the user confirms. Like register_library, this creates the "
        "libraries but does NOT populate them: call reindex on each one "
        "afterwards, or they stay empty and answer no queries. Reported "
        "audio_count is bounded by max_depth; a candidate carrying "
        "audio_count_is_a_floor has more files below that depth.",
        {
            "type": "object",
            "properties": {
                "root": {"type": "string",
                         "description":
                             "Container directory to walk. acidcat refuses "
                             "to discover at the user's home dir."},
                "min_samples": {"type": "integer", "default": 20,
                                "description":
                                    "Minimum audio files in a subtree for "
                                    "it to qualify as a library."},
                "max_depth": {"type": "integer", "default": 3,
                              "description":
                                  "How many levels into the tree to walk."},
                "label_prefix": {"type": "string", "default": "",
                                 "description":
                                     "Prefix every auto-derived label "
                                     "with this string. Useful for "
                                     "namespacing scattered collections."},
                "dry_run": {"type": "boolean", "default": True,
                            "description":
                                "Return the candidate list without "
                                "writing to the registry. Defaults to "
                                "true; pass false explicitly to commit."},
                "with_features": {"type": "boolean", "default": False,
                                  "description":
                                      "Also walk + extract librosa features "
                                      "for each registered library. VERY "
                                      "SLOW; defer unless explicitly asked."},
            },
            "required": ["root"],
        },
        discover_libraries,
        {"readOnlyHint": False, "destructiveHint": True,
         "idempotentHint": True, "openWorldHint": False},
    )

    # write tools (sample-level)
    _tool(
        "tag_sample",
        "Destructive. Add or remove tags on a sample. Confirm with "
        "the user before calling.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the sample file."},
                "add_tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to add."},
                "remove_tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to remove."},
            },
            "required": ["path"],
        },
        tag_sample,
        {"readOnlyHint": False, "destructiveHint": True,
         "idempotentHint": False, "openWorldHint": False},
    )
    _tool(
        "set_sample_description",
        "Destructive. Set or clear the free-text description on a "
        "sample. Confirm with the user before calling.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the sample file."},
                "description": {"type": "string"},
            },
            "required": ["path"],
        },
        set_sample_description,
        {"readOnlyHint": False, "destructiveHint": True,
         "idempotentHint": True, "openWorldHint": False},
    )


_register_all()
