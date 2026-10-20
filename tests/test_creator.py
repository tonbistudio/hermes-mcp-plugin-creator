"""End-to-end tests against a throwaway stdio MCP server, in a temporary HERMES_HOME.

Run from the repo root with Hermes' Python (the plugin imports Hermes modules):
    <hermes-venv>/python -m pytest tests -q -o addopts=
"""
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FAKE = REPO / "tests" / "fake_server.py"


@pytest.fixture()
def creator(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    sys.path.insert(0, str(REPO))
    for mod in [m for m in sys.modules if m == "core" or m.startswith("core.")]:
        del sys.modules[mod]
    from core import tools
    yield tools
    sys.path.remove(str(REPO))


def _fake_config():
    return {"command": sys.executable, "args": [str(FAKE)]}


def test_introspect_reads_tools_and_instructions(creator):
    out = json.loads(creator.h_introspect({"server": "fake", "config": _fake_config(), "timeout": 60}))
    assert out["success"], out
    assert sorted(t["name"] for t in out["tools"]) == ["list_notes", "read_note"]
    assert out["instructions"] == "Call list_notes before read_note."
    read = next(t for t in out["tools"] if t["name"] == "read_note")
    assert read["required"] == ["id"]


def test_convert_moves_literal_secrets_to_vars(creator):
    cfg = {"command": "npx", "args": ["-y", "some-mcp@1.2.3"], "env": {"API_KEY": "sk-abcdefghijklmnopqrstuvwx"}}
    out = json.loads(creator.h_convert({"server": "svc", "config": cfg}))
    assert out["portable_entry"]["env"]["API_KEY"] == "${API_KEY}"
    assert "API_KEY" in out["env_to_set"]
    assert "sk-abcdefghijklmnopqrstuvwx" not in json.dumps(out)


SKILL = """---
name: fake-notes
description: "Use when reading notes from the fake-notes test server. Test skill only."
version: 0.0.1
license: MIT
---

# Fake notes

Call `list_notes` before `read_note`.
"""


def _scaffold(creator, tmp_path, build, servers):
    return json.loads(creator.h_scaffold({
        "plugin_name": "fake-notes", "description": "Test package.", "servers": servers,
        "skills": [{"name": "fake-notes", "skill_md": SKILL}], "build": build,
        "output_dir": str(tmp_path / f"out-{build}")}))


def test_scaffold_writes_a_package_that_hermes_validates(creator, tmp_path):
    res = _scaffold(creator, tmp_path, "local", {"notes": {"type": "stdio", "command": "uvx",
                                                           "args": ["fake-notes-mcp==1.0.0"]}})
    assert res["success"], res
    root = Path(res["root"])
    assert (root / "plugin.json").is_file() and (root / "mcp.json").is_file()
    assert (root / "skills" / "fake-notes" / "SKILL.md").is_file()
    val = json.loads(creator.h_validate({"root": str(root)}))
    assert val["success"], val


def test_publish_build_refuses_private_paths(creator, tmp_path):
    res = _scaffold(creator, tmp_path, "publish", {"notes": {"type": "stdio", "command": "node",
                                                             "args": ["C:/Users/someone/notes/server.js"]}})
    assert not res["success"]
    assert any(f["kind"] == "windows_path" for f in res["private_data"])


def test_drift_detects_a_removed_tool(creator, tmp_path):
    from core import drift
    from core.common import data_dir
    cfg = _fake_config()
    snap = json.loads(creator.h_introspect({"server": "fake", "config": cfg, "timeout": 60}))
    assert snap["success"]
    from core import introspect
    drift.save_baseline("fake", introspect.load_snapshot("fake"), None)
    assert json.loads(creator.h_drift({"server": "fake", "config": cfg}))["verdict"] == "unchanged"
    path = data_dir("baselines") / "fake.json"
    base = json.loads(path.read_text(encoding="utf-8"))
    base["tools"]["old_tool"] = {}
    path.write_text(json.dumps(base), encoding="utf-8")
    r = json.loads(creator.h_drift({"server": "fake", "config": cfg}))
    assert r["verdict"] == "recompile" and r["removed"] == ["old_tool"]


def test_approval_gate_asks_only_for_side_effects():
    sys.path.insert(0, str(REPO.parent))
    import importlib
    gate = importlib.import_module(REPO.name)._approval_gate
    sys.path.remove(str(REPO.parent))
    assert gate("mcp_creator_servers", {}) is None
    assert gate("mcp_creator_introspect", {"server": "x"}) is None
    assert gate("mcp_creator_introspect", {"server": "x", "config": {"command": "node"}})["action"] == "approve"
    assert gate("mcp_creator_install", {"root": "/tmp/p", "plugin_name": "p"})["action"] == "approve"
    assert gate("mcp_creator_review", {"plugin": "p", "probe": False}) is None
    assert gate("terminal", {"command": "ls"}) is None


def test_redact_strips_secrets():
    sys.path.insert(0, str(REPO))
    from core.common import redact
    sys.path.remove(str(REPO))
    text = "token ghp_abcdefghijklmnopqrstuvwxyz123456 and Bearer abcdefghijklmnopqrstuvwxyz"
    out = redact(text, paths=False)
    assert "ghp_" not in out and "abcdefghijklmnopqrstuvwxyz" not in out
