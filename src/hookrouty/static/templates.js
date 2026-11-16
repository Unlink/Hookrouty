"use strict";

async function wizard() {
  const form = $("#wizard-form"), chooser = $("#template-choice"), inputs = $("#wizard-inputs");
  const panels = $$(".wizard-panel"), next = $("#wizard-next"), back = $("#wizard-back");
  const templates = (await api("/templates")).filter(template => template.enabled);
  let step = 0, selected = null, busy = false;
  chooser.replaceChildren();
  for (const template of templates) {
    const option = node("option", template.name); option.value = template.id; chooser.append(option);
  }
  function showStep(value) {
    step = value;
    panels.forEach((panel, index) => { panel.hidden = index !== step; panel.disabled = index !== step; });
    $$(".wizard-progress li").forEach((item, index) => { if (index === step) item.setAttribute("aria-current", "step"); else item.removeAttribute("aria-current"); });
    back.hidden = step === 0; next.textContent = step === 2 ? "Create webhook" : "Continue";
    $("legend", panels[step]).tabIndex = -1; $("legend", panels[step]).focus();
  }
  function selectTemplate() {
    selected = templates.find(template => template.id === chooser.value);
    inputs.replaceChildren();
    $("#template-description").textContent = selected?.description || "";
    next.disabled = !selected;
    if (!selected) return;
    for (const field of selected.fields) {
      const label = node("label", field.label), input = node(field.kind === "select" ? "select" : field.kind === "json" ? "textarea" : "input");
      input.id = "wizard-value-" + field.key;
      input.required = field.required;
      if (field.kind === "select") for (const choice of field.options) { const option = node("option", choice); option.value = choice; input.append(option); }
      else if (field.kind !== "json") { input.type = field.kind === "secret" ? "password" : field.kind === "url" ? "url" : "text"; input.autocomplete = field.kind === "secret" ? "new-password" : "off"; }
      else { input.rows = 5; input.className = "code"; }
      input.value = field.default === null || field.default === undefined ? "" : field.kind === "json" ? JSON.stringify(field.default, null, 2) : field.default;
      label.append(input); inputs.append(label);
      if (field.help) inputs.append(node("p", field.help, "hint"));
    }
  }
  function draft() {
    const values = Object.create(null);
    for (const field of selected.fields) {
      const input = $("#wizard-value-" + field.key);
      if (input.value === "" && !field.required && field.default !== null && field.default !== undefined) continue;
      values[field.key] = field.kind === "json" ? input.value.trim() ? JSON.parse(input.value) : null : input.value;
    }
    return { name: form.elements.name.value, values, input_path: form.elements.input_path.value || null, auth_mode: form.elements.auth_mode.value, enabled: form.elements.enabled.checked };
  }
  async function review() {
    const preview = await api("/templates/" + selected.id + "/preview", {method:"POST", body:draft()});
    if (!form.elements.input_path.value) form.elements.input_path.value = preview.input_path;
    $("#wizard-destination").textContent = preview.method + " " + preview.url;
    $("#wizard-preview").textContent = JSON.stringify(preview, null, 2);
    showStep(2);
  }
  function shellQuote(value) { return "'" + value.replaceAll("'", "'\"'\"'") + "'"; }
  function result(created) {
    const hook = created.hook, token = created.token;
    const base = document.body.dataset.appUrl.replace(/\/$/, "") + hook.input_path;
    const url = hook.auth.mode === "query" ? base + "?token=" + encodeURIComponent(token) : base;
    $("#created-url").value = url; $("#created-token").value = token;
    $("#created-url-hint").textContent = hook.auth.mode === "query" ? "This URL contains your token. Keep it private." : "Send the token in the X-Webhook-Token header, or use the curl below.";
    $("#created-curl").value = "curl --fail-with-body -X POST " + shellQuote(url) + (hook.auth.mode === "header" ? " -H " + shellQuote("X-Webhook-Token: " + token) : "");
    $("#created-test").href = "/app/hooks/" + created.id + "/test";
    $("#created-edit").href = "/app/hooks/" + created.id + "/edit";
    for (const input of $$("input[type=password]", form)) input.value = "";
    form.hidden = true; $(".wizard-progress").hidden = true; $("#wizard-result").hidden = false;
    notice(hook.enabled ? "Webhook created and enabled. No destination request was sent." : "Webhook created and disabled. No destination request was sent.");
  }
  chooser.addEventListener("change", selectTemplate);
  back.addEventListener("click", () => { if (!busy) showStep(step - 1); });
  form.addEventListener("submit", event => { event.preventDefault(); if (busy) return; run(async () => {
    busy = true; next.disabled = true; back.disabled = true;
    try {
      if (step === 0) showStep(1);
      else if (step === 1) await review();
      else result(await api("/templates/" + selected.id + "/hooks", {method:"POST", body:draft()}));
    } finally { busy = false; next.disabled = !selected; back.disabled = false; }
  }); });
  for (const button of $$('[data-copy]')) button.addEventListener("click", () => run(async () => {
    const input = $("#" + button.dataset.copy), value = input.value;
    try { if (!navigator.clipboard?.writeText) throw new Error(); await navigator.clipboard.writeText(value); }
    catch { const type = input.type; if (type === "password") input.type = "text"; input.focus(); input.select(); const copied = document.execCommand("copy"); if (type === "password") input.type = type; if (!copied) throw new Error("Automatic copy is unavailable. Select and copy the field manually."); }
    notice("Copied. Keep webhook credentials private.");
  }));
  $("#no-templates").hidden = templates.length > 0;
  selectTemplate();
}

