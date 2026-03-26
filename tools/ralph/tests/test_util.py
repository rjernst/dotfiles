"""Unit tests for ralph.util — pure utility functions."""

import pytest

from ralph.util import parse_duration, parse_frontmatter, parse_issue_branch


# ---------------------------------------------------------------------------
# parse_duration
# ---------------------------------------------------------------------------

class TestParseDuration:
    def test_plain_number_treated_as_seconds(self):
        assert parse_duration("30") == 30

    def test_seconds_suffix(self):
        assert parse_duration("30s") == 30

    def test_minutes(self):
        assert parse_duration("5m") == 300

    def test_hours(self):
        assert parse_duration("2h") == 7200

    def test_days(self):
        assert parse_duration("1d") == 86400

    def test_invalid_input_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_duration("abc")

    def test_invalid_suffix_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_duration("5x")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_duration("")


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_bracket_list_with_multiple_values(self):
        body = "---\ndepends: [11, 17]\nbranch: my-branch\n---\nSome content"
        assert parse_frontmatter(body, "depends") == "11 17"

    def test_bracket_list_with_single_value(self):
        body = "---\ndepends: [11]\n---\nSome content"
        assert parse_frontmatter(body, "depends") == "11"

    def test_scalar_value(self):
        body = "---\ndepends: 11\n---\nSome content"
        assert parse_frontmatter(body, "depends") == "11"

    def test_bracket_list_with_three_values(self):
        body = "---\ndepends: [11, 17, 18]\n---\nSome content"
        assert parse_frontmatter(body, "depends") == "11 17 18"

    def test_missing_field_returns_none(self):
        body = "---\nbranch: my-branch\n---\nSome content"
        assert parse_frontmatter(body, "depends") is None

    def test_no_frontmatter_returns_none(self):
        body = "Some content without frontmatter"
        assert parse_frontmatter(body, "depends") is None

    def test_extracts_branch_field(self):
        body = "---\nbranch: workon-skill\ndepends: [11, 17]\n---\nSome content"
        assert parse_frontmatter(body, "branch") == "workon-skill"

    def test_strips_whitespace_in_list_values(self):
        body = "---\ndepends: [ 11 , 17 ]\n---\nSome content"
        assert parse_frontmatter(body, "depends") == "11 17"

    def test_extracts_branch_from_frontmatter(self):
        body = "---\nbranch: fix-auth\n---\n# Spec"
        assert parse_frontmatter(body, "branch") == "fix-auth"

    def test_extracts_base_from_frontmatter(self):
        body = "---\nbranch: fix-auth\nbase: 8.x\n---\n# Spec"
        assert parse_frontmatter(body, "base") == "8.x"

    def test_no_frontmatter_returns_none_for_branch(self):
        body = "no frontmatter here"
        assert parse_frontmatter(body, "branch") is None

    def test_missing_field_returns_none_for_base(self):
        body = "---\nbranch: fix-auth\n---\n# Spec"
        assert parse_frontmatter(body, "base") is None

    def test_ignores_extra_fields(self):
        body = "---\nbranch: fix-auth\nbase: 8.x\nextra: ignored\n---\n# Spec"
        assert parse_frontmatter(body, "branch") == "fix-auth"

    def test_handles_whitespace_after_colon(self):
        body = "---\nbranch:   fix-auth\n---\n# Spec"
        assert parse_frontmatter(body, "branch") == "fix-auth"


# ---------------------------------------------------------------------------
# parse_issue_branch
# ---------------------------------------------------------------------------

class TestParseIssueBranch:
    def test_extracts_branch_from_title(self):
        assert parse_issue_branch("[my-branch] Some Title") == "my-branch"

    def test_handles_branches_with_slashes(self):
        assert parse_issue_branch("[feature/foo] Title") == "feature/foo"

    def test_handles_branches_with_numbers_and_hyphens(self):
        assert parse_issue_branch("[fix-123-bug] Title") == "fix-123-bug"

    def test_malformed_title_returns_none(self):
        assert parse_issue_branch("No brackets here") is None
