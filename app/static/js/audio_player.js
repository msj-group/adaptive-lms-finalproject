/* Listening audio controls -- presentation only.
 * Phase 4 / M05. No dependency, no build step.
 *
 * WHAT THIS DOES
 *   - toggles play/pause through the native <audio> element;
 *   - replays from the beginning;
 *   - switches the playback rate between 0.75x, 1x, 1.25x and 1.5x, and
 *     shows which one is current (both as visible text and, for assistive
 *     technology, as aria-pressed on the speed buttons);
 *   - keeps the play/pause label truthful when playback starts, pauses or
 *     ends for any reason, including the native controls the element
 *     already renders.
 *
 * WHAT THIS DELIBERATELY NEVER DOES
 *   - it decides no authorization, no attempt state, no deadline, no
 *     completion and no grade. Those are server decisions taken under
 *     locks; nothing here can influence any of them.
 *   - it enforces no playback limit, no forced sequential listening and
 *     no DRM, generates no waveform, transcodes nothing, transcribes
 *     nothing, recognises no speech and records nothing itself. (Phase 6:
 *     while natural-use collection runs for a Student, the separate
 *     usage_collector.js observes play/pause/seek events of the element
 *     -- never the audio -- under the documented event dictionary.)
 *   - it never fetches anything. The <audio> element's own src is an
 *     authorized server route that re-checks the whole nested chain on
 *     every request, including every Range request the browser makes
 *     while seeking.
 *
 * WITHOUT JAVASCRIPT the page is still usable: the <audio> element
 * carries the native `controls` attribute, so play, pause and seeking all
 * work, and this script's extra buttons stay hidden because the markup
 * ships them hidden and only this file reveals them. Nothing a Student
 * needs in order to take the attempt depends on this file.
 */
(function () {
  "use strict";

  var SPEEDS = [0.75, 1, 1.25, 1.5];

  function label(rate) {
    /* 1 renders as "1x", 0.75 as "0.75x" -- no trailing zeros. */
    return String(rate) + "×";
  }

  function setup(root) {
    var audio = root.querySelector("[data-audio-element]");
    var panel = root.querySelector("[data-audio-controls]");
    if (!audio || !panel) {
      return;
    }

    var toggle = panel.querySelector("[data-audio-toggle]");
    var replay = panel.querySelector("[data-audio-replay]");
    var speedButtons = Array.prototype.slice.call(
      panel.querySelectorAll("[data-audio-speed]")
    );
    var current = panel.querySelector("[data-audio-current-speed]");

    /* The controls are inert markup until this point: revealing them here
       is what keeps a JavaScript-disabled page from showing buttons that
       would do nothing. */
    panel.hidden = false;

    function renderToggle() {
      if (!toggle) {
        return;
      }
      var playing = !audio.paused && !audio.ended;
      toggle.textContent = playing ? "Pause" : "Play";
      toggle.setAttribute("aria-pressed", playing ? "true" : "false");
    }

    function renderSpeed() {
      var rate = audio.playbackRate;
      if (current) {
        current.textContent = label(rate);
      }
      speedButtons.forEach(function (button) {
        var value = parseFloat(button.getAttribute("data-audio-speed"));
        var active = Math.abs(value - rate) < 0.001;
        button.setAttribute("aria-pressed", active ? "true" : "false");
        button.classList.toggle("btn--primary", active);
        button.classList.toggle("btn--secondary", !active);
      });
    }

    if (toggle) {
      toggle.addEventListener("click", function () {
        if (audio.paused || audio.ended) {
          /* play() rejects in some browsers (autoplay policy, missing
             media). Swallow it: the native controls remain available and
             the label is re-rendered by the real events below. */
          var started = audio.play();
          if (started && typeof started.catch === "function") {
            started.catch(function () {});
          }
        } else {
          audio.pause();
        }
      });
    }

    if (replay) {
      replay.addEventListener("click", function () {
        try {
          audio.currentTime = 0;
        } catch (error) {
          /* Seeking before metadata has loaded can throw; the play()
             below still starts from the beginning in that case. */
        }
        var started = audio.play();
        if (started && typeof started.catch === "function") {
          started.catch(function () {});
        }
      });
    }

    speedButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        var value = parseFloat(button.getAttribute("data-audio-speed"));
        if (SPEEDS.indexOf(value) === -1) {
          return;
        }
        audio.playbackRate = value;
        renderSpeed();
      });
    });

    /* Listen to the element, not only to our own buttons, so the labels
       stay truthful when the native controls are used instead. */
    ["play", "pause", "ended"].forEach(function (name) {
      audio.addEventListener(name, renderToggle);
    });
    audio.addEventListener("ratechange", renderSpeed);

    renderToggle();
    renderSpeed();
  }

  Array.prototype.slice
    .call(document.querySelectorAll("[data-audio-player]"))
    .forEach(setup);
})();
