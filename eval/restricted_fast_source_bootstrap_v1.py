"""Source-only loader for an isolated restricted experiment subprocess."""
import hashlib
import importlib.machinery
import os
from pathlib import Path
import sys

_loaded = {}
_root = None
_source_hook = None
_sourceless_hook = None


def install(root, expected=None):
    global _root, _source_hook, _sourceless_hook
    if _root is not None:
        raise RuntimeError('source loader already installed')
    _root = Path(root).resolve()
    for module in tuple(sys.modules.values()):
        path = getattr(module, '__file__', None)
        if path and Path(path).resolve().is_relative_to(_root):
            raise RuntimeError('repository module loaded before source verification')
    old_source = importlib.machinery.SourceFileLoader.get_code
    old_sourceless = importlib.machinery.SourcelessFileLoader.get_code

    def source(loader, name):
        filename = loader.get_filename(name)
        lexical = Path(os.path.abspath(filename))
        path = Path(filename).resolve()
        if not lexical.is_relative_to(_root):
            return old_source(loader, name)
        if not path.is_relative_to(_root):
            raise ImportError('repository source symlink leaves the verified root')
        raw = loader.get_data(filename)
        digest = hashlib.sha256(raw).hexdigest()
        if expected is not None and expected.get(str(lexical)) != digest:
            raise ImportError('repository source differs from the preverified manifest')
        if path in _loaded and _loaded[path] != digest:
            raise RuntimeError('repository source changed between imports')
        _loaded[path] = digest
        return loader.source_to_code(raw, filename)

    def sourceless(loader, name):
        if Path(os.path.abspath(loader.get_filename(name))).is_relative_to(_root):
            raise ImportError('repository sourceless bytecode is not supported')
        return old_sourceless(loader, name)

    _source_hook, _sourceless_hook = source, sourceless
    importlib.machinery.SourceFileLoader.get_code = source
    importlib.machinery.SourcelessFileLoader.get_code = sourceless


def verify():
    if (importlib.machinery.SourceFileLoader.get_code is not _source_hook
            or importlib.machinery.SourcelessFileLoader.get_code is not _sourceless_hook):
        raise RuntimeError('source-only loader changed')
    for path, expected in tuple(_loaded.items()):
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError('loaded repository source changed')
