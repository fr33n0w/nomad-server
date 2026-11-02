// Loaded in <head>: sets the theme before the page paints (saved choice, else the system's)
(function () {
  var t = null;
  try { t = localStorage.getItem("ns-theme"); } catch (e) { /* private mode */ }
  if (t !== "light" && t !== "dark") t = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  document.documentElement.dataset.theme = t;
  // no page scrollbar while the splash covers the page
  document.documentElement.classList.add("splashing");
})();
