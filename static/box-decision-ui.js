(function(){
  'use strict';

  function patchLabels(){
    const age=document.getElementById('bx_max_age_seconds');
    if(age&&age.parentElement){
      const label=age.parentElement.querySelector('label');
      if(label)label.textContent='黄金抢流窗口（秒，软）';
    }
    const seed=document.getElementById('bx_max_seeders');
    if(seed&&seed.parentElement){
      const label=seed.parentElement.querySelector('label');
      if(label)label.textContent='Seeder 竞争参考线（软）';
    }
    const poll=document.getElementById('bx_rss_poll_seconds');
    if(poll&&poll.parentElement){
      const label=poll.parentElement.querySelector('label');
      if(label)label.textContent='RSS 轮询（秒，建议 45）';
    }
    const note=document.querySelector('#page-box .box-config-note');
    if(note&&!document.getElementById('bxDecisionHint')){
      const hint=document.createElement('div');
      hint.id='bxDecisionHint';
      hint.className='box-config-note';
      hint.style.marginTop='8px';
      hint.innerHTML='<strong>趋势策略：</strong>900 秒只代表黄金窗口结束，不会直接拒绝；超过后若 Leecher / 需求比 / 增长趋势足够强仍可救援。Seeder 超过参考线只扣竞争分，不再一刀切。默认绝对观察上限 3600 秒。';
      note.insertAdjacentElement('afterend',hint);
    }
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>setTimeout(patchLabels,0));
  else setTimeout(patchLabels,0);
})();
