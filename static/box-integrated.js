(function(){
  'use strict';

  const numFields=['rss_poll_seconds','min_size_gb','max_size_gb','max_age_seconds','min_leechers','max_seeders','min_demand','min_score','max_active_downloads','data_cap_gb','disk_reserve_gb','traffic_budget_gb','traffic_hard_stop_gb','billing_reset_day','cleanup_ratio','cleanup_idle_minutes','cleanup_min_seed_minutes','max_rss_items_per_run'];
  const strFields=['rss_url','qbit_url','qbit_username','qbit_password','download_dir','vnstat_interface'];
  let boxLoaded=false;
  let refreshTimer=null;

  const el=(id)=>document.getElementById(id);
  const esc=(s)=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function fmtBytes(n){n=Number(n||0);for(const u of ['B','KB','MB','GB','TB']){if(Math.abs(n)<1024)return n.toFixed(u==='B'?0:1)+u;n/=1024}return n.toFixed(1)+'PB'}
  function boxMsg(text,type='info'){const n=el('bxMsg');if(!n)return;n.className='box-alert show '+type;n.textContent=text}
  function clearMsg(){const n=el('bxMsg');if(n)n.className='box-alert'}

  function pageHtml(){return `
    <section class="page" id="page-box">
      <div class="page-inner">
        <div class="page-head">
          <div><h2>盒子抢流</h2><p>M-Team RSS 抢新种 · qBittorrent · 小盘滚种 · vnStat 流量钱包</p></div>
          <div class="box-toolbar-status"><i class="box-status-dot" id="bxStatusDot"></i><span class="muted" id="bxStatusText">读取中</span></div>
        </div>

        <div class="hero-tech box-hero">
          <div>
            <div class="pulse"><i></i> Seedbox 实时状态</div>
            <h3>M-Team Box</h3>
            <p>只盯真正的新种，把 25G 小盘用在最有上传机会的地方</p>
            <div class="hero-metrics">
              <div class="hero-metric"><b id="bxRun">-</b><span>运行状态</span></div>
              <div class="hero-metric"><b id="bxTrafficHero">-</b><span>本周期流量</span></div>
              <div class="hero-metric"><b id="bxDiskHero">-</b><span>磁盘可用</span></div>
              <div class="hero-metric"><b id="bxTasksHero">-</b><span>盒子任务</span></div>
            </div>
          </div>
          <div class="hero-panel">
            <div class="row"><span>RSS</span><b id="bxRssTitle">-</b></div>
            <div class="row"><span>最近扫描</span><b id="bxLastRun">尚未</b></div>
            <div class="row"><span>下载中</span><b id="bxDownloading">0</b></div>
            <div class="row"><span>做种中</span><b id="bxSeeding">0</b></div>
          </div>
        </div>

        <div class="toolbar">
          <div>
            <button class="btn btn-primary" id="bxStart">启动盒子</button>
            <button class="btn" id="bxStop">停止</button>
            <button class="btn" id="bxRunOnce">立即扫一次 RSS</button>
            <button class="btn" id="bxCleanup">立即淘汰</button>
            <button class="btn" id="bxRefresh">刷新状态</button>
          </div>
          <span class="muted">首次 RSS 会先暖机登记现状，从下一条新种开始判断</span>
        </div>
        <div class="box-alert" id="bxMsg"></div>

        <div class="box-cards">
          <div class="box-stat blue"><div class="k">本周期流量</div><div class="v" id="bxTraffic">-</div><div class="h" id="bxTrafficHint">软预算 -</div><div class="box-progress"><i id="bxTrafficBar" style="width:0%"></i></div></div>
          <div class="box-stat green"><div class="k">磁盘可用</div><div class="v" id="bxDisk">-</div><div class="h" id="bxDiskHint">安全预留 -</div></div>
          <div class="box-stat"><div class="k">当前盒子任务</div><div class="v" id="bxTasks">-</div><div class="h" id="bxTaskHint">下载 0 · 做种 0</div></div>
          <div class="box-stat amber"><div class="k">当前速度</div><div class="v" id="bxSpeed">-</div><div class="h">qBittorrent 实时上传</div></div>
        </div>

        <details class="cfg-fold box-config-block" open>
          <summary class="cfg-fold-hd">盒子配置与抢流规则</summary>
          <div class="cfg-fold-body">
            <div class="form-grid">
              <div class="form-item full"><label>M-Team RSS 私有地址</label><input id="bx_rss_url" placeholder="已保存时留空即可；换地址会自动重新暖机" /></div>
              <div class="form-item"><label>RSS 轮询（秒）</label><input id="bx_rss_poll_seconds" type="number" min="20" /></div>
              <div class="form-item"><label>每轮最多看多少条 RSS</label><input id="bx_max_rss_items_per_run" type="number" min="1" max="100" /></div>
              <div class="form-item"><label>qBittorrent WebUI</label><input id="bx_qbit_url" /></div>
              <div class="form-item"><label>qBit 用户名</label><input id="bx_qbit_username" /></div>
              <div class="form-item"><label>qBit 密码</label><input id="bx_qbit_password" type="password" placeholder="已保存时留空即可" /></div>
              <div class="form-item"><label>下载目录</label><input id="bx_download_dir" /></div>
              <div class="form-item"><label>vnStat 网卡</label><input id="bx_vnstat_interface" /></div>
              <div class="form-item"><label>自动淘汰</label><select id="bx_auto_cleanup"><option value="1">开启</option><option value="0">关闭</option></select></div>

              <div class="form-item"><label>最小体积（GB）</label><input id="bx_min_size_gb" type="number" step="0.1" /></div>
              <div class="form-item"><label>最大体积（GB）</label><input id="bx_max_size_gb" type="number" step="0.1" /></div>
              <div class="form-item"><label>最大种龄（秒）</label><input id="bx_max_age_seconds" type="number" /></div>
              <div class="form-item"><label>最少 Leecher</label><input id="bx_min_leechers" type="number" /></div>
              <div class="form-item"><label>最多 Seeder</label><input id="bx_max_seeders" type="number" /></div>
              <div class="form-item"><label>最低需求比 L/(S+1)</label><input id="bx_min_demand" type="number" step="0.05" /></div>
              <div class="form-item"><label>最低抢流评分</label><input id="bx_min_score" type="number" step="1" /></div>
              <div class="form-item"><label>同时下载数</label><input id="bx_max_active_downloads" type="number" /></div>
              <div class="form-item"><label>盒子数据上限（GB）</label><input id="bx_data_cap_gb" type="number" step="0.5" /></div>
              <div class="form-item"><label>磁盘安全预留（GB）</label><input id="bx_disk_reserve_gb" type="number" step="0.5" /></div>

              <div class="form-item"><label>流量软预算（GB）</label><input id="bx_traffic_budget_gb" type="number" step="10" /></div>
              <div class="form-item"><label>流量硬停止（GB）</label><input id="bx_traffic_hard_stop_gb" type="number" step="10" /></div>
              <div class="form-item"><label>账期重置日</label><input id="bx_billing_reset_day" type="number" min="1" max="31" /></div>
              <div class="form-item"><label>满分享率自动淘汰</label><input id="bx_cleanup_ratio" type="number" step="0.05" /></div>
              <div class="form-item"><label>完成后空闲多少分钟淘汰</label><input id="bx_cleanup_idle_minutes" type="number" /></div>
              <div class="form-item"><label>至少做种多少分钟再判空闲</label><input id="bx_cleanup_min_seed_minutes" type="number" /></div>
            </div>
            <div class="box-config-note"><strong>当前策略建议：</strong>小盘不追求塞满，优先新鲜度和需求。新 RSS 第一次只做基线暖机；错误/删除种在 RSS 层直接过滤；低需求候选会退避后复查，不会每轮反复请求详情。</div>
            <div class="form-actions">
              <button class="btn btn-primary" id="bxSave">保存配置</button>
              <button class="btn" id="bxTestQb">测试 qBittorrent</button>
              <button class="btn" id="bxResetTraffic">重置流量基线</button>
              <span class="muted" id="bxCfgHint"></span>
            </div>
          </div>
        </details>

        <div class="card" style="margin-top:12px">
          <div class="box-section-head"><div><h3>当前 qBittorrent 盒子任务</h3><p>只显示带 mteam-box 标签的任务，不碰你手工添加的其他种子</p></div></div>
          <div class="box-table-wrap"><table class="box-table"><thead><tr><th>名称</th><th>状态</th><th>进度</th><th>下载</th><th>上传</th><th>Ratio</th><th>速度</th><th>操作</th></tr></thead><tbody id="bxTorrentRows"></tbody></table></div>
        </div>

        <div class="card" style="margin-top:12px">
          <div class="box-section-head"><div><h3>最近判断</h3><p>为什么吃、为什么不吃，全部留痕</p></div><button class="btn" id="bxRefreshDecisions">刷新</button></div>
          <div class="box-decision-list" id="bxDecisions"></div>
        </div>
      </div>
    </section>`}

  function install(){
    if(el('page-box'))return;
    try{titles.box='盒子抢流';pageGroup.box='task'}catch(_e){}
    const taskBody=document.querySelector('.nav-sub[data-group="task"] .nav-sub-body');
    if(taskBody){
      const btn=document.createElement('button');
      btn.type='button';btn.className='nav-item';btn.dataset.page='box';
      btn.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16v10H4z"/><path d="M8 7V4h8v3M8 17v3h8v-3"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/></svg>盒子抢流';
      btn.addEventListener('click',()=>{goPage('box');boxLoadAll().catch(e=>boxMsg(e.message,'bad'))});
      taskBody.appendChild(btn);
    }
    const main=document.querySelector('.admin-main');
    if(main)main.insertAdjacentHTML('beforeend',pageHtml());
    bind();
    refreshTimer=setInterval(()=>{const p=el('page-box');if(p&&p.classList.contains('active'))boxLoadStatus().catch(()=>{})},15000);
  }

  function getInput(name){return el('bx_'+name)}
  async function boxLoadCfg(){
    const c=await api('/api/box/config');
    for(const k of [...numFields,...strFields]){
      if(k==='rss_url'||k==='qbit_password')continue;
      const n=getInput(k);if(n&&c[k]!==undefined)n.value=c[k]
    }
    el('bx_auto_cleanup').value=c.auto_cleanup?'1':'0';
    getInput('rss_url').placeholder=c.rss_url_set?'已保存 RSS 地址；留空不修改':'粘贴 M-Team 个人 RSS 地址';
    getInput('qbit_password').placeholder=c.qbit_password_set?'已保存密码；留空不修改':'输入 qBit WebUI 密码';
    boxLoaded=true;
  }

  async function boxSaveCfg(silent=false){
    const body={};
    for(const k of strFields){const n=getInput(k);if(!n)continue;const v=n.value.trim();if(v)body[k]=v}
    for(const k of numFields){const n=getInput(k);if(n&&n.value!=='')body[k]=Number(n.value)}
    body.auto_cleanup=el('bx_auto_cleanup').value==='1';
    await api('/api/box/config',{method:'POST',body:JSON.stringify(body)});
    getInput('rss_url').value='';getInput('qbit_password').value='';
    await boxLoadCfg();
    if(!silent)boxMsg('配置已保存','ok');
  }

  async function boxLoadStatus(){
    const s=await api('/api/box/status');
    const running=!!s.running;
    el('bxRun').textContent=running?'运行中':'已停止';
    el('bxStatusText').textContent=running?'盒子正在巡逻':'盒子已停止';
    el('bxStatusDot').className='box-status-dot '+(running?'on':'');
    const t=s.traffic||{}, d=s.disk||{}, q=s.qbit||{}, sum=q.summary||{};
    const used=t.used_gb!==undefined?Number(t.used_gb):null, budget=Number(t.budget_gb||0), hard=Number(t.hard_stop_gb||0);
    el('bxTrafficHero').textContent=used===null?'-':used+'GB';
    el('bxTraffic').textContent=used===null?'-':used.toFixed(1)+' GB';
    el('bxTrafficHint').textContent=`软预算 ${budget||'-'}GB · 硬停 ${hard||'-'}GB`;
    const pct=used!==null&&budget>0?Math.max(0,Math.min(100,used/budget*100)):0;el('bxTrafficBar').style.width=pct+'%';
    el('bxDiskHero').textContent=d.free_gb!==undefined?d.free_gb+'GB':'-';el('bxDisk').textContent=d.free_gb!==undefined?d.free_gb+' GB':'-';el('bxDiskHint').textContent=`安全预留 ${d.reserve_gb??'-'}GB`;
    el('bxTasksHero').textContent=sum.count??'-';el('bxTasks').textContent=sum.count??'-';el('bxDownloading').textContent=sum.active_downloads??0;el('bxSeeding').textContent=sum.seeding??0;el('bxTaskHint').textContent=`下载 ${sum.active_downloads??0} · 做种 ${sum.seeding??0}`;
    el('bxSpeed').textContent=fmtBytes(sum.up_speed||0)+'/s';
    el('bxRssTitle').textContent=s.last_rss_title||'-';el('bxLastRun').textContent=s.last_run_at||'尚未';
    if(s.last_error)boxMsg(s.last_error,'bad');
    renderTorrents(q.items||[]);renderDecisions(s.decisions||[]);
    return s;
  }

  function renderTorrents(rows){
    const body=el('bxTorrentRows');if(!body)return;
    body.innerHTML=rows.map(x=>`<tr><td class="box-name" title="${esc(x.name)}">${esc(x.name)}</td><td>${esc(x.state)}</td><td>${Number(x.progress_pct||0).toFixed(1)}%</td><td>${fmtBytes(x.downloaded)}</td><td>${fmtBytes(x.uploaded)}</td><td>${Number(x.ratio||0).toFixed(2)}</td><td class="box-speed">↓${fmtBytes(x.dlspeed)}/s ↑${fmtBytes(x.upspeed)}/s</td><td><button class="btn" data-box-action="pause" data-hash="${esc(x.hash)}">暂停</button> <button class="btn" data-box-action="delete-data" data-hash="${esc(x.hash)}">删除</button></td></tr>`).join('')||'<tr><td colspan="8" class="box-empty">暂无盒子任务</td></tr>';
  }

  function renderDecisions(rows){
    const body=el('bxDecisions');if(!body)return;
    body.innerHTML=rows.map(x=>`<div class="box-decision"><div class="box-decision-head"><span class="box-pill ${esc(x.result||'')}">${esc(x.result||'-')}</span><b title="${esc(x.name||x.torrent_id||'-')}">${esc(x.name||x.torrent_id||'-')}</b></div><div class="box-decision-meta">${esc(x.at||'')} · score ${x.score??'-'} · ${esc(x.reason||'')}</div></div>`).join('')||'<div class="box-empty">还没有判断记录</div>';
  }

  async function boxLoadAll(){clearMsg();if(!boxLoaded)await boxLoadCfg();await boxLoadStatus()}
  async function boxTestQb(){try{await boxSaveCfg(true);const d=await api('/api/box/qbit/test',{method:'POST'});boxMsg(`qBittorrent ${d.version} 连接正常，状态 ${d.connection_status||'-'}`,'ok');await boxLoadStatus()}catch(e){boxMsg(e.message,'bad')}}
  async function boxRunOnce(){try{await boxSaveCfg(true);boxMsg('正在扫描 RSS…','info');const d=await api('/api/box/run',{method:'POST'});if(d.warmup)boxMsg(d.message||'RSS 暖机完成','ok');else boxMsg(`扫描完成：新增 ${d.added?.length||0}，观察 ${d.pending?.length||0}，拒绝 ${d.rejected?.length||0}${d.skipped_backoff?`，退避跳过 ${d.skipped_backoff}`:''}`,'ok');await boxLoadStatus()}catch(e){boxMsg(e.message,'bad')}}
  async function boxStart(){try{await boxSaveCfg(true);await api('/api/box/start',{method:'POST'});boxMsg('盒子模式已启动','ok');await boxLoadStatus()}catch(e){boxMsg(e.message,'bad')}}
  async function boxStop(){try{await api('/api/box/stop',{method:'POST'});boxMsg('盒子模式已停止','ok');await boxLoadStatus()}catch(e){boxMsg(e.message,'bad')}}
  async function boxCleanup(){if(!confirm('现在执行一次自动淘汰规则？'))return;try{const d=await api('/api/box/cleanup',{method:'POST'});boxMsg(`本轮淘汰 ${d.deleted?.length||0} 个任务`,'ok');await boxLoadStatus()}catch(e){boxMsg(e.message,'bad')}}
  async function boxResetTraffic(){if(!confirm('从现在开始重新计算本账期盒子流量？'))return;try{await api('/api/box/traffic/reset',{method:'POST'});boxMsg('流量基线已重置','ok');await boxLoadStatus()}catch(e){boxMsg(e.message,'bad')}}
  async function torrentAction(action,hash){try{if(action==='delete-data'&&!confirm('删除该任务并删除本地数据？'))return;await api('/api/box/torrents/action',{method:'POST',body:JSON.stringify({hashes:[hash],action})});boxMsg(action==='pause'?'任务已暂停':'任务已删除','ok');await boxLoadStatus()}catch(e){boxMsg(e.message,'bad')}}

  function bind(){
    el('bxSave').addEventListener('click',()=>boxSaveCfg().catch(e=>boxMsg(e.message,'bad')));
    el('bxTestQb').addEventListener('click',boxTestQb);el('bxRunOnce').addEventListener('click',boxRunOnce);el('bxStart').addEventListener('click',boxStart);el('bxStop').addEventListener('click',boxStop);el('bxCleanup').addEventListener('click',boxCleanup);el('bxResetTraffic').addEventListener('click',boxResetTraffic);el('bxRefresh').addEventListener('click',()=>boxLoadStatus().catch(e=>boxMsg(e.message,'bad')));el('bxRefreshDecisions').addEventListener('click',()=>boxLoadStatus().catch(e=>boxMsg(e.message,'bad')));
    el('bxTorrentRows').addEventListener('click',(e)=>{const b=e.target.closest('[data-box-action]');if(b)torrentAction(b.dataset.boxAction,b.dataset.hash)});
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();