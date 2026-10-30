# Research quality monitoring: cloud execution plan

## Current preparation checkpoint - 2026-10-30

Owner chose preparation only, without paid activation. The latest source has
independent server-prepared session presence, client-startup silence evidence,
versioned v2 retention backlog and a separate retention-success heartbeat.
Render-clock flags are reconstructed from raw causal/uncertainty bounds rather
than an answer-quality code that may be overwritten by a late answer. Turning
off optional rate evaluation preserves active incidents rather than claiming
recovery. No scheduled job, actual purge, Telegram send or missed-run acceptance
has been executed. Initial-silence detection does not cover every later gap.
See RESEARCH_V2_LAUNCH_ACCEPTANCE.md and RESEARCH_V2_CLOUD_COST_PROPOSAL.md.
The older design/rollout descriptions below are historical or unactivated plans.

The current checker also records stored-primary-label-yield.v1 per-group
private JSON evidence: matured rendered prompts, raw/timely/late answers,
stored primary-eligible answers, excluded answers and closed stored-quality
reasons, skips and unanswered prompts. Technical scope does not SELECT rating.
The eligible predicate matches export: saved answered status, timely and stored
quality eligible. Historical quality is preserved. Counts are factual at every
size; fraction/assessment remain unknown below five matured rendered prompts.
These fields do not add SQL columns: Researcher SQL counts still include raw
late/excluded answers. Read the protected daily JSON for usable-label review.

The optional rate-alert setting also controls a provisional zero-eligible
investigation signal: at least five matured rendered prompts and zero stored
eligible examples on each of two distinct, nonoverlapping completed Tripoli
days, within retained evidence. Populated days may be separated by weekends.
Duplicate/overlapping/older periods cannot advance evidence; small/missing days
and a disabled setting cannot certify recovery or erase an active incident.
Recovery needs two comparable populated days with at least one eligible example.
The private state records daily horizon, semantics, boundaries and 45-day expiry.
Sparse groups may never meet the minimum; this is not a guarantee of data or
model quality. All deployment, notification and missed-run checks remain open.

Incident assessment uses a fresh time after snapshot/publication completion,
since snapshot cutoffs follow the runner's start time. Any assessment failure
becomes checker failure and suppresses the success heartbeat. Actual local
read-only counts matched the three fictional saved answers (two late, one
eligible) and one unanswered prompt. No scheduled runner or outbound service
was executed to validate alert/recovery delivery.

Date: 2026-10-28. Status: local checker/publisher/Researcher review source is
prepared; paid activation and delivery acceptance are not complete. Current
configuration, provisional thresholds and limits are in RESEARCH_V2_IMPLEMENTATION.md.
No scheduler, Telegram send, API usage or paid service has been activated.

## Owner decisions

- Monitor the study throughout collection without requiring the owner's
  computer or this desktop chat to remain online.
- Notify through Telegram in addition to the role-gated Researcher workspace.
- Retain research data for 45 days for the forthcoming study. This is the
  newly approved target, not a verified deployed environment value.
- No additional monthly budget is agreed. Show an estimate before enabling
  any paid resource. AI analysis is optional and disabled by default.

The collection method and scientific acceptance gates are specified in
[RESEARCH_COLLECTION_V2_PLAN.md](RESEARCH_COLLECTION_V2_PLAN.md).

## Verified hosting checkpoint

Read-only Railway environment discovery on 2026-10-28 found:

| Item | Observed state |
| --- | --- |
| Account / project | MSJ Group / AdaptiveEnglishLMS |
| Project | c0fa2362-4e1e-4412-908a-1b4cbee38efd |
| Environment | production / 78c6b914-a09a-4a0e-81a9-ec82cd22a5da |
| Web service | adaptive-lms-web / 0134642d-f6f5-4e40-9f16-6fc389502d9c |
| Web source | msj-group/adaptive-lms-finalproject, master |
| Latest web deployment | 0fbcbdf5-fc44-4e9b-a043-8efa06a3522a, SUCCESS, 2026-10-26T17:20:40Z |
| Database service | MySQL / efc1b4bf-86ce-4550-a01c-5467be5a5fde, image mysql:8.0.46 |
| Current service inventory | Web and MySQL; neither has a cron schedule |
| Pending environment changes | None at inspection |

This inventory does not verify database records, active collection, live
retention, backups, other providers' monitors, or the deployed commit's
equivalence to the dirty local tree. No secret values were inspected. The
existing retention TOML is an example, not proof of an active hosted job.

## Recommended operating mechanism