async function templateAdmin() {
  const form = $("#template-form");
  let editing = null, templates = [];
  const example = {name:"Simple URL template", description:"Call an HTTP endpoint", enabled:true, timeout_seconds:60, fields:[{key:"url",label:"Destination URL",kind:"url",required:true}], target:{method:"POST",url:"{{inputs.url}}"}};
  function edit(template, copy = false) {
    editing = copy ? null : template.id || null;
    form.elements.name.value = template.name + (copy ? " (copy)" : "");
    form.elements.description.value = template.description || "";
    form.elements.enabled.checked = template.enabled;
    form.elements.timeout_seconds.value = template.timeout_seconds || 60;
    form.elements.fields.value = JSON.stringify(template.fields, null, 2);
    form.elements.target.value = JSON.stringify(template.target, null, 2);
    $("#template-editor-title").textContent = editing ? "Edit template" : "New template";
  }
  async function refresh() {
    templates = await api("/templates"); const list = $("#templates-list"); list.replaceChildren();
    $("#empty-templates").hidden = templates.length > 0;
    for (const template of templates) {
      const row = node("tr"), first = node("td"), state = node("td"), controls = node("td");
      first.append(node("strong", template.name), node("p", template.description, "hint")); state.append(badge(template.enabled ? "enabled" : "disabled"));
      controls.append(action("Edit", () => edit(template)), action("Copy", () => edit(template,true)), action("Delete", async () => {
        if (!confirm("Delete this template? Existing hooks remain available.")) return;
        await api("/templates/" + template.id, {method:"DELETE"});
        if (editing === template.id) edit(example); await refresh();
      }, "danger")); row.append(first,state,controls); list.append(row);
    }
  }
  $("#new-template").addEventListener("click", () => edit(example));
  bindForm("#template-form", async () => {
    const spec = {name:form.elements.name.value,description:form.elements.description.value,enabled:form.elements.enabled.checked,timeout_seconds:Number(form.elements.timeout_seconds.value),fields:JSON.parse(form.elements.fields.value),target:JSON.parse(form.elements.target.value)};
    const saved = await api(editing ? "/templates/" + editing : "/templates", {method:editing ? "PUT" : "POST",body:spec});
    edit(saved); await refresh(); notice("Template saved. Existing hooks were not changed.");
  });
  edit(example); await refresh();
}
