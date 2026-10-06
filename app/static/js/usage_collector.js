/* Natural-use collector -- Phase 6 research collection.
 * No dependency, no build step.
 *
 * WHEN IT RUNS
 *   In the background on every Student page, but only when the page carries
 *   #research-collector-config, which the server renders only for an eligible
 *   Student (an active, non-excluded Student account) while a configuration
 *   is collecting inside its period. It shows nothing on the page; the only
 *   visible element is the optional feedback question, and only when the
 *   server sampled and granted one.
 *
 * WHAT IT SENDS (natural-use-events.v1, see research_event_dictionary.py)
 *   Event types, allowlisted page/element identifiers, closed-set detail
 *   codes and bounded integers: page views and visible time, visibility
 *   changes, 30-second heartbeats while visible (active / idle /
 *   media_playing -- whether any input happened, never which), control
 *   clicks by declared identifier or structural kind, bounded repeated-click
 *   bursts, clicks on non-controls (no target, no position), the fact that
 *   an answer option, filter or file field changed, form submission attempts
 *   and client-side invalid-field counts, media play/pause/seek/ended/rate
 *   changes with a whole-second position, and speaking-recorder states.
 *
 * WHAT IT NEVER READS OR SENDS
 *   Typed text, key identities or key counts, field values, option
 *   identities, answers, search terms, message text, file names or
 *   contents, audio, URLs or query strings, DOM text or HTML, coordinates,
 *   user-agent strings or any fingerprint.
 *
 * DELIVERY
 *   Batched POSTs to the server with the CSRF token in X-CSRFToken, every
 *   one sent with fetch(..., {keepalive: true}) so that a navigation -- a
 *   submitted form, a followed link, a closed tab -- never cancels a batch
 *   already handed to the browser. There is no sendBeacon and no CSRF
 *   exemption. Each batch is written to a small per-tab outbox
 *   (sessionStorage) before it is sent and removed once the server answers;
 *   a batch whose answer never arrived (a network failure, a server error,
 *   or a page that navigated away first) is re-sent from the outbox with the
 *   same event ids, marked as a replay, at most three times. The server
 *   stores each event id at most once. The buffer and the outbox are
 *   bounded; overflow drops the oldest events and reports how many were
 *   dropped. When the server says collection is off, everything stops and
 *   the outbox is cleared.
 *
 * FAILURE ISOLATION
 *   Every handler is wrapped: an error here is swallowed, never prevents a
 *   default action, never blocks the page, and never affects a form
 *   submission. With JavaScript disabled nothing is collected and the LMS
 *   works exactly the same.
 */
