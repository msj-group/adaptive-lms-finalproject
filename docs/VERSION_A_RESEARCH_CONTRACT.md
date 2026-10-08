# Version A research contract — va-w7-r1

Owner authority: final Version A W0–W9 authorization, 2026-10-08.
Wire/event schema remains `natural-use-events.v1`; no event type, sampling,
window, rating anchor, retention, eligibility, provenance or A/B assignment changed.
Tracking scope revision: `va-scope-r1`. The executable dictionary is
`app/services/research_event_dictionary.py`.

## Scope and privacy

Every declared Student HTML page remains covered when the server grants collection.
Courses/Course and Student Account are now included. Shared Account, Messages,
Notifications, Help/Display run the collector only for an eligible Student.
Technical bytes/Range responses and bell-preview fragments are deliberately excluded.
Help/Display redirect to utilities on the host page; they have stable action IDs,
not new page identities. Teacher/Admin/Researcher never run the Student collector.

Account: page/visible-time/navigation/control and native form attempt/invalid count.
There is no Account input-change listener and no new server outcome/error event.
Password/photo success stays operational UI feedback, never inferred from redirects.
Messages edit/hide/clear similarly use native attempt/navigation only.

No password, answer, option identity, raw field value, typed text, search query,
message/subject/recipient, filename/file size/content, photo/audio, personal name,
grade, attendance, finance value, URL/query, DOM text/HTML, IP, user-agent or
fingerprint is a research payload. General input activity is a boolean heartbeat
context, never a key identity or a key count.

## Approved repairs

- Same stable navigation/action identity across Desktop/Mobile. Courses/Account,
  More/Help/Display, Quiz grid/bookmark and File Assignment IDs are closed-set.
- Activities native filters and all three Search selects report change occurrence
  only, never their chosen values. File Assignment reports file-selection occurrence,
  unlike Account photo, whose file field is intentionally unobserved.
- `search_result.position` means **global ordinal across the actual rendered list**,
  preserving the existing course/unit/lesson/material/announcement grouping and rank.
  It is 1-based and bounded to 500; larger positions are absent, never clamped.
  It is not rank across all database matches or across separate requests.
- Quiz capture handlers observe intention before asynchronous business guards.
  A native form resumed after confirmation/confirmed saves is the same attempt,
  identified by a transient `data-research-resuming-submit` flag, not double-counted.
- Quiz automatic saves remain serialized, with actual `input_change` observations
  and authoritative `quiz_answer` acknowledgements. An automatic fetch does not
  emit a fabricated native `form_submit`. Network-failed automatic request attempts
  without a server outcome are consequently not separately observable. Recording
  those as a new attempt meaning would need separate methodology approval.
- Final repeated-click bursts finish idempotently before submit/pagehide/hidden,
  cancelling their old timer before delivery. Threshold/window remain unchanged.
- Visible heartbeat 30 s, flush 15 s, buffer 200, batches 50 / 32 KiB, scoped outbox,
  retry/idempotency and server validation remain. Browser kill/power/network loss
  cannot guarantee delivery; unseen events are not fabricated.
  Existing explicit replay-only redeliveries are idempotent no-ops and do not
  increment session delivery counters; a duplicate response can therefore coexist
  with a zero recorded duplicate count. Delivery charts disclose this limitation.
- Survey remains the existing optional **non-modal** card (`aria-modal=false`).
  Neutral heading receives focus; no rating is preselected. Escape/Skip retain
  existing dismissal, response timing and retry meanings. Closing restores a valid
  origin/fallback only when focus is still inside the card or on the body; continuing
  in the background is respected. No new trapping, overlay or urgency is introduced.

## Export compatibility and historical boundary

CSV column layout/version is unchanged (`natural-use-csv.v2`). Additive manifest
keys identify the **generator's** dictionary/scope revision and explicitly warn
about historical Search positions. They do not label every old event as W7.
Existing immutable exports remain unchanged. A homogeneous post-freeze dataset
must use a newly activated configuration after the baseline. Development Pilot
gets a separate configuration; production starts empty, then uses an explicitly
approved Study configuration. Never retroactively reclassify old sessions.

Charts are read-only. UTC dashboard date/configuration filters are bounded to
90 days; each chart explains occurrence/offer/start scope, source, denominator
and missing-data limits. Raw 1–5 self-reports are not a diagnosis or prediction.
All retained aggregate tables have their separate whole-population scope.

Verification and freeze evidence belong to W8; this contract alone claims no
delivery/export/pilot/production acceptance.
