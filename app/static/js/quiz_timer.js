/* Quiz attempt countdown -- display only.
 * Phase 4 / M04D. No dependency, no build step.
 *
 * WHAT THIS DOES
 *   - renders the remaining time until the server's authoritative
 *     `deadline_at`, which the page carries as an ISO-8601 UTC string;
 *   - at zero, shows that the time is up and visually disables the local
 *     answer and submit controls so a Student is not invited to type into
 *     a form that will be refused.
 *
 * WHAT THIS DELIBERATELY NEVER DOES
 *   - it never decides expiry. The server compares its own clock against
 *     the stored deadline under the required locks; this script cannot
 *     make an attempt live longer or end sooner, and a Student whose
 *     browser clock is wrong, whose JavaScript is disabled, or who edits
 *     this file gains nothing at all.
 *   - it never submits anything. Auto-submitting from the browser would
 *     make the outcome depend on whether a tab was still open; the server
 *     grades an overdue attempt on whatever was saved, the next time any
 *     authorized request touches it.
 */
(function () {
  "use strict";
  var t = window.aelmsUI ? window.aelmsUI.t : function (text) { return text; };

  var root = document.querySelector("[data-quiz-timer]");
  if (!root) {
    return;
  }
  var deadline = Date.parse(root.getAttribute("data-deadline"));
  if (isNaN(deadline)) {
    return;
  }
  var output = root.querySelector("[data-timer-remaining]");
  if (!output) {
    return;
  }

  function pad(value) {
    return value < 10 ? "0" + value : String(value);
  }

  function disableControls() {
    var form = document.querySelector("[data-answer-form]");
    if (form) {
      Array.prototype.slice
        .call(form.querySelectorAll("input, button"))
        .forEach(function (control) {
          if (control.type !== "hidden") {
            control.disabled = true;
          }
        });
    }
    root.classList.add("quiz-timer--expired");
  }

  var timer = null;

  function tick() {
    var remaining = Math.floor((deadline - Date.now()) / 1000);
    if (remaining <= 0) {
      output.textContent = t("Time is up");
      /* The server has the last word: the next request finalizes and
         grades this attempt on whatever was already saved. */
      disableControls();
      if (!root.querySelector("[data-expiry-help]")) {
        var help = document.createElement("p"); help.dataset.expiryHelp = "true"; help.className = "alert alert--warning"; help.setAttribute("role", "status");
        help.textContent = t("Only answers already saved will be used. The server confirms the final attempt status on your next request. ");
        var link = document.createElement("a"); link.href = location.href; link.textContent = t("View saved attempt status"); help.appendChild(link); root.appendChild(help);
      }
      if (timer) {
        window.clearInterval(timer);
      }
      return;
    }
    var hours = Math.floor(remaining / 3600);
    var minutes = Math.floor((remaining % 3600) / 60);
    var seconds = remaining % 60;
    output.textContent =
      (hours > 0 ? hours + ":" + pad(minutes) : String(minutes)) + ":" + pad(seconds);
  }

  tick();
  timer = window.setInterval(tick, 1000);
})();
