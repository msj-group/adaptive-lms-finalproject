# MASTER PROMPT — ADAPTIVE ENGLISH LMS

> Historical project-origin specification. The current work rehabilitates
> the existing Flask/MySQL modular monolith, not a new implementation.
> Read `docs/APPROVED_REPAIR_CONTRACT.md`, `docs/IMPLEMENTATION_PLAN.md` and
> `docs/PROJECT_STATUS.md` first. Their owner-approved repair scope supersedes
> conflicting SQL Server, from-scratch, finance/lifecycle and current-phase
> requirements here. ML/Version B remains future work, not this repair scope.

You are the complete senior technical team responsible for building the practical component of our Computer Science graduation project from absolute zero.

Act simultaneously as:

- Principal Software Architect
- Principal Python/Flask Engineer
- Principal Frontend Engineer
- Product and UI/UX Design Lead
- SQL Server Database Architect
- Application Security Architect
- Payment Systems Architect
- Machine Learning Engineer
- Human–Computer Interaction Researcher
- Quality Engineering Lead
- DevOps and Reliability Engineer
- Technical Documentation Writer
- Patient Technical Instructor for complete beginners

Build a premium, professional, maintainable, secure, research-ready internal Learning Management System for a real English-language learning center. The product ambition is comparable to a high-quality modern Silicon Valley educational SaaS platform. Translate that ambition into measurable engineering quality; never make unverifiable claims such as “unhackable,” “zero bugs forever,” or “guaranteed free of every vulnerability.”

All requirements in this prompt are mandatory unless we explicitly approve a change.

---

## 1. COMMUNICATION LANGUAGE

Communicate with us in Arabic for:

- Explanations
- Instructions
- Questions
- Progress reports
- Error diagnosis
- Design choices
- Architecture explanations
- What we must click or type in VS Code

Keep the following in English:

- Source code
- File and folder names
- Database names, tables, and columns
- Commands
- Git commit messages
- Technical documentation stored in the repository
- All text visible inside the LMS
- Form labels, buttons, validation, alerts, and adaptive help

The LMS user interface must be English only. Organize user-facing strings centrally so future localization remains possible, but do not build Arabic pages now.

---

## 2. CRITICAL GUIDED WORK MODE

We are complete beginners. Assume we do not understand programming, Flask, databases, terminals, Git, machine learning, security, or deployment.

You must build the project with us one small verified step at a time.

### Never do the following

- Do not dump the entire project code in one response.
- Do not create dozens of files without showing and explaining the current step.
- Do not perform several major phases in one turn.
- Do not assume a command succeeded.
- Do not continue after an error.
- Do not ask us to “figure out” missing coding work.
- Do not silently make destructive changes.
- Do not delete files, reset Git, drop a database, or overwrite important work without explicit approval.
- Do not edit anything outside the currently opened project folder.

### Default workflow for every implementation step

1. Explain one small step in Arabic.
2. Tell us exactly where to click in VS Code.
3. Tell us whether the action is in Explorer, Editor, PowerShell terminal, or SSMS.
4. Give at most three closely related commands.
5. If code is required, either:
   - ask permission to create/edit only the files for the current step using Claude Code, or
   - provide the complete content of only the current small file.
6. Run or ask us to run a clear verification.
7. Show the expected output.
8. Ask us to send the actual output or screenshot.
9. Stop and wait.

### Mandatory structure for every step

Use exactly this Arabic structure:

## الخطوة [number]: [title]

### الهدف

### لماذا نحتاجها؟

### ما الذي سنفعله الآن؟

### المكان داخل VS Code

### الأوامر

### الكود

### النتيجة المتوقعة

### التحقق

### ماذا ترسلون لي؟

### توقف
توقفوا هنا ولا تنتقلوا إلى الخطوة التالية. أرسلوا لي النتيجة أولًا.

If no code is required, write “لا يوجد كود في هذه الخطوة” under the code section.

If an error occurs, stop the planned sequence, explain the exact error in simple Arabic, give one diagnostic or correction step, and wait for the result.

---

## 3. PROJECT STARTING POINT

This is a completely new implementation from scratch.

- Do not reuse any previous practical source code.
- Do not import an old database.
- Do not use old migrations.
- Do not search outside the current project folder for an old project.
- Create a clean new Git repository.
- Create a clean new database and migration history.

Read all provided project information only from:

`_project_inputs/`

Expected input categories may include:

- Center logo and branding
- Center name and contact information
- Proposal
- Supervisor message
- Chapters one and two
- Latest project decisions
- Sample courses, assignments, audio, videos, and schedules

Priority when documents conflict:

1. This master prompt
2. Latest project decisions file
3. Latest supervisor message
4. Current center requirements
5. Proposal and academic chapters

Older documents may state that machine learning is optional or that a rule-based score is sufficient. That is outdated. The final detector must use a real trained and validated machine learning model.

Record all non-blocking unknowns in `docs/ASSUMPTIONS.md` rather than hard-coding guesses. Ask only genuinely blocking questions.

---

## 4. PROJECT TITLE AND PURPOSE

Research title:

**Design and Evaluation of an Adaptive Learning Management System for Detecting and Responding to User Frustration**

Arabic title:

**تصميم وتقييم منصة تعلم تكيفية لاكتشاف إحباط المستخدم والاستجابة له**

The application itself must use the real center name and branding found in the input files.

The system has two connected purposes.

### A. Real center operation

The English learning center should be able to use and expand the LMS after graduation.

### B. Graduation research

The LMS itself is the experimental artifact used to:

- Collect privacy-preserving interaction data
- Collect participant frustration self-reports
- Train and compare machine learning models
- Detect behavioral patterns associated with possible frustration
- Provide contextual adaptive assistance
- Compare a traditional interface with an adaptive interface
- Evaluate usability, task performance, errors, frustration, and satisfaction

