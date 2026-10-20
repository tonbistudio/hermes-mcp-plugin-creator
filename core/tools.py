"""Tool schemas + handlers. Handlers are deterministic; synthesis happens in the agent turn."""
from __future__ import annotations

import json
from typing import Any

from . import drift, install, introspect, mining, review, scaffold
from .common import fail, ok, truncate

TOOLSET = "mcp_plugin_creator"


def _server_config(args: dict) -> tuple[str, dict] | str:
    """Resolve {server} from config.yaml, or an inline {config}. Returns error string on failure."""
    name = (args.get("server") or "").strip()
    inline = args.get("config")
    if isinstance(inline, str) and inline.strip():
        try:
            inline = json.loads(inline)
        except Exception:
            return "config must be a JSON object"
    if inline:
        if not name:
            return "give a server name with an inline config"
        return name, dict(inline)
    if not name:
        return "server is required"
    configured = scaffold.read_configured_servers()
    if name not in configured:
        return f"no mcp_servers.{name} in config.yaml (known: {sorted(configured)}); pass config inline"
    return name, configured[name]


# --------------------------------------------------------------------------- handlers

def h_servers(args: dict, **_: Any) -> str:
    configured = scaffold.read_configured_servers()
    rows = []
    for name, cfg in configured.items():
        rows.append({"server": name,
                     "transport": "streamable-http" if cfg.get("url") else "stdio",
                     "launch": cfg.get("url") or " ".join([str(cfg.get("command", ""))] +
                                                          [str(a) for a in cfg.get("args") or []]),
                     "enabled": cfg.get("enabled", True),
                     "env_keys": sorted((cfg.get("env") or {}).keys()),
                     "tool_filter": cfg.get("tools")})
    return ok(configured=rows, baselines=drift.list_baselines())


def h_introspect(args: dict, **_: Any) -> str:
    resolved = _server_config(args)
    if isinstance(resolved, str):
        return fail(resolved)
    name, cfg = resolved
    if cfg.get("enabled") is False and not args.get("allow_disabled", True):
        return fail(f"{name} is disabled in config")
    snap = introspect.introspect(name, cfg, timeout=float(args.get("timeout") or 120))
    if not snap.get("success"):
        return json.dumps(snap)
    return json.dumps(introspect.summarize(snap, max_desc=int(args.get("max_desc") or 220)),
                      ensure_ascii=False)


def h_mine(args: dict, **_: Any) -> str:
    server = (args.get("server") or "").strip()
    if not server:
        return fail("server is required")
    bundle = mining.mine(server, max_sessions=int(args.get("max_sessions") or 40),
                         since_days=args.get("since_days"),
                         redact_paths=bool(args.get("redact_paths")))
    out = mining.summarize(bundle, max_sessions=int(args.get("detail_sessions") or 6),
                           max_events=int(args.get("max_events") or 30))
    return truncate(json.dumps(out, ensure_ascii=False), 60000)


def h_convert(args: dict, **_: Any) -> str:
    resolved = _server_config(args)
    if isinstance(resolved, str):
        return fail(resolved)
    name, cfg = resolved
    entry, warnings, removed = scaffold.to_portable_server(cfg)
    return ok(server=name, portable_entry=entry, warnings=warnings,
              env_to_set=sorted(removed), note="Values in env_to_set were removed from the package; "
              "the user keeps them in their profile .env (Hermes forwards ${VAR}).")


def h_scaffold(args: dict, **_: Any) -> str:
    servers = args.get("servers")
    if isinstance(servers, str):
        try:
            servers = json.loads(servers)
        except Exception:
            return fail("servers must be a JSON object")
    skills = args.get("skills") or []
    if isinstance(skills, str):
        try:
            skills = json.loads(skills)
        except Exception:
            return fail("skills must be a JSON array")
    res = scaffold.write_package(
        plugin_name=args.get("plugin_name", ""), description=args.get("description", ""),
        version=args.get("version") or "0.1.0", servers=servers or {}, skills=skills,
        output_dir=args.get("output_dir"), author=args.get("author"), homepage=args.get("homepage"),
        license=args.get("license", "MIT"), keywords=args.get("keywords"),
        hermes_servers=args.get("hermes_servers"), readme=args.get("readme"),
        build=str(args.get("build") or "local"), provenance=args.get("provenance"),
    )
    if res.get("success") and args.get("baseline_server"):
        snap = introspect.load_snapshot(args["baseline_server"])
        if snap:
            drift.save_baseline(args["baseline_server"], snap, res["root"])
            res["baseline_saved"] = args["baseline_server"]
    return json.dumps(res, ensure_ascii=False)


