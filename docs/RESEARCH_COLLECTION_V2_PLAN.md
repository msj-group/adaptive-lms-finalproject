# Research collection v2: implementation and acceptance plan

Date: 2026-10-28. Status: approved direction, implementation specification.
Local source is now prepared behind a default-off release gate. This does not
certify MySQL, browser, pilot, cloud or real-study acceptance. Current evidence
and the release checklist are in RESEARCH_V2_IMPLEMENTATION.md.

## Owner decisions and current checkpoint

The owners want the collection method designed around the eventual task:
detect a Student's recent frustration and, in a later evaluated version,
offer useful help. The current implementation is a comparison baseline,
not a constraint on the new method. The owners report that no real study
data has been collected. They expect at least 30 consenting adults, all
over 18, using the platform mainly on phones at Youth Centre. External
participant and centre approvals have been obtained according to the owners.

On 2026-10-28 the owners selected Telegram notifications and 45-day research
retention. The additional monitoring budget is undecided. Paid activation
requires an estimate first. These decisions supersede the earlier 15-day
target for the forthcoming study; they do not claim a live settings change.

The scientific checkpoint is the end of Phase 6, with renewed collection
acceptance still required. Phase 7 is model development, Phase 8 is approved
inference/help, and Phase 9 is a comparative evaluation. Repair Parts and
UI delivery waves are different numbering systems. Historical Phase 6/W8
acceptance and the 2026-10-08 UI freeze do not accept today's UI or v2.

No model accuracy, event delivery rate or sufficient sample size can be
guaranteed before the pilot. Thirty participants is a recruitment target,
not evidence that the model will generalize to new people.

## Design to implement

Use three separate layers:

1. A small, allowlisted stream of meaningful activity observations and
   authoritative workflow outcomes.
2. Sparse, optional human reports about a clearly defined recent period.
3. Versioned features derived from observations available before a defined
   decision time. Questionnaire answers are labels, never predictors.

The unit of prediction is a recent observation window, not a permanent
classification of a person. Keep ordinary natural use; do not introduce
artificial errors, forced difficulty or assigned frustration tasks.
Keep existing role authorization, external exclusions, pseudonymous links,
immutable exports and truthful study/development provenance.

### Observation contract

Record semantic actions once, independent of transport retries. A logical
action identifier links intention, transport attempts and an eventual
server-confirmed outcome. An outcome distinguishes success, business
rejection, technical failure and unknown completion; a failed connection
alone does not establish what committed on the server.

Cover native pages, in-page readers, dialogs, asynchronous navigation,
ordinary help and learning/media activities. Use stable allowlisted
page/activity/action codes; never infer page coverage from URLs alone.
Record context transitions when a reader changes without a full page load.

For each observation retain event occurrence time, server receipt time,
source, schema/configuration scope, sequence and idempotency information.
Record measurement support, clock offset uncertainty and coverage gaps.
Store an action-scoped reference for research correlation, not duplicated
account or business-object identifiers in research payloads.

Do not collect typed text, answers or option identities, passwords,
messages, recordings, filenames/contents, payment/grade values, raw key
sequences, screen coordinates, IP/GPS, full user-agent or fingerprints.
If coarse device/browser capability categories are needed, define a
closed vocabulary and minimization rationale before implementation.

### Candidate feature families

These are a predeclared candidate set, not promised winning features.
Each feature needs a formula, unit, observation source, cutoff rule,
missing-value rule and immutable feature-version identifier.

| Family | Intended measurement | Essential interpretation boundary |
| --- | --- | --- |
| Repeated actions | Distinct logical attempts at the same action in a bounded period | Delivery retries are not new user attempts |
| Rejected/failed operations | Confirmed rejections/failures divided by eligible completed operations | Pending and unknown outcomes remain separate |
| Waiting | Union of visible waiting intervals divided by observable visible time | Concurrent waits cannot double-count duration |
| Response time | Latency of operations completed by cutoff; pending wait separately | An outcome received after cutoff is unavailable |
| Progress | Time since confirmed progress within the current activity | Reading/listening can be valid activity without a write |
| Navigation loops | Repeated returns between stable context codes | Exploration alone is not frustration |
| Revisions | Count of permitted answer/input revision occurrences | No answer, key, field value or selected option is stored |
| Recovery | Attempts/progress following an observed failure | Only prior context and outcomes available at cutoff |
| Help use | Ordinary user-initiated help occurrences | Exclude the research prompt and future adaptive help |
| Media difficulty | Buffering, replay and playback failure aggregates | Replay may reflect study strategy rather than difficulty |
| Activity context | Allowlisted activity type and observed progress context | No participant, account, session or object identity |
| Measurement coverage | Visible observation duration, missing/support/gap indicators | Missing is not zero and background time is not frustration |

