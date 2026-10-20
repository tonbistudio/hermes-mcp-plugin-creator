# Evaluating a generated skill

Goal: show the skill measurably helps, and cut what doesn't.

1. Write 3–5 realistic tasks from the mined sessions (the user's actual jobs). At least one
   should hit a known pitfall. Tasks must be read-only or use a scratch target.
2. For each task run two fresh subagents (`delegate_task`), identical goal and toolsets:
   - **A (baseline):** told not to load the plugin skill.
   - **B (skill):** told to load `<plugin>:<skill>` first.
3. Ask each to report: success (yes/no + evidence), tool calls made, tool errors hit, retries.
   Verify success claims yourself from their evidence — child summaries are self-reports.
4. Record a table: task | A ok/errors/calls | B ok/errors/calls | notes.
5. Keep content that B used where A failed or retried; delete sections nobody needed.
6. Save the table to `plugin-data/mcp-plugin-creator/evidence/<plugin>-eval.md` (outside the package).

Report honestly when the skill made no difference; that usually means it restates schemas.

## Lessons from real evals

- **Grade from the target's state, not the agent's answer.** Read back what actually exists
  after each run (objects, files, records). Answers can be right while side effects are wrong.
- **Check for collateral damage across runs.** Give each run an isolated target, then audit the
  *other* runs' targets afterwards. A "clean up before re-running" rule in a generated skill
  deleted another scene's objects in the Blender eval, because names were global.
- **Include failure-path tasks** (app closed, a deliberate exception, a disabled feature).
  Happy-path tasks rarely exercise the pitfalls, so they under-measure the skill.
- A capable baseline is normal. The skill's value then shows in consistency (readiness check,
  required args) and safety (asking before side effects), not in success rate.
- Headless runs: `hermes chat -q "<task>" --oneshot -Q --source <tag>` (+ `-s <plugin:skill>` for
  the skill arm). Disable the generated plugin for the baseline arm and re-enable it after.
  Stats come from state.db by session source.
- **A same-named config entry blocks the plugin's server, even with `enabled: false`.** For the
  skill arm remove it (`hermes config unset mcp_servers.<x>`); when switching back, write the
  whole entry, not just `enabled`. Treat any run with zero server calls as invalid, not failed.
- Give both arms the same server descriptions. If you fix a server's own descriptions, the skill's
  measured value drops; that is a better outcome than a skill that works around them.
