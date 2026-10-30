# Research v2 data contract: local implementation

## Render alignment measurement correction - 2026-10-29

The fictional rehearsal exposed a calibrated point estimate 64 ms before G
with 173 ms uncertainty, although the server received the render acknowledgement
only 91 ms after G. A point estimate is not an exact physical timestamp. Keep
the stored estimate and its uncertainty unchanged; do not clamp timestamps or
rewrite old observations, answers or eligibility.

For new render validation, retain the same actual reported-render allowance
[G,G+5000]. Let E be the stored mapped estimate, u its recorded uncertainty,
and R the server's render-acknowledgement receipt. For known 0<=u<=5000 and
R>=G, intersect [E-u,E+u] with the causal interval [G,R]. Alignment qualifies
only if that intersection is nonempty and its entire upper bound is at most
G+5000. Disjoint, unknown, excessive-uncertainty and boundary-straddling evidence
remains excluded. Fast receipt alone cannot override inconsistent clock data.
This validates the same bounded reported-display window; it does not prove
human attention, invent a precise display time or change the predictor window.

The 60-second deadline calculation, 120-second feature window, sampling policy,
raw labels, installed schema and version identities remain unchanged. The
source digest and explicit causal-receipt-clock-bounds.v1 metadata distinguish
the correction. Monitoring reconstructs timing evidence separately from a
later late_answer flag. The rehearsal's answer arrived after about 71 seconds
and remains late/excluded under either timing interpretation.

## Pre-study monitoring clarification - 2026-10-29

For v2 only, a session may start on server preparation of an eligible Student
page, before the first client observation. This supplies an independent signal
when a browser collector never starts. It does not count as page display,
attention, visible coverage, an event or an emotional label. V1 session start
and all recorded policy remain unchanged. Resolve the session reference before
scope-token serialization; use an isolated locked transaction. This clarification
is made before real v2 collection and is tracked in the immutable source digest.
Monitor client delivery and per-version retention backlog as separate evidence;
absence of activity cannot certify a resolved delivery incident.

Date: 2026-10-28. Owner subsequently requested implementation and continuation.
Local source integration is prepared behind default-off RESEARCH_V2_ENABLED.
The migration is active under versions/ and installed on local MySQL at
a6f2c9d84e71. Real schema/preservation/grant verification passed; all temporary
privileges removed. No study or cloud activation was performed.
Current execution evidence and remaining phone/pilot/monitor acceptance:
RESEARCH_V2_LOCAL_MIGRATION.md (2026-10-29).
Current source/evidence and remaining acceptance gates are recorded in
RESEARCH_V2_IMPLEMENTATION.md and RESEARCH_V2_MIGRATION_PROPOSAL.md.

## Versions and pilot starting settings

Proposed identifiers are collection protocol `natural-use-protocol.v2`,
wire/event schema `natural-use-events.v2`, feature dictionary
`recent-frustration-features.v1` and export format
`natural-use-v2-export.v1`. Never assign them retrospectively to v1 rows.

Use the following concrete defaults for the first technical/real pilot.
They are engineering starting choices, not validated scientific constants.
Change them only in a new immutable protocol/configuration version.

| Setting | Pilot proposal |
| --- | --- |
| Reference lookback | 120 seconds immediately before server grant G; freeze exact committed event membership |
| Minimum observed coverage | 90 seconds within that window; retain actual coverage/gaps |
| First sampling opportunity | After 120 accumulated observable visible seconds |
| Further opportunities | One per 60 additional observable visible seconds |
| Random offer probability | 100 per 1000 eligible opportunities |
| Activity-end supplementary sampling | Off in the initial pilot; a later version may evaluate it separately |
| Minimum spacing | 600 seconds after the participant's latest granted reservation, across sessions/devices |
| Display burden | At most 2 reservations per session and 3 per participant/local calendar day |
| Skip | Stops further offers in that session |
| Offer expiry | 180 seconds; deferred/expired offers do not accumulate catch-up draws |
| Timely-answer deadline | 60 seconds from reported render time; replays of an already accepted answer remain idempotent |
| Session inactivity | Existing 30-minute starting rule; preserve established lifecycle and eligibility |
| Visibility observation | 30-second heartbeats plus transitions; do not bridge an unobserved gap over 45 seconds |
| Clock uncertainty | Record the estimate; over 5 seconds or unknown is unsuitable for the primary temporally aligned analysis |
| Display alignment | Preserve the mapped render estimate and uncertainty; the full plausible causal render interval must fit G..G+5 seconds as specified above |
| Calendar | Africa/Tripoli, frozen on the protocol; daily budgets do not follow a later deployment timezone change |
| New study retention | 45 days under the explicitly deployed, verified policy |

