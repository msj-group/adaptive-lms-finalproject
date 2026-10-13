/* Switch between authorized activity summaries without requests or storage. */
(function () {
  "use strict";
  document.querySelectorAll("[data-activity-picker]").forEach(function (section) {
    var control = section.querySelector("[data-picker-control]");
    var choice = control && control.querySelector("select");
    var panels = section.querySelector("[data-picker-panels]");
    var cards = Array.from(section.querySelectorAll("[data-picker-card]"));
    if (!choice || !panels || cards.length < 2) return;

    function showCard() {
      var selected = cards.find(function (card) {
        return card.dataset.pickerCard === choice.value;
      });
      if (!selected) return;
      cards.forEach(function (card) { card.hidden = card !== selected; });
    }

    // Without this client every summary and native Result link stays readable.
    showCard();
    cards.forEach(function (card) {
      var heading = card.querySelector("[data-picker-heading]");
      if (heading) heading.classList.add("sr-only");
    });
    choice.addEventListener("change", showCard);
    window.addEventListener("pageshow", function () { requestAnimationFrame(showCard); });
    panels.setAttribute("aria-live", "polite");
    panels.setAttribute("aria-atomic", "true");
    control.hidden = false;
  });
}());
