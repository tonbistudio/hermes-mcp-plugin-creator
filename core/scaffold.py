"""Write a portable Agent Plugins v1 package: plugin.json + mcp.json + skills/.

Deterministic. The skill *content* comes from the agent; this module only places files, enforces
the format rules (names, ${VAR} secrets, pinned versions) and keeps a build manifest.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .common import (PLUGIN_NAME_RE, SECRET_KEY_HINT, SERVER_NAME_RE, SKILL_NAME_RE, VAR_REF_RE,
                     data_dir, hermes_home, safe_filename, scan_for_private_data)

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
HERMES_EXT = "com.nousresearch.hermes"
BUILDS = {"local", "publish"}
# Written into every package root. Only a folder holding it may be emptied on rebuild.
MARKER = ".mcp-plugin-creator"

# Hermes adds these utility tools to every MCP server; they are not the server's own surface.
HERMES_UTILITY_TOOLS = {"list_resources", "read_resource", "list_prompts", "get_prompt"}

_UNPINNED = [
    (re.compile(r"@latest\b"), "uses @latest"),
    (re.compile(r"^(?:@[a-z0-9-]+/)?[a-z0-9._-]+$", re.I), "npm package without @version"),
]


def default_output_root() -> Path:
    return data_dir("builds")


# --------------------------------------------------------------------------- config import

def read_configured_servers() -> dict[str, dict]:
    """mcp_servers from the active profile's config.yaml (read-only)."""
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly() or {}
    except Exception:
        path = hermes_home() / "config.yaml"
        cfg = _yaml_load(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
    servers = cfg.get("mcp_servers") or {}
    return {k: dict(v) for k, v in servers.items() if isinstance(v, dict)}


def to_portable_server(config: dict) -> tuple[dict, list[str], dict[str, str]]:
    """Convert a Hermes mcp_servers entry to a portable mcp.json entry.

    Returns (entry, warnings, env_passthrough) where env_passthrough maps env names whose literal
    values were removed (secrets and machine paths) -> the original value, so the caller can tell
    the user what to put in .env. Hermes-only keys (tools filters, timeout, enabled) are dropped
    with a warning; transport timeout moves into the Hermes extension block.
    """
    warnings: list[str] = []
    removed: dict[str, str] = {}
    if config.get("url"):
        entry: dict[str, Any] = {"type": "streamable-http", "url": config["url"]}
        headers = {}
        for k, v in (config.get("headers") or {}).items():
            v = str(v)
            if VAR_REF_RE.search(v) or "${" in v:
                headers[k] = v
            else:
                var = re.sub(r"[^A-Z0-9]", "_", k.upper())
                headers[k] = f"${{{var}}}" if not v.lower().startswith("bearer ") else f"Bearer ${{{var}}}"
                removed[var] = "<header value>"
                warnings.append(f"header {k!r} literal moved to ${{{var}}} (set it in .env)")
        if headers:
            entry["headers"] = headers
        if str(config["url"]).startswith("http://") and not re.match(
                r"^http://(localhost|127\.0\.0\.1|\[::1\])[:/]", str(config["url"])):
            warnings.append("plain-HTTP URL to a non-loopback host is rejected by portable packages")
    else:
        entry = {"type": "stdio", "command": config.get("command", "")}
        args = [str(a) for a in config.get("args") or []]
        if args:
            entry["args"] = args
            for a in args:
                if _looks_machine_specific(a):
                    warnings.append(f"arg {a!r} is a machine path: fine for a local build; for publish "
                                    "ship the script in the package and use ${PLUGIN_ROOT}, or a pinned package")
        env = {}
        for k, v in (config.get("env") or {}).items():
            v = str(v)
            if "${" in v:
                env[k] = v
            elif SECRET_KEY_HINT.search(k) or _looks_machine_specific(v):
                env[k] = f"${{{k}}}"
                removed[k] = v
                warnings.append(f"env {k} literal replaced with ${{{k}}} (secret or machine path)")
            else:
                env[k] = v
        if env:
            entry["env"] = env
        if config.get("cwd"):
            entry["cwd"] = str(config["cwd"])
            if _looks_machine_specific(entry["cwd"]):
                warnings.append("cwd is a machine path; use ${PLUGIN_ROOT} or drop it")
        warnings.extend(pin_warnings(entry["command"], args))
    tools_filter = config.get("tools")
    if tools_filter:
        warnings.append(f"Hermes tools filter {tools_filter} is not portable; the skill must say "
                        "which tools to use (or keep the filter in your config).")
    return entry, warnings, removed


def _looks_machine_specific(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith(("/home/", "/Users/")))


def pin_warnings(command: str, args: list[str]) -> list[str]:
    out = []
    cmd = Path(command).name.lower().removesuffix(".exe").removesuffix(".cmd")
    if cmd in {"npx", "bunx", "pnpx"}:
        pkgs = [a for a in args if not a.startswith("-")][:1]
        for p in pkgs:
            if p.endswith("@latest") or ("@" not in p.lstrip("@")):
                out.append(f"{cmd} package {p!r} is not pinned to an exact version")
    if cmd in {"uvx", "pipx"}:
        joined = " ".join(args)
        if "==" not in joined and "@" not in joined and "git+" not in joined:
            out.append(f"{cmd} package is not pinned (use pkg==x.y.z or a git commit)")
    if cmd in {"docker", "podman"} and "@sha256:" not in " ".join(args):
        out.append("container image is not pinned by digest")
    return out


# --------------------------------------------------------------------------- write package

def write_package(
    *,
    plugin_name: str,
    description: str,
    version: str = "0.1.0",
    servers: dict[str, dict],
    skills: list[dict],
    output_dir: str | None = None,
    author: dict | None = None,
    homepage: str | None = None,
    license: str | None = "MIT",
    keywords: list[str] | None = None,
    hermes_servers: dict[str, dict] | None = None,
    readme: str | None = None,
    build: str = "local",
    provenance: dict | None = None,
    overwrite: bool = True,
) -> dict:
    """Write the package and return paths, warnings and the private-data scan.

    ``skills``: [{"name", "skill_md", "references": {"setup.md": "..."}, "scripts": {...},
    "templates": {...}}]. ``servers``: portable mcp.json entries keyed by server name.
    ``build``: "local" (personal detail allowed) or "publish" (private-data findings are errors).
    """
    errors: list[str] = []
    warnings: list[str] = []
    if build not in BUILDS:
        return {"success": False, "errors": [f"build must be one of {sorted(BUILDS)}, got {build!r}"]}
    if not PLUGIN_NAME_RE.fullmatch(plugin_name or "") or len(plugin_name) > 64:
        errors.append(f"plugin name {plugin_name!r} must be lowercase a-z0-9 with . or - separators")
    if not servers:
        errors.append("at least one MCP server entry is required")
    for sname, entry in servers.items():
        if not SERVER_NAME_RE.fullmatch(sname):
            errors.append(f"server name {sname!r}: use 1-32 chars of A-Za-z0-9_-")
        elif len(sname) > 16:
            warnings.append(f"server name {sname!r} is long; tool names are capped at 64 chars "
                            f"(mcp__{sname}__<tool>) — prefer a short name")
        if entry.get("type") == "stdio":
            for k, v in (entry.get("env") or {}).items():
                if SECRET_KEY_HINT.search(k) and not str(v).startswith("${") and str(v) != "":
                    errors.append(f"server {sname}: env {k} holds a literal secret; use ${{{k}}}")
                if str(v) == "" and SECRET_KEY_HINT.search(k):
                    errors.append(f"server {sname}: env {k} is an empty literal; use ${{{k}}}")
            warnings.extend(pin_warnings(entry.get("command", ""), entry.get("args") or []))
        elif entry.get("type") not in ("streamable-http",):
            errors.append(f"server {sname}: type must be 'stdio' or 'streamable-http'")
    for sk in skills:
        n = sk.get("name", "")
        if not SKILL_NAME_RE.fullmatch(n) or len(n) > 64:
            errors.append(f"skill name {n!r} must be lowercase words joined by single hyphens")
        md = sk.get("skill_md", "")
        fm_name = _frontmatter_value(md, "name")
        if fm_name != n:
            errors.append(f"skill {n}: frontmatter name {fm_name!r} must equal the directory name")
        desc = _frontmatter_value(md, "description") or ""
        if not desc or len(desc) > 1024:
            errors.append(f"skill {n}: description must be 1-1024 chars")
        elif not desc.lower().startswith("use when"):
            warnings.append(f"skill {n}: description should start with 'Use when …' (trigger first)")
        lines = md.count("\n")
        if lines > 400:
            warnings.append(f"skill {n}: SKILL.md is {lines} lines; move topic detail to references/")
    if errors:
        return {"success": False, "errors": errors, "warnings": warnings}

    root, problem = resolve_root(plugin_name, build, output_dir)
    if problem:
        return {"success": False, "errors": [problem]}
    if root.exists():
        if not root.is_dir() or root.is_symlink():
            return {"success": False, "errors": [f"{root} exists and is not a plain directory; refusing"]}
        if any(root.iterdir()):
            if not (root / MARKER).is_file():
                return {"success": False, "errors": [
                    f"{root} is not empty and was not created by mcp-plugin-creator (no {MARKER}); "
                    "refusing to replace its contents. Choose an empty or new folder."]}
            if not overwrite:
                return {"success": False, "errors": [f"{root} exists (overwrite=false)"]}
            _clear_package(root)
    root.mkdir(parents=True, exist_ok=True)
    _write(root / MARKER, json.dumps({"plugin": plugin_name, "build": build}) + "\n")

    manifest: dict[str, Any] = {"$schema": PLUGIN_SCHEMA, "name": plugin_name, "version": version,
                                "description": description}
    if author:
        manifest["author"] = {k: v for k, v in author.items() if k in ("name", "email", "url") and v}
    if homepage:
        manifest["homepage"] = homepage
    if license:
        manifest["license"] = license
    if keywords:
        manifest["keywords"] = [str(k) for k in keywords]
    if hermes_servers:
        manifest["extensions"] = {HERMES_EXT: {"servers": hermes_servers}}
    _write(root / "plugin.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    _write(root / "mcp.json", json.dumps({"$schema": MCP_SCHEMA, "mcpServers": servers},
                                         indent=2, ensure_ascii=False) + "\n")
    written = ["plugin.json", "mcp.json"]
    for sk in skills:
        base = root / "skills" / sk["name"]
        _write(base / "SKILL.md", sk["skill_md"].rstrip() + "\n")
        written.append(f"skills/{sk['name']}/SKILL.md")
        for folder in ("references", "scripts", "templates"):
            for fname, body in (sk.get(folder) or {}).items():
                if "/" in fname or "\\" in fname or fname.startswith("."):
                    warnings.append(f"skipped unsafe file name {folder}/{fname}")
                    continue
                _write(base / folder / fname, body.rstrip() + "\n")
                written.append(f"skills/{sk['name']}/{folder}/{fname}")
    if readme:
        _write(root / "README.md", readme.rstrip() + "\n")
        written.append("README.md")
    if license == "MIT" and not (root / "LICENSE").exists():
        holder = (author or {}).get("name") or "the authors"
        _write(root / "LICENSE", _MIT.format(year=time.strftime("%Y"), holder=holder))
        written.append("LICENSE")

    # Creator bookkeeping lives outside the package so it never ships.
    record = {"plugin": plugin_name, "build": build, "root": str(root), "version": version,
              "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": written,
              "servers": list(servers), "provenance": provenance or {}}
    (data_dir("builds") / f"{safe_filename(plugin_name)}-{build}.creator.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8")

    private = scan_package(root)
    result = {"success": True, "root": str(root), "files": written, "warnings": warnings,
              "private_data": private}
    if build == "publish" and private:
        result["success"] = False
        result["errors"] = [f"publish build contains {len(private)} private-data finding(s); "
                            "generalize or remove them, then rebuild"]
    return result