Reserve the display budget under the subject lock before the browser renders
the card. This prevents concurrent tabs spending the last slot twice. An
unconfirmed reservation remains a delivery-uncertain reservation and still
consumes burden/cooldown; it does not become a confirmed display or label.
This conservative distinction avoids an interrupted phone producing a new
card on every retry. Record each count separately in quality reporting.
Each prompt has a UNIQUE opportunity_id FK. Repeating grant for that
opportunity returns its original reservation and does not spend another
slot, restart cooldown or redraw. The opportunity stores its one reserved
session slot, unique within that session; non-reserved opportunities have
no slot. Reservation creation/slot allocation is atomic under the subject
lock. A session-local cooldown cache is never authoritative: recheck the
participant's latest reservation across sessions before allocating a slot.

The server controls due opportunities, eligibility, randomness and budgets.
Transport retries or replayed receipts cannot advance the opportunity
schedule, trigger another draw or extend last activity. Multiple visible
tabs contribute union time, not additive elapsed time. If background time
or gaps missed several opportunities, resume with one fresh opportunity;
do not show a burst or perform a catch-up sequence of random draws.
Persist each opportunity and its decision with a unique session/slot key;
the decision, eligibility reason and selection probability survive retry.

At grant, recheck current actor/exclusion, collection period/configuration,
coverage, Skip, cooldown and cross-session daily budget under the established
research lock order. Do not impose a new universal lock order on business
workflows. Critical/timed activities, recording, uploads and blocking dialogs
defer display; a display grant is never proof of attention.

## Event vocabulary and payload shape

Every item is validated against a version-specific closed dictionary. These
are the proposed v2 semantic event families:

| Family | Source | Allowed meaning |
| --- | --- | --- |
| context_enter / context_leave | client | A visible page, in-page reader or activity context changed |
| visible_interval | client | A bounded observed interval, with visibility and activity-support state |
| action_intent | client | One logical user activation of an allowlisted action |
| request_start / request_finish | client | A transport attempt and its observed finish/timeout/unknown state |
| workflow_outcome | server | An authoritative committed/rejected/failed/already-completed result |
| revision_count | client | A debounced allowed revision occurrence/count, without value or option identity |
| ordinary_help | client | User-initiated ordinary Help/utility opening, excluding research/adaptive UI |
| media_interval / media_action | client | Bounded buffering/playing interval or replay/playback-error occurrence |
| observation_gap | client | A reported coverage/delivery gap with a closed cause code |
| measurement_support | client | Closed support/device-category codes required to interpret missing signals |

Source is server-assigned; a browser cannot submit workflow_outcome. A
server outcome describes what the business service established, never what
a redirect, toast, HTTP status alone or client guess implies. Only a
confirmed state transition can be progress. Already-completed/replayed
business operations do not create another progress increment.

Each logical activation has an action_ref. Each transport attempt has a
distinct attempt_ref and retries of that attempt reuse its idempotency
reference. Correlation is scoped to the server-validated actor/login/session;
a client reference is a join hint, never proof of authorization or success.
Native and asynchronous writers must validate the reference's envelope;
missing/invalid correlation remains explicit unknown. Research correlation
does not replace existing CSRF, operation keys, snapshots or business checks.

Common typed fields are event_uid, session_id (server-derived), source,
event_type, allowlisted page/action/context/activity/detail codes, optional
context_ref/action_ref/attempt_ref, bounded count, optional interval start,
occurrence/receipt timestamps, and client sequence/page/tab/clock fields.
An interval ends at occurred_at_ms; reject inverted or oversized intervals.
Server events have no client clock/sequence fields. Client-only measurements
remain client reports and cannot establish a server-confirmed result.

Keep page/element/action mappings explicit for every current Student journey,
including workspace.search and in-page reader transitions. Build the concrete
endpoint/DOM mapping with R1/R2 source changes; this vocabulary is not proof
that those integrations exist. Do not add arbitrary JSON payloads, URLs,
raw field text, coordinates, content/object IDs or fingerprint fields.

## Feature formulas at decision cutoff G

Let W = [G-120 seconds,G). Only exact event members frozen at grant may enter
features; occurrence and receipt must also be before G. Receipt timestamps
alone cannot prove that an observation committed before a decision. In the
formula table below, historical symbol T denotes this decision cutoff G.
Preserve source and uncertainty. V is the union of
known visible intervals in W; O is its length, never extrapolated across
gaps. Zero is permitted only for a supported, sufficiently observed channel.
Otherwise the feature is unavailable with a reason. Counts use distinct
logical references, not HTTP/batch retry counts. Clip known intervals to W.

