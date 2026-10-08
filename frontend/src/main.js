import { createApp } from 'vue'
import './prototype.css'
import { openAdmin } from './admin.js'

let installPrompt = null
window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault()
  installPrompt = event
})
window.addEventListener('appinstalled', () => {
  installPrompt = null
  document.querySelector('#installApp')?.remove()
})

const $ = selector => document.querySelector(selector)
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[ch])
const icon = (name, size=20) => `<svg width="${size}" height="${size}" aria-hidden="true"><use href="#i-${name}"/></svg>`
const params = object => new URLSearchParams(object).toString()
const jsonBody = value => ({ method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(value) })
const api = async (route, options={}) => {
  const response = await fetch('/api' + route, options)
  if (!response.ok) {
    if(response.status===401 && route!=='/me'){location.reload();throw new Error('Сессия завершена')}
    let message = 'Ошибка запроса'
    try { const payload = await response.json(); message = typeof payload.detail === 'string' ? payload.detail : payload.detail?.message || message } catch {}
    const error = new Error(message); error.status = response.status; throw error
  }
  return response.json()
}
const formatUnit = (value, digits, unit) => value.toFixed(digits).replace(/\.0+$/, '') + ' ' + unit
const capacity = bytes => bytes == null ? '—' : bytes >= 1024 ** 4 ? formatUnit(bytes / 1024 ** 4, 2, 'ТБ') : formatUnit(bytes / 1024 ** 3, 1, 'ГБ')
const size = bytes => bytes == null ? '—' : bytes >= 1024 ** 3 ? capacity(bytes) : bytes >= 1024 ** 2 ? formatUnit(bytes / 1024 ** 2, 1, 'МБ') : bytes >= 1024 ? formatUnit(bytes / 1024, 0, 'КБ') : bytes + ' Б'
const date = value => {
  if (!value) return '—'
  const day = new Date(value), today = new Date()
  if (day.toDateString() === today.toDateString()) return 'Сегодня'
  const yesterday = new Date(today); yesterday.setDate(today.getDate()-1)
  if (day.toDateString() === yesterday.toDateString()) return 'Вчера'
  return day.toLocaleDateString('ru-RU',{day:'numeric',month:'short'})
}
const typeOf = entry => entry.directory ? 'folder' : /\.(mp4|webm|mov)$/i.test(entry.name) ? 'video' : /\.(mp3|wav|ogg)$/i.test(entry.name) ? 'audio' : /\.(png|jpe?g|gif|webp)$/i.test(entry.name) ? 'image' : /\.pdf$/i.test(entry.name) ? 'pdf' : /\.zip$/i.test(entry.name) ? 'zip' : 'doc'
const filenameBytes = name => new TextEncoder().encode(name).length
const suggestedFilename = name => {
  const dot=name.lastIndexOf('.'),suffix=dot>0?name.slice(dot):''
  const stem=Array.from(dot>0?name.slice(0,dot):name)
  while(stem.length&&filenameBytes(stem.join('')+suffix)>255)stem.pop()
  return stem.join('').trimEnd()+suffix
}

