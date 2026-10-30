# Collection v2 launch acceptance ledger

Rehearsal dates: 2026-10-29 and 2026-10-30. Local handoff checkpoint: 2026-10-30.
Phase 6 remains open. The temporary local rehearsal was paused on Oct 29.
On Oct 30, collection was deliberately restarted through the ordinary UI for
a second local fictional session. A later answer-save failure stopped that
review. Collection was paused again at 14:48:10 Tripoli and both sessions
were confirmed closed by a read-only check. After the save correction and
source review, the server was restarted and collection was deliberately
restarted through the ordinary UI for a third local session. Collection is
now paused again after a fresh natural question was answered and saved within
the deadline. All three sessions are confirmed closed, and the ordinary
timely-answer, export and final-pause gates have passed locally. Monitoring
source review, bounded checks and a real read-only label-count snapshot have
also passed. External monitoring delivery and hosted publication remain open.
This ledger does not approve hosted execution, paid monitoring, Study
activation or main data collection.

## Authority and environment

The owner confirmed that all 81 local Student accounts are fictional and
authorized temporary local collection for ordinary Student/Researcher review,
followed by pausing collection. Monitoring is preparation only: no paid
resource activation. Preserve existing data and unrelated Finance changes.

The local rehearsal used http://127.0.0.1:5127 for Researcher and
http://localhost:5127 for Student, with process-only RESEARCH_V2_ENABLED=true,
retention 45 and development provenance. The .env file was unchanged. The
owner signed into both roles through the ordinary shared /auth/login workflow.
On Oct 30, the local server was restarted on port 5127 with the same process
overrides. Existing login cookies remained valid; no new login was needed.

## Completed evidence at this checkpoint

| Area | Actual evidence | Boundary |
| --- | --- | --- |
| Local schema | Exact a6f2c9d84e71 verified; original migration/runtime privileges restored | Earlier exact execution: RESEARCH_V2_LOCAL_MIGRATION.md |
| Versioned setup | Configuration 3, V2 local acceptance - fictional development only, Oct 29–31, activated with 45-day retention | Previous v1 configuration retired; its recorded 15-day policy preserved |
| Evaluation group | The sole fictional participant was assigned development before any labels | No final-test or Study classification fabricated |
| Ordinary Student use | Dashboard, Courses, Lesson, one lesson completion, activity filtering, Listening page and audio Play | One authorized fictional completion write; no reset or seed |
| Action linkage | Server lesson completion was committed and its action_ref matched the client action | Business confirmation is separate from HTTP delivery |
| Final Oct 29 event snapshot | 1 fictional participant, 1 session, 174 accepted events, 0 invalid events, 0 late events | Event timeliness does not imply that a survey answer was timely |
| Independent startup evidence | 1 matured server-presence session with accepted client observation; no v2 expiry backlog or critical issue at the earlier snapshot | Does not establish model accuracy, complete coverage or a successful scheduled purge |
| Natural sampling | A prompt was naturally reserved and rendered under the unchanged sampling policy | No forced question or modified probability; its timing exposed the corrected issue below |
| Fictional answer | Raw rating 3 and Help Yes were recorded after 70,838 ms, with quality flag late_answer | This is a late fictional answer, not an accepted training label |
| Frozen window | 166 frozen member events; none had occurred_at or received_at at or after the server anchor G | Window membership was checked separately from render timing |
| Student authorization | Student access to /research/v2 returned 403 through the browser | Manual role check only |
| Local interruption | The owned local server was stopped for about 23–34 seconds and restarted with the same code; an event created during the outage arrived with a 30,473 ms delay | Server interruption rehearsal, not proof of offline delivery on a physical phone |
| Narrow layout review | Widths 390 and 320 had no horizontal overflow; reviewed console warnings/errors were empty | Desktop viewport emulation, not a physical phone test |
| Local schema verifier | Read-only _verify_schema with newly_applied=False passed on local MySQL after correcting its handling of alembic_version, which is outside application metadata | The main predeploy command and its DDL path were not executed |
| Oct 29 rehearsal close | The session was closed and collection was paused through the ordinary Researcher UI, then confirmed by a read-only MySQL check | The paused state was checked again before the Oct 30 restart |
| Earlier source checks | 9 Python ASTs, 18 Jinja templates, 2 JSON files, JavaScript syntax and scoped diff checks passed | Historical scope; the Oct 30 correction checks are recorded separately below |

The prompt exposed a render-timing issue. Its estimated render time E was
64 ms before G, with uncertainty u of 173 ms; the server received the render
receipt R 91 ms after G. Comparing
the estimated point alone could treat an uncertain render as preceding the
window boundary. The alignment correction in four source files is complete and
passed independent peer review with no blockers reported. It preserves E and
the historical raw answers and labels; the 60-second answer deadline and
120-second lookback remain unchanged. The Oct 29 late answer is not an
accepted training label.

