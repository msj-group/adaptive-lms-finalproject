/* Native audio remains usable without this presentation-only speed selector.
   Authored rates shape the UI; they are not DRM or an authorization boundary. */
(function () {
  "use strict";
  document.querySelectorAll("[data-audio-player]").forEach(function (root) {
    var audio = root.querySelector("[data-audio-element]");
    if (!audio) return;
    var panel = root.querySelector("[data-audio-controls]");
    var select = root.querySelector("[data-audio-speed-select]");
    var rates = select ? Array.from(select.options, function (option) { return Number(option.value); }) : [1];
    function syncRate() {
      if (!rates.includes(audio.playbackRate)) audio.playbackRate = 1;
      if (select) select.value = String(audio.playbackRate);
    }
    if (select && rates.length > 1) {
      select.addEventListener("change", function () {
        var rate = Number(select.value);
        if (rates.includes(rate)) audio.playbackRate = rate;
        syncRate();
      });
      panel.hidden = false;
    }
    audio.addEventListener("ratechange", syncRate);
    syncRate();
  });
}());