def h_validate(args: dict, **_: Any) -> str:
    root = args.get("root")
    if not root:
        return fail("root is required")
    res = install.validate(root)
    try:
        manifest = json.loads((__import__("pathlib").Path(root) / "mcp.json").read_text(encoding="utf-8"))
        res["conflicts"] = install.conflicts(list(manifest.get("mcpServers", {})),
                                             json.loads((__import__("pathlib").Path(root) / "plugin.json")
                                                        .read_text(encoding="utf-8")).get("name"))
    except Exception as exc:
        res["conflicts_error"] = str(exc)
    return json.dumps(res, ensure_ascii=False)


def h_install(args: dict, **_: Any) -> str:
    root, name = args.get("root"), args.get("plugin_name")
    if not root or not name:
        return fail("root and plugin_name are required")
    v = install.validate(root)
    if not v.get("success"):
        return fail("package does not validate; fix it first", validation=v)
    linked = install.link(root, name, replace=bool(args.get("replace")))
    if not linked.get("success"):
        return json.dumps(linked)
    out = {"success": True, "link": linked}
    if args.get("enable", True):
        out["enable"] = install.enable(name)
        out["success"] = out["enable"]["success"]
    out["next"] = ("Start a NEW session (/new) so the plugin's MCP server and skill load. A config.yaml "
                   "mcp_servers entry with the same server name overrides the plugin even when disabled; "
                   "remove it with `hermes config unset mcp_servers.<name>`.")
    return json.dumps(out, ensure_ascii=False)


def h_drift(args: dict, **_: Any) -> str:
    resolved = _server_config(args)
    if isinstance(resolved, str):
        return fail(resolved)
    name, cfg = resolved
    return json.dumps(drift.check(name, cfg, timeout=float(args.get("timeout") or 120)), ensure_ascii=False)


def h_review(args: dict, **_: Any) -> str:
    plugin = (args.get("plugin") or "").strip()
    if not plugin:
        return ok(built=review.list_built())
    return json.dumps(review.review(plugin, timeout=float(args.get("timeout") or 90),
                                    probe=args.get("probe", True) is not False), ensure_ascii=False)


# --------------------------------------------------------------------------- schemas

def _schema(name: str, description: str, props: dict, required: list[str] | None = None) -> dict:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": required or []}}


_SERVER = {"type": "string", "description": "Server name as in config.yaml mcp_servers (or a new name with config)."}
_CONFIG = {"type": "object", "description": "Inline server config, Hermes mcp_servers shape: "
           "{command,args,env,cwd} or {url,headers}. Use ${VAR} for secrets."}