Collect any candidate touch/scroll aggregates first as a narrowly declared
experimental channel in the pilot, after physical-device measurement review.
Promote them to main-study model features only if participant-separated
development validation supports their value. Do not rely
on touch pressure, hover, mouse trajectories or raw typing dynamics as
universal phone signals. Normalize any permitted distance aggregate by
the viewport/scrollable extent and distinguish reading, scrolling and media.

### Human reference and sampling

Keep the raw optional 1-5 frustration scale and clear, localized anchors.
The question refers explicitly to the period immediately before the card
appeared. A 120-second lookback is a reasonable pilot starting candidate,
not a validated universal optimum. Fix the final reference period,
minimum observable coverage and response deadline before the main study.

Create sampling opportunities from observable elapsed time, independent
of batch count, retry count, errors and any predicted score. Record the
sampling policy, opportunity probability, eligibility and deferral reason.
Natural activity endings may provide a separate supplementary source;
keep its source and probability distinguishable in analysis. Apply shared
spacing and burden limits to both sources. Do not manufacture a desired
class balance by preferentially asking after failures.

Use a non-blocking accessible card with no selected rating. Defer during
timed/critical work, recording, uploads or another blocking dialog. Honor
Skip and burden limits. Pilot the frequency and clarity on phones before
freezing numeric settings; the old batch-based probability is not reused
as a claim of time-based random sampling.

Distinguish offer, display permission, actual rendered-display acknowledgement,
answer, Skip, pending, expired and delivery-uncertain states. A backend grant
does not prove that a card was rendered or read. Record the response policy
on the prompt's original immutable configuration, not the currently active
configuration at answer time.

Keep the optional cause and help preference separate from severity. Ask
help preference only on a limited subset if needed to control burden.
Missing, skipped, expired and uncertain answers remain unlabeled. Do not
assign a rating from behavior, observations or an early model. Any small
independent observation/interview exercise keeps its own source and timing;
observer disagreement is retained rather than overwritten.

Predeclare the prediction task and label mapping before fitting or comparing
models. The recommended primary pilot task is **moderate-or-higher reported
frustration**: ratings 3-5 versus 1-2. This is a transparent operational
definition, not a validated emotional diagnosis. Preserve all five raw
ratings; an ordinal analysis and a separately declared high-frustration
sensitivity analysis may be secondary. Do not search for the label cutoff
that produces the highest score. Report the primary positive prevalence,
average precision (with its specified PR-curve convention), precision/recall
at a development-selected operating threshold, calibration and participant-
level uncertainty. A fold without relevant classes is insufficient evidence,
not a zero-filled or silently omitted successful evaluation. The threshold
for offering help is a later policy decision, separate from the label mapping.

### Temporal contract and leakage prevention

The implementation refinement freezes the exact committed event membership
at server grant G. The question asks about the two minutes before it was
sent, and the reference window ends at G. Browser render T is mapped to
server time separately; unknown/over-5-second uncertainty or T-G outside
0..5 seconds excludes the primary label. The render acknowledgement is
metadata, never a predictor. Raw answers and exclusion reasons remain.

For deployable features at G, require frozen membership plus occurrence and
server receipt strictly before G. Receipt timestamps alone cannot establish
commit availability. An offline reconstruction with later-arriving
events may be retained as a separately named coverage diagnostic, but must
not be substituted for deployable training features. Exclude survey
interaction, response time, raw answer, optional causes/help preference,
post-G outcomes and future adaptive interventions. Use one feature
implementation with a parameterized cutoff for offline and online use.

Separate participants before preprocessing, feature selection, tuning and
evaluation. Participant/session identifiers are split keys only. Overlapping
windows from one person cannot cross train/test partitions. Fit imputation,
normalization and feature selection inside training folds. Keep a final
held-out evaluation closed while iterating; daily reviews must not turn it
into a tuning set. A participant-separated development estimate alone is
not proof of performance at another centre.

### Phone delivery and acceptance

Use one activation path so pointer/touch/click compatibility events do not
triple-count a tap. Cancellation, app switching, background suspension,
network loss and a missing unload callback do not indicate emotion.
Use a bounded acknowledged outbox, idempotent replay and exact actor,
login, configuration and provenance binding. Clear or reject obsolete
scope rather than attaching delayed events to a new user/configuration.

Rehearse on physical Android and iPhone devices using the target browsers:
normal navigation, in-page readers, keyboard, scrolling, media, background/
return, intermittent network, timeout/retry, logout and account switching.
Verify accepted events and exports, not merely that the UI looks correct.
Keep unsupported channels explicit; no browser can guarantee delivery after
process termination or device/network failure.

## Comparison with the current baseline

