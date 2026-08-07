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