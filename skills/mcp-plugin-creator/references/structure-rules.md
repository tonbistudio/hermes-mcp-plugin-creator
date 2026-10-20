# When to add files beyond SKILL.md

Default: one SKILL.md. Add only on evidence.

| Add | When | Catalog example |
|---|---|---|
| `references/setup.md` | Install/prereqs exceed a few lines (add-on, desktop app, login, ports, env vars) | Blender, NVIDIA |
| `references/tools.md` | > ~15 tools, or args with real gotchas. SKILL.md keeps the top ~10 | Snyk |
| `references/<topic>.md` | Areas needed only for some tasks that would push SKILL.md past ~200 lines | TouchDesigner (GLSL, audio, MIDI) |
| `references/errors.md` | Mined sessions show more recurring errors-with-fixes than fit in Pitfalls | REA (Ghidra timeouts) |
| `scripts/<x>.py` | Deterministic work the agent redoes or gets wrong (parse output, map IDs, readiness probe). Stdlib only, small, run once during verification, invoked with `python -I` | REA `core_mappings.py` |
| `templates/` | Structured input the agent keeps rebuilding | rare |
| More skills | Tool groups serve separate jobs with little overlap; add a short entry skill that routes | REA, Kuma |

## Never

- A reference that only repeats tool schemas the model already sees.
- A script with no test call.
- Topic/error references on a first, docs-only build — they come from a later refresh with real usage.

## Depth by experience level (`mcp_creator_mine`)

| level | Build |
|---|---|
| none | SKILL.md (lean) + setup.md if needed. Many `(unverified)` tags; plan a refresh after ~5 sessions |
| light | SKILL.md with the observed pitfalls |
| moderate | + tools.md / errors.md when warranted |
| heavy | consider topic references and a second skill |

## Server naming in mcp.json

Short (≤ 12 chars, e.g. `sn`, `td`, `blender`). When migrating, keeping the user's existing
config name means their old `mcp_servers.<name>` entry will override the package until disabled —
say so. Renaming avoids the clash but changes the tool prefix the user may be used to.
