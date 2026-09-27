"""Shared fixtures for the Phase 6 / M02A experiment protocol test modules.

Two kinds of helper, deliberately separated, exactly like
``tests/research_fixtures.py``:

- **Row helpers** (:func:`definition_row`, :func:`set_row`, :func:`task_row`,
  :func:`activate_row`) write straight into the tables, so a test about a
  constraint or a guard is not also a test of the route.
- **Route helpers** (:func:`create`, :func:`add_set`, :func:`add_task`,
  :func:`activate`, ...) drive the application's own write path and read
  every signed token out of the page the server rendered.

Every string here is obvious development placeholder wording. No protocol
in this module is, or claims to be, an approved research protocol.
"""

import re

from app.extensions import db
from app.models import (
    ExperimentDefinition,
    ExperimentDefinitionStatus,
    ExperimentTask,
    ExperimentTaskSet,
)
from app.models.submission_feedback import whole_second_utc
from app.services.experiment_protocol_review import content_digest
from app.services.experiment_protocol_transactions import _content
from tests.research_fixtures import STATE_FIELD, page

DRAFT = ExperimentDefinitionStatus.DRAFT.value
ACTIVE = ExperimentDefinitionStatus.ACTIVE.value
SUPERSEDED = ExperimentDefinitionStatus.SUPERSEDED.value
DISCARDED = ExperimentDefinitionStatus.DISCARDED.value

PROTOCOLS_URL = "/research/protocols"
NEW_URL = "/research/protocols/new"

#: Flash wording the routes use, asserted by name rather than retyped.
STALE_TEXT = "no longer describes the current protocol"
FROZEN_TEXT = "no longer a draft"
SAVED_TEXT = "Protocol draft saved"
ACTIVATED_TEXT = "is now the active catalogue version"
ALREADY_ACTIVE_TEXT = "already the active one"
NOT_READY_TEXT = "cannot be activated yet"
DISCARDED_TEXT = "was discarded"
CONFIRM_TEXT = "Tick the confirmation box"
PROHIBITED_TEXT = "Remove participant codes and email addresses"

#: One valid task's fields, as the model and the form name them.
TASK = {
    "task_type": "find_lesson",
    "title": "Find the placeholder lesson",
    "participant_instructions": "Development placeholder.\nOpen the lesson named in the task.",
    "expected_goal": "The named lesson is opened.",
    "difficulty": "easy",
    "recommended_duration_seconds": 120,
    "completion_criterion": "lesson_opened",
}


# ---------------------------------------------------------------------------
# Row helpers -- straight into the tables
# ---------------------------------------------------------------------------


def definition_row(creator, version="VA-1", title="Placeholder protocol", rationale=None,
                   derived_from=None, moment=None):
    """One draft definition written directly."""
    moment = moment or whole_second_utc()
    definition = ExperimentDefinition(
        version_identifier=version,
        title=title,
        study_stage="version_a_collection",
        equivalence_rationale=rationale,
        status=DRAFT,
        version=1,
        derived_from_id=None if derived_from is None else derived_from.id,
        created_by_id=creator.id,
        created_at=moment,
        updated_at=moment,
    )
    db.session.add(definition)
    db.session.commit()
    return definition


def set_row(definition, code="SET-A", title="Placeholder set", order=0):
    moment = whole_second_utc()
    task_set = ExperimentTaskSet(
        definition_id=definition.id, set_code=code, title=title, display_order=order,
        created_at=moment, updated_at=moment,
    )
    db.session.add(task_set)
    db.session.commit()
    return task_set


def task_row(task_set, order=0, **overrides):
    moment = whole_second_utc()
    task = ExperimentTask(
        task_set_id=task_set.id, display_order=order, created_at=moment, updated_at=moment,
        **{**TASK, **overrides},
    )
    db.session.add(task)
    db.session.commit()
    return task


def activate_row(definition, actor, supersede=None):
    """Flip a complete draft to ``active`` directly, sealing its real
    digest, optionally superseding `supersede` first."""
    moment = whole_second_utc()
    actor_id = actor.id
    if supersede is not None:
        with db.session.no_autoflush:
            supersede.status = SUPERSEDED
            supersede.current_marker = None
            supersede.superseded_at = moment
            supersede.superseded_by_id = actor_id
            supersede.version = supersede.version + 1
            supersede.updated_at = moment
        db.session.flush()
    digest = content_digest(definition, _content(definition.id))
    with db.session.no_autoflush:
        definition.status = ACTIVE
        definition.current_marker = 1
        definition.content_digest = digest
        definition.activated_at = moment
        definition.activated_by_id = actor_id
        definition.version = definition.version + 1
        definition.updated_at = moment
    db.session.commit()
    return definition


