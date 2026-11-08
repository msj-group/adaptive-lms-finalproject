# Collection coverage and assistance context — 2026-11-08

## Authority and timing

The owner requested implementation of collection gaps caused by recent platform
changes, with useful features for frustration detection and later assistance.
The expected graduation discussion is February 2027; 27 Students are currently
available, with possible growth. This is a planning constraint, not a statistical
power claim. Source changes are authorized. No production deployment, collection
activation, database migration/reset, scheduler change or Git mutation is performed.

The owner clarified that both phones AND computers are target collection devices.
Neither collection nor later assistance may assume phone-only use.

## Phone and computer contract

Semantic activation uses native click/change/submit events. Native buttons/links
support touch, mouse and keyboard activation; the collector deliberately does not
attach both pointerdown and click listeners or record key identities/typed text.
PointerEvent presence is an API-capability declaration, not a physical mouse test.
Detector action channels do not require that API. Unsupported/unknown channels
remain missing; they cannot serve as frustration labels.

Existing phone/tablet/desktop codes and SQL column names are preserved for schema
compatibility, but mean narrow/medium/wide viewport classes. A desktop in a narrow
window can have the phone code. The current collector re-declares a changed class
after a debounced resize, without storing dimensions or input hardware identity.
The Researcher report and export manifest explicitly state this limit.
New quality computations also order same-batch declarations by their recorded
event/page sequence; dictionary label order must not decide the viewport class.
Existing saved reports and raw observations are not rewritten.

