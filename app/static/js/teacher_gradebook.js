/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
/* Native forms own writes. Script changes presentation and local destinations only. */
(() => {
  'use strict';
  const root=document.querySelector('[data-gradebook]');
  if(!root) return;
  const pointsHint = (raw, minimum, maximum) => {
    // Hint only: keep the original input for the authoritative Decimal parser.
    const text = raw.replace(/[\u0660-\u0669\u06f0-\u06f9]/g, digit => String(digit.charCodeAt(0) & 15));
    // Do not reject other Unicode number forms that the server may support.
    if (/[^\x00-\x7f]/.test(text)) return true;
    return /^(?:\d{1,5}(?:\.\d{1,2})?|\.\d{1,2})$/.test(text) && Number(text) >= minimum && Number(text) <= maximum;
  };

  const groupSwitch=root.querySelector('[data-group-switch]');
  groupSwitch?.addEventListener('change',()=> { location.assign(groupSwitch.value); });
  const groupPicker=root.querySelector('.tgb-group-picker');
  pageScope.listen(document, 'click',event=> { if(groupPicker && !groupPicker.contains(event.target)) groupPicker.open=false; });
  pageScope.listen(root, 'keydown',event=> { if(event.key==='Escape' && groupPicker?.open) { groupPicker.open=false; groupPicker.querySelector('summary').focus(); } });
  const sectionOwner=root.querySelector('[data-tgb-sections]');
  if(sectionOwner) {
    const sections=[...sectionOwner.querySelectorAll('[data-tgb-section]')];
    const links=[...sectionOwner.querySelectorAll('[data-tgb-tab]')];
    const positions=new Map();
    const activate=(id, restorePosition=false)=> {
      if(!sections.some(section=>section.id===id)) id=sectionOwner.dataset.defaultSection || 'grade-tasks';
      const previous=root.dataset.activeSection;
      if(previous===id) return;
      if(previous) positions.set(previous, window.scrollY);
      sections.forEach(section=> { section.hidden=section.id!==id; });
      links.forEach(link=> { if(link.hash==='#'+id) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current'); });
      root.classList.toggle('is-student-view',id.startsWith('student-'));
      root.dataset.activeSection=id;
      if(restorePosition) pageScope.requestAnimationFrame(()=>window.scrollTo({top:positions.get(id) || 0,behavior:'instant'}));
    };
    root.querySelectorAll('[data-tgb-tab],[data-tgb-jump]').forEach(link=>pageScope.listen(link, 'click',event=> {
      event.preventDefault(); activate(link.hash.slice(1), true); history.replaceState(history.state,'',link.hash);
    }));
    pageScope.listen(window, 'hashchange',()=>activate(location.hash.slice(1), true));
    activate(location.hash.slice(1));
    const filters=root.querySelector('[data-task-filters]');
    if(filters) {
      const cards=[...root.querySelectorAll('[data-task-state]')];
      const phone=window.matchMedia('(max-width:767px)');
      let state=cards.some(card=>card.dataset.taskState==='draft')?'draft':'released';
      const filter=()=> {
        cards.forEach(card=> { card.hidden=phone.matches && (card.dataset.taskState!==state || card.hasAttribute('data-pickup-row')); });
        filters.querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.taskFilter===state)));
        root.querySelector('[data-filter-empty]').hidden=!phone.matches || cards.some(card=>!card.hidden && card.dataset.taskState===state);
      };
      filters.hidden=false;
      pageScope.listen(filters, 'click',event=> { const button=event.target.closest('[data-task-filter]'); if(button) {state=button.dataset.taskFilter;filter();} });
      pageScope.listen(phone, 'change',filter); filter();
    }
  }
  const itemForm=root.querySelector('[data-item-form]');
  if(itemForm) {
    const kind=itemForm.querySelector('[data-source-kind]');
    const sourceFields=[...itemForm.querySelectorAll('[data-source-field]')];
    const name=itemForm.querySelector('[data-item-name]');
    const maximum=itemForm.querySelector('[data-item-max]');
    const category=itemForm.querySelector('[data-item-category]');
    const label=select=>select.selectedOptions[0]?.textContent || '';
    const live=()=> {
      root.querySelector('[data-live-title]').textContent=name.value || root.querySelector('[data-live-title]').dataset.placeholder;
      root.querySelector('[data-live-category]').textContent=label(category);
      root.querySelector('[data-live-max]').textContent=maximum.value || '—';
    };
    root.querySelector('[data-live-title]').dataset.placeholder=root.querySelector('[data-live-title]').textContent;
    const choices=itemForm.querySelector('[data-kind-choices]');
    const updateSources=clear=> {
      sourceFields.forEach(field=> {
        const select=field.querySelector('select'); const active=field.dataset.sourceField===kind.value;
        if(clear && !active) select.value='';
        field.hidden=!active && !select.value && !field.querySelector('.field__error');
      });
      choices.querySelectorAll('[data-kind-choice]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.kindChoice===kind.value)));
      live();
    };
    choices.hidden=false;
    // Keep the canonical select enabled and expose its server errors.
    itemForm.querySelector('[data-kind-fallback]').hidden=!itemForm.querySelector('[data-kind-fallback] .field__error');
    pageScope.listen(choices, 'click',event=> {const button=event.target.closest('[data-kind-choice]');if(button){kind.value=button.dataset.kindChoice;updateSources(true);}});
    pageScope.listen(kind, 'change',()=>updateSources(true));
    pageScope.listen(itemForm, 'input',live); pageScope.listen(itemForm, 'change',live); updateSources(false);
    if(itemForm.dataset.editing!=='true') {
      const panels=[...itemForm.querySelectorAll('[data-item-step]')];
      const navigation=root.querySelector('[data-wizard-nav]');
      const next=itemForm.querySelector('[data-wizard-next]');
      const previous=itemForm.querySelector('[data-wizard-back]');
      const cancel=itemForm.querySelector('[data-wizard-cancel]');
      const submit=itemForm.querySelector('[data-item-submit]');
      const error=itemForm.querySelector('[data-wizard-error]'); let step=1;
      const show=target=> {
        step=target;
        const phoneTitle=root.querySelector('[data-create-title]');
        phoneTitle.textContent=phoneTitle.dataset[['titleOne','titleTwo','titleThree'][step-1]];
        const kicker=root.querySelector('[data-create-kicker]');
        kicker.textContent=kicker.dataset.template.replace('%(number)d',String(step));
        itemForm.dataset.step=String(step);
        panels.forEach(panel=> {panel.hidden=Number(panel.dataset.itemStep)!==step;});
        navigation.querySelectorAll('[data-wizard-step]').forEach(button=> {if(Number(button.dataset.wizardStep)===step)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');});
        root.querySelector('[data-wizard-phone-back]').hidden=step===1;
        previous.hidden=step===1; cancel.hidden=step!==1; next.hidden=step===3; submit.hidden=step!==3;
        itemForm.querySelector('[data-item-summary-title]').textContent=name.value;
        itemForm.querySelector('[data-item-summary-kind]').textContent=choices.querySelector('[data-kind-choice="'+kind.value+'"] strong')?.textContent || label(kind);
        itemForm.querySelector('[data-item-summary-category]').textContent=label(category);
        itemForm.querySelector('[data-item-summary-max]').textContent=maximum.value;
      };
      const valid=target=> {
        error.textContent='';
        if(target===1 && !kind.value){error.textContent=itemForm.dataset.msgRequired;return false;}
        if(target===2){
          const source=sourceFields.find(field=>field.dataset.sourceField===kind.value);
          if(!name.value.trim() || !category.value || (source && !source.querySelector('select').value)){error.textContent=itemForm.dataset.msgRequired;return false;}
          if(!pointsHint(maximum.value.trim(),0.01,99999.99)){error.textContent=itemForm.dataset.msgPoints;return false;}
        } return true;
      };
      const advance=target=> {for(let current=step;current<target;current++)if(!valid(current)){show(current);return;}show(target);};
      navigation.hidden=false;
      pageScope.listen(navigation, 'click',event=> {const button=event.target.closest('[data-wizard-step]');if(button)advance(Number(button.dataset.wizardStep));});
      pageScope.listen(next, 'click',()=>advance(Math.min(3,step+1)));
      const goBack=()=>{error.textContent='';show(Math.max(1,step-1));};
      pageScope.listen(previous, 'click',goBack);
      root.querySelector('[data-wizard-phone-back]').addEventListener('click',goBack);
      pageScope.listen(itemForm, 'submit',event=> {if(step!==3){event.preventDefault();advance(3);}});
      const invalidPanel=panels.find(panel=>panel.querySelector('.field__error'));show(invalidPanel?Number(invalidPanel.dataset.itemStep):1);
    }
  }
  const scoreForm=root.querySelector('[data-score-form]');
  if(scoreForm) {
    const rows=[...scoreForm.querySelectorAll('[data-score-row]')];const media=window.matchMedia('(max-width:767px)');
    const picker=scoreForm.querySelector('[data-score-picker]');const mode=scoreForm.querySelector('[data-score-mode]');
    const previous=scoreForm.querySelector('[data-score-previous]');const next=scoreForm.querySelector('[data-score-next]');
    const target=scoreForm.querySelector('[data-next-record]');const status=scoreForm.querySelector('[data-score-status]');
    const roster=scoreForm.querySelector('[data-score-roster]');const position=scoreForm.querySelector('[data-score-position]');
    const originalPosition=position.textContent;const ungraded=scoreForm.querySelector('[data-ungraded-only]');let all=false;
    let index=rows.findIndex(row=>row.dataset.record===scoreForm.dataset.focus);
    if(index<0)index=rows.findIndex(row=>!row.querySelector('[data-score-input]').value.trim());if(index<0)index=0;
    const update=()=> {
      const focus=media.matches && !all;scoreForm.classList.toggle('is-focus',focus);scoreForm.classList.toggle('is-roster',media.matches && all);
      rows.forEach((row,i)=>{row.hidden=media.matches ? (all || i!==index) : (ungraded.checked && Boolean(row.querySelector('[data-score-input]').value.trim()));});
      picker.hidden=!focus;scoreForm.querySelector('[data-score-focus-actions]').hidden=!focus || !rows.length;
      previous.hidden=next.hidden=!focus || !rows.length;previous.disabled=index===0;
      mode.hidden=!media.matches || !rows.length;mode.textContent=all?scoreForm.dataset.labelFocus:scoreForm.dataset.labelAll;
      roster.hidden=!(media.matches && all);scoreForm.querySelector('[data-score-close]').hidden=!focus;
      position.textContent=focus && rows.length ? scoreForm.dataset.labelPosition.replace('%(number)d',String(index+1)).replace('%(total)d',String(rows.length)) : originalPosition;
      const last=index>=rows.length-1;next.textContent=last?scoreForm.dataset.labelReview:scoreForm.dataset.labelNext;next.value=last?'review':'next';target.value=rows[index+1]?.dataset.record || '';
      let entered=0;rows.forEach((row,i)=> {
        const done=Boolean(row.querySelector('[data-score-input]').value.trim());if(done)entered++;
        const button=picker.children[i];if(button){button.classList.toggle('is-graded',done);button.setAttribute('aria-pressed',String(i===index));button.textContent=done && i!==index?'✓':String(i+1);}
      });status.textContent=entered+' / '+rows.length+' '+scoreForm.dataset.labelGraded;
    };
    const feedback=()=>rows.forEach(row=>{row.querySelector('details').open=!media.matches || Boolean(row.querySelector('textarea').value);});
    pageScope.listen(mode, 'click',()=>{all=!all;update();});pageScope.listen(previous, 'click',()=>{index=Math.max(0,index-1);update();});
    const choose=id=>{index=rows.findIndex(row=>row.dataset.record===id);all=false;update();};
    pageScope.listen(picker, 'click',event=>{const button=event.target.closest('[data-score-pick]');if(button)choose(button.dataset.scorePick);});
    pageScope.listen(roster, 'click',event=>{const button=event.target.closest('[data-roster-pick]');if(button)choose(button.dataset.rosterPick);});
    pageScope.listen(ungraded, 'change',update);pageScope.listen(scoreForm, 'input',update);pageScope.listen(media, 'change',()=>{feedback();update();});
    pageScope.listen(scoreForm, 'submit',event=>{
      let invalid=-1;rows.forEach((row,i)=>{
        const input=row.querySelector('[data-score-input]');const comment=row.querySelector('[data-score-comment]');const value=input.value.trim();let error='';
        if(!value && scoreForm.dataset.released==='true')error=scoreForm.dataset.msgBlank;
        else if(value && !pointsHint(value,0,Number(scoreForm.dataset.max)))error=scoreForm.dataset.msgScore;
        if(comment.value.length>comment.maxLength)error=scoreForm.dataset.msgFeedback;
        input.setAttribute('aria-invalid',String(Boolean(error)));row.querySelector('[data-score-error]').textContent=error;if(error && invalid<0)invalid=i;
      });if(invalid>=0){event.preventDefault();index=invalid;all=false;ungraded.checked=false;update();rows[index].querySelector('[data-score-input]').focus();}
    });
    pageScope.listen(scoreForm, 'keydown',event=>{if(event.key==='Enter' && event.target.matches('[data-score-input]') && media.matches && !all){event.preventDefault();scoreForm.requestSubmit(next);}});
    feedback();update();
  }
  const choice=root.querySelector('[data-preview-choice]');
  if(choice){const panels=[...root.querySelectorAll('[data-preview-record]')];const select=()=>panels.forEach(panel=>{panel.hidden=panel.dataset.previewRecord!==choice.value;});pageScope.listen(choice, 'change',select);select();}
})();

});
