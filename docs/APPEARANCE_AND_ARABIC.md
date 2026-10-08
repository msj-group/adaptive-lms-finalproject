# Arabic, themes and utility panels

Local date: 2026-10-07 (Africa/Tripoli).

## Owner scope

The owner requested dark mode and Arabic throughout the platform, then specified
that language/theme selection belongs in Display and that Display/Help open as
popups instead of separate pages. This explicitly authorizes Arabic UI text over
the earlier English-only presentation rule. English remains the source language
for code, route names, identifiers and repository documentation.

The earlier source-only instruction remains: no platform/server/browser launch,
database access, migration, installation, automated suite or Git mutation.

## User behavior

- Display and Help remain in every role's navigation rail and account disclosure.
  They open the same shared native popover panels without page navigation.
  Login/opening/error surfaces also expose these controls.
- Display offers English/Arabic and light/dark/device appearance. Theme changes
  preview immediately; Apply preferences saves the native protected form and
  returns to the current local page with its query parameters. Language takes
  effect on that response. Motion/spacing retain their existing device settings.
- Native auto popovers provide Escape/light dismissal. Panel close buttons,
  autofocus and focus restoration are implemented, including a JavaScript
  fallback for browsers without the Popover API. Opening a utility panel closes
  the account disclosure or mobile drawer through its existing handler.
- Legacy `/workspace/help` and `/workspace/preferences` URLs remain role-gated
  and redirect to that role's home with the corresponding panel requested.
  Normal navigation/account controls are buttons and do not use these URLs.
- Single page headings, role URLs, account settings/sign-out, sidebar pixel
  offset restoration and full-page messaging remain.

## Presentation and localization

`app/static/css/appearance.css` loads after all shared role styles. Semantic
surfaces, text, inputs/focus, tables, menus, dashboard cards, login/opening,
errors, calendar, message bubbles and utility panels use light/dark variables.
Brand artwork retains its colors; blue `#144BAA` anchors primary actions and
selection, navy/light blue support the identity, and red remains a limited accent.
Native color-scheme, system appearance, mobile layouts, reduced motion,
forced-color selects and light print surfaces are supported in source.

Arabic sets `lang="ar"` and `dir="rtl"` on the document, moves the navigation
rail/drawer and adjusts logical alignment, select arrows and directional icons.
Email/password/URL/code/time presentations retain isolated left-to-right direction.

`app/i18n.py` provides the owned UI catalog, form translations and a Jinja
preprocessor. It translates owned literal markup, accessibility labels, widget
arguments, recognized role/status labels, validation messages and dates. The
3,101-entry catalog is `app/locales/ar.json`. Complete count phrases use `_n`
instead of concatenating English plural suffixes. Owned client statuses,
confirmations, recording/audio controls and emoji/category search use the same
public catalog. Inline saving statuses use Jinja JSON encoding.

Names, messages, lesson/material titles and bodies, uploaded content, answers,
research data, identifiers, option values, URLs and machine exports retain their
original values. Translation does not run as a global DOM/response replacement.
Unknown new UI phrases fall back to their English source and need a catalog entry.

## Device persistence and integrity

`app/blueprints/appearance.py` adds a protected native POST at `/ui/preferences`.
Only `en/ar` and `system/light/dark` are accepted. The return location must be a
bounded local path; external URLs, scheme-relative paths, backslashes and control
characters are rejected. Global CSRF registration applies to the form for both
anonymous and authenticated pages.

One-year, path-wide, SameSite=Lax cookies store public presentation choices.
Language is HttpOnly; theme is script-readable for pre-style restoration and
preview. Secure is set under HTTPS. Motion/spacing use the existing localStorage
key. No account/business/research record is written by preference changes.
HTML responses vary by Cookie; preference responses are private/no-store.

`ui_boot.js` restores appearance before styles, observes device appearance when
selected and exposes the UI text helper. Arabic pages load a public, versioned,
ETagged `/ui/arabic.js` dictionary before deferred feature scripts. It contains
owned text only, with no personal or session data.

## Arabic emoji data

All 3,953 Unicode 17 emoji, including tones and flags, retain their exact glyphs,
group order and English search terms. Arabic labels and keywords come from the
official Unicode CLDR 48.2 Arabic annotation data, pinned to `release-48-2`:

- [Arabic annotations](https://github.com/unicode-org/cldr/blob/release-48-2/common/annotations/ar.xml)
- [Derived Arabic annotations](https://github.com/unicode-org/cldr/blob/release-48-2/common/annotationsDerived/ar.xml)
- [CLDR release information](https://cldr.unicode.org/downloads/cldr-48)
- [Unicode license](https://github.com/unicode-org/cldr/blob/release-48-2/LICENSE)

`app/static/data/emoji-17-ar.json` stores local Arabic names/search terms, English
search aliases and source hashes. Arabic pages select it; English pages keep the
existing English asset. Full Unicode License V3 text is preserved beside it in
`emoji-cldr-LICENSE.txt`. No runtime external emoji service or new dependency
is introduced. Public upstream XML snapshots used during preparation are ignored
local files, not application data.

## Verification actually performed

- Python AST parsing: 346 files across `app`, `scripts` and `migrations`.
- Jinja source parse and localized compilation: all 191 templates.
- StrictUndefined offline source rendering: 195 role/destination/shared/section
  states across English/light, Arabic/dark and Arabic/device; plus 48 public
  login/error language/theme states. Complete own-role menus, selected state,
  single shell heading, unique IDs, popup controls, account destination and
  native POST logout/preference forms with CSRF fields were checked.
- Direct preference response review checked strict choices, local return paths,
  cookie attributes, cache headers and public dictionary responses. Registration
  of global CSRF was reviewed; no application request was dispatched.
- Source probes preserved authored names/content, escaping, IDs, URLs and
  choice values while translating required-field errors, dates and count phrases.
- Node syntax checks passed for all 14 application JavaScript files. Dictionary
  substitution keys were reviewed. All 3,953 Arabic emoji entries matched the
  English asset's exact glyphs/order and retained English search aliases.
- CSS specificity and balanced lexical delimiters were reviewed; no CSS parser
  was available or installed. `git diff --check` passed and staged state was empty.

These are bounded source checks, not an automated regression suite. No application
factory, application database, server/browser, migration or Git write was used.
Actual appearance, keyboard interactions, device persistence and live workflows
remain unverified in a browser under the owner's source-only instruction.
