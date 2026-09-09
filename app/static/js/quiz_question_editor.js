/* Teacher question editor -- option rows and answer-mode help text.
 * Phase 4 / M04B. No dependency, no build step, no framework.
 *
 * WHAT THIS DOES
 *   - adds, removes and reorders answer-option rows in the page;
 *   - keeps each row's hidden `option_key` and its `option_correct`
 *     checkbox value identical, so the answer key stays attached to the
 *     right row however the rows are rearranged;
 *   - enables/disables Add and Remove at the 2..8 bounds and shows a live
 *     count;
 *   - swaps ONLY the explanatory help text when the answer mode changes.
 *
 * WHAT THIS DELIBERATELY NEVER DOES
 *   - it never checks or unchecks an option, in either direction, for any
 *     reason. Switching from several correct answers to single-answer mode
 *     does not clear anything here: the server rejects the save and the
 *     teacher chooses which one is right. Switching the other way does not
 *     select anything either. Silently changing an authored answer key is
 *     exactly the data loss this milestone forbids.
 *   - it never decides whether a save is valid. Every rule -- the row
 *     count, empty or duplicated wording, the length limit, the answer
 *     cardinality, and which options actually belong to this question --
 *     is enforced again on the server against locked rows. With
 *     JavaScript disabled, blocked or tampered with, the form still
 *     submits and the server still decides.
 */
(function () {
  "use strict";

  var form = document.querySelector("[data-question-editor]");
  if (!form) {
    return;
  }

  var list = form.querySelector("[data-option-list]");
  var template = form.querySelector("[data-option-template]");
  var addButton = form.querySelector("[data-option-add]");
  var counter = form.querySelector("[data-option-count]");
  if (!list || !template || !addButton) {
    return;
  }

  var minOptions = parseInt(form.getAttribute("data-min-options"), 10);
  var maxOptions = parseInt(form.getAttribute("data-max-options"), 10);
  if (!minOptions || !maxOptions) {
    return;
  }

  var NEW_KEY = /^new:(\d+)$/;

  function rows() {
    return Array.prototype.slice.call(list.querySelectorAll("[data-option-row]"));
  }

  /* A fresh row key that cannot collide with one already on the page --
   * including the `new:N` keys the server rendered for a brand-new
   * question, and the ones an earlier rejected submission echoed back. */
  function nextKey() {
    var highest = -1;
    rows().forEach(function (row) {
      var field = row.querySelector("[data-option-key]");
      if (!field) {
        return;
      }
      var match = NEW_KEY.exec(field.value);
      if (match) {
        highest = Math.max(highest, parseInt(match[1], 10));
      }
    });
    return "new:" + (highest + 1);
  }

  function refresh() {
    var current = rows();
    current.forEach(function (row, index) {
      var remove = row.querySelector("[data-option-remove]");
      var up = row.querySelector("[data-option-move-up]");
      var down = row.querySelector("[data-option-move-down]");
      if (remove) {
        remove.disabled = current.length <= minOptions;
      }
      if (up) {
        up.disabled = index === 0;
      }
      if (down) {
        down.disabled = index === current.length - 1;
      }
    });
    addButton.disabled = current.length >= maxOptions;
    if (counter) {
      counter.textContent =
        current.length + " of " + minOptions + "–" + maxOptions + " options";
    }
  }

  function addRow() {
    if (rows().length >= maxOptions) {
      return;
    }
    var fragment = template.content.cloneNode(true);
    var row = fragment.querySelector("[data-option-row]");
    var key = nextKey();
    var keyField = row.querySelector("[data-option-key]");
    var correct = row.querySelector("[data-option-correct]");
    /* The hidden key and the checkbox value must always be the same
     * string: that pairing is what tells the server which row a tick
     * belongs to. The checkbox is added unchecked and is never ticked
     * here. */
    if (keyField) {
      keyField.value = key;
    }
    if (correct) {
      correct.value = key;
      correct.checked = false;
    }
    list.appendChild(fragment);
    refresh();
    var text = list.lastElementChild.querySelector('input[name="option_text"]');
    if (text) {
      text.focus();
    }
  }

  function removeRow(row) {
    if (rows().length <= minOptions) {
      return;
    }
    row.remove();
    refresh();
  }

  /* Reordering is a DOM move only. The request body's row order IS the
   * order of the `option_key` fields, so nothing has to be renumbered and
   * no checkbox is touched. */
  function moveRow(row, delta) {
    var current = rows();
    var index = current.indexOf(row);
    var target = index + delta;
    if (index < 0 || target < 0 || target >= current.length) {
      return;
    }
    if (delta < 0) {
      list.insertBefore(row, current[target]);
    } else {
      list.insertBefore(current[target], row);
    }
    refresh();
  }

  list.addEventListener("click", function (event) {
    var button = event.target.closest("button");
    if (!button || !list.contains(button)) {
      return;
    }
    var row = button.closest("[data-option-row]");
    if (!row) {
      return;
    }
    if (button.hasAttribute("data-option-remove")) {
      removeRow(row);
    } else if (button.hasAttribute("data-option-move-up")) {
      moveRow(row, -1);
    } else if (button.hasAttribute("data-option-move-down")) {
      moveRow(row, 1);
    }
  });

  addButton.addEventListener("click", addRow);

  /* Answer mode: help text only. */
  var helps = Array.prototype.slice.call(form.querySelectorAll("[data-mode-help]"));
  var modeInputs = Array.prototype.slice.call(
    form.querySelectorAll('input[name="answer_mode"]')
  );

  function showHelpFor(mode) {
    helps.forEach(function (help) {
      help.hidden = help.getAttribute("data-mode-help") !== mode;
    });
  }

  modeInputs.forEach(function (input) {
    input.addEventListener("change", function () {
      if (input.checked) {
        showHelpFor(input.value);
      }
    });
  });

  var selected = modeInputs.filter(function (input) {
    return input.checked;
  })[0];
  if (selected) {
    showHelpFor(selected.value);
  }

  refresh();
})();
