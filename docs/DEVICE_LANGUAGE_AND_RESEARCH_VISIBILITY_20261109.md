# Device language, persistent opt-in and research visibility - 2026-11-09

## Owner scope

The owner requests per-device platform-language notifications, concise actual
notification content, persistent opt-in across logout, a phone logo at the far
left, a graphical activity chart and visible ordinary session/activity review.
The clarification makes collection inclusive of every active Student, without
individual exclusion. This supersedes the individual-exclusion population rule;
historical records are preserved rather than deleted or relabelled.

## Device delivery

Revision f8a012bc34de follows e7f9012abc34 and adds a two-value language column to
device subscriptions. Existing devices default to English until their next
platform visit/language update. Language comes from the device's platform cookie,
not browser language or the sending user's language. It is synced on normal
authenticated visits and preference changes, including signed-device changes on
the login screen. Background delivery translates owned captions only; names and
authored titles remain unchanged. Compact previews omit instructional boilerplate
and never query private message bodies. Stored Notification history is unchanged.

Logout closes the login/research session but retains device opt-in and its signed
binding. Explicit Disable, OS/browser revocation, expiry, account suspension or
auth-version changes still stop delivery. Active signed bindings refresh and
renew near expiry without prompting for permission again. Switching to another
account disables the previous account's binding; it is never silently transferred.

## Research population and review

Collection's sole population is active Student accounts with Study provenance,
while the global gate/configuration/period permit collection. Individual exclusions
are retired in both eligibility paths, the operator exclusion command and UI.
Historical status/audit rows and reinstatement tools remain for accountability.

The empty Sessions/Recent actions screens were caused by the old review filter,
which admitted only assigned training/monitoring participants. Session metadata
now covers everyone. Ordinary unassigned participants show operational page and
activity labels through read-only requests. Reserved final-test participants show
presence and technical session metadata. Raw measurements and questionnaire labels
require the established training or monitoring assignment. Sampling, labels, retained data and export
assignment policy are unchanged. No Student event is fabricated for validation.

Live requests update every three seconds; Student transport is five seconds. The
SVG daily activity chart uses actual counts, including a real zero baseline, and
updates every thirty seconds while visible. It requires no external chart library.

## Verification and deployment

Use static Python/Jinja/JavaScript/MySQL-DDL checks, pure translation/chart checks
and bounded browser presentation review. These do not replace actual Android push
delivery or Student collection acceptance. Hosted activation requires private
backup, authenticated native collection pause, the one additive migration,
source switch and native resume. Preserve all old subscription columns and all
other application rows when comparing the migration's data fingerprints.

The overview includes a persistent recent-visit list by pseudonymous student code,
with bounded action summaries. The shared header holds labelled installation and
notification controls, including in the reader outside full-screen focus. The
collector observes coarse control intent, in-app page transitions, visible time,
media and declared outcomes. Valid push launches reuse an opaque event identifier
for deduplication. Logout uses the existing server session end reason. Browser
backgrounding and shutdown are best effort, with no claim of detecting a force
kill.
