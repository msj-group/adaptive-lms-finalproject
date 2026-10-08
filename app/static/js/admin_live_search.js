/**
 * Generic admin live-search controller.
 *
 * Not specific to any resource (Students, Teachers, ...). Each page opts in
 * by marking one root element with `data-live-search` and providing the
 * pieces below as data attributes / child markers. This file is served as
 * a plain static asset (no Jinja processing), so nothing resource-specific
 * can be hard-coded here -- every endpoint, id, selector, and noun comes
 * from the page that includes it.
 *
 * Root element attributes:
 *   data-live-search              marks the root (required)
 *   data-list-url                 URL the live search fetches against
 *   data-singular                 singular noun for result counts (e.g. "student")
 *   data-plural                   plural noun for result counts (e.g. "students")
 *   data-status-form-selector     CSS selector matching a status-toggle form
 *                                 with a `data-confirm` attribute, used for
 *                                 the delegated Suspend/Reactivate confirmation
 *
 * Child elements, located via querySelector within the root:
 *   [data-live-search-form]            the filter <form> (search + status)
 *   [data-live-search-input]           the search <input type="search">
 *   [data-live-search-status]          the status <select>
 *   [data-live-search-results]         container whose innerHTML gets replaced
 *   [data-live-search-status-region]   role="status" aria-live="polite" region
 *
 * The Suspend/Reactivate confirmation listener is installed independently
 * of live-search support: a browser missing fetch/AbortController/History
 * APIs still gets a working confirm() prompt on the plain GET-form page,
 * it just does not get the debounced live search itself.
 */
(function () {
  "use strict";
  var t = window.aelmsUI ? window.aelmsUI.t : function (text) { return text; };

  function installStatusConfirmation(root) {
    var resultsContainer = root.querySelector("[data-live-search-results]");
    var statusFormSelector = root.dataset.statusFormSelector;
    if (!resultsContainer || !statusFormSelector) {
      return;
    }
    // Delegated so it keeps working for status-toggle forms injected by
    // every live update, without needing to re-attach listeners each time.
    resultsContainer.addEventListener("submit", function (event) {
      var statusForm = event.target.closest(statusFormSelector);
      if (statusForm && statusForm.hasAttribute("data-confirm") && !window.aelmsConfirm && !window.confirm(statusForm.getAttribute("data-confirm"))) {
        event.preventDefault();
      }
    });
  }

  function hasLiveSearchSupport() {
    return Boolean(window.fetch && window.AbortController && window.history && window.history.replaceState);
  }

  function readLiveSearchConfig(root) {
    var singular = root.dataset.singular;
    var plural = root.dataset.plural;
    if (!singular || !plural) {
      // Without both nouns the accessible result message could only ever
      // read "Showing 3 undefined." -- treat this as no configuration at
      // all rather than rendering that.
      return null;
    }
    return {
      form: root.querySelector("[data-live-search-form]"),
      searchInput: root.querySelector("[data-live-search-input]"),
      statusSelect: root.querySelector("[data-live-search-status]"),
      resultsContainer: root.querySelector("[data-live-search-results]"),
      statusRegion: root.querySelector("[data-live-search-status-region]"),
      listUrl: root.dataset.listUrl,
      singular: singular,
      plural: plural,
    };
  }

  function initLiveSearch(config) {
    var form = config.form;
    var searchInput = config.searchInput;
    var statusSelect = config.statusSelect;
    var resultsContainer = config.resultsContainer;
    var statusRegion = config.statusRegion;
    var listUrl = config.listUrl;
    var singular = config.singular;
    var plural = config.plural;

    if (!form || !searchInput || !statusSelect || !resultsContainer || !statusRegion || !listUrl) {
      return; // misconfigured root -- fail closed, do nothing
    }

    var debounceTimer = null;
    var activeController = null;
    var requestSequence = 0;

    function describeResults() {
      var count = resultsContainer.querySelectorAll("tbody tr").length;
      if (count === 0) {
        return t("No %(noun)s found.", {noun:t(plural)});
      }
      return t("Showing %(count)s %(noun)s.", {count:count,noun:t(count === 1 ? singular : plural)});
    }

    function buildUrl() {
      var params = new URLSearchParams();
      if (searchInput.value) {
        params.set("q", searchInput.value);
      }
      if (statusSelect.value) {
        params.set("status", statusSelect.value);
      }
      var queryString = params.toString();
      return listUrl + (queryString ? "?" + queryString : "");
    }

    function performSearch() {
      var url = buildUrl();
      var thisRequest = ++requestSequence;

      if (activeController) {
        activeController.abort();
      }
      var controller = new AbortController();
      activeController = controller;

      statusRegion.textContent = t("Searching...");

      fetch(url, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        signal: controller.signal,
      })
        .then(function (response) {
          if (response.redirected) {
            // Session no longer valid (e.g. signed out) -- follow it like a
            // normal navigation would instead of injecting login markup.
            window.location.href = response.url;
            return null;
          }
          if (!response.ok) {
            throw new Error("Request failed with status " + response.status);
          }
          return response.text();
        })
        .then(function (html) {
          if (html === null) {
            return;
          }
          if (thisRequest !== requestSequence) {
            return; // a newer request has already started; this one is stale
          }
          resultsContainer.innerHTML = html;
          history.replaceState(null, "", url);
          statusRegion.textContent = describeResults();
        })
        .catch(function (error) {
          if (error.name === "AbortError") {
            return; // superseded by a newer keystroke/status change
          }
          if (thisRequest !== requestSequence) {
            return; // an obsolete request's error must not touch the UI
          }
          statusRegion.textContent = t("Something went wrong loading results. Please try again.");
        })
        .finally(function () {
          if (activeController === controller) {
            activeController = null;
          }
        });
    }

    searchInput.addEventListener("input", function () {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(performSearch, 350);
    });

    statusSelect.addEventListener("change", function () {
      clearTimeout(debounceTimer);
      performSearch();
    });

    form.addEventListener("submit", function (event) {
      // No visible submit control exists once JS is active (the Filter
      // button only exists inside <noscript>), but native implicit
      // submission (e.g. pressing Enter in the search field) can still
      // fire this event. Route it through the same live-search path
      // instead of letting it fall through to a full page reload.
      event.preventDefault();
      clearTimeout(debounceTimer);
      performSearch();
    });

    window.addEventListener("popstate", function () {
      var params = new URLSearchParams(window.location.search);
      searchInput.value = params.get("q") || "";
      statusSelect.value = params.get("status") || "";
      performSearch();
    });
  }

  document.querySelectorAll("[data-live-search]").forEach(function (root) {
    // Status-confirmation must work regardless of live-search support, so
    // it is installed first and unconditionally.
    installStatusConfirmation(root);

    if (!hasLiveSearchSupport()) {
      // Live search itself is unavailable in this browser; the plain GET
      // form (with its <noscript> Filter fallback) keeps working on its
      // own, and the confirmation listener installed above still applies.
      return;
    }

    var config = readLiveSearchConfig(root);
    if (!config) {
      return;
    }
    initLiveSearch(config);
  });
})();
