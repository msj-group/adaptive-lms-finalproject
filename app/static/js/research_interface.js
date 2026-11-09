/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
/* Presentation helpers only. Native forms and the server own every mutation. */
(function () {
  'use strict';
  if (!document.body.classList.contains('rs-suite')) return;
  var live = document.querySelector('[data-research-live]');
  if (live) {
    var pending = false, stopped = false, timer = 0, failures = 0;
    var peopleSignature = '', eventSignature = '';
    var status = live.querySelector('[data-live-status]');
    var people = live.querySelector('[data-live-students]');
    var events = live.querySelector('[data-live-events]');
    var empty = function (container, message) {
      var node = document.createElement('p');
      node.className = 'rs-live-empty'; node.textContent = message;
      container.replaceChildren(node);
    };
    var render = function (data) {
      live.querySelector('[data-live-count]').textContent = data.active_count;
      var limit = live.querySelector('[data-live-limit]');
      limit.hidden = !data.capped; limit.textContent = live.dataset.limited;
      var nextPeople = JSON.stringify(data.students.map(function (row) {
        return [row.code, row.groups, row.context, row.detail_available];
      }));
      if (nextPeople !== peopleSignature) {
        peopleSignature = nextPeople;
        var cards = data.students.map(function (row) {
          var card = live.querySelector('[data-live-student]').content.firstElementChild.cloneNode(true);
          card.querySelector('[data-code]').textContent = row.code;
          card.querySelector('[data-groups]').textContent = row.groups.join(' · ');
          card.querySelector('[data-context]').textContent = row.context;
          var note = card.querySelector('[data-protected-note]');
          note.hidden = row.detail_available;
          note.textContent = live.dataset.protected;
          return card;
        });
        people.replaceChildren.apply(people, cards);
        if (!cards.length) empty(people, data.collecting ? live.dataset.empty : live.dataset.paused);
      }
      var nextEvents = JSON.stringify(data.activities);
      if (nextEvents !== eventSignature) {
        eventSignature = nextEvents;
        var items = data.activities.map(function (row) {
          var item = live.querySelector('[data-live-event]').content.firstElementChild.cloneNode(true);
          item.querySelector('[data-code]').textContent = row.code;
          item.querySelector('[data-action]').textContent = row.action + (row.detail ? ' · ' + row.detail : '');
          item.querySelector('[data-context]').textContent = row.context;
          var time = item.querySelector('time'), date = new Date(row.at_ms);
          time.dateTime = date.toISOString();
          time.textContent = date.toLocaleTimeString(document.documentElement.lang || undefined,
            {hour: '2-digit', minute: '2-digit', second: '2-digit'});
          return item;
        });
        events.replaceChildren.apply(events, items);
        if (!items.length) empty(events, live.dataset.noEvents);
      }
    };
    var poll = async function () {
      if (pending || stopped || pageScope.signal.aborted || document.hidden) return;
      pageScope.clearTimeout(timer);
      pending = true;
      var timeout = new AbortController();
      var deadline = pageScope.setTimeout(function () { timeout.abort(); }, 10000);
      try {
        var response = await pageScope.fetch(live.dataset.url, {credentials: 'same-origin', cache: 'no-store',
          headers: {'Accept': 'application/json'}, signal: timeout.signal});
        if (response.status === 401 || response.status === 403 || response.redirected) {
          stopped = true; throw new Error('authentication');
        }
        if (!response.ok || !(response.headers.get('content-type') || '').includes('application/json')) throw new Error('network');
        var data = await response.json();
        if (!Array.isArray(data.students) || !Array.isArray(data.activities)) throw new Error('payload');
        render(data); failures = 0;
        status.textContent = data.collecting ? live.dataset.online : live.dataset.paused;
        status.dataset.state = data.collecting ? 'online' : 'paused';
      } catch (error) {
        if (pageScope.signal.aborted) return;
        failures += 1; status.dataset.state = 'offline';
        status.textContent = stopped ? live.dataset.expired : live.dataset.offline;
      } finally {
        pageScope.clearTimeout(deadline); pending = false;
        if (!stopped && !pageScope.signal.aborted && !document.hidden)
          timer = pageScope.setTimeout(poll, Math.min(30000, 3000 * Math.max(1, failures)));
      }
    };
    pageScope.listen(document, 'visibilitychange', function () {
      pageScope.clearTimeout(timer);
      if (!document.hidden) poll();
    });
    poll();
  }
  var searchTrigger = document.querySelector('[data-workspace-search-open]');
  var searchDialog = document.querySelector('[data-workspace-search]');
  if (searchTrigger && searchDialog && typeof searchDialog.showModal === 'function') {
    pageScope.listen(document, 'keydown', function (event) {
      if (event.defaultPrevented || !(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || event.key.toLowerCase() !== 'k') return;
      if (document.querySelector('dialog[open]')) return;
      event.preventDefault();
      searchTrigger.click();
    });
  }
  var filters = document.querySelector('[data-rs-filters]');
  if (filters) {
    var compact = window.matchMedia('(max-width:599px)');
    var syncFilters = function () { filters.open = filters.dataset.active === 'true'; };
    pageScope.listen(compact, 'change', syncFilters);
    syncFilters();
  }
  document.querySelectorAll('[data-rs-dialog]').forEach(function (trigger) {
    var dialog = document.getElementById(trigger.dataset.rsDialog);
    if (!dialog || typeof dialog.showModal !== 'function') return;
    trigger.hidden = false;
    pageScope.listen(trigger, 'click', function () {
      if (dialog.open) return;
      dialog.showModal();
      pageScope.listen(dialog, 'close', function restore() {
        if (trigger.isConnected) trigger.focus({preventScroll: true});
        dialog.removeEventListener('close', restore);
      });
    });
  });
  document.querySelectorAll('dialog.rs-dialog').forEach(function (dialog) {
    dialog.querySelectorAll('[data-rs-close]').forEach(function (button) {
      pageScope.listen(button, 'click', function () { dialog.close(); });
    });
    pageScope.listen(dialog, 'click', function (event) {
      if (event.target !== dialog) return;
      var bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    });
  });
  document.querySelectorAll('[data-collection-control], [data-research-export]').forEach(function (form) {
    pageScope.listen(form, 'submit', function () {
      form.setAttribute('aria-busy', 'true');
      // The signed intended state on the server makes replay non-toggling.
      form.querySelectorAll('button[type=submit], input[type=submit]').forEach(function (button) {
        button.disabled = true;
      });
    });
  });
})();

});
