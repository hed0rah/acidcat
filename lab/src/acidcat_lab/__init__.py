"""acidcat-lab: the adversarial half.

acidcat reads files that exist. The lab makes files that did not, so that
acidcat's reading can be tested against something that is trying to hide.

    acidcat        does this file contain what it says it does?
    acidcat-lab    make me a file where the answer is interesting.

THE DEPENDENCY GOES ONE WAY. acidcat_lab imports acidcat; acidcat never imports
acidcat_lab, and a test enforces it. That is not tidiness -- it is what lets
`pip install acidcat` put no construction tooling on a forensics analyst's
machine, while `pip install acidcat[lab]` is five extra characters for anyone
who wants it.

If the base package ever NEEDS something from here, that is the signal the thing
was analysis all along and should move across. Nothing moves back. `probe` and
`viz` came over that way already.
"""

from acidcat import __version__ as acidcat_version
from acidcat_lab import cavity, mutations, polyglot, stego

__all__ = ["acidcat_version", "__version__", "cavity", "mutations", "polyglot", "stego"]
__version__ = acidcat_version
