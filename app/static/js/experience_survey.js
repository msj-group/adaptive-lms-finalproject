/* A local survey preview. Answers never leave the browser or enter Study. */
window.aelmsModules.register(document.currentScript, function (pageScope) {
  "use strict";
  const wrapper = document.querySelector('[data-experience-survey]');
  const card = wrapper?.querySelector('#research-feedback');
  if (!card) return;
  const form = card.querySelector('[data-research-feedback-form]');
  const submit = card.querySelector('[data-research-feedback-submit]');
  const status = card.querySelector('[data-research-feedback-status]');
  const key = 'youth.experience.survey.v1:' + wrapper.dataset.account;
  let saved;
  try { saved = sessionStorage.getItem(key); } catch (_) { /* Private mode still supports the preview. */ }
  if (saved === 'answered') return;
  const due = Number(saved) > 0 ? Number(saved) : Date.now() + 5000;
  try { sessionStorage.setItem(key, String(due)); } catch (_) { /* No server fallback. */ }
  card.querySelector('.research-question-rating legend').textContent = 'إلى أي درجة شعرت بالإحباط أثناء استخدام المنصة؟';
  const help = card.querySelector('[data-research-feedback-help-question]');
  if (help) help.hidden = false;
  const skip = card.querySelector('[data-research-feedback-skip]');
  if (skip) skip.hidden = true;
  status.textContent = 'استبيان تجريبي؛ لا تُحفظ الإجابات.';
  const moveToVisibleLayer = () => {
    const layer = document.fullscreenElement || wrapper;
    if (card.parentElement !== layer) layer.append(card);
  };
  pageScope.listen(document, 'fullscreenchange', moveToVisibleLayer);
  pageScope.setTimeout(() => { moveToVisibleLayer(); card.hidden = false; }, Math.max(0, due - Date.now()));
  pageScope.listen(form, 'change', () => {
    submit.disabled = !form.querySelector('[name="research-rating"]:checked');
  });
  pageScope.listen(form, 'submit', event => {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!form.querySelector('[name="research-rating"]:checked')) return;
    submit.disabled = true;
    try { sessionStorage.setItem(key, 'answered'); } catch (_) { /* Nothing is stored remotely. */ }
    status.textContent = 'شكرًا لك. اكتملت معاينة الاستبيان.';
    pageScope.setTimeout(() => { card.hidden = true; }, 700);
  }, {capture: true});
  pageScope.signal.addEventListener('abort', () => {
    if (card.parentElement !== wrapper) card.remove();
  }, {once: true});
});
