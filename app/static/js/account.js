/* Local photo preview; native POST/CSRF and the server decoder own persistence. */
(function () {
  "use strict";
  var root = document.querySelector("[data-account-photo]");
  if (!root) return;
  var t = window.aelmsUI.t;
  var menu = root.querySelector("[data-photo-menu]");
  var toggle = menu.querySelector("summary");
  var edit = root.querySelector("[data-photo-edit]");
  var input = root.querySelector("input[type='file']");
  var form = input.form;
  var save = root.querySelector("[data-photo-save]");
  var avatar = toggle.querySelector(".figma-avatar");
  var viewer = root.querySelector("[data-photo-viewer]");
  var full = root.querySelector("[data-photo-full]");
  var view = root.querySelector("[data-photo-view]");
  var empty = root.querySelector("[data-photo-empty]");
  var errorDialog = root.querySelector("[data-photo-error]");
  var message = root.querySelector("[data-photo-error-message]");
  var cropper = root.querySelector("[data-photo-cropper]");
  var stage = root.querySelector("[data-photo-crop-stage]");
  var canvas = root.querySelector("[data-photo-crop-canvas]");
  var context = canvas.getContext("2d");
  var zoom = root.querySelector("[data-photo-zoom]");
  var zoomValue = root.querySelector("[data-photo-zoom-value]");
  var useCrop = root.querySelector("[data-photo-crop-use]");
  var recrop = root.querySelector("[data-photo-recrop]");
  var cropField = form.querySelector("input[name$='-crop']");
  var original = avatar.innerHTML;
  var savedURL = view.hidden ? null : view.getAttribute("href");
  var previewURL = null;
  var revision = 0;
  var ready = false;
  var selection = null;
  var editingSnapshot = null;
  var pointer = null;

  function openDialog(dialog) {
    menu.open = false;
    if (!dialog.open) dialog.showModal();
  }
  function showError(text) {
    message.textContent = t(text);
    openDialog(errorDialog);
  }
  function releasePreview() {
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = null;
  }
  function releaseSelection() {
    if (selection) URL.revokeObjectURL(selection.url);
    selection = null;
    pointer = null;
    delete stage.dataset.dragging;
    if (context) context.clearRect(0, 0, canvas.width, canvas.height);
  }
  function restoreSavedPreview() {
    avatar.innerHTML = original;
    view.hidden = !savedURL;
    empty.hidden = !!savedURL;
    view.setAttribute("href", savedURL || "#");
    if (savedURL) full.src = savedURL;
    else full.removeAttribute("src");
    full.hidden = !savedURL;
  }
  function resetSelection() {
    ready = false;
    save.hidden = true;
    recrop.hidden = true;
    input.value = "";
    cropField.value = "";
    releasePreview();
    releaseSelection();
    restoreSavedPreview();
    form.dispatchEvent(new Event("change", {bubbles:true}));
  }
  function cropSide() { return Math.min(selection.image.naturalWidth, selection.image.naturalHeight) / selection.zoom; }
  function drawCrop() {
    if (!selection || !context) return;
    var side = cropSide();
    selection.left = Math.max(0, Math.min(selection.left, selection.image.naturalWidth - side));
    selection.top = Math.max(0, Math.min(selection.top, selection.image.naturalHeight - side));
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(selection.image, selection.left, selection.top, side, side, 0, 0, canvas.width, canvas.height);
    zoom.value = String(selection.zoom);
    zoomValue.textContent = Math.round(selection.zoom * 100) + "%";
    zoom.setAttribute("aria-valuetext", zoomValue.textContent);
  }
  function resetCrop() {
    if (!selection) return;
    selection.zoom = 1;
    selection.left = (selection.image.naturalWidth - cropSide()) / 2;
    selection.top = (selection.image.naturalHeight - cropSide()) / 2;
    drawCrop();
  }
  function setZoom(value) {
    if (!selection) return;
    var oldSide = cropSide();
    selection.zoom = Math.max(1, Math.min(4, value));
    var difference = (oldSide - cropSide()) / 2;
    selection.left += difference;
    selection.top += difference;
    drawCrop();
  }
  function openCrop() {
    cropper.returnValue = "cancel";
    useCrop.disabled = false;
    drawCrop();
    openDialog(cropper);
    stage.focus({preventScroll:true});
  }
  function isPhoto(bytes) {
    return bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff ||
      [137,80,78,71,13,10,26,10].every(function (value, index) { return bytes[index] === value; }) ||
      [82,73,70,70].every(function (value, index) { return bytes[index] === value; }) &&
      [87,69,66,80].every(function (value, index) { return bytes[index + 8] === value; });
  }
  input.addEventListener("change", async function () {
    var sequence = ++revision;
    var file = input.files[0];
    var candidate = null;
    ready = false;
    save.hidden = true;
    recrop.hidden = true;
    cropField.value = "";
    editingSnapshot = null;
    releaseSelection();
    releasePreview();
    restoreSavedPreview();
    menu.open = false;
    if (!file) { resetSelection(); return; }
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error("Choose a photo no larger than 5 MB.");
      if (!file.size) throw new Error("The photo is empty. Choose another image.");
      var header = new Uint8Array(await file.slice(0, 12).arrayBuffer());
      if (sequence !== revision) return;
      if (!isPhoto(header)) throw new Error("Choose a valid JPG, PNG or WebP photo.");
      candidate = URL.createObjectURL(file);
      var image = new Image();
      image.src = candidate;
      await image.decode();
      if (sequence !== revision) return;
      if (image.naturalWidth * image.naturalHeight > 16000000) {
        throw new Error("Choose a photo with no more than 16 million pixels.");
      }
      if (!context) throw new Error("Choose a valid photo crop and try again.");
      selection = {image:image, url:candidate, left:0, top:0, zoom:1};
      candidate = null;
      resetCrop();
      openCrop();
    } catch (error) {
      if (sequence !== revision) return;
      resetSelection();
      var known = ["Choose a photo no larger than 5 MB.", "The photo is empty. Choose another image.",
        "Choose a photo with no more than 16 million pixels.", "Choose a valid photo crop and try again."];
      showError(known.includes(error.message) ? error.message : "Choose a valid JPG, PNG or WebP photo.");
    } finally {
      if (candidate) URL.revokeObjectURL(candidate);
    }
  });
  zoom.addEventListener("input", function () { setZoom(Number(zoom.value)); });
  root.querySelector("[data-photo-crop-reset]").addEventListener("click", resetCrop);
  stage.addEventListener("pointerdown", function (event) {
    if (!selection || !event.isPrimary || event.button !== 0) return;
    event.preventDefault();
    stage.focus({preventScroll:true});
    pointer = {id:event.pointerId, x:event.clientX, y:event.clientY};
    stage.setPointerCapture(event.pointerId);
    stage.dataset.dragging = "true";
  });
  stage.addEventListener("pointermove", function (event) {
    if (!selection || !pointer || event.pointerId !== pointer.id) return;
    var scale = cropSide() / stage.getBoundingClientRect().width;
    selection.left -= (event.clientX - pointer.x) * scale;
    selection.top -= (event.clientY - pointer.y) * scale;
    pointer.x = event.clientX;
    pointer.y = event.clientY;
    drawCrop();
  });
  ["pointerup", "pointercancel", "lostpointercapture"].forEach(function (name) {
    stage.addEventListener(name, function (event) {
      if (!pointer || pointer.id !== event.pointerId) return;
      if (stage.hasPointerCapture(event.pointerId)) stage.releasePointerCapture(event.pointerId);
      pointer = null;
      delete stage.dataset.dragging;
    });
  });
  stage.addEventListener("keydown", function (event) {
    if (!selection) return;
    var step = cropSide() * (event.shiftKey ? .1 : .02);
    if (event.key === "ArrowLeft") selection.left += step;
    else if (event.key === "ArrowRight") selection.left -= step;
    else if (event.key === "ArrowUp") selection.top += step;
    else if (event.key === "ArrowDown") selection.top -= step;
    else if (event.key === "+" || event.key === "=") setZoom(selection.zoom + .05);
    else if (event.key === "-") setZoom(selection.zoom - .05);
    else if (event.key === "Home") resetCrop();
    else return;
    event.preventDefault();
    drawCrop();
  });
  recrop.addEventListener("click", function () {
    if (!selection || !ready) return;
    editingSnapshot = {left:selection.left, top:selection.top, zoom:selection.zoom};
    openCrop();
  });
  root.querySelectorAll("[data-photo-crop-cancel]").forEach(function (button) {
    button.addEventListener("click", function () { cropper.close("cancel"); });
  });
  cropper.addEventListener("close", function () {
    if (cropper.returnValue !== "use") {
      ++revision;
      if (editingSnapshot && selection) {
        Object.assign(selection, editingSnapshot);
        drawCrop();
      } else resetSelection();
    }
    editingSnapshot = null;
    (ready ? recrop : toggle).focus({preventScroll:true});
  });
  useCrop.addEventListener("click", function () {
    if (!selection || !context) return;
    var sequence = revision;
    var current = selection;
    var side = cropSide();
    var chosen = {left:current.left, top:current.top, zoom:current.zoom};
    var instruction = {x:chosen.left / current.image.naturalWidth,
      y:chosen.top / current.image.naturalHeight, size:side / Math.min(current.image.naturalWidth, current.image.naturalHeight)};
    useCrop.disabled = true;
    canvas.toBlob(function (blob) {
      if (sequence !== revision || current !== selection || !cropper.open) return;
      useCrop.disabled = false;
      if (!blob) { cropper.close("cancel"); showError("Choose a valid photo crop and try again."); return; }
      Object.assign(current, chosen);
      releasePreview();
      previewURL = URL.createObjectURL(blob);
      var image = new Image();
      image.src = previewURL;
      image.alt = "";
      avatar.replaceChildren(image);
      full.src = previewURL;
      full.hidden = false;
      view.href = previewURL;
      view.hidden = false;
      empty.hidden = true;
      // Send the original file and this square to the authoritative server decoder.
      cropField.value = JSON.stringify(instruction);
      ready = true;
      save.hidden = false;
      recrop.hidden = false;
      cropper.close("use");
      form.dispatchEvent(new Event("change", {bubbles:true}));
    }, "image/png");
  });
  edit.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); input.click(); }
  });
  view.addEventListener("click", function (event) {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    event.stopPropagation();
    openDialog(viewer);
  });
  form.addEventListener("submit", function (event) {
    if (!ready) { event.preventDefault(); return; }
    save.disabled = true;
    // Shared unsaved-work confirmation may temporarily cancel this submit.
    queueMicrotask(function () { if (event.defaultPrevented) save.disabled = false; });
  });
  root.querySelectorAll("[data-photo-close]").forEach(function (button) {
    button.addEventListener("click", function () { button.closest("dialog").close(); });
  });
  [viewer, errorDialog, cropper].forEach(function (dialog) {
    if (dialog !== cropper) dialog.addEventListener("close", function () { toggle.focus({preventScroll:true}); });
    dialog.addEventListener("click", function (event) {
      if (event.target !== dialog) return;
      var bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right ||
          event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    });
  });
  menu.addEventListener("keydown", function (event) {
    if (event.key === "Escape") { event.preventDefault(); menu.open = false; toggle.focus(); }
  });
  document.addEventListener("click", function (event) {
    if (!menu.contains(event.target)) menu.open = false;
  });
  menu.addEventListener("focusout", function (event) {
    if (event.relatedTarget && !menu.contains(event.relatedTarget)) menu.open = false;
  });
  if (errorDialog.hasAttribute("data-photo-server-error")) {
    errorDialog.close();
    openDialog(errorDialog);
  }
  window.addEventListener("pageshow", function (event) {
    save.disabled = false;
    if (event.persisted) { ++revision; resetSelection(); }
  });
  window.addEventListener("pagehide", function () {
    ++revision;
    if (cropper.open) cropper.close("cancel");
    releasePreview();
    releaseSelection();
  });
})();
