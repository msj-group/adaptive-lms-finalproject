/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
/* Eclipse presentation only. Credentials use the original native CSRF form;
   no authentication request, input storage or simulated success is added. */
(function () {
  "use strict";
  var page = document.querySelector("[data-eclipse-login]");
  if (!page) return;
  var root = document.documentElement;
  var t = window.aelmsUI ? window.aelmsUI.t : function (text) { return text; };
  var form = page.querySelector("[data-login-form]");
  var emailField = page.querySelector("[data-email-field]");
  var email = emailField.querySelector("input");
  var emailStatus = page.querySelector("[data-email-status]");
  var submit = page.querySelector("[data-login-submit]");
  var label = page.querySelector("[data-login-label]");
  var spinner = page.querySelector("[data-login-spinner]");
  var status = page.querySelector("[data-login-status]");
  var previousValid = false;

  function emailFormat() {
    // A visual format hint only. Flask/WTForms remains authoritative.
    var valid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value.trim());
    emailField.dataset.valid = String(valid && emailField.dataset.error !== "true");
    if (valid && !previousValid && emailField.dataset.error !== "true") emailStatus.textContent = t("Email address format is valid.");
    else if (!valid || emailField.dataset.error === "true") emailStatus.textContent = "";
    previousValid = valid;
  }
  form.querySelectorAll(".ec-field input").forEach(function (input) {
    pageScope.listen(input, "input", function () {
      var field = input.closest(".ec-field");
      if (field.dataset.error === "true") {
        field.dataset.error = "false";
        field.querySelector("[data-field-errors]").hidden = true;
        input.setAttribute("aria-invalid", "false");
      }
      emailFormat();
    });
    pageScope.listen(input, "change", emailFormat);
    pageScope.listen(input, "focus", emailFormat);
    pageScope.listen(input, "animationstart", function (event) { if (event.animationName === "ec-autofill") emailFormat(); });
  });
  emailFormat();

  var passwordToggle = page.querySelector("[data-password-toggle]");
  var password = document.getElementById(passwordToggle.getAttribute("aria-controls"));
  passwordToggle.hidden = false;
  pageScope.listen(passwordToggle, "click", function () {
    var show = password.type === "password";
    password.type = show ? "text" : "password";
    passwordToggle.setAttribute("aria-pressed", String(show));
    passwordToggle.setAttribute("aria-label", t(show ? "Hide password" : "Show password"));
  });

  function clearBusy() {
    form.removeAttribute("aria-busy");
    submit.disabled = false;
    label.textContent = t("Sign in");
    spinner.hidden = true;
    status.textContent = "";
    emailFormat();
  }
  pageScope.listen(form, "submit", function (event) {
    // Keep native submission, including the existing next path and CSRF value.
    queueMicrotask(function () {
      if (event.defaultPrevented) return;
      form.setAttribute("aria-busy", "true");
      label.textContent = t("Signing in…");
      status.textContent = t("Signing in…");
      spinner.hidden = false;
      // Let the browser capture the named submitter first, as before.
      pageScope.setTimeout(function () { submit.disabled = true; }, 0);
    });
  });
  pageScope.listen(window, "pageshow", clearBusy);

  var themeButton = page.querySelector("[data-login-theme]");
  function themeLabel() {
    themeButton.setAttribute("aria-label", t(root.dataset.theme === "dark" ? "Switch to light appearance" : "Switch to dark appearance"));
  }
  if (window.aelmsUI && typeof window.aelmsUI.theme === "function") {
    themeButton.hidden = false;
    themeLabel();
    pageScope.listen(themeButton, "click", function () {
      window.aelmsUI.theme(root.dataset.theme === "dark" ? "light" : "dark");
    });
    pageScope.createObserver(MutationObserver, themeLabel).observe(root, {attributes:true, attributeFilter:["data-theme"]});
  }

  var dialog = page.querySelector("[data-login-help-dialog]");
  var helpDetails = page.querySelector(".ec-help-inline");
  var previousFocus;
  page.querySelectorAll("[data-login-help]").forEach(function (trigger) {
    pageScope.listen(trigger, "click", function (event) {
      if (event.button && event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      if (typeof dialog.showModal !== "function") {
        if (trigger.tagName !== "SUMMARY") {
          event.preventDefault();
          helpDetails.open = true;
          helpDetails.querySelector("summary").focus();
        }
        return;
      }
      event.preventDefault();
      previousFocus = trigger;
      if (!dialog.open) dialog.showModal();
    });
  });
  page.querySelectorAll("[data-login-help-close]").forEach(function (button) {
    pageScope.listen(button, "click", function () { dialog.close(); });
  });
  pageScope.listen(dialog, "click", function (event) {
    var bounds = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close();
  });
  pageScope.listen(dialog, "close", function () { if (previousFocus && previousFocus.isConnected) previousFocus.focus({preventScroll:true}); });
  function syncVisibility() { page.dataset.pageHidden = String(document.hidden); }
  pageScope.listen(document, "visibilitychange", syncVisibility);
  syncVisibility();
}());

});
