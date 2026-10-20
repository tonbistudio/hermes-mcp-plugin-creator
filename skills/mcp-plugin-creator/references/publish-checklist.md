# Publish build checklist

A publish build is what goes to a public repo or the plugin catalog.

- [ ] Built with `build: "publish"`; the scaffold's private-data scan is empty (no user paths,
      user names, emails, tokens, project names).
- [ ] Mined with `redact_paths: true`; personal workflows generalized to the job, not the project.
- [ ] Every launcher pinned (npm `@x.y.z`, `uvx pkg==x.y.z`, git commit, image digest).
- [ ] Secrets only as `${VAR}`; README says which variables to set and where (profile `.env`).
- [ ] Description ends with a **Disclosure —** paragraph: what tools can execute, what is sent
      off the machine, what first start downloads, what the user must install separately.
- [ ] Hermes extension block for app-backed servers (`app` + `requires`) or hosted servers
      (`trust: untrusted`), validated.
- [ ] `hermes plugins validate <root>` passes; `mcp_creator_validate` loader diagnostics empty.
- [ ] README: what it does, install (`hermes plugins install owner/repo`, then `enable`, then new
      session), prerequisites, verification, license.
- [ ] Catalog entry (if submitting): pinned `sha`, `subdir`, `docs_url`, platforms, tier
      `community`; follow the catalog submission guide; an entry PR touches only `plugin-catalog/`.
