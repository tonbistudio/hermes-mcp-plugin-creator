"""Deterministic MCP server introspection: initialize, then list tools / prompts / resources.

Runs a short-lived client (never the agent's live connection). Produces a JSON snapshot plus a
fingerprint of the tool surface so later refreshes can detect drift.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from .common import data_dir, safe_filename

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _secret(name: str) -> str | None:
    """Resolve a ${VAR} through Hermes' profile-aware secret scope (catalog rule 11)."""
    try:
        from agent.secret_scope import get_secret
        return get_secret(name)
    except ImportError:  # outside Hermes (tests)
        return os.environ.get(name)


def _interpolate(value: str, plugin_root: str | None = None) -> str | None:
    """Resolve ${VAR} from the environment; ${PLUGIN_ROOT} from the package. Unset -> None."""
    missing = False

    def sub(m: re.Match) -> str:
        nonlocal missing
        name = m.group(1)
        if name == "PLUGIN_ROOT" and plugin_root:
            return plugin_root
        val = _secret(name)
        if not val:
            missing = True
            return ""
        return val

    out = _VAR_RE.sub(sub, value)
    return None if missing and _VAR_RE.fullmatch(value) else out


def _dump(model: Any) -> Any:
    if model is None:
        return None
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json", by_alias=True, exclude_none=True)
    return model


def fingerprint(tools: list[dict]) -> str:
    """Stable hash of tool names + input schemas (descriptions excluded: wording churn != drift)."""
    canon = sorted(
        (t.get("name", ""), json.dumps(t.get("inputSchema", {}), sort_keys=True)) for t in tools
    )
    return hashlib.sha256(json.dumps(canon).encode()).hexdigest()[:16]


async def _collect(config: dict, timeout: float, plugin_root: str | None) -> dict:
    from mcp.client import Client

    if config.get("url"):
        url = config["url"]
        headers = {}
        for k, v in (config.get("headers") or {}).items():
            resolved = _interpolate(str(v), plugin_root)
            if resolved is not None:
                headers[k] = resolved
        if headers:
            import httpx2  # bundled with the 2.0 SDK
            from mcp.client.streamable_http import streamable_http_client
            http = httpx2.AsyncClient(headers=headers, timeout=timeout)
            transport: Any = streamable_http_client(url, http_client=http)
        else:
            transport = url
    else:
        from mcp.client.stdio import StdioServerParameters, stdio_client
        command = config.get("command")
        if not command:
            raise ValueError("server config needs either 'url' or 'command'")
        env: dict[str, str] = {}
        for k, v in (config.get("env") or {}).items():
            resolved = _interpolate(str(v), plugin_root)
            if resolved is not None:
                env[k] = resolved
        args = [(_interpolate(str(a), plugin_root) or "") for a in (config.get("args") or [])]
        resolved_cmd = shutil.which(command) or command
        cwd = config.get("cwd")
        cwd = _interpolate(str(cwd), plugin_root) if cwd else None
        params = StdioServerParameters(command=resolved_cmd, args=args, env=env or None, cwd=cwd,
                                       encoding_error_handler="replace")
        errlog = open(data_dir("logs") / "introspect-stderr.log", "a", encoding="utf-8", errors="replace")
        errlog.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} {command} {args}\n")
        errlog.flush()
        transport = stdio_client(params, errlog=errlog)

    out: dict[str, Any] = {}
    async with Client(transport, read_timeout_seconds=timeout) as client:
        info = client.server_info
        out["server_info"] = _dump(info)
        out["instructions"] = client.instructions
        caps = client.server_capabilities
        out["capabilities"] = _dump(caps)

        async def paged(fn, attr):
            items, cursor, pages = [], None, 0
            while True:
                res = await fn(cursor=cursor)
                items.extend(_dump(x) for x in getattr(res, attr, []) or [])
                cursor = getattr(res, "next_cursor", None) or getattr(res, "nextCursor", None)
                pages += 1
                if not cursor or pages >= 50:
                    return items

        out["tools"] = await paged(client.list_tools, "tools")
        caps_d = out["capabilities"] or {}
        out["prompts"], out["resources"] = [], []
        if caps_d.get("prompts") is not None:
            try:
                out["prompts"] = await paged(client.list_prompts, "prompts")
            except Exception as exc:  # optional surface
                out["prompts_error"] = str(exc)[:300]
        if caps_d.get("resources") is not None:
            try:
                out["resources"] = await paged(client.list_resources, "resources")
            except Exception as exc:
                out["resources_error"] = str(exc)[:300]
    return out


