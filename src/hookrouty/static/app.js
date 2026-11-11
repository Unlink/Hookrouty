"use strict";
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const page = document.body.dataset.page;
const hookId = document.body.dataset.hookId;
const hook = $("#hook-data") ? JSON.parse($("#hook-data").textContent) : null;
let csrf = "";
function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
}
function notice(message, error = false) {
  const box = $("#notice"); box.textContent = message; box.hidden = false;
  box.className = error ? "error" : "";
  box.scrollIntoView({behavior: "smooth", block: "nearest"});
}
async function api(path, options = {}) {
  const response = await fetch("/api" + path, {credentials:"same-origin", ...options,
    headers:{"Content-Type":"application/json", "X-CSRF-Token":csrf, ...options.headers},
    body: options.body === undefined ? undefined : JSON.stringify(options.body)});
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) throw new Error(data?.error?.message || "Request failed.");
  return data;
}
function action(label, callback, style = "quiet") {
  const button = node("button", label, style); button.type = "button";
  button.addEventListener("click", () => run(callback)); return button;
}
async function run(fn) { try { await fn(); } catch (error) { notice(error.message, true); } }
function link(label, href) { const a = node("a", label); a.href = href; return a; }
function badge(value) { return node("span", value, "badge " + value); }
function bindForm(selector, callback) {
  const form = $(selector); if (!form) return;
  form.addEventListener("submit", event => { event.preventDefault(); run(async () => {
    const button = $("button[type=submit]", form); if (button) button.disabled = true;
    try { await callback(form, Object.fromEntries(new FormData(form))); }
    finally { if (button) button.disabled = false; }
  }); });
}
async function loadHooks() {
  const hooks = await api("/hooks"); const list = $("#hooks-list"); list.replaceChildren();
  $("#empty-hooks").hidden = hooks.length > 0;
  for (const h of hooks) {
    const tr = node("tr"); const name = node("td");
    name.append(node("strong", h.name), node("code", h.input_method + " " + h.input_path));
    const target = node("td"); target.append(node("code", h.target.method + " " + h.target.url));
    const status = node("td"); status.append(badge(h.enabled ? "enabled" : "disabled"));
    const controls = node("td"); const buttons = node("div", undefined, "row");
    buttons.append(link("Edit", "/app/hooks/" + h.id + "/edit"), link("Test", "/app/hooks/" + h.id + "/test"),
      action(h.enabled ? "Disable" : "Enable", async () => {await api("/hooks/"+h.id,{method:"PATCH",body:{enabled:!h.enabled}}); await loadHooks();}),
      action("Duplicate", async () => {const path = prompt("New input path", h.input_path + "-copy"); if (!path) return; const result=await api("/hooks/"+h.id+"/duplicate",{method:"POST",body:{input_path:path}}); notice("Disabled copy created. Store this token now; it will not be shown again:\n"+result.token); await loadHooks();}),
      action("Rotate token", async () => {if (!confirm("Replace this hook's token? Existing callers must use the new token.")) return; const result=await api("/hooks/"+h.id+"/rotate-token",{method:"POST",body:{}}); notice("Store the new token now:\n"+result.token);}),
      action("Delete", async () => {if (!confirm("Delete this hook and its configuration? History remains until retention expires.")) return; await api("/hooks/"+h.id,{method:"DELETE"}); await loadHooks();}, "danger"));
    controls.append(buttons); tr.append(name,target,status,controls); list.append(tr);
  }
}
function addField(kind, field = {}) {
  const row = node("div", undefined, "field-row"); row.dataset.id = field.id || "";
  const nameLabel = node("label", "Name"), valueLabel = node("label", "Value / template");
  const name = node("input"), value = node("input"); name.className = "field-name"; value.className = "field-value";
  name.value = field.name || ""; name.required = true; name.setAttribute("aria-label", kind + " field name");
  value.value = field.value || ""; value.placeholder = field.configured ? "Stored secret (hidden)" : "Value or {{query.value}}";
  value.autocomplete = "off"; nameLabel.append(name); valueLabel.append(value);
  const controls = node("div", undefined, "field-actions"), secretLabel = node("label", undefined, "check"), secret = node("input");
  secret.type = "checkbox"; secret.className = "field-secret"; secret.checked = !!field.secret;
  value.type = secret.checked ? "password" : "text";
  secret.addEventListener("change",()=>{value.type=secret.checked ? "password":"text";});
  secretLabel.append(secret, document.createTextNode("Secret"));
  const editLabel = node("label", "Action"), edit = node("select"); edit.className = "field-action";
  for (const [val,text] of [["replace","Replace"],["keep","Keep stored secret"],["remove","Remove"]]) {
    if (val === "keep" && !field.configured) continue;
    const option = node("option", text); option.value = val; edit.append(option);
  }
  edit.value = field.configured ? "keep" : "replace";
  value.disabled = edit.value === "keep";
  edit.addEventListener("change", () => {value.disabled = edit.value !== "replace"; name.required=edit.value!=="remove";});
  editLabel.append(edit); controls.append(secretLabel, editLabel, action("×", () => row.remove()));
  row.append(nameLabel,valueLabel,controls); $("#"+kind+"-fields").append(row);
}
function readFields(kind) {
  return $$("#"+kind+"-fields .field-row").map(row => ({id:row.dataset.id || null,
    name:$(".field-name",row).value, value:$(".field-action",row).value === "replace" ? $(".field-value",row).value : null,
    secret:$(".field-secret",row).checked, value_action:$(".field-action",row).value}));
}
async function editor() {
  for (const kind of ["query","headers"]) for (const f of hook?.target[kind] || []) addField(kind,f);
  $$('[data-add-field]').forEach(button => button.addEventListener("click",()=>addField(button.dataset.addField)));
  const form = $("#hook-form"), bodyAction = form.elements.body_action;
  const body = hook?.target.body;
  form.elements.body_template.value = body?.configured ? "" : body?.mode === "json" ? JSON.stringify(body.template,null,2) : body?.template || "";
  bodyAction.value = body?.configured ? "keep":"replace";
  function updateBody(){form.elements.body_template.disabled=bodyAction.value!=="replace";}
  updateBody(); bodyAction.addEventListener("change",updateBody);
  bindForm("#hook-form", async (form,data) => {
    const mode = data.body_mode; const keep = bodyAction.value === "keep";
    const template = keep || ["none","passthrough"].includes(mode) ? null : mode === "json" ? JSON.parse(data.body_template || "null") : data.body_template || "";
    const spec = {name:data.name,description:data.description,enabled:form.elements.enabled.checked,input_method:data.input_method,input_path:data.input_path,
      auth:{mode:data.auth_mode}, target:{method:data.target_method,url:data.target_url,query:readFields("query"),headers:readFields("headers"),body:{mode,template,secret:form.elements.body_secret.checked,value_action:bodyAction.value}},
      timeout_seconds:Number(data.timeout_seconds),capture_bodies:form.elements.capture_bodies.checked,secret_refs:data.secret_refs.split(/\r?\n/).map(s=>s.trim()).filter(Boolean)};
    if (data.custom_token) spec.auth.token = data.custom_token;
    const result = await api(hookId ? "/hooks/"+hookId : "/hooks", {method:hookId?"PATCH":"POST",body:spec});
    if (result.token) {notice("Hook created. Store this token now; it will not be shown again:\n" + result.token + "\nYou can now find the hook under Hooks."); $("button[type=submit]",form).hidden=true;}
    else {notice("Hook saved."); setTimeout(()=>location.reload(),650);}
  });
  if ($("#members")) {
    const [users, members] = await Promise.all([api("/users"),api("/hooks/"+hookId+"/members")]);
    for (const user of users) {
      const label=node("label",undefined,"check"), check=node("input"); check.type="checkbox"; check.value=user.id; check.checked=members.user_ids.includes(user.id);
      label.append(check,document.createTextNode(user.username+" ("+user.role+")")); $("#members").append(label);
    }
    $("#save-members").addEventListener("click",()=>run(async()=>{await api("/hooks/"+hookId+"/members",{method:"PUT",body:{user_ids:$$("#members input:checked").map(e=>e.value)}}); notice("Assignments saved. Access changes apply immediately.");}));
  }
}
async function testPage() {
  const values = {};
  for (const segment of hook.input_path.split("/")) if (segment.startsWith(":")) values[segment.slice(1)]="example";
  $("#test-form").elements.path.value=JSON.stringify(values,null,2);
  async function submit(real) {
    const form=$("#test-form"); const data=Object.fromEntries(new FormData(form));
    const samples={path:JSON.parse(data.path),query:JSON.parse(data.query),headers:JSON.parse(data.headers),body:data.body};
    const result=await api("/hooks/"+hookId+(real?"/test":"/preview"),{method:"POST",body:samples});
    $("#test-result").textContent=JSON.stringify(result,null,2);
    if(real) notice("Real test completed. HTTP status: "+result.status_code+". Request ID: "+result.request_id);
  }
  bindForm("#test-form",()=>submit(false));
  $("#run-test").addEventListener("click",()=>run(async()=>{if(confirm("Send one real HTTP request to the configured destination?")) await submit(true);}));
}
async function activity() {
  const hooks=await api("/hooks"); for(const h of hooks){const option=node("option",h.name);option.value=h.id;$("#filters").elements.hook_id.append(option);}
  let pageNumber=1;
  async function refresh(){
    const filters=Object.fromEntries(new FormData($("#filters")));
    const since=new Date(Date.now()-(filters.period==="7d"?7:1)*86400000).toISOString();
    const params=new URLSearchParams({...filters,since,page:String(pageNumber)});
    const [result,stats]=await Promise.all([api("/executions?"+params),api("/stats?"+params)]);
    const list=$("#executions-list");list.replaceChildren();$("#empty-executions").hidden=result.items.length>0;
    for(const record of result.items){const tr=node("tr"), first=node("td");first.append(link(record.hook_name_snapshot,"/app/executions/"+record.request_id),node("div",new Date(record.started_at).toLocaleString(),"hint"));const state=node("td");state.append(badge(record.outcome));tr.append(first,state,node("td",record.upstream_status??"—"),node("td",record.duration_ms===null?"—":Math.round(record.duration_ms)+" ms"),node("td",record.source));list.append(tr);}
    $("#page-number").textContent="Page "+pageNumber+" · "+result.total+" calls";$("#prev").disabled=pageNumber===1;$("#next").disabled=pageNumber*result.page_size>=result.total;
    const cards=$("#stats");cards.replaceChildren();
    for(const [label,value] of [["Calls",stats.calls],["Success rate",stats.success_rate===null?"—":(stats.success_rate*100).toFixed(1)+"%"],["Average duration",stats.average_duration_ms===null?"—":Math.round(stats.average_duration_ms)+" ms"],["Running / rejected",stats.running+" / "+stats.rejected]]){const card=node("div",undefined,"stat");card.append(node("span",label),node("strong",String(value)));cards.append(card);}
  }
  bindForm("#filters",async()=>{pageNumber=1;await refresh();});
  $("#prev").addEventListener("click",()=>run(async()=>{pageNumber--;await refresh();}));
  $("#next").addEventListener("click",()=>run(async()=>{pageNumber++;await refresh();}));
  await refresh();let busy=false;
  setInterval(()=>{if(!document.hidden&&!busy){busy=true;run(refresh).finally(()=>{busy=false;});}},5000);
}
async function usersPage(){
  async function refresh(){const users=await api("/users");const list=$("#users-list");list.replaceChildren();for(const u of users){const tr=node("tr"),role=node("select");for(const val of ["user","admin"]){const option=node("option",val);option.value=val;role.append(option);}role.value=u.role;role.setAttribute("aria-label",u.username+" role");const active=node("input");active.type="checkbox";active.checked=!!u.active;active.setAttribute("aria-label",u.username+" active");const roleCell=node("td"),activeCell=node("td"),controls=node("td");roleCell.append(role);activeCell.append(active);controls.append(action("Save",async()=>{await api("/users/"+u.id,{method:"PATCH",body:{role:role.value,active:active.checked}});notice("User updated. Existing sessions were invalidated.");await refresh();}),document.createTextNode(" "),action("Reset password",async()=>{const password=prompt("New password (at least 8 characters)");if(password===null)return;await api("/users/"+u.id+"/password",{method:"POST",body:{password}});notice("Password changed. Existing sessions were invalidated.");}));tr.append(node("td",u.username),roleCell,activeCell,controls);list.append(tr);}}
  bindForm("#user-form",async(form,data)=>{await api("/users",{method:"POST",body:data});form.reset();notice("Account created.");await refresh();});await refresh();
}
run(async()=>{
  const session=await api("/session");csrf=session.csrf_token;
  if(page!=="login"&&!session.user){location.assign("/login");return;}
  $("#logout")?.addEventListener("click",()=>run(async()=>{await api("/logout",{method:"POST"});location.assign("/login");}));
  bindForm("#login-form",async(form,data)=>{await api("/login",{method:"POST",body:data});location.assign("/app/hooks");});
  bindForm("#password-form",async(form,data)=>{await api("/me/password",{method:"POST",body:data});location.assign("/login");});
  if(page==="hooks")await loadHooks();if(page==="editor")await editor();if(page==="test")await testPage();if(page==="executions")await activity();if(page==="users")await usersPage();
  if(page==="detail"){const result=await api("/executions/"+document.body.dataset.executionId);$("#execution-detail").textContent=JSON.stringify(result,null,2);$("#uncertain").hidden=!result.uncertain_result;}
});