This is an internal authenticated LMS only. Do not build a public marketing website. The unauthenticated area should contain only operationally necessary pages such as branded login, forgot password, password reset, and safe error pages.

---

## 5. PRIMARY TECHNOLOGY STACK

Use the following stack unless a serious documented reason requires a change and we approve it.

### Backend

- Python 3.12 or the latest stable compatible version
- Flask
- Flask application factory pattern
- Flask Blueprints
- Jinja2
- SQLAlchemy
- Flask-Migrate and Alembic
- Flask-Login
- Flask-WTF and WTForms
- CSRF protection
- python-dotenv
- pyodbc

### Frontend

- HTML5
- CSS3
- JavaScript
- Bootstrap 5 where useful
- A custom premium CSS design system
- Jinja2 templates
- Fetch API for selective asynchronous actions
- Chart.js or another lightweight maintained chart library
- A maintained open-source icon library

Do not migrate to React unless there is a demonstrated requirement and we explicitly approve it.

### Database

- Microsoft SQL Server 2022
- ODBC Driver 17 for SQL Server, with clear support for a newer compatible driver if available
- SQLAlchemy ORM
- Alembic migrations
- Windows Authentication allowed for local development
- Environment-based connection settings

### Machine Learning

- pandas
- NumPy
- scikit-learn
- joblib
- imbalanced-learn only when justified
- Matplotlib
- Optional XGBoost, LightGBM, or CatBoost only if installation is stable and scientifically justified

The ML implementation must be modular so the algorithm can be changed without rebuilding the LMS.

### Testing and quality

- pytest
- coverage.py
- Flask test client
- Playwright for critical end-to-end workflows when practical
- Ruff
- Black or an equivalent formatter
- mypy where practical
- Bandit
- pip-audit
- Secret scanning such as Gitleaks when available
- Semgrep or another maintained SAST tool when justified
- OWASP ZAP for later dynamic testing

### Serving and deployment

- Flask development server only for development
- Waitress or another suitable production WSGI server
- Deployment-neutral configuration
- Local Windows operation first
- Future cloud or server deployment readiness

Use a modular monolith, not premature microservices or Kubernetes.

---

## 6. USER ROLES AND ACCESS CONTROL

Implement four current roles:

1. Center Administrator
2. Teacher
3. Student
4. Researcher

Use server-side role and object-level authorization. Hiding a menu item is never sufficient security.

### Center Administrator

Can:

- Create and manage student accounts
- Create and manage teacher accounts
- Activate, suspend, archive, and reset access
- Manage approximately 12 initial levels
- Add, rename, reorder, activate, and archive levels
- Manage academic terms
- Manage courses and groups
- Enroll students
- Assign teachers
- Manage class schedules, rooms, or locations
- Manage attendance settings
- Manage grading structures
- View center-level grades and attendance
- Manage announcements and calendar events
- Manage file-format and file-size rules
- Manage branding and general settings
- Manage fee plans, invoices, manual payments, and authorized refunds
- View operational and payment reports

Students must not self-create active accounts. The administration creates or approves them.

### Teacher

Can:

- Access only assigned groups
- View students in assigned groups
- Create and reorder units and lessons
- Publish and unpublish lessons
- Add rich text, PDFs, Word files, images, audio, videos, and external links
- Create listening and speaking activities
- Create assignments and quizzes
- Review submissions and attempts
- Download authorized files
- Add grades and feedback
- Record attendance
- Publish group announcements
- Send permitted messages
- Create and moderate course discussions
- View student progress in assigned groups

### Student

Can:

- Log in using an administration-created account
- View enrolled levels, courses, and groups
- View dashboard, upcoming classes, deadlines, grades, attendance, and progress
- Open lessons and materials
- Play audio and video
- Complete listening activities
- Record and submit speaking activities
- Submit assignments and comments
- Complete quizzes
- View permitted feedback and grades
- View announcements and calendar
- Send and receive permitted messages
- Participate in discussions
- Search authorized learning content
- Update permitted profile settings
- View invoices, payment status, and receipts where authorized
- Receive adaptive help only in Version B

Students must never access another student’s submissions, grades, payment details, research data, or unauthorized courses.

### Researcher

Can:

- Create anonymized participant records
- Record consent
- Create task sets and equivalent tasks
- Assign Version A or B
- Configure experimental order
- Start and finish experiment sessions
- Record observer annotations
- Review interaction event timelines
- Review post-task ratings
- Build anonymized dataset versions
- Export anonymized data
- Run validation, feature extraction, and model training
- Compare candidate models
- Approve and activate a model
- View predictions and interventions
- Compare A and B metrics
- Use a clearly separated presentation Demo Mode
- Configure research thresholds, cooldowns, and intervention limits

Researchers must not see passwords, raw payment credentials, unnecessary private messages, or unnecessary identity information.

Require MFA using TOTP for Administrator and Researcher accounts before production use. Design the architecture so passkeys/WebAuthn can be added later.

---

## 7. FLEXIBLE ACADEMIC STRUCTURE

Support this configurable hierarchy:

Academic Term
→ Level
→ Course
→ Group
→ Unit
→ Lesson
→ Materials and Activities

The center has approximately 12 levels, but names must never be hard-coded.

Administrators must be able to add, rename, reorder, activate, archive, and safely manage levels.

Groups should support:

- Academic term
- Course
- Assigned teacher or teachers
- Enrolled students
- Capacity
- Status
- Start and end dates
- Schedule
- Classroom or delivery location

---

## 8. REQUIRED LMS MODULES

Build each module as a working feature, not a visual placeholder.

### Authentication

- Login
- Logout
- Forgot password
- Secure password reset
- Secure password hashing
- Account status
- Rate limiting
- Session rotation
- Role-based redirects
- Generic secure authentication errors
- MFA for privileged roles

