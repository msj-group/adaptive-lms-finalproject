/* Versioned, mobile-first research observations. No business request is intercepted.
 * Closed semantic codes only: never read text, answers, URLs, key identities,
 * coordinates, file names, recordings or account/message field values.
 * Research questionnaire interactions are excluded from the observation stream.
 * The bounded acknowledgement outbox is best-effort; phone delivery is not assured.
 */
(function () {
  "use strict";
  var configNode = document.getElementById("research-v2-config");
  var config;
  try { config = configNode ? JSON.parse(configNode.textContent) : window.researchV2Config; }
  catch (_) { return; }
  if (!config || config.schema_version !== "natural-use-events.v2" ||
      typeof config.scope_token !== "string" || !config.scope_token ||
      !window.fetch || !window.crypto || !window.crypto.getRandomValues) return;

  var CONTEXTS = ["dashboard", "course_list", "course", "settings", "activity_list", "records",
    "lesson", "reader", "search", "assignment", "quiz", "listening", "speaking", "attendance",
    "grades", "announcement", "calendar", "discussion", "messaging", "notifications", "help", "unknown"];
  var ACTIONS = ["navigate", "open", "close", "search", "save", "submit", "answer", "complete",
    "undo_complete", "start", "next", "previous", "retry", "upload", "download", "play", "pause",
    "replay", "seek", "record", "send", "help", "revise", "unknown"];
  var DETAILS = ["visible", "hidden", "busy", "idle", "playing", "buffering", "paused", "ended",
    "started", "finished", "timeout", "network_unknown", "unknown", "changed", "opened", "replay",
    "playback_error", "background", "delivery_gap", "clock_unknown", "phone", "tablet", "desktop",
    "device_unknown", "pointer_supported", "pointer_unsupported", "media_supported", "media_unsupported",
    "visible_supported", "visible_unsupported"];
  var TYPES = ["context_enter", "context_leave", "visible_interval", "action_intent", "request_start",
    "request_finish", "revision_count", "ordinary_help", "media_interval", "media_action",
    "observation_gap", "measurement_support"];
  var UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  var OUTBOX = "researchV2Outbox", SCOPE = "researchV2Scope", TAB = "researchV2Tab";
  var SAMPLING = "researchV2Sampling";
  var policy = config.policy || {};
  var BATCH_SIZE = bounded(policy.batch_size, 1, 50, 30);
  var MAX_BUFFER = bounded(policy.max_buffer, BATCH_SIZE, 200, 200);
  var MAX_BATCHES = 4, MAX_ATTEMPTS = 4, MAX_AGE_MS = 1800000;
  var stopped = false, sequence = 0, buffer = [], dropped = 0;
  var sending = false, memoryOutbox = [], storageAvailable = true;
  var pageRef = uuid(), tabRef = uuid(), contextRef = uuid();
  var card = document.getElementById("research-feedback-v2");
  var page = code(config.page_code, CONTEXTS), context = code(config.context_code, CONTEXTS);
  var activity = code(config.activity_code, CONTEXTS);
  var clock = null, clockBusy = false;
  var visibleStart = document.visibilityState === "visible" ? Date.now() : null;
  var observableMs = 0, nextSampleMs = 60000, sampleBusy = false, deliveryReady = false;
  var pendingPrompt = null, promptBusy = false, activePrompt = null;
  var activeContextNode = null;
  var formActions = new WeakMap(), formReservations = new WeakMap(), nodeContexts = new WeakMap(), mediaStates = new WeakMap();
  var pendingRequests = new Map(), revisionTimers = new WeakMap();

  function bounded(value, low, high, fallback) {
    return Number.isInteger(value) && value >= low && value <= high ? value : fallback;
  }
  function code(value, list) { return list.indexOf(value) >= 0 ? value : "unknown"; }
  function uuid() {
    if (window.crypto.randomUUID) return window.crypto.randomUUID();
    var bytes = new Uint8Array(16); window.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    var text = Array.prototype.map.call(bytes, function (n) { return (n + 256).toString(16).slice(1); }).join("");
    return text.slice(0, 8) + "-" + text.slice(8, 12) + "-" + text.slice(12, 16) + "-" + text.slice(16, 20) + "-" + text.slice(20);
  }
  function guard(fn) { return function (event) { try { fn(event); } catch (_) { /* LMS remains usable. */ } }; }
  function ownScope() {
    try { return !storageAvailable || window.sessionStorage.getItem(SCOPE) === config.scope_token; }
    catch (_) { return true; }
  }
  function readOutbox() {
    if (!storageAvailable) return memoryOutbox;
    try {
      var list = JSON.parse(window.sessionStorage.getItem(OUTBOX) || "[]");
      return Array.isArray(list) ? list : [];
    } catch (_) { storageAvailable = false; return memoryOutbox; }
  }
  function writeOutbox(list) {
    memoryOutbox = list;
    if (storageAvailable) {
      try { window.sessionStorage.setItem(OUTBOX, JSON.stringify(list)); }
      catch (_) { storageAvailable = false; }
    }
  }
  function ownEntry(entry) {
    return entry && entry.scope === config.scope_token && UUID.test(entry.batch_ref) &&
      Array.isArray(entry.events) && entry.events.length <= BATCH_SIZE &&
      Number.isInteger(entry.created_at_ms) && Number.isInteger(entry.attempts);
  }
  function restoreSamplingProgress() {
    if (!storageAvailable) return;
    try {
      var saved = JSON.parse(window.sessionStorage.getItem(SAMPLING) || "null");
      var age = saved && Date.now() - saved.updated_at_ms;
      if (saved && saved.scope === config.scope_token && Number.isInteger(saved.updated_at_ms) &&
          age >= 0 && age <= MAX_AGE_MS && Number.isSafeInteger(saved.observed_ms) && saved.observed_ms >= 0 &&
          saved.observed_ms <= Number.MAX_SAFE_INTEGER - 60000 &&
          Number.isSafeInteger(saved.next_sample_ms) && saved.next_sample_ms >= 60000) {
        observableMs = saved.observed_ms; nextSampleMs = saved.next_sample_ms;
      }
    } catch (_) { /* A malformed or unavailable checkpoint starts a fresh local cadence. */ }
  }
  function saveSamplingProgress() {
    if (!storageAvailable || !ownScope()) return;
    try {
      window.sessionStorage.setItem(SAMPLING, JSON.stringify({scope: config.scope_token,
        observed_ms: observableMs, next_sample_ms: nextSampleMs, updated_at_ms: Date.now()}));
    } catch (_) { /* Server-side observation time and burden limits remain authoritative. */ }
  }
  function stop() {
    stopped = true; buffer = []; dropped = 0;
    // Late responses must never erase a newer login/configuration's outbox.
    writeOutbox(readOutbox().filter(function (entry) { return !ownEntry(entry); }));
    if (card) card.hidden = true;
  }
  function envelope(fields) {
    return Object.assign({schema_version: config.schema_version, scope_token: config.scope_token}, fields || {});
  }
  function sameOriginPath(url) {
    try { var parsed = new URL(url, window.location.href); return parsed.origin === window.location.origin; }
    catch (_) { return false; }
  }
  function post(url, body, keepalive) {
    if (!sameOriginPath(url)) return Promise.reject(new Error("invalid_endpoint"));
    var controller = window.AbortController ? new AbortController() : null;
    var timer = controller ? window.setTimeout(function () { controller.abort(); }, 15000) : null;
    return window.fetch(url, {method: "POST", credentials: "same-origin", keepalive: !!keepalive,
      signal: controller ? controller.signal : undefined,
      headers: {"Content-Type": "application/json", "X-CSRFToken": config.csrf}, body: JSON.stringify(body)})
      .then(function (response) { if (timer) window.clearTimeout(timer); return response; }, function (error) {
        if (timer) window.clearTimeout(timer); throw error;
      });
  }
  function jsonPost(url, body, terminalErrors) {
    return post(url, envelope(body), false).then(function (response) {
      return response.json().then(function (data) {
        if (data.scope_valid === false || data.collecting === false) stop();
        if (!response.ok && !(terminalErrors && response.status < 500 && response.status !== 429)) throw new Error("request_unconfirmed");
        return data;
      });
    });
  }
  function clockFields() {
    if (clock && Math.abs((Date.now() - clock.wall) - (window.performance.now() - clock.mono)) > 100) clock = null;
    return clock ? {clock_offset_ms: clock.offset, clock_uncertainty_ms: clock.uncertainty} : {};
  }
  function calibrateClock() {
    if (clockBusy || stopped || !config.clock_url) return;
    clockBusy = true;
    var before = Date.now(), started = window.performance.now();
    jsonPost(config.clock_url, {client_at_ms: before}).then(function (data) {
      var elapsed = window.performance.now() - started, after = Date.now();
      if (Number.isInteger(data.server_now_ms) && elapsed < 10000 && Math.abs(after - before - elapsed) <= 100) {
        clockFields();
        var recovered = clock === null;
        clock = {offset: Math.round(data.server_now_ms - (before + after) / 2), uncertainty: Math.ceil(elapsed / 2) + 1,
          wall: after, mono: window.performance.now()};
        if (recovered) {
          // Fresh measurements after recovery; never rewrite a previously queued batch's clock.
          declareMeasurementSupport();
          record("context_enter", {detail_code: "visible"});
        }
      } else { clock = null; }
    }).catch(function () { clock = null; }).then(function () { clockBusy = false; });
  }
  function record(type, fields) {
    if (stopped || !ownScope() || TYPES.indexOf(type) < 0) return;
    fields = fields || {};
    var event = {event_uid: uuid(), event_type: type, page_code: page, context_code: context,
      activity_code: activity, context_ref: contextRef, client_at_ms: Date.now(),
      client_sequence: ++sequence, page_ref: pageRef, tab_ref: tabRef};
    ["action_ref", "attempt_ref"].forEach(function (key) { if (UUID.test(fields[key])) event[key] = fields[key]; });
    if (ACTIONS.indexOf(fields.action_code) >= 0) event.action_code = fields.action_code;
    if (DETAILS.indexOf(fields.detail_code) >= 0) event.detail_code = fields.detail_code;
    if (Number.isInteger(fields.count) && fields.count >= 1 && fields.count <= 1000) event.count = fields.count;
    if (Number.isInteger(fields.client_at_ms)) event.client_at_ms = fields.client_at_ms;
    if (Number.isInteger(fields.interval_started_at_ms) && fields.interval_started_at_ms <= event.client_at_ms &&
        event.client_at_ms - fields.interval_started_at_ms <= 45000) event.interval_started_at_ms = fields.interval_started_at_ms;
    buffer.push(event);
    while (buffer.length > MAX_BUFFER) { buffer.shift(); dropped += 1; }
    if (buffer.length >= BATCH_SIZE) flush(false);
  }
  function declareMeasurementSupport() {
    record("measurement_support", {detail_code: document.visibilityState ? "visible_supported" : "visible_unsupported"});
    record("measurement_support", {detail_code: window.PointerEvent ? "pointer_supported" : "pointer_unsupported"});
    record("measurement_support", {detail_code: typeof document.createElement("audio").canPlayType === "function" ? "media_supported" : "media_unsupported"});
    // These are coarse viewport classes, not physical device identification.
    // A narrow desktop window can report phone; never use this as a fingerprint.
    var width = window.innerWidth || 0;
    record("measurement_support", {detail_code: width > 0 ? (width < 640 ? "phone" : width < 1024 ? "tablet" : "desktop") : "device_unknown"});
  }
  function rememberBatch() {
    if (!buffer.length) return;
    var list = readOutbox().filter(ownEntry);
    list.push({scope: config.scope_token, batch_ref: uuid(), events: buffer.splice(0, BATCH_SIZE),
      created_at_ms: Date.now(), attempts: 0, dropped: Math.min(dropped, 100000), clock: clockFields()});
    dropped = 0;
    while (list.length > MAX_BATCHES) dropped += list.shift().events.length;
    writeOutbox(list);
  }
  function settleEntry(batchRef, data) {
    var acknowledged = [];
    [data.accepted_event_uids, data.rejected_event_uids].forEach(function (list) {
      if (Array.isArray(list)) acknowledged = acknowledged.concat(list.filter(function (id) { return UUID.test(id); }));
    });
    writeOutbox(readOutbox().filter(function (entry) {
      if (!ownEntry(entry) || entry.batch_ref !== batchRef) return true;
      entry.events = entry.events.filter(function (event) { return acknowledged.indexOf(event.event_uid) < 0; });
      return entry.events.length > 0;
    }));
  }
  function deliver(entry, keepalive) {
    if (!ownScope() || stopped) { stop(); return Promise.resolve(); }
    entry.attempts += 1;
    var list = readOutbox();
    list.forEach(function (stored) { if (ownEntry(stored) && stored.batch_ref === entry.batch_ref) stored.attempts = entry.attempts; });
    writeOutbox(list);
    var body = envelope(Object.assign({batch_ref: entry.batch_ref, attempt_ref: uuid(), client_now_ms: Date.now(),
      events: entry.events, dropped_client: entry.dropped}, entry.clock || {}));
    return post(config.batch_url, body, keepalive).then(function (response) {
      return response.json().then(function (data) {
        if (data.scope_valid === false || data.collecting === false) { stop(); return; }
        if (response.ok && ownScope()) {
          deliveryReady = deliveryReady || Array.isArray(data.accepted_event_uids) &&
            data.accepted_event_uids.some(function (id) {
              return entry.events.some(function (event) { return event.event_uid === id; });
            });
          settleEntry(entry.batch_ref, data);
        }
      });
    }).catch(function () { /* Retain unacknowledged events; bounded retries only. */ });
  }
  function flush(keepalive) {
    if (stopped || !ownScope()) return Promise.resolve();
    while (buffer.length) rememberBatch();
    if (sending) return Promise.resolve();
    var list = readOutbox().filter(function (entry) {
      if (!ownEntry(entry)) return false;
      if (entry.attempts >= MAX_ATTEMPTS || Date.now() - entry.created_at_ms > MAX_AGE_MS) {
        dropped += entry.events.length; return false;
      }
      return true;
    });
    writeOutbox(list);
    if (!list.length) return Promise.resolve();
    sending = true;
    return deliver(list[0], keepalive).then(function () {
      sending = false;
      // A successor page can use acknowledged intervals left by the previous page.
      if (!keepalive && deliveryReady) sample();
    });
  }

  function insideResearch(node) { return !!(card && node && card.contains(node)); }
  function visible(node) { return node && !node.hidden && node.getClientRects().length > 0; }
  function detectContext() {
    var declared = document.querySelector("[data-research-v2-context]:not([hidden])");
    var next = declared && visible(declared) ? code(declared.getAttribute("data-research-v2-context"), CONTEXTS) : code(config.context_code, CONTEXTS);
    var node = declared && visible(declared) ? declared : null;
    var announcement = document.querySelector("[data-announcement-popup][open] [data-announcement-reader]");
    var reader = document.querySelector("[data-atlas-reader]");
    var search = document.querySelector("[data-workspace-search][open]");
    if (visible(search)) { next = "search"; node = search; }
    else if (visible(announcement)) { next = "announcement"; node = announcement; }
    else if (visible(reader)) { next = "reader"; node = reader; }
    if (next === context && node === activeContextNode) return;
    closeVisibleInterval();
    record("context_leave", {detail_code: "finished"});
    context = next; activity = next === "reader" ? code(config.activity_code, CONTEXTS) : next;
    contextRef = node && nodeContexts.get(node) || uuid(); activeContextNode = node;
    if (node) nodeContexts.set(node, contextRef);
    record("context_enter", {detail_code: "started"});
  }
  function criticalDeferral() {
    if (document.visibilityState !== "visible") return "hidden_tab";
    if (visible(document.querySelector("[data-quiz-timer]"))) return "timed_activity";
    var recorder = document.querySelector("[data-speaking-recorder]");
    var state = recorder && recorder.getAttribute("data-recorder-state");
    if (state === "requesting" || state === "recording") return "recording";
    if (state === "uploading" || Array.from(pendingRequests.values()).some(function (item) { return item.action_code === "upload"; })) return "uploading";
    var dialogs = document.querySelectorAll("dialog[open], [aria-modal='true']:not([hidden])");
    for (var i = 0; i < dialogs.length; i += 1) if (dialogs[i] !== card && visible(dialogs[i])) return "blocking_dialog";
    return null;
  }
  function closeVisibleInterval() {
    if (visibleStart === null) return;
    var now = Date.now(), elapsed = now - visibleStart;
    if (elapsed > 0 && elapsed <= 45000) {
      record("visible_interval", {interval_started_at_ms: visibleStart, client_at_ms: now, detail_code: "visible"});
      observableMs += elapsed;
      saveSamplingProgress();
    } else if (elapsed > 45000 || elapsed < 0) {
      record("observation_gap", {detail_code: elapsed < 0 ? "clock_unknown" : "background"});
      if (elapsed < 0) clock = null;
    }
    visibleStart = document.visibilityState === "visible" ? now : null;
  }
  var ACTION_MAP = {
    lesson_complete: "complete", lesson_undo_complete: "undo_complete", lesson_open: "open", course_open: "open",
    course_outline: "navigate", activity_open: "open", activity_filter: "search", search_submit: "search", search_result: "open",
    quiz_start: "start", listening_start: "start", quiz_save: "save", listening_save: "save",
    quiz_submit: "submit", listening_submit: "submit", quiz_previous: "previous", listening_previous: "previous",
    quiz_next: "next", listening_next: "next", listening_save_next: "next", quiz_question_jump: "navigate",
    quiz_bookmark: "save", assignment_submit: "submit", speaking_submit: "upload", speaking_file_select: "upload",
    speaking_record_start: "record", speaking_record_stop: "record", speaking_record_again: "retry",
    material_download: "download", material_external_link: "open", discussion_reply: "send", message_send: "send",
    help_open: "help", nav_help: "help", nav_search: "search"
  };
  function actionCode(node) {
    if (!node) return null;
    if (node.matches("[data-quiz-save-retry]")) return "retry";
    var explicit = node.getAttribute("data-research-v2-action");
    if (ACTIONS.indexOf(explicit) >= 0) return explicit;
    var identity = node.getAttribute("data-research-id") || node.getAttribute("data-research-form");
    if (Object.prototype.hasOwnProperty.call(ACTION_MAP, identity)) return ACTION_MAP[identity];
    if (/^nav_(dashboard|courses|activities|records|calendar|messages|settings|notifications|account|display|more|assignments|quizzes|listening|speaking|attendance|grades|announcements|discussions|logout)$/.test(identity || "")) return "navigate";
    return null;
  }
  function activate(action) {
    var ref = uuid(); record("action_intent", {action_ref: ref, action_code: action});
    if (action === "help") record("ordinary_help", {action_ref: ref, action_code: "help", detail_code: "opened"});
    return {action_ref: ref, action_code: action, at: Date.now()};
  }
  function hiddenReference(form, name, value) {
    var field = form.querySelector("input[type='hidden'][name='" + name + "']");
    if (!field) { field = document.createElement("input"); field.type = "hidden"; field.name = name; form.appendChild(field); }
    field.value = value;
  }
  function beginRequest(options) {
    options = options || {};
    var form = options.form;
    var action = form && formActions.get(form);
    if (!action && UUID.test(options.action_ref) && ACTIONS.indexOf(options.action_code) >= 0) {
      action = {action_ref: options.action_ref, action_code: options.action_code};
    }
    if (!action && form && actionCode(form)) {
      action = activate(actionCode(form)); formActions.set(form, action);
    }
    if (!action && ACTIONS.indexOf(options.action_code) >= 0) action = activate(options.action_code);
    if (!action) return null;
    var reserved = form && formReservations.get(form);
    var attempt = reserved && !reserved.used && reserved.action_ref === action.action_ref ? reserved.attempt_ref : uuid();
    if (reserved) reserved.used = true;
    pendingRequests.set(attempt, {action_ref: action.action_ref, action_code: action.action_code, at: Date.now()});
    record("request_start", {action_ref: action.action_ref, attempt_ref: attempt, action_code: action.action_code, detail_code: "started"});
    if (form && (form.getAttribute("method") || "get").toLowerCase() === "post") {
      hiddenReference(form, "research_v2_action_ref", action.action_ref);
      hiddenReference(form, "research_v2_attempt_ref", attempt);
      hiddenReference(form, "research_v2_context_ref", contextRef);
      hiddenReference(form, "research_v2_scope_token", config.scope_token);
      hiddenReference(form, "research_v2_context_code", context);
    }
    return attempt;
  }
  function finishRequest(attempt, detail) {
    var request = pendingRequests.get(attempt);
    if (!request || ["finished", "timeout", "network_unknown", "unknown"].indexOf(detail) < 0) return;
    record("request_finish", {action_ref: request.action_ref, attempt_ref: attempt, action_code: request.action_code, detail_code: detail});
    pendingRequests.delete(attempt);
  }
  function onClick(event) {
    if (!event.target || insideResearch(event.target)) return;
    var target = event.target.closest("a, button, input[type='submit'], input[type='button'], [data-research-v2-action]");
    if (!target || target.disabled) return;
    var action = actionCode(target);
    if (!action) return;
    var logical = activate(action);
    var form = target.form;
    if (form) formActions.set(form, logical);
  }
  function onSubmit(event) {
    var form = event.target;
    if (!form || insideResearch(form)) return;
    var action = actionCode(form);
    if (!action) return;
    var logical = formActions.get(form);
    if (!logical || Date.now() - logical.at > 1000) { logical = activate(action); formActions.set(form, logical); }
    // Prepare join hints before a progressive owner constructs FormData. A
    // cancelled/confirmation-delayed submit is not an observed request start.
    var reservation = {action_ref: logical.action_ref, attempt_ref: uuid(), used: false};
    formReservations.set(form, reservation);
    if ((form.getAttribute("method") || "get").toLowerCase() === "post") {
      hiddenReference(form, "research_v2_action_ref", reservation.action_ref);
      hiddenReference(form, "research_v2_attempt_ref", reservation.attempt_ref);
      hiddenReference(form, "research_v2_context_ref", contextRef);
      hiddenReference(form, "research_v2_scope_token", config.scope_token);
      hiddenReference(form, "research_v2_context_code", context);
    }
    Promise.resolve().then(guard(function () {
      if (!event.defaultPrevented && !reservation.used) beginRequest({form: form});
      flush(true);
    }));
  }
  var REVISIONS = {quiz_option: 1, listening_option: 1, search_filter: 1, activity_filter: 1};
  function onChange(event) {
    var target = event.target;
    if (!target || insideResearch(target) || !REVISIONS[target.getAttribute("data-research-id")]) return;
    // Whitelist controls, never inspect values or infer which option was selected.
    if (!target.matches("select, input[type='radio'], input[type='checkbox']")) return;
    var logical = activate(target.getAttribute("data-research-id") === "quiz_option" || target.getAttribute("data-research-id") === "listening_option" ? "answer" : "search");
    if (target.form) formActions.set(target.form, logical);
    var pending = revisionTimers.get(target) || {count: 0, timer: null};
    if (pending.timer) window.clearTimeout(pending.timer);
    pending.count = Math.min(1000, pending.count + 1);
    pending.timer = window.setTimeout(guard(function () {
      record("revision_count", {action_ref: logical.action_ref, action_code: "revise", detail_code: "changed", count: pending.count});
      revisionTimers.delete(target);
    }), 250);
    revisionTimers.set(target, pending);
  }

  function mediaInterval(node, now) {
    var state = mediaStates.get(node);
    if (!state || state.since === null) return;
    var elapsed = now - state.since;
    if (elapsed > 0 && elapsed <= 45000) {
      record("media_interval", {interval_started_at_ms: state.since, client_at_ms: now, detail_code: state.kind});
    }
    state.since = document.visibilityState === "visible" && !node.paused && !node.ended ? now : null;
  }
  function bindMedia() {
    document.querySelectorAll("audio[data-research-media],video[data-research-media]").forEach(function (node) {
      if (mediaStates.has(node)) return;
      var state = {since: null, kind: "playing", hadPlayback: false, ended: false}; mediaStates.set(node, state);
      ["play", "playing", "waiting", "stalled", "pause", "ended", "error", "seeked"].forEach(function (type) {
        node.addEventListener(type, guard(function () {
          var now = Date.now(); mediaInterval(node, now);
          if (type === "play") {
            record("media_action", {action_code: state.ended ? "replay" : "play", detail_code: state.ended ? "replay" : "started"});
            state.hadPlayback = true; state.ended = false;
          } else if (type === "error") record("media_action", {detail_code: "playback_error"});
          else if (type === "seeked") record("media_action", {action_code: "seek", detail_code: "started"});
          else if (type === "pause" || type === "ended") record("media_action", {action_code: "pause", detail_code: type === "ended" ? "ended" : "paused"});
          state.kind = type === "waiting" || type === "stalled" ? "buffering" : "playing";
          state.ended = type === "ended" || state.ended;
          state.since = document.visibilityState === "visible" && !node.paused && !node.ended ? now : null;
        }));
      });
    });
  }
  function mediaTick() {
    document.querySelectorAll("audio[data-research-media],video[data-research-media]").forEach(function (node) { mediaInterval(node, Date.now()); });
  }

  function promptUrl(id, action) {
    return config.prompt_url.replace("PROMPT", encodeURIComponent(id)).replace("ACTION", action);
  }
  function sample() {
    if (stopped || !ownScope() || !deliveryReady || sampleBusy || pendingPrompt || activePrompt || document.visibilityState !== "visible" || observableMs < nextSampleMs) return;
    // One opportunity after accumulated observed time; never replay missed draws.
    nextSampleMs = observableMs + 60000; sampleBusy = true; saveSamplingProgress();
    flush(false).then(function () { return jsonPost(config.sample_url, {client_at_ms: Date.now(), critical_deferral: criticalDeferral()}); })
      .then(function (data) { if (data.prompt && data.prompt.id) pendingPrompt = data.prompt; })
      .catch(function () {})
      .then(function () { sampleBusy = false; considerPrompt(); });
  }
  function considerPrompt() {
    if (!card || stopped || !pendingPrompt || promptBusy || activePrompt || criticalDeferral()) return;
    var prompt = pendingPrompt; promptBusy = true;
    jsonPost(promptUrl(prompt.id, "grant"), {client_at_ms: Date.now(), critical_deferral: null})
      .then(function (data) {
        if (data.status === "granted" && data.prompt && !criticalDeferral()) {
          pendingPrompt = null; showPrompt(data.prompt);
        } else if (data.status !== "deferred") pendingPrompt = null;
      }).catch(function () {})
      .then(function () { promptBusy = false; });
  }
  function message(name) {
    var messages = card.querySelector("template");
    var node = messages && messages.content.querySelector("[data-v2-message='" + name + "']"); return node ? node.textContent : "";
  }
  function showPrompt(prompt) {
    var form = card.querySelector("[data-v2-feedback-form]"), submit = card.querySelector("[data-v2-submit]");
    var skip = card.querySelector("[data-v2-skip]"), status = card.querySelector("[data-v2-status]");
    var helpGroup = card.querySelector("[data-v2-help-question]");
    var pending = null, waiting = false, renderConfirmed = false;
    activePrompt = prompt; form.reset(); status.textContent = "";
    submit.textContent = message("submit"); skip.textContent = message("skip");
    helpGroup.hidden = prompt.help_question_shown !== true;
    form.querySelectorAll("input").forEach(function (node) { node.disabled = false; });
    submit.disabled = true; skip.disabled = true;
    var focusOrigin = document.activeElement;
    card.hidden = false;
    // Non-modal: retain the Student's focus and let ordinary platform work continue.
    // Two animation frames give the visible card one paint opportunity. This
    // acknowledges reported rendering, never human attention or comprehension.
    window.requestAnimationFrame(function () { window.requestAnimationFrame(function () {
      if (card.hidden || document.visibilityState !== "visible") { close(); return; }
      var renderedBody = Object.assign({client_at_ms: Date.now(), locale: config.locale === "ar" ? "ar" : "en",
        help_question_shown: !helpGroup.hidden}, clockFields());
      jsonPost(promptUrl(prompt.id, "render"), renderedBody).then(function (data) {
        if (data.rendered || data.status === "rendered" || data.recorded) {
          renderConfirmed = true; skip.disabled = false; onChangeRating();
        } else close();
      }).catch(function () { status.textContent = message("unavailable"); window.setTimeout(close, 3000); });
    }); });
    function close() {
      var hadFocus = card.contains(document.activeElement);
      form.removeEventListener("submit", onAnswer); form.removeEventListener("change", onChangeRating);
      skip.removeEventListener("click", onSkip); card.removeEventListener("keydown", onEscape);
      card.hidden = true; activePrompt = null;
      if (hadFocus && focusOrigin && focusOrigin.isConnected && typeof focusOrigin.focus === "function") focusOrigin.focus({preventScroll: true});
    }
    function onChangeRating() { submit.disabled = !renderConfirmed || waiting || (!pending && !form.querySelector("input[name='v2-rating']:checked")); }
    function freeze() { form.querySelectorAll("input").forEach(function (node) { node.disabled = true; }); }
    function send(body) {
      if (!renderConfirmed || waiting) return;
      if (!pending) pending = body;
      waiting = true; freeze(); submit.disabled = true; skip.disabled = true;
      jsonPost(promptUrl(prompt.id, "respond"), pending, true).then(function (data) {
        waiting = false;
        if (data.recorded || data.status === "already") {
          status.textContent = message("recorded"); window.setTimeout(close, pending.skipped ? 0 : 1200);
        } else if (data.status === "different") {
          status.textContent = message("kept"); window.setTimeout(close, 2500);
        } else { status.textContent = message("unavailable"); window.setTimeout(close, 2500); }
      }).catch(function () {
        waiting = false; status.textContent = message("failed");
        submit.textContent = message("retry"); skip.textContent = message("close");
        submit.disabled = false; skip.disabled = false;
      });
    }
    function onAnswer(event) {
      event.preventDefault();
      if (pending) { send(pending); return; }
      var rating = form.querySelector("input[name='v2-rating']:checked"); if (!rating) return;
      var causes = Array.prototype.map.call(form.querySelectorAll("input[name='v2-cause']:checked"), function (node) { return node.value; });
      var help = helpGroup.hidden ? null : form.querySelector("input[name='v2-help']:checked");
      send({response_ref: uuid(), rating: Number(rating.value), causes: causes,
        help_requested: help ? help.value === "yes" : null, skipped: false});
    }
    function onSkip() {
      if (waiting) return;
      if (pending) { close(); return; }
      send({response_ref: uuid(), skipped: true});
    }
    function onEscape(event) { if (event.key === "Escape") onSkip(); }
    form.addEventListener("submit", onAnswer); form.addEventListener("change", onChangeRating);
    skip.addEventListener("click", onSkip); card.addEventListener("keydown", onEscape);
  }

  function heartbeat() {
    if (stopped || !ownScope()) { stop(); return; }
    closeVisibleInterval(); mediaTick();
    pendingRequests.forEach(function (request, attempt) {
      if (Date.now() - request.at > 120000) finishRequest(attempt, "timeout");
    });
    sample(); considerPrompt(); flush(false);
  }
  try {
    try {
      window.sessionStorage.setItem(SCOPE, config.scope_token);
      var existingTab = window.sessionStorage.getItem(TAB);
      if (UUID.test(existingTab)) tabRef = existingTab;
      else window.sessionStorage.setItem(TAB, tabRef);
    } catch (_) { storageAvailable = false; }
    restoreSamplingProgress();
    writeOutbox(readOutbox().filter(ownEntry));
    declareMeasurementSupport();
    record("context_enter", {detail_code: "started"});
    document.addEventListener("click", guard(onClick), true);
    document.addEventListener("change", guard(onChange), true);
    document.addEventListener("submit", guard(onSubmit), true);
    document.addEventListener("visibilitychange", guard(function () {
      closeVisibleInterval(); mediaTick();
      if (document.visibilityState === "visible") { visibleStart = Date.now(); calibrateClock(); considerPrompt(); }
      else { visibleStart = null; flush(true); }
    }));
    window.addEventListener("pagehide", guard(function () {
      closeVisibleInterval(); mediaTick(); record("context_leave", {detail_code: "finished"}); visibleStart = null; flush(true);
    }));
    window.addEventListener("pageshow", guard(function (event) {
      if (event.persisted) { visibleStart = Date.now(); record("context_enter", {detail_code: "started"}); calibrateClock(); }
    }));
    bindMedia(); detectContext(); calibrateClock();
    if (window.MutationObserver) {
      var mutationPending = false;
      new MutationObserver(guard(function () {
        if (mutationPending) return; mutationPending = true;
        window.setTimeout(guard(function () { mutationPending = false; detectContext(); bindMedia(); considerPrompt(); }), 100);
      })).observe(document.getElementById("workspace-content") || document.body,
        {childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "open", "data-research-v2-context", "data-recorder-state"]});
    }
    window.researchV2 = Object.freeze({beginRequest: beginRequest, finishRequest: finishRequest,
      actionFor: function (form) { var action = formActions.get(form); return action ? action.action_ref : null; }});
    window.setInterval(guard(heartbeat), 30000);
    window.setInterval(guard(function () { flush(false); }), bounded(policy.flush_ms, 5000, 30000, 15000));
    window.setInterval(guard(calibrateClock), 300000);
    window.setTimeout(guard(function () { flush(false); }), 1000);
  } catch (_) { stop(); }
}());