def resolve_root(plugin_name: str, build: str, output_dir: str | None) -> tuple[Path, str | None]:
    """Final package root, refused unless it is inside the builds dir or is the given output_dir.

    ``plugin_name`` and ``build`` are already validated; the check still resolves the path, so a
    future caller can't escape through either.
    """
    builds = default_output_root().resolve()
    if output_dir:
        given = Path(output_dir)
        if not given.is_absolute():
            return given, f"output_dir must be an absolute path, got {output_dir!r}"
        root = given.resolve()
        if root.parent == root:
            return root, f"output_dir {output_dir!r} is a filesystem root; refusing"
        if root in (hermes_home().resolve(), Path.home().resolve()):
            return root, f"output_dir {output_dir!r} is a home directory; refusing"
        return root, None
    root = (builds / f"{plugin_name}-{build}").resolve()
    if root.parent != builds:
        return root, f"package root {root} escapes {builds}; refusing"
    return root, None


def scan_package(root: Path) -> list[dict]:
    findings = []
    for path in sorted(Path(root).rglob("*")):
        if path.is_file() and path.suffix.lower() in {".md", ".json", ".py", ".txt", ".yaml", ".yml"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            for f in scan_for_private_data(text):
                findings.append({"file": str(path.relative_to(root)).replace("\\", "/"), **f})
    return findings


def _frontmatter_value(md: str, key: str) -> str | None:
    if not md.startswith("---"):
        return None
    end = md.find("\n---", 3)
    if end < 0:
        return None
    try:
        data = _yaml_load(md[3:end]) or {}
    except Exception:  # malformed frontmatter is reported by the caller as a missing value
        return None
    v = data.get(key) if isinstance(data, dict) else None
    return v if isinstance(v, str) else None


def _yaml_load(text: str) -> Any:
    """YAML via Hermes' public loader (core since 0.22.0); PyYAML is not a Hermes dependency."""
    from hermes_yaml import safe_load
    return safe_load(text)


def _clear_package(root: Path) -> None:
    """Remove a previous build's content. Callers check for MARKER first; links are never followed."""
    for child in root.iterdir():
        if child.is_symlink():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


_MIT = """MIT License

Copyright (c) {year} {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
