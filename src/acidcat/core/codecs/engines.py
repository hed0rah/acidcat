"""Tune engines: the players a `render` cap names.

A SID tune and an SPC snapshot hold no audio, only a program and the machine
state it runs on; the one way to hear them is to run it. A walker says which
engine by declaring `render: {engine: ...}` (node-v1.md section 8), and a
consumer looks the engine up here rather than knowing any format: registering
an engine is what lets a new tune format play in the TUI with no TUI edit.
"""

# how much of a tune an audition renders: rendering runs the tune's own code,
# so it costs real time (a SID renders at roughly a tenth of its duration)
SID_PREVIEW_SECONDS = 45.0
SPC_PREVIEW_SECONDS = 30.0


class Engine:
    """One engine: whether it can run these bytes, and what it makes of them.
    `module` is the codec that runs it (imported on first use: the engines
    need numpy)."""

    def __init__(self, name, what, busy, module, render):
        self.name = name
        self.what = what        # "tune", "snapshot": how a message names it
        self.busy = busy        # said while it renders
        self._module_name = module
        self._render = render

    def module(self):
        import importlib
        return importlib.import_module(self._module_name)

    def can_render(self, raw):
        """(True, "") or (False, why)."""
        return self.module().can_render(raw)

    def render(self, raw):
        """(pcm bytes, {"rate", "channels", "label"}). Raises the module's
        CannotRender for a tune it cannot drive."""
        return self._render(raw)


def _sid(raw):
    from acidcat.core.codecs import sid_render
    pcm, info = sid_render.render(raw, seconds=SID_PREVIEW_SECONDS)
    label = info["name"] or "SID tune"
    if info["songs"] > 1:
        label += f" (subtune {info['subtune']} of {info['songs']})"
    if info["sid_chips"] > 1:
        label += f", {info['sid_chips']} SID chips"
    return pcm, {"rate": info["sample_rate"], "channels": 1, "label": label}


def _spc(raw):
    from acidcat.core.codecs import spc_render
    pcm, info = spc_render.render(raw, seconds=SPC_PREVIEW_SECONDS, fade_ms=0)
    label = info["title"] or "SPC tune"
    if info["game"]:
        label += f" ({info['game']})"
    return pcm, {"rate": info["sample_rate"], "channels": 2, "label": label}


ENGINES = {
    "sid": Engine("sid", "tune",
                  "running the tune's 6510 player and synthesising the SID ...",
                  "acidcat.core.codecs.sid_render", _sid),
    "spc": Engine("spc", "snapshot", "running the SPC700 driver and the S-DSP ...",
                  "acidcat.core.codecs.spc_render", _spc),
}


def get(name):
    """The engine a `render` cap names, or None if there is no such player."""
    return ENGINES.get(name)