TOOLS = [
    ("mcp_creator_servers", h_servers, "🔎", _schema(
        "mcp_creator_servers",
        "List MCP servers configured in this profile (launch command, env key names, tool filters) "
        "and existing creator build baselines. Start here.", {})),
    ("mcp_creator_introspect", h_introspect, "🧭", _schema(
        "mcp_creator_introspect",
        "Connect once to an MCP server with a short-lived client and return its self-description: "
        "server info, instructions, tools (name, description, required/params, annotations), prompts "
        "and resources, plus a fingerprint. Saves the full snapshot for drift checks. Read-only: "
        "lists only, never calls tools.",
        {"server": _SERVER, "config": _CONFIG,
         "timeout": {"type": "number", "description": "Seconds (default 120; raise for slow first starts)."},
         "max_desc": {"type": "integer", "description": "Max description chars per tool (default 220)."}},
        ["server"])),
    ("mcp_creator_mine", h_mine, "⛏️", _schema(
        "mcp_creator_mine",
        "Mine past Hermes sessions (and the live call log) for real usage of one MCP server: per-tool "
        "call/error counts, error samples, common tool transitions, and the most error-dense sessions "
        "with the agent's diagnosis and the user's follow-ups. This is the main source of pitfalls.",
        {"server": _SERVER,
         "max_sessions": {"type": "integer", "description": "Sessions to scan, newest first (default 40)."},
         "since_days": {"type": "number", "description": "Only sessions from the last N days."},
         "redact_paths": {"type": "boolean", "description": "Also strip file paths (use for publish builds)."},
         "detail_sessions": {"type": "integer", "description": "Sessions returned in detail (default 6)."},
         "max_events": {"type": "integer", "description": "Events per detailed session (default 30)."}},
        ["server"])),
    ("mcp_creator_convert", h_convert, "🔁", _schema(
        "mcp_creator_convert",
        "Convert a config.yaml mcp_servers entry into a portable mcp.json entry: literal secrets and "
        "machine paths become ${VAR}, Hermes-only keys are reported, unpinned launchers are flagged.",
        {"server": _SERVER, "config": _CONFIG}, ["server"])),
    ("mcp_creator_scaffold", h_scaffold, "🏗️", _schema(
        "mcp_creator_scaffold",
        "Write a portable Agent Plugin package (plugin.json, mcp.json, skills/<name>/SKILL.md plus "
        "references/, scripts/, templates/). Enforces names, ${VAR} secrets and pinning; scans for "
        "private data (a publish build fails on any finding). Returns the package root.",
        {"plugin_name": {"type": "string", "description": "Lowercase package name, e.g. 'blender-studio'."},
         "description": {"type": "string", "description": "One paragraph: what it does, what it needs, disclosure."},
         "version": {"type": "string"},
         "servers": {"type": "object", "description": "mcp.json mcpServers map: {name: {type:'stdio',command,args,env} | {type:'streamable-http',url,headers}}."},
         "skills": {"type": "array", "description": "Skills to write.", "items": {"type": "object", "properties": {
             "name": {"type": "string"}, "skill_md": {"type": "string", "description": "Full SKILL.md including frontmatter."},
             "references": {"type": "object", "description": "{filename.md: content}"},
             "scripts": {"type": "object", "description": "{filename.py: content}"},
             "templates": {"type": "object", "description": "{filename: content}"}},
             "required": ["name", "skill_md"]}},
         "hermes_servers": {"type": "object", "description": "Optional extensions.com.nousresearch.hermes.servers block (app/requires/liveness/trust/timeout)."},
         "readme": {"type": "string"},
         "keywords": {"type": "array", "items": {"type": "string"}},
         "author": {"type": "object", "properties": {"name": {"type": "string"}, "url": {"type": "string"}}},
         "homepage": {"type": "string"}, "license": {"type": "string"},
         "build": {"type": "string", "enum": ["local", "publish"], "description": "local keeps personal detail; publish must be clean."},
         "output_dir": {"type": "string", "description": "Default: plugin-data/mcp-plugin-creator/builds/<name>-<build>."},
         "baseline_server": {"type": "string", "description": "Server whose last introspect snapshot becomes the drift baseline."},
         "provenance": {"type": "object", "description": "Evidence summary recorded outside the package."}},
        ["plugin_name", "description", "servers", "skills"])),
    ("mcp_creator_validate", h_validate, "✅", _schema(
        "mcp_creator_validate",
        "Validate a package with `hermes plugins validate` and Hermes' portable loader; report config "
        "conflicts (a same-named config.yaml server silently overrides the package).",
        {"root": {"type": "string", "description": "Package root directory."}}, ["root"])),
    ("mcp_creator_install", h_install, "📦", _schema(
        "mcp_creator_install",
        "Validate, then link the package into this profile's plugins dir (linked mode: edits to the "
        "build show up after /new) and enable it. Never touches a real installed plugin directory.",
        {"root": {"type": "string"}, "plugin_name": {"type": "string"},
         "enable": {"type": "boolean", "description": "Default true."},
         "replace": {"type": "boolean", "description": "Replace an existing mcp-plugin-creator link of that name."}},
        ["root", "plugin_name"])),
    ("mcp_creator_drift", h_drift, "🌊", _schema(
        "mcp_creator_drift",
        "Compare a server's current tools with the baseline saved at build time. Returns added / "
        "removed / schema-changed tools, the skill lines that mention them, and a verdict: "
        "unchanged, additive (append), or recompile (rewrite affected sections).",
        {"server": _SERVER, "config": _CONFIG, "timeout": {"type": "number"}}, ["server"])),
    ("mcp_creator_review", h_review, "🔁", _schema(
        "mcp_creator_review",
        "Re-examine a plugin built by mcp-plugin-creator: checks the server for tool changes since the "
        "build (drift) and mines usage since the build (calls, errors, error samples). Read-only; returns "
        "refresh_suggested plus reasons. Call with no plugin to list built plugins.",
        {"plugin": {"type": "string", "description": "Plugin name, e.g. 'blender-mcp'."},
         "probe": {"type": "boolean", "description": "Connect to the server for drift (default true)."},
         "timeout": {"type": "number"}}, [])),
]