Implement a short deterministic checker as a separate Railway Cron service.
It starts, reads a bounded consistent snapshot, creates a report and exits.
It does not need to sign into Student pages or generate synthetic Student
traffic during a real study.

1. **Hourly quality run:** read research configuration and homogeneous
   v2 receipt/outcome/prompt metrics. Check integrity and collection health.
2. **Daily review:** generate a simple Arabic report in the Researcher
   workspace, using the prior Libya calendar day and an explicit cutoff.
   Keep English source strings and the platform's existing Arabic localization.
   Suggested availability is 09:00 Africa/Tripoli (07:00 UTC); this is a
   proposed schedule, not an active job.
3. **Telegram incidents:** send a brief aggregate notice for a meaningful
   new incident, material escalation or recovery. Include a protected
   workspace link, no Student identity or individual rating. Keep unchanged
   conditions quiet; the daily report remains available in the workspace.
4. **Independent missed-run alarm:** a separate provider expects a success
   heartbeat. A missed/dead checker must still generate an alert even when
   Railway or the checker cannot send its own failure message.
5. **Optional AI commentary:** only after budget and integration approval,
   let a cloud reviewer interpret a minimized aggregate report once daily.
   Critical checks and alerts continue if AI is unavailable or its budget
   is exhausted. AI recommends actions; it does not apply them.

Hourly Cron candidate: `0 * * * *` in UTC. The hourly runner can generate
the daily report once, idempotently, after 07:00 UTC; a separate daily job
is unnecessary. If a run is delayed, publish the overdue daily report once
on the next successful run. Provider timing is not an exact alarm clock.

Use bounded runtime/queries, separate connection limits, a no-overlap
lease, idempotent report keys, bounded transient retries and explicit
connection cleanup. Avoid a long-lived AI agent that investigates raw data
every hour. Configure an independent expected interval and grace period
after measuring startup/runtime and provider delays; do not copy an
unverified timeout into production.

### Access and report storage

Use a dedicated MySQL principal restricted to SELECT on explicitly required
research tables/views. Independently aggregated eligible-usage evidence
must exclude identities and require its own minimal read scope. Do not give
the checker migration, LMS business-write or Student/Researcher credentials.
The ingestion application retains its own existing write principal.

The consistent snapshot is read-only, with an explicit as-of cutoff and
homogeneous provenance/configuration/protocol/schema/app-release grouping.
Do not call purge_expired or export creation as a monitoring query: even
count-only retention preview currently acquires control/configuration locks.
Monitor job receipts and expiry counts instead; missing evidence is unknown.

Persist aggregate reports, incident deduplication state, heartbeat receipts
and run metadata in a separate operational store. Proposed implementation:
dedicated relational quality-report/run/incident tables, with a different
principal allowed to write only those tables. The checker must not use the
operational write principal to update source observations, prompts or labels.
Report timestamps and unique period/version keys make retries auditable.

The existing active-Researcher authorization protects report reads. Never
add an unauthenticated raw export endpoint. If an optional AI reviewer
requires an endpoint, issue a separate read-only token restricted to the
minimized aggregate response; no identity mapping, events or free text.
Keep tokens out of URLs, logs, source and browser storage. Telegram bot
credentials, chat binding and heartbeat secret belong in private deployment
configuration, never in repository documentation or public messages.

## Metrics and correct denominators

Every metric declares period, cutoff, version, source, denominator, missing
coverage and sample count. Return null/unknown when evidence is unavailable.

| Check | Required calculation | Interpretation boundary |
| --- | --- | --- |
| Collection enabled | Valid active scope, period, pause/exclusion rules | Paused/out-of-period collection is expected silence |
| Eligible usage vs receipt | Independent eligible-use aggregate compared with accepted receipt activity | No activity alone proves neither failure nor health |
| Ingestion | Receipt-time accepted/rejected/duplicate/late/drop counts with aligned units | Session lifetime counters are not hourly counters |
| Delay | Occurrence-to-receipt distribution, clock support/uncertainty and late fraction | Bad client clocks are not negative latency or emotion |
| Action linkage | Logical attempts with confirmed, pending and unknown outcomes | Transport retries are not repeated behavior |
| Coverage | Observable visible time and supported feature channels by activity/device category | Background/unsupported/missing are not zero |
| Prompt delivery | Offers, grants, render acknowledgements and uncertainty | Permission does not prove an actual display |
| Response | Answers/Skip/expiry for a display cohort whose deadlines have matured | Pending prompts are reported separately |
| Reference alignment | Valid window/cutoff, response deadline, actual pre-T coverage | Label metadata/post-T observations cannot become features |
| Ratings | Raw 1-5 counts and distinct contributing participants | Ratings are self-reports; low high-rating prevalence is not a collector fault |
| Representativeness | Counts across activity, session stage, device category and participants | Thousands of rows from one person are not thousands of independent people |
| Export | Last verified snapshot, hash/manifest and decode/integrity result | A successful HTTP response alone does not verify the archive |
| Retention/backups | Configured policy, job/restore receipts and gaps | Existence of a script or volume is not successful execution |
| Model after evaluation | Participant-separated prospective labels and specified metrics | Distribution drift alone does not measure model performance |
| Help after evaluation | Exposure/refusal, annoyance, progress and learning under a comparison | Improvement after help does not establish causation |

