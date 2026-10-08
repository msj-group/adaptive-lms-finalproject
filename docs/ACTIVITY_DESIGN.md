# Activity workspaces — 2026-10-07

The owner requested a complete redesign of Student and Teacher activity pages,
with a distinct experience for each existing activity type and correct links
to the rest of the course. This is a source-only implementation. The earlier
instruction against launching the browser/server remains in force.

## Presentation

The shared activity shell provides the single topbar page title, course/group
path, activity library return and Units & lessons destination. The four spaces
use the Youth Centre blue/navy/light-blue/red identity with theme variables,
Arabic labels, RTL layout, narrow-screen stacking, visible focus and reduced
motion. Activity kind/phase values are machine identifiers, separate from
localized display labels.

| Space | Student experience | Teacher experience |
| --- | --- | --- |
| Quiz lab | Brief and launch panel, focused question paper, large selectable choices, saved-answer progress, separate final submit and saved results | Brief workbench, question cards/editor, availability, publication, attempt cards and answer review |
| Listening desk | Recording and authorized listening aids beside the current question; actual transcript policy remains authoritative | Recording/brief/transcript desk beside the question bank, content editor, availability and attempt review |
| Voice studio | Speaking brief beside the recorder/file fallback, preview and final upload; immutable playback/feedback receipt | Prompt editor, publication, recording cards and playback beside written feedback |
| Writing room | Task brief beside the writing paper, final-submission notice, own answer/feedback and unavailable-state guidance | Brief editor, publication cards, exact assignment overview and answer beside written feedback |

No new client library, AI score, fake progress, autoplay, auto-save, microphone
request or background upload is added. Recorder/audio/timer/option-editor and
shared confirmation/unsaved-form scripts keep their existing selectors and
protocols. The recording indicator animates only in the recorder's actual
`recording` state. Saved-answer progress displays the supplied saved count,
not an inferred completion or grade. Authored instructions, answers, names,
options and feedback stay escaped plain text with line breaks preserved.

The library uses four-type cards, native GET filters for type/state/course/group,
preserved-filter pagination, actual deadline/progress labels and explicit
empty states. Student publication/open-time and enrollment visibility are
unchanged. Teacher historical/nonoperational records remain readable; creation
links are offered only for the selected operational group. Existing editing,
publication, stale-form and submission freezes remain server-controlled.

## Course integration

Activities belong to a **Group**, whose Course/Level/AcademicTerm are the
existing authoritative hierarchy. Activities have no Unit/Lesson foreign key.
The interface does not fabricate a lesson association or completion rule.

- Student course/context links retain both Course and Group filters. Library
  card course links open that exact group's course overview; detail return
  links retain its group/type, alongside the real units/lessons destination.
- Teacher Activities is a primary navigation destination at
  `/teacher/activities`, with a scoped group library at
  `/teacher/groups/<group_public_id>/activities`. The course studio exposes
  the library and all four type views. Every activity opens its existing
  nested public-ID route, including extension IDs for Listening/Speaking.
- Assignments now have a read-only Teacher overview at their exact nested
  assignment URL. It reuses active Teacher assignment and ordinary-assignment
  ownership/extension exclusion helpers. Publication stays on the existing
  protected list forms; editing/submissions use their existing routes.
- The Teacher library is one four-branch SQL union, limited to 20 rows plus
  a next-page probe, filtered by active own Teacher assignment and role/status.
  It excludes extension rows from ordinary Assignment/Quiz branches, preserves
  historical hierarchy reads, and computes operational status from the parents.
  Course/group facets are bounded at 100. Student group facets reuse the same
  effective-visible union; an explicit group must pass the own outline lookup.

All added endpoints are read-only GETs. No model/schema change, migration,
business-data mutation, dependency installation or Git mutation is included.

## Verification boundary

Bounded offline source review used a minimal Flask/Jinja environment with
socket and SQLAlchemy connection/execution guards. It did not call the
application factory or dispatch application views.

- All 197 Jinja sources parsed and compiled with the localization extension.
- 340 synthetic markup states cover 36 activity templates and both libraries,
  including English/light and Arabic/dark/system, populated/empty and
  editable/final states. Markup nesting, unique IDs, one page heading, escaped
  authored content, active activity navigation and own course URLs were checked.
- 315 before/after state pairs compare rendered native POST action/method,
  field names/types/values, CSRF/state tokens and research/recorder attributes
  against an ignored source snapshot. The existing protocols are unchanged.
- Both four-branch queries compiled for MySQL without executing SQL. Teacher
  assignment/role/status and Student enrollment scope remain present. Native
  metadata resolves the four distinct nested routes and actual course URLs.
- The updated primary navigation passed 198 offline render states, retaining
  all own-role destinations and the shared account/logout/Display/Help controls.
- New owned activity copy is in the local Arabic dictionary. CSS received
  delimiter, theme-variable, responsive and specificity review; no CSS parser
  was installed. Source whitespace was checked.

These are static/source checks, not live application, database, browser,
device/keyboard, microphone/upload, timing or concurrency acceptance. Those
remain unverified under the owner's source-only instruction. No automated
suite was created or run. Review helpers/snapshots stay under ignored instance.