(function () {
  "use strict";

  var configNode = document.getElementById("research-collector-config");
  if (!configNode || !window.fetch || !window.JSON) {
    return;
  }
  var config;
  try {
    config = JSON.parse(configNode.textContent);
  } catch (error) {
    return;
  }
  if (!config || !config.page || !config.eventsUrl ||
      typeof config.deliveryScope !== "string" || config.deliveryScope.length !== 64 ||
      !/^[0-9a-f]{64}$/.test(config.deliveryScope)) {
    return;
  }

  var stopped = false;
  var buffer = [];
  var dropped = 0;
  var sequence = 0;
  var sending = false;
  var inFlight = {};
  var OUTBOX_KEY = "usageResearchOutbox";
  var SCOPE_KEY = "usageResearchDeliveryScope";
  var MAX_OUTBOX = 4;
  var MAX_ATTEMPTS = 3;
  var REPEAT_WINDOW_MS = 1000;
  var dialog = document.getElementById("research-feedback");
  var handlingOffer = false;

  function uuid() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    var bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    var hex = Array.prototype.map.call(bytes, function (b) {
      return (b + 0x100).toString(16).slice(1);
    }).join("");
    return hex.slice(0, 8) + "-" + hex.slice(8, 12) + "-" + hex.slice(12, 16) + "-" +
      hex.slice(16, 20) + "-" + hex.slice(20);
  }

  function tabRef() {
    try {
      var existing = window.sessionStorage.getItem("usageResearchTab");
      if (existing && existing.length === 36) {
        return existing;
      }
      var fresh = uuid();
      window.sessionStorage.setItem("usageResearchTab", fresh);
      return fresh;
    } catch (error) {
      return uuid();
    }
  }

  var pageView = uuid();
  var tab = tabRef();

  function record(type, fields) {
    if (stopped) {
      return;
    }
    sequence += 1;
    var event = { id: uuid(), type: type, t: Date.now(), page: config.page, view: pageView,
                  tab: tab, seq: sequence };
    if (fields) {
      for (var key in fields) {
        if (Object.prototype.hasOwnProperty.call(fields, key) && fields[key] !== undefined &&
            fields[key] !== null) {
          event[key] = fields[key];
        }
      }
    }
    buffer.push(event);
    while (buffer.length > config.maxBuffer) {
      buffer.shift();
      dropped += 1;
    }
    if (buffer.length >= config.batchSize) {
      flush(false);
    }
  }

  function stop() {
    stopped = true;
    buffer = [];
    dropped = 0;
    // A late answer for an earlier page must preserve a newer scope's data.
    writeOutbox(readOutbox().filter(function (entry) { return !ownEntry(entry); }));
  }

  // ---------------------------------------------------------------- delivery

  function post(url, body, keepalive) {
    return window.fetch(url, {
      method: "POST",
      credentials: "same-origin",
      keepalive: !!keepalive,
      headers: { "Content-Type": "application/json", "X-CSRFToken": config.csrf },
      body: JSON.stringify(body)
    });
  }

  function readOutbox() {
    try {
      var list = JSON.parse(window.sessionStorage.getItem(OUTBOX_KEY) || "[]");
      return Array.isArray(list) ? list : [];
    } catch (error) {
      return [];
    }
  }

  function writeOutbox(list) {
    try {
      window.sessionStorage.setItem(OUTBOX_KEY, JSON.stringify(list));
    } catch (error) { /* no storage: delivery is then best-effort only */ }
  }

  function ownEntry(entry) {
    return !!(entry && entry.body && entry.body.delivery_scope === config.deliveryScope &&
              Array.isArray(entry.body.events));
  }

  function currentScope() {
    try {
      return window.sessionStorage.getItem(SCOPE_KEY) === config.deliveryScope;
    } catch (error) {
      return true; // Without storage, server verification still binds delivery.
    }
  }

  function adoptOutboxScope() {
    try {
      window.sessionStorage.setItem(SCOPE_KEY, config.deliveryScope);
    } catch (error) { /* Storage is optional, scope verification is not. */ }
    // No old-scope event or dropped count belongs to this new page's scope.
    writeOutbox(readOutbox().filter(ownEntry));
  }

  function remember(entry) {
    if (!currentScope()) {
      stop();
      return false;
    }
    var list = readOutbox().filter(ownEntry);
    list.push(entry);
    while (list.length > MAX_OUTBOX) {
      dropped += list.shift().body.events.length;
    }
    writeOutbox(list);
    return true;
  }

  function forget(key) {
    writeOutbox(readOutbox().filter(function (entry) {
      return !(ownEntry(entry) && entry.key === key);
    }));
  }

  function failedAttempt(key) {
    var list = readOutbox();
    list.forEach(function (entry) {
      if (ownEntry(entry) && entry.key === key) {
        entry.attempts += 1;
      }
    });
    writeOutbox(list.filter(function (entry) {
      if (ownEntry(entry) && entry.attempts >= MAX_ATTEMPTS) {
        dropped += entry.body.events.length;
        return false;
      }
      return true;
    }));
  }

  function handleAnswer(answer) {
    if (!currentScope()) {
      stop();
      return;
    }
    if (!answer) {
      return;
    }
    if (answer.collecting === false) {
      stop();
      return;
    }
    if (answer.prompt && answer.prompt.id) {
      handleOffer(answer.prompt.id);
    }
  }

  /* Send one outbox entry. The entry leaves the outbox when the server
     answers -- accepted, or refused for good (a 4xx other than 409/429,
     which a resend could not change). */
  function deliver(entry, replay) {
    if (!ownEntry(entry) || !currentScope()) {
      stop();
      return Promise.resolve();
    }
    if (stopped || inFlight[entry.key]) {
      return Promise.resolve();
    }
    inFlight[entry.key] = true;
    var body = entry.body;
    body.sent_at = Date.now();
    if (replay) {
      body.replay = true;
    }
    var request;
    try {
      request = post(config.eventsUrl, body, true);
    } catch (error) {
      delete inFlight[entry.key];
      failedAttempt(entry.key);
      return Promise.resolve();
    }
    return request
      .then(function (response) {
        if (response.status === 409 || response.status === 429 || response.status >= 500) {
          throw new Error("retry");
        }
        forget(entry.key);
        if (response.ok) {
          return response.json();
        }
        return response.json().then(function (answer) {
          if (answer && answer.error === "stale_delivery_scope") {
            stop();
          }
          return null;
        }, function () { return null; });
      })
      .then(function (answer) {
        delete inFlight[entry.key];
        handleAnswer(answer);
      })
      .catch(function () {
        delete inFlight[entry.key];
        failedAttempt(entry.key);
      });
  }

  function flush(unloading) {
    if (stopped || buffer.length === 0 || (sending && !unloading)) {
      return;
    }
    var body = { schema: config.schema, sent_at: Date.now(),
                 delivery_scope: config.deliveryScope,
                 events: buffer.splice(0, config.batchSize) };
    if (dropped) {
      body.dropped = Math.min(dropped, 10000);
      dropped = 0;
    }
    var entry = { key: uuid(), attempts: 0, body: body };
    if (!remember(entry)) {
      return;
    }
    if (unloading) {
      deliver(entry, false);
      return;
    }
    sending = true;
    deliver(entry, false).then(function () { sending = false; });
  }

  /* Re-send what an earlier page (or an earlier failure) left unanswered. */
  function replayOutbox() {
    if (stopped) {
      return;
    }
    readOutbox().filter(ownEntry).forEach(function (entry) { deliver(entry, true); });
  }

  adoptOutboxScope();

  // ----------------------------------------------------------- observations

  function viewportClass() {
    var width = window.innerWidth || document.documentElement.clientWidth || 0;
    return width < 640 ? "narrow" : (width < 1024 ? "medium" : "wide");
  }

  var visibleSince = document.visibilityState === "visible" ? Date.now() : null;
  var visibleMs = 0;
  var inputSeen = false;
  var pendingHidden = null;

  function mediaPlaying() {
    var media = document.querySelectorAll("audio, video");
    for (var i = 0; i < media.length; i += 1) {
      if (!media[i].paused && !media[i].ended) {
        return true;
      }
    }
    return false;
  }

  function markInput() { inputSeen = true; }

  function heartbeat() {
    if (document.visibilityState !== "visible") {
      return;
    }
    var detail = inputSeen ? "active" : (mediaPlaying() ? "media_playing" : "idle");
    inputSeen = false;
    record("heartbeat", { detail: detail });
  }

  function onVisibility() {
    if (document.visibilityState === "hidden") {
      if (visibleSince !== null) {
        visibleMs += Date.now() - visibleSince;
        visibleSince = null;
      }
      record("visibility_hidden");
      pendingHidden = window.setTimeout(function () { pendingHidden = null; flush(false); }, 300);
    } else {
      visibleSince = Date.now();
      record("visibility_visible");
      considerPendingOffer();
    }
  }

  function onPageHide() {
    if (pendingHidden !== null) {
      /* The page is unloading, not being hidden: a navigation must not end
         the observation run. Drop the hidden event that unloading caused. */
      window.clearTimeout(pendingHidden);
      pendingHidden = null;
      var last = buffer[buffer.length - 1];
      if (last && last.type === "visibility_hidden" && Date.now() - last.t < 1000) {
        buffer.pop();
      }
    }
    if (visibleSince !== null) {
      visibleMs += Date.now() - visibleSince;
      visibleSince = null;
    }
    record("page_leave", { duration_ms: Math.min(Math.max(0, visibleMs), 86400000) });
    while (buffer.length) {
      flush(true);
    }
  }

  function insideDialog(node) {
    return !!(dialog && node && dialog.contains(node));
  }

  function researchId(node) {
    var marked = node.closest ? node.closest("[data-research-id]") : null;
    return marked ? marked.getAttribute("data-research-id") : null;
  }

  var CONTROL_SELECTOR = "a[href], button, input, select, textarea, label, summary, [role='button']";
  var lastClick = { node: null, at: 0, count: 0, element: null, timer: null };

  function endBurst() {
    if (lastClick.count >= 2) {
      record("repeated_click", { element: lastClick.element, count: Math.min(lastClick.count, 50) });
    }
    lastClick = { node: null, at: 0, count: 0, element: null, timer: null };
  }

  function onClick(event) {
    var target = event.target;
    if (!target || insideDialog(target)) {
      return;
    }
    var control = target.closest ? target.closest(CONTROL_SELECTOR) : null;
    if (!control) {
      record("non_interactive_click");
      return;
    }
    var tag = control.tagName.toLowerCase();
    if (tag === "input" || tag === "select" || tag === "textarea" || tag === "label") {
      /* Inputs are observed through `change`; typing fields are never observed. */
      var kind = (control.getAttribute("type") || "").toLowerCase();
      if (tag !== "input" || (kind !== "submit" && kind !== "button")) {
        return;
      }
    }
    var element = researchId(control) ||
      (tag === "a" ? "other_link" : (tag === "button" || tag === "input" ? "other_button" : "other_control"));
    var now = Date.now();
    if (lastClick.node === control && now - lastClick.at < REPEAT_WINDOW_MS) {
      lastClick.count += 1;
      lastClick.at = now;
      window.clearTimeout(lastClick.timer);
      lastClick.timer = window.setTimeout(endBurst, REPEAT_WINDOW_MS);
      return;
    }
    if (lastClick.node) {
      window.clearTimeout(lastClick.timer);
      endBurst();
    }
    var fields = { element: element };
    if (element === "search_result") {
      var position = parseInt(control.getAttribute("data-research-position"), 10);
      if (position > 0 && position <= 500) {
        fields.position = position;
      }
    }
    record("control_click", fields);
    lastClick = { node: control, at: now, count: 1, element: element,
                  timer: window.setTimeout(endBurst, REPEAT_WINDOW_MS) };
  }

  var INPUT_IDS = { quiz_option: 1, listening_option: 1, quiz_submit_confirm: 1,
                    listening_submit_confirm: 1, speaking_file_select: 1, search_filter: 1 };

  function onChange(event) {
    var target = event.target;
    if (!target || insideDialog(target)) {
      return;
    }
    var element = researchId(target);
    if (element && INPUT_IDS[element]) {
      record("input_change", { element: element });
    }
  }

  function formId(form) {
    return (form && form.getAttribute("data-research-form")) || "other_form";
  }

  var invalidCounts = {};

  function onInvalid(event) {
    var target = event.target;
    if (!target || !target.form || insideDialog(target)) {
      return;
    }
    var id = formId(target.form);
    if (!invalidCounts[id]) {
      invalidCounts[id] = 0;
      window.setTimeout(function () {
        record("form_invalid", { element: id, count: Math.min(invalidCounts[id], 50) });
        delete invalidCounts[id];
      }, 0);
    }
    invalidCounts[id] += 1;
  }

  function onSubmit(event) {
    var form = event.target;
    if (!form || insideDialog(form)) {
      return;
    }
    record("form_submit", { element: formId(form) });
    /* The form is about to navigate: hand the batch to the browser now, with
       keepalive, so the navigation cannot cancel it. The submission itself
       is never delayed or prevented. */
    flush(true);
  }

  function watchMedia() {
    var media = document.querySelectorAll("audio[data-research-media], video[data-research-media]");
    Array.prototype.forEach.call(media, function (node) {
      var kind = node.getAttribute("data-research-media");
      function note(detail) {
        return function () {
          var fields = { element: kind, detail: detail };
          if (isFinite(node.currentTime)) {
            fields.position = Math.max(0, Math.min(86400, Math.floor(node.currentTime)));
          }
          record("media_event", fields);
        };
      }
      node.addEventListener("play", note("play"));
      node.addEventListener("pause", note("pause"));
      node.addEventListener("ended", note("ended"));
      node.addEventListener("seeked", note("seek"));
      node.addEventListener("ratechange", note("rate_change"));
    });
  }

  var RECORDER_STATES = { requesting: 1, recording: 1, preview: 1, uploading: 1, error: 1 };
  var RECORDER_FAILURES = { permission_denied: 1, unsupported: 1, recorder_error: 1 };

  function watchRecorder() {
    var root = document.querySelector("[data-speaking-recorder]");
    if (!root || typeof window.MutationObserver !== "function") {
      return;
    }
    new window.MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        var value = root.getAttribute(mutation.attributeName);
        if (mutation.attributeName === "data-recorder-state" && RECORDER_STATES[value]) {
          record("recorder_state", { element: "speaking_recorder", detail: value });
        } else if (mutation.attributeName === "data-recorder-failure" && RECORDER_FAILURES[value]) {
          record("recorder_failure", { element: "speaking_recorder", detail: value });
        }
      });
    }).observe(root, { attributes: true,
                       attributeFilter: ["data-recorder-state", "data-recorder-failure"] });
  }

  // ---------------------------------------------------------- feedback prompt

  var pendingOffer = null;
  var lastDeferral = null;

  function promptUrl(id, action) {
    return config.promptUrl.replace("PROMPT", encodeURIComponent(id)).replace("ACTION", action);
  }

  function deferralReason() {
    if (document.visibilityState !== "visible") {
      return "hidden_tab";
    }
    if (document.querySelector("[data-quiz-timer]")) {
      return "timed_activity";
    }
    var recorder = document.querySelector("[data-speaking-recorder]");
    var state = recorder ? recorder.getAttribute("data-recorder-state") : null;
    if (state === "recording" || state === "requesting") {
      return "recording";
    }
    if (state === "uploading") {
      return "uploading";
    }
    return null;
  }

  /* A prompt is shown at most once by this page, whatever the server says. */
  var handledPrompts = {};

  function handleOffer(id) {
    if (!dialog || handlingOffer || !dialog.hidden || handledPrompts[id]) {
      return;
    }
    var reason = deferralReason();
    if (reason) {
      /* Report each deferral once per offer and reason, not once per batch. */
      if (pendingOffer !== id || lastDeferral !== reason) {
        post(promptUrl(id, "defer"), { reason: reason }, false).catch(function () {});
      }
      pendingOffer = id;
      lastDeferral = reason;
      return;
    }
    lastDeferral = null;
    handlingOffer = true;
    post(promptUrl(id, "display"), {}, false)
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (answer) {
        handlingOffer = false;
        if (answer && answer.granted) {
          pendingOffer = null;
          handledPrompts[id] = true;
          showDialog(id);
        }
      })
      .catch(function () { handlingOffer = false; });
  }

  function considerPendingOffer() {
    if (pendingOffer && !deferralReason()) {
      var id = pendingOffer;
      pendingOffer = null;
      handleOffer(id);
    }
  }

  function message(kind) {
    var templates = dialog.querySelector("[data-feedback-messages]");
    var node = templates && templates.content ?
      templates.content.querySelector('[data-message="' + kind + '"]') : null;
    return node ? node.textContent : "";
  }

  function showDialog(id) {
    var form = dialog.querySelector("[data-feedback-form]");
    var submit = dialog.querySelector("[data-feedback-submit]");
    var skip = dialog.querySelector("[data-feedback-skip]");
    var status = dialog.querySelector("[data-feedback-status]");
    var title = dialog.querySelector("#research-feedback-title");
    var inputs = form.querySelectorAll("input");
    var done = false;
    var waiting = false;
    /* The one response this card has sent and the server has not settled
       yet. Once set it never changes: every retry sends exactly it. */
    var pending = null;

    [submit, skip].forEach(function (button) {
      if (!button.hasAttribute("data-default-label")) {
        button.setAttribute("data-default-label", button.textContent);
      }
      button.textContent = button.getAttribute("data-default-label");
    });
    form.reset();
    setInputs(true);
    submit.disabled = true;
    skip.disabled = false;
    status.textContent = "";
    dialog.hidden = false;
    if (title) {
      title.focus();
    }

    function setInputs(enabled) {
      Array.prototype.forEach.call(inputs, function (input) { input.disabled = !enabled; });
    }

    function finish(kind, delay) {
      done = true;
      status.textContent = message(kind);
      setInputs(false);
      submit.disabled = true;
      skip.disabled = true;
      form.removeEventListener("change", onRating);
      form.removeEventListener("submit", onAnswer);
      skip.removeEventListener("click", onSkip);
      dialog.removeEventListener("keydown", onKey);
      window.setTimeout(function () { dialog.hidden = true; }, delay);
    }

    /* The response may or may not have been saved: the request failed, or
       its answer never came back. Keep the choice that was sent, frozen and
       visible, until the server settles it. "Try again" resends exactly that
       choice; "Close" leaves without sending anything and claims nothing. */
    function uncertain() {
      status.textContent = message("failed");
      setInputs(false);
      submit.textContent = message("retry_label");
      skip.textContent = message("close_label");
      submit.disabled = false;
      skip.disabled = false;
    }

    /* Say "Thank you" only when the server confirms that this exact choice
       is the stored one. The server keeps the first stored response of a
       prompt and never replaces it, so a retry can neither create a second
       one nor change the first. */
    function send(body) {
      if (done || waiting) {
        return;
      }
      if (pending === null) {
        pending = body;
      }
      var dismissed = pending.dismissed === true;
      waiting = true;
      setInputs(false);
      submit.disabled = true;
      skip.disabled = true;
      status.textContent = "";
      post(promptUrl(id, "respond"), pending, false)
        .then(function (response) {
          return response.json()
            .catch(function () { return null; })
            .then(function (answer) { return { code: response.status, answer: answer || {} }; });
        })
        .then(function (result) {
          waiting = false;
          var answer = result.answer;
          if (result.code === 200 && answer.recorded) {
            finish(dismissed ? "dismissed" : "recorded", dismissed ? 0 : 1500);
          } else if (answer.collecting === false) {
            finish("unavailable", 3000);
          } else if (result.code === 409 && answer.status === "already") {
            finish(dismissed ? "dismissed" : "recorded", dismissed ? 0 : 1500);
          } else if (result.code === 409 && answer.status === "different") {
            finish("kept", 3000);
          } else if (result.code === 409 && answer.status === "late") {
            finish("expired", 3000);
          } else if (result.code === 409 || result.code === 404 || result.code === 400) {
            finish("unavailable", 3000);
          } else {
            uncertain();
          }
        })
        .catch(function () {
          waiting = false;
          uncertain();
        });
    }

    function close() {
      done = true;
      status.textContent = "";
      setInputs(false);
      form.removeEventListener("change", onRating);
      form.removeEventListener("submit", onAnswer);
      skip.removeEventListener("click", onSkip);
      dialog.removeEventListener("keydown", onKey);
      dialog.hidden = true;
    }

    function onRating() {
      if (!waiting && pending === null) {
        submit.disabled = !form.querySelector("input[name='rating']:checked");
      }
    }

    function onAnswer(event) {
      event.preventDefault();
      if (pending !== null) {
        send(pending);
        return;
      }
      var checked = form.querySelector("input[name='rating']:checked");
      if (!checked) {
        return;
      }
      var causes = Array.prototype.map.call(
        form.querySelectorAll("input[name='cause']:checked"), function (box) { return box.value; });
      send({ rating: parseInt(checked.value, 10), causes: causes });
    }

    function onSkip() {
      if (waiting) {
        return;
      }
      if (pending !== null) {
        close();
        return;
      }
      send({ dismissed: true });
    }

    function onKey(event) {
      if (event.key === "Escape") {
        onSkip();
      }
    }

    form.addEventListener("change", onRating);
    form.addEventListener("submit", onAnswer);
    skip.addEventListener("click", onSkip);
    dialog.addEventListener("keydown", onKey);
  }

  // ------------------------------------------------------------------- start

  function guard(handler) {
    return function (event) {
      try { handler(event); } catch (error) { /* never affect the page */ }
    };
  }

  try {
    var fields = { detail: viewportClass() };
    if (config.progress) {
      fields.position = config.progress.position;
      fields.count = config.progress.count;
    }
    record("page_view", fields);
    document.addEventListener("click", guard(onClick), true);
    document.addEventListener("change", guard(onChange), true);
    document.addEventListener("invalid", guard(onInvalid), true);
    document.addEventListener("submit", guard(onSubmit), true);
    ["pointerdown", "keydown", "wheel", "touchstart", "scroll"].forEach(function (type) {
      document.addEventListener(type, markInput, { capture: true, passive: true });
    });
    document.addEventListener("visibilitychange", guard(onVisibility));
    window.addEventListener("pagehide", guard(onPageHide));
    watchMedia();
    watchRecorder();
    window.setInterval(guard(heartbeat), config.heartbeatMs);
    window.setInterval(guard(function () { flush(false); replayOutbox(); }), config.flushMs);
    window.setInterval(guard(considerPendingOffer), 5000);
    window.setTimeout(guard(function () { replayOutbox(); flush(false); }), 1000);
  } catch (error) {
    stop();
  }
})();
