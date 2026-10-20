"""Validate a generated package with Hermes itself, link it into the plugins dir, detect conflicts."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .common import hermes_home
from .scaffold import read_configured_servers


def _hermes_cmd() -> list[str]:
    exe = shutil.which("hermes")
    if exe:
        return [exe]
    return [sys.executable, "-m", "hermes_cli.main"]


def _run(args: list[str], timeout: int = 180) -> dict:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", NO_COLOR="1")
    try:
        p = subprocess.run(_hermes_cmd() + args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=env)
        return {"exit_code": p.returncode, "stdout": p.stdout[-6000:], "stderr": p.stderr[-3000:]}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"timed out after {timeout}s"}


def validate(root: str) -> dict:
    """``hermes plugins validate --json`` plus a direct load through Hermes' portable loader."""
    out: dict = {"root": root}
    res = _run(["plugins", "validate", "--json", root])
    out["validate_exit"] = res["exit_code"]
    try:
        out["validate"] = json.loads(res["stdout"])
    except Exception:
        out["validate_raw"] = (res["stdout"] or res["stderr"])[-3000:]
    # `hermes plugins validate` loads portable packages through Hermes' own loader, so its verdict
    # is the load check; no private core import needed (catalog rule 9).
    v = out.get("validate") or {}
    failed = [c for c in v.get("checks", []) if not c.get("ok")]
    out["loader"] = {"ok": out["validate_exit"] == 0 and bool(v.get("ok", out["validate_exit"] == 0)),
                     "diagnostics": [f"{c.get('name')}: {c.get('detail')}" for c in failed] + list(v.get("warnings", []))}
    out["success"] = out["loader"]["ok"]
    return out


def conflicts(server_names: list[str], plugin_name: str | None = None) -> dict:
    """A config.yaml mcp_servers entry with the same name silently wins over a package server."""
    configured = read_configured_servers()
    clashes = {n: configured[n] for n in server_names if n in configured}
    plugin_dir = hermes_home() / "plugins" / plugin_name if plugin_name else None
    return {
        "config_overrides": {n: {k: v for k, v in c.items() if k not in ("env", "headers")}
                             for n, c in clashes.items()},
        "plugin_slot_taken": bool(plugin_dir and (plugin_dir.exists() or plugin_dir.is_symlink())),
        "plugin_slot": str(plugin_dir) if plugin_dir else None,
        "advice": ([f"config.yaml mcp_servers.{n} overrides the plugin's server, even with enabled: false. "
                    f"To use the plugin's server, remove the entry: hermes config unset mcp_servers.{n} "
                    f"(back up config.yaml first), then /new"
                    for n in clashes]),
    }


def link(root: str, plugin_name: str, *, replace: bool = False) -> dict:
    """Junction (Windows) / symlink the build into <HERMES_HOME>/plugins/<name> — linked dev mode."""
    src = Path(root).resolve()
    dest = hermes_home() / "plugins" / plugin_name
    if dest.exists() or dest.is_symlink() or _is_junction(dest):
        if _is_junction(dest) or dest.is_symlink():
            current = os.path.realpath(dest)
            if Path(current) == src:
                return {"success": True, "linked": str(dest), "target": str(src), "already": True}
            if not replace:
                return {"success": False, "error": f"{dest} already links to {current}; pass replace=true"}
            _unlink(dest)
        else:
            return {"success": False, "error": f"{dest} is a real directory (an installed plugin). "
                                               "Refusing to touch it; pick another name or remove it yourself."}
    dest.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import _winapi  # directory junction without a shell (no admin rights needed)
        try:
            _winapi.CreateJunction(str(src), str(dest))
        except OSError as exc:
            return {"success": False, "error": str(exc)}
    else:
        dest.symlink_to(src, target_is_directory=True)
    return {"success": True, "linked": str(dest), "target": str(src)}


def enable(plugin_name: str) -> dict:
    res = _run(["plugins", "enable", plugin_name], timeout=120)
    return {"success": res["exit_code"] == 0, **res}


def unlink(plugin_name: str) -> dict:
    dest = hermes_home() / "plugins" / plugin_name
    if not (_is_junction(dest) or dest.is_symlink()):
        return {"success": False, "error": f"{dest} is not an mcp-plugin-creator link; not touching it"}
    _run(["plugins", "disable", plugin_name], timeout=120)
    _unlink(dest)
    return {"success": True, "removed": str(dest)}


def _is_junction(path: Path) -> bool:
    try:
        return bool(getattr(path, "is_junction", lambda: False)()) or (
            os.name == "nt" and path.exists() and os.path.realpath(path) != str(path.absolute())
            and not path.is_symlink() and path.is_dir() and _reparse(path))
    except OSError:
        return False


def _reparse(path: Path) -> bool:
    try:
        import stat
        return bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except (OSError, AttributeError):
        return False


def _unlink(path: Path) -> None:
    if os.name == "nt":
        os.rmdir(path)  # removes the junction, never the target's contents
    else:
        path.unlink()
