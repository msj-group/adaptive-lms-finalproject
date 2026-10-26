/* The existing GroupForm and create/edit endpoints remain authoritative. Enhance only
   their presentation/transport; never construct a replacement business form. */
(function () {
  "use strict";
  var root = document.querySelector("[data-admin-groups],[data-admin-group-workspace]");
  var register = window.aelmsRegisterForms;
  var changed = window.aelmsFormHasChanges;
  var confirmAction = window.aelmsConfirm;
  if (!root || !window.fetch || !window.AbortController || !window.DOMParser ||
      !register || !changed || !confirmAction) return;
  var dialog = root.querySelector("[data-group-create-dialog]");
  var query = root.querySelector("form[data-query-refresh]");
  var workspace = root.querySelector("[data-group-workspace-content]");
  if (!dialog || !dialog.showModal || (query ? query.dataset.queryRefreshReady !== "true" : !workspace)) return;
  var t = window.aelmsUI ? window.aelmsUI.t : function (value) { return value; };
  var actor = root.dataset.groupActor;
  var nativeLink = dialog.querySelector("[data-group-create-native]");
  var heading = dialog.querySelector("[data-group-editor-heading]");
  var createURL = new URL(nativeLink.href, location.href);
  var listURL = new URL(root.dataset.groupListUrl, location.href);
  if (!actor || createURL.origin !== location.origin || listURL.origin !== location.origin) return;
  var content = dialog.querySelector("[data-group-create-content]");
  var messages = dialog.querySelector("[data-group-create-messages]");
  var status = dialog.querySelector("[data-group-create-status]");
  var recovery = dialog.querySelector("[data-group-create-recovery]");
  var retry = dialog.querySelector("[data-group-create-retry]");
  var close = dialog.querySelector("[data-group-create-close]");
  var check = dialog.querySelector("[data-group-create-check]");
  var trigger, controller, revision = 0, active = true, posting = false;
  var confirming = false, scopeInvalid = false, uncertain = false;
  var initialValues = null;
  var targetURL = createURL, mode = "create", groupID = "";
  var uncertainMessage = "The result could not be confirmed. Your draft is still here. Check the group list before trying again.";
  var sessionMessage = "Your session changed. Reload this page to continue.";

  function notice(value, error) {
    status.textContent = value ? t(value) : "";
    status.hidden = !value;
    status.setAttribute("role", error ? "alert" : "status");
  }
  function cancelRead() {
    revision += 1;
    if (controller) controller.abort();
    controller = null;
  }
  function release() {
    content.querySelectorAll("form").forEach(function (form) {
      form.dispatchEvent(new Event("workspace:saved"));
    });
  }
  function values(form) {
    return JSON.stringify(Array.from(form.elements).filter(function (field) {
      return field.name && field.type !== "hidden" && field.type !== "submit";
    }).map(function (field) { return [field.name,field.value]; }));
  }
  function copyMessages(target, incoming) {
    var source = incoming.querySelector("[data-group-messages]");
    if (source && source.querySelector("script,iframe,object,embed")) throw new Error("Unexpected message");
    target.replaceChildren.apply(target, source ? Array.from(source.childNodes).map(function (node) {
      return document.importNode(node, true);
    }) : []);
  }
  async function parsed(response) {
    if (response.status === 401 || response.status === 403) scopeInvalid = true;
    if (!response.ok || !(response.headers.get("Content-Type") || "").includes("text/html")) throw new Error("Unavailable form");
    var url = new URL(response.url, location.href);
    if (url.origin !== location.origin || (url.pathname !== targetURL.pathname && url.pathname !== listURL.pathname)) throw new Error("Unexpected destination");
    var page = new DOMParser().parseFromString(await response.text(), "text/html");
    var incoming = url.pathname === targetURL.pathname ? page.querySelector('[data-group-editor][data-group-mode="' + mode + '"]') : page.querySelector("[data-admin-groups]");
    if (!incoming) throw new Error("Unexpected page");
    if (incoming.dataset.groupActor !== actor) {
      scopeInvalid = true;
      throw new Error("Changed session");
    }
    if (url.pathname === listURL.pathname) return {kind:"list", root:incoming};
    if (mode === "edit" && incoming.dataset.groupPublicId !== groupID) throw new Error("Unexpected group");
    var card = incoming.querySelector("[data-group-form-card]");
    var form = card && card.querySelector('form[method="post"]');
    if (!form || !form.querySelector('[name="csrf_token"]') || card.querySelector("script,iframe,object,embed")) throw new Error("Unexpected form");
    var action = new URL(form.getAttribute("action") || targetURL.href, targetURL.href);
    if (action.origin !== targetURL.origin || action.pathname !== targetURL.pathname) throw new Error("Unexpected action");
    if (mode === "edit" && !form.querySelector('[name="edit_snapshot"][value]:not([value=""])')) throw new Error("Missing edit snapshot");
    if (!["study_starts_at","academic_term_id","course_id","name","code","capacity"].every(function (name) {
      return Boolean(form.querySelector('[name="' + name + '"]'));
    })) throw new Error("Incomplete form");
    return {kind:"form", root:incoming, card:card, redirected:response.redirected};
  }
  function install(loaded) {
    var card = document.importNode(loaded.card, true);
    // The list filters and native editor share field IDs. Namespace IDs and
    // their label/ARIA references only; retain every native name and value.
    var ids = {};
    card.querySelectorAll("[id]").forEach(function (element) {
      var old = element.id;
      ids[old] = "group-create-" + old;
      element.id = ids[old];
    });
    card.querySelectorAll("[for],[aria-labelledby],[aria-describedby],[aria-controls]").forEach(function (element) {
      ["for","aria-labelledby","aria-describedby","aria-controls"].forEach(function (attribute) {
        if (element.hasAttribute(attribute)) element.setAttribute(attribute, element.getAttribute(attribute).split(/\s+/).map(function (id) { return ids[id] || id; }).join(" "));
      });
    });
    release();
    content.replaceChildren(card);
    copyMessages(messages, loaded.root);
    var form = card.querySelector("form");
    form.action = targetURL.href;
    form.dataset.groupCreateForm = "true";
    form.querySelectorAll("label").forEach(function (label) { label.textContent = t(label.textContent.trim()); });
    form.querySelectorAll('input[type="submit"]').forEach(function (button) { button.value = t(button.value); });
    var select = form.querySelector('select[name="course_id"]');
    var level = form.querySelector("[data-group-level-display]");
    function updateLevel() {
      var option = select && select.options[select.selectedIndex];
      if (level) level.textContent = option && option.dataset.level || "—";
    }
    if (select) select.addEventListener("change", updateLevel);
    updateLevel();
    if (initialValues === null) initialValues = values(form);
    form.querySelectorAll(".field__error").forEach(function (error, index) {
      var field = error.closest(".field");
      var input = field && field.querySelector("input:not([type='hidden']),select,textarea");
      if (!input) return;
      error.id = "group-create-error-" + index;
      input.setAttribute("aria-invalid", "true");
      input.setAttribute("aria-describedby", ((input.getAttribute("aria-describedby") || "") + " " + error.id).trim());
    });
    register(content);
    var focus = form.querySelector('[aria-invalid="true"]') || form.querySelector('[name="name"]');
    if (focus) focus.focus();
  }
  async function loadForm() {
    if (posting || scopeInvalid) return;
    cancelRead();
    var mine = revision;
    var request = new AbortController();
    controller = request;
    notice("Loading form…");
    recovery.hidden = true;
    check.hidden = true;
    try {
      var response = await fetch(targetURL.href, {credentials:"same-origin", cache:"no-store", signal:request.signal});
      var loaded = await parsed(response);
      if (mine !== revision || request.signal.aborted || !dialog.open || !active) return;
      if (loaded.kind === "form") install(loaded);
      else {
        copyMessages(messages, loaded.root);
        recovery.hidden = false;
      }
      notice("");
    } catch (error) {
      if (mine === revision && !request.signal.aborted && dialog.open && active) {
        notice(scopeInvalid ? sessionMessage : "The form could not load. Try again.", true);
        recovery.hidden = false;
        retry.disabled = scopeInvalid;
      }
    } finally { if (controller === request) controller = null; }
  }
  function finishClose() {
    cancelRead();
    release();
    content.replaceChildren();
    messages.replaceChildren();
    notice("");
    recovery.hidden = true;
    check.hidden = true;
    uncertain = false;
    initialValues = null;
    dialog.close();
    root.dispatchEvent(new Event("aelms:group-editor-close"));
    if (trigger && trigger.isConnected) trigger.focus();
  }
  async function closeDialog() {
    if (posting || confirming) return;
    var form = content.querySelector("form");
    // A validation response rebinds the entered values as the new native form's
    // baseline. Keep the original GET baseline so that draft is still protected.
    if (changed(content) || form && initialValues !== null && values(form) !== initialValues) {
      confirming = true;
      var accepted;
      try { accepted = await confirmAction(t("Discard this group draft?"), t("Discard draft")); }
      finally { confirming = false; }
      if (!accepted) return;
    }
    finishClose();
  }
  async function refreshWorkspace() {
    var destination = new URL(location.href);
    var message = root.querySelector("[data-group-workspace-status]");
    var reload = root.querySelector("[data-group-workspace-reload]");
    workspace.setAttribute("aria-busy", "true");
    try {
      var response = await fetch(destination.href, {credentials:"same-origin", cache:"no-store"});
      var url = new URL(response.url);
      if (!active || !response.ok || response.redirected || url.origin !== destination.origin || url.pathname !== destination.pathname ||
          !(response.headers.get("Content-Type") || "").includes("text/html")) throw new Error("Unavailable workspace");
      var page = new DOMParser().parseFromString(await response.text(), "text/html");
      var incoming = page.querySelector("[data-admin-group-workspace]");
      var updated = incoming && incoming.querySelector("[data-group-workspace-content]");
      if (!incoming || incoming.dataset.groupActor !== actor || incoming.dataset.groupPublicId !== root.dataset.groupPublicId ||
          !updated || updated.querySelector("script,iframe,object,embed")) throw new Error("Unexpected workspace");
      workspace.replaceChildren.apply(workspace, Array.from(updated.childNodes).map(function (node) { return document.importNode(node, true); }));
      document.title = page.title;
      message.hidden = true; reload.hidden = true;
      var edit = workspace.querySelector("[data-group-edit-open]");
      if (edit) edit.focus();
    } catch (error) {
      if (active) {
        message.textContent = t("The group details could not be refreshed. Reload this page to see the current values.");
        message.hidden = false; reload.hidden = false;
      }
    } finally { workspace.removeAttribute("aria-busy"); }
  }
  root.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var link = event.target.closest("a[href]");
    if (!link || link.target || link.hasAttribute("download")) return;
    if (dialog.open && link.matches("[data-group-create-cancel]")) {
      event.preventDefault(); event.stopPropagation(); closeDialog(); return;
    }
    if (!link.matches("[data-group-create-open],[data-group-edit-open]")) return;
    var url = new URL(link.href, location.href);
    var editID = link.matches("[data-group-edit-open]") ? link.dataset.groupPublicId : "";
    var expectedPath = editID ? listURL.pathname + "/" + encodeURIComponent(editID) + "/edit" : createURL.pathname;
    if (url.origin !== createURL.origin || url.pathname !== expectedPath) return;
    if (workspace && workspace.getAttribute("aria-busy") === "true") { event.preventDefault(); return; }
    event.preventDefault(); event.stopPropagation();
    if (dialog.open) return;
    trigger = link;
    targetURL = url; groupID = editID || ""; mode = groupID ? "edit" : "create";
    heading.textContent = t(mode === "edit" ? "Edit Group" : "New Group");
    nativeLink.href = targetURL.href;
    root.dispatchEvent(new Event("aelms:group-editor-open"));
    dialog.showModal();
    if (scopeInvalid) { notice(sessionMessage, true); recovery.hidden = false; retry.disabled = true; }
    else loadForm();
  });
  close.addEventListener("click", closeDialog);
  retry.addEventListener("click", loadForm);
  dialog.addEventListener("cancel", function (event) { event.preventDefault(); closeDialog(); });
  dialog.addEventListener("click", function (event) {
    if (event.target !== dialog) return;
    var rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDialog();
  });
  // Capture before the registered form's native-submit cleanup microtask.
  // Failed/uncertain asynchronous writes must retain their dirty draft state.
  dialog.addEventListener("submit", async function (event) {
    var form = event.target;
    if (event.defaultPrevented || !form.matches("form[data-group-create-form]") || form.method.toLowerCase() !== "post") return;
    event.preventDefault(); event.stopPropagation();
    if (posting || scopeInvalid || uncertain) return;
    var action = new URL(form.action, location.href);
    if (action.origin !== targetURL.origin || action.pathname !== targetURL.pathname) return;
    var button = event.submitter || form.querySelector('[type="submit"]');
    var data = new FormData(form);
    if (button && button.name) data.append(button.name, button.value);
    posting = true;
    close.disabled = true;
    if (button) button.disabled = true;
    form.inert = true;
    form.setAttribute("aria-busy", "true");
    recovery.hidden = true;
    notice("Saving...");
    try {
      var response = await fetch(targetURL.href, {method:"POST", body:data, credentials:"same-origin", cache:"no-store"});
      var loaded = await parsed(response);
      if (!active || !dialog.open) return;
      if (loaded.kind === "form") {
        // A native stale-edit redirect intentionally discards attempted values.
        // Keep its fresh persisted values paired with its fresh signed snapshot.
        if (mode === "edit" && loaded.redirected) initialValues = null;
        install(loaded); notice("");
      }
      else {
        copyMessages(root.querySelector("[data-group-messages]"), loaded.root);
        finishClose();
        if (query) query.dispatchEvent(new Event("aelms:query-refresh", {cancelable:true}));
        else refreshWorkspace();
      }
    } catch (error) {
      uncertain = true;
      if (active && dialog.open) { notice(scopeInvalid ? sessionMessage : uncertainMessage, true); check.hidden = false; }
    } finally {
      posting = false;
      close.disabled = false;
      form.inert = false;
      form.removeAttribute("aria-busy");
      if (button) button.disabled = uncertain || scopeInvalid;
    }
  }, true);
  window.addEventListener("pagehide", function () {
    active = false; cancelRead();
    if (posting) uncertain = true;
  });
  window.addEventListener("pageshow", function () {
    active = true;
    if (uncertain && dialog.open) {
      notice(uncertainMessage, true); check.hidden = false;
      content.querySelectorAll('[type="submit"]').forEach(function (button) { button.disabled = true; });
    }
  });
})();
