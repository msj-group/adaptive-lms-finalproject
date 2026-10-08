# Local Student usage and Researcher export walkthrough

The owner authorized an ordinary local rehearsal before the interface redesign.
Use the existing local accounts and content. Nothing here resets data, seeds
accounts, changes historical classifications or requires automated tests.

## 1. Open the application

Use the configured local application at `http://127.0.0.1:5000/auth/login`.
If it is stopped, launch the existing environment using the Quick Start.
The Student and Researcher use the same login form. Use separate browser
profiles for concurrent use; tabs in one browser profile share login cookies.

The local deployment remains `development` provenance with 15-day retention.
Hosting for actual centre Students will deliberately use `study`. These
settings control the source classification, not a different Student workflow.

## 2. Prepare collection in the Researcher workspace

1. Sign in with the existing Researcher account and open **Configurations**.
2. Inspect the active version, its collection period and **Collecting** state.
3. If the period does not include now, create a draft, give it an informative
   label and a period covering the rehearsal.
   Dates are local `Africa/Tripoli` time. Keep the established policy values.
4. Save the draft, confirm activation and select **Activate version**.
5. Select **Start collecting**. Activation alone does not start collection.
6. Confirm the dashboard reports an active configuration collecting inside
   its period. Excluded or inactive Students are not collected.

Do not switch the deployment to study just to make local exports nonempty.
Do not shorten observation windows or force probabilities to make feedback
appear. The rehearsal uses the same collection and sampling rules as hosting.

## 3. Perform Student tasks

Use an available lesson and open activities from **Activities**. Existing
attempt limits, release windows and deadlines apply normally; an already
finished activity may require a new activity created through Teacher workflows.

| Task | Suggested use | Research evidence to review |
| --- | --- | --- |
| Dashboard and lesson | Navigate normally and read a lesson for 2–3 minutes while the page stays visible. | Page views, visible observation and allowed interactions. |
| Lesson audio | Play, pause, replay and change speed when offered. | Allowed media controls and playback progress. |
| Quiz | Open an available attempt, answer questions, navigate and submit. | Attempt interactions and server-confirmed outcome. |
| Listening | Play the task audio, answer and submit an available attempt. | Media events, question progress and confirmed outcome. |
| Speaking | Allow the microphone, record, preview and submit. | Recording-control states and confirmed submission; no audio in the research ZIP. |
| Assignment | Open an available assignment and submit through its normal form. | Form interactions and confirmed outcome; no answer content in the ZIP. |
| Messages or notifications | Read or use the available workflow normally. | Allowlisted page/control events; no message text. |

If the optional 1–5 feedback prompt appears, answer according to the experience.
It is sampled and is not guaranteed after each task. The usual policy requires
a continuously observed window; timed activities, recording and uploading may
defer display. A session can have events and no rated prompts. Missing feedback
is unlabeled, never an invented emotion or a zero rating.

Remain online and give pending events a few seconds to arrive, then log out
normally. Record the approximate start time to identify the session afterwards.

## 4. Review as Researcher

1. Return to the Researcher profile or sign in after Student logout.
2. Open **Dashboard** and **Sessions**. Match the rehearsal by its start time,
   configuration version and pseudonymous subject code, not a Student name.
3. Open its timeline to inspect accepted events, sources, durations and
   confirmed outcomes. Inspect prompt states, raw ratings and observation
   windows when present.
4. Counts should describe stored rows. Collection pauses, out-of-period use,
   exclusions and delivery failures can explain a missing session.

## 5. Create and download the archive

1. Open **Exports**, choose the rehearsal configuration version, and select
   local dates that include when the session started. Dates select whole
   sessions by start time, not individual events inside the date range.
2. Select **Create export**, review its row counts, then **Download ZIP**.
3. The ZIP contains `sessions.csv`, `events.csv`, `prompts.csv`,
   `data_dictionary.csv` and `manifest.json`.
4. The local manifest declares `dataset_kind=operational_review` and
   `filters.provenance=development`; each session keeps its original source
   (`development`, or a retained historical `demo` classification). No
   historical source is rewritten. Actual study exports declare study and
   exclude every non-study session and demonstration subject.
5. Each export is an immutable snapshot. Create another export for later
   activity; downloading an older one does not refresh its contents.

Archives obey the approved retention and expire with their oldest included
data. Download promptly. Do not mix operational-review archives into the
actual study dataset or report their rows as centre Student study findings.
Research files contain pseudonymous behaviour and optional raw feedback;
they do not contain names, emails, answers, messages, grades or audio.

## 6. Finish the rehearsal

Use **Pause collection** when the rehearsal is finished if no further local
usage should be recorded. No reset or purge is needed. Keep the reviewed source
and workflow for the later interface redesign; preserve the collector hooks
when applying the Figma designs.
