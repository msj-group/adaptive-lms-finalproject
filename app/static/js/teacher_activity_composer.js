/* Local question authoring. One native, CSRF-protected form owns creation/publication. */
window.aelmsModules.register(document.currentScript,function(scope) {
  'use strict';
  const root=document.querySelector('[data-activity-composer]'); if (!root) return;
  const context=root.querySelector('[data-composer-context]');
  context.querySelector('[data-context-go]').hidden=true;
  scope.listen(context,'change',()=>{ if (context.reportValidity()) context.requestSubmit(); });
  const builder=root.querySelector('[data-question-builder]'); if (!builder) return;
  const cards=builder.querySelector('[data-question-cards]'), json=builder.querySelector('[data-questions-json]');
  const maximum=Number(builder.dataset.maxQuestions);
  function serialize() {
    const rows=[...cards.children].map(card=>({prompt:card.querySelector('[data-question-prompt]').value,
      mode:card.querySelector('[data-question-mode]').value,
      options:[...card.querySelectorAll('.ta-question-option')].map(option=>({text:option.querySelector('[data-option-text]').value,correct:option.querySelector('[data-option-correct]').checked}))}));
    json.value=JSON.stringify(rows); builder.querySelector('[data-question-count]').textContent=rows.length;
    builder.querySelector('[data-add-question]').disabled=rows.length>=maximum;
    [...cards.children].forEach((card,index)=>{card.querySelector('[data-question-number]').textContent=index+1;});
  }
  function option(card,data={text:'',correct:false}) {
    const node=document.getElementById('activity-question-option').content.firstElementChild.cloneNode(true);
    node.querySelector('[data-option-text]').value=data.text; node.querySelector('[data-option-correct]').checked=data.correct;
    card.querySelector('[data-question-options]').append(node);
  }
  function question(data={prompt:'',mode:'single',options:[{text:'',correct:false},{text:'',correct:false}]}) {
    const card=document.getElementById('activity-question-card').content.firstElementChild.cloneNode(true);
    card.querySelector('[data-question-prompt]').value=data.prompt; card.querySelector('[data-question-mode]').value=data.mode;
    data.options.forEach(value=>option(card,value)); cards.append(card); return card;
  }
  JSON.parse(builder.querySelector('[data-initial-questions]').textContent).forEach(question); serialize();
  scope.listen(builder,'input',serialize); scope.listen(builder,'change',serialize);
  scope.listen(builder,'click',event=>{
    const button=event.target.closest('button'); if (!button) return;
    if (button.matches('[data-add-question]') && cards.children.length<maximum) { const card=question(); card.querySelector('textarea').focus(); }
    if (button.matches('[data-remove-question]')) button.closest('.ta-question-card').remove();
    if (button.matches('[data-add-option]')) { const card=button.closest('.ta-question-card'); if (card.querySelectorAll('.ta-question-option').length<8) option(card); }
    if (button.matches('[data-remove-option]')) button.closest('.ta-question-option').remove();
    serialize(); json.dispatchEvent(new Event('change',{bubbles:true}));
  });
  scope.listen(root.querySelector('[data-composer-form]'),'submit',serialize);
});
