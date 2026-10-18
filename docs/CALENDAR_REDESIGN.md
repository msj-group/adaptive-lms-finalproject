# Student and Teacher calendar redesign — 2026-10-16

## Authority and checkpoint

The owner approved the mobile proposal shown in the calendar conversation and
requested implementation for Student and Teacher. The owner explicitly noted
that another conversation was changing Grades concurrently. Checkpoint: 491ef52.
This is a local source/presentation change; it does not authorize deployment,
Git mutation, database changes, migrations or automated tests.

This separate document avoids concurrent writes to the four shared repair
documents. Existing Attendance, My records, Quiz, Help and concurrent Grades work
is preserved. No Grades source/template/style/client file was edited here. The
only shared implementation file touched is `app/locales/ar.json`: calendar keys
were added at the start without replacing existing or concurrent translations.

## Implemented behavior

- Student desktop defaults to Month with selected-day details; Teacher desktop
  defaults to a Week board with selected-day entries. Both support Month, Week
  and Upcoming. The Week board is not an hourly duration/conflict diagram.
- Phones show one seven-day strip and the selected day's cards. Day/Upcoming
  switch locally. Upcoming means entries on/after the selected day within the
  loaded range, not an unbounded future query. Earlier dates remain selectable.
  Legacy `view=list` links without a selected day retain the full bounded agenda.
- Native closed filter disclosure submits Group, event type and inclusive
  From/To bounds through GET. Public Group choices come only from the already
  authorized fetched range. Invalid/stale type or Group choices normalize to
  All with an explanatory message. The existing range normalizer stays intact.
- Day/week selection changes presentation only. Across the loaded range edge,
  links request the existing adjacent bounded range and retain filters/view.
  Days outside the selected range are visibly unavailable; they do not claim
  to have no events. The displayed month follows the selected civil day.
- Cards retain opening/due/deadline distinctions, all-day versus single-instant
  versus duration semantics, source Group context, locations and escaped details.
  Native Details reveals only existing server-generated role-specific links.
  Center events gain no Student/Teacher management endpoint.
- English/Arabic, RTL and shared light/dark tokens are supported. Without the
  calendar client, the full bounded nonempty agenda and native GET filters and
  detail disclosures remain available.

## Files and integrity

`app/services/calendar_presentation.py` is a pure presentation helper over plain
authorized rows. Student/Teacher `calendar.py` each add its import and context
call. Their SQL source, role decorators, URL attachment and private/no-store
responses are unchanged. The shared query service, administrator calendar,
legacy calendar macros, schema, retention and research code are unchanged.

The two role `calendar/index.html` entry templates load isolated
`app/static/css/calendar.css`, `app/static/js/calendar.js` and
`app/templates/calendar/_workspace.html` / `_entry.html`. CSS is scoped to the
calendar; no shared shell stylesheet or script was modified. No dependencies
were installed. No synthetic schedule is included in runtime code or data.

Existing source/merged row caps, five-query read, publication/current membership
rules, canonical schedule occurrence expansion, center-local dates/times,
public identifiers and role-specific destinations remain authoritative. UI
filters run after the bounded authorized read and cannot grant access. The
original truncation warning remains visible even when a UI filter narrows rows;
counts and choices therefore describe only the available capped read.

## Executed verification

- AST parsing of the helper and both affected Python routes succeeded.
- All four affected Jinja templates compiled with the actual localization
  extension. Locale JSON parsed; newly added calendar keys are unique.
- `node --check app/static/js/calendar.js` succeeded.
- Bounded manual in-app browser review rendered the actual templates and assets
  through an ignored memory-only fixture at
  `instance/calendar_layout_20261015/preview.py`; it opens no database and does
  not use the production app factory or real authentication.
- Reviewed Student desktop Month, Teacher desktop Week, mobile Day/Upcoming,
  day selection, native event detail/Teacher attendance link, native GET type
  and Group filtering, English/Arabic and dark presentation. A Listening filter
  displayed its one matching entry; Teacher Group B showed only its class on
  the selected day. Calendar filters were closed after navigation.
- At 390px and 320px viewports the calendar's measured client/scroll widths
  matched (343/343px and 279/279px in the observed shells). The Arabic filter
  panel at 320px occupied 280px within the 305px page. Empty and long-title/
  long-context cases also had 279/279px widths. A literal script-tag title
  stayed text; there was no script element inside an entry.
- Native next-week navigation traversed the remaining October weeks and loaded
  the normalized November range; its label showed November and November 1 was
  selected. The fixture with only the calendar script omitted displayed all
  12 nonempty days / 13 entries with the generic agenda heading. Other shared
  JavaScript remained enabled in that specific fallback review.
- CRLF-compatible `git diff --check` succeeded; tracked/untracked state and
  affected diffs were reviewed. The staged state was inspected separately.

The fixture was stopped after review. Browser/static checks are not automated
regression acceptance, actual MySQL execution or authenticated LMS workflow
verification. Those checks, production behavior and performance remain unverified.
No reset, seed, data/schema/migration, scheduler, staging/commit/push or deployment
was performed. The stop point is the completed local calendar source change.
