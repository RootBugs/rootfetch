"""Python 3.14 compatibility patches."""
from __future__ import annotations

import collections
import collections.abc
import sys

if sys.version_info >= (3, 14):
    for _name in ("MutableSet", "MutableMapping", "MutableSequence"):
        if not hasattr(collections, _name):
            setattr(collections, _name, getattr(collections.abc, _name))
