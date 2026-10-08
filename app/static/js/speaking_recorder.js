/* Speaking recorder -- Phase 4 / M06.
 *
 * A small, dependency-free controller for the Student recording page. It
 * enhances a form that already works without it: the server renders an
 * ordinary `accept="audio/*"` file input and a real submit button, and
 * this module hides that input and drives MediaRecorder instead **only**
 * when the browser can actually do it.
 *
 * What this module deliberately does NOT do:
 *
 *   - it never requests microphone permission on load. The permission
 *     prompt appears only after the Student presses "Start recording",
 *     and the page explains what will happen before they do;
 *   - it never contacts the server on its own. Re-recording discards the
 *     previous Blob locally; nothing leaves the browser until the
 *     Student presses the one final submit button, which performs an
 *     ordinary multipart form submission carrying the CSRF token and the
 *     signed submission token the server put in the form;
 *   - it never persists a recording. There is no localStorage, no
 *     sessionStorage, no IndexedDB, no cookie, no logging and no
 *     analytics call anywhere in this file. A discarded take exists only
 *     as a Blob that is dropped and whose object URL is revoked;
 *   - it never autoplays. The preview element has `controls` and is
 *     played only when the Student presses play;
 *   - it decides nothing about authorization, the deadline, or whether a
 *     submission is allowed. All of that is decided on the server, under
 *     locks, and re-checked after them.
 *
 * Every MediaStream track is stopped when a recording ends, when an error
 * occurs, and when the page is left, so the browser's recording indicator
 * never stays on longer than the recording itself.
 */
