// Resolve theme and language before first paint (theme.ts and App.tsx keep them current).
// A file of its own so the page needs no inline script (the cloud dashboard's CSP forbids it).
try {
  var t = localStorage.getItem("tokenledger.theme");
  var dark = t === "dark" || (t !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  var l = localStorage.getItem("tokenledger.lang") || (navigator.language.toLowerCase().indexOf("zh") === 0 ? "zh" : "en");
  if (l === "en") document.documentElement.lang = "en";
} catch (e) {}