Acceptance must include desktop mouse and keyboard-only use, a touch-capable
computer when available, Android and iPhone touch use, orientation/window-size
changes, and multiple tabs. Exercise the same reader, media, form, feedback,
delivery and export paths on each. No physical-device acceptance is claimed here.
Review label yield/delivery and eventual model performance across these journeys.
All devices/sessions of one participant stay in the same train/test partition.
Viewport analysis alone cannot certify hardware/input fairness; model evaluation
must account for the capabilities and actual journeys represented in the study.
Native activation semantics are documented by
[MDN's click event reference](https://developer.mozilla.org/en-US/docs/Web/API/Element/click_event).

## Coverage corrections

| Surface | Finding and current correction |
| --- | --- |
| Workbook | New HTML routes map to the existing reader code; legacy aliases preserve v1 vocabulary. Images/media remain explicitly excluded byte streams. |
| Continuous Unit reader | The visible Student reader is recognized alongside the older atlas reader. |
| Unit completion | The transaction owner supplies server-only Unit scope: one reader/complete outcome, no invented sibling Lesson events. |
| Async navigation | The native page loader begins/reuses the reserved POST attempt, copies correlation fields, and finishes before disposing the old scope. |
| Reader/media controls | Outline, pins, navigation, fullscreen, seeking and speed changes use existing closed semantic codes. |
| Media continuity | Only actual playing reports buffering recovery. Visible-tab resume starts a fresh interval without bridging hidden time; context switches close the old interval first. |
| Researcher yield | Current seven-day eligible-answer/participant counts and sampling reasons supplement saved quality reports. |

Main-book HTML retains page_code=lesson; Workbook HTML resolves to page_code=reader.
context_code=reader describes either visible reader. These categories are not
content, physical page identities or proof of reading. No text, values, URLs,
coordinates, filenames or recordings are added to research collection.

The server-only Unit scope comes from note_outcome(unit_completion=True) in the
existing Unit transaction branch. Client hints cannot establish this scope.
Original feature-v1 Lesson-progress definitions remain; Unit completion is a
separate candidate in the new context payload. HTTP completion is transport
evidence, never an authoritative academic outcome.

## Additive export

Original features.csv, labels, 120-second membership/window, sampling probability,
spacing and burden limits, response deadline, retention and partition quarantine
remain in force. The two additional files are declared and hashed in the manifest.
The installed ZIP format and original required files are preserved.

context_features.csv contains recent-use-context.v1, computed from the SAME frozen
pre-grant members/cutoff as detector features:

- Visible reader fraction, observed media fraction, playing fraction and visible
  fraction without observed media. The latter does not measure reading/attention.
- Distinct open, close, navigate, retry and seek intentions.
- Playback-speed changes and confirmed Unit-completion transactions.
- Missing reasons, denominators and explicit instrumentation support.

Occurrence and receipt must precede grant; unknown/uncertain client clocks are
excluded. Coverage/support failures produce missing values. Only the writer's
exact immutable source release is accepted as known instrumentation support.
Other releases produce missing context values with instrumentation_revision_unknown,
never invented zeros. A future release needs deliberate compatibility review.
The manifest identifies supported source and extension version; it cannot restore
historical missing observations or retrospectively relabel a session.

assistance_preferences.csv separately exposes already stored optional help
preference, question exposure and cause flags. No question or exposure policy is
added. Null preference means unknown; an unchecked cause does not prove absence.
These questionnaire outcomes must never enter detector predictors. Ordinary v2
source/partition filtering happens before either additional file is constructed;
unassigned and reserved-final-test participants remain excluded from all files.
Consumers must select declared predictor fields, never blindly join every CSV
column into model inputs. Identifiers/source/partition remain metadata.

## Current yield

Collection quality includes a read-only seven-day SESSION-START cohort for the
active v2 configuration and server-selected source scope. Counts cover selected
sessions through the read cutoff: server-prepared/client-observed sessions,
reserved/rendered questions, raw/eligible/late answers, eligible people, Skip,
unassigned participants and opportunity decisions/reasons. No rating column,
account mapping, content or new report write is used. Technical counts cover all
evaluation groups without releasing final-test labels. These are yield indicators,
not sufficient-sample or model-readiness acceptance.

## Assistance and deadline decisions

No adaptive treatment is delivered by these collection corrections. Preferences
and behavior can support help selection, but cannot establish which help improves
learning or reduces frustration. Intervention evaluation must separately record
model/policy version, eligible moment, offered help type, render, accept/decline/use,
pre-intervention context, follow-up outcomes and an appropriate comparison.
Intervention observations must stay distinguishable from detector-baseline data.
Resolve current Lesson/question and permitted resources through ordinary server
authorization at runtime; never expose Teacher books, answer keys or raw Student
answers in research features or generated assistance.

With February approaching and 27 potential participants, inspect actual yield
from the first usage days while continuing ordinary use. Do not collect raw events
for weeks before reviewing eligible labels across people. Begin simple baselines
with participant-separated development evaluation as soon as relevant classes
exist; keep the final test closed. Rare classes, weak signals or poor response
yield require a documented decision, not relabeling or arbitrary probability
changes. Current sampling is retained pending that evidence.

Before a source deployment, pause collection through its normal workflow, close
old sessions, then review/resume the intended configuration. Session source digests
are immutable and existing sessions cannot silently change source. No schema
migration is required by this change.

## Verification boundary

Bounded static review covers Python AST, JavaScript syntax, localized Jinja,
JSON/catalog duplicates, Student-route classification, export/source review and
CRLF-aware whitespace. No automated application tests, MySQL queries, authentication
or business writes, real export, physical-device review, live collection or release
is claimed. End-to-end acceptance still needs an authorized runtime and actual
Android/iPhone delivery, main/Workbook reader, explicit Unit completion, async
save/send/upload and export round trips. Endpoint classification is not proof
that every signal/control/delivery path works on a real device.

Executed static results: 9 changed/new Python sources parsed; 3 changed JavaScript
sources passed node --check; 21 Researcher/shared-card templates parsed through
the actual OwnedUIExtension; 5,192 Arabic catalog entries parsed with zero duplicate
keys. All 54 statically declared Student endpoints have an explicit classification.
CRLF-aware git diff --check returned exit 0 with line-ending notices only. These
results are source/markup checks, not executed behavioral or database tests.

Desktop clarification follow-up: the collector passed JavaScript syntax review;
the two changed export/quality Python modules passed AST parsing, the localized
quality template parsed, and the catalog now has 5,194 entries with zero duplicate
keys. CRLF-aware diff review passed. No actual desktop/phone behavioral check,
database operation, automated application test, staging/commit or release ran.
