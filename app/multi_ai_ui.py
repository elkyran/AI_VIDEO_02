"""Loopback-only control panel for the optional multi-AI development mode.

The existing studio CLI and production registry are not opened by this launcher.
Real clients are opt-in; simulation stays available without network or credentials.
"""
from __future__ import annotations

import argparse
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlparse
import webbrowser

from .registry import StudioError, content_hash, workspace_path
from .ai_adapters import sanitize


PAGE = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI_VIDEO_02 · Команда ИИ</title><style>
:root{color-scheme:dark;font:15px/1.5 system-ui,sans-serif;background:#10141b;color:#e4eaf2}
*{box-sizing:border-box}body{margin:0}header{padding:22px 28px;border-bottom:1px solid #354052;background:#171f2b}
header h1{margin:0;font-size:25px}small,.muted{color:#aab8cd}button,input,select,textarea{font:inherit;border:1px solid #485971;border-radius:6px;padding:8px;background:#202c3c;color:inherit}
button{cursor:pointer}button:hover{background:#344b68}button:disabled{opacity:.5;cursor:wait}button.primary{background:#244f47;border-color:#5caa98}
button.danger{border-color:#b57074}label{display:block;margin:10px 0}textarea{width:100%;min-height:110px}input{max-width:100%}
nav{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}nav button[aria-selected=true]{background:#3b5678}
main{max-width:1400px;margin:auto;padding:0 28px 30px}.bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:16px 0}
.banner{background:#2b3027;border:1px solid #67714d;padding:12px 16px;border-radius:8px;margin:18px 0}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px}
.card{border:1px solid #35445a;border-radius:10px;padding:18px;background:#18212e;overflow-wrap:anywhere}.card h2,.card h3{margin:0 0 10px}.active{border-color:#80c7b3}
.badge{font-size:12px;padding:3px 7px;background:#314135;border-radius:4px}.error{background:#4c252e;color:#ffd2d7;padding:12px;white-space:pre-wrap}.notice{padding:8px 0;color:#a9d5c6}
table{width:100%;border-collapse:collapse}th,td{text-align:left;vertical-align:top;padding:10px;border-bottom:1px solid #35445a}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:400px;overflow:auto}
.scroll{overflow:auto}h2{font-size:20px}h3{font-size:17px}section[hidden]{display:none}a{color:#97c9ff}video{max-width:100%;max-height:480px;background:#000}.wide{grid-column:1/-1}#profiles{grid-template-columns:repeat(4,minmax(0,1fr))}#profiles>h3{grid-column:1/-1;margin:14px 0 0}@media(max-width:1100px){#profiles{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:760px){#profiles{grid-template-columns:1fr}}
@media(max-width:760px){.cards{grid-template-columns:1fr}main,header{padding-left:14px;padding-right:14px}}
</style></head><body><header><h1>AI_VIDEO_02 <small> / команда ИИ</small></h1>
<small>Сценарий → режиссура → проверка · существующая студия</small></header><main>
<div class="banner" id="mode-banner"><b>Офлайн · симуляция</b> — ответы ролей создаёт FakeAdapter. Реальные аккаунты не подключены. Качество сценария и видео моделями не проверено.</div>
<div id="error" class="error" role="alert" hidden></div><div id="notice" class="notice" role="status"></div>
<div class="bar"><button class="primary" data-action="demo" id="demo-button">Новая тестовая цепочка</button><label id="chain-mode-label" hidden>Режим цепочки <select id="chain-mode"><option value="auto">По доступности редактора</option><option value="gpt_only">GPT-only · текстовая самопроверка</option><option value="gemini">С редактором Gemini</option></select></label><button class="primary" id="live-button" hidden>Короткая сцена → план → проверка</button><span id="chain-mode-status" class="muted"></span><button data-action="resume">Продолжить</button><button data-action="pause">Пауза</button><button class="danger" data-action="stop">Остановить</button><span id="control" class="muted"></span></div>
<nav role="tablist"><button data-tab="release" aria-selected="true">Выпуск</button><button data-tab="accounts">Профили</button><button data-tab="scenes">Сцены</button><button data-tab="rules">Правила и роли</button><button data-tab="results">Результаты</button></nav>
<section id="release"><div id="roles" class="cards"></div><h2>Очередь</h2><p id="counts" class="muted"></p><div class="scroll"><table><thead><tr><th>Задача</th><th>Роль / ресурс</th><th>Состояние</th><th>Попытка / причина</th></tr></thead><tbody id="tasks"></tbody></table></div><h2>Наблюдаемые события</h2><pre id="events"></pre></section>
<section id="accounts" hidden><h2>Восемь профилей: 4 GPT + 4 Gemini</h2><p class="muted" id="account-mode">Метки симуляции не подтверждают личность или квоту настоящего аккаунта.</p><p class="muted">Номер — порядковая позиция профиля, а не остаток квоты.</p>
<div id="account-wizard" hidden class="card"><h3>Последовательное подключение</h3><p>GPT-1…4, затем Gemini-1…4. Сохранённые успешные проверки не требуют повторного входа. Одновременно открывается один официальный вход. Пароли и MFA вводятся только в окне провайдера.</p><p id="wizard-status"></p><div class="bar"><button data-action="wizard_start">Подключить все аккаунты</button><button data-action="wizard_pause">Пауза мастера</button><button data-action="wizard_resume">Продолжить мастер</button></div><label>Причина пропуска <input id="skip-reason" placeholder="Например: нет подписки"></label><button id="wizard-skip">Пропустить текущий профиль</button>
<form id="identity-form" hidden><h3 id="identity-title"></h3><p id="identity-evidence"></p><div id="identity-manual"><p>После входа закройте терминал клиента. Укажите метку аккаунта (например, email). Секреты здесь не нужны.</p><label>Метка аккаунта <input id="identity-label" autocomplete="off" maxlength="160"></label><label><input type="checkbox" id="identity-distinct"> Это отдельный аккаунт, не повтор другого слота этого сервиса</label><label><input type="checkbox" id="identity-auth"> В официальном клиенте вход выполнен; выбран именно этот аккаунт</label></div><div id="identity-subscription"><label><input type="checkbox" id="identity-billing"> Проверил официальные настройки: использую включённый лимит подписки или Google individual; дополнительные платные кредиты и автосписания отключены</label><p class="muted">Настройки оплаты подтверждает владелец. Нулевой баланс кредитов сам по себе не доказывает отключение автопокупки. При неизвестном режиме запрос блокируется.</p></div><div id="identity-api-free" hidden><p id="identity-project"></p><label><input type="checkbox" id="identity-key-saved"> Ключ именно этого проекта введён и сохранён только в официальном диалоге Gemini CLI; клиент закрыт</label><label><input type="checkbox" id="identity-free-tier"> В AI Studio у этого проекта указан Free Tier</label><label><input type="checkbox" id="identity-no-billing"> К этому проекту не привязан Cloud Billing</label><p class="muted">Подтверждение относится к проекту, а не к личности Google. «No credits» не означает Free Tier. Ключ сюда не вводится. После подтверждения выполняется короткая проверка доступа, если он ещё не проверен.</p></div><button type="submit" id="identity-submit">Сохранить подтверждение</button><button type="button" id="identity-cancel">Закрыть форму</button></form></div>
<div class="bar"><label>Режим <select id="selection-mode"><option value="per_provider">По провайдерам</option><option value="group">Общая группа</option></select></label><label>GPT <select id="codex-slot"><option>1</option><option>2</option><option>3</option><option>4</option></select></label><label>Gemini <select id="gemini-slot"><option>1</option><option>2</option><option>3</option><option>4</option></select></label><button id="select-profiles">Выбрать</button></div>
<p id="selection-status" class="muted"></p><div id="profiles" class="cards"></div><label><input type="checkbox" id="rotation"> Проверять переход после квоты в симуляторе (реальная ротация отключена)</label><button id="save-rotation">Сохранить режим симуляции</button></section>
<section id="scenes" hidden><h2>Версии сцен</h2><p class="muted">Правка создаёт новую версию и помечает зависимые результаты устаревшими. Озвучка и художественный preview требуют реального производственного задания.</p><div id="scene-list" class="cards"></div></section>
<section id="rules" hidden><h2>Назначения и инструкции</h2><p class="muted">Настройки применяются к новым заданиям. Модель реального клиента ещё не проверена; автоматической подмены модели нет.</p><div id="role-settings" class="cards"></div></section>
<section id="results" hidden><h2>Сохранённые результаты</h2><p class="muted">Технический preview проверяет исполнение монтажа. Он не является выпуском и не доказывает художественное качество.</p><div class="bar"><button id="preview-button">Собрать технический preview (3 секунды)</button><span id="preview-status"></span></div><div id="preview"></div><div id="artifacts" class="cards"></div></section>
</main><script>
const csrf='__CSRF__';let state={},lastEvent=0,editing=false,profileEditing=false,identityProfile=null,identityFreeRoute=false;const freeRouteDrafts={};
const $=id=>document.getElementById(id), rolesNames={writer:'Сценарист',director:'Режиссёр',critic:'Критик'};
const actionsNames={demo:'Тестовая цепочка запущена',resume:'Очередь продолжена',pause:'Выдача новых задач приостановлена',stop:'Управляемые задания остановлены',select_profiles:'Выбор профилей сохранён',set_rotation:'Режим симуляции сохранён',update_scene:'Новая версия сцены сохранена',configure_role:'Назначение роли сохранено',preview:'Техническая сборка поставлена в очередь',account_prepare_gemini_free:'Gemini Free Tier подготовлен. Для ввода ключа нажмите «Подключить»; запрос к модели ещё не отправлен.'};
const statusNames={pending:'Ожидает',running:'Работает',completed:'Результат сохранён',waiting_access:'Ожидает доступа',blocked:'Заблокировано',failed:'Ошибка',interrupted:'Прервано',stale:'Устарело',ready:'Доступен в симуляторе',not_configured:'Реальный вход не настроен'};
function node(tag,text,cls){let el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el}
function showError(e){$('error').hidden=false;$('error').textContent=e.message||String(e)}
async function request(path,body){let r=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});let data=await r.json();if(!r.ok)throw Error(data.error||JSON.stringify(data));return data}
async function action(name,args={}){$('error').hidden=true;try{let result=await request('/api/action',{action:name,...args});if(name==='live_chain')$('chain-mode-status').textContent=result.mode==='gpt_only'?'GPT-only: отдельная текстовая самопроверка, без независимой экспертизы Gemini.':'Редактор Gemini: проверка текста и плана.';$('notice').textContent=actionsNames[name]||'Готово';await refresh();return true}catch(e){showError(e);return false}}
function card(title){let el=node('article',undefined,'card');el.append(node('h3',title));return el}
function rows(value){return Array.isArray(value)?value:Object.values(value||{})}
function render(){let live=!!state.accounts; $('demo-button').hidden=live;$('live-button').hidden=!live;$('chain-mode-label').hidden=!live;$('account-wizard').hidden=!live;$('rotation').disabled=live;$('save-rotation').disabled=live;if(live){$('mode-banner').textContent='Реальные официальные CLI · задания доступны после проверки входа и режима оплаты. Автоматическая ротация отключена.';$('account-mode').textContent='Вход и право на запрос проверяются отдельно. Резервные аккаунты не получают фоновых заданий.';let w=state.accounts.wizard||{};$('wizard-status').textContent='Шаг: '+(w.current_profile||w.current||w.profile_id||'—')+' · '+({idle:'не начат',paused:'пауза',awaiting_login:'ожидает входа',awaiting_confirmation:'ожидает подтверждения',awaiting_access:'проверяется доступ',awaiting_next:'готов к следующему аккаунту',blocked:'требуется действие',complete:'завершён',complete_with_skips:'завершён с пропусками'}[w.status]||w.status||'не начат')+(w.reason?' · '+w.reason:'')}let chainTask=rows(state.tasks).filter(t=>t.context?.chain_mode).sort((a,b)=>b.created-a.created)[0];if(chainTask)$('chain-mode-status').textContent=chainTask.context.chain_mode==='gpt_only'?'GPT-only: отдельная текстовая самопроверка, без независимой экспертизы Gemini.':'Редактор Gemini: проверка текста и плана.';let roleRows=rows(state.roles).sort((a,b)=>['writer','director','critic'].indexOf(a.role)-['writer','director','critic'].indexOf(b.role));$('control').textContent='Очередь: '+({running:'включена',paused:'на паузе',stopped:'остановлена'}[state.control]||state.control)+' · сохранена локально';if(state.panel_error)showError(state.panel_error);$('roles').replaceChildren();if(!profileEditing){$('selection-mode').value=state.selection_mode||'per_provider';['codex','gemini'].forEach(p=>$(p+'-slot').value=String(state.selected_profiles?.[p]||p+'-1').split('-').pop());$('rotation').checked=!!state.rotation_on_quota}
roleRows.forEach(r=>{let el=card(rolesNames[r.role]||r.role||r.id);el.append(node('p',(r.provider||'—')+' · '+(r.model||'модель не проверена')));el.append(node('p','Активная позиция: '+String((state.selected_profiles||{})[r.provider]||1)));let task=rows(state.tasks).find(t=>t.role===r.role&&t.status==='running');el.append(node('span',task?task.task_id:'Ожидание задания','badge'));$('roles').append(el)});
$('counts').textContent=Object.entries(state.counts||{}).filter(([k,v])=>v>0).map(([k,v])=>(statusNames[k]||k)+': '+v).join(' · ')+' | лимиты: 3 ИИ / 1 на провайдера / 1 на профиль / 1 локальная сборка';$('tasks').replaceChildren();rows(state.tasks).forEach(t=>{let tr=node('tr');[t.task_id||t.id,(rolesNames[t.role]||t.role)+' / '+(t.resource_class==='local_heavy'?'локальная сборка':(t.execution_mode==='live'?'реальный CLI':'симулятор')),statusNames[t.status]||t.status,JSON.stringify(t.error||t.failure||t.blocker||t.wait_reason||t.attempt||t.attempt_count||'—')].forEach(v=>tr.append(node('td',v)));$('tasks').append(tr)});
$('selection-status').textContent=state.pending_selection?'Новый выбор ожидает завершения текущих исполнителей: '+JSON.stringify(state.pending_selection):'Активные позиции: '+JSON.stringify(state.selected_profiles||{});renderProfiles();
if(!editing){renderScenes();renderRules()}
$('artifacts').replaceChildren();rows(state.artifacts).forEach((a,i)=>{let el=card(a.kind||a.expected_output||a.task_id||'Артефакт');el.append(node('p',(a.task_id||'')+' · '+(a.sha256||a.hash||'')));let link=node('a','Открыть сохранённый файл');link.href='/api/artifact?id='+encodeURIComponent(a.artifact_id||a.id||a.path||String(i));link.target='_blank';link.rel='noopener';el.append(link);let payload=a.payload||a.result;if(payload)el.append(node('pre',JSON.stringify(payload,null,2)));$('artifacts').append(el)});
}
const accountStates={installed:'Установлен',missing:'Не установлен',broken:'Ошибка запуска',authenticated:'Вход выполнен',requires_login:'Требуется вход',not_authenticated:'Требуется вход',awaiting_login:'Ожидает входа',unknown:'Не проверено',verified:'Проверен',unverified:'Не проверен',unavailable:'Недоступен',waiting_user:'Ожидает вашего входа',billing_unverified:'Не подтверждён режим оплаты',network:'Нет сети',quota_exhausted:'Квота исчерпана',unsupported_client:'Клиент не поддерживается сервисом'};
function displayState(v){return accountStates[v]||v||'Не проверено'}
function profileName(p){return ({codex:'GPT',claude:'Claude',gemini:'Gemini'}[p.provider]||p.provider)+'-'+(p.slot||p.number||p.position)}
function quotaText(p){
 let d=p.codex_diagnostics,limits=d?.rate_limits_by_id?.codex||d?.rate_limits,parts=[];
 for(let name of ['primary','secondary']){let w=limits?.[name];if(w&&typeof w.usedPercent==='number'){
  let duration=w.windowDurationMins,window=duration===10080?'неделю':duration===300?'5 часов':duration?duration+' мин.':'окно квоты';
  parts.push('использовано '+w.usedPercent+'% за '+window+(w.resetsAt?' · сброс '+new Date(w.resetsAt*1000).toLocaleString('ru-RU'):''));
 }}
 return parts.length?'Квота по данным клиента: '+parts.join('; '):'Остаток квоты: неизвестен';
}
function isGeminiFree(p){return p.access_route==='gemini_api_free'}
function geminiFreePreparation(p){
 let id=p.profile_id||p.id,draft=freeRouteDrafts[id]||(freeRouteDrafts[id]={project:p.gemini_free_route?.project_label||'',free:false,noBilling:false,open:false});
 let details=node('details'),summary=node('summary','Подготовить Gemini API Free Tier'),form=node('form',undefined,'gemini-free-form');details.open=draft.open;details.ontoggle=()=>draft.open=details.open;details.append(summary,form);
 form.append(node('p','Отдельное подключение через официальный Gemini CLI. Подготовка сохраняет выбранный проект; вход и запрос к модели запускаются отдельно. Ключ здесь не вводится.','muted'));
 let project=node('input');project.type='text';project.name='project_label';project.autocomplete='off';project.maxLength=120;project.required=true;project.value=draft.project;project.oninput=()=>draft.project=project.value;
 let projectLabel=node('label','Название или ID проекта AI Studio (без секретов) ');projectLabel.append(project);form.append(projectLabel);
 let model=node('input');model.type='text';model.name='model';model.value='gemini-3.8-flash';model.readOnly=true;let modelLabel=node('label','Выбранная модель ');modelLabel.append(model);form.append(modelLabel);
 let free=node('input'),noBilling=node('input');free.type=noBilling.type='checkbox';free.required=noBilling.required=true;free.name='free_tier_confirmed';noBilling.name='no_billing_confirmed';free.checked=draft.free;noBilling.checked=draft.noBilling;free.onchange=()=>draft.free=free.checked;noBilling.onchange=()=>draft.noBilling=noBilling.checked;
 let freeLabel=node('label',' В AI Studio у этого проекта указан Free Tier'),billingLabel=node('label',' К этому проекту не привязан Cloud Billing');freeLabel.prepend(free);billingLabel.prepend(noBilling);form.append(freeLabel,billingLabel,node('p','Статус «No credits» не подтверждает бесплатность. Лимиты относятся к проекту; отдельный ключ не создаёт отдельную квоту.','muted'));
 let save=node('button','Подготовить подключение');save.type='submit';form.append(save);
 form.onsubmit=async e=>{e.preventDefault();save.disabled=true;let ok=await action('account_prepare_gemini_free',{profile_id:id,model:model.value,project_label:project.value,free_tier_confirmed:free.checked,no_billing_confirmed:noBilling.checked});save.disabled=false;if(ok){delete freeRouteDrafts[id];save.blur();renderProfiles()}};
 return details;
}
function renderProfiles(){
 if(document.activeElement?.closest('.gemini-free-form'))return;
 $('profiles').replaceChildren();let profiles=state.accounts?state.accounts.profiles:state.profiles;
 let previousProvider=null;rows(profiles).sort((a,b)=>['codex','gemini'].indexOf(a.provider)-['codex','gemini'].indexOf(b.provider)||(a.slot||a.number)-(b.slot||b.number)).forEach(p=>{
  if(p.provider!==previousProvider){$('profiles').append(node('h3',p.provider==='codex'?'GPT · сценарий и режиссура':'Gemini · редактор при подтверждённом доступе'));previousProvider=p.provider}
  let id=p.profile_id||p.id,d=p.codex_diagnostics,el=card(profileName(p));el.append(node('span',state.accounts?'ОФИЦИАЛЬНЫЙ CLI':'СИМУЛЯТОР','badge'));
  if(state.accounts){
   let identity={verified:'подтверждена клиентом',user_confirmed_not_automatically_verified:'подтверждена вами, автоматически не проверена',unverified:'не проверена'}[p.identity_verification]||'не проверена';
   el.append(node('p','Клиент: '+displayState(p.installation||p.install_state)+' · '+(p.client_version||p.version||'')),node('p','Вход: '+displayState(p.actual_auth_state||p.auth_state)),node('p','Доступ: '+displayState(p.access_state)),node('p',isGeminiFree(p)?'Подтверждение проекта: '+identity:'Личность: '+identity+(p.account_label?' · '+p.account_label:'')),node('p','Проверка: '+(p.last_checked?new Date(typeof p.last_checked==='number'?p.last_checked*1000:p.last_checked).toLocaleString('ru-RU'):'ещё не выполнена')));
   if(p.provider==='gemini'){el.append(node('p','Подключение: '+(isGeminiFree(p)?'Gemini API Free Tier':'Google individual (OAuth)')),node('p','Модель: '+(p.gemini_free_route?.model||p.model||'не выбрана')));if(isGeminiFree(p))el.append(node('p','Проект AI Studio: '+(p.gemini_free_route?.project_label||'не указан')),node('p','Проект подтверждён владельцем; личность Google автоматически не проверена. Независимость квоты не подтверждена.','muted'))}
   if(d?.plan_type)el.append(node('p','План клиента: '+d.plan_type));
   if(d?.credits)el.append(node('p','Баланс дополнительных кредитов: '+(d.credits.balance??'неизвестен')));
   let activeProfile=rows(state.profiles).find(x=>(x.profile_id||x.id)===id),attempt=rows(state.attempts).filter(a=>a.profile_id===id).sort((a,b)=>(b.created||0)-(a.created||0))[0],task=rows(state.tasks).find(t=>t.task_id===(activeProfile?.active_task_id||attempt?.task_id)),selected=state.selected_profiles?.[p.provider]===id,assigned=selected?rows(state.roles).filter(r=>r.provider===p.provider).map(r=>rolesNames[r.role]):[];
   el.append(node('p','Сервис: '+(p.provider==='codex'?'OpenAI / Codex':'Google / Gemini')),node('p','Роли: '+(assigned.length?assigned.join(', '):'резерв')),node('p','Активная / последняя задача: '+(task?task.task_id+' · '+(statusNames[task.status]||task.status):'ещё нет')));
   if(p.paused)el.append(node('p','Профиль на паузе','badge'));
   let reason=p.reason||p.last_error||p.error;if(reason)el.append(node('p',typeof reason==='string'?reason:JSON.stringify(reason),'error'));
   let bar=node('div',undefined,'bar');[['Подключить','account_connect'],['Проверить','account_check'],['Переподключить','account_reconnect']].forEach(([label,a])=>{let b=node('button',label);b.onclick=()=>action(a,{profile_id:id});bar.append(b)});
   let pause=node('button',p.paused?'Продолжить':'Пауза');pause.onclick=()=>action(p.paused?'account_resume':'account_pause',{profile_id:id});bar.append(pause);
   let confirm=node('button',isGeminiFree(p)?'Подтвердить ключ и Free Tier':p.identity_verification==='verified'?'Подтвердить режим оплаты':'Подтвердить аккаунт');confirm.onclick=()=>openIdentity(p);bar.append(confirm);el.append(bar);if(p.provider==='gemini')el.append(geminiFreePreparation(p));
  }else{el.append(node('p',statusNames[p.state||p.status]||p.state||'Не проверено'),node('p','Реальный вход: не настроен'))}
  el.append(node('p',quotaText(p)));if(d?.checked_at)el.append(node('p','Квота проверена: '+new Date(d.checked_at).toLocaleString('ru-RU'),'muted'));
  $('profiles').append(el);
 });
 let historical=rows(state.accounts?.historical_profiles||state.historical_profiles);if(historical.length){let details=node('details',undefined,'wide'),summary=node('summary','Исторические профили · '+historical.length);details.append(summary,node('p','Claude исключён из текущей команды. Профили и прежние результаты сохранены.'));historical.forEach(p=>details.append(node('p',profileName(p)+' · '+displayState(p.auth_state||p.actual_auth_state)+' · '+displayState(p.access_state||p.state))));$('profiles').append(details)}
}
function openIdentity(p){
 identityProfile=p.profile_id||p.id;identityFreeRoute=isGeminiFree(p);let verified=p.identity_verification==='verified';
 $('identity-title').textContent='Подтверждение '+profileName(p);$('identity-label').value=p.account_label||'';
 $('identity-manual').hidden=verified||identityFreeRoute;$('identity-subscription').hidden=identityFreeRoute;$('identity-api-free').hidden=!identityFreeRoute;
 $('identity-evidence').textContent=identityFreeRoute?'Подтвердите сохранение ключа и бесплатный режим указанного проекта. Значение ключа не требуется.':verified?'Личность аккаунта подтверждена официальным клиентом. Осталось подтвердить режим без дополнительных расходов.':'Клиент не подтвердил личность автоматически. Проверьте выбранный аккаунт в официальном окне.';
 $('identity-project').textContent=identityFreeRoute?'Проект AI Studio: '+(p.gemini_free_route?.project_label||'не указан')+' · модель: '+(p.gemini_free_route?.model||p.model||'не выбрана'):'';
 $('identity-submit').textContent=identityFreeRoute?'Подтвердить и проверить доступ':'Сохранить подтверждение';
 ['identity-distinct','identity-auth','identity-billing','identity-key-saved','identity-free-tier','identity-no-billing'].forEach(x=>$(x).checked=false);['identity-key-saved','identity-free-tier','identity-no-billing'].forEach(x=>$(x).required=identityFreeRoute);$('identity-form').hidden=false;$('identity-form').scrollIntoView({block:'center'});$(identityFreeRoute?'identity-key-saved':verified?'identity-billing':'identity-label').focus();
}
$('identity-form').onsubmit=async e=>{e.preventDefault();let args=identityFreeRoute?{profile_id:identityProfile,auth_confirmed:$('identity-key-saved').checked,billing_confirmed:$('identity-free-tier').checked&&$('identity-no-billing').checked}:{profile_id:identityProfile,account_label:$('identity-label').value,distinct_account:$('identity-distinct').checked,auth_confirmed:$('identity-auth').checked,billing_confirmed:$('identity-billing').checked};let response=await action('account_confirm',args);if(response!==false)$('identity-form').hidden=true};
$('identity-cancel').onclick=()=>{$('identity-form').hidden=true;identityProfile=null;identityFreeRoute=false};$('wizard-skip').onclick=()=>action('wizard_skip',{reason:$('skip-reason').value});
function renderScenes(){$('scene-list').replaceChildren();let scenes=rows(state.scenes);if(!scenes.length)$('scene-list').append(node('p','Создайте тестовую цепочку: её сцена появится здесь.'));scenes.forEach(s=>{let el=card(s.scene_id||s.id),text=node('textarea');text.value=s.text||s.payload?.narration_segments?.map(x=>x.text).join('\n')||'';text.onfocus=()=>editing=true;el.append(node('p','Версия '+s.version+' · '+(s.pinned?'закреплена':'доступна для правок')),text);let pin=node('input');pin.type='checkbox';pin.checked=!!s.pinned;let label=node('label','Закрепить сцену ');label.prepend(pin);el.append(label);let save=node('button','Сохранить новую версию');save.onclick=async()=>{editing=false;await action('update_scene',{project_id:s.project_id,scene_id:s.scene_id||s.id,text:text.value,expected_version:s.version,pinned:pin.checked})};let cancel=node('button','Отменить ввод');cancel.onclick=()=>{editing=false;renderScenes()};el.append(save,cancel);if(s.history)el.append(node('pre',JSON.stringify(s.history,null,2)));$('scene-list').append(el)})}
function renderRules(){$('role-settings').replaceChildren();rows(state.roles).forEach(r=>{let el=card(rolesNames[r.role]||r.role),provider=node('select');['codex','gemini'].forEach(p=>{let o=node('option',p);o.value=p;provider.append(o)});provider.value=r.provider;let model=node('input');model.placeholder='Модель не выбрана';model.value=r.model||'';let rules=node('textarea');rules.value=r.rules||r.instruction||'';[provider,model,rules].forEach(e=>e.onfocus=()=>editing=true);el.append(node('p','Провайдер'),provider,node('p','Модель'),model,node('p','Инструкции'),rules);let save=node('button','Применить к новым заданиям');save.onclick=async()=>{editing=false;await action('configure_role',{role:r.role,provider:provider.value,model:model.value||null,rules:rules.value})};el.append(save);$('role-settings').append(el)})}
async function refresh(){state=await request('/api/state');render();let p=state.panel_preview;if(p){$('preview-status').textContent=statusNames[p.status]||p.status||'';if(p.url&&$('preview').dataset.url!==p.url){$('preview').replaceChildren();$('preview').dataset.url=p.url;let v=node('video');v.controls=true;v.src=p.url;$('preview').append(v);[['MLT',p.mlt_url],['Технические проверки',p.checks_url]].forEach(([name,url])=>{let link=node('a',name);link.href=url;link.target='_blank';$('preview').append(node('p'),link)})}if(p.error)$('preview-status').textContent=typeof p.error==='string'?p.error:JSON.stringify(p.error)}}
$('live-button').onclick=()=>action('live_chain',{mode:$('chain-mode').value});
document.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>action(b.dataset.action));document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{document.querySelectorAll('main>section').forEach(s=>s.hidden=s.id!==b.dataset.tab);document.querySelectorAll('[data-tab]').forEach(t=>t.setAttribute('aria-selected',String(t===b)))});
['selection-mode','codex-slot','gemini-slot','rotation'].forEach(id=>$(id).onchange=()=>profileEditing=true);$('select-profiles').onclick=()=>{let mode=$('selection-mode').value,selection=mode==='group'?{group:Number($('codex-slot').value)}:{};if(mode!=='group')['codex','gemini'].forEach(p=>selection[p]=Number($(p+'-slot').value));profileEditing=false;action('select_profiles',{mode,selection})};$('save-rotation').onclick=()=>{let enabled=$('rotation').checked;profileEditing=false;action('set_rotation',{enabled})};$('preview-button').onclick=()=>action('preview');
const eventNames={task_enqueued:'Задание добавлено',run_started:'Исполнитель запущен',completed:'Исполнитель завершил работу',result_accepted:'Результат проверен и сохранён',control_changed:'Состояние очереди изменено',selection_requested:'Запрошена смена профилей',selection_applied:'Профили выбраны',failed:'Ошибка',progress:'Ожидание исполнителя',scene_updated:'Сцена обновлена'};
const stream=new EventSource('/api/events');stream.onopen=()=>{$('notice').textContent='Соединение с локальной очередью установлено'};stream.onmessage=e=>{let item=JSON.parse(e.data);lastEvent=Number(e.lastEventId)||lastEvent;let message=new Date(item.timestamp*1000).toLocaleTimeString('ru-RU')+' · '+(eventNames[item.kind]||item.kind)+(item.task_id?' · '+item.task_id:'');$('events').textContent=($('events').textContent+'\n'+message).split('\n').slice(-100).join('\n');refresh().catch(showError)};stream.onerror=()=>{$('notice').textContent='Поток событий переподключается; сохранённые результаты остаются в реестре.'};refresh().catch(showError);setInterval(()=>refresh().catch(showError),5000);
</script></body></html>'''


class PanelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, coordinator, *, enable_worker=True, accounts=None):
        if address[0] != '127.0.0.1':
            raise ValueError('The offline panel binds only to 127.0.0.1')
        self.coordinator = coordinator
        self.accounts = accounts
        self.workspace = Path(coordinator.workspace).resolve()
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.finished = threading.Event()
        self.worker_error = None
        self.preview = None
        super().__init__(address, PanelHandler)
        self.worker = None
        if enable_worker:
            self.worker = threading.Thread(target=self._work, daemon=True)
            self.worker.start()

    def _work(self):
        while not self.finished.wait(.1):
            try:
                with self.lock:
                    self.coordinator.tick()
            except Exception as exc:
                # Keep the actual error visible; do not claim progress or retry forever.
                self.worker_error = sanitize(str(exc))
                self._log('worker_error', self.worker_error)
                return

    def _log(self, kind, message):
        path = self.workspace / 'multi_ai_panel.log'
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'time':time.time(), 'kind':kind, 'message':sanitize(message)}, ensure_ascii=False)+'\n')

    def state(self):
        with self.lock:
            result = self.coordinator.snapshot()
        result['panel_preview'] = self._preview_state(result)
        result['panel_error'] = self.worker_error
        if self.accounts:
            result['accounts'] = self.accounts.snapshot()
        return result

    def command(self, body):
        action = body.get('action')
        if action in {'wizard_start', 'wizard_resume', 'wizard_pause', 'wizard_skip',
                      'account_connect', 'account_reconnect', 'account_check', 'account_confirm', 'account_pause', 'account_resume',
                      'account_prepare_gemini_free'}:
            return self.account_command(body)
        fields = {
            'demo': (), 'pause': (), 'resume': (), 'stop': (),
            'live_chain': ('mode',),
            'select_profiles': ('mode', 'selection'), 'set_rotation': ('enabled',),
            'update_scene': ('project_id', 'scene_id', 'text', 'expected_version', 'pinned'),
            'configure_role': ('role', 'provider', 'model', 'rules'),
        }
        if action == 'preview':
            return self.request_preview()
        if action not in fields or set(body) - {'action', *fields[action]}:
            raise ValueError('Unknown action or fields')
        with self.lock:
            result = getattr(self.coordinator, action)(**{k:body[k] for k in fields[action] if k in body})
            if action in {'demo', 'live_chain'}:
                self.coordinator.resume()
            return result

    def account_command(self, body):
        if self.accounts is None:
            raise ValueError('Подключения доступны при запуске multi_ai.cmd --live')
        action = body['action']
        allowed = {
            'wizard_start': (), 'wizard_resume': (), 'wizard_pause': (), 'wizard_skip': ('reason',),
            'account_connect': ('profile_id',), 'account_reconnect': ('profile_id',),
            'account_check': ('profile_id',), 'account_pause': ('profile_id',), 'account_resume': ('profile_id',),
            'account_confirm': ('profile_id', 'account_label', 'distinct_account', 'billing_confirmed', 'auth_confirmed'),
            'account_prepare_gemini_free': ('profile_id', 'model', 'project_label', 'free_tier_confirmed', 'no_billing_confirmed'),
        }
        if set(body) - {'action', *allowed[action]}:
            raise ValueError('Unknown account fields; passwords and tokens are not accepted')
        args = {k:body[k] for k in allowed[action] if k in body}
        if action == 'account_prepare_gemini_free' and set(args) != set(allowed[action]):
            raise ValueError('Укажите профиль, модель, проект и оба подтверждения Free Tier без billing')
        with self.lock:
            snapshot = self.coordinator.snapshot()
            active = [t for t in snapshot['attempts'] if t['status'] in {'running', 'stopping'}]
            if active and action in {'wizard_start','wizard_resume','account_connect','account_reconnect','account_confirm','account_prepare_gemini_free'}:
                raise ValueError('Дождитесь завершения текущих заданий перед сменой входа')
            if action == 'account_prepare_gemini_free':
                return self.accounts.prepare_gemini_free_route(**args)
            if action.startswith('wizard_'):
                method = {'wizard_start':'start_wizard', 'wizard_resume':'resume_wizard',
                          'wizard_pause':'pause_wizard', 'wizard_skip':'skip_profile'}[action]
                return getattr(self.accounts, method)(**args)
            if action in {'account_connect','account_reconnect'}:
                return self.accounts.connect(**args, reconnect=action == 'account_reconnect')
            if action in {'account_pause', 'account_resume'}:
                profile = getattr(self.accounts, 'pause_profile' if action == 'account_pause' else 'resume_profile')(**args)
                if action == 'account_resume':
                    self.coordinator.wake_profile(args['profile_id'])
                return profile
            if action == 'account_confirm':
                profile = self.accounts.confirm(**args)
                if profile.get('can_probe') and profile.get('access_state') != 'verified':
                    task = self.coordinator.enqueue_access_probe(args['profile_id'])
                    self.coordinator.resume()
                    return {'profile':profile, 'probe':task}
                return {'profile':profile, 'probe':None}
            profile_id = args['profile_id']
            self.accounts.check(profile_id)
            profile = self.accounts.queue_profile(profile_id)
            if profile.get('can_probe') and profile.get('access_state') != 'verified':
                # The queue reserves the provider and validates the short answer
                # before reporting model access. Login alone is not entitlement.
                task = self.coordinator.enqueue_access_probe(profile_id)
                self.coordinator.resume()
                return {'profile':profile, 'probe':task}
            return {'profile':profile, 'probe':None}

    def request_preview(self):
        # Wired to the persisted local-heavy queue by the optional fixture adapter.
        from .multi_ai_preview import enqueue_preview
        with self.lock:
            result = enqueue_preview(self.coordinator)
        return result

    def _preview_state(self, snapshot):
        tasks = [task for task in snapshot.get('tasks',[]) if task.get('kind')=='render_fixture']
        if not tasks:
            return None
        task=tasks[-1]
        result={'status':task['status'], 'error':task.get('error')}
        artifact=next((a for a in snapshot.get('artifacts',[]) if a['task_id']==task['task_id']),None)
        if artifact:
            base='/api/artifact?id='+artifact['id']
            result.update(url=base+'&file=preview',mlt_url=base+'&file=mlt',checks_url=base+'&file=checks')
        return result

    def artifact(self, identifier, file_kind='result'):
        with self.lock:
            artifacts = self.coordinator.snapshot().get('artifacts', [])
        if isinstance(artifacts, dict):
            artifacts = list(artifacts.values())
        for item in artifacts:
            if identifier in {str(item[k]) for k in ('artifact_id','id','path') if item.get(k)}:
                path=workspace_path(self.workspace, item['path'])
                if content_hash(path)!=item['sha256']:
                    raise StudioError('ARTIFACT_CHANGED','Saved result no longer matches its accepted hash')
                if file_kind=='result':
                    return path
                if file_kind not in {'preview','mlt','checks'}:
                    raise ValueError('Unsupported artifact view')
                note=json.loads(path.read_text(encoding='utf-8'))['payload']
                data=json.loads(note['text'])
                if data.get('technical_preview') is not True:
                    raise ValueError('Not a technical preview artifact')
                child=workspace_path(self.workspace,data[file_kind+'_ref'])
                if not child.is_relative_to(path.parent/'technical') or content_hash(child)!=data[file_kind+'_sha256']:
                    raise StudioError('ARTIFACT_CHANGED','Preview file is outside its attempt or has changed')
                return child
        raise FileNotFoundError('Unknown artifact')

    def server_close(self):
        self.finished.set()
        if self.worker:
            self.worker.join(timeout=2)
        super().server_close()


class PanelHandler(BaseHTTPRequestHandler):
    server: PanelServer

    def log_message(self, *_):
        pass

    def _headers(self, status, content_type, *, length=None, cookie=False):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; media-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if length is not None:
            self.send_header('Content-Length', str(length))
        if cookie:
            self.send_header('Set-Cookie', f'ai_panel={self.server.token}; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers()

    def _json(self, value, status=HTTPStatus.OK):
        data = (json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)+'\n').encode('utf-8')
        self._headers(status, 'application/json; charset=utf-8', length=len(data))
        self.wfile.write(data)

    def _host_ok(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

    def _authorized(self):
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            value = cookies.get('ai_panel')
            return self._host_ok() and value is not None and secrets.compare_digest(value.value, self.server.token)
        except Exception:
            return False

    def do_GET(self):
        target = urlparse(self.path)
        try:
            if target.path == '/' and self._host_ok():
                data = PAGE.replace('__CSRF__', self.server.token).encode('utf-8')
                self._headers(HTTPStatus.OK, 'text/html; charset=utf-8', length=len(data), cookie=True)
                self.wfile.write(data)
                return
            if not self._authorized():
                self._json({'error':'Open the local panel first'}, HTTPStatus.FORBIDDEN)
                return
            if target.path == '/api/state':
                self._json(self.server.state())
            elif target.path == '/api/events':
                after = int(self.headers.get('Last-Event-ID') or parse_qs(target.query).get('after',['0'])[0])
                if after < 0:
                    raise ValueError('Invalid event cursor')
                self._headers(HTTPStatus.OK, 'text/event-stream; charset=utf-8')
                deadline = time.monotonic() + 25
                while not self.server.finished.is_set() and time.monotonic() < deadline:
                    with self.server.lock:
                        batch = self.server.coordinator.events(after=after)
                    events = batch.get('events', []) if isinstance(batch, dict) else batch
                    for event in events:
                        seq = int(event.get('sequence', event.get('seq', event.get('event_id', event.get('id',0)))))
                        self.wfile.write(f'id: {seq}\ndata: {json.dumps(event,ensure_ascii=False)}\n\n'.encode('utf-8'))
                        after = max(after, seq)
                    self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()
                    self.server.finished.wait(.5)
                self.close_connection = True
            elif target.path == '/api/artifact':
                identifier = parse_qs(target.query).get('id',[''])[0]
                path = self.server.artifact(identifier,parse_qs(target.query).get('file',['result'])[0])
                # Only registered result files are readable, not arbitrary workspace paths.
                if not path.is_file() or path.suffix.lower() not in {'.json','.txt','.mlt','.mp4','.wav','.srt'}:
                    raise FileNotFoundError('Artifact is not available')
                data = path.read_bytes()
                content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
                self._headers(HTTPStatus.OK, content_type, length=len(data))
                self.wfile.write(data)
            else:
                self._json({'error':'Not found'}, HTTPStatus.NOT_FOUND)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:
            self.server._log('http_error', str(exc))
            self._json({'error':sanitize(str(exc))}, HTTPStatus.BAD_REQUEST)

    def do_POST(self):
        origin = self.headers.get('Origin')
        valid_origins = {f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}
        if (not self._authorized() or self.headers.get('X-CSRF-Token') != self.server.token
                or origin is not None and origin not in valid_origins):
            self._json({'error':'Local session / CSRF check failed'}, HTTPStatus.FORBIDDEN)
            return
        if self.path != '/api/action':
            self._json({'error':'Not found'}, HTTPStatus.NOT_FOUND)
            return
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0 < size <= 1_048_576 or self.headers.get('Content-Type') != 'application/json':
                raise ValueError('Expected bounded application/json request')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('Expected JSON object')
            self._json(self.server.command(body))
        except Exception as exc:
            self.server._log('command_error', str(exc))
            self._json({'error':sanitize(str(exc))}, HTTPStatus.BAD_REQUEST)


def main(argv=None):
    parser = argparse.ArgumentParser(description='AI_VIDEO_02 local multi-AI panel')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--live', action='store_true', help='Enable official CLI account wizard; model tasks remain gated')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--open', action='store_true', help='Open the panel in the default browser')
    args = parser.parse_args(argv)
    if args.workspace is None:
        args.workspace = Path('workspace/multi_ai_live' if args.live else 'workspace/multi_ai_demo')
    production = Path(__file__).resolve().parents[1] / 'workspace/studio'
    if args.workspace.resolve() == production.resolve():
        parser.error('Use a separate development workspace; the production registry is preserved')
    if not 0 <= args.port <= 65535:
        parser.error('Port must be in 0..65535')
    from .multi_ai import Coordinator
    from .ai_adapters import FakeAdapter
    from .multi_ai_preview import PreviewAdapter
    accounts = None
    coordinator_options = {}
    if args.live:
        from .ai_accounts import AccountManager
        from .ai_client_discovery import discover_clients
        from .ai_cli_runtime import LiveCLIAdapter
        accounts = AccountManager(args.workspace, clients=discover_clients())
        accounts.prepare_profiles()
        adapters = {name:LiveCLIAdapter(name, accounts) for name in ('codex','gemini')}
        coordinator_options['profile_source'] = accounts.queue_profile
        coordinator_options['result_observer'] = accounts.observe_result
    else:
        adapters = {name:FakeAdapter(name) for name in ('codex','gemini')}
    adapters['local'] = PreviewAdapter(args.workspace)
    coordinator = Coordinator(args.workspace, adapters=adapters, **coordinator_options)
    server = PanelServer(('127.0.0.1', args.port), coordinator, accounts=accounts)
    url = f'http://127.0.0.1:{server.server_port}/'
    print(json.dumps({'url':url,'mode':'live' if args.live else 'offline_simulation','workspace':str(args.workspace.resolve())}), flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        # Stop only this panel's managed executions, then preserve all saved results.
        with server.lock:
            coordinator.stop()
        server.server_close()
        if hasattr(coordinator, 'close'):
            coordinator.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
