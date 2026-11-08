/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
/* Native audio remains usable without this presentation-only speed selector.
   Authored rates shape the UI; they are not DRM or an authorization boundary. */
(function () {
  "use strict";
  document.querySelectorAll("[data-audio-player]").forEach(function (root) {
    var audio = root.querySelector("[data-audio-element]");
    if (!audio) return;
    var panel = root.querySelector("[data-audio-controls]");
    var select = root.querySelector("[data-audio-speed-select],[data-media-speed]");
    var rates = select ? Array.from(select.options, function (option) { return Number(option.value); }) : [1];
    function syncRate() {
      if (!rates.includes(audio.playbackRate)) audio.playbackRate = 1;
      if (select) select.value = String(audio.playbackRate);
    }
    if (select && rates.length > 1) {
      pageScope.listen(select, "change", function () {
        var rate = Number(select.value);
        if (rates.includes(rate)) audio.playbackRate = rate;
        syncRate();
      });
      if (panel) panel.hidden = false;
    }
    pageScope.listen(audio, "ratechange", syncRate);
    syncRate();
  });
}());

});