## Oct 30 continuation

Configuration 3 was restarted through the ordinary Researcher UI. Normal
Student use of the Calendar page created a second session. Its source release
digest differs from the first session, as expected after the correction; no
release mismatch error was observed.

A second natural question was rendered and answered with fictional rating 2
and Help No. The answer was late again at 81,068 ms, with quality flag
late_answer. For this question, E was 61 ms after G, uncertainty u was 13 ms,
and R was 83 ms after G. This is evidence of the corrected render path, not
an accepted timely training label. At that point, no such label had been
demonstrated.
The next attempt waited for a real question while preserving the existing
ten-minute spacing and sampling policy; it exposed the save blocker below.

The existing Skip button was moved into the sticky question header. A real
new natural question at width 320 showed Skip visible in
instance/research_v2_acceptance_20261030/natural-question-corrected-320.jpg.
This remains desktop viewport evidence, not a physical phone check.

A read-only technical snapshot covering 24 hours passed with two distinct
release groups: the old session had 173 client events and 1 server event, and
the new session had 36 events at that moment. It reported 0 invalid events,
0 late events and 0 clock flags under the raw clock-bound checks, with explicit
semantics metadata. No quality report was published to the database. These
counts describe the snapshot taken before the second session was paused.

### Answer-save blocker and confirmed pause

A third natural prompt was granted and rendered at 14:47:28 Tripoli. An
ordinary UI answer at 14:47:42, followed by Try again at 14:47:46, both
returned HTTP 503 with ResearchDataError. The UI correctly said that it could
not confirm saving. This was an attempted prompt answer within the time
limit, not a successfully saved or accepted label.

Collection was paused through the ordinary Researcher UI at 14:48:10. The
paused screen is recorded in
instance/research_v2_acceptance_20261030/paused-after-response-error.jpg.
A read-only MySQL check then confirmed configuration 3 collecting=False,
session 37 closed with 174 events, and session 38 closed with 58 events. Both
sessions had 0 invalid events and 0 late events. The third prompt remained
rendered/not_assessed, with a null rating and no persisted response; no partial
answer was saved. Its frozen membership had no events outside the G boundary.
Evidence is recorded in
instance/research_v2_acceptance_20261030/paused-response-error-check.json.

Source review identified an autoflush ordering problem: a membership query
flushed the prompt's answered state before its quality fields were assigned.
The later flush was correctly blocked by the terminal-state immutability
guard, and the surrounding transaction rolled back. The correction in
research_v2_collection.py now reads before making the terminal-state change
and applies one complete answer transition. It was implemented, reviewed and
passed Python AST validation. The immutability guard remains unchanged, and
old responses must not be replayed to demonstrate acceptance. The server was
restarted with the corrected source; collection was then restarted through
the ordinary UI for a third local session. A fresh natural question and save
review subsequently succeeded as recorded below. No automated tests, schema
changes, Git changes or cloud operations were performed for this finding.

### Fresh timely answer saved and collection paused

The fourth natural prompt was answered through the ordinary Student UI with
fictional rating 2 and Help No. The response lag was 31,123 ms. A read-only
check confirmed answer_timely=True and quality eligible. E was 76 ms after G,
uncertainty u was 22 ms, and R was 89 ms after G. This was a fresh question on
the corrected source, not a replay of the failed answer. The actual save
therefore exercised the corrected read-first atomic transition successfully.

The ordinary Researcher Pause collection action was then used. A read-only
MySQL check confirmed configuration 3 collecting=False and all three sessions
closed: session 37 had 174 events, session 38 had 58, and session 39 had 77.
All had 0 invalid events and 0 late events. No frozen window member fell
outside the G boundary. Evidence is kept in the Oct 30 directory as
final-timely-answer-check.json, timely-answer-confirmed-320.jpg and
final-paused-after-timely-answer.jpg. The final export also passed as recorded
below. The saved timely answer does not establish model accuracy or useful
help outcomes.

Correction checks passed: 4 Python ASTs, 1 Jinja template, a single Skip
control, JavaScript syntax and diff checks. No automated application tests or
pytest were run; these checks do not provide comprehensive regression
acceptance.

## Ordinary export review

The ordinary Researcher export workflow created archive
ff8418cb-a5d7-4ce4-a85f-f81a0e99f517. This first snapshot contained 1 subject,
1 session, 113 events and 0 prompts. Its provenance was operational review
and its recorded retention was 45 days. The earlier snapshot explains why its
event count differs from the final 174-event session above.

Two actual browser downloads were identical, with SHA-256
f29590b52d00c9380397e3f7a82b1b7ef68745003839a6890e737f3ba7360847.
All 9 CSV/JSON archive entries matched their manifest hashes, and the column
review found no direct identity columns. This was an operational-review
export, not a Study export.

