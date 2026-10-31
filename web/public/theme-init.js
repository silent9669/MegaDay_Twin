(function () {
  var pref = 'system';
  try { pref = localStorage.getItem('megaday.theme') || 'system'; } catch { /* Optional preference. */ }
  var dark = pref === 'dark' || (pref === 'system' && globalThis.matchMedia('(prefers-color-scheme: dark)').matches);
  globalThis.document.documentElement.dataset.theme = dark ? 'dark' : 'light';
})();