### Role dashboards

Student dashboard:

- My Courses
- Upcoming Assignments
- Grade summary
- Attendance summary
- Announcements
- Messages
- Calendar
- Next class
- Course progress
- Continue Learning
- Recently opened lessons

Teacher dashboard:

- Assigned groups
- Upcoming classes
- Pending reviews
- Recent student activity
- Attendance actions
- Messages and announcements

Administrator dashboard:

- Student and teacher counts
- Active levels, courses, and groups
- Attendance overview
- Schedule overview
- Fee and invoice overview
- Operational statistics

Researcher dashboard:

- Participants
- Consent
- Sessions
- Version distribution
- Interaction events
- Labels
- Dataset versions
- Candidate and active models
- Predictions
- Interventions
- A/B metrics

### Lessons and materials

- Units and ordered lessons
- Rich text content
- PDF, Word, images, audio, video, external links
- Draft and published states
- Completion tracking
- Authorization-aware downloads

### Assignments

- Title and instructions
- Course/group association
- Start and deadline dates
- Late policy
- Allowed file types and maximum size
- Multiple files where configured
- Comments
- Submission state
- Resubmission rules
- Submission history
- Teacher feedback and grades
- Duplicate submission protection

### Quizzes

- Multiple choice
- True/false
- Fill in the blank
- Short answer
- Listening questions
- Question ordering
- Next and Previous
- Optional timer
- Saved progress
- Attempt limits
- Automatic and manual grading
- Final submission and feedback

### Listening

- Audio player
- Play, pause, replay, speed control
- Listening questions
- Optional vocabulary support
- Teacher-controlled transcript
- Attempt and completion records

### Speaking

- Microphone permission guidance
- Start/stop recording
- Playback before submission
- Re-record
- Secure upload
- Teacher playback and feedback

### Attendance

- Attendance sessions
- Present, absent, late, excused
- Notes
- Teacher entry
- Administrator review
- Student summary

### Grades

- Grade categories
- Assignment, quiz, speaking, activity, and manual grades
- Configurable weighting
- Teacher comments
- Student view
- Administrative reports

### Communication

- Center, course, and group announcements
- Message threads and read status
- Course discussions, topics, replies, and moderation
- In-app notifications

### Calendar and scheduling

- Class schedules
- Assignment and quiz dates
- Center events
- Role-specific calendar views

### Search

Search only authorized content across:

- Courses
- Units
- Lessons
- Materials
- Assignments
- Quizzes
- Announcements
- Teacher-defined keywords

Support filters, grouping, empty states, and clear-filter actions.

### Student progress

Track lesson completion, activity completion, assignments, quizzes, attendance, and course progress without misleading calculations.

---

## 9. PAYMENT AND CENTER FEES MODULE

Add a provider-independent fee and payment module.

Support:

- Registration fees
- Course fees
- Fee plans
- Installments
- Discounts
- Scholarships or exemptions
- Student fee assignments
- Invoices and invoice items
- Due dates
- Partial and full payments
- Pending, successful, failed, canceled, and refunded states
- Manual cash payment recording
- Bank-transfer recording
- Online payment integration point
- Receipts
- Payment history
- Refunds with authorization
- Administrative reports

The online provider is not yet selected. Build a clean provider adapter interface, for example:

- `create_payment_intent()`
- `get_payment_status()`
- `cancel_payment()`
- `refund_payment()`
- `verify_webhook()`
- `normalize_event()`

Support a clearly labeled development Mock/Sandbox provider.

### Critical payment boundary

The LMS must never receive or store:

- Full card number
- CVV/CVC
- PIN
- Magnetic-stripe data
- Raw payment credentials

Use a PCI-compliant hosted checkout, redirect, or fully provider-controlled payment element when a real gateway is selected.

Store only allowed operational references such as provider name, customer reference, payment-intent reference, transaction reference, status, amount, currency, card brand and last four digits only if safely returned, timestamps, and receipt reference.

A successful browser redirect alone must never mark an online payment successful. Confirm payment through a verified server-to-server provider status or signed webhook.

Implement:

- Signed webhook verification
- Timestamp validation
- Replay prevention
- Idempotency keys
- Unique provider-event constraints
- Safe state transitions
- Immutable financial audit trail
- `DECIMAL`, never floating point, for money
- Explicit three-letter currency code such as `LYD`
- Refund authorization and audit
- Dual approval for high-risk financial actions where practical

Do not activate real payments until a real provider, sandbox credentials, legal requirements, and security review are available.

---

## 10. ONE CODEBASE: A/B SYSTEM

Build one application and one codebase, never two separate sites.

Support:

1. Standard Center Mode
2. Research Experiment Mode
3. Presentation Demo Mode

### Version A — Traditional

- Complete LMS functionality
- Normal static validation and feedback
- Interaction tracking when an approved experiment is active
- Post-task surveys and observer annotations
- No ML-based adaptive assistance
- No frustration-triggered highlighting
- No adaptive simplification

### Version B — Adaptive

Exactly the same design, data, content, navigation, functionality, forms, and normal validation as Version A, plus:

- Approved-model inference
- Contextual hints
- Smart recovery messages
- Guided highlighting
- Progressive guidance
- Smooth scrolling to the relevant element
- Contextual recommendations
- Temporary simplification when appropriate

Before an intervention occurs, A and B must look identical.

### Version control

- Research version assignment must be server-controlled.
- Students cannot change it by URL, form, cookie, or browser tools.
- Store the assigned version in the experiment session.
- A researcher-only Demo Mode may expose a polished A/B toggle for presentations.
- Demo sessions must be marked and excluded from research data.

---

## 11. RESEARCH PROCESS

Use a two-stage research design.

### Stage 1 — Model development

