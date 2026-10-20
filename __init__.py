"""MCP Plugin Creator — build Hermes plugins (MCP config + workflow skill) from an MCP server.

Deterministic work lives in tools (introspect, mine, convert, scaffold, validate, install, drift);
the bundled ``mcp-plugin-creator`` skill drives synthesis in the agent turn. ``register()`` is idempotent
(plugins can be reloaded).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
_HERE = Path(__file__).resolve().parent


def register(ctx) -> None:
    from .core import mining, tools

    for name, handler, emoji, schema in tools.TOOLS:
        ctx.register_tool(name=name, toolset=tools.TOOLSET, schema=schema, handler=handler,
                          description=schema["description"], emoji=emoji)

    skill_md = _HERE / "skills" / "mcp-plugin-creator" / "SKILL.md"
    if skill_md.is_file() and hasattr(ctx, "register_skill"):
        ctx.register_skill("mcp-plugin-creator", skill_md,
                           description="Turn an MCP server into a Hermes plugin (config + skill).")

    def _log_mcp_call(tool_name, args=None, result=None, **kwargs):
        if not str(tool_name or "").startswith("mcp__"):
            return
        try:
            if ctx.get_config("log_calls", True) is False:
                return
            mining.record_call(tool_name, args, result, kwargs.get("status"), kwargs.get("error_type"),
                               kwargs.get("error_message"), kwargs.get("session_id"),
                               kwargs.get("duration_ms"))
        except Exception as exc:  # observer: never break the turn
            logger.debug("mcp-plugin-creator log hook failed: %s", exc)

    ctx.register_hook("post_tool_call", _log_mcp_call)
    ctx.register_hook("pre_tool_call", _approval_gate)

    def _slash(raw_args: str) -> str:
        return _slash_command(raw_args or "")

    ctx.register_command("mcp-creator", _slash, description="MCP Plugin Creator: build/refresh a plugin from an MCP server",
                         args_hint="[servers | review <plugin> | status <server>]")

    ctx.register_cli_command(name="mcp-creator", help="MCP Plugin Creator: inspect servers, check drift, list builds",
                             description="Build Hermes plugins from MCP servers.",
                             setup_fn=_setup_cli, handler_fn=_handle_cli)


# --------------------------------------------------------------------------- approvals

def _approval_gate(tool_name=None, args=None, **_kwargs):
    """Route the creator's side effects through Hermes' approval system (catalog rule 12).

    Asks the human before: launching a server command the agent supplied inline (not one the user
    already configured), connecting to a built package's servers, writing a package outside the
    plugin's data dir, or linking + enabling a plugin. Read-only calls pass through.
    """
    if not str(tool_name or "").startswith("mcp_creator_"):
        return None
    args = args if isinstance(args, dict) else {}
    reason = None
    inline = args.get("config")
    if tool_name in ("mcp_creator_introspect", "mcp_creator_drift") and inline:
        launch = inline if isinstance(inline, str) else json.dumps(inline)
        reason = f"connect to an MCP server from an inline config (launches: {launch[:200]})"
    elif tool_name == "mcp_creator_review" and args.get("plugin") and args.get("probe", True) is not False:
        reason = f"start the MCP server(s) of the built plugin {args['plugin']!r} to check for drift"
    elif tool_name == "mcp_creator_scaffold" and args.get("output_dir"):
        reason = f"write a plugin package to {args['output_dir']}"
    elif tool_name == "mcp_creator_install":
        reason = (f"link {args.get('root')} into this profile's plugins dir as "
                  f"{args.get('plugin_name')!r}" + (" and enable it" if args.get("enable", True) else ""))
    if reason:
        return {"action": "approve", "message": f"MCP Plugin Creator wants to {reason}.",
                "rule_key": f"mcp-plugin-creator:{tool_name}"}
    return None


# --------------------------------------------------------------------------- /mcp-creator

def _slash_command(raw: str) -> str:
    from .core import drift, mining, scaffold
    parts = raw.split()
    if not parts or parts[0] in ("servers", "list", "help"):
        servers = scaffold.read_configured_servers()
        lines = ["**MCP Plugin Creator** — configured servers:"]
        for name, cfg in servers.items():
            state = "" if cfg.get("enabled", True) else " (disabled)"
            lines.append(f"- `{name}`{state}: {cfg.get('url') or cfg.get('command')}")
        base = drift.list_baselines()
        if base:
            lines.append("\nGenerated builds:")
            lines += [f"- `{b['server']}` → {b['package_root']} (baseline {b['fingerprint']})" for b in base]
        lines.append("\nAsk: *create a plugin for `<server>`* (loads the `mcp-plugin-creator:mcp-plugin-creator` skill), "
                     "or `/mcp-creator status <server>`.")
        return "\n".join(lines)
    if parts[0] == "review":
        from .core import review
        if len(parts) < 2:
            built = review.list_built()
            return "Usage: `/mcp-creator review <plugin>`. Built plugins: " + (", ".join(built) or "none")
        return review.format_report(review.review(parts[1]))
    if parts[0] == "status" and len(parts) > 1:
        b = mining.mine(parts[1], save=False)
        if not b.get("success"):
            return b.get("error", "failed")
        top = ", ".join(f"{t} ({c['calls']})" for t, c in list(b["per_tool"].items())[:6]) or "none"
        return (f"`{parts[1]}`: {b['total_calls']} calls, {b['total_errors']} errors across "
                f"{b['sessions_with_calls']} sessions — experience: **{b['experience_level']}**.\nTop tools: {top}")
    return (f"To build a plugin for `{parts[0]}`, ask in chat: \"create a plugin for {parts[0]}\". "
            "The agent will load the mcp-plugin-creator skill and run the procedure with you.")


# --------------------------------------------------------------------------- hermes mcp-creator

def _setup_cli(parser) -> None:
    sub = parser.add_subparsers(dest="creator_cmd")
    sub.add_parser("servers", help="List configured MCP servers and generated builds")
    p = sub.add_parser("introspect", help="Introspect a configured server")
    p.add_argument("server")
    p.add_argument("--timeout", type=float, default=120)
    p = sub.add_parser("usage", help="Summarize past usage of a server")
    p.add_argument("server")
    p = sub.add_parser("drift", help="Compare a server against its build baseline")
    p.add_argument("server")
    p = sub.add_parser("review", help="Re-examine a built plugin: server drift + usage since the build")
    p.add_argument("plugin")
    p = sub.add_parser("validate", help="Validate a package directory")
    p.add_argument("root")


def _handle_cli(args) -> int:
    from .core import tools
    cmd = getattr(args, "creator_cmd", None) or "servers"
    if cmd == "servers":
        out = tools.h_servers({})
    elif cmd == "introspect":
        out = tools.h_introspect({"server": args.server, "timeout": args.timeout})
    elif cmd == "usage":
        out = tools.h_mine({"server": args.server, "detail_sessions": 0})
    elif cmd == "drift":
        out = tools.h_drift({"server": args.server})
    elif cmd == "review":
        from .core import review
        r = review.review(args.plugin)
        print(review.format_report(r))
        return 0 if r.get("success") else 1
    elif cmd == "validate":
        out = tools.h_validate({"root": args.root})
    else:
        print("unknown command")
        return 2
    data = json.loads(out)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0 if data.get("success", True) else 1
