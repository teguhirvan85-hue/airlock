"""The only shape a triage summary may take to leave the airlock room.

The Reader turns a raw issue into this structure. The gatekeeper checks it
here, with no model involved, before a person decides whether to forward it
to the workshop room. Anything outside the shape is refused, so free text
from the issue cannot ride along into the room where the Fixer works.
"""

import json
import re

SEVERITIES = ("low", "medium", "high")
MAX_TITLE = 120
MAX_STEP = 200
MAX_STEPS = 8
MAX_FIELD = 300
MAX_FILES = 10

FIELDS = {"title", "severity", "steps", "expected", "actual", "files"}

# A repo-relative path: no scheme, no leading slash, no "..".
PATH = re.compile(r"^(?!/)(?!.*\.\.)[A-Za-z0-9_./-]{1,200}$")
URL = re.compile(r"\b[a-z][a-z0-9+.-]*://", re.IGNORECASE)
MENTION = re.compile(r"(^|\s)@[A-Za-z0-9_.-]+")
CONTROL = re.compile(r"[\u0000-\u0008\u000b-\u001f\u007f​-‏⁠-⁤﻿]")


class Refused(ValueError):
    """The summary does not fit the shape; the message says why."""


def _text(name: str, value: object, limit: int) -> str:
    if not isinstance(value, str):
        raise Refused(f"{name} must be text")
    value = value.strip()
    if not value:
        raise Refused(f"{name} is empty")
    if len(value) > limit:
        raise Refused(f"{name} is longer than {limit} characters")
    if "```" in value or "<" in value:
        raise Refused(f"{name} contains markup")
    if URL.search(value):
        raise Refused(f"{name} contains a link")
    if MENTION.search(value):
        raise Refused(f"{name} mentions someone")
    if CONTROL.search(value):
        raise Refused(f"{name} contains hidden characters")
    return value


def check(raw: str) -> dict:
    """Parse and check a summary. Returns the clean summary or raises Refused."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Refused(f"not JSON ({exc.msg})") from None
    if not isinstance(data, dict):
        raise Refused("not a JSON object")

    extra = set(data) - FIELDS
    missing = FIELDS - set(data)
    if extra:
        raise Refused(f"unexpected fields: {', '.join(sorted(extra))}")
    if missing:
        raise Refused(f"missing fields: {', '.join(sorted(missing))}")

    severity = data["severity"]
    if severity not in SEVERITIES:
        raise Refused(f"severity must be one of {', '.join(SEVERITIES)}")

    steps = data["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise Refused(f"steps must be a list of 1 to {MAX_STEPS} items")

    files = data["files"]
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise Refused(f"files must be a list of at most {MAX_FILES} paths")
    for path in files:
        if not isinstance(path, str) or not PATH.match(path):
            raise Refused(f"not a repository path: {str(path)[:60]!r}")

    return {
        "title": _text("title", data["title"], MAX_TITLE),
        "severity": severity,
        "steps": [_text(f"step {i + 1}", step, MAX_STEP) for i, step in enumerate(steps)],
        "expected": _text("expected", data["expected"], MAX_FIELD),
        "actual": _text("actual", data["actual"], MAX_FIELD),
        "files": files,
    }


def render(summary: dict) -> str:
    """The clean summary as the card a person reads and forwards."""
    steps = "\n".join(f"{i}. {step}" for i, step in enumerate(summary["steps"], 1))
    files = ", ".join(f"`{path}`" for path in summary["files"]) or "none named"
    return (
        f"**{summary['title']}** (severity: {summary['severity']})\n\n"
        f"{steps}\n\n"
        f"Expected: {summary['expected']}\n"
        f"Actual: {summary['actual']}\n"
        f"Files: {files}"
    )