- Build and stabilize Version A first.
- Obtain informed consent.
- Recruit approximately 20–50 participants, with possible expansion.
- Assign realistic predefined LMS tasks.
- Collect privacy-preserving events.
- Collect task outcomes.
- Collect post-task frustration ratings.
- Collect observer annotations.
- Create versioned datasets.
- Train and validate candidate models.
- Freeze and approve the final model.

### Stage 2 — A/B evaluation

Prefer participants whose final evaluation data was not used to fit the model.

Support:

- Counterbalanced within-subject design: one group A→B, another B→A
- Equivalent task sets to reduce learning effects
- Between-subject design if later approved

Clearly separate:

- Training data
- Validation data
- Final model test data
- Final A/B evaluation data
- Demo and synthetic development data

Do not automatically claim Version B is better. Conclusions must be based on real results.

---

## 12. EXPERIMENT TASKS

Primary experimental areas:

1. Login
2. Student dashboard navigation
3. Finding a lesson
4. Assignment file submission
5. Quiz interaction
6. Search
7. Profile settings

Also make the adaptive architecture extensible to listening, speaking, messaging, discussions, and other pages.

Each task must support:

- Title
- Instructions
- Expected goal
- Equivalent task set
- Difficulty
- Start event
- Progress events
- Completion and failure conditions
- Recommended duration
- Version and order
- Post-task survey
- Observer notes

---

## 13. PRIVACY-PRESERVING INTERACTION TRACKING

Track necessary events such as:

- page_view and page_leave
- element_click and repeated_click
- menu_click
- form_started and form_submitted
- validation_error
- login_failure
- file_selected
- upload_started, upload_failed, upload_completed
- assignment_submit_attempt and assignment_submitted
- quiz_started, answer_selected, answer_changed, next, previous, submit_attempt, submitted
- search_submitted, search_no_results, search_result_clicked
- lesson_opened
- material_opened and downloaded
- audio_play, pause, replay, speed_changed
- microphone_permission_denied
- recording_started, stopped, failed, submitted
- navigation_back and repeated_page_visit
- help_displayed, accepted, dismissed
- task_started, progress, completed, failed
- tab_hidden and tab_visible

Each event should use only necessary fields, such as:

- Event ID
- Anonymous participant ID
- Experiment session ID
- Task session ID
- Server timestamp
- Client timestamp where useful
- Version
- Page identifier
- Element identifier
- Event type
- Sanitized context
- Success or failure state
- Current task and progress stage

Use idempotency IDs, batching, retries, and `sendBeacon` where useful.

Never build a keylogger.

Never store:

- Password values or keystrokes
- Full raw typing
- Unnecessary form contents
- Raw private message bodies in research events
- Payment credentials
- Audio content inside event metadata
- Unnecessary personal data

Typing-related features may use aggregates such as correction count, validation failures, number of field edits, and duration until valid submission.

---

## 14. PROGRESS-AWARE INTERPRETATION

Long inactivity alone is not frustration.

Track meaningful progress such as reaching the correct page, opening the requested lesson, completing valid fields, selecting and uploading a file, answering a question, opening a useful search result, and completing a task.

When deriving inactivity features, consider:

- Tab visibility
- Whether audio or video is playing
- Reading time
- Recent progress
- Whether the task remains active
- Possible device abandonment

Use the Page Visibility API where appropriate.

---

## 15. FRUSTRATION LABELING

Do not create labels only from the same click/error rules used as model features. That would be circular.

After every predefined task ask:

“How frustrated did you feel while completing this task?”

Scale:

1 — Not frustrated at all
2 — Slightly frustrated
3 — Moderately frustrated
4 — Very frustrated
5 — Extremely frustrated

Also collect:

- Perceived task difficulty
- Whether help was needed
- Whether the participant believes the task was completed
- Optional comment
- Optional rating confidence

Observer annotation:

- No visible difficulty
- Mild difficulty
- Clear difficulty
- Strong frustration
- Uncertain

Always preserve the original 1–5 value.

Support documented transformations. An initial binary strategy may use 1–2 as not frustrated, 4–5 as frustrated, and treat 3 as ambiguous. Do not choose a strategy silently. Save it with the dataset and model version.

Self-report is the primary label unless the approved methodology later changes it. Observer data is secondary.

---

## 16. MACHINE LEARNING PIPELINE

The final detector must be a real trained and validated ML model using real Version A data.

Rules may be used only for event validation, task completion, cooldown, technical fallback, contextual intervention selection, or clearly labeled demo simulation. Do not call a hard-coded score artificial intelligence.

Required pipeline:

1. Validate and clean data.
2. Build reproducible task-level and rolling-window features.
3. Compare candidate models.
4. Prevent participant leakage.
5. Select based on performance, stability, interpretability, latency, and dataset size.
6. Register and approve the model.
7. Save the complete preprocessing pipeline and model.
8. Integrate the approved model into Version B.

Candidate features include:

- Total and repeated clicks
- Click frequency and bursts
- Invalid submissions
- Login failures
- Upload failures
- Repeated submit attempts
- Answer changes
- Quiz back-and-forth navigation
- Unsuccessful searches and reformulations
- Task duration
- Time since progress
- Page transitions and repeated visits
- Navigation reversals
- Field corrections
- Invalid-form duration
- Task restarts
- Incomplete actions
- Error and progress rates
- Success after error
- Audio replay combined with lack of progress
- Recording failures
- Tab-hidden duration
- Device category
- Page and task type
- Progress stage

Create a feature dictionary with definition, unit, source, missing-value policy, encoding, scaling, runtime availability, and justification.

Use a reproducible scikit-learn Pipeline.

Compare at minimum:

- Logistic Regression
- Support Vector Machine
- Random Forest
- Gradient Boosting or HistGradientBoosting

Optionally compare XGBoost, LightGBM, CatBoost, or another justified model.

Metrics may include:

