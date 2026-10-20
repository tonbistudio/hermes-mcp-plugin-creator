"""On-demand re-examination of a generated plugin: has the server changed, and what happened in use
since the last build? Read-only. It reports; it never edits the package."""
from __future__ import annotations

import glob
import json
import time
from pathlib import Path

from . import drift, mining
from .common import data_dir, safe_filename


def _build_record(plugin: str) -> dict | None:
    """Newest build record for *plugin* (local build preferred)."""
    files = sorted(glob.glob(str(data_dir("builds") / f"{safe_filename(plugin)}-*.creator.json")),
                   key=lambda f: ("-local." not in f, -Path(f).stat().st_mtime))
    return json.loads(Path(files[0]).read_text(encoding="utf-8")) if files else None


def list_built() -> list[str]:
    names = set()
    for f in glob.glob(str(data_dir("builds") / "*.creator.json")):
        try:
            names.add(json.loads(Path(f).read_text(encoding="utf-8"))["plugin"])
        except Exception:
            continue
    return sorted(names)


def _server_config(entry: dict) -> dict:
    if entry.get("type") == "streamable-http" or entry.get("url"):
        return {"url": entry["url"], "headers": entry.get("headers") or {}}
    return {k: entry[k] for k in ("command", "args", "env", "cwd") if k in entry}


def review(plugin: str, *, timeout: float = 90.0, probe: bool = True) -> dict:
    rec = _build_record(plugin)
    if not rec:
        return {"success": False, "error": f"no build record for {plugin!r}",
                "known": list_built()}
    root = Path(rec["root"])
    try:
        manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
        servers = json.loads((root / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    except Exception as exc:
        return {"success": False, "error": f"cannot read package at {root}: {exc}"}

    built_at = rec.get("written_at")
    try:
        since_days = (time.time() - time.mktime(time.strptime(built_at, "%Y-%m-%dT%H:%M:%S"))) / 86400
    except Exception:
        since_days = None

    out = {"success": True, "plugin": plugin, "version": manifest.get("version"), "root": str(root),
           "built_at": built_at, "servers": {}}
    reasons = []
    for name, entry in servers.items():
        s: dict = {}
        # 1. server drift (or create the first baseline)
        if probe:
            if (data_dir("baselines") / f"{safe_filename(name)}.json").is_file():
                d = drift.check(name, _server_config(entry), timeout=timeout, plugin_root=str(root))
                if d.get("success"):
                    s["drift"] = {k: d[k] for k in ("verdict", "added", "removed", "schema_changed",
                                                     "skill_lines_to_review", "server_version")}
                    if d["verdict"] != "unchanged":
                        reasons.append(f"{name}: server tools {d['verdict']}")
                else:
                    s["drift"] = {"verdict": "unreachable", "detail": (d.get("detail") or {}).get("error")
                                  or d.get("error")}
            else:
                from .introspect import introspect
                snap = introspect(name, _server_config(entry), timeout=timeout, plugin_root=str(root))
                if snap.get("success"):
                    drift.save_baseline(name, snap, str(root))
                    s["drift"] = {"verdict": "baseline_created", "tools": snap["tool_count"]}
                else:
                    s["drift"] = {"verdict": "unreachable", "detail": snap.get("error")}
        # 2. usage since the build
        b = mining.mine(name, since_days=since_days, save=False)
        if b.get("success"):
            s["usage_since_build"] = {
                "sessions": max(b["sessions_with_calls"], b.get("live_log_sessions", 0)), "calls": b["total_calls"], "errors": b["total_errors"],
                "per_tool": b["per_tool"],
                "new_error_samples": {t: [e["error"][:300] for e in v[:3]] for t, v in b["error_samples"].items()},
            }
            if b["total_errors"]:
                reasons.append(f"{name}: {b['total_errors']} tool error(s) in use since the build")
        out["servers"][name] = s

    out["refresh_suggested"] = bool(reasons)
    out["reasons"] = reasons
    out["next"] = (f'Say "refresh the {plugin} plugin" to update it (mcp-plugin-creator skill, Refresh section).'
                   if reasons else "Nothing new since the last build.")
    return out


def format_report(r: dict) -> str:
    if not r.get("success"):
        known = ", ".join(r.get("known") or []) or "none"
        return f"{r.get('error')}. Built plugins: {known}"
    lines = [f"**{r['plugin']} {r['version']}**: built {r['built_at']}"]
    for name, s in r["servers"].items():
        d = s.get("drift", {})
        verdict = d.get("verdict", "not checked")
        extra = ""
        if verdict in ("additive", "recompile"):
            extra = f" (+{len(d['added'])} / -{len(d['removed'])} / ~{len(d['schema_changed'])} tools)"
        elif verdict == "unreachable":
            extra = " (server not running?)"
        u = s.get("usage_since_build")
        usage = (f"{u['calls']} calls, {u['errors']} errors in {u['sessions']} sessions"
                 if u else "usage unavailable")
        lines.append(f"- `{name}`: tools {verdict}{extra}; since build: {usage}")
        for tool, errs in (u or {}).get("new_error_samples", {}).items():
            lines.append(f"  - `{tool}`: {errs[0][:160]}")
    lines.append("")
    lines.append("**Refresh suggested:** " + "; ".join(r["reasons"]) if r["refresh_suggested"]
                 else "No refresh needed.")
    if r["refresh_suggested"]:
        lines.append(r["next"])
    return "\n".join(lines)
