/* Setup Hub phone deck. Selection uses the existing four native GET destinations. */
(function () {
  "use strict";
  var hub = document.querySelector(".ax-academics-page [data-academic-hub]");
  if (!hub || !window.ResizeObserver || !window.matchMedia) return;
  var rail = hub.querySelector("[data-academic-track]");
  var cards = Array.from(rail.querySelectorAll("[data-academic-area]"));
  var selected = rail.querySelector("[aria-current='page']");
  if (!selected || cards.length !== 4) return;
  var media = window.matchMedia("(max-width:767px)");
  var position = hub.querySelector("[data-academic-position]");
  var frame = 0, settleTimer = 0, width = -1, phone = false;
  var gesture = false, pointerDown = false, moved = false, startScroll = 0, leaving = false, settleAttempts = 0;

  function nearest() {
    var bounds = rail.getBoundingClientRect(), middle = bounds.left + rail.clientWidth / 2;
    return cards.reduce(function (best, card) {
      var box = card.getBoundingClientRect(), distance = Math.abs(box.left + box.width / 2 - middle);
      return !best || distance < best.distance ? {card:card, distance:distance} : best;
    }, null);
  }
  function front() {
    frame = 0;
    if (!phone) return;
    var item = nearest();
    cards.forEach(function (card) { card.classList.toggle("is-front", card === item.card); });
    if (position) position.textContent = String(cards.indexOf(item.card) + 1) + " / " + String(cards.length);
  }
  function schedule() { if (!frame) frame = requestAnimationFrame(front); }
  function centre(card) {
    var bounds = rail.getBoundingClientRect(), box = card.getBoundingClientRect();
    var delta = box.left + box.width / 2 - bounds.left - rail.clientWidth / 2;
    if (Math.abs(delta) > 1) rail.scrollTo({left:rail.scrollLeft + delta, behavior:"instant"});
  }
  function cancelGesture() {
    gesture = false; moved = false; pointerDown = false; settleAttempts = 0;
    clearTimeout(settleTimer);
  }
  function settle() {
    clearTimeout(settleTimer);
    if (!phone || pointerDown || !gesture || leaving) return;
    if (!moved) { cancelGesture(); return; }
    var item = nearest();
    if (!item || item.distance > 3) {
      if (++settleAttempts > 6) { cancelGesture(); return; }
      settleTimer = setTimeout(settle, 160);
      return;
    }
    cancelGesture();
    if (item.card === selected) return;
    var target = new URL(item.card.href, location.href);
    if (target.origin !== location.origin) return;
    leaving = true;
    location.assign(target.href);
  }
  function layout() {
    var nextPhone = media.matches, nextWidth = rail.clientWidth;
    if (nextPhone === phone && nextWidth === width) return;
    cancelGesture(); phone = nextPhone; width = nextWidth;
    hub.dataset.enhanced = String(phone);
    requestAnimationFrame(function () {
      if (phone) { centre(selected); front(); }
      else cards.forEach(function (card) { card.classList.remove("is-front"); });
    });
  }
  var observer = new ResizeObserver(layout);
  observer.observe(rail);
  rail.addEventListener("scroll", function () {
    schedule();
    if (gesture && Math.abs(rail.scrollLeft - startScroll) > 8) moved = true;
    clearTimeout(settleTimer);
    if (gesture) settleTimer = setTimeout(settle, 180);
  }, {passive:true});
  rail.addEventListener("scrollend", settle, {passive:true});
  rail.addEventListener("pointerdown", function () {
    if (!phone || leaving) return;
    cancelGesture(); gesture = true; pointerDown = true; startScroll = rail.scrollLeft;
  }, {passive:true});
  function release() {
    pointerDown = false;
    if (gesture) { clearTimeout(settleTimer); settleTimer = setTimeout(settle, 180); }
  }
  rail.addEventListener("pointerup", release, {passive:true});
  rail.addEventListener("pointercancel", release, {passive:true});
  rail.addEventListener("wheel", function (event) {
    if (!phone || leaving || (!event.deltaX && !event.shiftKey)) return;
    if (!gesture) startScroll = rail.scrollLeft;
    gesture = true;
  }, {passive:true});
  rail.addEventListener("focusin", function (event) {
    var card = event.target.closest("[data-academic-area]");
    if (phone && card && !pointerDown) { cancelGesture(); centre(card); schedule(); }
  });
  document.addEventListener("visibilitychange", function () { if (document.hidden) cancelGesture(); });
  window.addEventListener("pagehide", function () {
    cancelGesture(); observer.disconnect();
    if (frame) cancelAnimationFrame(frame);
  });
  window.addEventListener("pageshow", function () {
    leaving = false; width = -1; observer.observe(rail); layout();
  });
  layout();
})();