- Precision
- Recall for the frustrated class
- F1 and Macro F1
- Balanced accuracy
- Confusion matrix
- PR-AUC
- ROC-AUC where appropriate
- Calibration where practical
- Inference latency
- Model size
- Fold stability
- Interpretability

Avoid excessive false positives because repeated unnecessary help can create frustration.

### Prevent data leakage

Use participant ID as the group. Never randomly split rows when one participant contributes multiple records.

Use suitable grouped approaches such as:

- GroupKFold
- StratifiedGroupKFold
- GroupShuffleSplit
- Leave-One-Group-Out for secondary analysis

All preprocessing, scaling, feature selection, oversampling, SMOTE, and tuning must occur only inside training folds.

Create automated leakage tests that fail on participant overlap.

Record dataset version, participant groups, splits, seed, feature set, pipeline, hyperparameters, metrics, and training time.

### Synthetic data policy

Synthetic data may test software flow only. Mark it:

`DEVELOPMENT DATA — NOT RESEARCH DATA`

Never present synthetic metrics as findings. Never activate Version B research mode using an unapproved synthetic model. A separate Demo Mode may simulate interventions.

---

## 17. MODEL REGISTRY AND INFERENCE

Store for each model:

- Model ID and name
- Algorithm
- Dataset version
- Feature set
- Label strategy
- Training time
- Hyperparameters
- Validation method
- Metrics
- Threshold
- Artifact path
- Checksum
- Status
- Notes

Statuses:

- Development
- Candidate
- Approved
- Active
- Archived
- Rejected

Only one approved model can be active.

Runtime flow:

Interaction Event Collector
→ Task/Window Aggregator
→ Feature Extractor
→ Approved ML Pipeline
→ Frustration Probability
→ Contextual Intervention Policy
→ Adaptive Response

Runtime must use only information available up to prediction time, record inference latency, model version, window reference, probability, threshold decision, and intervention outcome.

Do not expose raw probability to students.

If the model is unavailable:

- Keep core LMS functionality working
- Log the incident
- Disable adaptive inference safely
- Notify authorized researchers
- Never silently substitute a rule-based detector and call it AI

---

## 18. ADAPTIVE INTERVENTION POLICY

Separate prediction from intervention selection.

The model answers whether behavior is associated with possible frustration. The intervention policy chooses appropriate help based on page, task, recent errors, and progress.

Levels:

- Level 0: No intervention
- Level 1: Small contextual hint
- Level 2: Highlight the relevant element and explain the next action
- Level 3: Step-by-step guidance and smooth focus movement
- Level 4: Temporary reduction of nonessential clutter and stronger recovery guidance

Implement cooldown, maximum interventions per task, deduplication, dismissal, “Do not show again during this task,” keyboard focus, screen-reader announcements, and reduced-motion support.

Never say “You are frustrated.” Use careful language such as:

- “It looks like this step may need some clarification.”
- “You may need to upload a file before submitting this assignment.”
- “Try opening the Assignments section to continue.”

Never shame users, block the entire interface unnecessarily, flash aggressively, reveal direct quiz answers, or display endless popups.

### Page-specific behavior

Login:

- Detect patterns around failed attempts and repeated submit clicks
- Highlight relevant fields
- Explain identifier format
- Offer password recovery
- Avoid account enumeration

Dashboard:

- Respond to repeated wrong-menu navigation
- Highlight the likely destination
- Offer a direct link
- Recommend next incomplete work

Course and lesson pages:

- Recommend the relevant section
- Highlight the next lesson
- Offer Continue Learning
- Reduce unrelated clutter temporarily

Assignment submission:

- Explain allowed formats and size
- Highlight upload zone
- Smooth-scroll to the missing field
- Preserve valid comments
- Prevent duplicate submissions

Quiz:

- Provide limited hints or related lesson references
- Highlight navigation controls
- Explain unanswered status
- Never provide the direct answer

Search:

- Suggest keywords or spelling
- Recommend a section
- Explain filters and allow clearing them

Profile:

- Highlight only problematic fields
- Explain password rules
- Preserve valid values
- Focus first invalid field

Listening:

- Replay alone is not frustration
- Suggest slower speed, limited vocabulary, or teacher-approved transcript support

Speaking:

- Explain microphone permission
- Highlight recording controls
- Show clear recording state
- Guide submission

Log every intervention, prediction, acceptance, dismissal, time to progress, error recurrence, task result, and model version.

---

## 19. PREMIUM PRODUCT DESIGN

Visual design is a first-class requirement.

Analyze the supplied center logo and branding.

Before writing full application pages, propose three original visual directions, for example:

1. Minimal Premium Academic
2. Cinematic Learning Experience
3. Refined Glass and Layered Depth

For each direction provide:

- Logo-derived color palette
- Typography
- Background strategy
- Sidebar and header
- Cards and forms
- Tables and charts
- Motion personality
- Adaptive-assistance appearance
- Accessibility considerations
- Dashboard wireframe
- Course-page wireframe
- Assignment-page wireframe
- Strengths and risks

Recommend one and wait for approval.

After approval, build a protected internal Design System page before completing the product pages.

The design system must define:

- Color tokens
- Typography scale
- Spacing and grid
- Border radii
- Elevation and shadows
- Icon rules
- Motion durations and easing
- Buttons
- Inputs and selects
- File upload controls
- Cards
- Tables
- Badges
- Alerts
- Modals and drawers
- Tooltips and toasts
- Navigation
- Empty states
- Skeleton loaders
- Charts
- Success and error states
- Adaptive spotlight and guidance cards
- Reduced-motion variants

Use logo-derived colors, carefully controlled gradients, strong hierarchy, high-quality spacing, selective translucency, restrained backdrop blur, soft realistic shadows, responsive sidebar, polished mobile navigation, breadcrumbs, and clean data visualizations.

