import { createApp } from 'vue'
import './prototype.css'

const $ = selector => document.querySelector(selector)
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[ch])
const icon = (name, size=20) => `<svg width="${size}" height="${size}" aria-hidden="true"><use href="#i-${name}"/></svg>`
const params = object => new URLSearchParams(object).toString()
const jsonBody = value => ({ method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(value) })
const api = async (route, options={}) => {
  const response = await fetch('/api' + route, options)
  if (!response.ok) {
    let message = 'Ошибка запроса'
    try { const payload = await response.json(); message = typeof payload.detail === 'string' ? payload.detail : payload.detail?.message || message } catch {}
    const error = new Error(message); error.status = response.status; throw error
  }
  return response.json()
}
const gb = bytes => bytes == null ? '—' : (bytes / 1e9).toFixed(1).replace('.0','') + ' ГБ'
const size = bytes => bytes == null ? '—' : bytes >= 1e9 ? gb(bytes) : bytes >= 1e6 ? (bytes/1e6).toFixed(1).replace('.',',') + ' МБ' : bytes >= 1e3 ? Math.round(bytes/1e3) + ' КБ' : bytes + ' Б'
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

function init() {
  let storages = [], files = []
  let uploadBusy = false
  const fileDragHint=document.createElement('div')
  fileDragHint.className='file-drag-hint'
  fileDragHint.innerHTML=`${icon('upload',36)}<strong>Отпустите файлы для загрузки</strong><span>В текущее хранилище или в папку под курсором</span>`
  document.body.append(fileDragHint)
  let fileDragTimer
  const externalFiles=e=>Array.from(e.dataTransfer?.types||[]).includes('Files')
  const hideFileDrag=()=>{fileDragHint.classList.remove('open');clearTimeout(fileDragTimer)}
  const state = { storage:'', folder:'', view:'all', selected:null, search:'' }
  const currentStorage = () => storages.find(s => s.id === state.storage)
  const fileById = id => files.find(f => f.id === id)
  const fileUrl = (f, kind='preview') => '/api/' + kind + '?' + params({storage:f.storage,path:f.path})
  const asFile = (entry, index) => ({ ...entry, id:String(index), type:typeOf(entry), date:date(entry.modified), displaySize:entry.directory?'—':size(entry.size) })
  function showToast(message) { const target=$('#toast'); target.textContent=message; target.classList.add('open'); clearTimeout(window.toastTimer); window.toastTimer=setTimeout(()=>target.classList.remove('open'),3500) }
  function closeModal() { $('#overlay').classList.remove('open'); $('#modal').innerHTML='' }
  function modal(title, content, compact=false) { $('#modal').className='modal'+(compact?' compact':''); $('#modal').innerHTML=`<div class="modal-header"><h2>${esc(title)}</h2><button class="close" id="closeModal" aria-label="Закрыть">×</button></div>${content}`; $('#overlay').classList.add('open'); $('#closeModal').onclick=closeModal }
  function closeMenu() { $('#context').classList.remove('open') }
  function showContextMenu(f,x,y) {
    if (!f) return
    const c=$('#context')
    c.innerHTML=f.trashed?'<button data-menu="restore">Восстановить</button><button class="danger" data-menu="permanent">Удалить навсегда</button>':`${['video','audio','image','pdf'].includes(f.type)?'<button data-menu="preview">Открыть просмотр</button>':''}<button data-menu="favorite">${f.favorite?'Убрать из избранного':'Добавить в избранное'}</button>${f.type!=='folder'?'<button data-menu="download">Скачать</button>':''}<button data-menu="rename">Переименовать</button><button data-menu="move">Переместить</button><button class="danger" data-menu="trash">В корзину</button>`
    c.dataset.id=f.id; c.classList.add('open'); c.style.left=Math.max(8,Math.min(x,innerWidth-c.offsetWidth-8))+'px'; c.style.top=Math.max(8,Math.min(y,innerHeight-c.offsetHeight-8))+'px'
  }
  function render() {
    const active=currentStorage()
    $('#sideStorages').innerHTML=storages.map(s=>`<button class="storage ${s.id===state.storage&&state.view==='all'?'active':''}" data-storage="${esc(s.id)}" aria-label="${esc(s.name)}: ${s.online?'свободно '+gb(s.free)+' из '+gb(s.total):'недоступно'}">${icon('drive',18)}<span class="storage-info"><span class="storage-name">${esc(s.name)}</span><span class="storage-sub">${s.online?'Свободно '+gb(s.free)+' из '+gb(s.total):'Хранилище недоступно'}</span><span class="storage-meter"><i style="width:${s.online&&s.total?Math.round((s.total-s.free)/s.total*100):0}%"></i></span></span></button>`).join('')
    document.querySelectorAll('.nav').forEach(button=>button.classList.toggle('active',button.dataset.nav===state.view))
    $('#eyebrow').textContent=state.view==='trash'?'УДАЛЁННЫЕ ФАЙЛЫ':state.view==='all'?'РАБОЧЕЕ ПРОСТРАНСТВО':'БЫСТРЫЙ ДОСТУП'
    $('#pageTitle').textContent=state.view==='trash'?'Корзина':state.view==='recent'?'Недавние':state.view==='favorites'?'Избранное':state.folder?state.folder.split('/').at(-1):'Все файлы'
    $('#search').placeholder=state.view==='trash'?'Поиск в корзине':state.view==='recent'?'Поиск в недавних':state.view==='favorites'?'Поиск в избранном':'Поиск в текущей папке'
    $('#filesHead').style.display=state.view==='all'&&!!state.folder?'block':'none'
    const crumbs=[{name:active?.name||'Хранилище',path:''}]
    if (state.view==='all'&&state.folder) state.folder.split('/').forEach((name,i,all)=>crumbs.push({name,path:all.slice(0,i+1).join('/')}))
    $('#breadcrumbs').innerHTML=crumbs.map((crumb,i)=>`${i?'<span>›</span>':''}<button class="crumb" data-crumb="${esc(crumb.path)}">${esc(crumb.name)}</button>`).join('')
    const visible=files.filter(f=>(f.name||'').toLocaleLowerCase('ru').includes(state.search.toLocaleLowerCase('ru')))
    $('#rows').innerHTML=visible.length?visible.map(f=>`<div class="row ${state.selected===f.id?'selected':''}" data-id="${f.id}" tabindex="0" draggable="${state.view==='all'}"><div class="file-name"><span class="file-icon ${f.type}">${icon(f.type==='folder'?'folder':'file',21)}</span><span title="${esc(f.name)}">${esc(f.name)}</span>${!f.trashed?`<button class="favorite-toggle ${f.favorite?'active':''}" data-favorite="${f.id}" aria-label="${f.favorite?'Убрать из избранного':'Добавить в избранное'}: ${esc(f.name)}" title="${f.favorite?'Убрать из избранного':'Добавить в избранное'}">${icon('star',17)}</button>`:''}</div><span class="meta">${esc(f.date)}</span><span class="meta">${esc(f.displaySize)}</span><button class="more" data-more="${f.id}" aria-label="Действия с ${esc(f.name)}">•••</button></div>`).join(''):`<div class="empty">${icon('folder',42)}<h3>${state.search?'Ничего не найдено':state.view==='trash'?'Корзина пуста':state.view==='favorites'?'Пока нет избранных файлов и папок':'В этой папке пока пусто'}</h3><p>${state.search?'Попробуйте другой запрос':state.view==='favorites'?'Нажмите на звёздочку рядом с файлом или папкой, чтобы добавить сюда':'Файлы появятся здесь после добавления'}</p></div>`
  }
  async function load() {
    try {
      let entries
      if (state.view==='trash') entries=(await api('/trash')).map(e=>({id:e.id,trashId:e.id,storage:e.storage,path:e.original_path,name:e.original_path.split('/').at(-1),directory:false,size:null,modified:e.deleted_at,trashed:true}))
      else if (state.view==='favorites') entries=await api('/favorites')
      else if (state.view==='recent') { const batches=await Promise.all(storages.filter(s=>s.online).map(s=>api('/files?'+params({storage:s.id,path:''})).catch(()=>[]))); entries=batches.flat().filter(e=>!e.directory).sort((a,b)=>new Date(b.modified)-new Date(a.modified)).slice(0,50) }
      else entries=await api('/files?'+params({storage:state.storage,path:state.folder}))
      files=entries.map(asFile)
      if (state.view==='trash') files.forEach(f=>{f.id=f.trashId;f.date=date(f.modified)})
      render()
    } catch(e) { files=[];render();showToast(e.message) }
  }
  async function refreshStorages() { storages=await api('/storages'); if (!storages.some(s=>s.id===state.storage)) state.storage=storages[0]?.id||'';render() }
  async function selectStorage(id) { state.storage=id;state.folder='';state.view='all';state.selected=null;state.search='';$('#search').value='';$('#sidebar').classList.remove('open');await load() }
  async function selectView(view) { state.view=view;state.folder='';state.selected=null;state.search='';$('#search').value='';$('#sidebar').classList.remove('open');await load() }
  function openFile(f) { if (!f||f.trashed)return; if(f.directory){state.storage=f.storage;state.folder=f.path;state.view='all';state.search='';$('#search').value='';load()} else if(['video','audio','image','pdf'].includes(f.type))preview(f);else window.open(fileUrl(f,'download'),'_blank') }
  function preview(f) {
    const url=fileUrl(f)
    const content=f.type==='video'?`<video controls autoplay src="${url}"></video>`:f.type==='audio'?`<audio controls autoplay src="${url}"></audio>`:f.type==='image'?`<img src="${url}" alt="${esc(f.name)}">`:`<iframe src="${url}" title="${esc(f.name)}"></iframe>`
    modal(f.name,`<div class="real-preview">${content}</div><div class="modal-footer"><a class="primary-button" href="${fileUrl(f,'download')}">Скачать</a></div>`)
  }
  function download(f) { window.open(fileUrl(f,'download'),'_blank') }
  async function mutate(fn,success) { try {await fn();closeModal();closeMenu();await load();await refreshStorages();if(success)showToast(success)}catch(e){showToast(e.message)} }
  async function action(name,f) {
    closeMenu();if(!f)return
    if(name==='preview')return openFile(f)
    if(name==='download')return download(f)
    if(name==='favorite')return mutate(()=>api('/favorite',jsonBody({storage:f.storage,path:f.path})),f.favorite?'Убрано из избранного':'Добавлено в избранное')
    if(name==='trash')return mutate(()=>api('/trash',jsonBody({storage:f.storage,path:f.path})),'Перемещено в корзину')
    if(name==='restore')return mutate(()=>api('/trash/'+f.trashId+'/restore',{method:'POST'}),'Восстановлено')
    if(name==='permanent') {modal('Удалить окончательно?',`<p style="font-size:14px;line-height:1.5;color:#69768a">«${esc(f.name)}» нельзя будет восстановить.</p><div class="modal-footer"><button class="soft-button" id="cancelDelete">Отмена</button><button class="primary-button" id="confirmDelete">Удалить</button></div>`,true);$('#cancelDelete').onclick=closeModal;$('#confirmDelete').onclick=()=>mutate(()=>api('/trash/'+f.trashId,{method:'DELETE'}),'Удалено');return}
    if(name==='move')return moveModal(f)
    if(name==='rename') {const name=prompt('Новое имя',f.name);if(!name||name===f.name)return;const parent=f.path.split('/').slice(0,-1).join('/');return mutate(()=>api('/move',jsonBody({storage:f.storage,path:f.path,target:[parent,name].filter(Boolean).join('/')})),'Переименовано')}
  }
  function moveModal(f) {
    const folders=files.filter(x=>x.directory&&!x.trashed&&x.storage===f.storage&&x.path!==f.path)
    modal('Переместить файл',`<div class="field">Выберите папку в хранилище «${esc(storages.find(s=>s.id===f.storage)?.name)}»</div><div class="folder-options"><button class="folder-option active" data-destination="">Корень хранилища</button>${folders.map(x=>`<button class="folder-option" data-destination="${esc(x.path)}">${icon('folder',16)} ${esc(x.name)}</button>`).join('')}</div><div class="modal-footer"><button class="soft-button" id="cancelMove">Отмена</button><button class="primary-button" id="confirmMove">Переместить</button></div>`,true)
    let destination='';$('#cancelMove').onclick=closeModal;$('#modal').onclick=e=>{const option=e.target.closest('[data-destination]');if(option){destination=option.dataset.destination;document.querySelectorAll('.folder-option').forEach(x=>x.classList.remove('active'));option.classList.add('active')}}
    $('#confirmMove').onclick=()=>mutate(()=>api('/move',jsonBody({storage:f.storage,path:f.path,target:[destination,f.name].filter(Boolean).join('/')})),'Перемещено')
  }
  function createFolder() {
    if(state.view!=='all')return showToast('Сначала откройте хранилище')
    modal('Новая папка','<label class="field" for="folderName">Название папки</label><input class="text-field" id="folderName" placeholder="Например, Материалы"><div class="modal-footer"><button class="soft-button" id="cancelFolder">Отмена</button><button class="primary-button" id="confirmFolder">Создать</button></div>',true)
    $('#folderName').focus();$('#cancelFolder').onclick=closeModal;$('#confirmFolder').onclick=()=>{const name=$('#folderName').value.trim();if(name)mutate(()=>api('/folders',jsonBody({storage:state.storage,path:[state.folder,name].filter(Boolean).join('/')})),'Папка создана')};$('#folderName').onkeydown=e=>{if(e.key==='Enter')$('#confirmFolder').click()}
  }
  function uploadModal() {
    if(state.view!=='all')return showToast('Сначала откройте хранилище')
    modal('Загрузить файлы',`<div class="dropzone" id="dropzone">${icon('upload',32)}<strong>Перетащите файлы сюда</strong><p>или выберите их на устройстве</p><button class="soft-button" id="chooseFiles">Выбрать файлы</button></div><p class="modal-note" id="uploadStatus">Большие файлы загружаются частями. После обрыва выберите тот же файл для продолжения.</p>`,true)
    $('#chooseFiles').onclick=()=>$('#fileInput').click();const zone=$('#dropzone');zone.ondragover=e=>{e.preventDefault();zone.classList.add('drag-over')};zone.ondragleave=()=>zone.classList.remove('drag-over');zone.ondrop=e=>{e.preventDefault();addFiles(e.dataTransfer.files)}
  }
  async function uploadOne(file,storage,folder) {
    const path=[folder,file.name].filter(Boolean).join('/'),key='storagespace-upload:'+storage+':'+path+':'+file.size+':'+file.lastModified
    let session
    try {const saved=localStorage.getItem(key);if(saved){session=await api('/uploads/'+saved);if(session.storage!==storage||session.path!==path||session.size!==file.size)session=null}}catch{localStorage.removeItem(key)}
    if(!session){session=await api('/uploads',jsonBody({storage,path,size:file.size}));localStorage.setItem(key,session.id)}
    let offset=session.offset
    while(offset<file.size){
      const status=$('#uploadStatus');if(status)status.textContent=`${file.name}: ${Math.round(offset/file.size*100)}% · ${size(offset)} из ${size(file.size)}`
      try {const result=await api('/uploads/'+session.id,{method:'PATCH',headers:{'X-Upload-Offset':String(offset),'Content-Type':'application/octet-stream'},body:file.slice(offset,Math.min(offset+8*1024*1024,file.size))});offset=result.offset}
      catch(error){const current=await api('/uploads/'+session.id);if(current.offset===offset)throw error;offset=current.offset}
    }
    await api('/uploads/'+session.id+'/complete',{method:'POST'});localStorage.removeItem(key)
    const status=$('#uploadStatus');if(status)status.textContent=`${file.name}: 100% · загрузка завершена`
  }
  async function addFiles(list, destination=state.folder) {
    const selected=[...list]
    if(!selected.length)return
    if(uploadBusy)return showToast('Загрузка уже идёт')
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
    uploadBusy=true
    if(!$('#overlay').classList.contains('open'))modal('Загрузка файлов',`<p class="modal-note" id="uploadStatus">Подготовка загрузки…</p>`,true)
    try {
      for(const file of selected)await uploadOne(file,storage,destination)
      await load();await refreshStorages()
      showToast('Файлы загружены: '+selected.length)
      closeModal()
    } catch(e) {
      const status=$('#uploadStatus');if(status)status.textContent=`Ошибка: ${e.message}. Повторно выберите тот же файл для продолжения.`
      showToast(e.message)
    } finally {uploadBusy=false}
  }
  document.addEventListener('click',e=>{
    const storage=e.target.closest('[data-storage]');if(storage)return selectStorage(storage.dataset.storage)
    const nav=e.target.closest('[data-nav]');if(nav)return selectView(nav.dataset.nav)
    const crumb=e.target.closest('[data-crumb]');if(crumb){state.folder=crumb.dataset.crumb;state.selected=null;return load()}
    const favorite=e.target.closest('[data-favorite]');if(favorite){e.stopPropagation();return action('favorite',fileById(favorite.dataset.favorite))}
    const more=e.target.closest('[data-more]');if(more){e.stopPropagation();const rect=more.getBoundingClientRect();return showContextMenu(fileById(more.dataset.more),rect.left,rect.bottom+5)}
    const menu=e.target.closest('[data-menu]');if(menu)return action(menu.dataset.menu,fileById($('#context').dataset.id))
    const row=e.target.closest('#rows .row');if(row){state.selected=row.dataset.id;document.querySelectorAll('#rows .row.selected').forEach(x=>x.classList.remove('selected'));row.classList.add('selected');closeMenu();return}
    if(!e.target.closest('#context'))closeMenu()
  })
  $('#rows').addEventListener('contextmenu',e=>{const row=e.target.closest('.row');if(row){e.preventDefault();state.selected=row.dataset.id;render();showContextMenu(fileById(state.selected),e.clientX,e.clientY)}})
  $('#rows').addEventListener('dblclick',e=>{const row=e.target.closest('.row');if(row)openFile(fileById(row.dataset.id))})
  $('#rows').addEventListener('keydown',e=>{const row=e.target.closest('.row');if(row&&e.key==='Enter')openFile(fileById(row.dataset.id))})
  $('#rows').addEventListener('dragstart',e=>{const row=e.target.closest('.row');if(row)e.dataTransfer.setData('text/plain',row.dataset.id)})
  $('#rows').addEventListener('dragover',e=>{const row=e.target.closest('.row');if(row&&fileById(row.dataset.id)?.directory){e.preventDefault();row.classList.add('drag-over')}})
  $('#rows').addEventListener('dragleave',e=>{const row=e.target.closest('.row');if(row)row.classList.remove('drag-over')})
  $('#rows').addEventListener('drop',e=>{const row=e.target.closest('.row'),target=row&&fileById(row.dataset.id);if(row)row.classList.remove('drag-over');if(e.dataTransfer?.files?.length)return;const source=fileById(e.dataTransfer.getData('text/plain'));if(target?.directory&&source&&target.path!==source.path){e.preventDefault();mutate(()=>api('/move',jsonBody({storage:source.storage,path:source.path,target:target.path+'/'+source.name})),'Файл перемещён')}})
  $('#search').oninput=e=>{state.search=e.target.value;render()}
  $('#newFolderTop').onclick=createFolder;$('#newFolderHeading').onclick=createFolder;$('#uploadTop').onclick=uploadModal
  $('#fileInput').onchange=e=>{addFiles(e.target.files);e.target.value=''}
  $('#menuToggle').onclick=()=>$('#sidebar').classList.toggle('open')
  $('#overlay').onclick=e=>{if(e.target===$('#overlay'))closeModal()}
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeModal();closeMenu();$('#sidebar').classList.remove('open')}})
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
    addFiles(e.dataTransfer.files,target?.directory?target.path:state.folder)
  },true)
  refreshStorages().then(load).catch(e=>showToast(e.message))
}

createApp({template:'#app-template',mounted:init}).mount('#app')
