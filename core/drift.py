"""Detect server drift since the last build and point at the skill sections that mention changed tools."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .common import data_dir, safe_filename
from .introspect import introspect, load_snapshot


def _baseline_path(server: str) -> Path:
    return data_dir("baselines") / f"{safe_filename(server)}.json"


def save_baseline(server: str, snap: dict, package_root: str | None) -> dict:
    """Freeze the snapshot used for a build so later refreshes compare against it."""
    record = {"server": server, "fingerprint": snap.get("fingerprint"), "taken_at": snap.get("taken_at"),
              "tools": {t["name"]: t.get("inputSchema", {}) for t in snap.get("tools", [])},
              "server_version": (snap.get("server_info") or {}).get("version"),
              "package_root": package_root, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _baseline_path(server).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def check(server: str, config: dict, *, timeout: float = 120.0, plugin_root: str | None = None) -> dict:
    base_file = _baseline_path(server)
    if not base_file.is_file():
        return {"success": False, "error": f"no baseline for {server!r}; build it with mcp-plugin-creator first"}
    base = json.loads(base_file.read_text(encoding="utf-8"))
    snap = introspect(server, config, timeout=timeout, plugin_root=plugin_root)
    if not snap.get("success"):
        return {"success": False, "error": "could not reach server", "detail": snap}
    now = {t["name"]: t.get("inputSchema", {}) for t in snap.get("tools", [])}
    old = base["tools"]
    added = sorted(set(now) - set(old))
    removed = sorted(set(old) - set(now))
    changed = sorted(n for n in set(now) & set(old)
                     if json.dumps(now[n], sort_keys=True) != json.dumps(old[n], sort_keys=True))
    mentions = _skill_mentions(base.get("package_root"), removed + changed)
    drifted = bool(added or removed or changed)
    verdict = ("unchanged" if not drifted else
               "recompile" if removed or (changed and mentions) else "additive")
    return {"success": True, "server": server, "verdict": verdict,
            "baseline_fingerprint": base["fingerprint"], "current_fingerprint": snap["fingerprint"],
            "server_version": {"baseline": base.get("server_version"),
                               "current": (snap.get("server_info") or {}).get("version")},
            "added": added, "removed": removed, "schema_changed": changed,
            "skill_lines_to_review": mentions, "package_root": base.get("package_root")}


def _skill_mentions(root: str | None, names: list[str]) -> list[dict]:
    if not root or not names or not Path(root).is_dir():
        return []
    hits = []
    pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b")
    for path in Path(root).rglob("*.md"):
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = pattern.search(line)
            if m:
                hits.append({"file": str(path.relative_to(root)).replace("\\", "/"), "line": i,
                             "tool": m.group(1), "text": line.strip()[:200]})
    return hits[:200]


def list_baselines() -> list[dict]:
    out = []
    for p in sorted(data_dir("baselines").glob("*.json")):
        try:
            b = json.loads(p.read_text(encoding="utf-8"))
            out.append({"server": b["server"], "fingerprint": b["fingerprint"],
                        "saved_at": b.get("saved_at"), "package_root": b.get("package_root")})
        except Exception:
            continue
    return out


def current_snapshot_fingerprint(server: str) -> str | None:
    snap = load_snapshot(server)
    return snap.get("fingerprint") if snap else None