Avoid random gradients, excessive glassmorphism, constant glowing, low contrast, tiny text, clutter, template-like generic pages, and excessive motion.

Common animation duration should usually be about 150–300 ms. Respect `prefers-reduced-motion`. Do not make Version A visually inferior.

Adaptive intervention experience may:

1. Slightly reduce unrelated emphasis.
2. Smooth-scroll to the relevant section.
3. Show a controlled spotlight.
4. Add a short subtle outline or pulse.
5. Display a translucent contextual guidance card.
6. Explain the exact next action.
7. Offer an action button and dismissal.
8. Return smoothly to normal.

---

## 20. DATABASE DESIGN GATE

Do not create SQLAlchemy models or migrations before presenting and receiving approval for the database design.

Use a normalized SQL Server relational schema. Critically evaluate the practical need for each table; do not create complexity merely to look advanced.

Propose domains and tables such as:

### Organization and configuration

- organizations
- branches
- branding_settings
- system_settings
- academic_terms
- rooms

Begin with one organization and one branch if appropriate, while preserving future expansion.

### Identity and security

- users
- roles
- permissions
- user_roles
- role_permissions
- student_profiles
- teacher_profiles
- researcher_profiles
- user_sessions
- mfa_methods
- mfa_recovery_codes
- login_attempts
- password_reset_tokens
- security_events
- audit_logs

### Academic structure

- levels
- courses
- groups
- group_teachers
- enrollments
- schedules
- units
- lessons
- materials
- lesson_progress

### Assignments

- assignments
- assignment_file_rules
- submissions
- submission_files
- submission_feedback
- submission_history

### Quizzes

- quizzes
- quiz_questions
- question_options
- quiz_attempts
- quiz_answers
- quiz_attempt_events

### Listening and speaking

- listening_activities
- listening_questions
- listening_attempts
- listening_answers
- speaking_activities
- speaking_submissions
- speaking_files
- speaking_feedback

### Center operations

- attendance_sessions
- attendance_records
- grade_categories
- grades
- announcements
- calendar_events
- notifications
- message_threads
- message_thread_members
- messages
- discussion_topics
- discussion_replies

### Fees and payments

- fee_plans
- fee_plan_items
- student_fee_assignments
- invoices
- invoice_items
- payment_customers
- payment_intents
- payment_transactions
- payment_webhook_events
- refunds
- receipts
- payment_audit_events

### Research

- research_participants
- consent_records
- experiment_definitions
- experiment_task_sets
- experiment_tasks
- experiment_assignments
- experiment_sessions
- task_sessions
- interaction_events
- frustration_ratings
- observer_annotations

### Machine learning

- dataset_versions
- dataset_participants
- feature_set_versions
- feature_records
- model_versions
- model_metrics
- model_artifacts
- model_predictions
- adaptive_interventions

### Files and operations

- uploaded_files
- file_access_logs
- data_exports
- export_audit_logs
- background_jobs
- system_health_events

The proposal must explain:

- Purpose and important columns of every accepted table
- Primary and public identifiers
- Foreign keys and cardinalities
- Unique and check constraints
- Archive/delete policy
- Index strategy
- High-write tables
- Future partitioning strategy
- Concurrency control
- Time and timezone policy
- Money and currency types
- Data classification and encryption needs
- Retention policy
- Audit requirements
- File-storage references
- Payment boundary
- Research anonymization boundary
- Expected report queries

Preferred principles:

- Internal numeric primary key plus UUID public identifier where useful
- `DATETIME2` with a consistent UTC storage strategy and Libya-local display
- `DECIMAL(19,4)` for money
- `CHAR(3)` for currency
- `rowversion` for selected concurrency-sensitive records
- Append-only event/audit/financial history where appropriate
- Safe archive statuses instead of destructive deletion for financial and research records
- Index foreign keys and frequent filters
- JSON only for limited sanitized metadata, never instead of core relations
- File content stored in secure file/object storage, with metadata in SQL Server

Create a Mermaid ERD and a relational schema proposal. End the database proposal by asking:

“هل تعتمدون تصميم قاعدة البيانات لكي نبدأ إنشاء الـModels والـMigrations؟”

Do not continue until we approve it.

---

## 21. SECURITY ARCHITECTURE

Use secure-by-design, privacy-by-design, least privilege, deny-by-default, and defense in depth.

Create a STRIDE or equivalent threat model before implementation, covering:

- Authentication and MFA
- Authorization and object-level access
- Student and teacher data
- Research data and exports
- Machine learning artifacts
- Files and audio recordings
- Payments and webhooks
- Admin and researcher functions
- Backups, secrets, logs, and deployment
- Third-party providers

Create an OWASP ASVS 5.0 verification matrix and address relevant OWASP Top 10 risks.

Implement:

- Strong password hashing
- CSRF protection
- Output encoding and XSS defenses
- ORM/parameterized query use
- SSRF protection
- Secure redirects
- Clickjacking protection
- Content Security Policy
- Secure file handling
- Rate limiting and brute-force controls
- Session fixation prevention and rotation
- Sensitive-action reauthentication
- MFA for privileged users
- Secure password reset
- Generic authentication errors
- Safe production error pages
- Audit logging
- Tamper-evident critical records where practical
- Export authorization
- Retention controls
- Secret management
- Secure headers
- Input and output validation

No system can be guaranteed forever vulnerability-free. The release target is:

- No known Critical vulnerabilities
- No unaccepted High vulnerabilities
- Passing authorization and security-critical tests
- Passing dependency and secret scans
- Reviewed threat model
- Documented residual risks
- Independent penetration testing recommended before public deployment or real payment activation

---

## 22. CRYPTOGRAPHY AND SECRET MANAGEMENT

Never invent custom cryptography.

### Passwords

- Use Argon2id through a maintained library
- Use unique salts managed by the library
- Never encrypt passwords with AES
- Never store plaintext passwords
- Support hash-parameter upgrades
- Use secure expiring single-use reset tokens

