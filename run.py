"""Entry point for Rootfetch. Applies Python 3.14 compat patches and path fix first."""
from __future__ import annotations

import collections
import collections.abc
import sys
import os

if sys.version_info >= (3, 14):
    for _name in ("MutableSet", "MutableMapping", "MutableSequence"):
        if not hasattr(collections, _name):
            setattr(collections, _name, getattr(collections.abc, _name))

# Fix missing site-packages paths:
#   1. System site-packages (broken relative-path resolution in this Python install)
#   2. Current user's site-packages (needed when running as SYSTEM service)
_this_dir = os.path.dirname(os.path.abspath(__file__))
_exe_dir = os.path.dirname(sys.executable)
_paths_to_add = [_this_dir, os.path.join(_exe_dir, "Lib", "site-packages")]
# User site-packages (resolves to C:\Users\<user>\AppData\...)
_user_base = os.path.dirname(os.path.dirname(os.__file__))  # site-packages dir
for _scheme in ("site-packages", "site-python"):
    _user_sp = os.path.join(_user_base, "site-packages")
    if os.path.isdir(_user_sp):
        _paths_to_add.append(_user_sp)
for _p in _paths_to_add:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import uvicorn
from app.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
    )
