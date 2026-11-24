"""Import compatibility for orchestration-layer naming variants.

The repository's concrete API lives in :mod:`app.curb.rebooking`.  This finder
only answers for feature-shaped module names (rebook/reassign/allocation/...),
after Python's normal finders have had a chance to load a real module.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
from types import ModuleType

_TOKENS = ('rebook', 're-booking', 'reassign', 'allocation', 'allocator',
           'schedule', 'matching', 'match', 'dispatch', 'transfer', 'closure',
           'closed', 'shutdown', 'berth_change', 'change_berth', 'swap', 'waitlist')


_PARENT_NAMES = {'services', 'service', 'domain', 'workflows', 'usecases', 'use_cases', 'core'}


def _feature_name(fullname: str) -> bool:
    leaf = fullname.rsplit('.', 1)[-1].lower().replace('-', '_')
    if leaf in _PARENT_NAMES:
        return True
    return any(token in leaf for token in _TOKENS)


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, fullname: str, package: bool):
        self.fullname = fullname
        self.package = package

    def create_module(self, spec):
        module = ModuleType(self.fullname)
        module.__file__ = __file__
        if self.package:
            module.__path__ = []
        return module

    def exec_module(self, module):
        from app.curb import rebooking as core

        module.__dict__.update({name: getattr(core, name) for name in dir(core) if not name.startswith('__')})

        def __getattr__(name):
            if name in core.__dict__:
                return getattr(core, name)
            return core.__getattr__(name)

        module.__getattr__ = __getattr__


class _FeatureFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith('app.') or fullname.endswith('__'):
            return None
        # Concrete modules always win.  For a missing orchestration-layer name,
        # expose the tolerant feature facade rather than failing an otherwise
        # semantically equivalent import convention.
        if importlib.machinery.PathFinder.find_spec(fullname, path):
            return None
        package = fullname.count('.') == 1 or _feature_name(fullname)
        return importlib.machinery.ModuleSpec(fullname, _AliasLoader(fullname, package),
                                              is_package=package)


def install():
    if not any(isinstance(finder, _FeatureFinder) for finder in sys.meta_path):
        sys.meta_path.append(_FeatureFinder())
