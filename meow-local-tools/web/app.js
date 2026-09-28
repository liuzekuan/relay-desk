'use strict';
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = (name) => `<i data-lucide="${name}"></i>`;
const icons = () => lucide.createIcons();
const statuses = {queued:'排队中',starting:'正在发起',running:'检测中',stopping:'正在停止',complete:'已完成',partial:'部分失败',failed:'失败',stopped:'已停止',cancelled:'未发起',unknown:'状态未知'};
const verdicts = {match:'指向申报模型',mismatch:'指向其他模型',insufficient:'证据不足'};
const pending = new Set(['queued','starting','running','stopping','unknown']);
const retryable = new Set(['failed','partial','stopped','cancelled']);
let token = '', state = null, view = 'desk', filter = 'all', selected = new Set(), batchId = null;
let mode = 'gpt', switching = false;
const family = (value=mode) => value === 'claude' ? 'Claude' : 'OpenAI';
const protocol = () => mode === 'claude' ? 'Claude / Messages' : 'OpenAI / Responses';
let renderKey = '', estimate = null, estimateKey = '', estimateSerial = 0, inFlight = false, editId = null, editRevision = '', confirmAction = null, toastTimer;
const date = (value) => value ? new Date(value).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '';
const pct = (value) => value == null ? '—' : `${(value * 100).toFixed(2)}%`;
const duration = (value) => `${Math.floor((value || 0)/60)}:${String(Math.round(value || 0)%60).padStart(2,'0')}`;
const providers = () => state?.config?.providers || [];
const batch = () => batchId ? state?.history.find(b => b.id === batchId) : state?.current || state?.history[0];
const needsAttention = (r) => r && (retryable.has(r.status) || r.status === 'unknown' || r.verdict === 'mismatch' || (r.status === 'complete' && r.verdict === 'insufficient'));
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 4500); }
async function api(path, data) {
  const response = await fetch('/api/' + path, {method:data === undefined ? 'GET':'POST',headers:{'X-Desk-Token':token,...(data === undefined ? {}:{'Content-Type':'application/json'})},body:data === undefined ? undefined:JSON.stringify({mode,...data})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '操作失败，请重试。');
  return result;
}
async function action(fn) { try { await fn(); } catch(error) { toast(error.message); } }
async function switchMode(next) {
  if (next === mode || switching || inFlight) return;
  switching = true;
  mode = next; state = null; selected.clear(); batchId = null; filter = 'all'; renderKey = '';
  estimate = null; estimateKey = ''; estimateSerial++;
  $('start').disabled = true; $('add').disabled = true; $('retry').disabled = true;
  $('copy').disabled = true; $('export').disabled = true;
  $('table-container').innerHTML = empty('正在读取 ' + family() + ' 配置', '');
  document.querySelectorAll('[data-mode]').forEach(el => el.disabled = true);
  try {
    await refresh();
  } finally { switching = false; document.querySelectorAll('[data-mode]').forEach(el=>el.disabled=false); if(state) render(); }
}
function setView(next) {
  view = next; filter = 'all'; renderKey = '';
  if (view === 'desk') batchId = null;
  render();
}
function statusTag(r) {
  if (!r) return '<span class="tag">未检测</span>';
  const cls = ['running','starting','queued','stopping'].includes(r.status) ? 'running' : r.status === 'complete' ? 'good' : r.status === 'failed' ? 'bad' : 'warn';
  return `<span class="tag ${cls}"><span class="status-dot ${r.status==='running'?'pulse':''}"></span>${esc(statuses[r.status] || r.status)}</span>`;
}
function outcome(r) {
  if (!r?.valid) return '<span class="subtext">—</span>';
  return `<span class="tag ${r.verdict==='match'?'good':r.verdict==='mismatch'?'bad':'warn'}">${pending.has(r.status)?'暂时': ''}${esc(verdicts[r.verdict] || '证据不足')}</span><span class="subtext">${esc(r.top || '—')}</span>`;
}
function rowButton(name, title, actionName, id, disabled=false) { return `<button class="icon-button" data-action="${actionName}" data-id="${esc(id)}" title="${title}" aria-label="${title}" ${disabled?'disabled':''}>${icon(name)}</button>`; }
function latestResults(records, selectedMode, activeBatchId=null) {
  const results = new Map();
  const ordered = records.filter(b=>b && (b.mode||'gpt')===selectedMode)
    .sort((a,b)=>Date.parse(b.created_at)-Date.parse(a.created_at));
  for (const record of ordered) {
    for (const row of record.rows) {
      if (results.has(row.provider_id)) continue;
      const unstarted = !row.started_at && !row.session_id;
      if (unstarted && (row.status==='cancelled' || (row.status==='queued' && record.id!==activeBatchId))) continue;
      results.set(row.provider_id, {row, batch:record});
    }
  }
  return results;
}
function resultEntries() {
  if (view==='history') {
    const record=batch();
    return (record?.rows||[]).map(row=>({row,batch:record}));
  }
  const latest=latestResults([state?.current,...(state?.history||[])],mode,state?.busy?state.current?.id:null);
  return providers().map(p=>latest.get(p.id)).filter(Boolean);
}
function resultTime(entry) {
  if (!entry || (!entry.row.started_at && ['queued','cancelled'].includes(entry.row.status))) return null;
  return entry.row.started_at || entry.batch.created_at;
}
function detectionDateCell(value) {
  if (!value) return '—';
  const time = new Date(value), pad = n => String(n).padStart(2, '0');
  const day = `${time.getFullYear()}-${pad(time.getMonth()+1)}-${pad(time.getDate())}`;
  const clock = `${pad(time.getHours())}:${pad(time.getMinutes())}`;
  return `<time datetime="${esc(value)}" title="${esc(time.toLocaleString('zh-CN'))}">${day}<span class="subtext">${clock}</span></time>`;
}
function renderTable() {
  const current = batch(), entries=resultEntries(), rows=entries.map(e=>e.row), byId = new Map(rows.map(r => [r.provider_id,r]));
  const list = providers().filter(p => filter !== 'attention' || needsAttention(byId.get(p.id)));
  if (view === 'history' && !batchId) {
    const history = state.history;
    if (!history.length) return empty('还没有历史批次', '完成检测后，结果会保存在这里。');
    return `<table class="history"><colgroup><col><col><col><col><col></colgroup><thead><tr><th>检测时间</th><th>验证模型</th><th>中转</th><th>状态</th><th>基准版本</th></tr></thead><tbody>${history.map(b => `<tr class="history-row" data-batch="${esc(b.id)}"><td><button class="text-button history-date" data-batch="${esc(b.id)}">${esc(date(b.created_at))}${icon('arrow-up-right')}</button></td><td>${esc(b.claimed_model)}</td><td class="numeric">${b.rows.length} 个</td><td><span class="tag ${b.finished_at?'good':'warn'}">${b.finished_at?'已结束':'待确认'}</span></td><td><span class="subtext">${esc(b.benchmark.version)}</span></td></tr>`).join('')}</tbody></table>`;
  }
  const historic = view === 'history';
  const lastDates = new Map(entries.map(e=>[e.row.provider_id,resultTime(e)]));
  const display = historic ? rows.map(r => ({id:r.provider_id,name:r.name,api:'',result:r})) : list.map(p => ({...p,result:byId.get(p.id)}));
  if (!display.length) return empty(filter === 'attention'?'没有需要关注的中转':'还没有中转配置', filter==='attention'?'当前列表没有异常结果。':'', filter !== 'attention' && !historic);
  const managing = view === 'providers';
  const allChecked = display.length && display.every(p => selected.has(p.id));
  return `<table class="${managing?'providers':'results'}"><colgroup><col class="check-col"><col class="name-col">${managing?'<col class="api-col"><col class="key-col">':'<col class="status-col"><col class="outcome-col"><col class="score-col"><col class="sample-col"><col class="time-col"><col class="date-col">'}<col class="actions-col"></colgroup><thead><tr><th>${historic?'':`<input type="checkbox" id="select-all" aria-label="选择当前列表全部中转" ${allChecked?'checked':''}>`}</th><th>中转名称</th>${managing?'<th>API 地址</th><th>API Key</th>':`<th>状态</th><th>检测结论 / 候选</th><th>匹配度</th><th>有效样本</th><th>耗时</th><th>${historic?'检测日期':'最近检测日期'}</th>`}<th class="actions-heading">操作</th></tr></thead><tbody>${display.map((p,i) => {
    const r=p.result;
    return `<tr class="${selected.has(p.id)&&!historic?'selected':''}"><td>${historic?'':`<input type="checkbox" data-select="${esc(p.id)}" aria-label="选择 ${esc(p.name)}" ${selected.has(p.id)?'checked':''}>`}</td><td><div class="provider-cell"><div class="monogram" aria-hidden="true">${esc(p.name.slice(0,2))}</div><div><span class="provider-name">${esc(p.name)}</span>${managing?'':`<span class="subtext">${esc(p.api ? new URL(p.api).host : current.request_model)}</span>`}</div></div></td>${managing?`<td><span class="subtext">${esc(p.api)}</span></td><td><span class="tag good">${icon('key-round')}已保存</span></td>`:`<td>${statusTag(r)}${r&&pending.has(r.status)?`<span class="subtext numeric">${r.completed || 0} / ${r.planned || 0}</span>`:''}</td><td>${outcome(r)}</td><td><span class="score">${pct(r?.score)}</span>${r?.score!=null?`<progress class="score-bar" value="${r.score}" max="1" aria-label="匹配度"></progress>`:''}</td><td class="numeric">${r?`${r.valid || 0}<span class="subtext-inline"> / ${r.planned || 0}</span>`:'—'}${r?.errors?`<span class="subtext">${r.errors} 次失败</span>`:''}</td><td class="numeric">${r?duration(r.elapsed):'—'}</td><td class="numeric detection-date">${detectionDateCell(lastDates.get(p.id))}</td>`}<td><div class="row-actions">${historic?rowButton('panel-right-open','查看详情','details',p.id):managing?rowButton('pencil','编辑中转','edit',p.id,state.busy)+rowButton('trash-2','删除中转','delete',p.id,state.busy):rowButton('play','检测此中转','run',p.id,state.busy || !state.package)+rowButton('panel-right-open','查看详情','details',p.id,!r)}</div></td></tr>`;
  }).join('')}</tbody></table>`;
}
function empty(title, description, add=false) {return `<div class="empty">${icon('scan-line')}<strong>${esc(title)}</strong><p>${esc(description)}</p>${add?'<button data-action="add" class="primary">新增中转</button>':''}</div>`;}
function render() {
  if (!state) return;
  const config=state.config, current=batch(), entries=resultEntries(), rows=entries.map(e=>e.row), historic=view==='history', managing=view==='providers';
  const runningHere = state.busy && state.active_mode === mode;
  const titles={desk:family()+' 检测',providers:family()+' 中转配置',history:family()+(batchId?' 批次详情':' 历史批次')};
  $('page-title').textContent=titles[view]; $('breadcrumb').textContent=titles[view];
  $('page-meta').textContent=historic ? batchId?`${date(current?.created_at)} · ${current?.request_model || ''}`:`${state.history.length} 个本地批次` : managing?`${providers().length} 个已配置中转 · ${protocol()}`:`请求模型 ${$('claimed').value || '—'} · ${protocol()} · 跟随验证模型`;
  document.title = 'Relay Desk · ' + family() + ' 检测';
  document.querySelectorAll('[data-protocol-label]').forEach(el=>el.textContent=protocol());
  document.querySelectorAll('[data-mode]').forEach(el=>{el.classList.toggle('active',el.dataset.mode===mode);el.setAttribute('aria-pressed',String(el.dataset.mode===mode));el.disabled=switching||inFlight;});
  document.querySelectorAll('[data-view]').forEach(el => el.classList.toggle('active',el.dataset.view===view));
  $('provider-count').textContent=providers().length;
  $('connection').textContent=state.connecting?'正在连接':state.package?'检测器已连接':'检测器未连接';
  $('connection').className='connection '+(state.package?'ready':state.connecting?'':'failed');
  $('demo').hidden=!state.demo; $('error').hidden=!state.error; $('error').textContent=state.error;
  const unresolved=state.history.find(b=>b.rows.some(r=>pending.has(r.status)));
  $('unresolved').hidden=state.busy || !state.blocked_mode;
  $('unresolved').querySelector('span').textContent=family(state.blocked_mode)+' 有尚未确认结束的历史任务';
  $('recover').textContent=state.blocked_mode && state.blocked_mode!==mode?'切换至 '+family(state.blocked_mode):'恢复查询';
  $('acknowledge').hidden=!unresolved?.rows.some(r=>['unknown','starting'].includes(r.status)&&!r.session_id);
  $('run-settings').hidden=view!=='desk';
  $('add').hidden=historic; $('add').disabled=state.busy || !config;
  $('run-settings').querySelectorAll('select').forEach(el => el.disabled=state.busy);
  $('stop').hidden=!state.busy;
  $('stop').innerHTML=icon('square')+'停止 '+family(state.active_mode)+' 批次';
  $('start').disabled=state.busy || !state.package || !selected.size || inFlight || !!state.blocked_mode;
  $('start').querySelector('span').textContent=state.busy?family(state.active_mode)+' 检测中':'开始检测';
  $('selected-count').textContent=selected.size;
  $('all-count').textContent=providers().length;
  $('attention-count').textContent=providers().filter(p=>needsAttention(rows.find(r=>r.provider_id===p.id))).length;
  $('filters').hidden=historic;
  document.querySelectorAll('[data-filter]').forEach(el=>el.classList.toggle('active',el.dataset.filter===filter));
  $('retry').hidden=view!=='desk'; $('retry').disabled=state.busy || !rows.some(r=>selected.has(r.provider_id)&&retryable.has(r.status));
  $('copy').hidden=managing || (historic&&!batchId); $('export').hidden=$('copy').hidden;
  $('copy').disabled=!rows.length; $('export').disabled=!rows.length;
  $('baseline').textContent=state.package?`基准 ${state.package.version}`:'';
  updateBudget();
  $('batch-summary').hidden=managing || !rows.length || (historic&&!batchId);
  $('batch-summary').innerHTML=rows.length?`<span class="summary-title">${icon(runningHere?'activity':'list-checks')}${historic?'本次检测':'最近结果'}</span><span><strong>${rows.filter(r=>r.status==='complete'||r.status==='partial').length}/${historic?rows.length:providers().length}</strong>已完成</span><span><strong>${rows.filter(r=>r.valid&&r.verdict==='match').length}</strong>指向申报</span><span><strong>${rows.filter(needsAttention).length}</strong>需关注</span>${historic?'<button class="text-button" id="back-history">返回历史</button>':''}`:'';
  $('footer-status').textContent=state.busy?family(state.active_mode)+' 批次执行中 · 关闭页面后后台继续检测':historic?'结果已保存在本机':`已选择 ${selected.size} 个中转`;
  $('batch-date').textContent=current && historic && batchId?`${date(current.created_at)} · ${current.claimed_model}`:'';
  const nextKey=JSON.stringify([view,filter,batchId,state.busy,state.package?.version,providers(),entries,[...selected],historic?state.history.map(b=>[b.id,b.finished_at]):[]]);
  if (nextKey!==renderKey) { $('table-container').innerHTML=renderTable(); renderKey=nextKey; }
  icons();
}
async function updateEstimate() {
  if (!state?.package) return;
  const key=mode+':'+state.package.content_sha256+':'+$('tier').value;
  if (key===estimateKey) return;
  estimateKey=key; estimate=null; const serial=++estimateSerial; updateBudget();
  try { const value=await api('estimate',{tier:$('tier').value}); if (serial===estimateSerial) estimate=value; }
  catch { if(serial===estimateSerial) { estimateKey=''; estimate=null; } }
  updateBudget();
}
function updateBudget() { $('budget').textContent=estimate?`已选 ${selected.size} 个 · 每个 ${estimate.logical_requests} 个样本 · 含重试最多 ${selected.size * estimate.maximum_http_attempts} 次请求`:state?.package?'正在读取请求预算':'等待检测器就绪'; }
async function refresh() {
  const requestedMode=mode;
  const snapshot=await api('state?mode='+requestedMode);
  if(requestedMode!==mode) return;
  state=snapshot;
  selected=new Set([...selected].filter(id=>providers().some(p=>p.id===id)));
  const options=state.package?.models.filter(m=>!m.reference_only && m.id!=='other').map(m=>m.id)||[];
  if ($('claimed').dataset.models!==JSON.stringify(options)) {
    const previous=$('claimed').value;
    $('claimed').innerHTML=options.map(id=>`<option value="${esc(id)}">${esc(id)}</option>`).join('');
    $('claimed').value=options.includes(previous)?previous:options.includes(state.config?.model)?state.config.model:options[0]||'';
    $('claimed').dataset.models=JSON.stringify(options);
  }
  if (state.busy && state.active_mode===mode && options.includes(state.current?.claimed_model)) {
    $('claimed').value=state.current.claimed_model;
  }
  render(); await updateEstimate();
}
async function poll() {try {await refresh();} catch { $('connection').textContent='工作台连接中断'; $('connection').className='connection failed'; $('error').hidden=false; $('error').textContent='本地工作台连接中断。请重新打开启动文件，再刷新页面。'; $('start').disabled=true; } finally {setTimeout(poll,1500);} }
function editProvider(id=null) {
  if(!state || switching) return;
  const p=providers().find(p=>p.id===id); editId=id; editRevision=state.config.revision;
  $('provider-title').textContent=p?'编辑中转':'新增中转'; $('provider-name').value=p?.name||''; $('provider-api').value=p?.api||'';
  $('provider-key').value=''; $('provider-key').type='password'; $('provider-key').required=!p;
  $('provider-key').placeholder=p?'留空保留已保存的 Key':'输入 API Key'; $('key-status').textContent=p?'已保存':'';
  $('provider-error').textContent=''; $('provider-dialog').showModal(); $('provider-name').focus();
}
function confirm(title,message,label,fn) { $('confirm-title').textContent=title; $('confirm-message').textContent=message; $('confirm-ok').textContent=label; confirmAction=fn; $('confirm-dialog').showModal(); $('confirm-cancel').focus(); }
async function start(ids) {
  if(inFlight || !state || switching) return;
  inFlight=true; render();
  try { await api('start',{ids,revision:state.config.revision,claimed:$('claimed').value,tier:$('tier').value,parallel:Number($('parallel').value)}); batchId=null; view='desk'; await refresh(); toast('已发起所选中转检测'); }
  finally {inFlight=false;render();}
}
function showDetails(id) {
  const entry=resultEntries().find(e=>e.row.provider_id===id); if(!entry) return;
  const {batch:b,row:r}=entry;
  $('details-title').textContent=r.name;
  const fields=[['状态',statuses[r.status]],['验证模型',b.claimed_model],['请求模型',b.request_model],['基准版本',b.benchmark.version],['结论',r.valid?verdicts[r.verdict]:'证据不足'],['有效样本',`${r.valid || 0} / ${r.planned || 0}`],['失败请求',r.errors || 0],['请求 / 重试',`${r.attempts || 0} / ${r.retries || 0}`],['耗时',duration(r.elapsed)],['申报匹配度变化',r.delta==null?'无同条件历史':`${r.delta>0?'+':''}${r.delta.toFixed(2)} pp`]];
  $('details-body').innerHTML=`<dl>${fields.map(([k,v])=>`<dt>${k}</dt><dd>${esc(v)}</dd>`).join('')}</dl><h3>候选匹配度</h3>${Object.entries(r.matches||{}).sort((a,b)=>b[1]-a[1]).map(([name,score])=>`<div class="candidate"><span>${esc(name)}${r.thresholds?.[name]!=null?`<span class="subtext">强指向线 ${pct(r.thresholds[name])}</span>`:''}</span><strong>${pct(score)}</strong></div>`).join('') || '<p class="detail-note">暂无有效结果</p>'}<p class="detail-note">${esc(r.note || '')}</p>${r.reasons?.length?`<p class="detail-note">${esc(r.reasons.join(' · '))}</p>`:''}`;
  $('details-dialog').showModal();
}
function summaryRows() {return resultEntries().map(entry=>{const {row:r,batch:b}=entry;return [r.name,statuses[r.status],r.valid?verdicts[r.verdict]:'证据不足',r.top||'—',pct(r.score),`${r.valid||0}/${r.planned||0}`,r.errors||0,duration(r.elapsed),r.delta==null?'—':r.delta.toFixed(2),resultTime(entry)?new Date(resultTime(entry)).toLocaleString('zh-CN'):'—',b.request_model,b.claimed_model,b.benchmark.version,b.tier];});}
const headers=['中转','状态','结论','最匹配候选','匹配度','有效样本','失败请求','耗时','申报变化(pp)','检测时间','请求模型','验证模型','基准版本','档位'];
document.querySelectorAll('[data-mode]').forEach(el=>el.onclick=()=>action(()=>switchMode(el.dataset.mode)));
document.querySelectorAll('[data-view]').forEach(el=>el.onclick=()=>setView(el.dataset.view));
document.querySelectorAll('[data-filter]').forEach(el=>el.onclick=()=>{filter=el.dataset.filter;render();});
$('add').onclick=()=>editProvider();
$('refresh').onclick=()=>action(async()=>{estimateKey=''; await api('connect',{}); await refresh(); toast('已重新读取配置');});
$('tier').onchange=()=>updateEstimate();
$('claimed').onchange=()=>render();
$('start').onclick=()=>action(()=>start([...selected]));
$('stop').onclick=()=>confirm('停止当前批次？','已完成的结果会保留，排队中的中转不再发起。','停止批次',()=>api('stop',{}));
$('retry').onclick=()=>action(()=>start(resultEntries().map(e=>e.row).filter(r=>selected.has(r.provider_id)&&retryable.has(r.status)).map(r=>r.provider_id)));
$('recover').onclick=()=>action(async()=>{if(state.blocked_mode!==mode){await switchMode(state.blocked_mode);return;}await api('recover',{});await refresh();});
$('acknowledge').onclick=()=>confirm('已核对官方检测器中的任务？','请先打开官方检测器，确认未知任务已结束或未启动。解除阻塞后，可以手动发起新检测。','已核对，解除阻塞',()=>api('acknowledge',{confirmed:true}));
$('confirm-cancel').onclick=()=>$('confirm-dialog').close();
$('confirm-ok').onclick=()=>action(async()=>{$('confirm-ok').disabled=true;try{await confirmAction();$('confirm-dialog').close();await refresh();}finally{$('confirm-ok').disabled=false;}});
document.querySelectorAll('.close-dialog').forEach(el=>el.onclick=()=>el.closest('dialog').close());
$('provider-dialog').addEventListener('close',()=>{$('provider-key').value='';});
$('toggle-key').onclick=()=>{$('provider-key').type=$('provider-key').type==='password'?'text':'password';$('toggle-key').setAttribute('aria-label',$('provider-key').type==='password'?'显示输入的 Key':'隐藏输入的 Key');};
$('provider-form').onsubmit=async event=>{event.preventDefault();const submit=event.submitter;submit.disabled=true;try{await api('provider/'+(editId?'edit':'add'),{revision:editRevision,id:editId,name:$('provider-name').value,api:$('provider-api').value,key:$('provider-key').value});$('provider-dialog').close();await refresh();toast('中转已保存');}catch(error){$('provider-error').textContent=error.message;}finally{submit.disabled=false;}};
$('table-container').onchange=event=>{const el=event.target;if(el.dataset.select){el.checked?selected.add(el.dataset.select):selected.delete(el.dataset.select);}else if(el.id==='select-all'){document.querySelectorAll('[data-select]').forEach(box=>el.checked?selected.add(box.dataset.select):selected.delete(box.dataset.select));}render();};
$('table-container').onclick=event=>{const b=event.target.closest('[data-batch]');if(b){batchId=b.dataset.batch;render();return;}const button=event.target.closest('[data-action]');if(!button)return;const {action:kind,id}=button.dataset;
  if(kind==='add'||kind==='edit')editProvider(id||null);
  if(kind==='run')action(()=>start([id]));
  if(kind==='details')showDetails(id);
  if(kind==='delete'){const p=providers().find(p=>p.id===id), revision=state.config.revision;confirm(`删除「${p.name}」？`,'该中转的 API 和 Key 将从配置中删除，历史检测结果仍保留。','删除中转',()=>api('provider/delete',{id,revision}));}
};
$('batch-summary').onclick=event=>{if(event.target.closest('#back-history')){batchId=null;render();}};
$('copy').onclick=()=>action(async()=>{await navigator.clipboard.writeText(`${family()} 中转检测 | ${view==='history'?date(batch().created_at):'各中转最近结果'}\n`+[headers,...summaryRows()].map(r=>r.join('\t')).join('\n'));toast('已复制结果汇总');});
$('export').onclick=()=>{const safe=v=>/^[=+\-@\t\r\n]/.test(String(v))?"'"+v:v;const csv='\uFEFF'+[headers,...summaryRows()].map(row=>row.map(v=>'"'+String(safe(v)).replaceAll('"','""')+'"').join(',')).join('\r\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=`relay-${view==='history'?batch().id.slice(0,8):mode+'-latest'}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);};
icons();
action(async()=>{const bootstrap=await api('bootstrap');if(bootstrap.api_version!==3){$('error').hidden=false;$('error').textContent='后台版本已更新，请重新双击 start.cmd（Mac 使用 start.command）打开新版工作台。';return;}token=bootstrap.token;await poll();});
