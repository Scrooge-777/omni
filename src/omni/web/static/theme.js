// Runs before first paint so a saved theme never flashes the wrong colours.
(function () {
  try {
    const theme = localStorage.getItem('omni-theme');
    if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
  } catch (e) { /* storage blocked: follow the OS */ }
})();
