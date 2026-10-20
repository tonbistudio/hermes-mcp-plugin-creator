# MCP Plugin Creator

Turn any MCP server into a Hermes plugin: an MCP config plus a workflow skill built from evidence,
not guesses.

The model already sees every MCP tool's name, description and schema. What it doesn't see is how
to use the server well: readiness checks, which tools matter for which job, ordering, failure
modes and their fixes, limits, and what not to touch. MCP Plugin Creator collects that knowledge
and packages it as a portable [Agent Plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)
(`plugin.json` + `mcp.json` + `skills/`), the same shape as the official Snyk and TouchDesigner
plugins.

## How it works

The bundled `mcp-plugin-creator` skill drives the procedure; the tools do the deterministic work.
Evidence comes from four sources, strongest first:

1. **Your past use.** Mines this profile's sessions for calls to the server: per-tool counts,
   errors, the fixes that followed and your corrections. Recurring errors become pitfalls.
2. **The server's self-description.** Tools, instructions, prompts and resources from a
   read-only introspection.
3. **The docs.** README, docs site, changelog. Treated as unverified until probed.
4. **A live probe.** Read-only calls, including failure paths (app closed, wrong name, missing
   argument), because many servers report errors as successful results.

Every pitfall in a generated skill cites its source: `(seen in use)`, `(server docs)`,
`(probe)` or `(unverified)`.

## Install

```bash
hermes plugins install mcp-plugin-creator   # once listed in the catalog
# or, from this repo:
hermes plugins install https://github.com/tonbistudio/hermes-mcp-plugin-creator
hermes plugins enable mcp-plugin-creator
```

Start a new session (`/new`) after enabling. Requires Hermes **0.22.0** or later.

## Use

Ask in chat:

- "Create a plugin for the `<server>` MCP server": works for a server already in your
  `mcp_servers` config, or a new one from its docs.
- "Refresh the `<plugin>` plugin": re-checks a built plugin after the server or your usage changed.

Quick checks without the agent:

```text
/mcp-creator                       list configured servers and built plugins
/mcp-creator review <plugin>       server drift + errors in use since the build
/mcp-creator status <server>       usage summary for one server

hermes mcp-creator servers | introspect <server> | usage <server> | drift <server>
hermes mcp-creator review <plugin> | validate <package-dir>
```

### Tools

| Tool | Does |
|---|---|
| `mcp_creator_servers` | List configured MCP servers and existing build baselines |
| `mcp_creator_introspect` | Connect once and read tools, instructions, prompts and resources (never calls tools) |
| `mcp_creator_mine` | Usage evidence from past sessions: counts, errors, transitions, corrections |
| `mcp_creator_convert` | Config entry → portable `mcp.json` entry; literal secrets become `${VAR}` |
| `mcp_creator_scaffold` | Write the package; enforces names and pinning, scans for private data |
| `mcp_creator_validate` | `hermes plugins validate` plus a check for config entries that would override the package |
| `mcp_creator_install` | Link the package into the profile's plugins dir and enable it |
| `mcp_creator_drift` | Compare a server's current tools with the build baseline |
| `mcp_creator_review` | Drift plus errors in use since the build; says whether a refresh is worth doing |

### Local and publish builds

- **local** (default): may contain your paths and project detail. For your own use.
- **publish**: the scaffold refuses any finding from its private-data scan (secrets, emails,
  user paths, your user name), and usage mining strips paths. Use this for anything you share.

The scan matches patterns. It cannot recognise project or client names, hostnames or personal
details written in prose, so read a publish build before you share it.

### Measuring a skill

`skills/mcp-plugin-creator/references/eval.md` describes a with/without-skill comparison: the same
tasks run headless with the plugin's skill and without it, graded from the target's real state. In
our runs the skills rarely changed accuracy; they removed recurring tool errors, cut calls, and
caught one harmful rule before release.

## Disclosure

- **Starts MCP servers.** `introspect`, `drift` and `review` launch the target server's command
  (or connect to its URL), the same command your config already runs. A command the agent
  supplies inline, rather than one already in your config, asks for your approval first.
- **Reads session history.** Usage mining reads this profile's `state.db` read-only. Secrets are
  stripped from everything it returns.
- **Logs MCP calls locally.** A `post_tool_call` hook appends a redacted summary of each
  `mcp__*` call to `plugin-data/mcp-plugin-creator/logs/mcp-calls.jsonl` under your Hermes home,
  for later mining. It never leaves the machine. Turn it off with the `log_calls: false` setting.
- **Writes and links files.** Packages go to the plugin's data dir unless you choose another
  folder (approval required). `mcp_creator_install` creates a directory link (a junction on
  Windows) in the profile's plugins dir and runs `hermes plugins enable`; both ask for approval.
  It never replaces a real plugin directory.
- **No network calls of its own** besides the MCP servers you point it at. No telemetry.
- Generated plugins can contain your config's paths in a local build. Use a publish build to
  share one.

## Development

```bash
# from the repo root, with Hermes' Python and pytest available
python -m pytest tests -q -o addopts=
hermes plugins validate .
```

The tests start a tiny stdio MCP server (`tests/fake_server.py`) in a temporary `HERMES_HOME`.

## License

MIT. See [LICENSE](LICENSE).
