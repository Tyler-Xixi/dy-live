"use strict";
(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const message = document.getElementById("message");
  const show = (text) => { if (message) { message.hidden = false; message.textContent = text; } };
  const messages = {login_required:"请重新登录",session_expired:"登录已过期，请重新登录",invalid_credentials:"账号或密码错误",rate_limited:"尝试过于频繁，请稍后再试",invalid_csrf:"页面已过期，请刷新后重试",invalid_card_settings:"卡密数量、期限或备注格式有误",invalid_card_update:"修改内容不符合规则，请核对期限和当前状态"};
  async function post(path, data) {
    const response = await fetch(path, {method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(messages[result.detail] || "操作失败，请核对输入或稍后重试");
    return result;
  }
  function bindForm(id, action) {
    const form = document.getElementById(id);
    form?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      button.disabled = true;
      try { await action(form, new FormData(form)); } catch (error) { show(error.message || "网络连接失败"); }
      finally { button.disabled = false; }
    });
  }
  bindForm("login-form", async (_form,data) => {
    await post("/admin/login", {username:data.get("username"),password:data.get("password")});
    location.assign("/admin/cards");
  });
  document.getElementById("logout")?.addEventListener("click", async () => {
    try { await post("/admin/logout", {}); location.assign("/admin/login"); } catch (error) { show(error.message); }
  });
  const generated = document.getElementById("generated");
  const output = document.getElementById("generated-cards");
  let batchId = "";
  const clear = () => { if (output) output.value = ""; if (generated) generated.hidden = true; batchId = ""; };
  const preset = document.getElementById("term-preset"), days = document.getElementById("duration-days");
  preset?.addEventListener("change", () => {
    days.disabled = preset.value === "permanent";
    if (["1","30","365"].includes(preset.value)) days.value = preset.value;
  });
  bindForm("generate-form", async (_form,data) => {
    clear();
    const result = await post("/admin/cards/generate", {count:Number(data.get("count")),duration_days:data.get("preset")==="permanent"?null:Number(data.get("duration_days")),note:data.get("note")});
    batchId = result.batch_id;
    output.value = result.cards.join("\n");
    document.getElementById("batch-id").textContent = "批次编号：" + batchId;
    generated.hidden = false;
    show("生成成功，请立即保存；刷新页面后列表会更新。");
  });
  document.getElementById("copy-cards")?.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(output.value); show("已复制，请妥善保存。"); }
    catch { output.focus(); output.select(); show("自动复制失败，请按 Ctrl+C 复制。"); }
  });
  document.getElementById("download-cards")?.addEventListener("click", () => {
    if (!output.value) return;
    const url = URL.createObjectURL(new Blob([output.value], {type:"text/plain;charset=utf-8"}));
    const anchor = document.createElement("a"); anchor.href=url; anchor.download="cards-"+batchId+".txt";
    anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  document.getElementById("clear-cards")?.addEventListener("click", clear);
  window.addEventListener("pagehide", clear);
  window.addEventListener("pageshow", (event) => { if (event.persisted) clear(); });
  bindForm("edit-card-form", async (form,data) => {
    const values = {note:data.get("note")};
    if (data.has("expires_at")) {
      const value = data.get("expires_at");
      if (!data.has("permanent") && !value) throw new Error("请填写到期时间");
      const field = form.elements.namedItem("expires_at"), permanent = form.elements.namedItem("permanent");
      // A note-only edit must not resubmit a rounded/expired timestamp.
      if (value !== field.defaultValue || permanent.checked !== permanent.defaultChecked) {
        values.expires_at = data.has("permanent")?null:Math.floor(Date.parse(value+(value.length===16?":00":"")+"+08:00")/1000);
      }
    } else values.duration_days = data.has("permanent")?null:Number(data.get("duration_days"));
    await post("/admin/cards/"+encodeURIComponent(form.dataset.cardId)+"/edit", values); location.reload();
  });
  bindForm("renew-form", async (form,data) => {
    await post("/admin/cards/"+encodeURIComponent(form.dataset.cardId)+"/renew", {days:Number(data.get("days"))}); location.reload();
  });
  document.querySelectorAll("button[data-action]").forEach(button => button.addEventListener("click", async () => {
    const action = button.dataset.action;
    if (!["disable","restore","unbind","archive"].includes(action)) return;
    if (!confirm("确认执行此操作？长期断网的旧电脑无法即时撤销授权。")) return;
    button.disabled = true;
    try { await post("/admin/cards/"+encodeURIComponent(button.dataset.cardId)+"/"+action, {}); location.reload(); }
    catch (error) { show(error.message); button.disabled=false; }
  }));
})();