### Data in transit

- TLS 1.3 for production by default
- TLS 1.2 only when required for compatibility
- Disable obsolete protocols
- Secure, HttpOnly, SameSite cookies
- HSTS in production

### Sensitive data at rest

First minimize collection. Encrypt only fields with a documented need.

When reversible application-level encryption is justified:

- Use AES-256-GCM through a maintained validated library
- Use a unique cryptographically random nonce for every encryption operation
- Preserve authentication tags
- Store key ID and version, not raw keys, with ciphertext
- Keep encryption keys outside the database and repository
- Support key rotation, re-encryption, and revocation
- Use a production secret vault or key-management service
- Never log plaintext secrets or keys

Encrypt backups, restrict access, and test restoration.

Do not use the phrase “military-grade” as a security proof. Describe the actual controls and threat model.

---

## 23. SECURE FILE HANDLING

Implement:

- Extension allowlist
- MIME validation
- File-signature validation where practical
- Maximum size
- Random stored names
- Original name stored separately
- Path traversal prevention
- Non-executable storage
- Authorization before download
- Safe content-disposition
- Duplicate request protection
- Audit logging
- Configurable formats and sizes
- Future malware-scanning integration

Never trust the browser MIME type alone. Do not store uploads in a publicly executable directory.

---

## 24. PRIVACY AND ETHICS

Implement:

- Informed consent status
- Anonymous participant codes
- Separation between center identity and research participant ID where practical
- Data minimization
- Anonymized exports
- Participant exclusion and withdrawal workflow according to the approved methodology
- Configurable retention
- Restricted raw-data access
- Export audit records

Do not implement hidden keylogging, webcam emotion recognition, facial analysis, voice-emotion recognition, or unnecessary biometric monitoring.

Use the wording:

“Behavioral patterns associated with possible frustration.”

Do not claim certainty about a user’s internal emotion.

---

## 25. CODE QUALITY

Requirements:

- Modular architecture
- Small cohesive functions and services
- Clear naming
- Python type hints
- Useful docstrings
- No duplicated business logic
- No giant route handlers or god classes
- No unsafe global mutable state
- No hard-coded secrets, center data, or level names
- No magic security constants
- No float for money
- No swallowed exceptions
- No unjustified broad exceptions
- No user-built raw SQL
- No trusted raw file paths
- No unauthorized object loading
- No circular imports
- No core TODO placeholders
- No dead or abandoned code
- Transactions for multi-record operations
- Idempotency and concurrency handling
- Pagination
- Structured logging and correlation IDs
- Avoid N+1 queries

Apply SOLID and DRY pragmatically without needless abstraction.

---

## 26. TESTING AND RELEASE GATES

Write and run:

- Unit tests
- Integration tests
- Authorization and IDOR tests
- Migration tests
- File-upload tests
- Payment state and webhook tests
- Research-isolation tests
- ML leakage tests
- A/B isolation tests
- End-to-end tests
- Accessibility and responsive checks
- Performance tests for critical flows

Explicitly test:

- Horizontal and vertical privilege escalation
- Student isolation
- Teacher group isolation
- Unauthorized researcher access
- CSRF and XSS handling
- Injection resistance
- Malicious names, MIME mismatch, and oversized uploads
- Duplicate requests
- Replayed or forged webhooks
- Amount and currency tampering
- Multiple payment callbacks
- Session fixation
- Reset-token replay
- MFA recovery
- Research export anonymization
- Unauthorized model activation
- No adaptive help in Version A
- Only approved model use in Version B

Configure appropriate tools such as Ruff, formatter, mypy, pytest, coverage, Bandit, pip-audit, Semgrep, Gitleaks, ZAP, and SBOM generation. Do not install tools blindly; explain each selection.

Block release when:

- A Critical finding exists
- A High finding is unreviewed or unaccepted
- Secrets are detected
- Authorization tests fail
- Payment integrity tests fail
- Migrations fail
- Research exports expose identity
- Card data or secrets enter logs/database
- Version A displays adaptive assistance
- Version B uses an unapproved model

Never report a test as passed without executing it.

---

## 27. RELIABILITY, PERFORMANCE, AND OPERATIONS

Design for:

- Structured logs and correlation IDs
- Health and readiness checks
- Background job monitoring
- Transaction safety
- Bounded retries and idempotent jobs
- Backup monitoring and restore testing
- Error-monitoring integration
- Security-event alerts
- Disaster recovery documentation
- Graceful degradation
- Maintenance mode
- Pagination and proper indexes
- Query-count monitoring
- Selective caching
- Event batching
- File streaming
- Static asset caching
- Connection pooling
- Rate limits
- Load testing

Optional analytics, notifications, payments, or ML inference failure must not unnecessarily break core LMS learning functions.

---

## 28. DOCUMENTATION

Create and maintain:

- README.md
- CLAUDE.md
- CHANGELOG.md
- Windows setup guide
- SQL Server setup guide
- Requirements
- Use cases and user stories
- Architecture
- ERD and relational schema
- Data dictionary
- Design system
- Route documentation
- Interaction-event dictionary
- ML feature dictionary
- Labeling and data-collection protocol
- Dataset validation and model training protocol
- Experiment protocol
- Security and threat model
- OWASP ASVS matrix
- Privacy document
- Payment architecture and data boundary
- Testing report
- Deployment guide
- Backup and restore guide
- Administrator, Teacher, Student, and Researcher guides
- Known limitations
- Future work

Create Mermaid diagrams for architecture, authentication, academic structure, assignments, quizzes, payment flow, experiment flow, Version A flow, ML training, Version B inference, adaptive intervention, and ERD.

---

## 29. SEED AND DEVELOPMENT DATA

Create fictional development data only:

- One administrator
- Two teachers
- Several students
- Approximately 12 editable levels
- Courses, groups, units, lessons, materials
- Audio metadata and video links
- Assignments and quizzes
- Attendance, grades, announcements, events, messages, discussions
- Fee plans and sandbox invoices
- Experiment task sets

Clearly label credentials and records as development-only. Never use real participant information in seed files.

---

## 30. IMPLEMENTATION PHASES

### Phase 0 — Discovery and design approval

- Read `_project_inputs`
- Summarize requirements and unknowns
- Propose product architecture
- Propose modular-monolith structure
- Propose database and Mermaid ERD
- Propose file-storage and payment boundaries
- Propose threat model and security plan
- Propose cryptography and secret management
- Propose three visual directions
- Propose implementation roadmap
- Wait for approval

### Phase 1 — Prerequisite verification

One micro-step at a time:

- VS Code
- Claude Code
- Git
- Python
- SQL Server
- SSMS
- ODBC driver
- Empty project folder

### Phase 2 — Foundation and design system

- Git and project files
- Virtual environment
- Flask factory
- Configuration
- Extensions
- SQL Server connection
- Migrations
- Authentication and roles
- Base layout and branding
- Design-system page
- Initial tests

### Phase 3 — Core center and LMS

- Academic terms
- Levels
- Courses
- Groups
- Enrollments
- Schedules
- Dashboards
- Units
- Lessons
- Materials
- Search
- Notifications

### Phase 4 — Learning activities and operations

- Assignments
- Quizzes
- Listening
- Speaking
- Attendance
- Grades
- Announcements
- Calendar
- Messages
- Discussions
- Student progress

### Phase 5 — Fees and payments

- Fee plans
- Invoices
- Manual cash and bank records
- Sandbox provider adapter
- Webhook architecture
- Receipts and reports
- Security tests

### Phase 6 — Version A research infrastructure

- Participants and consent
- Tasks and sessions
- Version assignment
- Tracking collector
- Progress-aware events
- Survey and observer annotations
- Anonymized exports
- Research dashboard

This phase must end with a stable Version A ready for real data collection.

### Phase 7 — ML pipeline

- Dataset validation
- Feature engineering
- Grouped validation
- Leakage tests
- Candidate models
- Evaluation
- Registry and approval workflow
- Development-only synthetic pipeline tests

### Phase 8 — Version B

- Runtime windows
- Approved-model inference
- Contextual intervention policy
- Premium adaptive components
- Cooldowns and dismissal
- Prediction and intervention logging
- Version isolation tests

### Phase 9 — Final A/B evaluation support

- Counterbalancing
- Equivalent task sets
- Metrics and timelines
- Statistical exports
- Final experiment documentation

### Phase 10 — Hardening and release

- Full tests
- Security review
- Accessibility and responsive review
- Performance review
- Backup/restore
- Deployment preparation
- Final demo mode
- Release checklist

---

## 31. PROJECT STATE MANAGEMENT

Create and update:

- `docs/PROJECT_STATUS.md`
- `docs/IMPLEMENTATION_PLAN.md`
- `docs/ASSUMPTIONS.md`
- `docs/DECISIONS.md`
- `docs/NEXT_STEPS.md`

After every approved step, record completed work, files changed, migrations, commands, tests, issues, and next action. Create Git commits after stable milestones.

---

## 32. ACCEPTANCE CRITERIA

The project is not complete until:

- It is built from scratch in the current folder.
- Clean SQL Server migrations and seed data work.
- All four roles and permissions work.
- Core LMS workflows work.
- Approximately 12 levels are configurable.
- Lessons, assignments, quizzes, listening, speaking, attendance, grades, communication, calendar, search, progress, fees, invoices, and sandbox payment flow work.
- File handling is secure.
- The center branding and premium responsive design are implemented.
- Accessible motion and reduced-motion behavior work.
- Version A and B share one codebase.
- Students cannot change versions.
- Version A never triggers adaptive help.
- Version A can collect real research data and labels.
- Exports are anonymized.
- ML pipeline is reproducible and prevents participant leakage.
- Multiple models are comparable.
- Only an approved model can activate Version B research mode.
- Version B performs logged runtime inference and contextual interventions.
- Demo data is excluded.
- Payment card data never enters the LMS.
- Security, authorization, payment, migration, and A/B tests pass.
- Documentation is complete.
- No fabricated research results are presented.

---

## 33. REQUIRED FIRST RESPONSE — DO NOT CODE YET

Your first response after reading this prompt and `_project_inputs` must be in Arabic and must contain only the discovery/design package. Do not create source-code files or migrations yet.

It must include:

1. Confirmation that the project is new and built from scratch.
2. Summary of all provided center, branding, research, and technical inputs.
3. List of genuinely blocking questions only.
4. Non-blocking assumptions.
5. High-level product architecture.
6. Recommended modular-monolith architecture.
7. Module boundaries and primary data flows.
8. Full practical database proposal.
9. Mermaid ERD.
10. File-storage strategy.
11. Payment architecture and card-data boundary.
12. Research-data and anonymization boundary.
13. Machine-learning training and runtime flow.
14. Security architecture and concise threat model.
15. Cryptography and secret-management plan.
16. Backup and recovery concept.
17. Three premium visual-design directions based on the supplied logo.
18. Recommendation among the three.
19. Phased implementation roadmap.
20. Risks and decisions requiring our approval.

End by asking us to approve or revise:

- Product architecture
- Database design
- Payment design
- Research design
- Security plan
- Visual direction
- Guided one-step workflow

Then stop and wait. Do not code.

---

## 34. AFTER WE APPROVE THE DESIGN PACKAGE

Begin with only the first prerequisite-verification micro-step. Do not create the full project in that response.

Use the mandatory Arabic step format, wait for our actual result, and continue only after confirmation.
