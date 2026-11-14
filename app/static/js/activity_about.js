window.aelmsModules.register(document.currentScript, function (scope) {
  const button = document.querySelector('[data-activity-about-open]');
  const dialog = document.querySelector('[data-activity-about-dialog]');
  if (!button || !dialog) return;
  scope.listen(button, 'click', () => { if (!dialog.open) dialog.showModal(); });
  scope.listen(dialog, 'close', () => button.focus({preventScroll:true}));
});
