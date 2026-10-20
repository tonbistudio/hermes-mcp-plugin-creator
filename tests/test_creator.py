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


# --------------------------------------------------------------------------- review fixes (PR #141915)

def _pkg_args(tmp_path, **kw):
    a = {"plugin_name": "fake-notes", "description": "Test package.",
         "servers": {"notes": {"type": "stdio", "command": "uvx", "args": ["fake-notes-mcp==1.0.0"]}},
         "skills": [{"name": "fake-notes", "skill_md": SKILL}]}
    a.update(kw)
    return a


@pytest.mark.parametrize("build", ["local/../../../../escape", "../x", "publish/..", "", "LOCAL", "x"])
def test_build_outside_enum_is_rejected(creator, tmp_path, build):
    from core.common import data_dir
    res = json.loads(creator.h_scaffold(_pkg_args(tmp_path, build=build or "nope")))
    assert not res["success"] and "build must be one of" in res["errors"][0]
    assert not any(data_dir("builds").parent.parent.rglob("escape*"))


def test_default_root_stays_inside_builds_dir(creator, tmp_path):
    from core.common import data_dir
    res = json.loads(creator.h_scaffold(_pkg_args(tmp_path)))
    assert res["success"], res
    assert Path(res["root"]).resolve().parent == data_dir("builds").resolve()


def test_relative_or_root_output_dir_is_refused(creator, tmp_path):
    for bad in ("relative/dir", str(Path(tmp_path.anchor))):
        res = json.loads(creator.h_scaffold(_pkg_args(tmp_path, output_dir=bad)))
        assert not res["success"], bad


def test_foreign_non_empty_folder_is_never_emptied(creator, tmp_path):
    target = tmp_path / "users-folder"
    target.mkdir()
    (target / "precious.txt").write_text("keep me")
    (target / "sub").mkdir()
    (target / "sub" / "also.txt").write_text("keep me too")
    res = json.loads(creator.h_scaffold(_pkg_args(tmp_path, output_dir=str(target))))
    assert not res["success"] and "refusing to replace" in res["errors"][0]
    assert (target / "precious.txt").read_text() == "keep me"
    assert (target / "sub" / "also.txt").is_file()


def test_own_previous_build_is_replaced(creator, tmp_path):
    target = tmp_path / "pkg"
    first = json.loads(creator.h_scaffold(_pkg_args(tmp_path, output_dir=str(target))))
    assert first["success"], first
    (target / "stale.md").write_text("old")
    second = json.loads(creator.h_scaffold(_pkg_args(tmp_path, output_dir=str(target))))
    assert second["success"], second
    assert not (target / "stale.md").exists() and (target / "plugin.json").is_file()


def test_empty_existing_folder_is_used(creator, tmp_path):
    target = tmp_path / "empty"
    target.mkdir()
    assert json.loads(creator.h_scaffold(_pkg_args(tmp_path, output_dir=str(target))))["success"]


def test_scaffold_works_without_pyyaml(creator, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)  # import yaml -> ImportError, like a stock install
    res = json.loads(creator.h_scaffold(_pkg_args(tmp_path, output_dir=str(tmp_path / "noyaml"))))
    assert res["success"], res


def test_install_rejects_path_like_plugin_names(creator, tmp_path):
    from core import install
    pkg = json.loads(creator.h_scaffold(_pkg_args(tmp_path, output_dir=str(tmp_path / "p"))))["root"]
    for bad in ("../escape", "a/b", "..", "UPPER", ""):
        assert not install.link(pkg, bad)["success"], bad
        assert not install.unlink(bad)["success"], bad


def test_approval_message_names_replacement():
    sys.path.insert(0, str(REPO.parent))
    import importlib
    gate = importlib.import_module(REPO.name)._approval_gate
    sys.path.remove(str(REPO.parent))
    msg = gate("mcp_creator_scaffold", {"output_dir": "/tmp/x"})["message"]
    assert "replaced" in msg and "/tmp/x" in msg


@pytest.mark.parametrize("fm,expected", [
    ('description: "Use when x: y"', "Use when x: y"),
    ("description: 'Use when it''s x'", "Use when it's x"),
    ("description: Use when x # comment", "Use when x"),
    ("description: >\n  Use when x\n  and y\nversion: 1", "Use when x and y"),
    ("version: 1", None),
])
def test_frontmatter_parser(fm, expected):
    sys.path.insert(0, str(REPO))
    from core.scaffold import _frontmatter_value
    sys.path.remove(str(REPO))
    assert _frontmatter_value(f"---\nname: a\n{fm}\n---\n# A\n", "description") == expected
