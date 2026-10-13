# Approved UX release — 2026-10-11

## Activity, mobile header and Messages release — 2026-10-13

The owner requests upload and explicitly confirms GitHub plus updating the
Railway website. This supersedes the 2026-10-12/13 delivery pause for the reviewed
activity overview/result/receipt alignment, summary selectors, bottom details,
mobile breadcrumb/header behavior and Messages density. Checkpoint and both
remote branch heads were verified at
`747bf751e1d893df7a1d6786f1d634adb6af478b`; the existing Railway web service is
also pinned to that SHA, successful, with no staged environment changes.

Create one reviewed commit for the 22-file allowlist recorded in PROJECT_STATUS.
Push normally to `ui/platform-unification-20261009` and `preserve/st2-20261008`;
do not force-push or include the unrelated untracked Help template. Stage only
the new exact source pin for the same repo/release branch on `adaptive-lms-web`
in the existing production environment identified below. Verify that this is
the sole staged change before committing the Railway patch. Preserve services,
volumes, variables, domain, startup/predeploy commands, schema and research policy.
No automated tests, new migration or production business-data operation.

Static/manual evidence is in PROJECT_STATUS. Record actual commit, remote heads,
deployment identity/status, readiness, public Login and exact asset-byte checks
in ignored `instance/activity_mobile_release_20261013/release_report.json` after
execution; this authorization is not a claim of deployment success.

## Final Quiz attempt selector follow-up

After the preceding deployment, the owner requests a final Quiz adjustment:
one visible attempt summary and an Attempt dropdown instead of a long list.
Continue the existing explicit GitHub/Railway delivery authorization for this
bounded addition. Starting SHA: `0a75016f4191f0f39edcbf614db0f45d2fc0bec9`.
Static/manual presentation evidence and limits are in PROJECT_STATUS.md.

Create one reviewed commit for only the Quiz template/client/styles, portal
stylesheet cache version, Arabic Time expired label and directly related docs.
Preserve the untracked Help template and ignored manual review artifacts. Push
normally to the same follow-up/release branches, verify the exact SHA and update
only the existing web service's source pin. Preserve all other production config,
services, volumes and data. No models, migrations, authentication or research
policy changes. The unchanged predeploy must verify the existing schema head.
Record actual release results separately after execution.

## Platform navigation, Quiz and browser drafts follow-up release

The owner's latest instruction explicitly requests GitHub upload and Railway
deployment of the implemented four-role Back/breadcrumb navigation, Quiz
disclosure/card/result cleanup, automatic-only local Assignment text drafts and
platform-language file controls. Login-session policy recommendations have not
been implemented and are excluded from this release.

Starting checkpoint: b8e85ae01844505304b752d8988cf2590d65f173 on
`ui/platform-unification-20261009`. Both GitHub follow-up/release branches and
the live Railway service were verified at this SHA before release. No Railway
changes were staged. Preserve the unrelated untracked Help template and ignored
presentation-review artifacts. Push the reviewed commit normally to the existing
follow-up and `preserve/st2-20261008` release branches; do not force-push.

Deploy only `adaptive-lms-web` (0134642d-f6f5-4e40-9f16-6fc389502d9c) in the
existing production environment (78c6b914-a09a-4a0e-81a9-ec82cd22a5da) of
AdaptiveEnglishLMS (c0fa2362-4e1e-4412-908a-1b4cbee38efd). Advance its existing
exact source-commit pin after GitHub verification. Preserve all other service
settings, the /data volume, MySQL service, variables and research policy. Models,
migrations and deployment scripts are unchanged; existing predeploy must verify
the already-current 7d4e2a9c6013 head, not introduce a new migration.

Current static/manual verification and its presentation-only boundary are in
PROJECT_STATUS.md. Production acceptance still requires the actual successful
build/predeploy/startup, exact source SHA, readiness, Login and changed assets.
Execution evidence is retained in ignored `instance/platform_release_20261011/`
and reported to the owner after execution; authorization is not proof of success.

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

## Phone-reported bottom navigation correction — 2026-10-11

After the release, the owner reported More wrapping on an S23 Ultra. The actual
local Student dashboard reproduced two navigation rows and a 106px bar at 384px:
an obsolete Student-only four-column CSS rule overrode the shared five columns.
Remove that rule in the existing navigation stylesheet and bump only that asset's
version in the three authenticated layouts, so refreshed phones request the fix.
The selected destinations, More implementation and protected page design remain.

Bounded actual-app checks on owned fictional MySQL cover all four authenticated
roles at 320, 360, 384, 390, 412, 430, 540, 768, 900, 1023 and 1024px in English
Light and Arabic Dark: 88 observations. Below the desktop breakpoint, all five
controls occupy one row with targets at least 44px and no horizontal overflow;
Student's bar is 60px at 384px. More opening, Tab/Shift+Tab, Escape, outside-click
dismissal and focus return passed for all four roles. Student/Teacher Messages
retain the 66px bar, one row and correct More active state. These are touch/mobile
browser emulations, not verification on a physical S23 Ultra. The earlier checks
missed the row-count criterion; the correction evidence records it explicitly.

Continue the owner's commit/push/deploy authorization for this bounded deployed
defect, preserving database/configuration/provenance. Execution evidence is in
the external `mobile-nav-hotfix-20261011` directory alongside the release report.
