# Version A local source/UI baseline freeze

Decision date: 2026-10-08. Baseline identifier: **YC-VA-D1-20261008**.
Authority: the final consolidated owner approval delegates a justified freeze
after the approved acceptance gates. This records the reviewed local runtime/UI
baseline. It does not approve an unverified production host or activate Study.

## Frozen record

| Item | Value |
|---|---|
| Visual system | Direction D.1 — Branded Living Learning Experience |
| Motion | D-MOTION.1; deterministic/nonadaptive/reduced-motion compatible |
| Protected Login | Original composition/artwork/type/red CTA; scoped compatibility; fake Opening wait removed |
| Messages | Existing feature/routes/workflows evolved into D; presentation-only mobile action sheets |
| Architecture | Flask modular monolith + Jinja + WTForms + SQLAlchemy + MySQL |
| Runtime source | 619 allowlisted runtime/assets/operator/deployment files |
| Runtime aggregate SHA-256 | `77f2ff626775cf6443a53ec37d95dce385476c9d6e87dbd611314a15f52c70b3` |
| Migration head | `7d4e2a9c6013` |
| Event wire schema | `natural-use-events.v1`; existing 26 event types |
| Dictionary revision | `va-w7-r1` |
| Tracking scope | `va-scope-r1`; 37 page IDs, 86 allowlisted element IDs, 19 form IDs |
| Export layout | `natural-use-csv.v2`; unchanged CSV columns; additive generator revision notice |
| Pilot | Separate v4 operational-review configuration; Development deployment; historical fictional subject stays demo |
| Collection at close-out | Paused; no production Study activation |

`VERSION_A_BASELINE_SOURCE.json` is the machine-readable record: every runtime
path/SHA-256, event/page/element/form allowlist, aliases/exclusions and direct
template research bindings. Hash definition: SHA-256 of the UTF-8 compact,
sorted-key JSON path-to-file-SHA256 runtime map. Documents, data and Git metadata
are excluded to avoid self-reference. `release-manifest.json` separately hashes
all packaged files, including documentation and the baseline record.

The starting tree already had extensive uncommitted work and indexed deletions.
This is a working-source inventory, not a Git HEAD claim or attribution of all
changes to this delivery. No commit or push was created. The release builder
refuses a runtime that differs from this record; it does not silently replace
the baseline hash. Keep the identified release for reproducibility.

## Gate decision

Final D UI is implemented across the relevant roles; protected Login and evolved
Messages honor their reviewed exceptions. Final tracking/dictionary/stable hooks
are documented. Critical native learning/communication/media/Account and selected
Teacher/Admin writes passed. Text/File finality, authorization, signed context,
deadline and cleanup passed. Four-role responsive/theme/RTL/keyboard acceptance
and bounded contrast passed. Actual MySQL migration/empty reconstruction/restore
and instrumentation delivery/export passed. No known critical defect remained
in this reviewed scope when this record was made.

`VERSION_A_ACCEPTANCE.md` states exactly what was source-reviewed, rendered,
browser-exercised and not verified. Native screen-reader/cross-browser/physical
microphone coverage remains limited; this freeze does not represent WCAG
certification, exhaustive regression or native browser-zoom acceptance. Those
limits are explicitly recorded and do not hide a known failing critical flow.
Any later actual failure must be evaluated and corrected with a change record.

The Pilot used the existing policy without forced sampling: inactivity 30 min,
max one/session and two/day; lookback/minimum observed 120 s; random 50‰,
activity-end 500‰; offer TTL/response window 600 s; raw rating 1–5; daily 15-day
retention. The separate period was 2026-10-08 00:00–2026-10-09 23:59 local.
One pseudonymous subject/two sessions/86 stored events/one raw answer 3 were
verified. Final Pilot archive SHA-256:
`60729e31614f3ead2a9133d1d1fa929617e45eefb5049f07ab8be99859cf698c`.
Its historical demo provenance is preserved and dataset_kind is operational_review.
No Development/Pilot/demo traffic may be reclassified as Study or shipped.

## Stable interpretation

- Global Search position is the actual rendered cross-group ordinal 1..500;
  above 500 absent. Ranking/grouping unchanged. Older pre-W7 positions may reset
  by group; the generator notice does not relabel them. Use a new post-freeze
  configuration for homogeneous analysis.
- Quiz autosave observes input changes and actual server acknowledgements;
  failed unconfirmed automatic attempts are not separately counted as native
  form submits. Capture ordering/resumed submission preserves one intention.
- Account and communication instrumentation never collect passwords, field
  values, bodies, subjects, recipient names, filenames or file/photo contents.
- Survey is the existing optional non-modal card, neutral heading/no selected
  rating, respectful focus return; sampling/window/rating are unchanged.
- Repeated-click final burst is delivered idempotently before pagehide/hidden/
  submission. Browser termination/network loss remains partly unknowable.
  Explicit transport replay counters have the documented idempotency limit;
  no missing rating equals zero and no self-report is a psychological diagnosis.
- Version A is shared Core + shared instrumentation. No adaptive inference,
  spotlight, conditional help, A/B allocation or Version B runtime exists here.

## After this decision

Material experimental Student UI/workflow/instrumentation changes require a
dated change record, new version/identifier, source inventory, research-impact
evaluation and appropriate acceptance before collection resumes. Record affected
pages/hooks/semantics, prior/new versions, configuration/data boundary and owner
decision where new methodology/domain policy is involved. Preserve old archives.
Do not regenerate this identifier silently. Ordinary operational data does not
change a source baseline. Changes to hosting settings alone still require their
own deployment acceptance and must not change research methodology implicitly.

Actual host build/TLS/proxy/private storage/scheduled retention/backup restore/
production smoke and external participation/eligibility arrangements remain gates
in `VERSION_A_READINESS.md`. A clean production Study configuration starts only
through the deliberate procedure in `VERSION_A_RESEARCH_OPERATIONS.md`.