The Oct 29 post-answer export was subsequently created through the ordinary
UI as archive 1f8d8cd5-7eca-459c-b07f-b6edd70f5d39. The downloaded ZIP had
SHA-256 edd1d6103b6e78663e1154dd9ab94222026488697d7e9b14f22bee250c886788
and contained 1 subject, 1 session, 174 events, 1 prompt and 166 window member
records. All manifest hashes matched. The raw rating 3 and Help True remained
present with answer_timely=False, quality late_answer,
primary_label_eligible=False and a blank derived target. Predictor feature
values contained none of the rating, help answer, response or identifiers.
This verifies the late-answer exclusion in that archive.

The closed Oct 30 session was exported through the ordinary UI as archive
bcdd2b7f-35f7-4baa-824e-70d85ef7a4bb. The actual browser download had SHA-256
56ca90e97cf3cbe1927d64d0d8c03c7cf67fe5ea0fc6ff20a25ac6edc5f76480.
ZIP integrity and all manifest hashes passed. It contained 1 subject,
1 session, 58 events, 2 prompts, 79 window member records and 2 feature
records, with 45-day retention and operational_review provenance. The earlier
rating 2 and Help No retained quality late_answer and a blank target. The
failed prompt remained raw rendered and derived no_response at the export
cutoff, with null rating and response and a blank target. This confirms that
the failed save did not produce an exported answer or training target.
Evidence, including failed-response-export-check.json and the ZIP, is kept
in the ignored Oct 30 evidence directory.

The final ordinary UI export, archive
2fb94eaf-31c3-4173-af5a-499d53f33147, used the Oct 30 date filter and
configuration 3. The downloaded ZIP had SHA-256
e91d7cfbbb01ec42e6bffc75ab279d44ae1b10a27e1b7257e07129ce654d5266.
ZIP integrity and all manifest hashes passed. It contained 1 subject,
2 sessions, 135 events, 3 prompts, 155 window member records and 3 feature
rows, with 45-day retention and operational_review/development provenance.
No window member had occurred_at or received_at at or after G. Feature
values had no forbidden rating, help, response or identity keys.

Exactly one new answer was eligible, timely and quality eligible: fictional
rating 2, Help False and derived target 0. The older late rating 2 was excluded
with a blank target. The failed prompt remained unanswered, derived
no_response and had a blank target. This verifies a saved eligible answer and
the separate exclusion of late and failed responses through the ordinary
export path. Evidence, including final-eligible-export-check.json and the
downloaded ZIP, is kept in the ignored Oct 30 directory. The ordinary local
answer-save, export and final-pause checks are complete.

Rehearsal screenshots and JSON evidence are kept in the ignored local
directories instance/research_v2_acceptance_20261029 and
instance/research_v2_acceptance_20261030.

Source correction also preserves an existing optional rate incident when its
alert setting is disabled, avoiding an unsupported recovery notification.
Actual cloud alert and recovery delivery have not been exercised.

## Release and monitoring preparation

The revised predeploy and release builder are prepared. Only the read-only
schema verifier described above was run; the main predeploy command and DDL
path were not executed. No cloud deployment or Git mutation occurred for
this work.

The filtered source candidate is privately prepared at
instance/research_v2_release_candidate_20261029T173101Z/source, based on
4544d56915b7e321921f9ea652ba9b6b9c86e357. Its final handoff inventory contains
73 Research overlays, 919 source files, 846 unchanged base paths, 826 runtime
files and 49 migrations with head a6f2c9d84e71. Its runtime fingerprint is
201ead4f8d0fd95a320800cbb1fd56d50404d3d222f0f55bf23d14542b525c37.
This includes the answer-save and monitoring corrections. The candidate-review
record and independent-review-final.json in the candidate directory record the
exact captured bytes and root verification. Earlier refresh receipts are kept
privately as history. This is a concrete source proposal: no approved baseline
freeze, release ZIP acceptance, Git publication or deployment. Runtime hashes
do not turn the local fictional review into actual-phone or Study acceptance.

The Oct 30 read-only Railway recheck found the same two online services, no
cron service or pending changes, and unchanged source pin 4544d56. Quality
monitor variables were absent. The hosted proposal is prepared in
docs/RESEARCH_V2_HOSTED_RELEASE_CONTRACT.md; preparing it did not authorize or
perform hosted writes.

Server-presence monitoring detects missing initial client delivery after its
startup grace. It cannot detect every later silence after an earlier accepted
client event in the same session. Retention backlog monitoring covers v2
sessions and their recorded policies; a separate retention success heartbeat
is required to establish that the daily job ran. Neither that heartbeat nor
an actual purge has been exercised here. The owner requested monitoring
preparation only and declined paid activation at this checkpoint.

