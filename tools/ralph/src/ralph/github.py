"""GitHub CLI helper."""

import json
import subprocess
import sys
import time


class GitHub:
    MAX_RETRIES = 3
    RETRY_BACKOFF = [1, 3, 10]

    @staticmethod
    def _run_gh(cmd, **kwargs):
        """Run a gh CLI command with retries on transient failures.

        Retries up to MAX_RETRIES times with exponential backoff when the
        gh command fails (non-zero exit). This absorbs transient GitHub API
        errors (rate limits, network blips, 5xx responses).
        """
        last_err = None
        for attempt in range(GitHub.MAX_RETRIES):
            try:
                return subprocess.run(cmd, **kwargs)
            except subprocess.CalledProcessError as e:
                last_err = e
                delay = GitHub.RETRY_BACKOFF[min(attempt, len(GitHub.RETRY_BACKOFF) - 1)]
                print(f"ralph: gh command failed (attempt {attempt + 1}/{GitHub.MAX_RETRIES},"
                      f" retrying in {delay}s): {e.stderr.strip() if e.stderr else ''}",
                      file=sys.stderr)
                time.sleep(delay)
        raise last_err

    def issue_view_field(self, number, field, repo):
        """Fetch a single field from an issue using --json."""
        result = self._run_gh(
            ["gh", "issue", "view", str(number),
             "--json", field, "--repo", repo],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        return data.get(field, "")

    def issue_view_title(self, number, repo):
        return self.issue_view_field(number, "title", repo)

    def issue_view_body(self, number, repo):
        return self.issue_view_field(number, "body", repo)

    def issue_view_labels(self, number, repo):
        result = self._run_gh(
            ["gh", "issue", "view", str(number),
             "--json", "labels", "--repo", repo],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        return [label["name"] for label in data.get("labels", [])]

    def issue_edit(self, number, repo, remove_label=None, add_label=None, body=None):
        cmd = ["gh", "issue", "edit", str(number)]
        if remove_label:
            cmd.extend(["--remove-label", remove_label])
        if add_label:
            cmd.extend(["--add-label", add_label])
        if body is not None:
            cmd.extend(["--body", body])
        cmd.extend(["--repo", repo])
        self._run_gh(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    def issue_list(self, repo, labels, author=None):
        cmd = ["gh", "issue", "list"]
        for label in labels:
            cmd.extend(["--label", label])
        if author:
            cmd.extend(["--author", author])
        cmd.extend(["--repo", repo, "--json", "number"])
        result = self._run_gh(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
        )
        data = json.loads(result.stdout)
        return [item["number"] for item in data]
