'use strict';
const $ = id => document.getElementById(id);
const escapeHtml = val => String(val ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let library=[], sessions=[], chosenSession=null;
let toastTimeout;
const humanDuration = secs => {
  if(!Number.isFinite(Number(secs)) || Number(secs)<=0) return '未识别时长';
  const s=Math.round(Number(secs)); return `${Math.floor(s/3600)}:${String(Math.floor(s%3600/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
};
const durationSum = items => (items||[]).reduce((a,v)=>a+(Number(v.duration)||0),0);
const slotName = x=>x==='AM'?'上午':'下午';
const dayTitle = s=>`${s.day} · ${slotName(s.slot)}`;
function toast(message,isError=false){const el=$('toast');el.textContent=message;el.style.background=isError?'#af243b':'#162a49';el.classList.remove('hidden');clearTimeout(toastTimeout);toastTimeout=setTimeout(()=>el.classList.add('hidden'),5000);}
async function api(url,method='GET',body){
  const options={method,cache:'no-store'};
  if(body!==undefined){options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(body);}
  const res=await fetch(url,options);
  let response;try{response=await res.json();}catch{throw new Error('请求失败：HTTP '+res.status);}
  if(!res.ok)throw new Error(response.error||'请求失败');
  return response;
}
async function guarded(cb){try{await cb();}catch(err){console.error(err);toast(err.message,true);}}
async function reload(){
  const [v,p] = await Promise.all([api('/api/library'),api('/api/plan')]);
  library=v.videos;sessions=p.sessions;
  $('serviceTime').textContent='服务时间：'+new Date(p.server_now_ms).toLocaleString('zh-CN',{hour12:false});
  $('videosCount').textContent=library.length;
  $('libraryCount').textContent=`(${library.length})`;
  $('sessionsCount').textContent=sessions.length;
  $('unknownCount').textContent=library.filter(v=>!v.duration).length;
  renderLibrary();renderSchedule();renderEditor();
}
function renderLibrary(){
  const filter=$('searchVideos').value.trim().toLowerCase();
  const visible=library.filter(v=>v.title.toLowerCase().includes(filter)||v.path.toLowerCase().includes(filter));
  const el=$('libraryList');
  if(!visible.length){el.innerHTML='<div class="empty-state">还没有匹配的视频。先上传或导入本机文件夹。</div>';return;}
  el.innerHTML=visible.map(v=>`<div class="library-video" draggable="true" data-video-id="${v.id}">
    <div class="library-info"><strong title="${escapeHtml(v.path)}">${escapeHtml(v.title)}</strong>
      <small>${escapeHtml(humanDuration(v.duration))} · ${escapeHtml(v.source)} ${!v.duration?' · ⚠ 未识别':''}</small></div>
    <button class="btn btn-secondary btn-sm" data-action="add" data-id="${v.id}" title="加入已选场次">＋加入</button>
    <button class="btn btn-ghost btn-sm" data-action="editVideo" data-id="${v.id}" title="修改名称或时长">编辑</button></div>`).join('');
}
function renderSchedule(){
  const el=$('scheduleCards');
  if(!sessions.length){el.innerHTML='<div class="empty-state">暂未生成课程场次。</div>';return;}
  el.innerHTML=sessions.map(s=>{
    const sum=durationSum(s.videos);
    const target=(new Date(`${s.day}T${s.end_time}:00`)-new Date(`${s.day}T${s.start_time}:00`))/1000;
    const unknown=s.videos.some(v=>!v.duration);
    const issue=unknown?'⚠ 有未知时长':!s.videos.length?'未编排':Math.abs(target-sum)>1?`相差 ${humanDuration(Math.abs(target-sum))}`:'时长匹配';
    return `<button class="schedule-card ${chosenSession===s.id?'active':''}" data-session="${s.id}">
      <strong>${escapeHtml(dayTitle(s))}</strong><span>${s.start_time}–${s.end_time} · ${s.videos.length}个视频</span>
      <small class="${(unknown||Math.abs(target-sum)>1)?'issue':''}">${escapeHtml(issue)}</small></button>`;
  }).join('');
}
function editorSession(){return sessions.find(s=>s.id===chosenSession);}
function renderEditor(){
  const s=editorSession(),el=$('editor');
  if(!s){el.classList.add('empty-state');el.innerHTML='选择一个上午或下午场次，开始编排视频。';return;}
  el.classList.remove('empty-state');
  const sum=durationSum(s.videos);
  const target=(new Date(`${s.day}T${s.end_time}:00`)-new Date(`${s.day}T${s.start_time}:00`))/1000;
  const difference=target-sum;
  const unknown=s.videos.some(v=>!v.duration);
  const msg=unknown?'部分视频时长尚未识别，不能可靠地按时间恢复播放。请先进行视频时长识别。':
    !s.videos.length?'本场次还没有视频。':
    Math.abs(difference)<=1?'视频总时长与场次时间基本相符。':
    difference>0?`视频总时长不足 ${humanDuration(difference)}，提早播完后会显示“本时段课程已播放完毕”。`:
    `视频时长超过场次 ${humanDuration(-difference)}，到结束时间将强制停止，后续视频可能播不完。`;
  el.innerHTML=`<div class="editor-title">${escapeHtml(dayTitle(s))}</div>
    <div class="editor-times">
      <label>日期<input id="editDay" type="date" value="${escapeHtml(s.day)}"></label>
      <label>时段<select id="editSlot" style="height:39px;width:100%;border:1px solid #d6dfec;border-radius:9px"><option value="AM" ${s.slot==='AM'?'selected':''}>上午</option><option value="PM" ${s.slot==='PM'?'selected':''}>下午</option></select></label>
      <label>开始<input id="editStart" type="time" value="${s.start_time}"></label>
      <label>结束<input id="editEnd" type="time" value="${s.end_time}"></label></div>
    <div class="editor-actions"><button class="btn btn-main btn-sm" id="saveSession">保存场次时间</button>
      <button class="btn btn-danger btn-sm" id="deleteSession">删除这个场次</button></div>
    <div class="duration-banner ${(unknown || Math.abs(difference)>1)?'warning':''}">
      <b>本场视频总长 ${humanDuration(sum)} / 目标 ${humanDuration(target)}</b><br>${escapeHtml(msg)}</div>
    <div class="list-heading"><strong>按顺序播放的视频（${s.videos.length}）</strong><small class="muted">可拖拽排序或点击↑↓</small></div>
    <div class="playlist" id="playlist">${s.videos.length?s.videos.map((v,i)=>`<div class="playlist-item" draggable="true" data-position="${i}">
      <span class="grip" aria-hidden="true">☷</span><div class="title">${i+1}. ${escapeHtml(v.title)}<small>${humanDuration(v.duration)}</small></div>
      <div class="tiny-buttons"><button data-action="up" data-index="${i}" title="上移">↑</button><button data-action="down" data-index="${i}" title="下移">↓</button><button data-action="remove" data-index="${i}" title="从当前列表移除">✕</button></div></div>`).join(''):
      '<div class="empty-state" style="padding:18px">从左边视频库点击“＋加入”，视频将出现在这里。</div>'}</div>`;
}
async function savePlaylist(ids){
  if(chosenSession==null)throw new Error('请先选择一个场次');
  await api(`/api/sessions/${chosenSession}/items`,'PUT',{video_ids:ids});
  await reload();
}
function currentIds(){const s=editorSession();return s?s.videos.map(v=>v.id):[];}
async function moveVideo(from,to){let ids=currentIds();if(to<0||to>=ids.length||from===to)return;
  const [one]=ids.splice(from,1);ids.splice(to,0,one);await savePlaylist(ids);}
async function scanDuration(v){
  return new Promise((resolve,reject)=>{
    const el=document.createElement('video');let finished=false;
    const done=(val,err)=>{if(finished)return;finished=true;clearTimeout(timer);el.removeAttribute('src');el.load();if(err)reject(err);else resolve(val);};
    const timer=setTimeout(()=>done(null,new Error(`${v.title} 读取元数据超时`)),15000);
    el.preload='metadata';
    el.onloadedmetadata=()=>Number.isFinite(el.duration)&&el.duration>0?done(el.duration):done(null,new Error(`${v.title} 无法识别时长`));
    el.onerror=()=>done(null,new Error(`${v.title} 浏览器不支持其格式或文件已丢失`));
    el.src=`/media/${v.id}`;
  });
}
async function scanMissing(){
  const missing=library.filter(v=>!v.duration);if(!missing.length){toast('视频时长已经全部识别');return;}
  const btn=$('scanDurations');btn.disabled=true;let ok=0;let errors=[];
  try{
    for(let i=0;i<missing.length;i++){
      btn.textContent=`识别中 ${i+1}/${missing.length}`;
      try{let d=await scanDuration(missing[i]);await api(`/api/videos/${missing[i].id}/duration`,'POST',{duration:d});ok++;}
      catch(err){errors.push(err.message);}
    }
  }finally{btn.disabled=false;btn.textContent='浏览器识别未知时长';await reload();}
  toast(`已识别 ${ok} 个；失败 ${errors.length} 个。${errors.slice(0,2).join('；')}`,errors.length>0);
}
async function uploadFiles(files){
  const list=[...files].filter(f=>/\.(mp4|m4v|webm|mov|mkv)$/i.test(f.name));
  if(!list.length){toast('没有检测到支持的视频格式',true);return;}
  const progress=$('uploadProgress');progress.classList.remove('hidden');let count=0;
  for(let i=0;i<list.length;i++){
    const file=list[i];progress.textContent=`正在上传 ${i+1}/${list.length}：${file.name}（${(file.size/1024/1024).toFixed(1)} MB）`;
    try{await new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();xhr.open('POST',`/api/upload?filename=${encodeURIComponent(file.name)}`);
      xhr.upload.onprogress=e=>{if(e.lengthComputable)progress.textContent=`上传 ${i+1}/${list.length}：${file.name} ${(e.loaded/e.total*100).toFixed(0)}%`;};
      xhr.onload=()=>xhr.status>=200&&xhr.status<300?resolve():reject(new Error(`上传失败：${file.name} HTTP ${xhr.status} ${xhr.responseText.slice(0,100)}`));
      xhr.onerror=()=>reject(new Error(`上传失败：${file.name}，请检查本地服务`));xhr.send(file);
    });count++;}catch(err){toast(err.message,true);}
  }
  progress.classList.add('hidden');await reload();toast(`已上传 ${count}/${list.length} 个视频`);
}
async function refreshStatus(){
  try{
    const res=await api('/api/status');const hb=res.heartbeat;
    const fresh=hb && Date.now()/1000-hb.last_seen<35;
    $('heartbeatStatus').textContent=fresh?'正常':'未连接';
    $('heartbeatStatus').style.color=fresh?'#0c956c':'#be6b11';
    const name=fresh?({playing:'课程播放中',paused:'直播暂停',finished:'场次已播放完毕',error:'播放器异常',loading:'正在加载'}[hb.payload.mode]||hb.payload.mode):'播放器未连接';
    $('liveStatus').textContent=name;
    $('playerDetails').textContent=hb?`最后心跳：${new Date(hb.last_seen*1000).toLocaleString('zh-CN',{hour12:false})} ｜ 状态：${name} ｜ 场次：${hb.payload.session||'—'} ｜ 视频：${hb.payload.video||'—'} ｜ 播放位置：${humanDuration(hb.payload.position)} ｜ 错误：${hb.payload.error||'无'}`:'未发现播放器心跳。打开独立播放页面并保持窗口运行。';
    $('eventList').innerHTML=res.events.length?res.events.map(x=>`<div class="event-line"><time>${escapeHtml(x.ts)}</time><strong>${escapeHtml(x.kind)}</strong><span>${escapeHtml(x.detail)}</span></div>`).join(''):'<div class="hint">目前没有事件。</div>';
  }catch(err){$('heartbeatStatus').textContent='服务异常';}
}
function bindEvents(){
  $('generateForm').addEventListener('submit',e=>{e.preventDefault();guarded(async()=>{
    const r=await api('/api/generate','POST',{start_day:$('startDay').value,days:Number($('days').value),am_start:$('amStart').value,am_end:$('amEnd').value,pm_start:$('pmStart').value,pm_end:$('pmEnd').value});
    await reload();toast(`已新建 ${r.created} 个场次，跳过已有 ${r.skipped} 个`);
  });});
  $('importFolder').onclick=()=>guarded(async()=>{if(!$('folderPath').value.trim())throw new Error('请先输入本机文件夹路径');
    const r=await api('/api/import','POST',{folder:$('folderPath').value.trim(),recursive:true});await reload();toast(`扫描完成：新增 ${r.added} 个，已存在 ${r.skipped} 个`);});
  $('autoAssign').onclick=()=>guarded(async()=>{
    if(!confirm('将按 Day01/AM、Day01/PM 等目录自动覆盖对应场次现有的播放顺序。是否继续？'))return;
    const r=await api('/api/auto-assign','POST',{});await reload();toast(`已匹配 ${r.sessions} 个场次，安排 ${r.assigned} 个视频`);});
  $('dropZone').onclick=()=>$('fileInput').click();$('dropZone').onkeydown=e=>{if(e.key==='Enter')$('fileInput').click();};
  $('fileInput').onchange=e=>guarded(()=>uploadFiles(e.target.files));
  ['dragenter','dragover'].forEach(name=>$('dropZone').addEventListener(name,e=>{e.preventDefault();$('dropZone').classList.add('dragover');}));
  ['dragleave','drop'].forEach(name=>$('dropZone').addEventListener(name,e=>{e.preventDefault();$('dropZone').classList.remove('dragover');}));
  $('dropZone').addEventListener('drop',e=>guarded(()=>uploadFiles(e.dataTransfer.files)));
  $('scanDurations').onclick=()=>guarded(scanMissing);
  $('searchVideos').oninput=renderLibrary;
  $('libraryList').addEventListener('click',e=>guarded(async()=>{
    const btn=e.target.closest('button[data-action]');if(!btn)return;
    const id=Number(btn.dataset.id),video=library.find(v=>v.id===id);if(!video)return;
    if(btn.dataset.action==='add'){await savePlaylist([...currentIds(),id]);toast('已添加至当前场次');}
    else if(btn.dataset.action==='editVideo'){
      const title=prompt('视频名称',video.title);if(title===null)return;
      const d=prompt('视频时长（秒），如 1800.50；清空代表未识别',video.duration??'');if(d===null)return;
      await api(`/api/videos/${id}`,'PUT',{title:title.trim(),duration:d.trim()?Number(d):null});await reload();toast('视频信息已更新');
    }
  }));
  $('scheduleCards').addEventListener('click',e=>{const item=e.target.closest('[data-session]');if(!item)return;chosenSession=Number(item.dataset.session);renderSchedule();renderEditor();});
  $('editor').addEventListener('click',e=>guarded(async()=>{
    if(e.target.id==='saveSession'){
      await api(`/api/sessions/${chosenSession}`,'PUT',{day:$('editDay').value,slot:$('editSlot').value,start_time:$('editStart').value,end_time:$('editEnd').value});await reload();toast('场次已保存');return;
    }
    if(e.target.id==='deleteSession'){
      if(!confirm('确认删除这个场次及其播放列表？视频文件不会被删除。'))return;
      await api(`/api/sessions/${chosenSession}`,'DELETE');chosenSession=null;await reload();toast('场次已删除');return;
    }
    const b=e.target.closest('button[data-action]');if(!b)return;
    const idx=Number(b.dataset.index),ids=currentIds();
    if(b.dataset.action==='remove'){ids.splice(idx,1);await savePlaylist(ids);}
    if(b.dataset.action==='up')await moveVideo(idx,idx-1);
    if(b.dataset.action==='down')await moveVideo(idx,idx+1);
  }));
  let dragged=null;
  $('editor').addEventListener('dragstart',e=>{let item=e.target.closest('.playlist-item');if(item){dragged=Number(item.dataset.position);item.classList.add('dragging');e.dataTransfer.effectAllowed='move';}});
  $('editor').addEventListener('dragover',e=>{if(e.target.closest('.playlist-item'))e.preventDefault();});
  $('editor').addEventListener('drop',e=>guarded(async()=>{const dest=e.target.closest('.playlist-item');if(!dest||dragged===null)return;e.preventDefault();await moveVideo(dragged,Number(dest.dataset.position));dragged=null;}));
  $('editor').addEventListener('dragend',()=>{dragged=null;document.querySelectorAll('.dragging').forEach(el=>el.classList.remove('dragging'));});
  $('refreshAll').onclick=()=>guarded(async()=>{await reload();await refreshStatus();toast('数据已刷新');});
}
async function start(){
  const now=new Date(),yyyy=now.getFullYear(),mm=String(now.getMonth()+1).padStart(2,'0'),dd=String(now.getDate()).padStart(2,'0');
  $('startDay').value=`${yyyy}-${mm}-${dd}`;
  bindEvents();await reload();await refreshStatus();
  setInterval(refreshStatus,12000);
  setInterval(()=>guarded(reload),60000);
}
start().catch(err=>toast('无法连接本地服务：'+err.message,true));
