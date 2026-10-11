/* Switch between already-authorized summaries; no requests or stored results. */
(function () {
  "use strict";
  document.querySelectorAll("[data-quiz-attempt-history]").forEach(function (history) {
    var picker = history.querySelector("[data-attempt-picker]");
    var choice = picker && picker.querySelector("select");
    var panels = history.querySelector(".st-attempt-panels");
    var cards = Array.from(history.querySelectorAll("[data-quiz-attempt]"));
    if (!choice || !panels || cards.length < 2) return;

    function showAttempt() {
      var selected = cards.find(function (card) {
        return card.dataset.quizAttempt === choice.value;
      });
      if (!selected) return;
      cards.forEach(function (card) { card.hidden = card !== selected; });
    }

    // Without this client every summary and its native Result link stays readable.
    showAttempt();
    cards.forEach(function (card) {
      card.querySelector("[data-attempt-heading]").classList.add("sr-only");
    });
    choice.addEventListener("change", showAttempt);
    // History traversal can restore the native select after deferred scripts run.
    window.addEventListener("pageshow", function () { requestAnimationFrame(showAttempt); });
    panels.setAttribute("aria-live", "polite");
    panels.setAttribute("aria-atomic", "true");
    picker.hidden = false;
  });
}());
