"""Pure utility functions for ralph."""

import re
import sys


def parse_duration(s):
    """Parse duration string like '30', '30s', '5m', '2h', '1d'. Returns seconds.

    Raises ValueError for invalid input.
    """
    if not s:
        raise ValueError("empty string")
    m = re.match(r'^(\d+)([smhd]?)$', s)
    if not m:
        raise ValueError(s)
    num = int(m.group(1))
    suffix = m.group(2)
    multipliers = {'': 1, 's': 1, 'm': 60, 'h': 3600, 'd': 86400}
    return num * multipliers[suffix]


def parse_frontmatter(body, field):
    """Extract a field from YAML-like frontmatter (between --- delimiters).
    Returns None when field is missing or no frontmatter found.
    For bracket lists like [11, 17], returns space-separated string of values.
    """
    lines = body.split('\n')
    # Find frontmatter block
    fm_lines = []
    in_fm = False
    for line in lines:
        if line.strip() == '---':
            if not in_fm:
                in_fm = True
                continue
            else:
                break
        if in_fm:
            fm_lines.append(line)

    if not fm_lines:
        return None

    for line in fm_lines:
        colon_pos = line.find(':')
        if colon_pos < 0:
            continue
        key = line[:colon_pos].strip()
        if key != field:
            continue
        value = line[colon_pos + 1:].strip()
        if not value:
            return None
        # Handle bracket list: [11, 17]
        if value.startswith('[') and value.endswith(']'):
            inner = value[1:-1]
            parts = [p.strip() for p in inner.split(',') if p.strip()]
            return ' '.join(parts)
        return value

    return None


def parse_issue_branch(title):
    """Extract branch name from [branch] prefix in issue title."""
    m = re.match(r'^\[([^\]]+)\]', title)
    if m:
        return m.group(1)
    print(f"ralph: cannot parse branch from issue title: {title}", file=sys.stderr)
    return None
