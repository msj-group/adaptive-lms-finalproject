/* Ordinary Quiz: serialized answer saves, own-attempt review flags and navigation.
 * No browser storage of answers. Never abort an in-flight write on a new choice.
 * CSRF, signed state, selection ownership and expiry are enforced by the server.
 */
(function () {
  "use strict";
  var root = document.querySelector("[data-quiz-workspace]");
  if (!root || !window.fetch || !window.FormData) return;
  var t = window.aelmsUI.t;
  var form = root.querySelector("[data-quiz-autosave]");
  var output = form.querySelector("[data-quiz-save-status]");
  var feedback = form.querySelector("[data-quiz-save-feedback]");
  var retry = form.querySelector("[data-quiz-save-retry]");
  var reload = root.querySelector("[data-quiz-reload]");
  var links = Array.from(root.querySelectorAll("[data-quiz-question]"));
  var currentLink = links.find(function (link) { return link.dataset.quizQuestion === root.dataset.questionId; });
  var bookmarkForm = root.querySelector("[data-quiz-bookmark-form]");
  var bookmarkButton = bookmarkForm.querySelector("button");
  var bookmarkStatus = root.querySelector("[data-bookmark-status]");
  var worker = null;
  var bookmarkWorker = null;
  var bookmarkFailed = false;
  var uncertain = false;
  var terminal = false;
  var leaving = false;
  var approved = new WeakSet();

  function selection() {
    return Array.from(form.querySelectorAll("input[name='option']:checked"), function (input) { return input.value; });
  }
  function signature(options) { return JSON.stringify(options); }
  var saved = signature(selection());
  var grid = currentLink.closest(".quiz-question-grid");
  var sidebar = root.querySelector(".quiz-question-sidebar");
  // Only one-use visual coordinates and authorized public URL identity. Never
  // store prompts, options, answers, bookmarks, scores, credentials or tokens.
  var scrollKey = "aelms.quiz-scroll.v1";
  var questionPaths = links.map(function (link) { return new URL(link.href, location.href).pathname; });
  var attemptPath = location.pathname.slice(0, location.pathname.lastIndexOf("/questions/"));
  function questionTarget(url) { return url.origin === location.origin && questionPaths.includes(url.pathname); }
  function rememberPosition(url) {
    if (!questionTarget(url)) return;
    try {
      sessionStorage.setItem(scrollKey, JSON.stringify({
        scope:attemptPath, target:url.pathname, at:Date.now(),
        x:window.scrollX, y:window.scrollY, grid:grid.scrollTop, sidebar:sidebar.scrollTop
      }));
    } catch (_) { /* Storage restrictions never prevent protected navigation. */ }
  }
  function navigate(value) {
    var url = new URL(value, location.href);
    rememberPosition(url);
    location.assign(url.href);
  }
  var position = null;
  try {
    position = JSON.parse(sessionStorage.getItem(scrollKey) || "null");
    sessionStorage.removeItem(scrollKey);
  } catch (_) { position = null; }
  if (position && (position.scope !== attemptPath || position.target !== location.pathname ||
      !Number.isFinite(position.at) || Date.now() - position.at < 0 || Date.now() - position.at > 300000 ||
      !Number.isFinite(position.x) || Math.abs(position.x) > 10000000 ||
      ![position.y,position.grid,position.sidebar].every(function (value) { return Number.isFinite(value) && value >= 0 && value <= 10000000; }))) position = null;
  if (position) {
    var restoreCanceled = false;
    function restorePosition() {
      if (restoreCanceled) return;
      grid.scrollTop = position.grid;
      sidebar.scrollTop = position.sidebar;
      var html = document.documentElement;
      var behavior = html.style.scrollBehavior;
      html.style.scrollBehavior = "auto";
      window.scrollTo(position.x, position.y);
      html.style.scrollBehavior = behavior;
    }
    ["pointerdown","touchstart","wheel","keydown"].forEach(function (type) {
      window.addEventListener(type, function () { restoreCanceled = true; }, {once:true,passive:true});
    });
    requestAnimationFrame(restorePosition);
    window.addEventListener("load", function () { requestAnimationFrame(restorePosition); }, {once:true});
    if (document.fonts) document.fonts.ready.then(function () { requestAnimationFrame(restorePosition); });
  } else {
    var offset = currentLink.getBoundingClientRect().top - grid.getBoundingClientRect().top;
    if (offset + currentLink.offsetHeight > grid.clientHeight) grid.scrollTop += offset + currentLink.offsetHeight - grid.clientHeight;
  }
  function needsSave() { return uncertain || signature(selection()) !== saved; }
  function acknowledge() { form.dispatchEvent(new Event("workspace:saved")); }
  function showStatus(message, failed) {
    output.textContent = failed ? t(message) : "";
    output.hidden = !failed;
    feedback.hidden = !failed;
    output.dataset.state = failed ? "error" : "saved";
    retry.hidden = !failed;
    reload.hidden = !failed;
  }
  function progress(answered) {
    currentLink.dataset.answered = answered ? "true" : "false";
    currentLink.querySelector(".quiz-question-state").textContent = t(answered ? "Answered" : "Not answered");
    var count = links.filter(function (link) { return link.dataset.answered === "true"; }).length;
    var unanswered = links.length - count;
    var meter = root.querySelector("[data-quiz-progress]");
    var previousCount = meter.value;
    meter.value = count;
    root.querySelector("[data-quiz-progress-label]").textContent = t("%(answered)s of %(total)s answered", {answered:count,total:links.length});
    root.querySelector("[data-quiz-unanswered-note]").hidden = !unanswered;
    root.querySelector("[data-quiz-complete-note]").hidden = Boolean(unanswered);
    root.querySelector("[data-quiz-unanswered-confirm]").hidden = !unanswered;
    var checkbox = root.querySelector("[name='confirm_unanswered']");
    checkbox.disabled = !unanswered;
    // A changed count requires a fresh acknowledgement, never a remembered one.
    if (previousCount !== count) checkbox.checked = false;
    root.querySelector("[data-quiz-confirm-text]").textContent = t(unanswered === 1 ? "Submit with %(count)d unanswered question" : "Submit with %(count)d unanswered questions", {count:unanswered});
  }
  function safeTarget(value) {
    var url = new URL(value, location.href);
    if (url.origin !== location.origin) throw new Error("Invalid destination");
    return url.href;
  }
  async function post(url, body) {
    var response;
    var data;
    try {
      response = await fetch(url, {method:"POST",body:body,credentials:"same-origin",redirect:"error",cache:"no-store",headers:{"Accept":"application/json","X-Quiz-Autosave":"1"}});
      if ((response.headers.get("Content-Type") || "").includes("application/json")) data = await response.json();
    } catch (_) { throw new Error(t("Could not save. Check your connection or session, then retry before leaving.")); }
    if (!(response.headers.get("Content-Type") || "").includes("application/json")) throw new Error(t("Could not save. Check your connection or session, then retry before leaving."));
    if (data.redirect_url) {
      var target = safeTarget(data.redirect_url);
      terminal = true; uncertain = false; bookmarkFailed = false;
      acknowledge(); leaving = true;
      navigate(target);
      throw new Error(t(data.error || "This attempt has ended."));
    }
    if (!response.ok || data.error || data.question_public_id !== root.dataset.questionId) throw new Error(t(data.error || "Could not save. Check your connection or session, then retry before leaving."));
    return data;
  }
  function ensureSaved() {
    if (terminal) return Promise.resolve(false);
    if (worker) return worker;
    if (!needsSave()) return Promise.resolve(true);
    worker = (async function () {
      try {
        while (needsSave()) {
          var chosen = selection();
          var snapshot = signature(chosen);
          var body = new FormData();
          body.set("csrf_token", form.querySelector("[name='csrf_token']").value);
          body.set("answer_state", form.querySelector("[name='answer_state']").value);
          chosen.forEach(function (value) { body.append("option", value); });
          showStatus("", false);
          form.setAttribute("aria-busy", "true");
          var data = await post(form.action, body);
          if (typeof data.answered !== "boolean" || data.answered !== Boolean(chosen.length)) throw new Error(t("Could not save. Check your connection or session, then retry before leaving."));
          saved = snapshot; uncertain = false;
          progress(data.answered);
        }
        acknowledge();
        showStatus("", false);
        return true;
      } catch (error) {
        if (!terminal) {
          // A missing response may follow a successful commit. Re-send the full
          // latest set on retry, even if it equals the initial page selection.
          uncertain = true;
          showStatus(error.message || "Could not save. Check your connection or session, then retry before leaving.", true);
        }
        return false;
      } finally { form.removeAttribute("aria-busy"); worker = null; }
    })();
    return worker;
  }
  form.addEventListener("change", function (event) {
    if (event.target.matches("input[name='option']")) ensureSaved();
  });
  retry.addEventListener("click", ensureSaved);

  function toggleBookmark() {
    if (bookmarkWorker || terminal) return bookmarkWorker || Promise.resolve(false);
    var marked = bookmarkButton.getAttribute("aria-pressed") !== "true";
    var body = new FormData(bookmarkForm);
    body.set("bookmark", marked ? "yes" : "no");
    bookmarkButton.disabled = true;
    bookmarkStatus.hidden = true;
    bookmarkStatus.textContent = "";
    bookmarkButton.setAttribute("aria-busy", "true");
    bookmarkWorker = (async function () {
      try {
        var data = await post(bookmarkForm.action, body);
        if (data.bookmarked !== marked) throw new Error(t("Could not update bookmark. Try again."));
        bookmarkButton.setAttribute("aria-pressed", marked ? "true" : "false");
        bookmarkButton.value = marked ? "no" : "yes";
        bookmarkButton.setAttribute("aria-label", t(marked ? "Remove bookmark" : "Bookmark"));
        bookmarkButton.title = t(marked ? "Remove bookmark" : "Bookmark");
        currentLink.querySelector("[data-question-bookmark]").hidden = !marked;
        bookmarkFailed = false;
        return true;
      } catch (_) {
        if (!terminal) {
          bookmarkFailed = true;
          bookmarkStatus.hidden = false;
          bookmarkStatus.textContent = t("Could not update bookmark. Try again.");
        }
        return false;
      } finally { bookmarkButton.disabled = terminal; bookmarkButton.removeAttribute("aria-busy"); bookmarkWorker = null; }
    })();
    return bookmarkWorker;
  }
  async function readyToLeave() {
    while (!terminal) {
      if (!await ensureSaved()) return false;
      if (bookmarkWorker && !await bookmarkWorker) return false;
      // Choices can change while the bookmark response is arriving. Recheck
      // both workers and the latest selection before releasing navigation.
      if (!worker && !bookmarkWorker && !needsSave()) return !bookmarkFailed;
    }
    return false;
  }
  // Run before shared dirty-form guards. Wait for the latest selection before
  // any same-tab navigation or POST (including final submission/logout).
  document.addEventListener("click", function (event) {
    var link = event.target.closest("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target === "_blank" || link.hasAttribute("download")) return;
    if (link === reload) {
      event.preventDefault(); event.stopImmediatePropagation();
      if (worker || bookmarkWorker) return;
      window.aelmsConfirm(t("You have unsaved work. Leave this page and discard it?")).then(function (accepted) {
        if (accepted) { terminal = true; leaving = true; acknowledge(); navigate(safeTarget(link.href)); }
      });
      return;
    }
    if (link.closest("[data-expiry-help]")) { terminal = true; leaving = true; acknowledge(); return; }
    var url = new URL(link.href, location.href);
    if (url.protocol !== "http:" && url.protocol !== "https:" || url.pathname === location.pathname && url.search === location.search && url.hash) return;
    if (!worker && !bookmarkWorker && !needsSave() && !bookmarkFailed) { rememberPosition(url); return; }
    event.preventDefault(); event.stopImmediatePropagation();
    if (leaving) return;
    leaving = true;
    readyToLeave().then(function (ready) { if (ready) navigate(url.href); else if (!terminal) leaving = false; });
  }, true);
  document.addEventListener("submit", function (event) {
    var target = event.target;
    if (approved.has(target)) { approved.delete(target); return; }
    if (target === bookmarkForm) {
      event.preventDefault(); event.stopImmediatePropagation(); toggleBookmark(); return;
    }
    if (target !== form && !worker && !bookmarkWorker && !needsSave() && !bookmarkFailed) return;
    if ((target.getAttribute("method") || "get").toLowerCase() === "dialog") return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (leaving) return;
    leaving = true;
    var submitter = event.submitter;
    readyToLeave().then(function (ready) {
      if (!ready) { if (!terminal) leaving = false; return; }
      leaving = false;
      if (target === form) {
        var next = root.querySelector("[data-research-id='quiz_next']");
        if (next) { leaving = true; navigate(safeTarget(next.href)); }
      } else {
        approved.add(target);
        // The collector saw the original native submission intention. This
        // resumption follows confirmed saves and is not a second attempt.
        target.setAttribute("data-research-resuming-submit", "");
        try { target.requestSubmit(submitter || undefined); }
        finally { target.removeAttribute("data-research-resuming-submit"); }
      }
    });
  }, true);
  window.addEventListener("beforeunload", function (event) {
    if (!terminal && (worker || bookmarkWorker || needsSave() || bookmarkFailed)) { event.preventDefault(); event.returnValue = ""; }
  });
  // Returning from bfcache must reauthorize the attempt and reload saved state.
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) { rememberPosition(new URL(location.href)); location.reload(); }
  });
})();
