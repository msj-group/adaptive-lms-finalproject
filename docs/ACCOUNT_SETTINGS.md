# Account settings — 2026-10-07

The owner requested voluntary password changes and profile-photo uploads.
Entry: the existing top-right account card → **Account settings**, for Student,
Teacher, Administrator and Researcher. The shared page retains the owning role's
shell and navigation, with its page title once below the workspace eyebrow.

## Password

`POST /account/password` requires the current password, a new 15–128 character
password and matching confirmation. The shared password service supplies Argon2
and the same length policy as administrator-managed resets. Current passwords
may be shorter than the new-password minimum, preserving access for existing
accounts; input is bounded before hashing. The new password must differ from
the current one. No forced-change flag, forgotten-password/email workflow,
MFA, composition requirement or compromised-password blocklist was added.

Purpose-specific signed snapshots expire after one hour and bind public account
identity, role, account version and auth version. Global CSRF remains enabled.
The shared locking service preserves Group-before-User ordering for Teachers;
the same active authenticated actor, version and current hash are rechecked
under locks. Increment account version and auth version and append a password
revision in the same transaction, without password/hash audit values. After a
successful commit, close the current browser's collector reference, log out and
redirect to shared sign-in. Other sessions become invalid on their next request.
Limit the endpoint to five requests per minute per authenticated account.

## Photo

`POST /account/photo` accepts a still JPG, PNG or WebP, at most 5 MiB and
16 million pixels. Use the installed Pillow 12.3.0 decoder, not browser
filename/MIME or file-input `accept`, to establish the image. Reject animation
and decompression bombs. Apply orientation, centre-crop to 512×512, then paste
onto a new canvas and save PNG, removing original EXIF/GPS/ICC/text/container
metadata. The private storage core verifies PNG signature, bounds writes,
hashes bytes and atomically publishes under a fresh random key.

Decode/store before taking account write locks. Reauthorize the current actor
and signed snapshot under the canonical locks. Create UploadedFile metadata
with the current account as uploader and a `profile_photo` AccountRevision with
the file's public identifier; increment account version and commit together.
Existing schema provides positive-size/category/file-owner and account-version
constraints. The photo reference in revision JSON is an event projection rather
than a database FK; all reads additionally prove the file is still an owned
canonical PNG. Other account edits/password revisions do not reset the latest
photo revision. Existing administrator stale-form snapshots remain unchanged.

`GET /account/photo` serves only the authenticated account's latest photo.
Query parameter `v` changes the image URL after replacement; it cannot select
an account or file. Authorization still applies to direct requests. Responses
are private/no-store with nosniff, canonical image/png and restrictive CSP.
No public/static photo endpoint or cross-user photo permission was introduced.
Header lookup is lazy and memoized per request: one revision lookup and, when
there is a reference, one owned-file lookup. Missing physical photos fall back
to initials in the UI. Account menus and the settings preview use that helper.

Register per-endpoint body limits before global CSRF: photo source cap plus
64 KiB multipart overhead, 128 KiB form parser memory and eight parts; password
16 KiB body/form memory and eight parts. Photo uploads are limited to ten per
minute per authenticated account. The generic Material upload policy is unchanged.

Replacements preserve earlier file rows/bytes and revisions. No successful
route deletes a committed photo. A rejected transaction removes only the file
it just stored. The existing private storage crash window between atomic file
publish and database commit can leave an orphan file; no new reconciliation or
retention worker was added. Backups must include the configured private storage
alongside account/file metadata.

## Source evidence and boundary

- 292 application Python files parse with AST; 190 Jinja sources parse.
- Project Python 3.14.6 imports the account blueprint and Pillow 12.3.0 without
  creating the application. The bundled Python 3.12 runtime cannot load the
  project's compiled Python 3.14 dependencies; use the existing project runtime.
- Offline HTML rendering covers 16 combinations of four roles, initials/photo
  and form errors. It checks one h1, unique element IDs, blank password values,
  both native POST forms, separate prefixed CSRF fields and photo/menu links.
- Four URL rules resolve in registered blueprint metadata: settings GET,
  password POST and photo GET/POST. No endpoint was dispatched.
- The first offline rendering fixture omitted a Flask-Login user loader and
  stopped before template rendering; the corrected fixture uses a synthetic
  loader and rendered successfully. Network/SQL execution are explicitly
  forbidden by review guards, and no SQLAlchemy app/database is initialized.
- No browser/server, automated test package/suite, database connection/write,
  migration, install, deployment/scheduler or Git mutation. Credential/photo
  persistence, visual interaction and concurrent writes await authorized runtime
  verification. Static review is not end-to-end or regression acceptance.

Implementation: `app/blueprints/account.py`, `account_forms.py`,
`app/services/self_service_accounts.py`, `profile_photos.py`,
`app/templates/workspace/account.html` and `app/static/css/account.css`, with
shared account-menu/layout and password-policy integration.
