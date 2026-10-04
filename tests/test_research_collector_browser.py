"""The natural-use collector in a real browser (Phase 6 replacement,
corrected).

These tests run the real application on a local port (an isolated temporary
SQLite file, never MySQL), open it in headless Chrome, and let the real
``usage_collector.js`` talk to the real collection routes. A small WSGI
harness in front of the application only signs the Student in, injects a
driver script into Student pages, slows or fails chosen answers, and keeps a
log; every stored row comes from the real server.

1. **A form submission that navigates while its batch is being delivered.**
   The answer to the batch holding ``form_submit`` is held back until the
   browser has already left the page. The event is stored once; the next
   page re-sends the unanswered batch from the outbox with the same event
   ids, marked as a replay; the server stores nothing twice and counts no
   duplicate; the outbox is empty afterwards; every batch was sent with
   ``keepalive``.
2. **An answer is acknowledged only when the server confirms that exact
   choice.** After an attempt whose outcome is unknown (it failed before the
   server, or the server stored it and its answer was lost) the card keeps
   the sent choice frozen: the inputs are disabled, "Try again" resends
   exactly that choice and "Close" sends nothing. Scenarios: a failed
   request, then a lost answer, then "already" (one stored answer, "Thank
   you." only at the end); a lost answer followed by an attempt to change
   the rating and causes (the change cannot happen, the retry sends the
   original, "Thank you." refers to it); a lost answer followed by Close
   (nothing more is sent, nothing is acknowledged); a lost Skip followed by
   an attempt to rate (the dismissal is resent and stays the stored outcome,
   no "Thank you."); and a retry the server finds different from its stored
   answer (the card says the earlier response was kept, never "Thank you.").

Skipped when no Chrome or Chromium binary is available (set ``CHROME_BIN``
to point at one). These are browser checks against SQLite; they prove
nothing about MySQL.
"""

import io
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import pytest
from sqlalchemy import text
from werkzeug.serving import make_server
from werkzeug.wrappers import Request, Response

import tests.research_world as rw
from app import create_app
from app.blueprints.collector import hooks
from app.extensions import db
from app.models import CAUSE_COLUMNS, ResearchEvent, ResearchFeedbackPrompt, ResearchSession
from app.services import research_sampling as sampling