def complete_row(creator, version="VA-1", tasks=1):
    """A draft with one set holding `tasks` tasks, written directly."""
    definition = definition_row(creator, version=version)
    task_set = set_row(definition)
    for order in range(tasks):
        task_row(task_set, order=order, title=f"Placeholder task {order + 1}")
    return definition


def discard_row(definition, actor):
    moment = whole_second_utc()
    actor_id = actor.id
    with db.session.no_autoflush:
        definition.status = DISCARDED
        definition.discarded_at = moment
        definition.discarded_by_id = actor_id
        definition.version = definition.version + 1
        definition.updated_at = moment
    db.session.commit()
    return definition


# ---------------------------------------------------------------------------
# Route helpers -- the application's own write path
# ---------------------------------------------------------------------------


def detail_url(public_id):
    return f"{PROTOCOLS_URL}/{public_id}"


def set_url(protocol_public_id, set_public_id):
    return f"{detail_url(protocol_public_id)}/sets/{set_public_id}"


def task_url(protocol_public_id, set_public_id, task_public_id):
    return f"{set_url(protocol_public_id, set_public_id)}/tasks/{task_public_id}"


def state_token(html):
    found = re.search(rf'name="{STATE_FIELD}" value="([^"]+)"', html)
    return None if found is None else found.group(1)


def move_tokens(html, action_url):
    """``{direction: token}`` for the move forms that post to `action_url`."""
    tokens = {}
    for form in re.findall(rf'<form method="post" action="{re.escape(action_url)}">(.*?)</form>',
                           html, re.S):
        direction = re.search(r'name="direction" value="(\w+)"', form).group(1)
        tokens[direction] = re.search(rf'name="{STATE_FIELD}" value="([^"]+)"', form).group(1)
    return tokens


def form_token(html, action_url):
    form = re.search(rf'action="{re.escape(action_url)}">(.*?)</form>', html, re.S)
    return None if form is None else state_token(form.group(1))


def definition(version):
    db.session.expire_all()
    return ExperimentDefinition.query.filter_by(version_identifier=version).one()


def sets_of(protocol):
    db.session.expire_all()
    return (ExperimentTaskSet.query.filter_by(definition_id=protocol.id)
            .order_by(ExperimentTaskSet.display_order, ExperimentTaskSet.id).all())


def tasks_of(task_set):
    db.session.expire_all()
    return (ExperimentTask.query.filter_by(task_set_id=task_set.id)
            .order_by(ExperimentTask.display_order, ExperimentTask.id).all())


def create(client, version="VA-1", title="Placeholder protocol", rationale=""):
    return client.post(
        NEW_URL,
        data={"version_identifier": version, "title": title,
              "equivalence_rationale": rationale},
        follow_redirects=True,
    )


def add_set(client, protocol, code="SET-A", title="Placeholder set", token=None):
    url = f"{detail_url(protocol.public_id)}/sets/new"
    if token is None:
        token = state_token(page(client, url))
    return client.post(url, data={STATE_FIELD: token or "", "set_code": code, "title": title},
                       follow_redirects=True)


def task_form(**overrides):
    fields = {**TASK, **overrides}
    fields["recommended_duration_seconds"] = str(fields["recommended_duration_seconds"])
    return fields


def add_task(client, protocol, task_set, token=None, **overrides):
    url = f"{set_url(protocol.public_id, task_set.public_id)}/tasks/new"
    if token is None:
        token = state_token(page(client, url))
    return client.post(url, data={STATE_FIELD: token or "", **task_form(**overrides)},
                       follow_redirects=True)


def review_url(protocol):
    return f"{detail_url(protocol.public_id)}/activate"


def activate(client, protocol, token=None, confirm="yes"):
    if token is None:
        token = state_token(page(client, review_url(protocol)))
    return client.post(review_url(protocol), data={STATE_FIELD: token or "", "confirm": confirm},
                       follow_redirects=True)


def discard(client, protocol, token=None, confirm="yes"):
    url = f"{detail_url(protocol.public_id)}/discard"
    if token is None:
        token = form_token(page(client, detail_url(protocol.public_id)), url)
    return client.post(url, data={STATE_FIELD: token or "", "confirm": confirm},
                       follow_redirects=True)


def derive(client, protocol, version, title="Placeholder protocol", token=None):
    url = f"{detail_url(protocol.public_id)}/new-version"
    if token is None:
        token = state_token(page(client, url))
    return client.post(url, data={STATE_FIELD: token or "", "version_identifier": version,
                                  "title": title}, follow_redirects=True)


def build(client, version="VA-1", sets=("SET-A",), tasks=1, rationale=""):
    """Create a complete draft through the routes. Returns the definition."""
    create(client, version=version, rationale=rationale)
    protocol = definition(version)
    for code in sets:
        add_set(client, protocol, code=code)
    for task_set in sets_of(protocol):
        for number in range(tasks):
            add_task(client, definition(version), task_set, title=f"Placeholder task {number + 1}")
    return definition(version)
