"""Git subprocess helper — shared across dotfiles tools."""

import subprocess


class Git:
    def __init__(self, cwd=None):
        self.cwd = cwd

    def run(self, *args, check=True, cwd=None):
        """Run git command. cwd overrides instance default."""
        effective_cwd = cwd if cwd is not None else self.cwd
        return subprocess.run(
            ["git"] + list(args),
            cwd=effective_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=check,
        )

    def output(self, *args, cwd=None):
        """Run and return stripped stdout. Returns '' on error."""
        try:
            result = self.run(*args, check=True, cwd=cwd)
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return ""