def introspect(server_name: str, config: dict, *, timeout: float = 120.0,
               plugin_root: str | None = None, save: bool = True) -> dict:
    """Connect once, list everything, return (and optionally persist) a snapshot."""
    started = time.time()

    async def run():
        return await asyncio.wait_for(_collect(config, timeout, plugin_root), timeout=timeout + 15)

    try:
        result = _run(run())
    except BaseException as exc:  # noqa: BLE001 - report anything (incl. ExceptionGroup)
        detail = _flatten_exc(exc)
        return {"success": False, "server": server_name, "error": detail,
                "hint": _hint(detail, config)}
    tools = result.get("tools", [])
    snap = {
        "success": True,
        "server": server_name,
        "taken_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_s": round(time.time() - started, 1),
        "transport": "streamable-http" if config.get("url") else "stdio",
        "fingerprint": fingerprint(tools),
        "tool_count": len(tools),
        **result,
    }
    if save:
        path = data_dir("snapshots") / f"{safe_filename(server_name)}.json"
        path.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
        snap["snapshot_path"] = str(path)
    return snap


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Called from inside a running loop (async tool host): run in a worker thread.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _flatten_exc(exc: BaseException) -> str:
    subs = getattr(exc, "exceptions", None)
    if subs:
        return "; ".join(_flatten_exc(e) for e in subs)[:1200]
    return f"{type(exc).__name__}: {exc}"[:1200]


def _hint(detail: str, config: dict) -> str:
    d = detail.lower()
    if "timeout" in d or "timed out" in d:
        return ("Server did not answer in time. Slow first starts (npx/uvx downloads, JVM/Ghidra) "
                "are common: retry with a larger timeout.")
    if "filenotfound" in d or "no such file" in d or "winerror 2" in d:
        return f"Command {config.get('command')!r} not found on PATH."
    if "connect" in d and config.get("url"):
        return "Nothing is listening at that URL. Is the host app (and its MCP bridge) running?"
    if "401" in d or "403" in d or "unauthor" in d:
        return "Server needs authentication (OAuth or a header token)."
    return "See plugin-data/mcp-plugin-creator/logs/introspect-stderr.log for the server's stderr."


def summarize(snap: dict, *, max_desc: int = 220) -> dict:
    """Compact view of a snapshot for the model: names, short descriptions, required args."""
    if not snap.get("success"):
        return snap
    tools = []
    for t in snap.get("tools", []):
        schema = t.get("inputSchema") or {}
        props = schema.get("properties") or {}
        tools.append({
            "name": t.get("name"),
            "description": (t.get("description") or "").strip()[:max_desc],
            "required": schema.get("required") or [],
            "params": sorted(props)[:25],
            "annotations": t.get("annotations"),
        })
    return {
        "success": True,
        "server": snap["server"],
        "server_info": snap.get("server_info"),
        "instructions": snap.get("instructions"),
        "fingerprint": snap["fingerprint"],
        "tool_count": snap["tool_count"],
        "tools": tools,
        "prompts": [{"name": p.get("name"), "description": (p.get("description") or "")[:max_desc],
                     "arguments": [a.get("name") for a in p.get("arguments") or []]}
                    for p in snap.get("prompts", [])],
        "resources": [{"uri": r.get("uri"), "name": r.get("name"),
                       "description": (r.get("description") or "")[:max_desc]}
                      for r in snap.get("resources", [])][:50],
        "snapshot_path": snap.get("snapshot_path"),
    }


def load_snapshot(server_name: str) -> dict | None:
    path = data_dir("snapshots") / f"{safe_filename(server_name)}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


if __name__ == "__main__":  # manual: python -m core.introspect name '{"command":...}'
    cfg = json.loads(sys.argv[2])
    print(json.dumps(summarize(introspect(sys.argv[1], cfg)), indent=2))