function init(user) {
  const displayName=user.first_name?.trim()||user.email.split('@')[0].replace(/[._-]+/g,' ').trim()||user.email
  $('#profileName').textContent=displayName
  $('#profileAvatar').textContent=displayName.split(' ').map(part=>part[0]||'').slice(0,2).join('').toUpperCase()
  const installed=window.matchMedia('(display-mode: standalone)').matches||navigator.standalone===true
  $('#profileMenu').innerHTML=(user.admin?'<button id="openAdministration">Администрирование</button>':'')+(installed?'':'<button id="installApp">Установить приложение</button>')+'<button id="logout">Выйти</button>'
  $('#profileButton').onclick=()=>{$('#profileMenu').hidden=!$('#profileMenu').hidden}
  $('#profileMenu').onclick=async event=>{
    if(event.target.id==='openAdministration'){$('#profileMenu').hidden=true;await openAdmin()}
    if(event.target.id==='installApp'){
      $('#profileMenu').hidden=true
      if(installPrompt){
        const prompt=installPrompt
        installPrompt=null
        await prompt.prompt()
      }else{
        const ios=/iPhone|iPad|iPod/.test(navigator.userAgent)
        const macSafari=/Macintosh/.test(navigator.userAgent)&&/Safari/.test(navigator.userAgent)&&!/Chrome|CriOS|Chromium|Edg/.test(navigator.userAgent)
        const instruction=ios?'В Safari нажмите «Поделиться» → «На экран Домой», включите «Открывать как веб-приложение» и нажмите «Добавить».':macSafari?'В Safari откройте меню «Файл» → «Добавить в Dock» или нажмите «Поделиться» → «Добавить в Dock».':'Откройте меню браузера и выберите «Установить приложение» или «Добавить на главный экран». В некоторых браузерах значок установки находится рядом с адресной строкой.'
        modal('Установить приложение',`<p class="modal-note">${instruction}</p><div class="modal-footer"><button class="primary-button" id="installHelpOk">Понятно</button></div>`,true)
        $('#installHelpOk').onclick=closeModal
      }
    }
    if(event.target.id==='logout'){await fetch('/api/auth/logout',{method:'POST'});location.reload()}
  }
  let storages = [], files = []
  const uploadQueue = []
  let pendingUploadTasks = [], nextPendingUpload = 0, folderPreparation = null, folderPreparationChain = Promise.resolve(), folderUploadGeneration = 0
  let uploadWorkerRunning = false, uploadPanelCollapsed = false, nextUploadId = 0
  const uploadPanel=document.createElement('aside')
  uploadPanel.id='uploadPanel'
  uploadPanel.className='upload-panel'
  uploadPanel.setAttribute('aria-label','Загрузки')
  document.body.append(uploadPanel)
  const fileDragHint=document.createElement('div')
  fileDragHint.className='file-drag-hint'
  fileDragHint.innerHTML=`${icon('upload',36)}<strong>Отпустите файлы или папку для загрузки</strong><span>В текущее хранилище или в папку под курсором</span>`
  document.body.append(fileDragHint)
  let fileDragTimer
  const externalFiles=e=>Array.from(e.dataTransfer?.types||[]).includes('Files')
  const hideFileDrag=()=>{fileDragHint.classList.remove('open');clearTimeout(fileDragTimer)}
  const state = { storage:'', folder:'', view:'all', selected:null, search:'', trashBrowse:null }
  let personalLabels={}
  const currentStorage = () => storages.find(s => s.id === state.storage)
  const fileById = id => files.find(f => f.id === id)
  const fileUrl = (f, kind='preview') => f.fileId ? '/api/files/'+encodeURIComponent(f.fileId)+'/content?download='+(kind==='download'?'true':'false') : '/api/' + kind + '?' + params({storage:f.storage,path:f.path})
  const asFile = (entry, index) => ({ ...entry, displayName:entry.display_name, fileId:entry.id||null, id:String(index), type:typeOf(entry), date:date(entry.modified), displaySize:entry.directory?'—':size(entry.size) })
  const ensureFileId = async f => {
    if(f.fileId)return f.fileId
    const record=await api('/files/resolve',jsonBody({storage:f.storage,path:f.path}))
    f.fileId=record.id
    return record.id
  }
  function showToast(message) { const target=$('#toast'); target.textContent=message; target.classList.add('open'); clearTimeout(window.toastTimer); window.toastTimer=setTimeout(()=>target.classList.remove('open'),3500) }
  const uploadKey = task => 'storagespace-upload:'+task.storage+':'+task.path+':'+task.file.size+':'+task.file.lastModified
  function renderUploadPanel() {
    if(!uploadQueue.length&&!folderPreparation){uploadPanel.className='upload-panel';uploadPanel.innerHTML='';return}
    const pending=uploadQueue.filter(task=>task.status==='queued'||task.status==='uploading').length
    const errors=uploadQueue.filter(task=>task.status==='error').length
    const done=uploadQueue.filter(task=>task.status==='done').length
    const skipped=uploadQueue.filter(task=>task.status==='skipped').length
    const title=folderPreparation?.cancelRequested?'Отмена загрузки папок':folderPreparation?`Подготовка папок · ${folderPreparation.done}/${folderPreparation.total}`:pending?`Загружается ${pending} ${pending===1?'файл':pending<5?'файла':'файлов'}`:errors?`Ошибки загрузки · ${errors}`:'Загрузка завершена'
    const active=uploadQueue.find(task=>task.status==='uploading')
    const visible=uploadQueue.length<=12?uploadQueue:[active,...uploadQueue.filter(task=>task.status==='queued').slice(0,3),...uploadQueue.filter(task=>task.status==='error').slice(0,3),...uploadQueue.filter(task=>task.status==='skipped').slice(-2),...uploadQueue.filter(task=>task.status==='done').slice(-2)].filter((task,index,array)=>task&&array.indexOf(task)===index)
    uploadPanel.className='upload-panel open'+(uploadPanelCollapsed?' collapsed':'')
    uploadPanel.innerHTML=`<div class="upload-panel-head"><strong>${title}</strong><div class="upload-panel-controls"><button data-upload-toggle aria-label="${uploadPanelCollapsed?'Развернуть загрузки':'Свернуть загрузки'}">${uploadPanelCollapsed?'⌃':'⌄'}</button><button data-upload-dismiss aria-label="${pending||errors||folderPreparation?'Отменить все загрузки':'Закрыть панель загрузок'}">×</button></div></div><div class="upload-panel-body">${uploadQueue.length?`<div class="upload-summary">Загружено ${done} из ${uploadQueue.length} · Пропущено ${skipped}${errors?' · Ошибок '+errors:''}</div>`:''}${visible.map(task=>{
      const percent=task.file.size?Math.min(100,Math.round(task.offset/task.file.size*100)):task.status==='done'?100:0
      const circumference=62.83
      const ring=`<svg class="upload-ring" width="26" height="26" viewBox="0 0 26 26" aria-hidden="true"><circle class="upload-ring-track" cx="13" cy="13" r="10"/><circle class="upload-ring-value" cx="13" cy="13" r="10" stroke-dasharray="${circumference}" stroke-dashoffset="${(circumference*(1-percent/100)).toFixed(2)}"/></svg>`
      const status=task.status==='done'?'<span class="upload-done" role="img" aria-label="Загружено">✓</span>':task.status==='skipped'?'<span class="upload-skipped" role="img" aria-label="Пропущено: файл уже существует">—</span>':task.status==='error'?`<button class="upload-retry" data-upload-retry="${task.id}" aria-label="Повторить загрузку ${esc(task.file.name)}" title="${esc(task.error||'Ошибка загрузки')}">↻</button>`:task.cancelRequested?'<span class="upload-cancelling" aria-label="Отменяется">×</span>':task.phase==='finishing'?ring:`<button class="upload-progress" data-upload-cancel="${task.id}" aria-label="Отменить загрузку ${esc(task.file.name)}, ${percent}%" title="Отменить загрузку">${ring}<span class="upload-progress-cross">×</span></button>`
      return `<div class="upload-item ${task.status}"><span class="upload-file-icon" aria-hidden="true">${icon('file',17)}</span><span class="upload-item-name" title="${esc(task.path)}">${esc(task.folderBatch?task.relativePath:task.file.name)}</span>${status}</div>`
    }).join('')}${uploadQueue.length>visible.length?`<div class="upload-summary">И ещё ${uploadQueue.length-visible.length} файлов в очереди и истории</div>`:''}</div>`
  }
  function closeModal() { $('#overlay').classList.remove('open');delete $('#modal').dataset.fileId;delete $('#modal').dataset.resolveToken;$('#modal').innerHTML='' }
  function modal(title, content, compact=false) { delete $('#modal').dataset.fileId;delete $('#modal').dataset.resolveToken;$('#modal').className='modal'+(compact?' compact':''); $('#modal').innerHTML=`<div class="modal-header"><h2>${esc(title)}</h2><button class="close" id="closeModal" aria-label="Закрыть">×</button></div>${content}`; $('#overlay').classList.add('open'); $('#closeModal').onclick=closeModal }
  function closeMenu() { $('#context').classList.remove('open') }
  function showContextMenu(f,x,y) {
    if (!f||f.trashChild) return
    const c=$('#context')
    c.innerHTML=f.trashed?'<button data-menu="restore">Восстановить</button><button class="danger" data-menu="permanent">Удалить навсегда</button>':`${['video','audio','image','pdf'].includes(f.type)?'<button data-menu="preview">Открыть просмотр</button>':''}<button data-menu="favorite">${f.favorite?'Убрать из избранного':'Добавить в избранное'}</button>${f.type!=='folder'?'<button data-menu="download">Скачать</button><button data-menu="details">Сведения</button>':''}<button data-menu="rename">Переименовать</button><button data-menu="move">Переместить</button><button data-menu="copy">Копировать</button><button class="danger" data-menu="trash">В корзину</button>`
    c.dataset.id=f.id; c.classList.add('open'); c.style.left=Math.max(8,Math.min(x,innerWidth-c.offsetWidth-8))+'px'; c.style.top=Math.max(8,Math.min(y,innerHeight-c.offsetHeight-8))+'px'
  }
  function render() {
    const active=currentStorage()
    const offline=state.view==='all'&&active&&!active.online
    $('#newFolderHeading').disabled=!!offline||!state.storage
    $('#uploadTop').disabled=!!offline||!state.storage
    $('#sideStorages').innerHTML=storages.map(s=>`<button class="storage ${s.id===state.storage&&state.view==='all'?'active':''}" data-storage="${esc(s.id)}" aria-label="${esc(s.name)}: ${s.online?'свободно '+capacity(s.free)+' из '+capacity(s.total):'недоступно'}">${icon('drive',18)}<span class="storage-info"><span class="storage-name">${esc(s.name)}</span><span class="storage-sub">${s.online?'Свободно '+capacity(s.free)+' из '+capacity(s.total):'Хранилище недоступно'}</span><span class="storage-meter"><i style="width:${s.online&&s.total?Math.round((s.total-s.free)/s.total*100):0}%"></i></span></span></button>`).join('')
    document.querySelectorAll('.nav').forEach(button=>button.classList.toggle('active',button.dataset.nav===state.view))
    $('#pageTitle').textContent=state.view==='trash'?'Корзина':state.view==='recent'?'Недавние':state.view==='favorites'?'Избранное':'Все файлы'
    $('#search').placeholder=state.view==='trash'?'Поиск в корзине':state.view==='recent'?'Поиск в недавних':state.view==='favorites'?'Поиск в избранном':'Поиск в текущей папке'
    const browsingTrash=state.view==='trash'&&state.trashBrowse
    $('#filesHead').style.display=(state.view==='all'&&!!state.folder)||browsingTrash?'block':'none'
    if(browsingTrash){
      const browse=state.trashBrowse, parts=browse.path?browse.path.split('/'):[]
      const crumbs=[{name:'Корзина',root:true},{name:browse.name,path:''},...parts.map((name,i)=>({name,path:parts.slice(0,i+1).join('/')}))]
      $('#breadcrumbs').innerHTML=crumbs.map((crumb,i)=>`${i?'<span>›</span>':''}<button class="crumb" ${crumb.root?'data-trash-root="true"':`data-trash-crumb="${esc(crumb.path)}"`}>${esc(crumb.name)}</button>`).join('')
    }else{
      const crumbs=[{name:active?.name||'Хранилище',path:''}]
      if(state.view==='all'&&state.folder)state.folder.split('/').forEach((name,i,all)=>{const path=all.slice(0,i+1).join('/');crumbs.push({name:personalLabels[path]||name,path})})
      $('#breadcrumbs').innerHTML=crumbs.map((crumb,i)=>`${i?'<span>›</span>':''}<button class="crumb" data-crumb="${esc(crumb.path)}" title="Открыть папку или перетащить сюда файл">${esc(crumb.name)}</button>`).join('')
    }
    const visible=files.filter(f=>(f.displayName||f.name||'').toLocaleLowerCase('ru').includes(state.search.toLocaleLowerCase('ru')))
    $('#rows').innerHTML=offline?`<div class="empty storage-offline">${icon('drive',42)}<h3>Хранилище недоступно</h3><p>Проверяем подключение автоматически. После восстановления монтирования файлы появятся здесь.</p><button class="soft-button" id="retryStorage">Проверить сейчас</button></div>`:visible.length?visible.map(f=>`<div class="row ${state.selected===f.id?'selected':''}" data-id="${f.id}" tabindex="0" draggable="${state.view==='all'}"><div class="file-name"><span class="file-icon ${f.type}">${icon(f.type==='folder'?'folder':'file',21)}</span><span title="${esc(f.displayName||f.name)}">${esc(f.displayName||f.name)}</span>${!f.trashed?`<button class="favorite-toggle ${f.favorite?'active':''}" data-favorite="${f.id}" aria-label="${f.favorite?'Убрать из избранного':'Добавить в избранное'}: ${esc(f.displayName||f.name)}" title="${f.favorite?'Убрать из избранного':'Добавить в избранное'}">${icon('star',17)}</button>`:''}</div><span class="meta">${esc(f.date)}</span><span class="meta">${esc(f.displaySize)}</span>${f.trashChild?"":`<button class="more" data-more="${f.id}" aria-label="Действия с ${esc(f.displayName||f.name)}">•••</button>`}</div>`).join(''):`<div class="empty">${icon('folder',42)}<h3>${state.search?'Ничего не найдено':state.view==='trash'&&!state.trashBrowse?'Корзина пуста':state.view==='favorites'?'Пока нет избранных файлов и папок':'В этой папке пока пусто'}</h3><p>${state.search?'Попробуйте другой запрос':state.view==='favorites'?'Нажмите на звёздочку рядом с файлом или папкой, чтобы добавить сюда':'Файлы появятся здесь после добавления'}</p></div>`
  }
  async function load() {
    try {
      let entries
      if (state.view==='trash'){
        if(state.trashBrowse){
          const browse=state.trashBrowse
          entries=(await api('/trash/'+browse.id+'/files?'+params({path:browse.path}))).map(e=>({...e,trashed:true,trashChild:true,trashId:browse.id,trashPath:e.trash_path}))
        }else{
          entries=(await api('/trash')).map(e=>({trashId:e.id,storage:e.storage,path:e.original_path,name:e.original_path.split('/').at(-1),directory:e.directory,size:null,modified:e.deleted_at,trashed:true}))
        }
      }
      else if (state.view==='favorites') entries=await api('/favorites')
      else if (state.view==='recent') { const batches=await Promise.all(storages.filter(s=>s.online).map(s=>api('/files?'+params({storage:s.id,path:''})).catch(()=>[]))); entries=batches.flat().filter(e=>!e.directory).sort((a,b)=>new Date(b.modified)-new Date(a.modified)).slice(0,50) }
      else entries=state.storage&&currentStorage()?.online?await api('/files?'+params({storage:state.storage,path:state.folder})):[]
      if(state.view==='all'&&state.storage&&currentStorage()?.online)personalLabels=await api('/personal-folders?'+params({storage:state.storage,path:state.folder}))
      else personalLabels={}
      files=entries.map(asFile)
      if (state.view==='trash'&&!state.trashBrowse) files.forEach(f=>{f.id=f.trashId;f.date=date(f.modified)})
      render()
    } catch(e) { files=[];render();if(e.status===503)void refreshStorages(true);else showToast(e.message) }
  }
  async function refreshStorages(force=false) {
    const wasOnline=currentStorage()?.online
    storages=await api('/storages'+(force?'?fresh=true':''))
    if (!storages.some(s=>s.id===state.storage)) state.storage=storages[0]?.id||''
    if(state.view==='all'&&currentStorage()?.online===false)files=[]
    render()
    if(wasOnline===false&&currentStorage()?.online&&state.view==='all')await load()
  }
  async function selectStorage(id) { state.storage=id;state.folder='';state.view='all';state.trashBrowse=null;state.selected=null;state.search='';$('#search').value='';$('#sidebar').classList.remove('open');await load() }
  async function selectView(view) { state.view=view;state.folder='';state.trashBrowse=null;state.selected=null;state.search='';$('#search').value='';$('#sidebar').classList.remove('open');await load() }
  async function openFile(f) {
    if(!f)return
    if(f.trashed){
      if(f.directory){
        if(state.trashBrowse)state.trashBrowse.path=f.trashPath
        else state.trashBrowse={id:f.trashId,name:f.name,path:'',storage:f.storage}
        state.selected=null;state.search='';$('#search').value='';void load()
      }
      return
    }
    if(f.directory){state.storage=f.storage;state.folder=f.path;state.view='all';state.search='';$('#search').value='';void load()}
    else if(['video','audio','image','pdf'].includes(f.type))await preview(f)
    else await download(f)
  }
  async function preview(f) {
    try{await ensureFileId(f)}catch(error){showToast(error.message);return}
    const url=fileUrl(f)
    const content=f.type==='video'?`<video controls autoplay src="${url}"></video>`:f.type==='audio'?`<audio controls autoplay src="${url}"></audio>`:f.type==='image'?`<img src="${url}" alt="${esc(f.name)}">`:`<iframe src="${url}" title="${esc(f.name)}"></iframe>`
    modal(f.name,`<div class="real-preview">${content}</div><div class="modal-footer"><a class="primary-button" href="${fileUrl(f,'download')}">Скачать</a></div>`)
  }
  async function download(f) {
    try{await ensureFileId(f)}catch(error){showToast(error.message);return}
    const link=document.createElement('a');link.href=fileUrl(f,'download');link.hidden=true;document.body.append(link);link.click();link.remove()
  }
  async function details(f) {
    modal('Сведения о файле','<p class="modal-note">Загрузка сведений…</p>',true)
    const resolveToken=Math.random().toString(36).slice(2)
    $('#modal').dataset.resolveToken=resolveToken
    try{await ensureFileId(f)}catch(error){if($('#modal').dataset.resolveToken===resolveToken){closeModal();showToast(error.message)}return}
    if(!$('#overlay').classList.contains('open')||$('#modal').dataset.resolveToken!==resolveToken)return
    delete $('#modal').dataset.resolveToken
    $('#modal').dataset.fileId=f.fileId
    const refresh=async()=>{
      try {
        const item=await api('/files/'+encodeURIComponent(f.fileId))
        if($('#modal').dataset.fileId!==f.fileId||!$('#overlay').classList.contains('open'))return
        const hash=item.hash_status==='ready'?`<code class="file-hash">${esc(item.sha256)}</code>`:item.hash_status==='queued'||item.hash_status==='running'?'Рассчитывается…':item.hash_status==='error'?'Ошибка расчёта':'Не рассчитан'
        const button=['not_computed','error'].includes(item.hash_status)?'<button class="soft-button" id="calculateHash">Рассчитать SHA-256</button>':''
        $('#modal').innerHTML=`<div class="modal-header"><h2>Сведения о файле</h2><button class="close" id="closeModal" aria-label="Закрыть">×</button></div><div class="file-details"><strong>${esc(f.name)}</strong><dl><dt>ID файла</dt><dd><code>${esc(item.id)}</code></dd><dt>Размер</dt><dd>${size(item.size)}</dd><dt>SHA-256</dt><dd>${hash}</dd></dl>${button}</div>`
        $('#closeModal').onclick=closeModal
        if(button)$('#calculateHash').onclick=async()=>{try{await api('/files/'+encodeURIComponent(f.fileId)+'/sha256',{method:'POST'});await refresh()}catch(error){showToast(error.message)}}
        if(['queued','running'].includes(item.hash_status))setTimeout(()=>{if($('#modal').dataset.fileId===f.fileId&&$('#overlay').classList.contains('open'))void refresh()},2500)
      } catch(error) {if($('#modal').dataset.fileId===f.fileId)showToast(error.message)}
    }
    await refresh()
  }
  async function mutate(fn,success) { try {await fn();closeModal();closeMenu();await load();await refreshStorages();if(success)showToast(success)}catch(e){showToast(e.message)} }
  async function action(name,f) {
    closeMenu();if(!f)return
    if(name==='preview')return openFile(f)
    if(name==='download')return download(f)
    if(name==='details')return details(f)
    if(name==='favorite')return mutate(()=>api('/favorite',jsonBody({storage:f.storage,path:f.path})),f.favorite?'Убрано из избранного':'Добавлено в избранное')
    if(name==='trash')return mutate(()=>api('/trash',jsonBody({storage:f.storage,path:f.path})),'Перемещено в корзину')
    if(name==='restore')return mutate(()=>api('/trash/'+f.trashId+'/restore',{method:'POST'}),'Восстановлено')
    if(name==='permanent') {modal('Удалить окончательно?',`<p style="font-size:14px;line-height:1.5;color:#69768a">«${esc(f.name)}» нельзя будет восстановить.</p><div class="modal-footer"><button class="soft-button" id="cancelDelete">Отмена</button><button class="primary-button" id="confirmDelete">Удалить</button></div>`,true);$('#cancelDelete').onclick=closeModal;$('#confirmDelete').onclick=()=>mutate(()=>api('/trash/'+f.trashId,{method:'DELETE'}),'Удалено');return}
    if(name==='move')return moveModal(f)
    if(name==='copy')return moveModal(f,true)
    if(name==='rename') {
      modal('Переименовать',`<label class="field" for="renameInput">Новое имя</label><input class="text-field" id="renameInput" value="${esc(f.name)}"><div class="modal-footer"><button class="soft-button" id="cancelRename">Отмена</button><button class="primary-button" id="confirmRename">Переименовать</button></div>`,true)
      $('#renameInput').focus();$('#renameInput').select();$('#cancelRename').onclick=closeModal
      $('#confirmRename').onclick=()=>{const newName=$('#renameInput').value.trim();if(!newName||newName===f.name)return;const parent=f.path.split('/').slice(0,-1).join('/');return mutate(()=>api('/move',jsonBody({storage:f.storage,path:f.path,target:[parent,newName].filter(Boolean).join('/')})),'Переименовано')}
      $('#renameInput').onkeydown=e=>{if(e.key==='Enter')$('#confirmRename').click()}
      return
    }
  }
  function moveModal(f,copy=false) {
    const originalParent=f.path.split('/').slice(0,-1).join('/')
    let destination=originalParent, requestId=0
    modal(copy?'Копировать':'Переместить',`<p class="move-hint">Выберите папку в хранилище «${esc(storages.find(s=>s.id===f.storage)?.name||f.storage)}».</p><div id="movePicker"></div>${copy?`<label class="field" for="copyName">Имя копии</label><input class="text-field" id="copyName" value="${esc('Копия — '+f.name)}">`:''}<div class="modal-footer"><button class="soft-button" id="cancelMove">Отмена</button><button class="primary-button" id="confirmMove" disabled>${copy?'Копировать сюда':'Переместить сюда'}</button></div>`,true)
    $('#cancelMove').onclick=closeModal
    $('#confirmMove').onclick=()=>{
      const name=copy?$('#copyName').value.trim():f.name
      if(!name||name==='.'||name==='..'||name.startsWith('.')||name.includes('/')||name.includes('\\'))return showToast('Некорректное имя')
      const target=[destination,name].filter(Boolean).join('/')
      if(target!==f.path)void mutate(()=>api(copy?'/copy':'/move',jsonBody({storage:f.storage,path:f.path,target})),copy?'Скопировано':'Перемещено')
    }
    if(copy)$('#copyName').oninput=()=>{$('#confirmMove').disabled=!$('#copyName').value.trim()}
    $('#movePicker').onclick=e=>{
      const option=e.target.closest('[data-move-path]')
      if(option){destination=option.dataset.movePath;void renderPicker()}
    }
    async function renderPicker() {
      const currentRequest=++requestId, picker=$('#movePicker'), confirm=$('#confirmMove')
      if(!picker||!confirm)return
      confirm.disabled=true
      const parts=destination?destination.split('/'):[]
      const trail=[{name:storages.find(s=>s.id===f.storage)?.name||'Хранилище',path:''},...parts.map((name,i)=>{const path=parts.slice(0,i+1).join('/');return {name:personalLabels[path]||name,path}})]
      const breadcrumbs=trail.map((part,i)=>`${i?'<span>›</span>':''}<button type="button" data-move-path="${esc(part.path)}" class="${part.path===destination?'current':''}">${esc(part.name)}</button>`).join('')
      picker.innerHTML=`<div class="move-breadcrumbs">${breadcrumbs}</div><div class="folder-options"><div class="move-loading">Загрузка папок…</div></div>`
      try {
        const entries=await api('/files?'+params({storage:f.storage,path:destination}))
        if(currentRequest!==requestId||picker!==$('#movePicker'))return
        const folders=entries.filter(item=>item.directory&&item.path!==f.path&&!(f.directory&&item.path.startsWith(f.path+'/')))
        const parent=parts.slice(0,-1).join('/')
        picker.querySelector('.folder-options').innerHTML=`${destination?`<button type="button" class="folder-option" data-move-path="${esc(parent)}">↑ На уровень выше</button>`:''}${folders.map(item=>`<button type="button" class="folder-option" data-move-path="${esc(item.path)}">${icon('folder',16)} ${esc(item.display_name||item.name)}</button>`).join('')}${!folders.length?'<div class="move-empty">Вложенных папок нет</div>':''}`
        confirm.disabled=copy?!$('#copyName').value.trim():[destination,f.name].filter(Boolean).join('/')===f.path
      } catch(error) {
        if(currentRequest!==requestId||picker!==$('#movePicker'))return
        picker.querySelector('.folder-options').innerHTML=`<div class="move-error">${esc(error.message)}</div>`
      }
    }
    void renderPicker()
  }
  function createFolder() {
    if(state.view!=='all')return showToast('Сначала откройте хранилище')
    modal('Новая папка','<label class="field" for="folderName">Название папки</label><input class="text-field" id="folderName" placeholder="Например, Материалы"><div class="modal-footer"><button class="soft-button" id="cancelFolder">Отмена</button><button class="primary-button" id="confirmFolder">Создать</button></div>',true)
    $('#folderName').focus();$('#cancelFolder').onclick=closeModal;$('#confirmFolder').onclick=()=>{const name=$('#folderName').value.trim();if(name)mutate(()=>api('/folders',jsonBody({storage:state.storage,path:[state.folder,name].filter(Boolean).join('/')})),'Папка создана')};$('#folderName').onkeydown=e=>{if(e.key==='Enter')$('#confirmFolder').click()}
  }
  const validRelativePath=path=>{
    const parts=path.split('/')
    return parts.every(part=>part&&part!=='.'&&part!=='..'&&!part.startsWith('.')&&!part.includes('\\')&&!part.includes('\0')&&filenameBytes(part)<=255)&&filenameBytes(path)<=4095
  }
  async function readDroppedEntry(entry,prefix,manifest){
    if(entry.name.startsWith('.')){manifest.ignored++;return}
    const relativePath=prefix+entry.name
    if(entry.isDirectory){
      manifest.dirs.add(relativePath)
      const reader=entry.createReader()
      while(true){
        const children=await new Promise((resolve,reject)=>reader.readEntries(resolve,reject))
        if(!children.length)break
        await Promise.all(children.map(child=>readDroppedEntry(child,relativePath+'/',manifest)))
      }
    }else if(entry.isFile){
      const file=await new Promise((resolve,reject)=>entry.file(resolve,reject))
      manifest.files.push({file,relativePath})
    }
  }
  function prepareFolderManifest(manifest,destination,storage){
    const validDirs=new Set([...manifest.dirs].filter(path=>{if(validRelativePath(path))return true;manifest.ignored++;return false}))
    const validFiles=manifest.files.filter(item=>{
      if(validRelativePath(item.relativePath))return true
      manifest.ignored++;return false
    })
    for(const item of validFiles){
      const parts=item.relativePath.split('/')
      for(let i=1;i<parts.length;i++)validDirs.add(parts.slice(0,i).join('/'))
    }
    const dirs=[...validDirs].filter(path=>validRelativePath(path)).sort((a,b)=>a.split('/').length-b.split('/').length||a.localeCompare(b))
    const files=validFiles.filter(item=>item.relativePath.split('/').slice(0,-1).every((_,i,parts)=>validDirs.has(parts.slice(0,i+1).join('/'))))
    const bytes=files.reduce((total,item)=>total+item.file.size,0)
    if(!files.length&&!dirs.length){showToast('В папке нет файлов и папок, которые можно загрузить');return}
    modal('Загрузить папку',`<div class="folder-upload-summary"><strong>${files.length} файлов · ${size(bytes)}</strong><span>${dirs.length} папок${manifest.ignored?' · пропущено скрытых или некорректных элементов: '+manifest.ignored:''}</span><span>Место: ${esc(storages.find(item=>item.id===storage)?.name||storage)}${destination?' / '+esc(destination):''}</span></div><p class="modal-note">Структура папок сохранится. Существующие файлы не будут перезаписаны.</p><div class="modal-footer"><button class="soft-button" id="cancelFolderUpload">Отмена</button><button class="primary-button" id="startFolderUpload">Начать загрузку</button></div>`,true)
    $('#cancelFolderUpload').onclick=closeModal
    $('#startFolderUpload').onclick=()=>{
      closeModal()
      if(folderPreparation)showToast('Папка добавлена в очередь подготовки')
      const generation=folderUploadGeneration
      folderPreparationChain=folderPreparationChain.then(()=>startFolderUpload({files,dirs},destination,storage,generation))
    }
  }
  async function startFolderUpload(manifest,destination,storage,generation){
    if(generation!==folderUploadGeneration)return
    folderPreparation={done:0,total:manifest.dirs.length}
    uploadPanelCollapsed=false;renderUploadPanel()
    try {
      for(let index=0;index<manifest.dirs.length;index+=250){
        const paths=manifest.dirs.slice(index,index+250).map(path=>[destination,path].filter(Boolean).join('/'))
        await api('/folders/ensure',jsonBody({storage,paths}))
        if(generation!==folderUploadGeneration){folderPreparation=null;renderUploadPanel();return}
        folderPreparation.done+=paths.length;renderUploadPanel()
      }
      folderPreparation=null
      let added=0
      const activePaths=new Set(uploadQueue.filter(task=>task.storage===storage&&['queued','uploading'].includes(task.status)).map(task=>task.path))
      for(const {file,relativePath} of manifest.files){
        const path=[destination,relativePath].filter(Boolean).join('/')
        if(activePaths.has(path))continue
        const task={id:++nextUploadId,file,storage,folder:path.split('/').slice(0,-1).join('/'),path,relativePath,folderBatch:true,status:'queued',offset:0,error:''}
        uploadQueue.push(task);pendingUploadTasks.push(task);activePaths.add(path);added++
      }
      renderUploadPanel()
      if(state.view==='all'&&state.storage===storage&&state.folder===destination)void load()
      if(added){showToast(`Добавлено файлов в очередь: ${added}`);void drainUploadQueue()}
      else showToast(manifest.files.length?'Эти файлы уже находятся в очереди':'Папки созданы')
    } catch(error){
      folderPreparation=null;renderUploadPanel()
      if(generation!==folderUploadGeneration)return
      modal('Не удалось подготовить папку',`<p class="modal-note">${esc(error.message)}. Уже созданные папки сохраняются; можно повторить загрузку.</p><div class="modal-footer"><button class="primary-button" id="folderUploadErrorOk">Понятно</button></div>`,true)
      $('#folderUploadErrorOk').onclick=closeModal
    }
  }
  async function handleExternalDrop(roots,fallbackFiles,destination,storage){
    if(state.view!=='all'||!storage){showToast('Сначала откройте хранилище');return}
    if(!roots.length){if(fallbackFiles.length)addFiles(fallbackFiles,destination);else showToast('Браузер не передал содержимое папки. Используйте «Выбрать папку».');return}
    const manifest={files:[],dirs:new Set(),ignored:0}
    try {await Promise.all(roots.map(entry=>readDroppedEntry(entry,'',manifest)))}
    catch(error){showToast('Не удалось прочитать папку: '+error.message);return}
    if(!manifest.dirs.size){addFiles(manifest.files.map(item=>item.file),destination);return}
    prepareFolderManifest(manifest,destination,storage)
  }
  function uploadModal() {
    if(state.view!=='all')return showToast('Сначала откройте хранилище')
    modal('Загрузить',`<div class="dropzone" id="dropzone">${icon('upload',32)}<strong>Перетащите файлы или папку сюда</strong><p>или выберите на устройстве</p><div class="upload-choices"><button class="soft-button" id="chooseFiles">Выбрать файлы</button><button class="soft-button" id="chooseFolder">Выбрать папку</button></div></div><p class="modal-note">После выбора загрузка продолжится в фоне. Новые файлы добавятся в очередь.</p>`,true)
    $('#chooseFiles').onclick=()=>$('#fileInput').click();$('#chooseFolder').onclick=()=>$('#folderInput').click();const zone=$('#dropzone');zone.ondragover=e=>{e.preventDefault();zone.classList.add('drag-over')};zone.ondragleave=()=>zone.classList.remove('drag-over')
  }
  async function discardUpload(task, updatePanel=true) {
    const key=uploadKey(task), sessionId=task.sessionId||localStorage.getItem(key)
    if(sessionId){try{await api('/uploads/'+sessionId,{method:'DELETE'})}catch(error){if(error.status!==404)throw error}}
    localStorage.removeItem(key)
    const index=uploadQueue.indexOf(task)
    if(index>=0)uploadQueue.splice(index,1)
    if(updatePanel)renderUploadPanel()
  }
  function confirmCancelUpload(task) {
    if(!task||task.status==='done'||task.phase==='finishing'||task.cancelRequested)return
    modal('Прервать загрузку?',`<p class="modal-note">Файл «${esc(task.file.name)}» перестанет загружаться. Уже переданная часть будет удалена с сервера.</p><div class="modal-footer"><button class="soft-button" id="keepUpload">Продолжить загрузку</button><button class="primary-button" id="confirmCancelUpload">Прервать загрузку</button></div>`,true)
    $('#keepUpload').onclick=closeModal
    $('#confirmCancelUpload').onclick=()=>{
      closeModal()
      if(task.status==='done'||task.phase==='finishing'){showToast('Файл уже загружен');return}
      void cancelUpload(task)
    }
    $('#keepUpload').focus()
  }
  async function cancelUpload(task) {
    if(!task||task.status==='done'||task.phase==='finishing'||task.cancelRequested)return
    task.cancelRequested=true
    task.controller?.abort()
    renderUploadPanel()
    if(task.status!=='uploading'){
      try{await discardUpload(task)}catch(error){task.status='error';task.error=error.message;task.cancelRequested=false;renderUploadPanel()}
    }
  }
  function dismissFinishedUploads(){
    for(let i=uploadQueue.length-1;i>=0;i--)if(['done','skipped'].includes(uploadQueue[i].status))uploadQueue.splice(i,1)
    renderUploadPanel()
  }
  function confirmCancelAllUploads(){
    const unfinished=uploadQueue.filter(task=>['queued','uploading','error'].includes(task.status))
    if(!unfinished.length&&!folderPreparation){dismissFinishedUploads();return}
    const count=unfinished.length
    modal('Прервать все загрузки?',`<p class="modal-note">${count?`Незавершённых загрузок: ${count}. `:''}Недокачанные части будут удалены с сервера. Уже загруженные файлы останутся на месте.${folderPreparation?' Создание новых папок остановится после текущего запроса; уже созданные папки сохранятся.':''}</p><div class="modal-footer"><button class="soft-button" id="keepAllUploads">Продолжить загрузку</button><button class="primary-button" id="confirmCancelAllUploads">Прервать все</button></div>`,true)
    $('#keepAllUploads').onclick=closeModal
    $('#confirmCancelAllUploads').onclick=()=>{closeModal();void cancelAllUploads()}
    $('#keepAllUploads').focus()
  }
  async function cancelAllUploads(){
    folderUploadGeneration++
    if(folderPreparation)folderPreparation.cancelRequested=true
    uploadPanelCollapsed=false
    const unfinished=uploadQueue.filter(task=>['queued','uploading','error'].includes(task.status))
    for(const task of unfinished){task.cancelRequested=true;task.controller?.abort()}
    dismissFinishedUploads()
    const inactive=unfinished.filter(task=>task.status!=='uploading')
    const results=await Promise.allSettled(inactive.map(task=>discardUpload(task,false)))
    for(let i=0;i<results.length;i++)if(results[i].status==='rejected'){
      inactive[i].status='error';inactive[i].error=results[i].reason?.message||'Не удалось удалить недокачанный файл';inactive[i].cancelRequested=false
    }
    renderUploadPanel()
    if(results.some(result=>result.status==='rejected'))showToast('Не все недокачанные файлы удалось удалить. Проверьте ошибки в панели.')
    else showToast('Загрузки отменены')
  }
  async function uploadOne(task) {
    const {file,storage,path}=task, key=uploadKey(task)
    let session
    try {const saved=localStorage.getItem(key);if(saved){session=await api('/uploads/'+saved);if(session.storage!==storage||session.path!==path||session.size!==file.size)session=null}}catch{localStorage.removeItem(key)}
    if(task.cancelRequested)throw new Error('Загрузка отменена')
    if(!session){session=await api('/uploads',jsonBody({storage,path,size:file.size}));localStorage.setItem(key,session.id)}
    task.sessionId=session.id
    if(task.cancelRequested)throw new Error('Загрузка отменена')
    let offset=session.offset
    task.offset=offset;renderUploadPanel()
    while(offset<file.size){
      if(task.cancelRequested)throw new Error('Загрузка отменена')
      const controller=new AbortController()
      task.controller=controller
      try {const result=await api('/uploads/'+session.id,{method:'PATCH',headers:{'X-Upload-Offset':String(offset),'Content-Type':'application/octet-stream'},body:file.slice(offset,Math.min(offset+8*1024*1024,file.size)),signal:controller.signal});offset=result.offset}
      catch(error){if(task.cancelRequested)throw error;const current=await api('/uploads/'+session.id);if(current.offset===offset)throw error;offset=current.offset}
      finally{task.controller=null}
      task.offset=offset;renderUploadPanel()
    }
    if(task.cancelRequested)throw new Error('Загрузка отменена')
    task.phase='finishing';renderUploadPanel()
    await api('/uploads/'+session.id+'/complete',{method:'POST'});localStorage.removeItem(key)
  }
  async function drainUploadQueue() {
    if(uploadWorkerRunning)return
    uploadWorkerRunning=true
    try {
      while(nextPendingUpload<pendingUploadTasks.length){
        const task=pendingUploadTasks[nextPendingUpload++]
        if(task.status!=='queued'||task.cancelRequested)continue
        task.status='uploading';renderUploadPanel()
        try {
          await uploadOne(task)
          task.status='done';task.offset=task.file.size;renderUploadPanel()
          if(state.view==='all'&&state.storage===task.storage&&state.folder===task.folder)void load()
          void refreshStorages().catch(()=>{})
        } catch(error) {
          if(task.cancelRequested){try{await discardUpload(task)}catch(cleanupError){task.status='error';task.error=cleanupError.message;task.cancelRequested=false;renderUploadPanel()}}
          else if(task.folderBatch&&error.status===409&&error.message==='Такое имя уже существует'){
            try {
              if(task.sessionId)await api('/uploads/'+task.sessionId,{method:'DELETE'})
              localStorage.removeItem(uploadKey(task))
              task.status='skipped';task.error='Файл уже существует';renderUploadPanel()
            } catch(cleanupError){task.status='error';task.error=cleanupError.message;renderUploadPanel()}
          }
          else{task.status='error';task.error=error.message;renderUploadPanel()}
        }
      }
    } finally {pendingUploadTasks=[];nextPendingUpload=0;uploadWorkerRunning=false;renderUploadPanel()}
  }
  function addFiles(list, destination=state.folder) {
    const selected=[...list]
    if(!selected.length)return
    if(state.view!=='all')return showToast('Сначала откройте хранилище')
    const storage=state.storage
    if(!storage)return showToast('Выберите хранилище')
    const invalid=selected.find(file=>filenameBytes(file.name)>255)
    if(invalid){
      const suggested=suggestedFilename(invalid.name)
      const currentBytes=filenameBytes(invalid.name),suggestedBytes=filenameBytes(suggested)
      modal('Слишком длинное имя файла',`<p class="modal-note">«${esc(invalid.name)}» занимает <strong>${currentBytes} байт</strong>. Максимум для имени файла — <strong>255 байт</strong> в UTF-8. Русские буквы обычно занимают по 2 байта, поэтому одного ограничения в символах нет.</p><p class="modal-note">Например, можно сократить до <strong>${Array.from(suggested).length} символов (${suggestedBytes} байт)</strong>, сохранив расширение:</p><div class="filename-suggestion">${esc(suggested)}</div><p class="modal-note">Переименуйте файл на компьютере и выберите его снова. Загрузка не началась.</p><div class="modal-footer"><button class="primary-button" id="filenameUnderstood">Понятно</button></div>`,true)
      $('#filenameUnderstood').onclick=closeModal
      return
    }
    let added=0
    const activePaths=new Set(uploadQueue.filter(task=>task.storage===storage&&['queued','uploading'].includes(task.status)).map(task=>task.path))
    for(const file of selected){
      const path=[destination,file.name].filter(Boolean).join('/')
      if(activePaths.has(path))continue
      const task={id:++nextUploadId,file,storage,folder:destination,path,status:'queued',offset:0,error:''}
      uploadQueue.push(task);pendingUploadTasks.push(task);activePaths.add(path)
      added++
    }
    if($('#dropzone'))closeModal()
    uploadPanelCollapsed=false
    renderUploadPanel()
    if(added){showToast(added===1?'Файл добавлен в очередь':`Добавлено файлов в очередь: ${added}`);void drainUploadQueue()}
    else showToast('Эти файлы уже находятся в очереди')
  }
  document.addEventListener('click',e=>{
    if(e.target.closest('#retryStorage')){void refreshStorages(true).then(()=>load()).catch(error=>showToast(error.message));return}
    if(!e.target.closest('#profileButton,#profileMenu'))$('#profileMenu').hidden=true
    if(e.target.closest('[data-upload-toggle]')){uploadPanelCollapsed=!uploadPanelCollapsed;renderUploadPanel();return}
    if(e.target.closest('[data-upload-dismiss]')){confirmCancelAllUploads();return}
    const cancel=e.target.closest('[data-upload-cancel]')
    if(cancel){confirmCancelUpload(uploadQueue.find(item=>item.id===Number(cancel.dataset.uploadCancel)));return}
    const retry=e.target.closest('[data-upload-retry]')
    if(retry){const task=uploadQueue.find(item=>item.id===Number(retry.dataset.uploadRetry));if(task?.status==='error'){task.status='queued';task.error='';task.cancelRequested=false;task.phase='';pendingUploadTasks.push(task);renderUploadPanel();void drainUploadQueue()}return}
    const storage=e.target.closest('[data-storage]');if(storage)return selectStorage(storage.dataset.storage)
    const nav=e.target.closest('[data-nav]');if(nav)return selectView(nav.dataset.nav)
    if(e.target.closest('[data-trash-root]')){state.trashBrowse=null;state.selected=null;return load()}
    const trashCrumb=e.target.closest('[data-trash-crumb]')
    if(trashCrumb&&state.trashBrowse){state.trashBrowse.path=trashCrumb.dataset.trashCrumb;state.selected=null;return load()}
    const crumb=e.target.closest('[data-crumb]');if(crumb){state.folder=crumb.dataset.crumb;state.selected=null;return load()}
    const favorite=e.target.closest('[data-favorite]');if(favorite){e.stopPropagation();return action('favorite',fileById(favorite.dataset.favorite))}
    const more=e.target.closest('[data-more]');if(more){e.stopPropagation();const rect=more.getBoundingClientRect();return showContextMenu(fileById(more.dataset.more),rect.left,rect.bottom+5)}
    const menu=e.target.closest('[data-menu]');if(menu)return action(menu.dataset.menu,fileById($('#context').dataset.id))
    const row=e.target.closest('#rows .row');if(row){state.selected=row.dataset.id;document.querySelectorAll('#rows .row.selected').forEach(x=>x.classList.remove('selected'));row.classList.add('selected');closeMenu();return}
    if(!e.target.closest('#context'))closeMenu()
  })
  $('#breadcrumbs').addEventListener('dragover',e=>{
    const crumb=e.target.closest('[data-crumb]')
    if(!crumb||state.view!=='all'||externalFiles(e))return
    e.preventDefault();e.dataTransfer.dropEffect='move'
    $('#breadcrumbs').querySelectorAll('.crumb.drag-over').forEach(item=>item.classList.remove('drag-over'))
    crumb.classList.add('drag-over')
  })
  $('#breadcrumbs').addEventListener('dragleave',e=>{
    const crumb=e.target.closest('[data-crumb]')
    if(crumb&&!crumb.contains(e.relatedTarget))crumb.classList.remove('drag-over')
  })
  $('#breadcrumbs').addEventListener('drop',e=>{
    const crumb=e.target.closest('[data-crumb]')
    $('#breadcrumbs').querySelectorAll('.crumb.drag-over').forEach(item=>item.classList.remove('drag-over'))
    if(!crumb||state.view!=='all'||externalFiles(e))return
    e.preventDefault()
    const source=fileById(e.dataTransfer.getData('text/plain'))
    if(!source)return
    const target=[crumb.dataset.crumb,source.name].filter(Boolean).join('/')
    if(target===source.path)return
    void mutate(()=>api('/move',jsonBody({storage:source.storage,path:source.path,target})),'Файл перемещён')
  })
  $('#rows').addEventListener('contextmenu',e=>{const row=e.target.closest('.row');if(row){e.preventDefault();state.selected=row.dataset.id;render();showContextMenu(fileById(state.selected),e.clientX,e.clientY)}})
  $('#rows').addEventListener('dblclick',e=>{const row=e.target.closest('.row');if(row)openFile(fileById(row.dataset.id))})
  $('#rows').addEventListener('keydown',e=>{const row=e.target.closest('.row');if(row&&e.key==='Enter')openFile(fileById(row.dataset.id))})
  $('#rows').addEventListener('dragstart',e=>{const row=e.target.closest('.row');if(row)e.dataTransfer.setData('text/plain',row.dataset.id)})
  $('#rows').addEventListener('dragover',e=>{const row=e.target.closest('.row');if(row&&fileById(row.dataset.id)?.directory){e.preventDefault();row.classList.add('drag-over')}})
  $('#rows').addEventListener('dragleave',e=>{const row=e.target.closest('.row');if(row)row.classList.remove('drag-over')})
  $('#rows').addEventListener('drop',e=>{const row=e.target.closest('.row'),target=row&&fileById(row.dataset.id);if(row)row.classList.remove('drag-over');if(e.dataTransfer?.files?.length)return;const source=fileById(e.dataTransfer.getData('text/plain'));if(target?.directory&&source&&target.path!==source.path){e.preventDefault();mutate(()=>api('/move',jsonBody({storage:source.storage,path:source.path,target:target.path+'/'+source.name})),'Файл перемещён')}})
  $('#search').oninput=e=>{state.search=e.target.value;render()}
  $('#newFolderHeading').onclick=createFolder;$('#uploadTop').onclick=uploadModal
  $('#fileInput').onchange=e=>{addFiles(e.target.files);e.target.value=''}
  $('#folderInput').onchange=e=>{
    const manifest={files:[],dirs:new Set(),ignored:0}
    for(const file of e.target.files){
      const relativePath=file.webkitRelativePath||file.name
      manifest.files.push({file,relativePath})
      const parts=relativePath.split('/')
      for(let i=1;i<parts.length;i++)manifest.dirs.add(parts.slice(0,i).join('/'))
    }
    e.target.value=''
    if(!manifest.files.length)return showToast('В выбранной папке нет файлов. Пустую папку можно создать кнопкой «Новая папка».')
    prepareFolderManifest(manifest,state.folder,state.storage)
  }
  $('#menuToggle').onclick=()=>$('#sidebar').classList.toggle('open')
  $('#overlay').onclick=e=>{if(e.target===$('#overlay'))closeModal()}
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeModal();closeMenu();$('#sidebar').classList.remove('open')}})
  window.addEventListener('beforeunload',e=>{if(uploadQueue.some(task=>task.status==='queued'||task.status==='uploading')){e.preventDefault();e.returnValue=''}})
  window.addEventListener('dragover',e=>{
    if(!externalFiles(e))return
    e.preventDefault()
    e.dataTransfer.dropEffect='copy'
    if(state.view==='all'&&!$('#overlay').classList.contains('open'))fileDragHint.classList.add('open')
    clearTimeout(fileDragTimer)
    fileDragTimer=setTimeout(hideFileDrag,250)
  },true)
  window.addEventListener('drop',e=>{
    if(!externalFiles(e)&&!e.dataTransfer?.files?.length)return
    e.preventDefault()
    e.stopPropagation()
    hideFileDrag()
    const row=e.target.closest('#rows .row'),target=row&&fileById(row.dataset.id)
    const destination=target?.directory?target.path:state.folder
    const storage=state.storage
    const roots=[...(e.dataTransfer?.items||[])].map(item=>item.webkitGetAsEntry?.()||item.getAsEntry?.()).filter(Boolean)
    const fallbackFiles=[...(e.dataTransfer?.files||[])]
    void handleExternalDrop(roots,fallbackFiles,destination,storage)
  },true)
  refreshStorages().then(load).catch(e=>showToast(e.message))
  window.setInterval(()=>{void refreshStorages().catch(()=>{})},30000)
}

