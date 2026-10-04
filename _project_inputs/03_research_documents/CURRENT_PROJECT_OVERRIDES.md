# Current Project Overrides

This file contains the latest project decisions.

These decisions override conflicting requirements in MASTER_PROMPT.md, previous Claude responses, and any older document.

## Source Documents

Do not request, read, or depend on:

- The old proposal
- The old Chapter One
- The old Chapter Two
- Any old practical project
- Any old database
- Any old migrations

The current sources of truth are:

1. CURRENT_PROJECT_OVERRIDES.md
2. SUPERVISOR_REQUIREMENTS.md
3. MASTER_PROMPT.md only where it does not conflict with these files
4. Center information and branding files

## Project Scope

- Build a completely new system from scratch.
- Build an internal LMS only.
- Do not build a public marketing website.
- The LMS is for a real English language learning center.
- The complete platform interface is English only.
- The development explanations provided to the students must be in Arabic.
- Current roles are Administrator, Teacher, Student, and Researcher.
- The center has approximately 12 levels.
- Levels must be configurable and must not be hard-coded.
- Student accounts are created by the administration.
- The project must support Version A and Version B in one codebase.
- Students cannot change their assigned experimental version.
- Researchers may use a separate Demo Mode for presentation.
- The design must follow the center logo and branding.
- The design must be professional, responsive, accessible, premium, and visually polished.

## Database Technology Override

Use MySQL instead of Microsoft SQL Server.

Required database stack:

- MySQL Server
- MySQL Workbench for local database management
- SQLAlchemy ORM
- Flask-Migrate
- Alembic
- PyMySQL as the initial Python database driver

Do not use:

- SQL Server
- SSMS
- pyodbc
- ODBC Driver 17
- SQL Server-specific SQL
- DATETIME2
- UNIQUEIDENTIFIER
- IDENTITY syntax
- SQL Server rowversion
- SQL Server-specific backup commands

Before selecting MySQL-specific features, verify the actual installed MySQL version.

Use MySQL-compatible choices such as:

- BIGINT AUTO_INCREMENT for internal primary keys
- Application-generated UUID values stored initially as CHAR(36) for public identifiers
- DATETIME(6) for timestamps where appropriate
- DECIMAL(19,4) for monetary values
- CHAR(3) for currency codes
- SQLAlchemy version columns for optimistic concurrency when needed
- utf8mb4 character set
- utf8mb4_unicode_ci or another justified modern collation

The initial development database name should be:

adaptive_english_lms

The exact username and password must not be hard-coded.

Database configuration must be loaded from environment variables.

A development connection format may later use:

mysql+pymysql://USERNAME:PASSWORD@localhost:3306/adaptive_english_lms

Do not request or store real passwords inside documentation or source code.

## Machine Learning

- Machine learning is mandatory in the final Version B.
- Version A is built and used first for real behavioral data collection.
- Multiple algorithms must be compared.
- Participant-grouped validation must prevent data leakage.
- The selected approved model is integrated into Version B.
- The final detector must not be only rule-based.
- Synthetic data is only for development testing.

## Database Scope

Do not build approximately 90 tables immediately.

Create a reduced Core Graduation Project Schema first.

Create database tables gradually according to the implementation phases.

Document future enterprise expansion separately.

Every implemented table must have:

- A real page or workflow that uses it
- A real relationship
- A real test
- A justified research, academic, financial, or operational purpose

## Payment Scope

The initial system should support:

- Fee plans
- Student fee assignments
- Invoices
- Invoice items
- Discounts
- Installments where practical
- Manual cash payments
- Bank transfers
- Receipts
- Payment history
- Administrative reports
- A clearly labeled Mock Payment Provider

A real online payment provider has not been selected.

Do not implement direct card processing.

Never store:

- Full card number
- CVV or CVC
- PIN
- Raw card credentials

Keep an adapter architecture for a future hosted or redirect payment provider.

## Visual Direction

Use a hybrid visual direction.

Foundation:

- Minimal Premium Academic

Add:

- Layered depth
- High-quality soft shadows
- Carefully controlled transparency
- Refined cards
- Premium charts and tables
- Premium empty and loading states

Use cinematic effects only for adaptive interventions:

- Guided spotlight
- Subtle background de-emphasis
- Short controlled target glow
- Smooth scrolling
- Elegant contextual guidance cards

Do not use:

- A permanently dark interface
- Constant glowing
- Excessive glassmorphism
- Excessive gradients
- Low contrast
- Continuous animations

Use blue as the main interface color.

Use red as a limited strong accent based on the center logo.

## Guided Development Workflow

The project owners are beginners.

Provide only one small verifiable implementation step at a time.

For every step explain in Arabic:

- The goal
- Why it is needed
- The exact place inside VS Code
- The exact command
- The exact expected result
- How to verify it
- What result the students must send back

Do not provide the complete project code at once.

Stop after every step and wait for the actual result.

## Phase 6 Research Method (supersedes task-based collection and in-app consent)

This section overrides MASTER_PROMPT.md sections 6 (Researcher capabilities that assume tasks, consent recording and observer annotation), 11 (Stage 1 predefined tasks and in-app informed consent), 12 (Experiment Tasks), 15 (post-task rating) and the Phase 6 bullets of section 30, and it supersedes the Phase 6 M01 and M02A implementation.

- Version A collection uses natural use of the LMS by adult Students. There are no researcher-assigned tasks, task sets, task order, task start button or task-completion declaration.
- Population rule: while an authorized configuration is active, collecting and inside its period, collection runs automatically for every eligible Student (an active, authenticated Student account that is not excluded), including Students created or activated later. There is no operator inclusion or batch enrolment. Teachers, Administrators, Researchers, anonymous visitors, suspended Students and accounts marked as demonstration or development data are outside the real population.
- Participation arrangements are handled externally by the center. The application has no consent screen and no institutional agreement workflow, never records that a Student accepted anything, never treats silence as consent, and never claims that external legal or ethics approval was verified. Stored bases describe what happened (population rule, operator reinstatement, external exclusion, legacy exclusion). An operator records exclusions and reinstatements; an exclusion, including one carried over from a legacy refusal or withdrawal, is enforced on every path and is never reversed by login, page visits or delayed batches.
- Signals are a documented allowlist of interaction events plus server-confirmed workflow outcomes. Labels are occasional, optional frustration ratings (1-5, raw value preserved) sampled at random eligible moments and at natural activity endings, each linked to the exact observed interval before the prompt. Unrated data stays unlabeled.
- The collector runs in the background on every authenticated Student page; every Student route has an intentional tracking classification, and only technical routes that serve file or audio bytes are excluded, for data minimisation. No content is collected.
- Researchers use a separate workspace with a dedicated login, where excluded Students are listed by pseudonymous code. No research status, notice, indicator, information page, researcher identity, management surface, participant list, protocol catalogue or research result appears in the Administrator, Teacher or Student portals. The optional frustration question (with Skip, and no research or study wording) is the only Student-facing element.
- Each research export is stored once as an immutable archive and served unchanged until the configured retention removes it.
- Researchers authenticate with approved email/password accounts. On 2026-10-04 the owners removed the second-factor requirement. A configured retention period remains required before collection.
- Phase 6 performs no model training, prediction or adaptive intervention. The current contract is docs/PHASE6_NATURAL_USE_RESEARCH.md.