| Area | Existing v1 baseline | Required v2 target |
| --- | --- | --- |
| Signals and labels | Allowlisted observations, server outcomes, optional raw 1-5 | Preserve those protections; strengthen semantic context and action/result linkage |
| Random sampling | Sampling checks coupled to collection requests | Time-based opportunities independent of transport |
| Prompt display | Server display grant anchors the stored window | Explicit render acknowledgement and uncertainty |
| Delivery health | Session lifetime counters | Receipt-scoped measurements and explicit missing coverage |
| Phone coverage | Existing collector/outbox with delivery limits | Physical-device acceptance and semantic action deduplication |
| Feature pipeline | Future model work | Explicit deployable cutoff and one offline/online feature contract |
| Monitoring | Researcher dashboard; readiness checks | Scheduled data-quality checks plus independent missed-run detection |
| Retention | Earlier approved deployment target: 15 days | Newly approved forthcoming-study target: 45 days |
| Detection/help | No deployed adaptive model | Preserve Version A; evaluate detection and assistance in separate later gates |

This comparison does not label v1 data as invalid or claim v2 is implemented.
Keep old events, configuration meanings and immutable exports intact. A new
wire/schema/export/protocol revision must distinguish v2; do not overwrite
the executable v1 vocabulary and retrospectively call every row v2.

## Implementation sequence and stop gates

| Part | Concrete deliverable | Acceptance before the next part |
| --- | --- | --- |
| R0: specification | This plan, monitoring plan, event/feature dictionaries and schema diff | Numeric pilot settings and additive migration scope explicitly recorded |
| R1: storage/ingestion | Versioned v2 schema, relational constraints, idempotency and action-result linkage | Separately authorized MySQL forward migration; parent revision and preservation verified |
| R2: client/reference | Mobile semantic collector, time sampling, render acknowledgement and localized question | Fictional technical rehearsal through actual routes, DB and export with correct provenance |
| R3: operations | Read-only quality snapshots, Researcher report, Telegram and missed-run alarm | Cost approval, private secrets, deliberate failure/recovery rehearsal and retention verification |
| R4: real pilot | Consented natural-use pilot, separately versioned from fictional rehearsal | First-day review; early label/coverage and burden decisions before main collection |
| R5: feasibility | Simple baseline model with participant-separated development evaluation | Report class prevalence, distinct people, precision/recall, PR-AUC, calibration and uncertainty |
| R6: main study | New accepted baseline, immutable study configuration and handover | Correct provenance, exclusions, 45-day retention, export/restore and active monitoring evidenced |

Current R0 completion boundary: these two design documents are prepared.
The companion RESEARCH_V2_DATA_CONTRACT.md now proposes concrete pilot
settings, feature formulas and the relational change boundary. Executable
dictionary/endpoint mapping and the exact reviewed model/migration diff
are still required. R0 as a whole is not declared complete.

Model feasibility starts as soon as adequate labels from different people
and relevant rating ranges exist during the pilot; it does not wait for a
month. A pilot of roughly a week is a planning allowance, not a statistical
minimum or a promise that feasibility can be established by that date.
Compare simple context/outcome features, then navigation, then optional
touch/scroll families within grouped development evaluation. A rare rating
class is an evidence limitation, not a reason to relabel other cases.

Permitted gate outcomes are continue, correct a measurement defect, recruit/
observe longer, or make a documented protocol revision. No automatic label
editing, synthetic study examples or accuracy-driven data deletion.

The owner prohibits creating/running a replacement automated test package.
Use bounded source/template/JS checks and separately authorized manual
fictional browser/MySQL/migration/export rehearsals. Report the resulting
regression limitation honestly. This plan supplies no authorization to
create/apply a migration, reset data, deploy, change secrets, start a cloud
scheduler or mutate Git; prepare exact reviewable changes before those steps.

## Detection and help are separate deliveries

Version A supplies observations and labels without automated adaptive
messages. After the detector is evaluated, Version B may offer optional,
context-grounded help with refusal and cooldown. Do not submit work, alter
grades/deadlines or reveal prohibited answers. Keep detected frustration,
cause, help preference, intervention and help outcomes as separate concepts.

Evaluate acceptance, annoyance, progress/learning and subsequent experience
with an appropriate comparison. A lower rating after a message alone does
not show that the message caused an improvement. The quality monitor never
deploys a model or launches an intervention on its own.

## Sources and related records

- [Group-aware validation](https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-iterators-for-grouped-data)
  and [data leakage pitfalls](https://scikit-learn.org/stable/common_pitfalls.html).
- [Shared preprocessing for training and serving](https://www.tensorflow.org/tfx/guide/transform).
- [DeFalco et al., frustration interventions in a learning environment](https://link.springer.com/article/10.1007/s40593-017-0152-1).
- Historical implementation: PHASE6_COMPLETION.md, VERSION_A_RESEARCH_CONTRACT.md
  and VERSION_A_BASELINE_FREEZE.md. Current authority: APPROVED_REPAIR_CONTRACT.md.
- Operational design, cost assumptions and execution evidence:
  [RESEARCH_QUALITY_MONITOR_PLAN.md](RESEARCH_QUALITY_MONITOR_PLAN.md).
- Proposed defaults, feature formulas and pre-migration schema scope:
  [RESEARCH_V2_DATA_CONTRACT.md](RESEARCH_V2_DATA_CONTRACT.md).
