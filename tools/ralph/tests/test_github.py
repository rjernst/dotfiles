"""Unit tests for ralph.github — GitHub CLI helper."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ralph.github import GitHub


class TestGitHubClassAttributes:
    def test_max_retries(self):
        assert GitHub.MAX_RETRIES == 3

    def test_retry_backoff(self):
        assert GitHub.RETRY_BACKOFF == [1, 3, 10]


class TestGitHubRetry:
    @patch("ralph.github.time.sleep")
    @patch("ralph.github.subprocess.run")
    def test_retries_on_transient_failure(self, mock_run, mock_sleep):
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, "gh", stderr="API error"),
            MagicMock(returncode=0, stdout='[{"number": 1}]'),
        ]
        gh = GitHub()
        numbers = gh.issue_list("owner/repo", ["spec"])
        assert numbers == [1]
        assert mock_run.call_count == 2
        mock_sleep.assert_called_once_with(1)

    @patch("ralph.github.time.sleep")
    @patch("ralph.github.subprocess.run")
    def test_raises_after_max_retries(self, mock_run, mock_sleep):
        mock_run.side_effect = subprocess.CalledProcessError(1, "gh", stderr="down")
        gh = GitHub()
        with pytest.raises(subprocess.CalledProcessError):
            gh.issue_list("owner/repo", ["spec"])
        assert mock_run.call_count == 3
        assert mock_sleep.call_count == 3

    @patch("ralph.github.subprocess.run")
    def test_no_retry_on_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='[{"number": 5}]')
        gh = GitHub()
        numbers = gh.issue_list("owner/repo", ["spec"])
        assert numbers == [5]
        assert mock_run.call_count == 1
