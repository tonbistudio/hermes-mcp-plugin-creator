---
name: mcp-plugin-creator
description: "Use when turning an MCP server into a Hermes plugin (MCP config + workflow skill), migrating a config.yaml mcp_servers entry into a package, or refreshing a generated plugin after the server changed. Evidence-first: server self-description, past sessions, docs, then a verification probe."
version: 0.2.1
author: tonbistudio
license: MIT
---

# MCP Plugin Creator

Builds a portable Agent Plugin (`plugin.json` + `mcp.json` + `skills/`) for one MCP server, the
same shape as the official REA, Snyk and Blender plugins. The tools do the deterministic work; you
do the judgment: what goes in the skill, what is verified, what is private.

**The rule that decides quality:** the model already sees every tool's name, description and
schema. A skill that restates them is worthless. Write what schemas cannot say: readiness,
which tools matter for which job, ordering, failure modes and their fixes, limits, boundaries.

## Modes

| User says | Mode |
|---|---|
| "make a plugin for <server I already use>" | **migrate**: config entry → package, usage-based skill |
| "make a plugin for <new server / docs URL>" | **new**: docs + self-description, lean first build |
| "refresh / update the <x> plugin" | **refresh**: `mcp_creator_drift`, then additive edit or recompile |

## Procedure

1. **Locate the server.** `mcp_creator_servers`. For a new server, build the config (pinned:
   `pkg@x.y.z`, `uvx pkg==x.y.z`, git commit, image digest — never `@latest`) and pass it inline.
2. **Introspect.** `mcp_creator_introspect`. Note `instructions`, prompts (the author's own workflows)
   and "helper" tools whose descriptions say *call before* (`get_hints`, `get_par_info`, `describe_*`)
   — those become critical rules. Slow first starts: raise `timeout`, don't give up.
3. **Mine usage.** `mcp_creator_mine`. Read `experience_level`, error samples, transitions and the
   error-dense sessions. Every recurring error with a known fix becomes a Pitfall; the user's
   follow-up corrections outrank the agent's guesses. `none` → docs-only build.
4. **Read the docs.** `web_extract` the README / docs site / changelog for setup, prerequisites,
   auth and version. Docs claims are *unverified* until step 6.
5. **Plan the package** (see `references/structure-rules.md`): one skill per distinct job (most
   servers: one); add `references/` only when a section would bloat SKILL.md; add `scripts/` only
   for deterministic work the agent keeps getting wrong. State the plan to the user in 3–6 lines.
6. **Verify claims.** Delegate a probe to a fresh subagent with the server available: read-only
   calls (list/get/inspect/status) against a safe target, one per non-obvious claim. Never mutate
   user data; use a scratch target for anything that writes. Mark what it could not confirm.
   Probe the failure paths too, not just the happy path: app closed, a deliberate error, a
   missing name, a missing required argument. Many servers return failures as successful
   results with error text; the skill must say how to recognize them.
7. **Write the skill** from `references/skill-template.md`. Each Pitfall cites its source:
   `(seen in use)`, `(server docs)`, `(probe)`. Unverified docs claims get `(unverified)`.
   Use bare tool names (`get_objects_summary`), never `mcp__…` prefixes — the prefix varies by
   install; tell the agent to discover tools via tool search/schemas.
8. **Convert + scaffold.** `mcp_creator_convert` for an existing entry (secrets/paths → `${VAR}`), then
   `mcp_creator_scaffold` with `build: "local"` and `baseline_server` set. Propose a Hermes extension
   block when the server needs a desktop app (`app`/`requires`) or is hosted (`trust: untrusted`).
9. **Validate.** `mcp_creator_validate`. Fix every error. Read `conflicts`: a same-named
   `mcp_servers` entry overrides the package — tell the user the exact disable command; never
   edit their config yourself.
10. **Install (with consent).** `mcp_creator_install` links the build and enables it; Hermes asks
    the user to approve it. Tell the user to
    start a new session; then check the tools and skill appear.
11. **Evaluate** (see `references/eval.md`) when the user wants proof: 3–5 realistic tasks,
    with vs without the skill, compare errors and turns. Cut content that did not help.
12. **Publish build (optional).** Rebuild with `build: "publish"`, `mcp_creator_mine` with
    `redact_paths: true`, generalized wording, a disclosure paragraph. The scaffold refuses any
    private-data finding. Follow `references/publish-checklist.md`.

## Refresh

`mcp_creator_drift` → verdict:
- `unchanged`: re-run `mcp_creator_mine` for new sessions; add new pitfalls only.
- `additive`: new tools — add them to Workflows/Quick reference if they matter.
- `recompile`: removed or changed tools are named in `skill_lines_to_review`; rewrite those
  sections, re-probe, rescaffold with a bumped version (new baseline).

Edit the build in place (it is linked); bump `version` in `plugin.json` for every content change.

## Pitfalls

- Plugin skills are read-only to the self-improvement loop; corrections go into the creator build,
  then `/new`. Machine-specific notes belong in a small local companion skill, not the package.
- `tool success` ≠ `task success`. Every Workflow ends with a readback step.
- A Hermes `tools: include:` filter in config does not carry into the package; if the user relied
  on it, the skill must say which tools to use and which to avoid.
- Literal empty secrets (`"API_KEY": ""`) break forwarding; always `"${API_KEY}"`.
- Long server names eat the 64-char tool-name budget; keep them ≤ 12 chars.
- Never introspect or probe a server whose tools run arbitrary code against the user's real
  project without saying so first.
- **Credential-gated servers** (OAuth, own developer app): you usually can't list tools before
  the user logs in. Build from docs, ship the package linked but disabled, mark tool names
  `(unverified)`, and plan a refresh right after login. Never guess the tool names in the skill.
- **Read what the server ships:** `instructions`, prompts and resources (some docs servers
  expose a whole skill as a resource). These are often richer than usage history.
- Portable `mcp.json` has no timeout field (stdio: type/command/args/env/cwd only), and Hermes
  waits ~60 s for a server to connect. Servers that block startup on an interactive login need a
  "log in once outside Hermes first" step in setup.md.
- If `web_extract` is blocked on the docs site, read it with the browser tool.
- **A same-named `mcp_servers` entry blocks the plugin's server even when `enabled: false`.**
  Hermes logs `Portable MCP server '<x>' conflicts with native config; skipping`. The fix is
  `hermes config unset mcp_servers.<x>`. In evals, the with-skill arm must remove the entry, and
  every run must show at least one server call, or the arm is invalid.
- **Local builds keep real paths.** Use `${VAR}` placeholders for secrets, not for paths, in a
  `build: "local"` package. A path variable adds a setup step and the server fails to connect
  until it's set. Generalize paths only for a publish build.

## Verification

- `mcp_creator_validate` returns `success: true` with no loader diagnostics.
- After `/new`, `skills_list` shows `<plugin>:<skill>` and the server's tools are callable.
- The skill contains at least one Pitfall or rule that is *not* derivable from the tool schemas.