(function () {
  "use strict";
  var t = window.aelmsUI ? window.aelmsUI.t : function (text) { return text; };

  var STATES = {
    UNSUPPORTED: "unsupported",
    READY: "ready",
    REQUESTING: "requesting",
    RECORDING: "recording",
    PREVIEW: "preview",
    UPLOADING: "uploading",
    ERROR: "error"
  };

  /* Preferred container/codec pairs, most preferred first. The browser,
   * not this list, has the final say: each candidate is offered to
   * MediaRecorder.isTypeSupported and the first accepted one is used.
   * Chromium and Firefox take WebM/Opus; Safari takes MP4/AAC. If none
   * is accepted the recorder is created with no explicit type and its own
   * negotiated `mimeType` is read back afterwards. */
  var PREFERRED_TYPES = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4;codecs=mp4a.40.2",
    "audio/mp4",
    "audio/mpeg",
    "audio/wav"
  ];

  /* Base MIME type -> the extension the server's Speaking allowlist
   * accepts. The server re-derives everything from the bytes; this only
   * decides the safe generated filename. */
  var EXTENSION_BY_TYPE = {
    "audio/webm": "webm",
    "audio/mp4": "mp4",
    "audio/x-m4a": "mp4",
    "audio/m4a": "mp4",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav"
  };

  var BASE_FILENAME = "speaking-recording";

  function baseType(mimeType) {
    return String(mimeType || "").split(";")[0].trim().toLowerCase();
  }

  function extensionFor(mimeType) {
    return EXTENSION_BY_TYPE[baseType(mimeType)] || null;
  }

  function pickType() {
    if (typeof window.MediaRecorder !== "function") {
      return null;
    }
    if (typeof window.MediaRecorder.isTypeSupported !== "function") {
      // A MediaRecorder without the capability query: let it negotiate on
      // its own and read `recorder.mimeType` back after it starts.
      return "";
    }
    for (var i = 0; i < PREFERRED_TYPES.length; i += 1) {
      if (window.MediaRecorder.isTypeSupported(PREFERRED_TYPES[i])) {
        return PREFERRED_TYPES[i];
      }
    }
    return "";
  }

  function recordingIsSupported() {
    return !!(
      window.navigator &&
      window.navigator.mediaDevices &&
      typeof window.navigator.mediaDevices.getUserMedia === "function" &&
      typeof window.MediaRecorder === "function" &&
      typeof window.Blob === "function" &&
      typeof window.URL !== "undefined" &&
      typeof window.URL.createObjectURL === "function" &&
      typeof window.DataTransfer === "function" &&
      typeof window.File === "function"
    );
  }

  function setup(root) {
    var form = document.querySelector("[data-speaking-form]");
    if (!form) {
      return;
    }

    var fileField = form.querySelector("[data-speaking-file]");
    var fallbackBlock = form.querySelector("[data-speaking-fallback]");
    var submitButton = form.querySelector("[data-speaking-submit]");
    var controls = root.querySelector("[data-recorder-controls]");
    var unsupportedBlock = root.querySelector("[data-recorder-unsupported]");
    var guidance = root.querySelector("[data-recorder-guidance]");
    var status = root.querySelector("[data-recorder-status]");
    var errorBox = root.querySelector("[data-recorder-error]");
    var startButton = root.querySelector("[data-recorder-start]");
    var stopButton = root.querySelector("[data-recorder-stop]");
    var againButton = root.querySelector("[data-recorder-again]");
    var preview = root.querySelector("[data-recorder-preview]");
    var previewBlock = root.querySelector("[data-recorder-preview-block]");
    var confirmMessage = form.getAttribute("data-confirm") || "";

    var stream = null;
    var recorder = null;
    var chunks = [];
    var blob = null;
    var blobExtension = null;
    var objectUrl = null;
    var pickedType = null;
    var state = STATES.READY;

    function say(message) {
      if (status) {
        status.textContent = t(message);
      }
    }

    function showError(message) {
      if (!errorBox) {
        return;
      }
      if (message) {
        errorBox.textContent = t(message);
        errorBox.hidden = false;
      } else {
        errorBox.textContent = "";
        errorBox.hidden = true;
      }
    }

    function releaseObjectUrl() {
      // A superseded preview URL is revoked immediately: the discarded
      // take must not stay reachable through a stale blob: URL.
      if (objectUrl) {
        window.URL.revokeObjectURL(objectUrl);
        objectUrl = null;
      }
    }

    function stopTracks() {
      if (!stream) {
        return;
      }
      var tracks = stream.getTracks ? stream.getTracks() : [];
      for (var i = 0; i < tracks.length; i += 1) {
        tracks[i].stop();
      }
      stream = null;
    }

    function discardRecording() {
      releaseObjectUrl();
      blob = null;
      blobExtension = null;
      chunks = [];
      if (preview) {
        preview.removeAttribute("src");
        preview.load();
      }
      if (previewBlock) {
        previewBlock.hidden = true;
      }
      if (fileField) {
        // Clear anything a previous take put on the input, so a discarded
        // recording can never be the thing that gets uploaded.
        fileField.value = "";
      }
    }

    function applyState(next) {
      state = next;
      var recording = state === STATES.RECORDING;
      var hasTake = state === STATES.PREVIEW;
      var busy = state === STATES.REQUESTING || state === STATES.UPLOADING;

      if (startButton) {
        startButton.disabled = recording || hasTake || busy;
        startButton.hidden = hasTake;
      }
      if (stopButton) {
        stopButton.disabled = !recording;
        stopButton.hidden = !recording;
      }
      if (againButton) {
        againButton.disabled = !hasTake;
        againButton.hidden = !hasTake;
      }
      if (submitButton) {
        var selectedFile = fileField && fileField.files && fileField.files.length === 1;
        submitButton.disabled = busy || recording || !(hasTake || selectedFile || state === STATES.UNSUPPORTED);
      }
      if (root) {
        root.setAttribute("data-recorder-state", state);
      }
    }

    /* Phase 6: a closed failure code (never a message, never audio) that
       the separate natural-use collector may observe when, and only
       when, collection runs for this Student. This recorder itself
       sends nothing anywhere. */
    function markFailure(code) {
      if (root) {
        root.setAttribute("data-recorder-failure", code);
      }
    }

    function fail(message, code) {
      markFailure(code || "recorder_error");
      stopTracks();
      recorder = null;
      discardRecording();
      // ERROR is a reported state, not a dead end: `applyState` leaves
      // Start enabled in it, so the Student can simply try again.
      applyState(STATES.ERROR);
      showError(message);
      say(t("Recording stopped because of a problem. You can press Start recording to try again."));
      if (fallbackBlock) fallbackBlock.hidden = false;
    }

    function onStop() {
      stopTracks();
      var type = (recorder && recorder.mimeType) || pickedType || "";
      var extension = extensionFor(type);
      recorder = null;

      if (!chunks.length) {
        fail(
          t("Nothing was recorded. Please check that your microphone is working and try again.")
        );
        return;
      }
      if (!extension) {
        // The browser negotiated a container this server does not accept.
        // Say so plainly and hand the Student the file-input fallback
        // rather than uploading something that can only be rejected.
        chunks = [];
        revealFallback(
          "Your browser recorded in a format this site cannot accept. You can still choose " +
            "an audio file to upload instead."
        );
        return;
      }

      releaseObjectUrl();
      blob = new window.Blob(chunks, { type: baseType(type) });
      blobExtension = extension;
      chunks = [];
      objectUrl = window.URL.createObjectURL(blob);
      if (preview) {
        // `controls` only -- never autoplay. The Student decides when to
        // listen back.
        preview.src = objectUrl;
      }
      if (previewBlock) {
        previewBlock.hidden = false;
      }
      applyState(STATES.PREVIEW);
      say(t("Recording ready. Play it back, record again, or submit it as your final answer."));
    }

    function startRecording() {
      showError("");
      if (fallbackBlock) fallbackBlock.hidden = true;
      applyState(STATES.REQUESTING);
      say(t("Asking your browser for permission to use the microphone…"));

      window.navigator.mediaDevices
        .getUserMedia({ audio: true })
        .then(function (granted) {
          stream = granted;
          pickedType = pickType();
          try {
            recorder =
              pickedType
                ? new window.MediaRecorder(stream, { mimeType: pickedType })
                : new window.MediaRecorder(stream);
          } catch (creationError) {
            fail(
              "This browser could not start a recording. You can choose an audio file to " +
                "upload instead.",
              "unsupported"
            );
            revealFallback("");
            return;
          }
          chunks = [];
          recorder.addEventListener("dataavailable", function (event) {
            if (event.data && event.data.size > 0) {
              chunks.push(event.data);
            }
          });
          recorder.addEventListener("stop", onStop);
          recorder.addEventListener("error", function () {
            fail(t("Recording stopped unexpectedly. Please try again."));
          });
          recorder.start();
          applyState(STATES.RECORDING);
          say(t("Recording… press Stop when you have finished speaking."));
        })
        .catch(function (permissionError) {
          stopTracks();
          var name = permissionError && permissionError.name;
          markFailure(
            name === "NotAllowedError" || name === "SecurityError"
              ? "permission_denied"
              : "recorder_error"
          );
          var message;
          if (name === "NotAllowedError" || name === "SecurityError") {
            message =
              t("Your browser blocked access to the microphone. Allow microphone access for this site in your browser settings, then press Start recording again.");
          } else if (name === "NotFoundError" || name === "DevicesNotFoundError") {
            message =
              t("No microphone was found. Connect a microphone and press Start recording again.");
          } else if (name === "NotReadableError") {
            message =
              t("Your microphone is already in use by another program. Close it and press Start recording again.");
          } else {
            message =
              t("The microphone could not be started. Please check your browser's microphone settings and try again.");
          }
          applyState(STATES.READY);
          // A missing/blocked microphone must not hide the existing file path.
          // Keep Start available for a later permission/device retry as well.
          if (fallbackBlock) fallbackBlock.hidden = false;
          showError(message);
          say(t("Microphone not available."));
        });
    }

    function stopRecording() {
      if (recorder && recorder.state !== "inactive") {
        recorder.stop(); // `onStop` stops every track and builds the take
      } else {
        stopTracks();
        applyState(STATES.READY);
      }
    }

    function recordAgain() {
      // Purely local: the previous take is dropped, its object URL is
      // revoked, and NOTHING is sent to the server.
      discardRecording();
      showError("");
      applyState(STATES.READY);
      say(t("Previous recording discarded. Press Start recording when you are ready."));
    }

    function revealFallback(message) {
      // Hand the page back to the ordinary file input.
      if (controls) {
        controls.hidden = true;
      }
      if (guidance) {
        guidance.hidden = true;
      }
      if (previewBlock) {
        previewBlock.hidden = true;
      }
      if (fallbackBlock) {
        fallbackBlock.hidden = false;
      }
      if (unsupportedBlock) {
        unsupportedBlock.hidden = false;
      }
      if (submitButton) {
        submitButton.disabled = false;
      }
      if (message) {
        showError(message);
      }
      applyState(STATES.UNSUPPORTED);
      say(t("Choose an audio file to upload as your final answer."));
    }

    function attachBlobToField() {
      if (!blob || !fileField) {
        return false;
      }
      var extension = blobExtension || extensionFor(blob.type);
      if (!extension) {
        return false;
      }
      var file = new window.File([blob], BASE_FILENAME + "." + extension, {
        type: blob.type
      });
      var transfer = new window.DataTransfer();
      transfer.items.add(file);
      fileField.files = transfer.files;
      return true;
    }

    form.addEventListener("submit", async function (event) {
      if (state === STATES.UPLOADING) {
        event.preventDefault();
        return;
      }
      if (state === STATES.RECORDING || state === STATES.REQUESTING) {
        event.preventDefault();
        showError(t("Please stop the recording before submitting it."));
        return;
      }
      if (blob) {
        if (!attachBlobToField()) {
          event.preventDefault();
          showError(
            "This recording could not be prepared for upload. Please record again, or " +
              "choose an audio file instead."
          );
          revealFallback("");
          return;
        }
      }
      event.preventDefault();
      if (confirmMessage && !(window.aelmsConfirm ? await window.aelmsConfirm(confirmMessage) : window.confirm(confirmMessage))) return;
      // Explicit final submission only. The same multipart payload and
      // one-time token are retained, including on a retry after a lost response.
      event.preventDefault();
      applyState(STATES.UPLOADING);
      if (submitButton) {
        submitButton.disabled = true;
      }
      say(t("Uploading your final recording… please do not close this page."));
      var progress = form.querySelector("[data-upload-progress]");
      if (!progress) {
        progress = document.createElement("progress"); progress.dataset.uploadProgress = "true";
        progress.max = 100; progress.setAttribute("aria-label", t("Recording upload progress"));
        progress.style.width = "100%"; form.appendChild(progress);
      }
      progress.hidden = false; progress.removeAttribute("value");
      var upload = new XMLHttpRequest();
      upload.open("POST", form.action);
      upload.upload.addEventListener("progress", function (update) {
        if (update.lengthComputable) {
          progress.value = Math.round(update.loaded * 100 / update.total);
          say(t("Uploading recording: %(percent)s%%", {percent:progress.value}) + (progress.value === 100 ? t(" · Waiting for server confirmation…") : ""));
        }
      });
      function retry(message) {
        progress.hidden = true;
        applyState(blob ? STATES.PREVIEW : STATES.UNSUPPORTED);
        if (submitButton) submitButton.disabled = false;
        showError(message);
        say(t("Review the recording, then retry with the same submission. A previously accepted upload will open its receipt."));
      }
      upload.addEventListener("load", function () {
        var destination = new URL(upload.responseURL || form.action, location.href);
        if (upload.status >= 200 && upload.status < 400 && destination.origin === location.origin && destination.pathname.startsWith("/student/") && destination.href !== new URL(form.action, location.href).href) {
          form.dispatchEvent(new Event("workspace:saved"));
          root.dataset.recorderState = "submitted";
          window.location.assign(destination.href);
          return;
        }
        var result = new DOMParser().parseFromString(upload.responseText, "text/html");
        var messages = Array.from(result.querySelectorAll(".field__error,.alert")).map(function (item) { return item.textContent.trim(); }).filter(Boolean);
        retry(messages.join(" ") || t("The server could not confirm this recording. Check your session and file limits, then retry."));
      });
      upload.addEventListener("error", function () { retry(t("The connection was interrupted. The server may have received the recording; retrying uses the same one-time submission token.")); });
      upload.send(new FormData(form));
    });

    if (fileField) fileField.addEventListener("change", function () {
      if (state !== STATES.RECORDING && state !== STATES.REQUESTING && state !== STATES.UPLOADING) {
        applyState(state);
      }
    });

    window.addEventListener("pagehide", function () {
      stopTracks();
      releaseObjectUrl();
    });

    if (!recordingIsSupported()) {
      revealFallback("");
      return;
    }

    // Supported: take over. The file input is hidden (it stays in the DOM
    // so the recorded take can be attached to it) and the recorder
    // controls are revealed.
    if (fallbackBlock) {
      fallbackBlock.hidden = true;
    }
    if (unsupportedBlock) {
      unsupportedBlock.hidden = true;
    }
    if (controls) {
      controls.hidden = false;
    }
    if (guidance) {
      guidance.hidden = false;
    }
    applyState(STATES.READY);
    say(t("Press Start recording when you are ready. Your browser will ask for permission to use the microphone."));

    if (startButton) {
      startButton.addEventListener("click", startRecording);
    }
    if (stopButton) {
      stopButton.addEventListener("click", stopRecording);
    }
    if (againButton) {
      againButton.addEventListener("click", recordAgain);
    }
  }

  document.querySelectorAll("[data-speaking-recorder]").forEach(function (root) {
    setup(root);
  });
})();
