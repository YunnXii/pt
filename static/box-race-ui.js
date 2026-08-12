(function(){
  'use strict';

  const el=(id)=>document.getElementById(id);
  const pct=(n)=>Number(n||0).toFixed(1)+'%';
  const ratio=(n)=>Number(n||0).toFixed(2);

  function install(){
    if(el('bxRaceExperiment'))return;
    const cards=document.querySelector('#page-box .box-cards');
    if(!cards)return;
    const wrap=document.createElement('div');
    wrap.className='card';
    wrap.id='bxRaceExperiment';
    wrap.style.marginTop='12px';
    wrap.innerHTML=`
      <div class="box-section-head">
        <div><h3>Race vs Trend 实验</h3><p>Race 不等 Leecher 直接入场；Trend 等需求/趋势确认后入场。样本少时先看，不急着下结论。</p></div>
        <span class="muted" id="bxRaceRule">读取中</span>
      </div>
      <div class="box-cards">
        <div class="box-stat blue"><div class="k">Race 极速车道</div><div class="v" id="bxRaceRatio">-</div><div class="h" id="bxRaceHint">样本 -</div></div>
        <div class="box-stat green"><div class="k">Trend 趋势车道</div><div class="v" id="bxTrendRatio">-</div><div class="h" id="bxTrendHint">样本 -</div></div>
        <div class="box-stat amber"><div class="k">Ratio ≥ 2 命中率</div><div class="v" id="bxRaceHit">-</div><div class="h" id="bxTrendHit">Trend -</div></div>
        <div class="box-stat"><div class="k">实验说明</div><div class="v" style="font-size:15px">抢先 vs 确认</div><div class="h">自动记录 1/3/5/10 分钟和最终 Ratio</div></div>
      </div>`;
    cards.insertAdjacentElement('afterend',wrap);
    refresh().catch(()=>{});
  }

  async function refresh(){
    if(typeof api!=='function'||!el('bxRaceExperiment'))return;
    const s=await api('/api/box/status');
    const r=s.race_lane||{};
    const e=s.experiment||{};
    const race=e.race||{}, trend=e.trend||{};
    el('bxRaceRule').textContent=r.enabled?`开启 · ${Number(r.min_size_gb||0).toFixed(1)}~${Number(r.max_size_gb||0).toFixed(1)}GB · ≤${r.max_age_seconds||0}s`:'已关闭';
    el('bxRaceRatio').textContent=ratio(race.avg_ratio)+'×';
    el('bxTrendRatio').textContent=ratio(trend.avg_ratio)+'×';
    el('bxRaceHint').textContent=`样本 ${race.count||0} · 完成 ${race.completed||0} · ≥1× ${race.ratio_ge_1||0}`;
    el('bxTrendHint').textContent=`样本 ${trend.count||0} · 完成 ${trend.completed||0} · ≥1× ${trend.ratio_ge_1||0}`;
    el('bxRaceHit').textContent=pct(race.ratio_ge_2_rate||0);
    el('bxTrendHit').textContent=`Trend ${pct(trend.ratio_ge_2_rate||0)}`;
  }

  function waitInstall(){
    install();
    if(!el('bxRaceExperiment'))setTimeout(waitInstall,300);
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',waitInstall);else waitInstall();
  setInterval(()=>{const p=el('page-box');if(p&&p.classList.contains('active'))refresh().catch(()=>{})},15000);
})();
