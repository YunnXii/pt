(function(){
  'use strict';

  const sections = [
    {
      title:'连接与扫描', desc:'RSS、qBittorrent 与扫描节奏。RSS 私有地址和密码读取时不会回显。',
      fields:[
        ['rss_url','M-Team RSS 私有地址','text','换 RSS 会重新暖机'],
        ['rss_poll_seconds','RSS 轮询（秒）','number','建议 60~90 秒'],
        ['max_rss_items_per_run','每轮最多处理 RSS 条目','number','RSS 放宽后可设 50'],
        ['qbit_url','qBittorrent WebUI','text','例如 http://127.0.0.1:8080'],
        ['qbit_username','qBit 用户名','text',''],
        ['qbit_password','qBit 密码','password','留空保持原密码'],
        ['qbit_tag','qBit 标签','text','默认 mteam-box'],
        ['qbit_category','qBit 分类','text','默认 mteam-box'],
        ['download_dir','下载目录','text',''],
        ['vnstat_interface','vnStat 网卡','text','通常 eth0'],
      ]
    },
    {
      title:'Race 极速车道', desc:'不等 Leecher / 需求比 / 评分。只要首次发现够新、体积命中对应档位，就直接入场。',
      fields:[
        ['race_lane_enabled','启用 Race Lane','bool',''],
        ['race_min_size_gb','Race 最小体积（GB）','number',''],
        ['race_tier1_max_size_gb','第一档最大体积（GB）','number','默认 2GB'],
        ['race_tier1_max_age_seconds','第一档最大种龄（秒）','number','默认 180s'],
        ['race_tier2_max_size_gb','第二档最大体积（GB）','number','默认 4GB'],
        ['race_tier2_max_age_seconds','第二档最大种龄（秒）','number','默认 150s'],
        ['race_tier3_max_size_gb','第三档最大体积（GB）','number','默认 6GB'],
        ['race_tier3_max_age_seconds','第三档最大种龄（秒）','number','默认 90s'],
        ['race_candidates_per_run','Race 每轮最多探测新 ID','number','默认 6，受 detail API 配额保护'],
        ['experiment_enabled','记录 Race vs Trend 战绩','bool','建议开启'],
      ]
    },
    {
      title:'Trend 趋势车道', desc:'没有命中 Race 的候选继续使用 S/L、需求比、趋势和动态分数判断。',
      fields:[
        ['min_size_gb','全局最小体积（GB）','number',''],
        ['max_size_gb','全局最大体积（GB）','number','Race 和 Trend 都不能超过它'],
        ['max_age_seconds','黄金窗口（秒，软）','number','超过后仍可晚起量救援'],
        ['hard_max_age_seconds','绝对观察上限（秒）','number','普通 Trend 超过后永久放弃'],
        ['min_leechers','基础最少 Leecher','number','实际门槛会随种龄动态变化'],
        ['max_seeders','Seeder 竞争参考线','number','软惩罚，不是一刀切'],
        ['min_demand','基础最低需求比 L/(S+1)','number','实际门槛会随种龄变化'],
        ['min_score','基础最低评分','number','体积与种龄会动态修正'],
      ]
    },
    {
      title:'资源与等待队列', desc:'控制小盘如何分配空间，以及曾经因资源不足错过的候选怎么回来补抓。',
      fields:[
        ['max_active_downloads','同时下载数','number','建议 1'],
        ['data_cap_gb','盒子逻辑数据上限（GB）','number','按实际完成数据量计算'],
        ['disk_reserve_gb','磁盘安全预留（GB）','number',''],
        ['resource_queue_recheck_seconds','资源队列复查间隔（秒）','number',''],
        ['resource_queue_checks_per_run','每轮最多复查队列候选','number',''],
        ['resource_queue_max_items','资源等待队列上限','number',''],
        ['resource_queue_hard_max_age_seconds','资源等待最长宽限（秒）','number','通过评分但没位置的种可等更久'],
      ]
    },
    {
      title:'API 与月流量', desc:'盒子模式有自己的 API 预算，不再使用原项目 40/小时总限额。',
      fields:[
        ['detail_limit_per_hour','torrent/detail 每小时预算','number','建议不超过 90'],
        ['download_limit_per_hour','获取种子每小时预算','number','建议不超过 80'],
        ['traffic_budget_gb','月流量软预算（GB）','number','到线停止新增'],
        ['traffic_hard_stop_gb','月流量硬停止（GB）','number','到线暂停盒子任务'],
        ['billing_reset_day','账期重置日','number','1~31'],
      ]
    },
    {
      title:'清理与卡死保护', desc:'把已经吃干净或根本下不动的任务及时赶走，避免堵住唯一下载槽。',
      fields:[
        ['auto_cleanup','自动清理','bool',''],
        ['cleanup_ratio','满分享率淘汰','number','受限新种默认 2.85'],
        ['cleanup_idle_minutes','完成后空闲淘汰（分钟）','number',''],
        ['cleanup_min_seed_minutes','至少做种多久再判空闲（分钟）','number',''],
        ['cleanup_stalled_zero_minutes','0B stalledDL 清理（分钟）','number',''],
        ['cleanup_stalled_partial_minutes','部分下载无进展清理（分钟）','number',''],
      ]
    },
  ];

  const byId=id=>document.getElementById(id);
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const allFields=sections.flatMap(s=>s.fields);

  function fieldHtml(f){
    const [key,label,type,hint]=f;
    if(type==='bool'){
      return `<div class="form-item"><label>${esc(label)}</label><select id="bs_${key}"><option value="1">开启</option><option value="0">关闭</option></select>${hint?`<small class="muted">${esc(hint)}</small>`:''}</div>`;
    }
    const step=type==='number'?' step="any"':'';
    return `<div class="form-item"><label>${esc(label)}</label><input id="bs_${key}" type="${type}"${step} />${hint?`<small class="muted">${esc(hint)}</small>`:''}</div>`;
  }

  function pageHtml(){
    return `<section class="page" id="page-boxsettings"><div class="page-inner">
      <div class="page-head"><div><h2>盒子设置</h2><p>Race / Trend / 资源 / API / 清理参数集中管理</p></div><button class="btn" id="bsBack">返回盒子抢流</button></div>
      <div class="box-config-note" style="margin-bottom:12px"><strong>配置原则：</strong>策略行为尽量可调；更底层的数学权重暂留在代码里，避免把常用设置做成几十个难以理解的旋钮。</div>
      <div id="bsMsg" class="box-alert"></div>
      ${sections.map((s,i)=>`<details class="cfg-fold" ${i<2?'open':''}><summary class="cfg-fold-hd">${esc(s.title)} <span class="muted">· ${esc(s.desc)}</span></summary><div class="cfg-fold-body"><div class="form-grid">${s.fields.map(fieldHtml).join('')}</div></div></details>`).join('')}
      <div class="card" style="margin-top:12px"><div class="form-actions" style="padding:16px">
        <button class="btn btn-primary" id="bsSave">保存全部设置</button>
        <button class="btn" id="bsReload">重新读取</button>
        <button class="btn" id="bsTestQb">测试 qBittorrent</button>
        <button class="btn" id="bsResetTraffic">重置流量基线</button>
      </div></div>
    </div></section>`;
  }

  function msg(text,type='info'){
    const n=byId('bsMsg'); if(!n)return;
    n.className='box-alert show '+type; n.textContent=text;
  }

  async function load(){
    const c=await api('/api/box/config');
    for(const [key,,type] of allFields){
      const n=byId('bs_'+key); if(!n)continue;
      if(key==='rss_url'){
        n.value=''; n.placeholder=c.rss_url_set?'已保存 RSS；留空保持不变':'粘贴 M-Team RSS 私有地址'; continue;
      }
      if(key==='qbit_password'){
        n.value=''; n.placeholder=c.qbit_password_set?'已保存密码；留空保持不变':'输入 qBit WebUI 密码'; continue;
      }
      if(type==='bool') n.value=c[key]?'1':'0';
      else if(c[key]!==undefined && c[key]!==null) n.value=c[key];
    }
    msg('设置已读取','ok');
  }

  async function save(){
    const body={};
    for(const [key,,type] of allFields){
      const n=byId('bs_'+key); if(!n)continue;
      if(type==='bool'){ body[key]=n.value==='1'; continue; }
      const v=String(n.value??'').trim();
      if(!v) continue;
      body[key]=type==='number'?Number(v):v;
    }
    const r=await api('/api/box/config',{method:'POST',body:JSON.stringify(body)});
    if(!r||r.ok!==true) throw new Error('保存失败');
    await load();
    msg('全部设置已保存；策略下一轮立即使用新参数','ok');
  }

  function install(){
    if(byId('page-boxsettings')) return;
    try{ titles.boxsettings='盒子设置'; pageGroup.boxsettings='task'; }catch(_e){}

    const oldCfg=document.querySelector('#page-box .box-config-block');
    if(oldCfg) oldCfg.style.display='none';

    const main=document.querySelector('.admin-main');
    if(main) main.insertAdjacentHTML('beforeend',pageHtml());

    const taskBody=document.querySelector('.nav-sub[data-group="task"] .nav-sub-body');
    if(taskBody){
      const btn=document.createElement('button');
      btn.type='button'; btn.className='nav-item'; btn.dataset.page='boxsettings';
      btn.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1.1V21h-4v-.09A1.7 1.7 0 0 0 8.6 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1.1-.4H3v-4h.09A1.7 1.7 0 0 0 4.6 8.6a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .4-1.1V3h4v.09A1.7 1.7 0 0 0 15.4 4.6a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.4 9c.15.37.36.7.6 1 .28.33.67.53 1.1.6H21v4h-.09a1.7 1.7 0 0 0-1.51.4z"/></svg>盒子设置';
      btn.addEventListener('click',()=>{goPage('boxsettings');load().catch(e=>msg(e.message,'bad'));});
      taskBody.appendChild(btn);
    }

    byId('bsBack')?.addEventListener('click',()=>goPage('box'));
    byId('bsSave')?.addEventListener('click',()=>save().catch(e=>msg(e.message,'bad')));
    byId('bsReload')?.addEventListener('click',()=>load().catch(e=>msg(e.message,'bad')));
    byId('bsTestQb')?.addEventListener('click',async()=>{try{const r=await api('/api/box/qbit/test',{method:'POST'});msg(`qBittorrent 正常 · ${r.version||''}`,'ok')}catch(e){msg(e.message,'bad')}});
    byId('bsResetTraffic')?.addEventListener('click',async()=>{try{await api('/api/box/traffic/reset',{method:'POST'});msg('流量基线已重置','ok')}catch(e){msg(e.message,'bad')}});
  }

  function waitInstall(){
    if(document.querySelector('.admin-main') && document.querySelector('#page-box')) install();
    else setTimeout(waitInstall,100);
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',waitInstall);
  else waitInstall();
})();
