# Research v2 model and vocabulary implementation notes

Current source, 2026-10-30: local schema is installed; fictional operational
rehearsal does not supply Study/training data. The render estimate remains raw,
and the full nonempty calibrated interval intersected with G..receipt must fit
the unchanged G..G+5-second allowance. This is timing measurement metadata,
never a predictor or a retrospective relabeling. The existing 60-second deadline
is unchanged. Current acceptance: RESEARCH_V2_LAUNCH_ACCEPTANCE.md; exact timing
semantics: RESEARCH_V2_DATA_CONTRACT.md. Older preparation statements follow.

The Oct 30 correction computes terminal feedback quality before ORM terminal
assignments, preserving the immutable guard and original deadline. A new
fictional answer and its ordinary export verified stored timely eligibility;
this is acceptance evidence, not Study/training material. The quality checker
now reports stored eligibility and exclusion reasons as separate private JSON
metadata without reading rating in technical scope or changing SQL counters.
These monitoring fields are never predictors or a relabeling mechanism.

Date: 2026-10-28. Source preparation under the owner's instruction to
implement the reviewed change. These declarations do not create tables,
activate configurations, change retention or deploy a study.

`app/models/research_v2.py` maps the eleven reviewed tables using the shared
Flask-SQLAlchemy metadata and signed BIGINT identity type. Its schema names,
columns, binary collation, explicit foreign keys, unique keys, indexes and
literal CHECK expressions mirror the promoted migration revision. Its local
database application and independent schema/preservation verification passed;
see RESEARCH_V2_LOCAL_MIGRATION.md for the 2026-10-29 evidence and limits. There
are no relationship cascades or new identity mappings. The subsequent
implementation refinement records an immutable canonical application
`release_ref` on each v2 sampling-state row so historical metrics cannot
be attributed to whichever application happens to run the checker later.

The mapped classes are `ResearchV2Protocol`, `ResearchV2SamplingState`,
`ResearchV2SamplingOpportunity`, `ResearchV2Event`, `ResearchV2Prompt`,
`ResearchV2FeatureMembership`, `ResearchV2DeliveryReceipt`, `ResearchV2EvaluationPartition`,
`ResearchQualityRun`, `ResearchQualityReport` and `ResearchQualityIncident`.
Required values are explicit; the mappings do not silently manufacture
policy, timestamps, release provenance or observation defaults.

The normalized membership table freezes the exact event snapshot at grant G;
the reference ends at G, with browser render T recorded separately. Protocol
study_timezone is frozen to Africa/Tripoli. Workflow outcomes may carry a
validated opaque attempt reference; browser source fields remain forbidden
on server observations. No new column is added to an LMS business table.

Ordinary ORM writes cannot edit recorded events, delivery receipts or
quality reports. A protocol can be attached only to a matching v2 draft
configuration, and its timing policy freezes after activation. A sampling
decision is final; one later reservation is allowed only for a selected
opportunity. Prompt identity and an acknowledged render window cannot be
rewritten. Answered, skipped and expired prompts are final; a no-response
prompt can receive a raw late answer without being reclassified as timely.
Final-test partitions freeze and development exposure cannot be erased.
Quality-run scopes freeze and a running receipt can finish only once.

These are ORM safeguards, not database triggers or a substitute for the
service's authoritative locks, actor validation and transactions. Direct
SQL/bulk writers remain responsible for the same invariants. Ordinary ORM
deletion is refused; authorized retention must deliberately remove expired
dependent rows using its scoped bulk path in FK order. The models do not
provide a general deletion or retention bypass API.

`app/services/research_v2_dictionary.py` provides immutable versioned
event/context/action/detail codes, quality metrics/scopes and pure
`validate_client_event(raw)` validation. The validator returns a closed-key
copy or `None`; it rejects arbitrary payloads and server-only outcomes. Raw
times remain client times. The ingestion service owns clock correction,
receipt time, authenticated session/source and coverage interpretation.
Canonical UUIDs are correlation hints, never authorization or proof of a
committed business result. Optional fields preserve missing values rather
than manufacturing zero measurements.

Executed verification for this model/vocabulary portion: Python AST parsing,
source-level table-declaration comparison against the proposal, and literal
event/context/action/detail/quality-metric parity inspection. These checks
use no database and are not automated regression tests. Actual MySQL DDL,
constraint behavior, ORM flush guards, browser round trips and data exports
require the separately authorized integration/rehearsal steps; this note
does not claim those passed.
