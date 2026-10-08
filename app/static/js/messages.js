/* Presentation enhancements only. Private content is never stored or fetched here.
   Native POST, CSRF, nonce protection and unsaved-work handling remain authoritative. */
(function () {
  "use strict";
  var t = window.aelmsUI ? window.aelmsUI.t : function (text) { return text; };
  var emojiCatalog;

  document.querySelectorAll("[data-conversation-panel]").forEach(function (panel) {
    var search = panel.querySelector("[data-conversation-search]");
    var input = panel.querySelector("[data-conversation-query]");
    var status = panel.querySelector("[data-conversation-status]");
    var tabs = Array.from(panel.querySelectorAll("[data-chat-tab]"));
    var current = panel.dataset.initialTab || "chats";
    if (!search || !input || !status) return;
    search.hidden = false;
    panel.querySelector("[data-chat-tabs]").hidden = false;
    function filter() {
      if (panel.hasAttribute("data-contacts-search-only") && current === "chats") {
        panel.querySelectorAll("[data-conversation-row]").forEach(function (row) { row.hidden = false; });
        status.hidden = true;
        return;
      }
      var query = input.value.trim().toLocaleLowerCase();
      var rows = Array.from(panel.querySelectorAll(current === "chats" ? "[data-conversation-row]" : "[data-contact-row]"));
      var visible = 0;
      rows.forEach(function (row) {
        row.hidden = !row.textContent.toLocaleLowerCase().includes(query);
        if (!row.hidden) visible += 1;
      });
      status.hidden = !query;
      status.textContent = visible ? t(visible === 1 ? "%(count)s match in the loaded list." : "%(count)s matches in the loaded list.", {count:visible}) : t("No matches in the loaded list. Use Browse or search to find other contacts.");
    }
    function activate(name) {
      current = name;
      search.hidden = panel.hasAttribute("data-contacts-search-only") && name === "chats";
      panel.querySelector("[data-contact-search-submit]").hidden = name !== "contacts";
      tabs.forEach(function (tab) { tab.setAttribute("aria-selected", String(tab.dataset.chatTab === name)); tab.tabIndex = tab.dataset.chatTab === name ? 0 : -1; });
      panel.querySelectorAll("[data-chat-panel]").forEach(function (section) { section.hidden = section.dataset.chatPanel !== name; section.setAttribute("role", "tabpanel"); });
      filter();
    }
    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () { activate(tab.dataset.chatTab); });
      tab.addEventListener("keydown", function (event) {
        var next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : null;
        if (next !== null) { event.preventDefault(); activate(tabs[next].dataset.chatTab); tabs[next].focus(); }
      });
    });
    input.addEventListener("input", filter);
    input.addEventListener("keydown", function (event) { if (event.key === "Enter" && current === "chats" && !event.isComposing) { event.preventDefault(); filter(); } });
    search.addEventListener("submit", function (event) { if (current === "chats") { event.preventDefault(); filter(); } });
    activate(current);
  });

  var timeline = document.querySelector("[data-message-timeline]");
  var bottomButton = document.querySelector("[data-scroll-bottom]");
  var followTimeline = false;
  function nearBottom() {
    return !timeline || timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 100;
  }
  function scrollToBottom() {
    if (timeline) timeline.scrollTop = timeline.scrollHeight;
  }
  function updateBottomButton() {
    if (bottomButton && timeline) bottomButton.hidden = nearBottom();
  }
  if (timeline) {
    // Respect message deep links and older-page reading positions.
    if (location.hash) {
      var linkedMessage = null;
      try { linkedMessage = document.getElementById(decodeURIComponent(location.hash.slice(1))); } catch (_) { /* Leave malformed fragments to the browser. */ }
      if (linkedMessage && timeline.contains(linkedMessage)) linkedMessage.scrollIntoView({block:"center"});
    } else if (timeline.dataset.latestPage === "true") {
      scrollToBottom();
    }
    followTimeline = nearBottom();
    timeline.addEventListener("scroll", function () {
      followTimeline = nearBottom();
      updateBottomButton();
    }, {passive:true});
    if (bottomButton) bottomButton.addEventListener("click", function () {
      scrollToBottom();
      timeline.focus({preventScroll:true});
      updateBottomButton();
    });
    updateBottomButton();
  }

  document.querySelectorAll("[data-message-form]").forEach(function (form) {
    var input = form.querySelector("[data-message-input]");
    var count = form.querySelector("[data-message-count]");
    var shortcut = form.querySelector("[data-message-shortcut]");
    var submit = form.querySelector("button[type='submit']");
    if (!input) return;
    var minimum = parseFloat(getComputedStyle(input).minHeight) || 48;
    var maximum = parseFloat(getComputedStyle(input).maxHeight) || 176;
    function updateInput() {
      var follow = nearBottom();
      input.style.height = "auto";
      input.style.height = Math.min(maximum, Math.max(minimum, input.scrollHeight + 2)) + "px";
      if (count) count.textContent = input.value.length + " / " + input.maxLength;
      if (follow) scrollToBottom();
      updateBottomButton();
    }
    input.addEventListener("input", updateInput);
    window.addEventListener("resize", function () {
      var follow = followTimeline;
      updateInput();
      if (follow) scrollToBottom();
    });
    input.addEventListener("keydown", function (event) {
      // Enter sends; Shift+Enter inserts a newline. IME composition never sends.
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229 && !event.repeat) {
        event.preventDefault();
        if (input.value.trim() && !form.hasAttribute("aria-busy")) form.requestSubmit(submit);
      }
    });
    if (shortcut) shortcut.hidden = false;
    updateInput();
    var invalid = form.querySelector("[aria-invalid='true']");
    if (invalid) invalid.focus();
    setupEmoji(form, input);
  });


  function catalog(url) {
    if (!emojiCatalog) emojiCatalog = fetch(url, {credentials:"omit", cache:"force-cache"}).then(function (response) {
      if (!response.ok) throw new Error(t("Emoji unavailable"));
      return response.json();
    }).then(function (data) { return data.groups.flatMap(function (group) { return group.emoji.map(function (emoji) { return {glyph:emoji[0], name:emoji[1], search:(emoji[1] + " " + emoji[2] + " " + group.name + " " + t(group.name)).toLocaleLowerCase(), group:group.name}; }); }); }).catch(function (error) { emojiCatalog = null; throw error; });
    return emojiCatalog;
  }
  function setupEmoji(form, input) {
    var toggle = form.querySelector("[data-emoji-toggle]");
    var panel = form.querySelector("[data-emoji-panel]");
    if (!toggle || !panel) return;
    var search = panel.querySelector("[data-emoji-search]");
    var category = panel.querySelector("[data-emoji-category]");
    var tone = panel.querySelector("[data-emoji-tone]");
    var grid = panel.querySelector("[data-emoji-grid]");
    var status = panel.querySelector("[data-emoji-status]");
    var more = panel.querySelector("[data-emoji-more]");
    var data, visible = [], limit = 80, caretStart = input.selectionStart, caretEnd = input.selectionEnd;
    function rememberCaret() { caretStart = input.selectionStart; caretEnd = input.selectionEnd; }
    ["select", "input", "keyup", "click", "blur"].forEach(function (event) { input.addEventListener(event, rememberCaret); });
    function close(restore) { panel.hidden = true; toggle.setAttribute("aria-expanded", "false"); if (restore) toggle.focus(); }
    function render() {
      var query = search.value.trim().toLocaleLowerCase();
      visible = data.filter(function (emoji) {
        if (category.value && emoji.group !== category.value) return false;
        var tones = Array.from(emoji.glyph).filter(function (char) { return char.codePointAt(0) >= 0x1f3fb && char.codePointAt(0) <= 0x1f3ff; });
        if (tone.value === "base" && tones.length || tone.value !== "all" && tone.value !== "base" && !tones.some(function (char) { return char.codePointAt(0) === 0x1f3fa + Number(tone.value); })) return false;
        return !query || emoji.search.includes(query) || emoji.glyph.includes(query);
      });
      var fragment = document.createDocumentFragment();
      visible.slice(0, limit).forEach(function (emoji) {
        var button = document.createElement("button"); button.type = "button"; button.textContent = emoji.glyph; button.title = emoji.name; button.setAttribute("aria-label", emoji.name);
        button.addEventListener("click", function () {
          if (input.value.length - (caretEnd - caretStart) + emoji.glyph.length > input.maxLength) { status.textContent = t("Your message is at the character limit."); return; }
          input.setRangeText(emoji.glyph, caretStart, caretEnd, "end"); rememberCaret(); input.dispatchEvent(new Event("input", {bubbles:true})); input.focus({preventScroll:true});
        }); fragment.appendChild(button);
      });
      grid.replaceChildren(fragment);
      more.hidden = visible.length <= limit;
      status.textContent = visible.length ? t("%(shown)s of %(total)s emoji", {shown:Math.min(limit,visible.length),total:visible.length}) : t("No matching emoji.");
    }
    toggle.hidden = false;
    toggle.addEventListener("click", async function () {
      if (!panel.hidden) { close(false); return; }
      panel.hidden = false; toggle.setAttribute("aria-expanded", "true"); search.focus();
      if (!data) {
        status.textContent = t("Loading emoji…");
        try {
          data = await catalog(panel.dataset.emojiUrl);
          Array.from(new Set(data.map(function (emoji) { return emoji.group; }))).forEach(function (name) { var option = document.createElement("option"); option.value = name; option.textContent = t(name); category.appendChild(option); });
        } catch (_) { status.textContent = t("Emoji could not be loaded. Close the picker and try again."); return; }
      }
      render();
    });
    panel.querySelector("[data-emoji-close]").addEventListener("click", function () { close(true); });
    [search, category, tone].forEach(function (field) { field.addEventListener(field === search ? "input" : "change", function () { if (data) { limit = 80; grid.scrollTop = 0; render(); } }); });
    more.addEventListener("click", function () { var top = grid.scrollTop; limit += 80; render(); grid.scrollTop = top; });
    document.addEventListener("pointerdown", function (event) { if (!panel.hidden && !panel.contains(event.target) && !toggle.contains(event.target)) close(false); });
    document.addEventListener("keydown", function (event) { if (event.key === "Escape" && !panel.hidden) { event.preventDefault(); close(true); } });
    form.addEventListener("submit", function () { close(false); });
  }

  var threadQuery = document.querySelector("[data-thread-query]");
  var searchToggle = document.querySelector("[data-thread-search-toggle]");
  if (threadQuery && searchToggle && timeline) {
    var searchPanel = document.querySelector("[data-thread-search]");
    var threadStatus = document.querySelector("[data-thread-search-status]");
    function filterMessages() {
      var query = threadQuery.value.trim().toLocaleLowerCase(), count = 0, day;
      Array.from(timeline.children).forEach(function (child) {
        if (child.matches(".messenger-date")) { day = child; day.hidden = !!query; }
        else if (child.matches(".messenger-bubble")) {
          var body = child.querySelector(".messenger-bubble__body, textarea");
          child.hidden = !!query && !(body ? body.value || body.textContent : "").toLocaleLowerCase().includes(query);
          if (!child.hidden) { count++; if (day) day.hidden = false; }
        }
      });
      threadStatus.textContent = query ? t("%(count)s matching messages on this page", {count:count}) : t("Search the loaded page");
      updateBottomButton();
    }
    searchToggle.hidden = false;
    searchToggle.addEventListener("click", function () { searchPanel.hidden = !searchPanel.hidden; searchToggle.setAttribute("aria-expanded", String(!searchPanel.hidden)); if (!searchPanel.hidden) threadQuery.focus(); else { threadQuery.value = ""; filterMessages(); } });
    threadQuery.addEventListener("input", filterMessages);
  }
  var infoToggle = document.querySelector("[data-thread-info-toggle]");
  if (infoToggle) { infoToggle.hidden = false; infoToggle.addEventListener("click", function () { var info = document.querySelector("[data-thread-info]"); info.hidden = !info.hidden; infoToggle.setAttribute("aria-expanded", String(!info.hidden)); }); }
  // Move the existing action nodes into a native modal on small screens.
  // Never clone forms, tokens or controls; this is the same authorized POST.
  var actionSheet = document.createElement("dialog");
  actionSheet.className = "messenger-action-sheet";
  actionSheet.setAttribute("aria-labelledby", "message-actions-title");
  actionSheet.innerHTML = '<header><h2 id="message-actions-title"></h2><button type="button" class="messenger-icon-button" data-message-sheet-close></button></header>';
  var sheetClose = actionSheet.querySelector("[data-message-sheet-close]");
  sheetClose.textContent = "×";
  sheetClose.setAttribute("aria-label", t("Close actions"));
  document.body.appendChild(actionSheet);
  var sheetOwner = null;
  var sheetMenu = null;
  var sheetFocus = null;
  function closeActions() { if (actionSheet.open) actionSheet.close(); }
  sheetClose.addEventListener("click", closeActions);
  actionSheet.addEventListener("close", function () {
    if (sheetOwner && sheetMenu) { sheetOwner.appendChild(sheetMenu); sheetOwner.open = false; }
    var returnTo = sheetFocus;
    sheetOwner = sheetMenu = sheetFocus = null;
    if (returnTo && returnTo.isConnected) returnTo.focus({preventScroll:true});
  });
  actionSheet.addEventListener("click", function (event) {
    if (event.target !== actionSheet) return;
    var bounds = actionSheet.getBoundingClientRect();
    if (event.clientY < bounds.top || event.clientX < bounds.left || event.clientX > bounds.right) closeActions();
  });
  var mobileActions = window.matchMedia("(max-width:760px)");
  mobileActions.addEventListener("change", closeActions);
  document.querySelectorAll(".messenger-message-actions, .messenger-header-actions").forEach(function (details) {
    details.addEventListener("toggle", function () {
      if (!details.open) return;
      document.querySelectorAll(".messenger-message-actions[open], .messenger-header-actions[open]").forEach(function (other) { if (other !== details) other.open = false; });
      if (mobileActions.matches && typeof actionSheet.showModal === "function") {
        if (actionSheet.open) return;
        sheetOwner = details; sheetMenu = details.querySelector(".messenger-action-menu"); sheetFocus = details.querySelector("summary");
        actionSheet.querySelector("h2").textContent = t(details.matches(".messenger-message-actions") ? "Message actions" : "Conversation actions");
        actionSheet.appendChild(sheetMenu); actionSheet.showModal(); sheetClose.focus();
        return;
      }
      if (timeline && details.matches(".messenger-message-actions")) { var menu = details.querySelector(".messenger-action-menu"); details.toggleAttribute("data-menu-below", details.getBoundingClientRect().top - menu.offsetHeight < timeline.getBoundingClientRect().top + 8); }
    });
    details.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && details.open && !actionSheet.open) { event.preventDefault(); details.open = false; details.querySelector("summary").focus(); }
    });
  });
  document.addEventListener("click", function (event) { if (actionSheet.open) return; document.querySelectorAll(".messenger-message-actions[open], .messenger-header-actions[open]").forEach(function (details) { if (!details.contains(event.target)) details.open = false; }); });
})();
