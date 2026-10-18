// Runs before first paint so a saved theme never flashes the wrong colours.
// Also shows a visible message if any script fails, instead of a page stuck on "Connecting".
function omniFatal(message) {
  const show = () => {
    if (document.getElementById('fatal')) return;
    const box = document.createElement('div');
    box.id = 'fatal';
    box.className = 'fatal';
    box.textContent = `OMNI hit an error: ${message}. Press Ctrl+F5 to reload. If it keeps happening, restart OMNI.`;
    document.body.prepend(box);
  };
  if (document.body) show(); else window.addEventListener('DOMContentLoaded', show);
}
window.addEventListener('error', (event) => omniFatal(event.message));
window.addEventListener('unhandledrejection', (event) => omniFatal(event.reason?.message || String(event.reason)));
(function () {
  try {
    const theme = localStorage.getItem('omni-theme');
    if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
  } catch (e) { /* storage blocked: follow the OS */ }
})();