For a display cohort C selected by actual display in [start,end), include
in the response denominator only prompts whose immutable response deadlines
have passed at cutoff. Count answers from those same prompts, even when
they arrive on another calendar day. Show pending cohorts separately.
Do not divide today's answers by today's displays or silently mix v1 grants
with v2 render acknowledgements.

Delivery counters need receipt-scoped v2 records or correctly retained
snapshot deltas. The existing dashboard sums session counters for sessions
selected by started_at_ms; those sums must not be relabeled as hourly errors.
Distinguish event counts, batch counts and client-reported loss. An outbox
that vanished before reporting cannot supply an exact lost-event count.

Technical invariant failures can alert immediately. Rate/trend alerts require
explicit minimum denominators and repeat evidence calibrated in the pilot.
Show counts and alternative explanations; do not invent scientific thresholds
such as a universal acceptable refusal percentage. Suppress sensitive small
cells from Telegram/AI, and aggregate broader categories when necessary.

Enforce evaluation partitioning in the report queries, not only in reviewer
instructions. Scientific label distributions, comparisons and recommendations
exclude locked_final_test labels and model outcomes. Technical-only health
checks may cover that partition without revealing its label mix/performance.
Use development or separately designated prospective monitoring participants
for methodology/model diagnostics. Aggregate final-test labels can also leak
information into recruitment, feature or threshold decisions.

## Alert and review policy

Incidents use a stable rule/scope/version key. Record first seen, last seen,
last notified and recovery. Retry notifications independently of report
generation; Telegram failure must not turn an unnotified incident into a
delivered one. Report generation success and notification delivery are
different receipts. The external watchdog tracks successful checker runs;
it must also expose a failed Telegram/notification path rather than implying
that successful data queries guarantee notification delivery.

Do not alert on every no-Student hour. Distinguish: technical failure,
repeated degradation, insufficient evidence, research limitation and healthy
observations. Weekly methodology review may recommend additional natural
use, recruitment or a versioned correction. Daily review does not mean daily
changes to the question or sampling policy.

No automated edits to ratings, missing data, exclusions, collection settings,
sampling, windows, provenance or model thresholds. No autonomous retraining,
deployment, retention deletion or help messages to Students. Preserve the
held-out final evaluation from repeated inspection and feature tuning.

## Retention: approved target and activation boundary

The owner selected 45 days. Existing code accepts configurable retention
within 1-3650 days; the previous 15 days is not a required schema constant.
Activation must verify the live value and daily enforcement, and create a
new collection configuration that truthfully records the new policy. Do not
edit a frozen historical configuration to make it appear approved at 45 days.

Important source limitation: the current purge selects all expired sessions
using the deployment value, not each configuration's recorded retention.
Changing the environment to 45 alone would also extend the period for older
retained sessions, even when their frozen configuration still records 15.
The activation change must resolve this explicitly. Recommended v2 behavior
is configuration-scoped expiry: new study configurations use 45 days while
historical configurations keep their recorded policy. An archive spanning
policies expires at the earliest included session expiry. This requires a
reviewed retention implementation change; it has not been made here.

Preserve the existing expiry anchor unless separately revised: session
last activity; associated events/prompts expire with it, and an archive's
expiry uses its oldest included session's last activity. The actual deletion
occurs on the next successful daily run, whose cadence must be documented.
Backup/export copies and separately retained operational aggregates require
documented expiry/access rules; exporting is not permission for indefinite
retention. The conservative target for new quality reports is also 45 days,
with no participant identifiers in report bodies.

Changing the setting cannot recover data already purged. Do not execute a
purge as part of this design/discovery task. Do not silently resume a frozen
15-day configuration under a different deployment setting. Record the
effective date, approved scope and receipt when the new policy is activated.

## Subscription coverage and provisional cost

