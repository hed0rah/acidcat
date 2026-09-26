"""acidcat TUI -- interactive terminal explorer for audio and preset files.

Walk a file's structure in a tree, view any node's bytes in a hex pane, scan for
hidden/embedded audio, carve regions, extract samples, edit metadata, and repair
containers -- all over the never-raise walk engine, so the core stays
zero-dependency (Textual loads only when the TUI runs).

Split into render (byte/field helpers + edit profiles), screens (the modal
widgets + hex pane), and app (the AcidcatTUI application). This module re-exports
the stable surface.
"""

# Loaded on first use (PEP 562), not here: importing the package must not import
# Textual, so the model (tui_app/model.py) and the render helpers stay usable
# and testable without it.
_HOME = {
    "AcidcatTUI": "app",
    "edit_profile": "render", "hex_text": "render", "text_field_for": "render",
    "BrowseScreen": "screens", "ConfirmScreen": "screens", "DiffScreen": "screens",
    "DiscScreen": "screens", "EditScreen": "screens", "HelpScreen": "screens",
    "HexPane": "screens", "MapScreen": "screens", "PromptScreen": "screens",
    "RegionsScreen": "screens", "ValidateScreen": "screens",
}


def __getattr__(name):
    if name in _HOME:
        import importlib
        return getattr(importlib.import_module("acidcat.tui_app." + _HOME[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AcidcatTUI", "edit_profile", "hex_text", "text_field_for",
    "BrowseScreen", "ConfirmScreen", "DiffScreen", "DiscScreen", "EditScreen",
    "HelpScreen", "HexPane", "MapScreen", "PromptScreen", "RegionsScreen",
    "ValidateScreen",
]