### Local monitoring preparation verified

The monitoring source improvement was completed after the confirmed pause,
without schema changes. Two Python AST checks and independent source review
passed. Incident assessment uses a fresh time after snapshots, rather than
comparing their later cutoffs with the runner's earlier start time. Private
JSON summaries now separate, per group,
matured prompts, raw answers, timely and late answers, stored eligible answers,
and exclusion reasons. These technical counts do not read rating values. The
existing Researcher SQL workspace counts remain raw counts, not eligible-label
counts; no database report was published.

The actual read-only snapshot passed and was saved as
instance/research_v2_acceptance_20261030/technical-final-label-yield.json.
The original three source release groups were kept separate:

- The original Oct 29 group had 1 raw answer, 1 late answer, 0 eligible
  answers and 1 late-answer exclusion.
- The session 38 group had 2 matured prompts, 1 raw answer, 1 late answer,
  0 eligible answers, 1 late-answer exclusion and 1 unanswered prompt.
- The session 39 group had 1 matured prompt, 1 raw answer, 1 timely answer,
  1 eligible answer and no exclusions.

Across the groups there were 3 raw answers, 2 late answers, 1 eligible answer
and 1 unanswered prompt. Every group had fewer than 5 matured prompts, so
each assessment was insufficient_evidence and its eligible fraction was null.
The monitor did not turn a small sample into a success signal. It reported
0 critical technical issues; this does not certify data quality or model
readiness.

The optional zero-eligible-answer investigation uses two distinct,
non-overlapping, populated, completed Tripoli days, each with at least
5 matured renders and no eligible answers. Recovery requires two comparable
days with eligible answers. Missing data, a small sample or disabling the
check preserves an existing incident and must not report recovery. These
thresholds are provisional; sparse groups may never trigger the investigation,
so the rule is not a guarantee of useful training data. No scheduled runner,
notification, heartbeat or purge was exercised. External alert failure and
recovery delivery remain pending, and no paid service was activated.

## Actual-phone review plan

The owner confirmed that both Android and iPhone are available. The following
review is planned and has not been executed. It does not replace the hosted
release and backup approvals above. Use the exact reviewed deployed candidate
with development provenance and a fictional Student account. Keep Researcher
access available for the follow-up checks.

1. On each phone, sign in normally and use ordinary pages, an audio activity
   and a form. Record the phone platform, browser and OS versions, source
   build and review date, without names, account details or other identities.
2. In narrow portrait view, check that the question and its Skip button are
   visible and reachable. Open the keyboard on a normal form and check that
   it does not hide the controls or cause horizontal overflow.
3. During normal page use, put the browser in the background, return to it,
   then briefly disconnect and restore the network. Check that the page
   resumes and that an unsent action is not reported as saved. Record any
   error and its time; background time is not evidence of active use.
4. When a question appears through normal sampling, answer within 60 seconds
   and check the visible save result. If no question appears, record that
   outcome and continue ordinary use; do not force a question, change the
   probability or device clock, or fabricate activity or coverage.
5. In Researcher, check the matching session's delivery, gaps, answer quality,
   label eligibility and frozen window. Download its ordinary operational
   review export and compare the result with the actions actually observed.
   A visible answer alone does not prove that it was accepted for training.
6. Record pass, failure or not observed for each phone. Preserve existing
   records and caches; do not reset, delete or clear data for the review.
   Resolve failures and record the small pilot decision before main Study
   collection begins.

## Remaining gates and live stop point

1. Obtain concrete source/publication approval for the filtered candidate.
   Use its final inventory and fingerprints, preserving the unrelated worktree.
   The ordinary final export, its eligible answer and exclusion checks have
   passed locally, as have the monitor source review and actual read-only
   counts. Retain their evidence with the confirmed paused state.
2. Review actual Android/iPhone behavior, including background/resume, network
   interruption, forms and audio, using the plan above. Both phones are
   available, but this review has not run. The layout checks do not replace it.
3. Accept the dedicated monitor, alert/recovery delivery, missed-run alarm,
   retention heartbeat and secure Telegram configuration. Prepare costs and
   obtain separate approval before any new paid activation; the current owner
   instruction is preparation only.
4. Verify the named hosted parent and backup, approve the final research-only
   source/Git/publication operation, then verify the deployed candidate. Do not
   publish the mixed working tree or infer hosted readiness from local success.
5. Complete a small consented adult pilot and record its decision before main
   collection or training. No model accuracy or useful-help result is claimed.

No reset, purge, hosted migration, cloud paid activation or Git operation was
performed in this rehearsal. The prepared source handoff does not close
the actual-phone, hosted, monitor-delivery or pilot gates. The corrected-source
third session ended with a successfully saved timely answer and a confirmed
pause. Phase 6 and launch acceptance remain open.
