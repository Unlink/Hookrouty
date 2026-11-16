"use strict";

function curlPage() {
  const form = $("#curl-form"), token = $("#curl-token"), body = $("#curl-body");
  const url = $("#curl-url"), command = $("#curl-command"), error = $("#curl-validation");
  const copies = [$("#curl-copy-url"), $("#curl-copy-command")];
  const fields = new Map();
  const bodyAllowed = !["GET", "HEAD"].includes(hook.input_method);
  let rotating = false;
  for (const segment of hook.input_path.split("/")) {
    if (!segment.startsWith(":")) continue;
    const key = segment.slice(1), label = node("label", "Path parameter: " + key), input = node("input");
    input.value = "example"; input.required = true; input.autocomplete = "off";
    label.append(input); $("#curl-path-fields").append(label); fields.set(key, input);
  }
  function quote(value) { return "'" + value.replaceAll("'", "'\"'\"'") + "'"; }
  function segment(value) {
    return [".", ".."].includes(value) ? value.replaceAll(".", "%2E") : encodeURIComponent(value);
  }
  function update() {
    url.value = ""; command.value = ""; error.hidden = true;
    copies.forEach(button => { button.disabled = true; });
    try {
      const path = hook.input_path.split("/").map(part => {
        if (!part.startsWith(":")) return segment(part);
        const value = fields.get(part.slice(1)).value;
        if (!value || /[/\\\0]/.test(value)) throw new Error("Fill each path parameter with one nonempty segment, without a slash or backslash.");
        return segment(value);
      }).join("/");
      if (/[\r\n\0]/.test(token.value)) throw new Error("The webhook token must not contain line breaks or NUL characters.");
      if (bodyAllowed && body.value !== "") JSON.parse(body.value);
      const credential = token.value || "REPLACE_WITH_WEBHOOK_TOKEN";
      let address = document.body.dataset.appUrl.replace(/\/$/, "") + path;
      if (hook.auth.mode === "query") address += "?token=" + encodeURIComponent(credential);
      const parts = ["curl --fail-with-body --path-as-is", hook.input_method === "HEAD" ? "--head" : "--request " + hook.input_method, quote(address)];
      if (hook.auth.mode === "header") parts.push("--header " + quote("X-Webhook-Token: " + credential));
      if (bodyAllowed && body.value !== "") parts.push("--header 'Content-Type: application/json'", "--data-binary " + quote(body.value));
      url.value = address; command.value = parts.join(" ");
      $("#curl-url-hint").textContent = hook.auth.mode === "query" ? "This URL contains the webhook token. Keep it private." : "This URL requires an X-Webhook-Token header. Use the curl command below.";
      copies.forEach(button => { button.disabled = false; });
    } catch (failure) {
      error.textContent = failure instanceof SyntaxError ? "Enter valid JSON, or leave the request body empty." : failure instanceof URIError ? "Enter valid text for every path parameter and token." : failure.message;
      error.hidden = false;
    }
  }
  async function copy(input) {
    try { if (!navigator.clipboard?.writeText) throw new Error(); await navigator.clipboard.writeText(input.value); }
    catch { input.focus(); input.select(); if (!document.execCommand("copy")) throw new Error("Automatic copy is unavailable. Select and copy the field manually."); }
    notice("Copied. Keep webhook credentials private.");
  }
  form.addEventListener("submit", event => event.preventDefault());
  form.addEventListener("input", update);
  copies[0].addEventListener("click", () => run(() => copy(url)));
  copies[1].addEventListener("click", () => run(() => copy(command)));
  $("#curl-rotate").addEventListener("click", () => run(async () => {
    if (rotating || !confirm("Generate a new webhook token? The previous token will stop working immediately. Update all existing callers.")) return;
    rotating = true; $("#curl-rotate").disabled = true;
    try {
      const result = await api("/hooks/" + hookId + "/rotate-token", {method:"POST", body:{}});
      token.value = result.token; update();
      notice("New token generated. Previous token no longer works. Copy and save the new token from the field or command now; it cannot be retrieved later.");
    } finally { rotating = false; $("#curl-rotate").disabled = false; }
  }));
  window.addEventListener("pagehide", () => { token.value = ""; body.value = ""; url.value = ""; command.value = ""; });
  window.addEventListener("pageshow", update);
  update();
}