function showLogin() {
  const root=$('#app')
  root.innerHTML=`<div class="auth-screen"><div class="auth-card"><h1>Storage Space</h1><p>Войдите с помощью учётной записи Ark Messenger.</p><form id="authForm"><label>Email<input name="email" type="email" autocomplete="email" required></label><label id="codeField" hidden>Код из письма<input name="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code"></label><p id="authError" class="auth-error" hidden></p><button class="primary-button" id="authSubmit">Получить код</button><button class="auth-change" type="button" id="authChange" hidden>Другой email</button></form></div></div>`
  let codeStep=false
  const error=message=>{const target=$('#authError');target.hidden=false;target.textContent=message}
  $('#authChange').onclick=()=>{codeStep=false;$('#codeField').hidden=true;$('#authChange').hidden=true;$('#authSubmit').textContent='Получить код';$('#authForm').elements.email.disabled=false;$('#authError').hidden=true}
  $('#authForm').onsubmit=async event=>{
    event.preventDefault()
    const email=event.target.elements.email.value.trim().toLowerCase(),button=$('#authSubmit')
    button.disabled=true;$('#authError').hidden=true
    try {
      const response=await fetch(codeStep?'/api/auth/login':'/api/auth/identify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(codeStep?{email,code:event.target.elements.code.value}:{email})})
      const result=await response.json().catch(()=>({}))
      if(!response.ok||result.error)throw new Error(result.detail||'Проверьте email и код')
      if(codeStep)location.reload()
      else{codeStep=true;$('#codeField').hidden=false;$('#authChange').hidden=false;$('#authSubmit').textContent='Войти';event.target.elements.email.disabled=true;event.target.elements.code.focus()}
    }catch(e){error(e.message)}finally{button.disabled=false}
  }
}

async function start() {
  try {
    const response=await fetch('/api/me')
    if(!response.ok){showLogin();return}
    const user=await response.json()
    window.storageSpaceUser=user
    createApp({template:'#app-template',mounted(){init(user)}}).mount('#app')
  } catch { showLogin() }
}
start()
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', {updateViaCache:'none'}).catch(() => {})
  })
}
