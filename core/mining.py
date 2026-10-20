"""Mine past Hermes sessions for real usage of one MCP server.

Reads state.db read-only. Calls are found two ways:
  * direct:   assistant tool_calls whose function name is ``mcp__<server>__<tool>``
  * wrapped:  the deferred-tool wrapper ``tool_call`` with ``{"calls": [{"name": "mcp__..."}]}``
Tool result rows carry the inner MCP name and the outer tool_call_id, so results join by id.
Also merges the live JSONL log written by this plugin's post_tool_call hook.

Output is a compact, redacted evidence bundle: per-tool stats, error clusters, call sequences and
the user messages that followed failures (where corrections usually live).
"""
from __future__ import annotations

import collections
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from .common import data_dir, hermes_home, redact, safe_filename, sanitize_component, truncate

_UNTRUSTED_WRAP = re.compile(
    r"^<untrusted_tool_result[^>]*>\s*(?:The following content.*?can issue instructions\.\s*)?", re.S)
_ERROR_HINT = re.compile(r'("error"\s*:|\berror\b|exception|traceback|failed|not found|timed? ?out|'
                         r'refused|invalid|denied|missing required)', re.I)


def _strip_wrapper(content: str) -> str:
    content = content or ""
    content = _UNTRUSTED_WRAP.sub("", content, count=1)
    return content.replace("</untrusted_tool_result>", "").strip()


def _looks_error(content: str) -> bool:
    head = content[:600]
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            if parsed.get("error") or parsed.get("success") is False or parsed.get("isError"):
                return True
            return False
    except Exception:
        pass
    return bool(_ERROR_HINT.search(head))


def _prefixes(server: str) -> list[str]:
    s = sanitize_component(server)
    return sorted({f"mcp__{server}__", f"mcp__{s}__"})


def _match_tool(name: str, prefixes: list[str]) -> str | None:
    for p in prefixes:
        if name.startswith(p):
            return name[len(p):]
    # portable packages may namespace the server: mcp__agent_plugin_<x>__<server>__<tool>
    for p in prefixes:
        inner = "__" + p[len("mcp__"):]
        if name.startswith("mcp__") and inner in name:
            return name.split(inner, 1)[1]
    return None


def _db_path() -> Path:
    return hermes_home() / "state.db"


def _iter_calls(tool_calls_json: str) -> Iterable[tuple[str, str, dict]]:
    """Yield (call_id, function_name, arguments) for each call, unwrapping ``tool_call``."""
    try:
        calls = json.loads(tool_calls_json)
    except Exception:
        return
    for call in calls or []:
        fn = call.get("function") or {}
        name = fn.get("name") or ""
        cid = call.get("id") or call.get("call_id") or ""
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {"_raw": truncate(fn.get("arguments"), 300)}
        if name == "tool_call" and isinstance(args, dict):
            inner = args.get("calls") or []
            for i, sub in enumerate(inner):
                sub_id = cid if len(inner) == 1 else f"{cid}#{i}"
                yield sub_id, sub.get("name") or "", sub.get("arguments") or {}
        else:
            yield cid, name, args if isinstance(args, dict) else {"_value": args}