OpenAI's official pricing/authentication documents distinguish ChatGPT-plan
usage from API-key billing. Plus includes eligible ChatGPT/Codex use subject
to plan limits; it does not pay Railway infrastructure charges. OpenAI API
calls made with an API key are billed separately through the Platform account.
Eligible participating applications can support ChatGPT-plan usage through
an approved integration; that does not automatically connect this private
Railway monitor to Plus. No such integration has been established here.

The desktop scheduling tools available in this chat create local automations,
which require the local runtime. They do not satisfy the computer-off
requirement. Cloud ChatGPT tasks require separately configured cloud access
and tools. Do not promise that this local conversation will autonomously wake
up and read private production data while the computer is offline.

Use deterministic cloud checks with AI off as the recommended first release.
Illustrative Railway compute only, using documentation fetched on 2026-10-28:

| Assumption / item | Illustration, not a measured bill |
| --- | --- |
| Documented resource rates | RAM $10/GB-month; CPU $20/vCPU-month |
| Workload example | 720 hourly runs in 30 days; average 0.25GB RAM and 1vCPU during each run |
| If each run consumes 1 minute | (0.25 x $10 + 1 x $20) x 720/43200 = about $0.38/month |
| If each run consumes 5 minutes | The same calculation with 3600 active minutes = about $1.88/month |
| Equivalent 45-day illustration | 1080 runs: about $0.56-$2.81 compute under the same assumptions |
| Suggested provisional monitoring allowance | $5/month for discussion; not an approved spend or enforced cap |
| AI/model charges in the first release | None, if no AI/API calls are enabled |

These examples use average consumed resources, not configured resource
limits. Startup/billing rounding, builds, retries, report storage/egress,
extra database load, provider plan requirements, tax and the independent
watchdog are not included. Runtime has not been measured and the account's
remaining plan allowance/bill has not been inspected. Existing web/MySQL
hosting is excluded. Added consumption may fit existing included credit;
do not promise a zero additional invoice or a fixed maximum.

Before activation: measure a bounded rehearsal, recalculate total cost,
choose the watchdog and any applicable fee, and obtain the owner's budget
decision. Do not enable a workspace-wide hard spending stop that could
unintentionally stop the LMS/MySQL. Any later AI option needs a chosen
supported integration/model, measured token budget, request limits and
failure fallback before its price can be estimated.

## Execution checklist and evidence still required

- Implement v2 measurements and the aggregate schema; obtain exact forward
  migration authorization and verify the parent before any database action.
- Implement the short runner, bounded read snapshot, independent operational
  writes, report reader and idempotent notification delivery.
- Approve cost and privately configure the dedicated DB scopes, bot/chat and
  heartbeat integration. Do not install new dependencies without authorization.
- Rehearse success, schema/DB failure, missing-run, duplicate invocation,
  Telegram timeout/recovery and daily-report catch-up using fictional data.
- Verify physical phone collection/reference/export behavior separately.
- Verify new 45-day configuration and scheduled daily expiry with documented
  backup/export retention. No reset, reseed or reclassification.
- Activate only after a new acceptance baseline; record actual service,
  schedule, timezone, secrets configured by name only, receipts and rollback.

Completed in this session: source/documentation inspection, official-source
research, read-only Railway inventory and these specifications. No database
connection/query, source runtime change, migration, dependency install,
Git mutation, deployment, scheduled job, paid resource activation, Telegram
message or automatic test was performed. Monitoring is not active yet.

Documentation verification: all three new files contain no trailing spaces;
their local cross-links resolve. Normal git diff --check exits 2 on the
working tree's CR-at-EOL diagnostics (including retained Windows line endings).
The same check with core.whitespace=cr-at-eol exits 0. These are documentation/
diff checks, not application, database, browser or regression acceptance.

## Official references

- [Railway Cron lifecycle and timing](https://docs.railway.com/cron-jobs)
  and [worker selection](https://docs.railway.com/guides/cron-workers-queues).
- [Railway pricing](https://docs.railway.com/pricing).
- [Railway observability](https://docs.railway.com/observability): resource
  monitors do not replace research-quality checks.
- [Independent expected-success monitoring](https://healthchecks.io/docs/monitoring_cron_jobs/)
  and [grace-period configuration](https://healthchecks.io/docs/configuring_checks/).
- [OpenAI plan pricing](https://learn.chatgpt.com/docs/pricing),
  [authentication/billing](https://learn.chatgpt.com/docs/auth),
  [eligible ChatGPT-plan integrations](https://developers.openai.com/siwc/quickstart)
  and [local/cloud automation boundaries](https://learn.chatgpt.com/docs/automations).
