#!/usr/bin/env node

import http from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { DEFAULT_CONFIG, runAutomation } from "./automation_runner.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 8787);

const clients = new Set();
let currentTask = null;
const logs = [];

function addLog(level, message, data = undefined) {
  const entry = {
    time: new Date().toLocaleString("zh-CN", { hour12: false }),
    level,
    message,
    data,
  };
  logs.push(entry);
  while (logs.length > 500) logs.shift();
  broadcast({ type: "log", entry });
}

function broadcast(payload) {
  const text = `data: ${JSON.stringify(payload)}\n\n`;
  for (const res of clients) {
    res.write(text);
  }
}

function taskStatus() {
  return {
    running: Boolean(currentTask),
    startedAt: currentTask?.startedAt || null,
    config: currentTask?.config || null,
  };
}

function sendJson(res, statusCode, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(statusCode, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  res.end(body);
}

async function readJsonBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const text = Buffer.concat(chunks).toString("utf8");
  if (!text.trim()) return {};
  return JSON.parse(text);
}

function validateStartConfig(config) {
  if (config.mode === "batch") {
    if (!String(config.productUrl || "").trim()) {
      return "批量购买模式需要填写现有商品链接";
    }
    return "";
  }

  if (!String(config.liveUrl || "").trim()) {
    return "抢商品模式需要填写直播间链接";
  }
  if (!String(config.productName || config.productId || "").trim()) {
    return "抢商品模式需要填写商品关键词或商品编号";
  }
  return "";
}

async function serveStatic(res, fileName, contentType) {
  const filePath = path.join(__dirname, "public", fileName);
  const body = await readFile(filePath);
  res.writeHead(200, {
    "content-type": contentType,
    "cache-control": "no-store",
  });
  res.end(body);
}

async function handleApi(req, res, pathname) {
  if (pathname === "/api/status" && req.method === "GET") {
    sendJson(res, 200, { ok: true, status: taskStatus(), logs });
    return;
  }

  if (pathname === "/api/start" && req.method === "POST") {
    if (currentTask) {
      sendJson(res, 409, { ok: false, error: "已有任务正在运行" });
      return;
    }

    const body = await readJsonBody(req);
    const config = {
      ...DEFAULT_CONFIG,
      ...body,
      scheduleWindows: [
        body.scheduleWindow1,
        body.scheduleWindow2,
        body.scheduleWindow3,
        body.scheduleWindow4,
      ].filter(Boolean),
      strictPriceMatch: body.strictPriceMatch,
      allowReservationClick: body.allowReservationClick,
      openPanelIntervalMs: body.openPanelIntervalMs,
      maxOrderSteps: body.maxOrderSteps,
      postBuyDelayMs: body.postBuyDelayMs,
      orderStepDelayMs: body.orderStepDelayMs,
      clickTimeoutMs: body.clickTimeoutMs,
      autoPay: body.autoPay,
      submitPaymentAndAbandon: false,
      saveDiagnostics: body.saveDiagnostics,
      profileDir: body.profileDir || DEFAULT_CONFIG.profileDir,
      networkLogPath: body.networkLogPath || DEFAULT_CONFIG.networkLogPath,
      diagnosticsDir: body.diagnosticsDir || DEFAULT_CONFIG.diagnosticsDir,
      closeBrowserOnFinish: body.closeBrowserOnFinish ?? DEFAULT_CONFIG.closeBrowserOnFinish,
    };
    const error = validateStartConfig(config);
    if (error) {
      sendJson(res, 400, { ok: false, error });
      return;
    }

    const controller = new AbortController();
    currentTask = {
      controller,
      startedAt: new Date().toISOString(),
      config,
    };
    addLog("info", "收到启动请求，任务开始运行");
    broadcast({ type: "status", status: taskStatus() });

    runAutomation(config, {
      signal: controller.signal,
      onLog: (entry) => {
        logs.push(entry);
        while (logs.length > 500) logs.shift();
        broadcast({ type: "log", entry });
      },
    })
      .then((result) => {
        addLog("info", `任务结束：${result.status}${result.reason ? `，${result.reason}` : ""}`);
      })
      .catch((taskError) => {
        addLog("error", `任务异常退出：${taskError.message}`);
      })
      .finally(() => {
        currentTask = null;
        broadcast({ type: "status", status: taskStatus() });
      });

    sendJson(res, 200, { ok: true, status: taskStatus() });
    return;
  }

  if (pathname === "/api/stop" && req.method === "POST") {
    if (!currentTask) {
      sendJson(res, 200, { ok: true, status: taskStatus() });
      return;
    }

    addLog("warn", "收到停止请求，正在中止任务");
    currentTask.controller.abort();
    sendJson(res, 200, { ok: true, status: taskStatus() });
    return;
  }

  sendJson(res, 404, { ok: false, error: "Not found" });
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);

    if (url.pathname === "/events") {
      res.writeHead(200, {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-store",
        connection: "keep-alive",
      });
      clients.add(res);
      res.write(`data: ${JSON.stringify({ type: "hello", status: taskStatus(), logs })}\n\n`);
      req.on("close", () => clients.delete(res));
      return;
    }

    if (url.pathname.startsWith("/api/")) {
      await handleApi(req, res, url.pathname);
      return;
    }

    if (url.pathname === "/" || url.pathname === "/index.html") {
      await serveStatic(res, "index.html", "text/html; charset=utf-8");
      return;
    }

    if (url.pathname === "/app.css") {
      await serveStatic(res, "app.css", "text/css; charset=utf-8");
      return;
    }

    if (url.pathname === "/app.js") {
      await serveStatic(res, "app.js", "text/javascript; charset=utf-8");
      return;
    }

    res.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
    res.end("Not found");
  } catch (error) {
    sendJson(res, 500, { ok: false, error: error.message });
  }
});

server.listen(PORT, "127.0.0.1", () => {
  addLog("info", `GUI 服务已启动：http://127.0.0.1:${PORT}`);
});