def mine(server: str, *, max_sessions: int = 40, since_days: float | None = None,
         redact_paths: bool = False, save: bool = True) -> dict:
    """Collect usage evidence for *server*. ``redact_paths`` is for publishable builds."""
    prefixes = _prefixes(server)
    db = _db_path()
    if not db.is_file():
        return {"success": False, "error": f"session store not found at {db}"}
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    like = [f"%{p}%" for p in prefixes]
    where = " or ".join("tool_calls like ?" for _ in like)
    params: list[Any] = list(like)
    since_sql = ""
    if since_days:
        since_sql = " and timestamp >= ?"
        params.append(time.time() - since_days * 86400)
    sids = [r[0] for r in con.execute(
        f"select session_id, max(timestamp) t from messages where ({where}){since_sql} "
        f"group by session_id order by t desc limit ?", (*params, max_sessions))]

    def clean(text: Any, limit: int) -> str:
        return truncate(redact(text if isinstance(text, str) else json.dumps(text, default=str),
                               paths=redact_paths), limit)

    per_tool: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    errors: dict[str, list[dict]] = collections.defaultdict(list)
    sessions_out = []
    for sid in sids:
        meta = con.execute("select title, source, started_at from sessions where id=?", (sid,)).fetchone()
        rows = con.execute(
            "select id, role, content, tool_call_id, tool_calls, tool_name, timestamp from messages "
            # include compacted/inactive rows: they are still real history of what happened
            "where session_id=? order by id", (sid,)).fetchall()
        pending: dict[str, tuple[str, dict]] = {}
        events: list[dict] = []
        last_error_idx: int | None = None
        for row in rows:
            role = row["role"]
            if role == "assistant" and row["tool_calls"]:
                for cid, fname, args in _iter_calls(row["tool_calls"]):
                    tool = _match_tool(fname, prefixes)
                    if tool:
                        pending[cid] = (tool, args)
            elif role == "tool":
                cid = row["tool_call_id"] or ""
                hit = pending.pop(cid, None)
                if hit is None:
                    tool = _match_tool(row["tool_name"] or "", prefixes)
                    if not tool:
                        continue
                    hit = (tool, {})
                tool, args = hit
                body = _strip_wrapper(row["content"] or "")
                is_err = _looks_error(body)
                per_tool[tool]["calls"] += 1
                per_tool[tool]["errors" if is_err else "ok"] += 1
                ev = {"tool": tool, "args": clean(args, 400), "ok": not is_err,
                      "result": clean(body, 700 if is_err else 240)}
                events.append(ev)
                if is_err:
                    last_error_idx = len(events) - 1
                    errors[tool].append({"session": sid, "args": ev["args"], "error": clean(body, 500)})
            elif role == "user" and events:
                text = (row["content"] or "").strip()
                if text and not text.startswith("[System") and len(text) < 4000:
                    tag = "user_after_error" if last_error_idx is not None else "user"
                    events.append({"user": clean(text, 500), "kind": tag})
                    last_error_idx = None
            elif role == "assistant" and row["content"] and last_error_idx is not None:
                # the agent's own diagnosis right after a failure
                events.append({"assistant_after_error": clean(row["content"], 400)})
        if events:
            sessions_out.append({
                "session": sid,
                "title": clean((meta["title"] if meta else None) or "", 120),
                "source": meta["source"] if meta else None,
                "calls": sum(1 for e in events if "tool" in e),
                "errors": sum(1 for e in events if e.get("ok") is False),
                "events": events[:80],
            })
    con.close()

    live = _read_live_log(server, prefixes, clean,
                          since_ts=(time.time() - since_days * 86400) if since_days else None)
    for ev in live:
        per_tool[ev["tool"]]["calls"] += 1
        per_tool[ev["tool"]]["errors" if not ev["ok"] else "ok"] += 1

    sequences = collections.Counter()
    for s in sessions_out:
        seq = [e["tool"] for e in s["events"] if "tool" in e]
        for a, b in zip(seq, seq[1:]):
            if a != b:
                sequences[f"{a} -> {b}"] += 1

    bundle = {
        "success": True,
        "server": server,
        "prefixes": prefixes,
        "sessions_scanned": len(sids),
        "sessions_with_calls": len(sessions_out),
        "total_calls": sum(c["calls"] for c in per_tool.values()),
        "total_errors": sum(c["errors"] for c in per_tool.values()),
        "per_tool": {t: dict(c) for t, c in sorted(per_tool.items(), key=lambda kv: -kv[1]["calls"])},
        "common_transitions": sequences.most_common(20),
        "error_samples": {t: v[:6] for t, v in errors.items()},
        "live_log_events": len(live),
        "live_log_sessions": len({e.get("session") for e in live if e.get("session")}),
        "sessions": sessions_out,
        "experience_level": _level(sum(c["calls"] for c in per_tool.values()), len(sessions_out)),
    }
    if save:
        path = data_dir("evidence") / f"{safe_filename(server)}-usage.json"
        path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
        bundle["evidence_path"] = str(path)
    return bundle


def _level(calls: int, sessions: int) -> str:
    if calls == 0:
        return "none"
    if calls < 15 or sessions < 2:
        return "light"
    if calls < 80 or sessions < 5:
        return "moderate"
    return "heavy"


def summarize(bundle: dict, *, max_sessions: int = 8, max_events: int = 30) -> dict:
    """What the model actually reads: stats + the most error-dense sessions, trimmed."""
    if not bundle.get("success"):
        return bundle
    ranked = sorted(bundle["sessions"], key=lambda s: (-s["errors"], -s["calls"]))[:max_sessions]
    return {
        **{k: bundle[k] for k in ("server", "sessions_with_calls", "total_calls", "total_errors",
                                  "per_tool", "common_transitions", "error_samples",
                                  "experience_level", "live_log_events")},
        "sessions": [{**s, "events": s["events"][:max_events]} for s in ranked],
        "evidence_path": bundle.get("evidence_path"),
    }


# --------------------------------------------------------------------------- live log (hook)

def live_log_path() -> Path:
    return data_dir("logs") / "mcp-calls.jsonl"


def record_call(tool_name: str, args: Any, result: Any, status: str | None, error_type: str | None,
                error_message: str | None, session_id: str | None, duration_ms: int | None) -> None:
    """Append one MCP call to the live log (called from the post_tool_call hook)."""
    if not tool_name.startswith("mcp__"):
        return
    body = result if isinstance(result, str) else json.dumps(result, default=str)
    stripped = _strip_wrapper(body)
    is_err = (status not in (None, "ok", "success")) or _looks_error(stripped)
    entry = {
        "ts": time.time(), "tool_name": tool_name, "session": session_id, "duration_ms": duration_ms,
        "ok": not is_err, "status": status, "error_type": error_type,
        "args": truncate(redact(json.dumps(args, default=str), paths=False), 600),
        "result": truncate(redact(stripped, paths=False), 900 if is_err else 200),
        "error_message": truncate(error_message or "", 400),
    }
    path = live_log_path()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    # keep the log bounded
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            lines = path.read_text(encoding="utf-8").splitlines()[-20000:]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def _read_live_log(server: str, prefixes: list[str], clean, since_ts: float | None = None) -> list[dict]:
    path = live_log_path()
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if since_ts and (e.get("ts") or 0) < since_ts:
            continue
        tool = _match_tool(e.get("tool_name", ""), prefixes)
        if tool:
            out.append({"tool": tool, "ok": e.get("ok", True), "session": e.get("session"), "args": clean(e.get("args", ""), 400),
                        "result": clean(e.get("result", ""), 500)})
    return out
