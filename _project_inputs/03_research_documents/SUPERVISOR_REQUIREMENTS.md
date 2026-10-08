# Supervisor Requirements and Current Research Direction

## Part 1: Supervisor Requirements

The user interface itself must be the experimental artifact.

The project must not be implemented as a generic public website.

A Learning Management System should be used because it includes realistic tasks and interaction patterns such as:

- Login forms
- Dashboard navigation
- Course navigation
- Search
- Quizzes
- Assignment submission
- File uploads
- Multi-step tasks
- Profile settings

The system must contain two experimental conditions inside one platform:

### Version A — Traditional Interface

- A normal non-adaptive interface
- No frustration-triggered assistance
- No adaptive highlighting
- No progressive guidance triggered by behavioral detection
- Behavioral interaction data may still be collected for approved research purposes

### Version B — Adaptive Interface

- The same design as Version A
- The same pages
- The same content
- The same functionality
- The same navigation
- The same tasks

The only experimental difference is the activation of frustration detection and adaptive assistance.

Potential behavioral indicators include:

- Repeated failed login attempts
- Repeated clicks
- Incorrect inputs
- Long periods without meaningful progress
- Repeated navigation
- Failed file uploads
- Repeated answer changes
- Unsuccessful searches
- Repeated form submission attempts

Potential adaptive responses include:

- Contextual hints
- Highlighting the relevant element
- Smart error messages
- Progressive step-by-step guidance
- Smooth scrolling to the relevant section
- Suggesting the correct menu or section
- Temporary reduction of unrelated visual clutter
- Contextual assistance cards

The platform should include experimental tasks involving:

- Login
- Dashboard navigation
- Finding a course lesson
- Assignment submission
- Quiz completion
- Search
- Profile settings

## Part 2: Current Approved Project Requirement

The following is a newer project decision added after the original supervisor requirements.

It must not be falsely attributed to the original supervisor message unless the supervisor confirms it later.

Frustration detection in the final adaptive Version B must use a real trained machine-learning model.

The required process is:

1. Build Version A first.
2. Collect privacy-preserving behavioral data from real use of Version A.
3. Collect post-task self-reported frustration ratings.
4. Use observer annotations as secondary evidence where approved.
5. Extract behavioral features from the collected interaction data.
6. Train and compare multiple machine-learning algorithms.
7. Prevent participant data leakage during training and evaluation.
8. Keep all records from the same participant inside the same data split.
9. Select the best validated model based on scientific and practical metrics.
10. Register and approve the selected model.
11. Integrate the approved model into Version B.
12. Use Version B to detect behavioral patterns associated with possible frustration and trigger contextual adaptive assistance.

The final detector must not be only:

- A manually calculated frustration score
- A collection of if-statements
- A purely rule-based threshold system

Rules may be used for event validation, task logic, intervention cooldowns, and technical fallback behavior, but the primary research detector in Version B must be the trained machine-learning model.

Synthetic data may be used only to test the software pipeline.

Synthetic data must never be presented as real research data or as the final validated model.