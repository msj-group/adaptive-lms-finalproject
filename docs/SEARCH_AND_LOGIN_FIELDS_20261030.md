# Caret-only search and neutral Login fields - 2026-10-30

## Authority and scope

The owner supplied a screenshot of the workspace search dialog's focus rings
and requested removing them from search fields throughout the platform. They
also requested ordinary neutral Login fields while typing, with email and
password icons. This is a local CSS/template correction. Preserve all prior
navigation, Finance and Research work, application data and authentication.
No automated application tests, database work, migration, deployment or Git
mutation are included.

## Changes

- `app/static/css/search_focus.css`, loaded after role/feature styles by
  `app/templates/layouts/base.html`, suppresses focus outlines/shadows on
  search inputs and the workspace, Group and Messages search wrappers.
  It covers native search inputs and the established plain-text `q`/`search`
  fields. The grouped workspace search input has no duplicate inner border.
  Existing ordinary field boundaries and text carets remain.
- `app/static/css/base.css` and `app/static/css/product/foundation.css` exclude
  those search inputs from generic focus recoloring. Other fields, buttons,
  results, selectors and links retain their existing focus styles. Layout
  asset versions are updated in Admin, portal and Research bases.
- `app/static/css/login_eclipse.css` removes the red focus border, glow and
  active-field fill. Login's normal fill/border remain during typing. Existing
  server-error styling, error messages and email-format feedback remain.
  The password visibility button uses the ordinary blue focus color.
- `app/templates/auth/login.html` gives the existing envelope and lock SVGs
  explicit decorative icon containers, with a stable 20px size and visible
  theme-aware blue color. Labels, field names, autocomplete, password masking,
  visibility control, native submission and CSRF markup are preserved. The
  Login stylesheet version is updated.

No JavaScript, route, authentication or search-query behavior was changed.

## Verification actually performed

Bounded static review: all five changed templates parsed with Jinja. The final
CRLF-aware tracked diff check passed; no Git configuration was changed. The
new stylesheet and this receipt were checked for trailing spaces separately.

Manual browser review used the existing owned loopback renderer with actual
templates, localization, registered URLs and static assets, fictional display
context and an anonymous Login form. No database extension is initialized and
no real business/authentication view is executed. Fictional credentials were
typed only for presentation and were never submitted.

Observed:

- Admin workspace search: the focused input had `outline: none`, `box-shadow:
  none`, zero border width and an outer wrapper with no outline.
- Directory, plain-text Finance, Group-wrapper and Messages-wrapper examples:
  focus rings/shadows were absent. The directory's ordinary border color was
  identical before and after focus. A non-search field retained its existing
  solid outline, blue border and focus shadow.
- The Student, Teacher and Researcher search dialogs also showed no input or
  wrapper focus ring. The browser accepted all three shared CSS rules.
- Login in dark mode: email before/after focus and password during typing had
  the same neutral fill/border and no shadow. Both decorative icon containers
  were visible at 20 x 20px. Arabic/RTL labels and password masking remained.
- Login in light mode at 390 x 844: the focused password field retained the
  ordinary neutral fill/border, with no shadow or outline. The envelope and
  lock remained visible. Search was reviewed at the same phone size and at
  1440 x 1000, with no new focus rectangle.

Ignored screenshots and the temporary renderer remain under
`instance/ui_shell_review_20261030/`: `login-neutral-desktop.jpg`,
`login-neutral-phone.jpg`, `search-caret-desktop.jpg`, and
`search-caret-phone.jpg`. The owned renderer is stopped at close-out.

These are manual presentation/static checks, not comprehensive automated
regression acceptance. No automated test or pytest collection ran. Actual
authenticated search results, successful/failed authentication, browser
autofill, forced-colors mode and physical phones were not exercised. Existing
validation and authentication are preserved by source review. No MySQL access,
migration, reset, seed, installation, scheduler, secret/configuration change,
Git staging/commit/push or deployment was performed. Stop point: source complete
locally; the hosted installation is unchanged.