| Feature | Exact starting definition |
| --- | --- |
| observed_fraction | O / 120 seconds, plus gap/support flags |
| action_count | Number of distinct logical action_refs initiated in W |
| rapid_repeat_fraction | Fraction of those actions whose preceding distinct same-action/same-context activation in W was at most 2 seconds earlier |
| rejection_fraction | Distinct actions with a confirmed business rejection by T / distinct actions with a known terminal business result by T |
| technical_failure_fraction | The corresponding fraction for confirmed technical failure; do not call network uncertainty a committed failure |
| unknown_action_fraction | Actions without a known terminal result by T / action_count; pending and timeout reasons reported separately |
| visible_wait_fraction | Union of observed declared-workflow request waits intersected with V / O; a lower bound, not complete platform latency; unavailable without instrumented request evidence |
| completed_latency_p50 / p90 | Quantiles of known start-to-result durations for actions started in W and completed/received before T; report completed count |
| pending_wait_max | Longest observed visible wait through T for an unfinished action; unavailable if start/support is unknown |
| confirmed_progress_count | Distinct authoritative progress transitions in W, from an explicitly enumerated activity-specific code list |
| since_progress_visible_seconds | Observable visible time since the latest confirmed same-activity progress within W; right-censored at W start if none, with a censor flag |
| return_loop_count | Count of adjacent context-code triples A,B,A in W, with A different from B; collapse repeated same-code entries |
| revision_count | Sum of permitted revision occurrences in W, excluding survey/password/messaging/content values |
| recovery_fraction | Failures/rejections followed by confirmed same-context progress within 30 seconds and before T / failures/rejections with a full observable 30-second follow-up before T |
| ordinary_help_count | Distinct user-initiated ordinary Help openings in W |
| media_buffer_fraction | Union of buffering intervals intersected with V / observed supported media-visible time |
| media_replay_count / error_count | Distinct permitted replay/error observations in W; absent support is unavailable |
| activity_context | Last known pre-T activity kind from a small fixed vocabulary, plus unknown; no object/person identity |

Use a declared quantile convention and version it. Zero-denominator rates
are null with denominator zero. Recovery with incomplete follow-up is
pending, not a failed recovery. No confirmed progress in W means censored
time, not proof of inactivity. Keep per-feature sample/support counts for
quality checks and future abstention. The final classifier feature subset
is chosen inside development folds, not by repeatedly scoring a final test.

For completed latency, business outcome time and client start must have
compatible clock quality; do not clamp impossible durations to zero. Feature
calculation never waits for an outcome after T. Clip/union rules must be
shared by offline reconstruction and serving, with explicit missingness.

The question explicitly asks about the two minutes before it was sent. Grant
G anchors that reference, browser render T and answer receipt are separate
metadata. Require a timely answer and a known, well-aligned render; preserve
raw answers when alignment/coverage fails and retain the exclusion reason.
Poor-network labels must not disappear from raw data or denominator audits.
Primary label mapping and evaluation rules are in RESEARCH_COLLECTION_V2_PLAN.md.

## Proposed relational/schema change boundary

Static parsing of migration revision assignments on 2026-10-28 found one
source head: 8e5f3b0d7124, after 7d4e2a9c6013. This is not a query of local
or hosted MySQL. Before applying anything, separately verify the actual
target revision; an unexpected revision is a stop condition, not permission
to repair the database. Do not invent or reuse a future migration ID here.

Prefer separate v2 measurement tables with existing subject/configuration/
session identities. The v1 event constraints and prompt grant/window lifecycle
have different meanings; nullable fields alone would not preserve that
semantic boundary. Proposed tables and constraints:

| Table | Purpose and required integrity |
| --- | --- |
| research_v2_protocols | One immutable policy per configuration_id FK/PK; protocol/feature versions, frozen study timezone and bounded timing/clock rules; activated only with a matching v2 configuration |
| research_v2_sampling_state | One row per session_id FK/PK; immutable source release_ref, due visible-time opportunity and Skip state; mutate under the existing subject/control scope and check nonnegative counters; participant cooldown is checked across sessions |
| research_v2_sampling_opportunities | Unique session_id/opportunity_slot; immutable time, eligibility/deferral code, selection probability and decision; at most one subsequently reserved slot with UNIQUE(session_id,reservation_slot); a retry reuses its decision/reservation |
| research_v2_events | Append-only typed observations; unique event_uid; session FK; source/type and source/clock-field checks; bounded codes/counts/intervals; indexes on session/occurrence, receipt and action_ref |
| research_v2_prompts | UNIQUE opportunity_id FK and unique public ID; session/configuration resolved through the opportunity; separate grant/render/reference/response moments and states; raw 1-5/cause flags; immutable terminal answer |
| research_v2_feature_memberships | Composite prompt_id/event_id PK and separate FKs; append-only exact committed snapshot membership, no copied identity or event payload |
| research_v2_delivery_receipts | Timestamped ingestion-attempt result counts with distinct batch/attempt references and scope; no field values; foreign-key scope when valid, no subject creation for an invalid/ineligible request |
| research_v2_evaluation_partitions | Current-study subject FK/PK and unassigned/development/locked_final_test/prospective_monitor code; assignment/lock audit; no Student account ID or raw labels |
| research_quality_runs | Bounded operational receipts: start/finish, scope/version/cutoff, safe error code and unique run key |
| research_quality_reports | Unique period/scope/version key, counts/closed-schema aggregate body and expiry; no per-person rows or free text |
| research_quality_incidents | Stable rule/scope/version key, first/last seen, severity, notification attempt/confirmed delivery and recovery; references only aggregate run/report records |

