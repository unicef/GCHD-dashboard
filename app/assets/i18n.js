// Language switcher (the translate icon beside the app title).
//
// Picking a language stores it in a cookie and reloads: the server renders the
// layout and every callback in the cookie's language, so nothing else needs to
// know a switch happened. Event delegation, because Dash renders the menu
// after this script loads.
(function () {
  var YEAR = 60 * 60 * 24 * 365;

  function setCookie(name, value, maxAge) {
    document.cookie = name + "=" + encodeURIComponent(value) +
      "; path=/; max-age=" + maxAge + "; SameSite=Lax";
  }

  function closeMenus(except) {
    document.querySelectorAll(".lang-switch.open").forEach(function (el) {
      if (el !== except) { el.classList.remove("open"); }
    });
  }

  document.addEventListener("click", function (e) {
    var opt = e.target.closest && e.target.closest(".lang-opt");
    if (opt) {
      var code = opt.getAttribute("data-lang");
      setCookie(opt.getAttribute("data-cookie"), code, YEAR);
      // Don't make the user re-acknowledge a data-use notice they already
      // dismissed on this page just because they changed language.
      var gate = document.getElementById("embargo-gate");
      if (gate && getComputedStyle(gate).display === "none") {
        setCookie(opt.getAttribute("data-skip-cookie"), "1", 30);
      }
      window.location.reload();
      return;
    }
    var btn = e.target.closest && e.target.closest(".lang-btn");
    if (btn) {
      var sw = btn.closest(".lang-switch");
      closeMenus(sw);
      sw.classList.toggle("open");
      return;
    }
    closeMenus(null);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { closeMenus(null); }
  });

  // Mirror the layout's language onto <html> (screen readers, fonts, and
  // browser translation prompts read it there) and translate the tab title.
  var applied = false;
  new MutationObserver(function () {
    var root = document.getElementById("app-root");
    if (!root || applied) { return; }
    applied = true;
    document.documentElement.lang = root.getAttribute("lang") || "en";
    document.documentElement.dir  = root.getAttribute("dir")  || "ltr";
    var title = root.getAttribute("data-title");
    if (title) { document.title = title; }
  }).observe(document.documentElement, {childList: true, subtree: true});
})();