_CANDIDATES = (
    os.environ.get("CHROME_BIN") or "",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def _chrome():
    for candidate in _CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


CHROME = _chrome()
pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chrome/Chromium binary available")

#: Recorded in every page before the collector sends anything: each call to
#: the collection routes, with its keepalive flag, kept across navigations.
_FETCH_SPY = """
<script>
(function () {
  var original = window.fetch;
  window.fetch = function (url, options) {
    try {
      if (String(url).indexOf("/collect/") === 0) {
        var list = JSON.parse(sessionStorage.getItem("harnessFetches") || "[]");
        list.push({ url: String(url), keepalive: !!(options && options.keepalive) });
        sessionStorage.setItem("harnessFetches", JSON.stringify(list));
      }
    } catch (error) {}
    return original.apply(this, arguments);
  };
  window.harnessReport = function (kind, data) {
    var request = new XMLHttpRequest();
    request.open("POST", "/__harness/report", true);
    request.setRequestHeader("Content-Type", "application/json");
    request.send(JSON.stringify({ kind: kind, data: data }));
  };
})();
</script>
"""

_NAVIGATION_DRIVER = """
<script>
(function () {
  var step = sessionStorage.getItem("harnessStep") || "start";
  if (location.pathname === "/student/dashboard" && step === "start") {
    sessionStorage.setItem("harnessStep", "search");
    setTimeout(function () { location.href = "/student/search"; }, 1500);
  } else if (location.pathname === "/student/search" && step === "search") {
    sessionStorage.setItem("harnessStep", "results");
    setTimeout(function () {
      var form = document.querySelector('form[data-research-form="search_submit"]');
      form.querySelector('input[name="q"]').value = "harness";
      form.requestSubmit();
    }, 1500);
  } else if (location.pathname === "/student/search" && step === "results") {
    sessionStorage.setItem("harnessStep", "done");
    setTimeout(function () {
      window.harnessReport("final", {
        outbox: JSON.parse(sessionStorage.getItem("usageResearchOutbox") || "[]").length,
        fetches: JSON.parse(sessionStorage.getItem("harnessFetches") || "[]"),
        search: location.search
      });
    }, 6000);
  }
})();
</script>
"""

_ANSWER_DRIVER = """
<script>
(function () {
  var dialog = document.getElementById("research-feedback");
  if (!dialog) { return; }
  var form = dialog.querySelector("[data-feedback-form]");
  var submit = dialog.querySelector("[data-feedback-submit]");
  var skip = dialog.querySelector("[data-feedback-skip]");
  var status = dialog.querySelector("[data-feedback-status]");
  var FAILED = "We could not confirm that your response was saved. Please try again.";
  var KEPT = "Your earlier response to this question was already saved. It has not been changed.";
  var seen = [];
  new MutationObserver(function () {
    if (status.textContent) { seen.push(status.textContent); }
  }).observe(status, { childList: true, characterData: true, subtree: true });
  function state() {
    var rating = form.querySelector("input[name='rating']:checked");
    var inputs = form.querySelectorAll("input");
    return {
      hidden: dialog.hidden, status: status.textContent,
      rating: rating ? rating.value : null,
      causes: Array.prototype.map.call(form.querySelectorAll("input[name='cause']:checked"),
                                       function (box) { return box.value; }),
      inputsDisabled: Array.prototype.every.call(inputs, function (i) { return i.disabled; }),
      submitDisabled: submit.disabled, skipDisabled: skip.disabled,
      submitLabel: submit.textContent.trim(), skipLabel: skip.textContent.trim(),
      statuses: seen.slice()
    };
  }
  function report(kind) { window.harnessReport(kind, state()); }
  function rate(value) { form.querySelector("input[name='rating'][value='" + value + "']").click(); }
  function cause(code) { form.querySelector("input[name='cause'][value='" + code + "']").click(); }
  function shown() { return !dialog.hidden; }
  function uncertain() { return !dialog.hidden && status.textContent === FAILED && !submit.disabled; }
  function says(text) { return function () { return status.textContent === text; }; }
  function hidden() { return dialog.hidden; }
  function answerFour() { report("shown"); rate(4); cause("technical"); submit.click(); }
  var SCENARIOS = {
    retry_sequence: [
      [shown, answerFour],
      [uncertain, function () { report("after_failed_request"); submit.click(); }],
      [uncertain, function () { report("after_lost_answer"); submit.click(); }],
      [says("Thank you."), function () { report("final"); }],
      [hidden, function () { report("closed"); }]
    ],
    lost_then_change: [
      [shown, answerFour],
      [uncertain, function () {
        report("uncertain");
        rate(2); cause("content"); cause("technical");
        report("after_change_attempt");
        submit.click();
      }],
      [says("Thank you."), function () { report("final"); }],
      [hidden, function () { report("closed"); }]
    ],
    lost_then_close: [
      [shown, answerFour],
      [uncertain, function () { report("uncertain"); rate(1); skip.click(); }],
      [hidden, function () { report("closed"); }]
    ],
    skip_lost_then_rating: [
      [shown, function () { report("shown"); skip.click(); }],
      [uncertain, function () {
        report("uncertain");
        rate(5); cause("interface");
        report("after_change_attempt");
        submit.click();
      }],
      [hidden, function () { report("closed"); }]
    ],
    server_kept_earlier: [
      [shown, answerFour],
      [uncertain, function () { report("uncertain"); submit.click(); }],
      [says(KEPT), function () { report("final"); }],
      [hidden, function () { report("closed"); }]
    ]
  };
  var steps = SCENARIOS["%SCENARIO%"];
  var index = 0;
  var timer = setInterval(function () {
    if (index >= steps.length) { clearInterval(timer); return; }
    if (steps[index][0]()) {
      var action = steps[index][1];
      index += 1;
      action();
    }
  }, 50);
})();
</script>
"""


class Harness:
    """WSGI wrapper: sign-in shortcut, driver injection, slow or failing
    answers on request, and a log of every collection request."""

    def __init__(self, flask_app, driver, delay_form_submit=0.0, respond_plan=()):
        self.flask_app = flask_app
        self.driver = driver
        self.delay_form_submit = delay_form_submit
        self.respond_plan = list(respond_plan)
        self.lock = threading.Lock()
        self.batches = []
        self.responds = []
        self.reports = []
        self.page_loads = []

    def __call__(self, environ, start_response):
        request = Request(environ)
        path = request.path
        if path == "/__harness/login":
            email = parse_qs(environ.get("QUERY_STRING", "")).get("email", [""])[0]
            body = urlencode({"email": email, "password": rw.PW}).encode()
            environ.update({"REQUEST_METHOD": "POST", "PATH_INFO": "/auth/login",
                            "QUERY_STRING": "", "CONTENT_LENGTH": str(len(body)),
                            "CONTENT_TYPE": "application/x-www-form-urlencoded",
                            "wsgi.input": io.BytesIO(body)})
            return self.flask_app.wsgi_app(environ, start_response)
        if path == "/__harness/report":
            with self.lock:
                self.reports.append(json.loads(request.get_data() or b"{}"))
            return Response(status=204)(environ, start_response)
        if path == "/collect/events" and request.method == "POST":
            return self._events(request, environ, start_response)
        if path.startswith("/collect/prompts/") and path.endswith("/respond"):
            return self._respond(request, environ, start_response)
        response = Response.from_app(self.flask_app.wsgi_app, environ)
        if request.method == "GET" and response.mimetype == "text/html" \
                and path.startswith("/student/"):
            with self.lock:
                self.page_loads.append((time.monotonic(), path, request.query_string.decode()))
            html = response.get_data(as_text=True)
            html = html.replace("<head>", "<head>" + _FETCH_SPY, 1)
            response.set_data(html.replace("</body>", self.driver + "</body>", 1))
        return response(environ, start_response)

    def _events(self, request, environ, start_response):
        raw = request.get_data(cache=True)
        environ["wsgi.input"] = io.BytesIO(raw)
        environ["CONTENT_LENGTH"] = str(len(raw))
        payload = json.loads(raw)
        response = Response.from_app(self.flask_app.wsgi_app, environ)
        entry = {
            "replay": payload.get("replay") is True,
            "uids": [event.get("id") for event in payload.get("events", [])],
            "types": [event.get("type") for event in payload.get("events", [])],
            "status": response.status_code,
            "answer": json.loads(response.get_data() or b"null"),
            "csrf_header": bool(environ.get("HTTP_X_CSRFTOKEN")),
            "processed_at": time.monotonic(),
        }
        held = self.delay_form_submit and "form_submit" in entry["types"] and not entry["replay"]
        if held:
            # The server has stored the batch; its answer is held back until
            # the browser has navigated away, as on a slow connection.
            time.sleep(self.delay_form_submit)
        entry["answered_at"] = time.monotonic()
        with self.lock:
            self.batches.append(entry)
        return response(environ, start_response)

    def _respond(self, request, environ, start_response):
        raw = request.get_data(cache=True)
        environ["wsgi.input"] = io.BytesIO(raw)
        environ["CONTENT_LENGTH"] = str(len(raw))
        with self.lock:
            plan = self.respond_plan.pop(0) if self.respond_plan else "pass"
            self.responds.append({"plan": plan, "body": json.loads(raw)})
        if isinstance(plan, dict):
            # The server receives a different choice than the card sent (an
            # older collector, a second path): its reconciliation is tested.
            raw = json.dumps(plan).encode()
            environ["wsgi.input"] = io.BytesIO(raw)
            environ["CONTENT_LENGTH"] = str(len(raw))
        if plan == "fail_before_server":
            return Response(json.dumps({"error": "unavailable"}), status=503,
                            mimetype="application/json")(environ, start_response)
        response = Response.from_app(self.flask_app.wsgi_app, environ)
        with self.lock:
            self.responds[-1]["server_status"] = response.status_code
            self.responds[-1]["server_answer"] = json.loads(response.get_data() or b"null")
        if plan == "lose_answer":
            return Response("Bad gateway", status=502)(environ, start_response)
        return response(environ, start_response)


@pytest.fixture
def served(tmp_path, monkeypatch):
    """The application on a local port with an isolated SQLite file."""
    database = tmp_path / "browser.db"
    flask_app = create_app("testing", SQLALCHEMY_DATABASE_URI=f"sqlite:///{database.as_posix()}")
    with flask_app.app_context():
        db.create_all()
        rw.world(flask_app)
    servers = []

    def start(driver, **options):
        harness = Harness(flask_app, driver, **options)
        server = make_server("127.0.0.1", 0, harness, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        return harness, f"http://127.0.0.1:{server.server_port}"

    yield flask_app, start
    for server in servers:
        server.shutdown()
        server.server_close()
    with flask_app.app_context():
        db.session.remove()
        db.engine.dispose()


def _browse(url, profile, until, timeout=60):
    """Open `url` in headless Chrome and keep it running until `until()`
    holds (or the timeout passes), then close it."""
    process = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
         "--no-default-browser-check", "--disable-extensions",
         "--disable-background-networking", "--disable-renderer-backgrounding",
         "--disable-background-timer-throttling", f"--user-data-dir={profile}", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if until():
                return True
            time.sleep(0.2)
        return False
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=15)


def _report(harness, kind):
    with harness.lock:
        return next((r["data"] for r in harness.reports if r["kind"] == kind), None)


def test_a_navigating_form_submission_is_delivered_once_and_replayed_safely(served, tmp_path):
    flask_app, start = served
    harness, base = start(_NAVIGATION_DRIVER, delay_form_submit=3.0)
    finished = _browse(f"{base}/__harness/login?email=s1@example.com", tmp_path / "profile",
                       until=lambda: _report(harness, "final") is not None)
    assert finished, (harness.batches, harness.reports)
    final = _report(harness, "final")
    assert final["search"].startswith("?q=harness&")

    with harness.lock:
        batches = list(harness.batches)
        loads = list(harness.page_loads)
    originals = [b for b in batches if "form_submit" in b["types"] and not b["replay"]]
    assert len(originals) == 1
    original = originals[0]
    assert original["status"] == 200 and original["answer"]["accepted"] >= 1
    assert original["csrf_header"]
    # The browser had already navigated to the results page while the
    # answer to that batch was still being held back.
    results_load = next(t for t, path, query in loads if query.startswith("q=harness&"))
    assert original["processed_at"] < results_load < original["answered_at"]

    # The unanswered batch was re-sent from the outbox with the same ids,
    # marked as a replay, and the server stored nothing twice.
    replays = [b for b in batches if b["replay"] and b["uids"] == original["uids"]]
    assert replays, batches
    for replay in replays:
        assert replay["status"] == 200
        assert replay["answer"]["accepted"] == 0
        assert replay["answer"]["duplicates"] == len(original["uids"])
    assert final["outbox"] == 0
    event_fetches = [f for f in final["fetches"] if f["url"] == "/collect/events"]
    assert event_fetches and all(f["keepalive"] for f in event_fetches)

    with flask_app.app_context():
        submits = ResearchEvent.query.filter_by(event_type="form_submit").all()
        assert [(e.element_id, e.page_id) for e in submits] == [("search_submit",
                                                                  "student.search")]
        all_uids = [row[0] for row in db.session.query(ResearchEvent.event_uid)]
        assert len(all_uids) == len(set(all_uids))
        session = ResearchSession.query.one()
        assert session.events_duplicate == 0
        assert session.batches_received == len([b for b in batches if not b["replay"]])
        searches = ResearchEvent.query.filter_by(source="server", event_type="search").all()
        assert len(searches) == 1


FAILED = "We could not confirm that your response was saved. Please try again."
KEPT = "Your earlier response to this question was already saved. It has not been changed."
_FROZEN_FOUR = {"hidden": False, "status": FAILED, "rating": "4", "causes": ["technical"],
                "inputsDisabled": True, "submitDisabled": False, "skipDisabled": False,
                "submitLabel": "Try again", "skipLabel": "Close"}


def _answer_run(served, tmp_path, monkeypatch, scenario, plan):
    """Sign s1 in, let the server offer and grant a prompt, and drive the
    card through `scenario` while the harness applies `plan` to the
    successive answers."""
    flask_app, start = served
    monkeypatch.setattr(hooks, "HEARTBEAT_MS", 1000)
    monkeypatch.setattr(hooks, "FLUSH_MS", 1000)
    monkeypatch.setattr(sampling, "_draw", lambda: 0)
    harness, base = start(_ANSWER_DRIVER.replace("%SCENARIO%", scenario), respond_plan=plan)
    seeded = []

    def seed_observation_then_wait():
        # After the first stored batch, give the session 150 s of prior
        # continuous observation (time travel, as rw.age_session does), so
        # the next batch is eligible for an offer.
        if not seeded:
            with harness.lock:
                ready = any(b["answer"] and b["answer"].get("accepted") for b in harness.batches)
            if ready:
                with flask_app.app_context():
                    db.session.execute(text(
                        "UPDATE research_sessions SET started_at_ms = started_at_ms - 150000,"
                        " observed_since_ms = observed_since_ms - 150000"))
                    db.session.commit()
                seeded.append(True)
        return _report(harness, "closed") is not None

    finished = _browse(f"{base}/__harness/login?email=s1@example.com", tmp_path / "profile",
                       until=seed_observation_then_wait, timeout=90)
    assert finished, (harness.responds, harness.reports)
    time.sleep(1.0)   # anything the card might still send would arrive now
    with harness.lock:
        responds = list(harness.responds)
    with flask_app.app_context():
        prompt = ResearchFeedbackPrompt.query.one()
        stored = (prompt.status, prompt.rating,
                  sorted(code for code, column, _label in CAUSE_COLUMNS
                         if getattr(prompt, column)),
                  prompt.window_end_ms == prompt.displayed_at_ms <= prompt.responded_at_ms)
    return harness, responds, stored


def test_an_unrecorded_answer_is_never_acknowledged_and_a_retry_stores_one(served, tmp_path,
                                                                            monkeypatch):
    harness, responds, stored = _answer_run(
        served, tmp_path, monkeypatch, "retry_sequence",
        ["fail_before_server", "lose_answer", "pass"])
    shown = _report(harness, "shown")
    assert shown["hidden"] is False and shown["status"] == ""
    # 1. The request never reached the server; 2. the server stored it but
    #    its answer was lost. Both leave the card frozen on the sent choice.
    assert _report(harness, "after_failed_request") == dict(_FROZEN_FOUR, statuses=[FAILED])
    assert _report(harness, "after_lost_answer") == dict(_FROZEN_FOUR,
                                                         statuses=[FAILED, FAILED])
    # 3. The retry is told this exact choice is stored: now, and only now, thanks.
    final = _report(harness, "final")
    assert final["status"] == "Thank you." and final["statuses"] == [FAILED, FAILED,
                                                                    "Thank you."]
    assert _report(harness, "closed")["hidden"] is True
    assert [r["plan"] for r in responds] == ["fail_before_server", "lose_answer", "pass"]
    assert all(r["body"] == {"rating": 4, "causes": ["technical"]} for r in responds)
    assert "server_status" not in responds[0]
    assert (responds[1]["server_status"], responds[1]["server_answer"]["recorded"]) == (200,
                                                                                       True)
    assert (responds[2]["server_status"], responds[2]["server_answer"]["status"]) == (409,
                                                                                     "already")
    # The question's own interaction is outside the window it asked about.
    assert stored == ("answered", 4, ["technical"], True)


def test_a_lost_answer_cannot_be_changed_and_the_thanks_is_for_the_stored_choice(
        served, tmp_path, monkeypatch):
    harness, responds, stored = _answer_run(served, tmp_path, monkeypatch, "lost_then_change",
                                            ["lose_answer", "pass"])
    assert _report(harness, "uncertain") == dict(_FROZEN_FOUR, statuses=[FAILED])
    # Trying to pick rating 2 and other causes changes nothing on screen ...
    assert _report(harness, "after_change_attempt") == dict(_FROZEN_FOUR, statuses=[FAILED])
    # ... nor on the wire: the retry sends the original choice.
    assert [r["body"] for r in responds] == [{"rating": 4, "causes": ["technical"]}] * 2
    assert responds[1]["server_answer"]["status"] == "already"
    final = _report(harness, "final")
    assert final["rating"] == "4" and final["causes"] == ["technical"]
    assert final["statuses"] == [FAILED, "Thank you."]
    assert stored == ("answered", 4, ["technical"], True)


def test_closing_after_a_lost_answer_sends_and_claims_nothing(served, tmp_path, monkeypatch):
    harness, responds, stored = _answer_run(served, tmp_path, monkeypatch, "lost_then_close",
                                            ["lose_answer"])
    assert _report(harness, "uncertain") == dict(_FROZEN_FOUR, statuses=[FAILED])
    closed = _report(harness, "closed")
    assert closed["hidden"] is True and "Thank you." not in closed["statuses"]
    # Close is not Skip: no dismissal was sent, and the stored answer stands.
    assert [r["body"] for r in responds] == [{"rating": 4, "causes": ["technical"]}]
    assert stored == ("answered", 4, ["technical"], True)


def test_a_lost_skip_stays_the_outcome_and_a_rating_is_never_claimed(served, tmp_path,
                                                                     monkeypatch):
    harness, responds, stored = _answer_run(served, tmp_path, monkeypatch,
                                            "skip_lost_then_rating", ["lose_answer", "pass"])
    frozen_skip = {"hidden": False, "status": FAILED, "rating": None, "causes": [],
                   "inputsDisabled": True, "submitDisabled": False, "skipDisabled": False,
                   "submitLabel": "Try again", "skipLabel": "Close", "statuses": [FAILED]}
    assert _report(harness, "uncertain") == frozen_skip
    assert _report(harness, "after_change_attempt") == frozen_skip
    assert [r["body"] for r in responds] == [{"dismissed": True}] * 2
    assert responds[1]["server_answer"]["status"] == "already"
    closed = _report(harness, "closed")
    assert closed["hidden"] is True and "Thank you." not in closed["statuses"]
    assert stored == ("dismissed", None, [], True)


def test_a_retry_the_server_finds_different_is_reported_as_kept(served, tmp_path,
                                                                monkeypatch):
    harness, responds, stored = _answer_run(
        served, tmp_path, monkeypatch, "server_kept_earlier",
        ["lose_answer", {"rating": 2, "causes": ["content"]}])
    assert _report(harness, "uncertain") == dict(_FROZEN_FOUR, statuses=[FAILED])
    assert responds[1]["body"] == {"rating": 4, "causes": ["technical"]}
    assert (responds[1]["server_status"], responds[1]["server_answer"]["status"]) == (
        409, "different")
    final = _report(harness, "final")
    assert final["statuses"] == [FAILED, KEPT] and "Thank you." not in final["statuses"]
    assert _report(harness, "closed")["hidden"] is True
    assert stored == ("answered", 4, ["technical"], True)
