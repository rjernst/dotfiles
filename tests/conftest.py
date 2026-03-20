"""Shared test helpers for importing scripts without .py extension."""

import importlib.machinery
import importlib.util
import os
import sys


def import_script(name, path=None):
    """Import a script file by path, even without a .py extension.

    Args:
        name: Module name to register in sys.modules.
        path: Absolute path to the script. Defaults to scripts/<name> in the
              repository root.

    Returns:
        The imported module.
    """
    if path is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo_root, "scripts", name)

    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
