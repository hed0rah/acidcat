"""CTX_KEYS covers every ctx key the WAV walker publishes.

Moved out of test_grammar_wav.py when the grammar engine was removed in 2.0
(architecture-2.0.md section 12, decisions K3): the check is about the walker
and the vocabulary, not the engine, and holds for as long as ctx does.

The corpus is `ACIDCAT_CORPUS` when set (every `*.wav` under it), otherwise
the synthetic one make_corpus.ensure() regenerates on demand, which exercises
the smpl/acid/cue/fact chunks a single hermetic file does not. See
tests/README.md.
"""

import glob
import os

import pytest

_CORPUS = os.environ.get("ACIDCAT_CORPUS")


def _corpus_wavs():
    corpus = _CORPUS
    if not corpus:
        from make_corpus import ensure
        corpus = ensure()
    if not os.path.isdir(corpus):
        return [pytest.param(None,
                             marks=pytest.mark.skip(reason="corpus not present"))]
    paths = sorted(glob.glob(os.path.join(corpus, "**", "*.wav"),
                             recursive=True))
    if not paths:
        return [pytest.param(None,
                             marks=pytest.mark.skip(reason="corpus is empty"))]
    limit = os.environ.get("ACIDCAT_CORPUS_LIMIT")
    return paths[:int(limit)] if limit else paths


@pytest.mark.parametrize("path", _corpus_wavs())
def test_ctx_keys_covers_walker(path):
    """CTX_KEYS must stay a superset of every semantic ctx key the walker
    publishes, so the descriptor vocabulary cannot silently fall behind the
    walker (a published-but-unsanctioned key would reject a valid future
    descriptor field at construction). Self-maintaining across the corpus,
    which exercises smpl/acid/cue/fact chunks a hermetic file does not."""
    from acidcat.core.walk.wav import inspect_wav
    from acidcat.core.infra.vocab import CTX_KEYS
    ctx = {}
    inspect_wav(path, ctx=ctx)  # non-WAV degrades to an empty ctx (passes)
    missing = set(ctx) - set(CTX_KEYS)
    assert not missing, f"walker publishes ctx keys not in CTX_KEYS: {missing}"
