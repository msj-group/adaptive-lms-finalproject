/* Presentation only: centre the phone Group deck. Native GET links own selection. */
(function () {
  "use strict";
  var rail = document.querySelector(".teacher-announcements .tan-group-list");
  if (!rail || !window.ResizeObserver) return;
  var cards = Array.from(rail.querySelectorAll(".tan-group"));
  if (!cards.length) return;
  var selected = rail.querySelector("[aria-current]") || cards[0];
  var frame = 0, deck = false;
  function phoneDeck() {
    return getComputedStyle(rail).getPropertyValue("--tan-deck").trim() === "1";
  }
  function centre(card) {
    var bounds = rail.getBoundingClientRect(), item = card.getBoundingClientRect();
    var delta = item.left + item.width / 2 - bounds.left - rail.clientWidth / 2;
    // Fractional rounding must not initiate a directional mandatory-snap step.
    if (Math.abs(delta) > 1) rail.scrollTo({left:rail.scrollLeft + delta,behavior:"instant"});
  }
  function front() {
    frame = 0;
    if (!deck) return;
    var bounds = rail.getBoundingClientRect(), centreX = bounds.left + rail.clientWidth / 2;
    var nearest = cards.reduce(function (best, card) {
      var item = card.getBoundingClientRect(), distance = Math.abs(item.left + item.width / 2 - centreX);
      return !best || distance < best.distance ? {card:card,distance:distance} : best;
    }, null);
    cards.forEach(function (card) { card.classList.toggle("is-front", card === nearest.card); });
  }
  function schedule() { if (!frame) frame = requestAnimationFrame(front); }
  var width = -1;
  var observer = new ResizeObserver(function () {
    var next = phoneDeck(), nextWidth = rail.clientWidth;
    if (next === deck && nextWidth === width) return;
    deck = next; width = nextWidth;
    rail.classList.toggle("tan-deck-enhanced", deck);
    // Let the browser finish reordering/snap layout before initial centring.
    if (deck) requestAnimationFrame(function () {
      if (phoneDeck()) { centre(selected); front(); }
    });
    else cards.forEach(function (card) { card.classList.remove("is-front"); });
    schedule();
  });
  observer.observe(rail);
  rail.addEventListener("scroll", schedule, {passive:true});
  rail.addEventListener("focusin", function (event) {
    var card = event.target.closest(".tan-group");
    if (deck && card) { centre(card); schedule(); }
  });
  window.addEventListener("pagehide", function () {
    observer.disconnect();
    if (frame) cancelAnimationFrame(frame);
  });
  window.addEventListener("pageshow", function () {
    observer.observe(rail);
    requestAnimationFrame(function () {
      if (phoneDeck()) { centre(selected); front(); }
    });
  });
})();
