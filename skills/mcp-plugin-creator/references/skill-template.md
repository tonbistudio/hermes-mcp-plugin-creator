# Generated skill template

Target 50–200 lines. Sections in this order; drop a section only when it would be empty.

```markdown
---
name: <kebab-name, equals the directory>
description: "Use when <trigger: the jobs this server does>. <one-line behavior: the core rule>."
version: 0.1.0
license: MIT
metadata:
  sources: "usage:<N calls/M sessions>, server:<name vX>, docs:<url>"
---

# <Product> through MCP

One paragraph: what the server controls, what it does NOT do (install the app, open the project,
authenticate), and the readiness layers (installed → MCP connected → app/account ready).

## Critical rules            <- only if the evidence produced hard rules (call X before Y, stop on Z)

1. **<rule>.** <why, in one line>. (seen in use | server docs | probe)

## Before working

1. Prerequisites that must be true (app running, add-on enabled, port, login).
2. The cheapest read-only call that proves readiness, and what its failure means.
3. When to ask the user before mutating anything.

## Workflows

**"<user intent>"**
1. tool → what to read from it
2. tool → ...
3. Readback: the call that proves the change happened.

(2–5 workflows, the jobs this user actually does; order matters.)

## Quick reference

| Goal | Tool | Key args / gotcha |
|---|---|---|
(only the ~10 tools that matter; the full list lives in the schemas or references/tools.md)

## Pitfalls

- <symptom or error text> → <cause> → <fix>. (seen in use)
- Slow first start / timeouts / limits / quotas.
- Arguments that look optional but are required, or formats (absolute paths, units).

## Security and boundaries

What the tools can execute or send off the machine; what never to do without consent.

## Verification

- A command or call whose output proves the plugin works (version, a known-good read).
```

## Writing rules

- Imperative, specific, short. No marketing. No restating schemas.
- Bare tool names. Never `mcp__server__tool`.
- Every Pitfall has a source tag. Unconfirmed docs claims are tagged `(unverified)`.
- Personal paths/projects only in a `local` build, and preferably in a companion skill.
- If a reference file exists, SKILL.md links it at the point of need ("for X, read
  `references/x.md`"), and SKILL.md still works without opening it.
