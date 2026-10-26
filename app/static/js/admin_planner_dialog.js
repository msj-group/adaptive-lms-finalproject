/* Focus planner dialogs reuse the native actor-bound event form and Group GET.
   Business validation, signed tokens, authorization and writes remain server-owned. */
(function () {
  "use strict";
  var root = document.querySelector("[data-planner-workspace]");
  var dialog = root && root.querySelector("[data-planner-dialog]");
  if (!dialog || !dialog.showModal || !window.fetch || !window.DOMParser ||
      !window.AbortController || !window.FormData || !window.aelmsRegisterForms ||
      !window.aelmsFormHasChanges || !window.aelmsConfirm) return;
  var t = window.aelmsUI.t;
  var actor = root.dataset.plannerActor;
  var createURL = new URL(root.dataset.plannerCreateUrl, location.href);
  var eventPattern = new URL(root.dataset.plannerEventUrl, location.href);
  var groupPattern = new URL(root.dataset.plannerGroupUrl, location.href);
  var slotPattern = new URL(root.dataset.plannerSlotCreateUrl, location.href);
  if (!actor || [createURL, eventPattern, groupPattern, slotPattern].some(function (url) { return url.origin !== location.origin; })) return;
  var title = dialog.querySelector("[data-planner-dialog-title]");
  var content = dialog.querySelector("[data-planner-dialog-content]");
  var messages = dialog.querySelector("[data-planner-dialog-messages]");
  var status = dialog.querySelector("[data-planner-status]");
  var close = dialog.querySelector("[data-planner-close]");
  var retry = dialog.querySelector("[data-planner-retry]");
  var recovery = dialog.querySelector("[data-planner-recovery]");
  var nativeLink = dialog.querySelector("[data-planner-native]");
  var back = dialog.querySelector("[data-planner-back]");
  var choices = root.querySelector("[data-planner-group-choices]");
  var trigger, targetURL, groupID = "", mode = "", controller, revision = 0;
  var posting = false, confirming = false, scopeInvalid = false, uncertain = false;
  var completed = false, active = true, fromChoices = false, baseline = null;
  var slotParent = null, refreshOnClose = false;
  var sessionMessage = "Your session changed. Reload this page to continue.";
  var uncertainMessage = "The result could not be confirmed. Your details remain here. Review the calendar before trying again.";
  var slotUncertainMessage = "The result could not be confirmed. Your details remain here. Review the group schedule before trying again.";

  function notice(value, error) {
    status.textContent = value ? t(value) : "";
    status.hidden = !value;
    status.setAttribute("role", error ? "alert" : "status");
  }
  function safeNode(node) {
    if (!node || node.querySelector("script,iframe,object,embed")) throw new Error("Unexpected content");
    return document.importNode(node, true);
  }
  function release() {
    content.querySelectorAll("form").forEach(function (form) { form.dispatchEvent(new Event("workspace:saved")); });
  }
  function values(form) {
    return JSON.stringify(Array.from(form.elements).filter(function (field) {
      return field.name && field.type !== "hidden" && field.type !== "submit";
    }).map(function (field) { return [field.name, field.type === "checkbox" ? field.checked : field.value]; }));
  }
  function copyMessages(incoming) {
    var source = incoming.querySelector("[data-planner-messages]");
    messages.replaceChildren();
    if (source) messages.appendChild(safeNode(source));
  }
  function cancelRead() {
    revision += 1;
    if (controller) controller.abort();
    controller = null;
  }
  function fallback(url, label) {
    nativeLink.href = url.href;
    nativeLink.textContent = t(label || "Open full page");
  }
  function checkActor(node) {
    if (!node || node.dataset.plannerActor !== actor) { scopeInvalid = true; throw new Error("Changed actor"); }
  }
  function groupURL() { return new URL(groupPattern.pathname.replace("__group__", encodeURIComponent(groupID)), location.origin); }
  function groupResult(page, url) {
    if (url.pathname !== groupURL().pathname) throw new Error("Unexpected group URL");
    var group = page.querySelector("[data-planner-group-workspace]");
    checkActor(group);
    if (group.dataset.groupPublicId !== groupID) throw new Error("Unexpected group");
    return {kind:"group", root:group};
  }
  async function parse(response, afterPost) {
    if (response.status === 401 || response.status === 403) scopeInvalid = true;
    if (!response.ok || !(response.headers.get("Content-Type") || "").includes("text/html")) throw new Error("Unavailable page");
    var url = new URL(response.url, location.href);
    if (url.origin !== location.origin || url.search || url.hash) throw new Error("Unexpected destination");
    var page = new DOMParser().parseFromString(await response.text(), "text/html");
    if (mode === "group") return groupResult(page, url);
    if (mode === "slot") {
      if (url.pathname === groupURL().pathname && response.redirected) {
        var result = groupResult(page, url);
        // Rejections can also redirect here. Only the native create-success
        // marker confirms a save; retain all actual rejection messages.
        result.created = afterPost && result.root.dataset.plannerSlotCreated === "true";
        return result;
      }
      if (url.pathname !== targetURL.pathname) throw new Error("Unexpected slot URL");
      var slotEditor = page.querySelector('[data-planner-slot-editor][data-planner-mode="create"]');
      checkActor(slotEditor);
      if (slotEditor.dataset.groupPublicId !== groupID) throw new Error("Unexpected slot group");
      var slotForm = slotEditor.querySelector("form[data-planner-slot-form]");
      var slotAction = slotForm && new URL(slotForm.getAttribute("action") || targetURL.href, targetURL.href);
      if (!slotForm || slotForm.method.toLowerCase() !== "post" || slotAction.href !== targetURL.href ||
          !["csrf_token", "day_of_week", "start_time", "end_time", "effective_start_date", "effective_end_date", "room_id", "location"].every(function (name) { return slotForm.querySelector('[name="' + name + '"]'); })) throw new Error("Unexpected slot form");
      return {kind:"form", root:slotEditor, form:slotForm, redirected:response.redirected};
    }
    if (url.pathname === createURL.pathname) {
      var editor = page.querySelector('[data-planner-event-editor][data-planner-mode="create"]');
      checkActor(editor);
      var form = editor.querySelector("form[data-planner-native-form]");
      var action = form && new URL(form.getAttribute("action") || createURL.href, createURL.href);
      if (!form || form.method.toLowerCase() !== "post" || action.origin !== location.origin ||
          action.pathname !== createURL.pathname || action.search || action.hash ||
          !["csrf_token", "event_state", "title", "event_date", "start_time", "end_time", "location", "details"].every(function (name) { return form.querySelector('[name="' + name + '"]'); }) ) throw new Error("Unexpected native form");
      if (!form.querySelector('[name="event_state"][value]:not([value=""])')) throw new Error("Missing signed state");
      return {kind:"form", root:editor, form:form, redirected:response.redirected};
    }
    var detail = page.querySelector("[data-planner-event-detail]");
    if (!afterPost || !response.redirected || !detail || !detail.dataset.plannerEventId ||
        url.pathname !== eventPattern.pathname.replace("__event__", encodeURIComponent(detail.dataset.plannerEventId))) throw new Error("Unconfirmed result");
    checkActor(detail);
    return {kind:"created", root:detail, url:url};
  }
  function installForm(loaded) {
    var slot = mode === "slot";
    var formURL = slot ? targetURL : createURL;
    var prefix = slot ? "planner-slot-" : "planner-event-";
    var form = safeNode(loaded.form);
    var ids = {};
    form.querySelectorAll("[id]").forEach(function (field) { ids[field.id] = prefix + field.id; field.id = ids[field.id]; });
    form.querySelectorAll("[for],[aria-labelledby],[aria-describedby]").forEach(function (field) {
      ["for", "aria-labelledby", "aria-describedby"].forEach(function (attribute) {
        if (field.hasAttribute(attribute)) field.setAttribute(attribute, field.getAttribute(attribute).split(/\s+/).map(function (id) { return ids[id] || id; }).join(" "));
      });
    });
    form.action = formURL.href;
    var noRoom = slot && form.querySelector('select[name="room_id"] option[value="0"]');
    if (noRoom) noRoom.textContent = t("No room assigned");
    form.querySelectorAll('a[href]').forEach(function (link) {
      var url = new URL(link.href, formURL.href);
      if (url.origin === location.origin) link.dataset.plannerFormCancel = "true";
    });
    release(); content.replaceChildren();
    var context = slot && loaded.root.querySelector("[data-planner-slot-context]");
    if (context) content.appendChild(safeNode(context));
    var hint = loaded.root.querySelector(slot ? "[data-planner-slot-hint]" : "[data-planner-event-hint]");
    if (hint) content.appendChild(safeNode(hint));
    content.appendChild(form); copyMessages(loaded.root);
    if (baseline === null || loaded.redirected) baseline = values(form);
    form.querySelectorAll(".field__error").forEach(function (error, index) {
      var field = error.closest(".field"), input = field && field.querySelector("input,textarea,select");
      if (!input) return;
      error.id = prefix + "error-" + index;
      input.setAttribute("aria-invalid", "true");
      input.setAttribute("aria-describedby", ((input.getAttribute("aria-describedby") || "") + " " + error.id).trim());
    });
    window.aelmsRegisterForms(content);
    var focus = form.querySelector('[aria-invalid="true"]') || form.querySelector(slot ? '[name="day_of_week"]' : '[name="title"]');
    if (focus) focus.focus();
  }
  function installGroup(loaded) {
    var body = safeNode(loaded.root.querySelector("[data-planner-group-content]"));
    var actions = safeNode(loaded.root.querySelector("[data-planner-group-actions]"));
    release(); content.replaceChildren(actions, body); copyMessages(loaded.root);
    title.textContent = loaded.root.querySelector("h1").textContent;
    window.aelmsRegisterForms(content);
  }
  function displayGroup(loaded) {
    mode = "group"; targetURL = groupURL(); dialog.dataset.mode = mode;
    slotParent = null; baseline = null; uncertain = false;
    installGroup(loaded); fallback(targetURL);
    back.textContent = t("Choose another group"); back.hidden = !fromChoices;
  }
  async function load() {
    if (posting || scopeInvalid || uncertain) return;
    cancelRead();
    var mine = revision, request = new AbortController(); controller = request;
    notice("Loading…"); recovery.hidden = true;
    try {
      var response = await fetch(targetURL.href, {credentials:"same-origin", cache:"no-store", signal:request.signal, headers:{Accept:"text/html"}});
      var loaded = await parse(response, false);
      if (mine !== revision || request.signal.aborted || !dialog.open || !active) return;
      if (loaded.kind === "form") installForm(loaded); else displayGroup(loaded);
      notice(""); recovery.hidden = false; retry.hidden = true;
    } catch (error) {
      if (mine === revision && !request.signal.aborted && dialog.open && active) {
        notice(scopeInvalid ? sessionMessage : "The page could not load. Retry or open the full page.", true);
        recovery.hidden = false; retry.hidden = false; retry.disabled = scopeInvalid;
      }
    } finally { if (controller === request) controller = null; }
  }
  function showChoices() {
    cancelRead(); release(); content.replaceChildren(choices.content.cloneNode(true)); messages.replaceChildren();
    mode = "choices"; dialog.dataset.mode = mode; title.textContent = t("Choose a group");
    notice(""); back.hidden = true; recovery.hidden = true;
    var search = content.querySelector("[data-planner-group-search]");
    search.addEventListener("input", function () {
      var needle = search.value.trim().toLocaleLowerCase(), count = 0;
      content.querySelectorAll(".fp-choice").forEach(function (link) {
        link.hidden = !link.querySelector("[data-planner-group-name]").textContent.toLocaleLowerCase().includes(needle);
        if (!link.hidden) count += 1;
      });
      content.querySelector("[data-planner-choice-empty]").hidden = count > 0;
    });
    search.focus();
  }
  async function permitClose() {
    if (posting || confirming) return false;
    var form = content.querySelector("form[data-planner-native-form],form[data-planner-slot-form]");
    if (window.aelmsFormHasChanges(content) || form && baseline !== null && baseline !== values(form)) {
      confirming = true;
      var accepted;
      try { accepted = await window.aelmsConfirm(t("Discard these unsaved details?")); }
      finally { confirming = false; }
      if (!accepted) return false;
    }
    return true;
  }
  async function closeDialog() {
    if (!await permitClose()) return;
    if (mode === "slot" && slotParent) {
      cancelRead(); release(); content.replaceChildren(); messages.replaceChildren();
      mode = "group"; dialog.dataset.mode = mode; targetURL = slotParent.url;
      fromChoices = slotParent.fromChoices; slotParent = null; baseline = null; uncertain = false;
      title.textContent = t("Teaching schedules"); fallback(targetURL);
      back.textContent = t("Choose another group"); back.hidden = !fromChoices;
      load(); return;
    }
    cancelRead(); release(); content.replaceChildren(); messages.replaceChildren(); dialog.close();
    baseline = null;
    if (completed || refreshOnClose) { location.reload(); return; }
    if (trigger && trigger.isConnected) trigger.focus();
  }
  root.addEventListener("click", function (event) {
    var link = event.target.closest("a[href]");
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target || link.hasAttribute("download")) return;
    if (dialog.open && link.matches("[data-planner-form-cancel]")) { event.preventDefault(); event.stopPropagation(); closeDialog(); return; }
    var choose = link.matches("[data-planner-choose-group]");
    var create = link.matches("[data-planner-new-event]");
    var group = link.matches("[data-planner-open-group]");
    var slot = link.matches("[data-planner-new-slot]");
    if (!choose && !create && !group && !slot || choose && !choices) return;
    var url = new URL(link.href, location.href), id = group || slot ? link.dataset.groupPublicId : "";
    if (url.origin !== location.origin || url.search || url.hash ||
        group && (!id || url.pathname !== groupPattern.pathname.replace("__group__", encodeURIComponent(id))) ||
        slot && (!id || url.pathname !== slotPattern.pathname.replace("__group__", encodeURIComponent(id))) ||
        create && url.pathname !== createURL.pathname) return;
    if (posting || completed || dialog.open && mode !== "choices" && !(slot && mode === "group" && id === groupID)) return;
    event.preventDefault(); event.stopPropagation();
    if (!dialog.open) {
      trigger = link; fromChoices = false; uncertain = false; baseline = null; slotParent = null;
      dialog.showModal();
    } else if (slot) slotParent = {url:targetURL, fromChoices:fromChoices};
    else fromChoices = mode === "choices";
    if (choose) { showChoices(); return; }
    cancelRead(); release(); content.replaceChildren(); messages.replaceChildren();
    mode = slot ? "slot" : group ? "group" : "create"; groupID = id || ""; targetURL = url;
    baseline = null;
    dialog.dataset.mode = mode; title.textContent = t(slot ? "New Schedule Slot" : group ? "Teaching schedules" : "New centre event");
    fallback(url); back.hidden = slot ? !slotParent : !fromChoices;
    back.textContent = t(slot ? "Back to group schedules" : "Choose another group");
    retry.hidden = false; retry.disabled = scopeInvalid;
    if (scopeInvalid) { notice(sessionMessage, true); recovery.hidden = false; }
    else load();
  });
  close.addEventListener("click", closeDialog);
  back.addEventListener("click", function () { if (mode === "slot") closeDialog(); else showChoices(); });
  retry.addEventListener("click", load);
  dialog.addEventListener("cancel", function (event) { event.preventDefault(); closeDialog(); });
  dialog.addEventListener("click", function (event) {
    if (event.target !== dialog) return;
    var rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDialog();
  });
  dialog.addEventListener("submit", async function (event) {
    var form = event.target;
    if (event.defaultPrevented || !(mode === "create" && form.matches("form[data-planner-native-form]") || mode === "slot" && form.matches("form[data-planner-slot-form]"))) return;
    event.preventDefault(); event.stopPropagation();
    if (posting || uncertain || scopeInvalid) return;
    var action = new URL(form.action, location.href);
    var postURL = mode === "slot" ? targetURL : createURL;
    var slotPost = mode === "slot";
    if (action.href !== postURL.href) return;
    var data = new FormData(form), submitter = event.submitter;
    if (submitter && submitter.name) data.append(submitter.name, submitter.value);
    posting = true; close.disabled = true; form.inert = true; form.setAttribute("aria-busy", "true");
    notice("Saving…"); recovery.hidden = true;
    try {
      var response = await fetch(postURL.href, {method:"POST", body:data, credentials:"same-origin", cache:"no-store", headers:{Accept:"text/html"}});
      var loaded = await parse(response, true);
      if (!active || !dialog.open) { uncertain = true; return; }
      if (loaded.kind === "form") { installForm(loaded); notice(""); }
      else if (loaded.kind === "group") {
        refreshOnClose = refreshOnClose || loaded.created === true;
        displayGroup(loaded); recovery.hidden = false; retry.hidden = true; notice("");
        var add = content.querySelector("[data-planner-new-slot]");
        if (add) add.focus(); else close.focus();
      } else {
        var summary = safeNode(loaded.root.querySelector("[data-planner-event-summary]"));
        release(); content.replaceChildren(summary); copyMessages(loaded.root);
        title.textContent = loaded.root.querySelector("h1").textContent;
        completed = true; baseline = null;
        fallback(loaded.url, "Open event details"); recovery.hidden = false; retry.hidden = true;
        notice("Close to refresh the calendar."); close.focus();
      }
    } catch (error) {
      uncertain = true;
      notice(scopeInvalid ? sessionMessage : slotPost ? slotUncertainMessage : uncertainMessage, true);
      fallback(slotPost ? groupURL() : new URL(location.href), slotPost ? "Open group schedules" : "Review calendar"); recovery.hidden = false; retry.hidden = true;
    } finally {
      posting = false; close.disabled = false; form.inert = false; form.removeAttribute("aria-busy");
      if (uncertain || scopeInvalid) content.querySelectorAll('[type="submit"]').forEach(function (button) { button.disabled = true; });
    }
  }, true);
  window.addEventListener("pagehide", function () { active = false; cancelRead(); if (posting) uncertain = true; });
  window.addEventListener("pageshow", function () {
    active = true;
    if (uncertain && dialog.open) {
      notice(mode === "slot" ? slotUncertainMessage : uncertainMessage, true); recovery.hidden = false; retry.hidden = true;
      content.querySelectorAll('[type="submit"]').forEach(function (button) { button.disabled = true; });
    }
  });
})();
