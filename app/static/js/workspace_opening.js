/* No credential handling or data requests: follow the server-approved link. */
(function () {
  "use strict";
  var link = document.querySelector("[data-opening-continue]");
  var cancel = document.querySelector("[data-opening-cancel]");
  if (!link) return;
  var leaving = false;
  function stop() { leaving = true; }
  function openWorkspace() {
    if (leaving) return;
    var target = new URL(link.href, location.href);
    if (target.origin !== location.origin) return;
    location.replace(target.href);
  }
  link.addEventListener("click", stop);
  if (cancel) cancel.addEventListener("submit", stop);
  window.addEventListener("pagehide", stop);
  // The deferred script already has the authorized link. No artificial wait
  // for decorative images, timers or a simulated loading indicator.
  openWorkspace();
})();
