"""Shared helpers: paths, names, redaction."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

PLUGIN_NAME = "mcp-plugin-creator"

# Agent Plugins v1 constraints (mirrors hermes_cli/agent_plugins.py).
PLUGIN_NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
SKILL_NAME_RE = re.compile(r"^(?!.*--)[a-z0-9]+(?:-[a-z0-9]+)*$")
SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

SECRET_KEY_HINT = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.I)
VAR_REF_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        env = os.environ.get("HERMES_HOME")
        if env:
            return Path(env)
        local = os.environ.get("LOCALAPPDATA")
        return Path(local) / "hermes" if local else Path.home() / ".hermes"


def data_dir(*parts: str) -> Path:
    """Profile-scoped writable dir: <HERMES_HOME>/plugin-data/mcp-plugin-creator/<parts>."""
    try:
        from plugins.plugin_storage import plugin_data_dir
        root = Path(plugin_data_dir(PLUGIN_NAME))
    except Exception:
        root = hermes_home() / "plugin-data" / PLUGIN_NAME
    path = root.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_component(value: str) -> str:
    """Same rule Hermes uses for the mcp__<server>__<tool> name components."""
    return re.sub(r"[^A-Za-z0-9_]", "_", str(value or ""))


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:80] or "unnamed"


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def ok(**payload: Any) -> str:
    return json.dumps({"success": True, **payload}, ensure_ascii=False, default=str)


def fail(message: str, **payload: Any) -> str:
    return json.dumps({"success": False, "error": message, **payload}, ensure_ascii=False, default=str)


def truncate(text: Any, limit: int) -> str:
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"... [+{len(text) - limit} chars]"


# --------------------------------------------------------------------------- redaction

_SECRET_PATTERNS = [
    ("api_key", re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("url_credentials", re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
    ("long_hex", re.compile(r"\b[a-f0-9]{40,}\b")),
]
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Windows drive paths and POSIX home paths.
_WIN_PATH_RE = re.compile(r"\b[A-Za-z]:[\\/](?:[^\s\"'<>|*?:,;)\]}]+[\\/]?)*")
_POSIX_HOME_RE = re.compile(r"(?:/home|/Users)/[A-Za-z0-9._-]+(?:/[^\s\"'<>|,;)\]}]*)?")


def _home_markers() -> list[str]:
    markers = set()
    for value in (str(Path.home()), os.environ.get("USERPROFILE", ""), os.environ.get("USERNAME", ""),
                  os.environ.get("USER", "")):
        if value and len(value) > 2:
            markers.add(value)
    return sorted(markers, key=len, reverse=True)


def redact(text: str, *, paths: bool = True) -> str:
    """Strip secrets, emails and (optionally) user filesystem paths from free text."""
    if not text:
        return text
    for label, pattern in _SECRET_PATTERNS:
        text = pattern.sub(f"<{label.upper()}>", text)
    text = _EMAIL_RE.sub("<EMAIL>", text)
    if paths:
        text = _WIN_PATH_RE.sub("<PATH>", text)
        text = _POSIX_HOME_RE.sub("<PATH>", text)
        for marker in _home_markers():
            text = text.replace(marker, "<USER>")
    return text


def scan_for_private_data(text: str) -> list[dict[str, str]]:
    """Report (do not rewrite) secrets, emails, user paths and user names — the publish gate."""
    findings: list[dict[str, str]] = []
    for label, pattern in _SECRET_PATTERNS:
        for m in pattern.finditer(text):
            findings.append({"kind": label, "match": m.group(0)[:60]})
    for m in _EMAIL_RE.finditer(text):
        if not m.group(0).lower().endswith(("@example.com", "@example.org", "@users.noreply.github.com")):
            findings.append({"kind": "email", "match": m.group(0)})
    for m in _WIN_PATH_RE.finditer(text):
        findings.append({"kind": "windows_path", "match": m.group(0)[:120]})
    for m in _POSIX_HOME_RE.finditer(text):
        findings.append({"kind": "home_path", "match": m.group(0)[:120]})
    lowered = text.lower()
    for marker in _home_markers():
        if os.sep not in marker and "/" not in marker and marker.lower() in lowered:
            findings.append({"kind": "user_name", "match": marker})
    return findings
