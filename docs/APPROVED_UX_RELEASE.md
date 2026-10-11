# Approved UX release — 2026-10-11

## Owner authority and source boundary

Following review of the implemented proposals and account access, the owner
explicitly requested commit, push and deployment. This supersedes the preceding
local-only restriction. Release only to the existing GitHub repository and
Railway `adaptive-lms-web` service in its existing production environment.
No database/schema change, volume replacement, variable change, new service,
Study, Version B or Baseline Freeze is included.

The reviewed implementation began at f227629 on
`ui/platform-unification-20261009`. GitHub's same branch subsequently contains
f337f2d, an owner-requested search/Messages follow-up. Preserve its record-only
workspace search, navigation-link removal, inline validation behavior and emoji
category layout. Integrate rather than replace or force-push it. The existing
Railway release branch is `preserve/st2-20261008`.

## Evidence and release gates

[APPROVED_UX_PROPOSALS.md](APPROVED_UX_PROPOSALS.md) records the native selected
dashboards, exact mobile destinations, safe GET enhancement, copy/structure
decisions and actual four-role QA. Its 17 protected-file equality checks describe
the local implementation stage before integrating f337f2d; the remote follow-up's
owner-requested Messages changes are separate and must be reviewed after integration.

The compatible implementation was checked through ordinary authentication on
owned fictional MySQL, CSRF enabled and TESTING disabled. The 680-capture core
matrix covers 17 families, ten widths, Arabic/English and Light/Dark. Additional
long/empty/validation/preferences/More checks and static parsing are recorded.
These are bounded browser/static observations, not a recreated test package.
Overall acceptance remains PARTIAL for physical devices/assistive technology,
native zoom, final academic/financial transactions and new scientific baseline.

Before push, inspect integration conflicts, prevent duplicate query controllers,
parse current source and recheck affected authenticated views. Use normal
fast-forward-compatible pushes only. Verify exact GitHub SHA and Railway source
SHA, build/predeploy/startup, `/health/ready`, public Login and current static assets.
The existing predeploy schema check remains at 7d4e2a9c6013; no migration files or
models are changed by this release. Preserve both existing persistent volumes,
MySQL service/configuration, development provenance and RAILWAY_ALLOW_STUDY=0.

## Execution evidence and limits

Deployment IDs and the final SHA are recorded after execution in the external
Arabic release report and machine-readable evidence directory:
`C:/Users/abdul/.codex/visualizations/2026/10/10/01a12430-b1a2-7e62-a185-8944e4c04d3c/approved-proposals-release/`.
An authorization or successful push alone is not deployment acceptance.
No production account credentials are available for an authenticated postdeploy
four-role smoke; local QA credentials must never be copied to production.
