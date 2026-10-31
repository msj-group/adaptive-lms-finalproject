# Direct sign-in destination - 2026-10-30

The owner requested removing the loading/intermediate page after sign-in.
This authorizes a local source correction only; preserve all prior UI,
Finance/Research work, authentication controls and application data.

## Changes

- `app/blueprints/auth/routes.py`: successful shared login redirects directly
  to the existing validated `next` target or the authenticated role's dashboard.
  No new intermediate session target is written. A legacy pending target is
  cleared on successful login.
- `login_target_for` resolves the approved role home before honoring `next`.
  This preserves the unknown-role refusal previously enforced by the opening
  route. Same-origin redirect validation and Researcher-only return paths
  remain unchanged.
- `/auth/opening` remains login-protected for compatibility, but returns only
  a direct HTTP redirect. Existing one-use targets are consumed and revalidated;
  missing/invalid targets use the role dashboard. Its private/no-store and
  Vary: Cookie headers remain.
- Delete the unused `app/templates/auth/opening.html`,
  `app/static/css/workspace_opening.css` and
  `app/static/js/workspace_opening.js`.

Credential lookup, password verification, active-account check, native form
validation/CSRF, shared login rate limit, placement and logout are preserved.
The shared Research login compatibility handler inherits the direct redirect.
The change applies to all roles and both desktop and phone without JavaScript.

## Verification and limits

Python AST parsing passed. Bounded source review compared the login guards,
role-home allowlist, safe redirect helper, Research compatibility entry and
final diff. Runtime-reference search found no remaining use of the deleted
template/CSS/JavaScript or a login redirect to `auth.opening`. The CRLF-aware
tracked diff check passed, with an empty staged state and unchanged HEAD.

No automated tests, pytest collection or browser authentication were run.
Successful/failed real login, CSRF/rate-limit execution, actual role access and
physical-phone behavior were not newly exercised. This is source/static
verification, not comprehensive authentication regression acceptance.

No database access, migration, reset/seed, scheduler, dependency installation,
secret/configuration change, staging/commit/push or deployment was performed.
Stop point: source complete locally; hosting is unchanged.
