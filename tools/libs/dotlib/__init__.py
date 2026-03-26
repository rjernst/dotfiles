import os

from dotlib.git import Git

DOTFILES_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", ".."))

__all__ = ["Git", "DOTFILES_DIR"]
