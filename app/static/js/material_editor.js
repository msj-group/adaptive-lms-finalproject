/*
 * Small local rich-text editor for Lesson Materials (M12). No CDN, no
 * external library. A `contenteditable` surface with a limited toolbar
 * mirroring the server-side nh3 allowlist (app/services/material_content.py):
 * paragraphs, headings (h2/h3), bold/italic/underline, lists, blockquote,
 * preformatted text, a horizontal rule, and HTTPS links.
 *
 * The editor is a convenience only -- the server sanitizes on every save
 * and re-sanitizes again at render time. Nothing here is a security
 * boundary.
 */
(function () {
  "use strict";

  function initEditor(root) {
    var editor = root.querySelector(".rte-editor");
    var hidden = root.querySelector("textarea");
    if (!editor || !hidden) {
      return;
    }

    // Seed the visible editor from the hidden field's current value
    // (existing sanitized content on an edit form) and hide the raw
    // textarea -- it stays in the DOM only to carry the value on submit.
    editor.innerHTML = hidden.value || "";
    hidden.style.display = "none";
    hidden.setAttribute("aria-hidden", "true");
    hidden.tabIndex = -1;

    function sync() {
      hidden.value = editor.innerHTML;
    }

    editor.addEventListener("input", sync);
    editor.addEventListener("blur", sync);

    var form = root.closest("form");
    if (form) {
      form.addEventListener("submit", sync);
    }

    root.querySelectorAll(".rte-toolbar__btn[data-command]").forEach(function (button) {
      button.addEventListener("click", function (event) {
        event.preventDefault();
        editor.focus();
        var command = button.dataset.command;
        var value = button.dataset.value || null;
        if (command === "createLink") {
          var url = window.prompt("Link URL (https:// only):", "https://");
          if (!url) {
            return;
          }
          if (url.indexOf("https://") !== 0) {
            window.alert("Only https:// links are allowed.");
            return;
          }
          document.execCommand("createLink", false, url);
        } else {
          document.execCommand(command, false, value);
        }
        sync();
      });
    });
  }

  document.querySelectorAll("[data-rte-root]").forEach(initEditor);
})();
