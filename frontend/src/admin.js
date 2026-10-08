import './admin.css'

const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]))
const request = async (path, method='GET', body) => {
  const response = await fetch('/api/admin'+path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined})
  if(!response.ok){const result=await response.json().catch(()=>({}));throw new Error(result.detail||'Не удалось сохранить')}
  return response.json()
}

export async function openAdmin() {
  const panel=document.createElement('div')
  panel.className='admin-screen'
  panel.innerHTML='<div class="admin-shell"><div class="admin-loading">Загрузка настроек…</div></div>'
  document.body.append(panel)
  let data, tab='access', editingRole=null, editingGroup=null, auditPage=null
  const shell=panel.querySelector('.admin-shell')
  const close=()=>panel.remove()
  const nameOf=(type,id)=>type==='role'?data.roles.find(x=>x.id===id)?.name:type==='group'?data.groups.find(x=>x.id===id)?.name:data.users.find(x=>x.id===id)?.email
  const message=error=>{const note=shell.querySelector('.admin-message');if(note){note.textContent=error.message||String(error);note.hidden=false}}
  const run=async work=>{try{await work();data=await request('/overview');render()}catch(error){message(error)}}
  const field=(label,name,value='',extra='')=>`<label class="admin-field">${label}<input name="${name}" value="${esc(value)}" ${extra}></label>`
  const options=(items,selected)=>items.map(item=>`<option value="${esc(item.id)}" ${String(item.id)===String(selected)?'selected':''}>${esc(item.name||item.email)}</option>`).join('')
  function renderRoles(){
    return `<div class="admin-section-head"><div><h2>Роли</h2><p>Идентификатор Ark связывает роль с учётной записью. Права привязаны к внутреннему числовому ID.</p></div></div>
      <div class="admin-list">${data.roles.map(role=>`<div class="admin-list-row"><div><strong>${esc(role.name)}</strong><small>Внутренний ID ${role.id} · Ark: <code>${esc(role.ark_name)}</code>${role.system?' · системная':''}</small></div><button class="soft-button" data-edit-role="${role.id}">Изменить</button>${role.system?'':`<button class="admin-text-button danger" data-delete-role="${role.id}">Удалить</button>`}</div>`).join('')}</div>
      <form class="admin-form" id="roleForm"><h3>${editingRole?'Изменить роль':'Новая роль'}</h3>${field('Название','name',editingRole?.name,'required maxlength="80"')}${field('Идентификатор Ark','ark_name',editingRole?.ark_name,editingRole?.system?'readonly':'required maxlength="80" pattern="[A-Za-z0-9_-]+"')}<div class="admin-form-actions"><button class="primary-button" type="submit">Сохранить</button>${editingRole?'<button class="soft-button" type="button" data-cancel-edit>Отмена</button>':''}</div></form>`
  }
  function renderGroups(){
    return `<div class="admin-section-head"><div><h2>Группы</h2><p>Локальные группы позволяют выдавать общий доступ нескольким пользователям.</p></div></div>
      <div class="admin-list">${data.groups.map(group=>`<div class="admin-list-row"><div><strong>${esc(group.name)}</strong><small>ID ${group.id} · ${data.members.filter(m=>m.group_id===group.id).map(m=>data.users.find(u=>u.id===m.user_id)?.email).filter(Boolean).map(esc).join(', ')||'Нет участников'}</small></div><button class="soft-button" data-edit-group="${group.id}">Изменить</button><button class="admin-text-button danger" data-delete-group="${group.id}">Удалить</button></div>`).join('')||'<p class="admin-empty">Групп пока нет.</p>'}</div>
      <form class="admin-form" id="groupForm"><h3>${editingGroup?'Изменить группу':'Новая группа'}</h3>${field('Название','name',editingGroup?.name,'required maxlength="80"')}<div class="admin-form-actions"><button class="primary-button" type="submit">Сохранить</button>${editingGroup?'<button class="soft-button" type="button" data-cancel-edit>Отмена</button>':''}</div></form>
      ${data.groups.length&&data.users.length?`<form class="admin-form" id="memberForm"><h3>Добавить участника</h3><label class="admin-field">Группа<select name="group_id">${options(data.groups)}</select></label><label class="admin-field">Пользователь<select name="user_id">${options(data.users)}</select></label><button class="primary-button">Добавить</button></form>`:''}
      <div class="admin-members">${data.members.map(m=>`<div><span>${esc(nameOf('user',m.user_id))} · ${esc(nameOf('group',m.group_id))}</span><button data-remove-member="${m.group_id}:${m.user_id}" aria-label="Убрать из группы">×</button></div>`).join('')}</div>`
  }
  function renderUsers(){
    return `<div class="admin-section-head"><div><h2>Пользователи</h2><p>Пользователь появляется после первого входа через Ark. Отключение и отзыв сессий действуют сразу.</p></div></div><div class="admin-list">${data.users.map(user=>`<div class="admin-list-row"><div><strong>${esc([user.first_name,user.last_name].filter(Boolean).join(' ')||user.email)}</strong><small>${esc(user.email)} · ID ${user.id} · ${data.user_roles.filter(item=>item.user_id===user.id).map(item=>nameOf('role',item.role_id)).filter(Boolean).map(esc).join(', ')||'Нет сопоставленной роли'} · ${user.active?'Активен':'Отключён'}</small></div><button class="soft-button" data-revoke="${user.id}">Завершить сессии</button><button class="admin-text-button ${user.active?'danger':''}" data-toggle-user="${user.id}" ${user.id===data.self_id?'disabled':''}>${user.active?'Отключить':'Включить'}</button></div>`).join('')||'<p class="admin-empty">Пользователей пока нет.</p>'}</div>`
  }
  function renderPersonalFolders(){
    return `<div class="admin-section-head"><div><h2>Персональные папки</h2><p>Выберите существующую папку. Её прямые подпапки с именами user-ID будут показаны по имени пользователя из Ark. Названия на диске и права доступа не меняются.</p></div></div>
      <div class="admin-list">${data.personal_folders.map(item=>`<div class="admin-list-row"><div><strong>${esc(item.label)}</strong><small>${esc(data.storages.find(x=>x.id===item.storage)?.name||item.storage)} / ${esc(item.path)}</small></div><button class="admin-text-button danger" data-delete-personal="${esc(item.storage)}">Убрать</button></div>`).join('')||'<p class="admin-empty">Настроенных папок пока нет.</p>'}</div>
      <form class="admin-form" id="personalForm"><h3>Настроить папку</h3><label class="admin-field">Хранилище<select name="storage">${options(data.storages)}</select></label>${field('Путь к существующей папке','path','','required placeholder="Например, Users"')}${field('Название в интерфейсе','label','','required maxlength="80" placeholder="Например, Пользовательские"')}<button class="primary-button">Сохранить</button></form>`
  }
  function renderAccess(){
    const subjects=[...data.roles.map(x=>({id:'role:'+x.id,name:'Роль · '+x.name})),...data.groups.map(x=>({id:'group:'+x.id,name:'Группа · '+x.name})),...data.users.map(x=>({id:'user:'+x.id,name:'Пользователь · '+x.email}))]
    return `<div class="admin-section-head"><div><h2>Доступ к хранилищам</h2><p>Разрешение на папку действует и во всех вложенных папках. Пустой путь означает всё хранилище.</p></div></div>
      <div class="admin-list">${data.grants.map(grant=>`<div class="admin-list-row"><div><strong>${esc(nameOf(grant.subject_type,grant.subject_id)||'Удалённый получатель')}</strong><small>${esc(data.storages.find(s=>s.id===grant.storage)?.name||grant.storage)}${grant.path?' / '+esc(grant.path):' · целиком'} · ${grant.level===2?'Изменение':'Просмотр'}</small></div><button class="admin-text-button danger" data-delete-grant="${grant.id}">Убрать доступ</button></div>`).join('')||'<p class="admin-empty">Разрешений пока нет.</p>'}</div>
      <form class="admin-form" id="grantForm"><h3>Выдать доступ</h3><label class="admin-field">Кому<select name="subject">${options(subjects)}</select></label><label class="admin-field">Хранилище<select name="storage">${options(data.storages)}</select></label>${field('Путь к папке (пусто — всё хранилище)','path','','placeholder="Например, Проекты/2026"')}<label class="admin-field">Уровень<select name="level"><option value="1">Просмотр и скачивание</option><option value="2">Изменение файлов</option></select></label><button class="primary-button">Выдать доступ</button></form>`
  }
  function renderAudit(){
    const names={mkdir:'Новая папка',upload:'Загрузка',upload_cancel:'Отмена загрузки',move:'Перемещение',copy:'Копирование',trash:'В корзину',restore:'Восстановление',delete_forever:'Удаление навсегда'}
    return `<div class="admin-section-head"><div><h2>Журнал изменений файлов</h2><p>Записываются завершённые операции изменения. Просмотры и скачивания сюда не входят.</p></div></div>
      <div class="admin-list">${auditPage?.items?.map(item=>`<div class="admin-list-row"><div><strong>${esc(names[item.operation]||item.operation)} · ${esc(item.path)}</strong><small>${esc(new Date(item.at).toLocaleString('ru-RU'))} · ${esc(item.user_email||'Пользователь не указан')} · ${esc(data.storages.find(s=>s.id===item.storage)?.name||item.storage)}${item.target?' → '+esc(item.target):''}</small></div></div>`).join('')||'<p class="admin-empty">Записей пока нет.</p>'}</div>
      ${auditPage?.next_before?'<button class="soft-button admin-more" data-more-audit>Показать ещё</button>':''}`
  }
  function render(){
    data.self_id=data.self_id||window.storageSpaceUser?.id
    shell.innerHTML=`<header class="admin-header"><div><span>Storage Space</span><h1>Администрирование</h1></div><button class="admin-close" aria-label="Закрыть администрирование">×</button></header><nav class="admin-tabs">${[['access','Доступ'],['personal','Папки'],['users','Пользователи'],['roles','Роли'],['groups','Группы'],['audit','Журнал']].map(([key,label])=>`<button data-admin-tab="${key}" class="${tab===key?'active':''}">${label}</button>`).join('')}</nav><div class="admin-content"><p class="admin-message" hidden></p>${tab==='roles'?renderRoles():tab==='groups'?renderGroups():tab==='users'?renderUsers():tab==='personal'?renderPersonalFolders():tab==='audit'?renderAudit():renderAccess()}</div>`
  }
  panel.addEventListener('click',event=>{
    const target=event.target.closest('button')
    if(!target)return
    if(target.classList.contains('admin-close'))return close()
    if(target.dataset.adminTab){tab=target.dataset.adminTab;editingRole=null;editingGroup=null;if(tab==='audit'){request('/audit').then(page=>{auditPage=page;render()}).catch(message)}else render();return}
    if(target.hasAttribute('data-more-audit')){request('/audit?before='+auditPage.next_before).then(page=>{auditPage={items:[...auditPage.items,...page.items],next_before:page.next_before};render()}).catch(message);return}
    if(target.dataset.editRole){editingRole=data.roles.find(x=>x.id===Number(target.dataset.editRole));render();return}
    if(target.dataset.editGroup){editingGroup=data.groups.find(x=>x.id===Number(target.dataset.editGroup));render();return}
    if(target.hasAttribute('data-cancel-edit')){editingRole=null;editingGroup=null;render();return}
    if(target.dataset.deleteRole){if(confirm('Удалить роль и все выданные ей права?'))run(()=>request('/roles/'+target.dataset.deleteRole,'DELETE'));return}
    if(target.dataset.deleteGroup){if(confirm('Удалить группу и все её права?'))run(()=>request('/groups/'+target.dataset.deleteGroup,'DELETE'));return}
    if(target.dataset.deleteGrant){run(()=>request('/grants/'+target.dataset.deleteGrant,'DELETE'));return}
    if(target.dataset.deletePersonal){run(()=>request('/personal-folders/'+encodeURIComponent(target.dataset.deletePersonal),'DELETE'));return}
    if(target.dataset.removeMember){const [group,user]=target.dataset.removeMember.split(':');run(()=>request(`/groups/${group}/members/${user}`,'DELETE'));return}
    if(target.dataset.revoke){if(confirm('Завершить все активные сессии пользователя?'))run(()=>request('/users/'+target.dataset.revoke+'/revoke','POST'));return}
    if(target.dataset.toggleUser){const user=data.users.find(x=>x.id===Number(target.dataset.toggleUser));if(confirm(user.active?'Отключить доступ пользователя?':'Включить доступ пользователя?'))run(()=>request('/users/'+user.id,'PUT',{active:!user.active}));return}
  })
  panel.addEventListener('submit',event=>{
    event.preventDefault()
    const form=event.target, values=Object.fromEntries(new FormData(form))
    if(form.id==='roleForm')run(async()=>{await request(editingRole?'/roles/'+editingRole.id:'/roles',editingRole?'PUT':'POST',values);editingRole=null})
    if(form.id==='groupForm')run(async()=>{await request(editingGroup?'/groups/'+editingGroup.id:'/groups',editingGroup?'PUT':'POST',values);editingGroup=null})
    if(form.id==='memberForm')run(()=>request('/groups/'+values.group_id+'/members','POST',{user_id:Number(values.user_id)}))
    if(form.id==='grantForm'){const [subject_type,subject_id]=values.subject.split(':');run(()=>request('/grants','POST',{subject_type,subject_id:Number(subject_id),storage:values.storage,path:values.path.trim(),level:Number(values.level)}))}
    if(form.id==='personalForm')run(()=>request('/personal-folders','PUT',{storage:values.storage,path:values.path.trim(),label:values.label.trim()}))
  })
  try{data=await request('/overview');render()}catch(error){shell.innerHTML=`<div class="admin-loading">${esc(error.message)} <button class="soft-button admin-close">Закрыть</button></div>`}
}
