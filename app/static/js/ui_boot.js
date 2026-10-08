/* Run before styles: restore only public display choices, never learning data. */
(function () {
  "use strict";
  var root = document.documentElement;
  var match = document.cookie.match(/(?:^|;\s*)aelms\.theme=(system|light|dark)(?:;|$)/);
  var preference = match ? match[1] : "system";
  var dark = window.matchMedia("(prefers-color-scheme: dark)");
  var labelAliases;
  var labelCatalog;
  function apply() {
    root.dataset.themePreference = preference;
    root.dataset.theme = preference === "system" ? (dark.matches ? "dark" : "light") : preference;
  }
  apply();
  dark.addEventListener("change", function () { if (preference === "system") apply(); });
  try {
    var settings = JSON.parse(localStorage.getItem("aelms.display.v1") || "{}");
    root.dataset.uiMotion = settings && settings.motion === "reduce" ? "reduce" : "system";
    root.dataset.uiDensity = settings && settings.density === "compact" ? "compact" : "comfortable";
  } catch (_) { /* Native preferences and system defaults remain usable. */ }
  function text(message, values) {
    if (typeof message !== "string") return message;
    var normalized = message.replace(/\s+/g, " ").trim();
    var translated = message;
    var catalog = root.lang === "ar" ? window.AELMS_AR || {} : {};
    if (Object.prototype.hasOwnProperty.call(catalog, normalized)) translated = catalog[normalized];
    else if (root.lang === "ar") {
      if (!labelAliases || labelCatalog !== catalog) {
        labelCatalog = catalog;
        labelAliases = Object.create(null);
        Object.keys(catalog).forEach(function (key) { labelAliases[key.toLocaleLowerCase().replace(/_/g, " ")] = catalog[key]; });
      }
      translated = labelAliases[normalized.toLocaleLowerCase().replace(/_/g, " ")] || message;
    }
    if (values) translated = translated.replace(/%\((\w+)\)[sd]/g, function (_, key) { return values[key] === undefined ? "" : String(values[key]); }).replace(/%%/g, "%");
    return translated;
  }
  window.aelmsUI = { t: text, theme: function (value) {
    if (!["system", "light", "dark"].includes(value)) return;
    preference = value;
    document.cookie = "aelms.theme=" + value + "; Path=/; Max-Age=31536000; SameSite=Lax" + (location.protocol === "https:" ? "; Secure" : "");
    apply();
  } };
})();
