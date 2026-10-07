const form = document.querySelector("#taskForm");
const statusPill = document.querySelector("#statusPill");
const statusText = document.querySelector("#statusText");
const startBtn = document.querySelector("#startBtn");
const stopBtn = document.querySelector("#stopBtn");
const logsEl = document.querySelector("#logs");
const clearLogsBtn = document.querySelector("#clearLogsBtn");

let localLogs = [];

function formValue(name) {
  const element = form.elements.namedItem(name);
  if (!element) return "";
  if (element instanceof RadioNodeList) return element.value;
  if (element.type === "checkbox") return element.checked;
  return element.value;
}

function numberValue(name) {
  const value = Number(formValue(name));
  return Number.isFinite(value) ? value : undefined;
}

function buildPayload() {
  return {
    mode: formValue("mode"),
    liveUrl: formValue("liveUrl"),
    productUrl: formValue("productUrl"),
    productName: formValue("productName"),
    productId: formValue("productId"),
    targetPrice: numberValue("targetPrice"),
    pollMs: numberValue("pollMs"),
    jitterMs: numberValue("jitterMs"),
    prewarmMs: numberValue("prewarmMs"),
    openPanelIntervalMs: numberValue("openPanelIntervalMs"),
    buyQuantity: numberValue("buyQuantity"),
    buyTimes: numberValue("buyTimes"),
    scheduleWindow1: formValue("scheduleWindow1"),
    scheduleWindow2: formValue("scheduleWindow2"),
    scheduleWindow3: formValue("scheduleWindow3"),
    scheduleWindow4: formValue("scheduleWindow4"),
    maxRetries: numberValue("maxRetries"),
    retryBaseMs: numberValue("retryBaseMs"),
    circuitBreaker429: numberValue("circuitBreaker429"),
    monitorDurationMs: numberValue("monitorDurationMs"),
    maxOrderSteps: numberValue("maxOrderSteps"),
    postBuyDelayMs: numberValue("postBuyDelayMs"),
    orderStepDelayMs: numberValue("orderStepDelayMs"),
    clickTimeoutMs: numberValue("clickTimeoutMs"),
    strictPriceMatch: formValue("strictPriceMatch"),
    autoPay: formValue("autoPay"),
    allowReservationClick: formValue("allowReservationClick"),
    dryRun: formValue("dryRun"),
    headless: formValue("headless"),
    saveDiagnostics: formValue("saveDiagnostics"),
    closeBrowserOnFinish: formValue("closeBrowserOnFinish"),
  };
}

function setRunning(running) {
  statusPill.classList.toggle("running", running);
  statusText.textContent = running ? "运行中" : "未运行";
  startBtn.disabled = running;
  stopBtn.disabled = !running;
}

function addLog(entry) {
  localLogs.push(entry);
  if (localLogs.length > 500) localLogs = localLogs.slice(-500);
  renderLogs();
}

function renderLogs() {
  logsEl.innerHTML = "";
  for (const entry of localLogs) {
    const row = document.createElement("div");
    row.className = `log-row ${entry.level || "info"}`;

    const time = document.createElement("span");
    time.className = "time";
    time.textContent = entry.time || "";

    const level = document.createElement("span");
    level.className = "level";
    level.textContent = String(entry.level || "info").toUpperCase();

    const msg = document.createElement("span");
    msg.className = "msg";
    msg.textContent = entry.message || "";

    row.append(time, level, msg);
    logsEl.append(row);
  }
  logsEl.scrollTop = logsEl.scrollHeight;
}

function appendClientLog(level, message) {
  addLog({
    time: new Date().toLocaleString("zh-CN", { hour12: false }),
    level,
    message,
  });
}

function updateMode() {
  const mode = formValue("mode");
  document.querySelectorAll(".flash-only").forEach((node) => {
    node.classList.toggle("hidden", mode !== "flash");
  });
  document.querySelectorAll(".batch-only").forEach((node) => {
    node.classList.toggle("hidden", mode !== "batch");
  });
}

async function postJson(url, payload = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

form.addEventListener("change", (event) => {
  if (event.target.name === "mode") updateMode();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const payload = buildPayload();
    if (!payload.dryRun) {
      const message = payload.autoPay
        ? "已开启自动点击立即支付，可能直接产生真实扣款。确定启动吗？"
        : "已关闭 DRY_RUN，将执行真实下单操作。确定启动吗？";
      if (!window.confirm(message)) return;
    }
    const data = await postJson("/api/start", payload);
    setRunning(data.status.running);
  } catch (error) {
    appendClientLog("error", `启动失败：${error.message}`);
  }
});

stopBtn.addEventListener("click", async () => {
  try {
    const data = await postJson("/api/stop");
    setRunning(data.status.running);
  } catch (error) {
    appendClientLog("error", `停止失败：${error.message}`);
  }
});

clearLogsBtn.addEventListener("click", () => {
  localLogs = [];
  renderLogs();
});

async function loadInitialStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    const data = await response.json();
    setRunning(data.status.running);
    localLogs = data.logs || [];
    renderLogs();
  } catch (error) {
    appendClientLog("error", `读取状态失败：${error.message}`);
  }
}

function connectEvents() {
  const source = new EventSource("/events");

  source.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "hello") {
      setRunning(Boolean(payload.status?.running));
      localLogs = payload.logs || localLogs;
      renderLogs();
      return;
    }
    if (payload.type === "status") {
      setRunning(Boolean(payload.status?.running));
      return;
    }
    if (payload.type === "log") {
      addLog(payload.entry);
    }
  });

  source.addEventListener("error", () => {
    appendClientLog("warn", "日志连接断开，浏览器会自动重连");
  });
}

updateMode();
await loadInitialStatus();
connectEvents();