Normalize through existing relationships. Do not duplicate users/account
columns or put names, content or identity mappings in quality tables.
Configuration/role/provenance eligibility is rechecked server-side after
the appropriate locks; FKs prove existence, not active Student eligibility.
Keep database constraints as final defense and handle IntegrityError with
rollback and a safe generic response. Exact field lengths, checks, indexes
and FK names belong to the reviewed model/migration diff, not an improvised
production command. No cascading or business-table hard deletion is added.

The prompt also records its rendered locale (English/Arabic) and whether
the optional help-preference question was shown. A missing help preference
when that question was not shown is different from an unanswered question
and from an explicit No. These are reference-quality metadata, not model
predictors. Do not infer either field from the user's later preferences.

Existing research_configurations needs a reviewed schema-version CHECK
extension to admit v2. Existing export metadata needs a reviewed format
extension and a truthful archive expiry_at_ms for configuration-scoped retention.
Do not backdate old immutable export descriptors; a legacy archive keeps
its recorded-policy expiry through its existing source/manifest semantics.
New export manifests record exact source versions, reference/feature cutoffs,
missingness, sampling opportunities/probabilities, split rules and counts.
A v1 exporter must not silently return
an empty archive when selected sessions actually contain v2 observations.
Keep old archive bytes/download identity unchanged.

Dispatch ingestion, server outcomes, sampling, feature building, report
queries and exports from the session configuration's immutable schema.
A batch/request claiming a different version is rejected, not converted.
Unknown versions fail closed. No v2 session may fall through the existing
v1 writer/sampler; extending a configuration CHECK alone is insufficient.
Creating a configuration must explicitly choose the intended supported
protocol rather than silently defaulting all new configurations to v1.

New v2 tables share the session/configuration parent, so retention code must
deliberately select/delete their dependent expired rows in FK order. Keep
existing authorized expiry and audit/gap records; do not add unapproved
cascades or a general delete API. Source events/answers remain immutable
until policy expiry. Configuration-scoped retention and mixed-policy archive
expiry require implementation, not only an environment edit.

Partition assignment must precede scientific label reports and fitting.
Unassigned subjects are excluded from scientific comparisons until assigned;
technical health still runs. Reserve final-test participants before exposure
to pilot development results. A person already used for development cannot
become an independent final-test person by changing configuration or session.
If all available participants are used in pilot development, recruit a new
reserved group or explicitly report that no independent final test exists.

## Reviewable implementation scope

R1/R2 would touch research-specific models/services/blueprints/templates,
usage_collector.js, localized question text, versioned dictionaries, research
exports/retention and supporting research documentation. Integration hooks
in Student workflow owners are limited to validated action correlation and
confirmed outcome recording; preserve their business policy and commits.
List the exact hook files after source mapping, before editing outside the
research allowlist. Preserve concurrent Finance/UI changes.

Creating the forward migration is a separate owner authorization under
AGENTS.md; this was subsequently granted for local inactive file preparation.
Applying it to either local or hosted MySQL is another explicit
operation with an exact parent/target, preservation/backup evidence and
acceptance plan. No database reset, synthetic study relabeling, dependency
installation, automated test package, commit/push or deployment is included
in this proposed Part. Cost approval/private credentials/scheduler activation
remain separate from source preparation.

Before a real pilot, evidence must include physical-phone round trips,
actual MySQL schema/constraint/migration checks on an authorized target,
versioned export decode/hash/row alignment, exclusion/actor-switch rejection,
timely-answer replay, clock/gap handling and monitor failure/recovery. Use
the owner's permitted manual/static verification; do not label it a
comprehensive automated regression suite.

## Sources and relationship to the other plans

This contract makes proposed defaults and formulas concrete; it does not
claim those settings are scientifically optimal or owner-approved as an
executed migration. Research rationale, model task and comparative baseline:
[RESEARCH_COLLECTION_V2_PLAN.md](RESEARCH_COLLECTION_V2_PLAN.md).
Read-only cloud operation, cost and live-inventory evidence:
[RESEARCH_QUALITY_MONITOR_PLAN.md](RESEARCH_QUALITY_MONITOR_PLAN.md).
