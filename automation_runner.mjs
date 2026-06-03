import fs from "node:fs";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { setTimeout as delay } from "node:timers/promises";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright";

const DEFAULT_PRODUCT_NAME =
  "Geil/金邦巨蟹升级款ddr5内存条6000频率台式机内存条";

const BUY_TEXT_RE =
  /(抢购|立即抢|马上抢|立即购买|去购买|购买|下单|加入购物车|提交订单|去结算)/;
const BUY_READY_TEXT_RE = /(立即购买|去抢购|抢购|立即抢|马上抢|购买|下单)/;
const WAITING_SALE_TEXT_RE =
  /(等待开售|待开售|即将开售|未开售|开售提醒|开抢提醒|预约|已预约|提醒我|距开售)/;
const RESERVATION_TEXT_RE = /(预约|提醒我|开售提醒|开抢提醒)/;
const PAYMENT_TEXT_RE =
  /(付款|支付|立即支付|确认支付|输入密码|收银台|支付方式)/;
const PAYMENT_SUBMIT_TEXT_RE =
  /(付款|支付|立即支付|确认支付|确认付款|提交支付|去支付)/;
const PAYMENT_AMOUNT_TEXT_RE =
  /^(付款|支付|立即支付|确认支付|确认付款|提交支付|去支付)\s*(?:¥|￥)?\s*\d+(?:\.\d{1,2})?$/;
const CLOSE_PAYMENT_TEXT_RE = /(关闭|取消|返回|×|✕|X)/;
const ABANDON_PAYMENT_TEXT_RE =
  /(放弃|确认放弃|放弃支付|确认离开|离开|仍要离开|确定放弃|暂不支付)/;
const COMMERCE_PANEL_TEXT_RE =
  /(小黄车|购物车|购物袋|商品|商品列表|全部商品|讲解商品|正在讲解|橱窗|去看看)/;
const ALL_PRODUCTS_TEXT_RE = /^\s*全部商品\s*$/;
const ORDER_STEP_TEXT_RE =
  /(确定|确认|选好了|完成|下一步|提交订单|提交|去结算|立即购买|下单)/;
const DISMISS_TEXT_RE =
  /(我知道了|知道了|同意|允许|稍后再说|以后再说|关闭|继续看播|继续观看|继续看直播)/;
const UNAVAILABLE_TEXT_RE =
  /(售罄|已售罄|抢光|已抢光|缺货|补货中|已结束|已下架|不可购买|卖光)/;

export const DEFAULT_CONFIG = {
  mode: "flash",
  liveUrl: "https://live.douyin.com/952520575686?anchor_id=",
  productUrl: "",
  productName: DEFAULT_PRODUCT_NAME,
  productId: "",
  targetPrice: 799,
  dryRun: true,
  headless: false,
  pollMs: 80,
  jitterMs: 10,
  monitorDurationMs: 75_000,
  prewarmMs: 300,
  strictPriceMatch: false,
  allowReservationClick: false,
  openPanelIntervalMs: 500,
  maxOrderSteps: 8,
  postBuyDelayMs: 120,
  orderStepDelayMs: 120,
  clickTimeoutMs: 700,
  submitPaymentAndAbandon: true,
  saveDiagnostics: true,
  maxRetries: 7,
  retryBaseMs: 200,
  circuitBreaker429: 3,
  buyQuantity: 1,
  buyTimes: 1,
  scheduleWindows: ["10:29:00", "12:29:00", "14:29:00", "16:29:00"],
  profileDir: path.resolve("live-room-profile"),
  networkLogPath: path.resolve("live_room_network_hits.jsonl"),
  diagnosticsDir: path.resolve("diagnostics"),
  closeBrowserOnFinish: true,
};

class GracefulStop extends Error {
  constructor(message) {
    super(message);
    this.name = "GracefulStop";
  }
}

export function nowText(date = new Date()) {
  return date.toLocaleString("zh-CN", { hour12: false });
}

function asBoolean(value, fallback = false) {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "boolean") return value;
  return ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
}

function asNumber(value, fallback, min = undefined) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  if (min !== undefined && number < min) return min;
  return number;
}

function parseScheduleWindows(value) {
  const rawItems = Array.isArray(value)
    ? value
    : String(value || "")
        .split(/[,\n;|]+/)
        .map((item) => item.trim());

  return rawItems
    .filter(Boolean)
    .slice(0, 4)
    .map((item) => {
      const match = String(item).match(/^(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?$/);
      if (!match) return null;
      const hour = Number(match[1]);
      const minute = Number(match[2]);
      const second = Number(match[3] || 0);
      if (hour > 23 || minute > 59 || second > 59) return null;
      return {
        label: `${String(hour).padStart(2, "0")}:${String(minute).padStart(
          2,
          "0",
        )}:${String(second).padStart(2, "0")}`,
        hour,
        minute,
        second,
      };
    })
    .filter(Boolean);
}

function normalizeText(value) {
  return String(value || "")
    .replace(/\s+/g, "")
    .toLowerCase();
}

function compactText(value, limit = 300) {
  return String(value || "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, limit);
}

function escapeRegex(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function keywordTokens(config) {
  const values = [config.productName]
    .flatMap((value) => String(value || "").split(/[\s,，;；|/()（）【】\[\]\-_/]+/))
    .map((value) => value.trim())
    .filter((value) => {
      if (value.length < 2) return false;
      if (/^\d+$/.test(value)) return false;
      return true;
    });

  if (values.length > 0) return [...new Set(values)];
  return ["Geil", "金邦", "巨蟹", "ddr5", "6000"];
}

function productMatches(text, config) {
  const haystack = normalizeText(text);
  const exactName = normalizeText(config.productName);

  if (exactName && haystack.includes(exactName)) return true;

  const tokens = keywordTokens(config).map(normalizeText);
  const hitCount = tokens.filter((token) => haystack.includes(token)).length;
  const requiredHits = Math.min(Math.max(3, Math.ceil(tokens.length * 0.75)), 5);
  return tokens.length > 0 && hitCount >= requiredHits;
}

function productIndexMatches(text, config) {
  const productId = String(config.productId || "").trim();
  if (!productId) return true;
  if (!/^\d+$/.test(productId)) {
    return normalizeText(text).includes(normalizeText(productId));
  }
  const value = productId.replace(/^0+/, "") || "0";
  const textValue = String(text || "");
  return new RegExp(`(^|\\s|\\n)${escapeRegex(value)}(\\s|\\n|【|\\[)`).test(textValue);
}

function productListIndexMatches(text, config) {
  const productId = String(config.productId || "").trim();
  if (!productId) return true;
  if (!/^\d+$/.test(productId)) {
    return normalizeText(text).includes(normalizeText(productId));
  }

  const value = productId.replace(/^0+/, "") || "0";
  const lines = String(text || "")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
  const firstText = compactText(lines.slice(0, 3).join(" "), 180);
  return new RegExp(`^${escapeRegex(value)}(?:\\s|【|\\[)`).test(firstText);
}

function exactTargetPriceMatches(text, config) {
  const targetPrice = Number(config.targetPrice);
  if (!Number.isFinite(targetPrice) || targetPrice <= 0) return false;

  const prices = [
    ...String(text || "").matchAll(/(?:￥|¥|RMB|CNY)?\s*(\d+(?:\.\d{1,2})?)/gi),
  ].map((match) => Number(match[1]));

  return prices.some((price) => Math.abs(price - targetPrice) < 0.01);
}

function priceMatches(text, config) {
  if (!config.strictPriceMatch) return true;
  return exactTargetPriceMatches(text, config);
}

function productUnavailable(text) {
  return UNAVAILABLE_TEXT_RE.test(String(text || ""));
}

function productActionState(text) {
  const value = String(text || "");
  if (productUnavailable(value)) return "unavailable";
  if (BUY_READY_TEXT_RE.test(value)) return "ready";
  if (WAITING_SALE_TEXT_RE.test(value)) return "waiting";
  return "unknown";
}

function isLikelyListProductCardText(text, config) {
  const value = compactText(text, 900);
  if (!value || value.length > 820) return false;
  if (/(商品详情|产品参数|订单留言|优惠明细|购买数量|请打开抖音APP扫描二维码)/.test(value)) {
    return false;
  }
  if (!productMatches(value, config)) return false;

  const indexMatched = productListIndexMatches(value, config);
  const keywordPriceFallback = !indexMatched && exactTargetPriceMatches(value, config);
  if (!indexMatched && !keywordPriceFallback) return false;

  return productActionState(value) !== "unknown" && priceMatches(value, config);
}

function productMatchReason(text, config) {
  if (productListIndexMatches(text, config)) return "编号命中";
  if (productMatches(text, config) && exactTargetPriceMatches(text, config)) {
    return "关键词+价格命中";
  }
  return "未命中";
}

async function textOf(locator, timeout = 350) {
  try {
    return await locator.innerText({ timeout });
  } catch {
    return "";
  }
}

function scheduleCandidateServerMs(point, nowServerMs) {
  const candidate = new Date(nowServerMs);
  candidate.setHours(point.hour, point.minute, point.second, 0);
  if (candidate.getTime() <= nowServerMs) {
    candidate.setDate(candidate.getDate() + 1);
  }
  return candidate.getTime();
}

function activeWindowEndLocalMs(config, clockOffsetMs) {
  const nowLocalMs = Date.now();
  const nowServerMs = nowLocalMs + clockOffsetMs;
  for (const point of config.scheduleWindows) {
    const start = new Date(nowServerMs);
    start.setHours(point.hour, point.minute, point.second, 0);
    const startServerMs = start.getTime();
    const endServerMs = startServerMs + config.monitorDurationMs;
    if (nowServerMs >= startServerMs - config.prewarmMs && nowServerMs < endServerMs) {
      return endServerMs - clockOffsetMs;
    }
  }
  return null;
}

function nextWindowOpenLocalMs(config, clockOffsetMs) {
  const nowServerMs = Date.now() + clockOffsetMs;
  const nextServerMs = Math.min(
    ...config.scheduleWindows.map((point) => scheduleCandidateServerMs(point, nowServerMs)),
  );
  return nextServerMs - clockOffsetMs;
}

function randomizedDelayMs(config) {
  const jitter = Math.max(0, Number(config.jitterMs) || 0);
  const delta = jitter === 0 ? 0 : Math.round(Math.random() * jitter * 2 - jitter);
  return Math.max(20, Number(config.pollMs) + delta);
}

async function waitWithAbort(ms, ctx) {
  ctx.assertRunning();
  const signal = ctx.signal;
  if (!signal) {
    await delay(Math.max(0, ms));
    return;
  }
  await delay(Math.max(0, ms), undefined, { signal });
}

async function preciseWaitUntil(targetLocalMs, ctx) {
  while (Date.now() < targetLocalMs) {
    ctx.assertRunning();
    const remaining = targetLocalMs - Date.now();
    if (remaining > 130) {
      await waitWithAbort(Math.min(remaining - 90, 30_000), ctx);
      continue;
    }
    if (remaining > 12) {
      await waitWithAbort(remaining - 5, ctx);
      continue;
    }
    await new Promise((resolve) => setImmediate(resolve));
  }
}

async function retryAction(label, config, ctx, action) {
  let lastError;
  const maxRetries = Math.max(1, Number(config.maxRetries) || 1);
  for (let attempt = 1; attempt <= maxRetries; attempt += 1) {
    ctx.assertRunning();
    try {
      return await action(attempt);
    } catch (error) {
      lastError = error;
      const backoff = config.retryBaseMs * 2 ** (attempt - 1);
      ctx.log(
        "warn",
        `${label} 第 ${attempt}/${maxRetries} 次失败：${error.message}`,
      );
      if (attempt < maxRetries) {
        await waitWithAbort(backoff, ctx);
      }
    }
  }
  throw lastError;
}

function buildConfig(overrides = {}) {
  const config = {
    ...DEFAULT_CONFIG,
    ...overrides,
  };

  config.mode = config.mode === "batch" ? "batch" : "flash";
  config.liveUrl = String(config.liveUrl || DEFAULT_CONFIG.liveUrl).trim();
  config.productUrl = String(config.productUrl || "").trim();
  config.productName = String(config.productName || "").trim();
  config.productId = String(config.productId || "").trim();
  config.targetPrice = asNumber(config.targetPrice, DEFAULT_CONFIG.targetPrice, 0);
  config.dryRun = asBoolean(config.dryRun, DEFAULT_CONFIG.dryRun);
  config.headless = asBoolean(config.headless, DEFAULT_CONFIG.headless);
  config.closeBrowserOnFinish = asBoolean(
    config.closeBrowserOnFinish,
    DEFAULT_CONFIG.closeBrowserOnFinish,
  );
  config.pollMs = asNumber(config.pollMs, DEFAULT_CONFIG.pollMs, 20);
  config.jitterMs = asNumber(config.jitterMs, DEFAULT_CONFIG.jitterMs, 0);
  config.monitorDurationMs = asNumber(
    config.monitorDurationMs,
    DEFAULT_CONFIG.monitorDurationMs,
    1_000,
  );
  config.prewarmMs = asNumber(config.prewarmMs, DEFAULT_CONFIG.prewarmMs, 0);
  config.strictPriceMatch = asBoolean(config.strictPriceMatch, DEFAULT_CONFIG.strictPriceMatch);
  config.allowReservationClick = asBoolean(
    config.allowReservationClick,
    DEFAULT_CONFIG.allowReservationClick,
  );
  config.openPanelIntervalMs = asNumber(
    config.openPanelIntervalMs,
    DEFAULT_CONFIG.openPanelIntervalMs,
    50,
  );
  config.maxOrderSteps = Math.floor(
    asNumber(config.maxOrderSteps, DEFAULT_CONFIG.maxOrderSteps, 0),
  );
  config.postBuyDelayMs = asNumber(config.postBuyDelayMs, DEFAULT_CONFIG.postBuyDelayMs, 0);
  config.orderStepDelayMs = asNumber(
    config.orderStepDelayMs,
    DEFAULT_CONFIG.orderStepDelayMs,
    0,
  );
  config.clickTimeoutMs = asNumber(config.clickTimeoutMs, DEFAULT_CONFIG.clickTimeoutMs, 100);
  config.submitPaymentAndAbandon = asBoolean(
    config.submitPaymentAndAbandon,
    DEFAULT_CONFIG.submitPaymentAndAbandon,
  );
  config.saveDiagnostics = asBoolean(config.saveDiagnostics, DEFAULT_CONFIG.saveDiagnostics);
  config.maxRetries = asNumber(config.maxRetries, DEFAULT_CONFIG.maxRetries, 1);
  config.retryBaseMs = asNumber(config.retryBaseMs, DEFAULT_CONFIG.retryBaseMs, 20);
  config.circuitBreaker429 = asNumber(
    config.circuitBreaker429,
    DEFAULT_CONFIG.circuitBreaker429,
    1,
  );
  config.buyQuantity = Math.floor(asNumber(config.buyQuantity, DEFAULT_CONFIG.buyQuantity, 1));
  config.buyTimes = Math.floor(asNumber(config.buyTimes, DEFAULT_CONFIG.buyTimes, 1));
  config.scheduleWindows = parseScheduleWindows(config.scheduleWindows);
  config.profileDir = path.resolve(String(config.profileDir || DEFAULT_CONFIG.profileDir));
  config.networkLogPath = path.resolve(
    String(config.networkLogPath || DEFAULT_CONFIG.networkLogPath),
  );
  config.diagnosticsDir = path.resolve(
    String(config.diagnosticsDir || DEFAULT_CONFIG.diagnosticsDir),
  );
  return config;
}

export function configFromEnv(env = process.env) {
  return buildConfig({
    mode: env.MODE,
    liveUrl: env.LIVE_URL,
    productUrl: env.PRODUCT_URL,
    productName: env.PRODUCT_NAME,
    productId: env.PRODUCT_ID,
    targetPrice: env.TARGET_PRICE,
    dryRun: env.DRY_RUN ?? "1",
    headless: env.HEADLESS,
    pollMs: env.POLL_MS,
    jitterMs: env.JITTER_MS,
    monitorDurationMs: env.MONITOR_DURATION_MS,
    prewarmMs: env.PREWARM_MS,
    strictPriceMatch: env.STRICT_PRICE_MATCH,
    allowReservationClick: env.ALLOW_RESERVATION_CLICK,
    openPanelIntervalMs: env.OPEN_PANEL_INTERVAL_MS,
    maxOrderSteps: env.MAX_ORDER_STEPS,
    postBuyDelayMs: env.POST_BUY_DELAY_MS,
    orderStepDelayMs: env.ORDER_STEP_DELAY_MS,
    clickTimeoutMs: env.CLICK_TIMEOUT_MS,
    submitPaymentAndAbandon: env.SUBMIT_PAYMENT_AND_ABANDON,
    saveDiagnostics: env.SAVE_DIAGNOSTICS,
    maxRetries: env.MAX_RETRIES,
    retryBaseMs: env.RETRY_BASE_MS,
    circuitBreaker429: env.CIRCUIT_BREAKER_429,
    buyQuantity: env.BUY_QUANTITY,
    buyTimes: env.BUY_TIMES,
    scheduleWindows: env.SCHEDULE_WINDOWS,
    profileDir: env.PROFILE_DIR,
    networkLogPath: env.NETWORK_LOG_PATH,
    diagnosticsDir: env.DIAGNOSTICS_DIR,
    closeBrowserOnFinish: env.CLOSE_BROWSER_ON_FINISH,
  });
}

function createRunContext(config, hooks) {
  const state = {
    stopReason: "",
    tooManyRequests: 0,
    lastPanelOpenAt: 0,
    scans: 0,
    candidateNodes: 0,
    matchedProducts: 0,
    unavailableMatches: 0,
    clickAttempts: 0,
    successfulClicks: 0,
    paymentClicks: 0,
    abandonClicks: 0,
    orderSubmitted: false,
    lastMatchText: "",
    lastClickLabel: "",
    lastDiagnosticAt: 0,
  };

  const ctx = {
    config,
    signal: hooks.signal,
    state,
    log(level, message, data = undefined) {
      const entry = {
        time: nowText(),
        level,
        message,
        data,
      };
      if (typeof hooks.onLog === "function") {
        hooks.onLog(entry);
      } else {
        const prefix = `[${entry.time}]`;
        if (level === "error") console.error(prefix, message);
        else console.log(prefix, message);
      }
    },
    requestStop(reason) {
      if (!state.stopReason) {
        state.stopReason = reason;
        ctx.log("warn", reason);
      }
    },
    assertRunning() {
      if (hooks.signal?.aborted) {
        throw new GracefulStop("任务已由用户停止");
      }
      if (state.stopReason) {
        throw new GracefulStop(state.stopReason);
      }
    },
  };

  return ctx;
}

async function launchContext(ctx) {
  const config = ctx.config;
  fs.mkdirSync(config.profileDir, { recursive: true });

  const launchOptions = {
    headless: config.headless,
    viewport: { width: 1365, height: 900 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
  };

  try {
    return await chromium.launchPersistentContext(config.profileDir, launchOptions);
  } catch (error) {
    if (String(error.message || "").includes("spawn EPERM")) {
      throw new Error(
        [
          "Playwright 浏览器启动失败：spawn EPERM。",
          "这通常表示当前 Node 进程没有权限执行 Playwright 自带的 Chromium。",
          "请在普通 PowerShell/CMD 中运行 `npm run gui`，或重新启动非沙箱环境下的 GUI 服务。",
        ].join(" "),
        { cause: error },
      );
    }
    throw error;
  }
}

async function calibrateClock(context, targetUrl, ctx) {
  const started = Date.now();
  try {
    const response = await context.request.get(targetUrl, {
      timeout: 15_000,
      maxRedirects: 2,
      failOnStatusCode: false,
    });
    const ended = Date.now();
    const dateHeader = response.headers().date;
    if (!dateHeader) {
      ctx.log("warn", "服务器响应未包含 Date 头，本次使用本地时间");
      return 0;
    }

    const serverMs = Date.parse(dateHeader);
    if (!Number.isFinite(serverMs)) {
      ctx.log("warn", `无法解析服务器 Date 头：${dateHeader}`);
      return 0;
    }

    const midpointMs = (started + ended) / 2;
    const offsetMs = Math.round(serverMs - midpointMs);
    const rttMs = ended - started;
    const verdict = Math.abs(offsetMs) <= 50 ? "满足 ≤50ms 要求" : "超过 50ms，按偏差补偿";
    ctx.log("info", `时间校准完成：offset=${offsetMs}ms, rtt=${rttMs}ms, ${verdict}`);
    return offsetMs;
  } catch (error) {
    ctx.log("warn", `时间校准失败，使用本地时间：${error.message}`);
    return 0;
  }
}

async function captureNetworkEvidence(page, ctx) {
  page.on("response", async (response) => {
    const url = response.url();
    const status = response.status();

    if (status === 429) {
      ctx.state.tooManyRequests += 1;
      ctx.log(
        "warn",
        `收到 429 Too Many Requests，连续次数 ${ctx.state.tooManyRequests}/${ctx.config.circuitBreaker429}`,
      );
      if (ctx.state.tooManyRequests >= ctx.config.circuitBreaker429) {
        ctx.requestStop("触发 429 熔断保护，任务已停止");
      }
      return;
    }

    if (status < 400) {
      ctx.state.tooManyRequests = 0;
    }

    if (!/product|goods|sku|shop|cart|order|live|commerce|ecom/i.test(url)) return;

    const contentType = response.headers()["content-type"] || "";
    if (!contentType.includes("json")) return;

    try {
      const json = await response.json();
      const body = JSON.stringify(json);
      if (productMatches(body, ctx.config) || priceMatches(body, ctx.config)) {
        ctx.log("info", `记录相关响应：${status} ${url}`);
        fs.appendFileSync(
          ctx.config.networkLogPath,
          JSON.stringify({ time: nowText(), status, url, body }) + "\n",
          "utf8",
        );
      }
    } catch {
      // Ignore non-JSON or streaming responses.
    }
  });
}

async function safeClick(locator, label, ctx) {
  ctx.state.clickAttempts += 1;
  if (ctx.config.dryRun) {
    ctx.log("info", `DRY_RUN 命中动作：${label}`);
    ctx.state.lastClickLabel = label;
    return true;
  }

  await retryAction(label, ctx.config, ctx, async () => {
    await locator.scrollIntoViewIfNeeded({ timeout: Math.min(300, ctx.config.clickTimeoutMs) }).catch(
      () => {},
    );
    await locator.click({ timeout: ctx.config.clickTimeoutMs });
    return true;
  });
  ctx.log("info", `已点击：${label}`);
  ctx.state.successfulClicks += 1;
  ctx.state.lastClickLabel = label;
  return true;
}

async function clickFirstVisible(page, candidates, label, ctx) {
  for (const candidate of candidates) {
    ctx.assertRunning();
    try {
      if ((await candidate.count()) === 0) continue;
      const action = candidate.first();
      if (!(await action.isVisible({ timeout: 120 }))) continue;
      if (!(await action.isEnabled({ timeout: 120 }).catch(() => true))) continue;
      return await safeClick(action, label, ctx);
    } catch {
      // Try the next candidate.
    }
  }
  return false;
}

async function visibleTextElementCandidates(page, pattern, ctx) {
  const viewport = page.viewportSize() || { width: 1365, height: 900 };
  const handles = await page
    .locator("button, a, [role=button], div, span")
    .filter({ hasText: pattern })
    .elementHandles()
    .catch(() => []);

  const candidates = [];
  for (const handle of handles) {
    ctx.assertRunning();
    try {
      const box = await handle.boundingBox();
      if (!box || box.width <= 8 || box.height <= 8) continue;
      if (box.x + box.width < 0 || box.y + box.height < 0) continue;
      if (box.x > viewport.width || box.y > viewport.height) continue;
      const text = compactText(await handle.evaluate((node) => node.innerText || node.textContent || ""));
      if (text.length > 120) continue;
      if (!pattern.test(text)) continue;
      candidates.push({
        handle,
        box,
        text,
        area: box.width * box.height,
      });
    } catch {
      // Try next visible text element.
    }
  }

  return candidates.sort((a, b) => a.area - b.area || b.box.y - a.box.y || b.box.x - a.box.x);
}

async function hasVisibleTextElement(page, pattern, ctx) {
  return (await visibleTextElementCandidates(page, pattern, ctx)).length > 0;
}

async function clickVisibleTextElement(page, pattern, label, ctx) {
  const candidates = await visibleTextElementCandidates(page, pattern, ctx);

  for (const candidate of candidates) {
    ctx.assertRunning();
    try {
      const { handle, box, text } = candidate;
      if (ctx.config.dryRun) {
        ctx.log("info", `DRY_RUN 命中动作：${label} (${text})`);
        return true;
      }
      await handle
        .click({ timeout: ctx.config.clickTimeoutMs })
        .catch(async () => {
          await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
        });
      ctx.state.clickAttempts += 1;
      ctx.state.successfulClicks += 1;
      ctx.state.lastClickLabel = label;
      ctx.log("info", `已点击：${label} (${text})`);
      return true;
    } catch {
      // Try next visible text element.
    }
  }

  return false;
}

async function clickTopRightIcon(page, selectors, label, ctx) {
  const viewport = page.viewportSize() || { width: 1365, height: 900 };
  const handles = await page.locator(selectors.join(", ")).elementHandles().catch(() => []);
  const candidates = [];

  for (const handle of handles) {
    ctx.assertRunning();
    try {
      const box = await handle.boundingBox();
      if (!box || box.width < 10 || box.height < 10 || box.width > 80 || box.height > 80) continue;
      if (box.x < viewport.width * 0.45 || box.y > viewport.height * 0.35) continue;
      candidates.push({ handle, box, score: box.y * 2 - box.x });
    } catch {
      // Try next icon candidate.
    }
  }

  candidates.sort((a, b) => a.score - b.score);
  for (const { handle, box } of candidates) {
    ctx.assertRunning();
    try {
      if (ctx.config.dryRun) {
        ctx.log("info", `DRY_RUN 命中动作：${label}`);
        return true;
      }
      await handle.click({ timeout: ctx.config.clickTimeoutMs }).catch(async () => {
        await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
      });
      ctx.state.clickAttempts += 1;
      ctx.state.successfulClicks += 1;
      ctx.state.lastClickLabel = label;
      ctx.log("info", `已点击：${label}`);
      return true;
    } catch {
      // Try next icon candidate.
    }
  }

  return false;
}

async function dismissBlockingOverlays(page, ctx) {
  const candidates = [
    page.getByRole("button", { name: DISMISS_TEXT_RE }),
    page.getByText(DISMISS_TEXT_RE),
    page.locator("button, [role=button], a").filter({ hasText: DISMISS_TEXT_RE }),
  ];
  await clickFirstVisible(page, candidates, "dismiss overlay", ctx);
}

async function hasVisibleAllProductsEntry(page) {
  const entry = page
    .locator("button, a, [role=button], div, span")
    .filter({ hasText: ALL_PRODUCTS_TEXT_RE })
    .first();
  return entry.isVisible({ timeout: 120 }).catch(() => false);
}

async function hasVisibleProductList(page) {
  const candidates = [
    page.locator('[data-e2e="promotion-title"], [data-e2e="shop-buyBtn"], [data-e2e="price-Area"]'),
    page.locator('[role="dialog"] li, [role="dialog"] [class*="product" i], [role="dialog"] [class*="goods" i]'),
  ];

  for (const candidate of candidates) {
    try {
      if ((await candidate.count()) === 0) continue;
      if (await candidate.first().isVisible({ timeout: 120 }).catch(() => false)) {
        return true;
      }
    } catch {
      // Try the next product-list signal.
    }
  }
  return false;
}

async function openCommercePanel(page, ctx, force = false) {
  const now = Date.now();
  if (!force && now - ctx.state.lastPanelOpenAt < ctx.config.openPanelIntervalMs) {
    return false;
  }
  ctx.state.lastPanelOpenAt = now;

  if (await clickVisibleTextElement(page, ALL_PRODUCTS_TEXT_RE, "open all products tab", ctx)) {
    await waitWithAbort(ctx.config.orderStepDelayMs, ctx);
    return true;
  }

  if (await clickVisibleTextElement(page, COMMERCE_PANEL_TEXT_RE, "open live commerce panel", ctx)) {
    await waitWithAbort(ctx.config.orderStepDelayMs, ctx);
    return true;
  }

  const candidates = [
    page.getByRole("button", { name: ALL_PRODUCTS_TEXT_RE }),
    page.getByRole("link", { name: ALL_PRODUCTS_TEXT_RE }),
    page.locator("button, a, [role=button], div, span").filter({ hasText: ALL_PRODUCTS_TEXT_RE }),
    page.getByRole("button", { name: COMMERCE_PANEL_TEXT_RE }),
    page.getByRole("link", { name: COMMERCE_PANEL_TEXT_RE }),
    page.locator("button, a, [role=button], [aria-label], [title]").filter({
      hasText: COMMERCE_PANEL_TEXT_RE,
    }),
    page.locator('[aria-label*="购物"], [aria-label*="商品"], [title*="购物"], [title*="商品"]'),
  ];

  const opened = await clickFirstVisible(page, candidates, "open live commerce panel", ctx);
  if (opened) {
    await waitWithAbort(ctx.config.orderStepDelayMs, ctx);
  }
  return opened;
}

async function ensureAllProductsPanel(page, ctx) {
  await dismissBlockingOverlays(page, ctx);
  if (await hasVisibleProductList(page)) {
    return true;
  }
  if (await hasVisibleAllProductsEntry(page)) {
    return openCommercePanel(page, ctx, true);
  }
  return openCommercePanel(page, ctx);
}

async function scrollLikelyProductLists(page, ctx) {
  ctx.assertRunning();
  return page
    .evaluate(() => {
      const nodes = [
        ...document.querySelectorAll(
          '[role="dialog"], [role="list"], [class*="list" i], [class*="scroll" i], [class*="product" i], [class*="goods" i], [class*="sku" i]',
        ),
        document.scrollingElement,
        document.body,
      ].filter(Boolean);

      let scrolled = 0;
      for (const node of nodes) {
        if (!(node instanceof HTMLElement)) continue;
        if (node.scrollHeight <= node.clientHeight + 20) continue;
        const before = node.scrollTop;
        node.scrollTop = Math.min(
          node.scrollHeight,
          node.scrollTop + Math.max(260, node.clientHeight * 0.85),
        );
        if (node.scrollTop !== before) scrolled += 1;
      }

      window.scrollBy(0, Math.max(260, window.innerHeight * 0.45));
      return scrolled > 0;
    })
    .catch(() => false);
}

async function findAndClickBuyAction(page, container, ctx, label = "buy/order button") {
  const actions = [
    container.getByRole("button", { name: BUY_TEXT_RE }).first(),
    container.getByRole("link", { name: BUY_TEXT_RE }).first(),
    container
      .locator("button, a, [role=button], [class*=button], [class*=btn]")
      .filter({ hasText: BUY_TEXT_RE })
      .first(),
    container
      .locator('[data-e2e*="buy" i], [data-e2e*="cart" i], [data-e2e*="order" i]')
      .first(),
  ];

  if (await clickFirstVisible(page, actions, label, ctx)) {
    return true;
  }

  if (ctx.config.allowReservationClick) {
    const reservationActions = [
      container.getByRole("button", { name: RESERVATION_TEXT_RE }),
      container.getByRole("link", { name: RESERVATION_TEXT_RE }),
      container.locator("button, a, [role=button]").filter({ hasText: RESERVATION_TEXT_RE }),
    ];
    if (await clickFirstVisible(page, reservationActions, "reservation/reminder button", ctx)) {
      return true;
    }
  }

  return false;
}

async function findTightProductContainers(node, ctx) {
  const candidates = [
    node,
    node.locator("xpath=ancestor-or-self::*[self::div or self::li or self::section][1]"),
    node.locator("xpath=ancestor::*[self::div or self::li or self::section][2]"),
    node.locator("xpath=ancestor::*[self::div or self::li or self::section][3]"),
    node.locator("xpath=ancestor::*[self::div or self::li or self::section][4]"),
  ];

  const matched = [];
  const seen = new Set();
  for (const candidate of candidates) {
    ctx.assertRunning();
    const text = compactText(await textOf(candidate), 700);
    if (!isLikelyListProductCardText(text, ctx.config)) {
      continue;
    }

    const normalized = normalizeText(text);
    if (seen.has(normalized)) continue;
    seen.add(normalized);
    matched.push({ locator: candidate, text });
  }

  matched.sort((a, b) => a.text.length - b.text.length);
  return matched.slice(0, 3);
}

async function closePaymentLayer(page, ctx) {
  const candidates = [
    page.getByRole("button", { name: CLOSE_PAYMENT_TEXT_RE }),
    page.getByRole("link", { name: CLOSE_PAYMENT_TEXT_RE }),
    page.locator(
      [
        'button[aria-label*="关闭"]',
        'button[title*="关闭"]',
        '[role=button][aria-label*="关闭"]',
        '[role=button][title*="关闭"]',
        "button",
        "[role=button]",
      ].join(", "),
    ).filter({ hasText: CLOSE_PAYMENT_TEXT_RE }),
  ];

  if (await clickFirstVisible(page, candidates, "close payment layer", ctx)) {
    await waitWithAbort(ctx.config.orderStepDelayMs, ctx);
    return true;
  }

  if (
    await clickTopRightIcon(
      page,
      [
        'svg[class*="close" i]',
        'svg[class*="qk3" i]',
        '[class*="close" i] svg',
        '[class*="Close" i] svg',
        '[role=dialog] svg',
      ],
      "close payment layer icon",
      ctx,
    )
  ) {
    await waitWithAbort(ctx.config.orderStepDelayMs, ctx);
    return true;
  }

  if (!ctx.config.dryRun) {
    await page.keyboard.press("Escape").catch(() => {});
    await waitWithAbort(ctx.config.orderStepDelayMs, ctx);
  }
  return false;
}

async function confirmAbandonPayment(page, ctx) {
  const candidates = [
    page.getByRole("button", { name: ABANDON_PAYMENT_TEXT_RE }),
    page.getByRole("link", { name: ABANDON_PAYMENT_TEXT_RE }),
    page.locator("button, a, [role=button], [class*=button], [class*=btn]").filter({
      hasText: ABANDON_PAYMENT_TEXT_RE,
    }),
    page.getByText(ABANDON_PAYMENT_TEXT_RE),
  ];

  if (await clickFirstVisible(page, candidates, "confirm abandon payment", ctx)) {
    ctx.state.abandonClicks += 1;
    await waitWithAbort(ctx.config.orderStepDelayMs, ctx);
    return true;
  }
  return false;
}

async function submitPaymentThenAbandon(page, ctx) {
  const paymentButtonVisible =
    (await hasVisibleTextElement(page, PAYMENT_AMOUNT_TEXT_RE, ctx)) ||
    (await page
      .locator("button, a, [role=button], [class*=button], [class*=btn]")
      .filter({ hasText: PAYMENT_SUBMIT_TEXT_RE })
      .first()
      .isVisible({ timeout: 160 })
      .catch(() => false));

  if (!paymentButtonVisible) return false;

  if (!ctx.config.submitPaymentAndAbandon) {
    ctx.log("info", "检测到支付页，已按配置停在支付前");
    return true;
  }

  ctx.log("info", "检测到支付页，开始点击支付按钮以提交订单");
  const paymentCandidates = [
    page.getByRole("button", { name: PAYMENT_SUBMIT_TEXT_RE }),
    page.getByRole("link", { name: PAYMENT_SUBMIT_TEXT_RE }),
    page.locator("button, a, [role=button], [class*=button], [class*=btn]").filter({
      hasText: PAYMENT_SUBMIT_TEXT_RE,
    }),
  ];

  const clickedPayment =
    (await clickFirstVisible(page, paymentCandidates, "submit payment / create order", ctx)) ||
    (await clickVisibleTextElement(
      page,
      PAYMENT_AMOUNT_TEXT_RE,
      "submit payment / create order",
      ctx,
    ));
  if (!clickedPayment) {
    ctx.log("warn", "已进入支付页，但未找到可点击的支付按钮");
    return false;
  }

  ctx.state.paymentClicks += 1;
  ctx.state.orderSubmitted = true;
  await waitWithAbort(ctx.config.orderStepDelayMs, ctx);

  const closed = await closePaymentLayer(page, ctx);
  const abandoned = await confirmAbandonPayment(page, ctx);

  if (closed || abandoned) {
    ctx.log("info", "订单已提交到支付态，并已执行放弃支付确认流程");
    return true;
  }

  ctx.log("warn", "订单已进入支付态，但未确认找到关闭/放弃支付入口");
  return true;
}

async function advanceOrderFlow(page, ctx) {
  if (ctx.config.dryRun || ctx.config.maxOrderSteps <= 0) {
    return true;
  }

  for (let step = 1; step <= ctx.config.maxOrderSteps; step += 1) {
    ctx.assertRunning();
    if (await submitPaymentThenAbandon(page, ctx)) return true;
    await dismissBlockingOverlays(page, ctx);

    const candidates = [
      page.getByRole("button", { name: ORDER_STEP_TEXT_RE }),
      page.getByRole("link", { name: ORDER_STEP_TEXT_RE }),
      page.locator("button, a, [role=button], [class*=button], [class*=btn]").filter({
        hasText: ORDER_STEP_TEXT_RE,
      }),
    ];

    const advanced = await clickFirstVisible(page, candidates, `order flow step ${step}`, ctx);
    if (!advanced) {
      ctx.log("warn", `下单流程第 ${step} 步未找到可继续按钮`);
      return false;
    }

    await waitWithAbort(ctx.config.orderStepDelayMs, ctx);
  }

  return submitPaymentThenAbandon(page, ctx);
}

async function scanDomAndOrder(page, ctx) {
  ctx.state.scans += 1;
  if (await submitPaymentThenAbandon(page, ctx)) return true;
  await ensureAllProductsPanel(page, ctx);

  const tokens = keywordTokens(ctx.config);
  const tokenRegex = new RegExp(tokens.map(escapeRegex).join("|"), "i");
  const productNodes = page
    .locator(
      [
        "li",
        '[role="dialog"] *',
        '[class*="product" i]',
        '[class*="goods" i]',
        '[class*="sku" i]',
        '[data-e2e*="product" i]',
        '[data-e2e*="goods" i]',
      ].join(", "),
    )
    .filter({ hasText: tokenRegex })
    .or(page.locator('[data-e2e="promotion-title"]').filter({ hasText: tokenRegex }).locator("xpath=ancestor::li[1]"))
    .or(page.locator('[data-e2e="shop-buyBtn"]').locator("xpath=ancestor::li[1]").filter({ hasText: tokenRegex }))
    .or(page.locator('[data-e2e="price-Area"]').locator("xpath=ancestor::li[1]").filter({ hasText: tokenRegex }));

  const total = await productNodes.count();
  ctx.state.candidateNodes = total;
  const count = Math.min(total, 120);
  if (ctx.state.scans % 10 === 1) {
    ctx.log("info", `扫描商品候选节点：${total} 个`);
  }

  for (let index = 0; index < count; index += 1) {
    ctx.assertRunning();
    const node = productNodes.nth(index);
    let text = await textOf(node, 350);
    if (!text) continue;

    if (!isLikelyListProductCardText(text, ctx.config)) continue;

    ctx.state.matchedProducts += 1;
    ctx.state.lastMatchText = compactText(text, 500);

    const actionState = productActionState(text);
    if (actionState === "waiting") {
      if (ctx.state.scans % 10 === 1) {
        ctx.log("info", `目标商品仍在等待开售：${compactText(text, 180)}`);
      }
      continue;
    }

    if (actionState !== "ready" || productUnavailable(text) || !priceMatches(text, ctx.config)) {
      ctx.state.unavailableMatches += 1;
      ctx.log("warn", `命中商品但状态或价格不满足：${compactText(text, 220)}`);
      continue;
    }

    const containers = await findTightProductContainers(node, ctx);
    if (containers.length === 0) continue;

    for (const container of containers) {
      if (!isLikelyListProductCardText(container.text, ctx.config)) continue;
      ctx.state.lastMatchText = container.text;
      ctx.log(
        "info",
        `命中目标商品且可购买（${productMatchReason(container.text, ctx.config)}）：${compactText(
          container.text,
          300,
        )}`,
      );
      if (await findAndClickBuyAction(page, container.locator, ctx, "target product buy button")) {
        await waitWithAbort(ctx.config.postBuyDelayMs, ctx);
        return advanceOrderFlow(page, ctx);
      }
    }
  }

  await scrollLikelyProductLists(page, ctx);
  ctx.log("info", "未在目标商品卡片内找到可点击购买按钮，跳过页面级兜底点击");

  return false;
}

async function waitForScheduleWindow(ctx, clockOffsetMs) {
  const config = ctx.config;
  if (config.scheduleWindows.length === 0) {
    ctx.log("info", "未配置监控时间点，立即进入监控窗口");
    return Date.now() + config.monitorDurationMs;
  }

  while (true) {
    ctx.assertRunning();
    const activeEnd = activeWindowEndLocalMs(config, clockOffsetMs);
    if (activeEnd) {
      return activeEnd;
    }

    const nextOpen = nextWindowOpenLocalMs(config, clockOffsetMs);
    const wakeAt = Math.max(Date.now(), nextOpen - config.prewarmMs);
    const seconds = Math.max(0, Math.ceil((wakeAt - Date.now()) / 1000));
    ctx.log("info", `等待下一个监控窗口，约 ${seconds}s 后预热`);
    await preciseWaitUntil(wakeAt, ctx);
    ctx.log("info", "已进入预热阶段，开始高频监控");
    return nextOpen + config.monitorDurationMs;
  }
}

async function saveDiagnostics(page, ctx, reason) {
  if (!ctx.config.saveDiagnostics) return;
  const now = Date.now();
  if (now - ctx.state.lastDiagnosticAt < 5_000) return;
  ctx.state.lastDiagnosticAt = now;

  try {
    fs.mkdirSync(ctx.config.diagnosticsDir, { recursive: true });
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const safeReason = String(reason || "snapshot").replace(/[^\w-]+/g, "_").slice(0, 40);
    const base = path.join(ctx.config.diagnosticsDir, `${stamp}_${safeReason}`);
    await page.screenshot({ path: `${base}.png`, fullPage: true, timeout: 5_000 });
    fs.writeFileSync(`${base}.html`, await page.content(), "utf8");
    fs.writeFileSync(
      `${base}.json`,
      JSON.stringify(
        {
          reason,
          url: page.url(),
          state: ctx.state,
          config: {
            mode: ctx.config.mode,
            productName: ctx.config.productName,
            productId: ctx.config.productId,
            targetPrice: ctx.config.targetPrice,
            strictPriceMatch: ctx.config.strictPriceMatch,
          },
        },
        null,
        2,
      ),
      "utf8",
    );
    ctx.log("info", `已保存诊断快照：${base}.png`);
  } catch (error) {
    ctx.log("warn", `保存诊断快照失败：${error.message}`);
  }
}

async function setQuantity(page, quantity, ctx) {
  if (quantity <= 1) return;

  const input = page
    .locator(
      'input[type="number"], input[name*="quantity" i], input[name*="count" i], input[class*="quantity" i], input[class*="count" i]',
    )
    .first();

  if ((await input.count()) > 0 && (await input.isVisible({ timeout: 500 }).catch(() => false))) {
    if (ctx.config.dryRun) {
      ctx.log("info", `DRY_RUN 将设置购买数量为 ${quantity}`);
      return;
    }
    await input.fill(String(quantity), { timeout: 1_500 });
    ctx.log("info", `已设置购买数量：${quantity}`);
    return;
  }

  const plusButton = page
    .locator('button, [role=button], a')
    .filter({ hasText: /^\s*(\+|加|增加)\s*$/ })
    .first();

  if ((await plusButton.count()) === 0) {
    ctx.log("warn", "未找到数量输入控件，继续使用页面默认数量");
    return;
  }

  if (ctx.config.dryRun) {
    ctx.log("info", `DRY_RUN 将点击数量增加按钮 ${quantity - 1} 次`);
    return;
  }

  for (let index = 1; index < quantity; index += 1) {
    ctx.assertRunning();
    await plusButton.click({ timeout: 1_500 });
    await waitWithAbort(80, ctx);
  }
  ctx.log("info", `已通过增加按钮设置购买数量：${quantity}`);
}

async function runFlashSale(page, ctx, clockOffsetMs) {
  const config = ctx.config;
  ctx.log("info", `打开直播间：${config.liveUrl}`);
  await page.goto(config.liveUrl, { waitUntil: "domcontentloaded", timeout: 60_000 });
  ctx.log("info", "页面已打开，如需登录请在浏览器中完成登录");
  await dismissBlockingOverlays(page, ctx);
  await openCommercePanel(page, ctx, true);

  let ordered = false;
  while (!ordered) {
    ctx.assertRunning();
    const windowEndsAt = await waitForScheduleWindow(ctx, clockOffsetMs);
    ctx.log("info", "监控窗口已激活，开始扫描商品");

    while (Date.now() < windowEndsAt && !ordered) {
      ctx.assertRunning();
      try {
        ordered = await scanDomAndOrder(page, ctx);
      } catch (error) {
        ctx.log("warn", `扫描异常：${error.message}`);
      }

      if (!ordered) {
        await waitWithAbort(randomizedDelayMs(config), ctx);
      }
    }

    if (!ordered) {
      ctx.log(
        "info",
        [
          "当前窗口结束，未完成目标动作。",
          `扫描次数=${ctx.state.scans}`,
          `候选节点=${ctx.state.candidateNodes}`,
          `命中商品=${ctx.state.matchedProducts}`,
          `不可购买=${ctx.state.unavailableMatches}`,
          `点击尝试=${ctx.state.clickAttempts}`,
          `支付点击=${ctx.state.paymentClicks}`,
          `订单提交=${ctx.state.orderSubmitted ? "是" : "否"}`,
        ].join(" "),
      );
      await saveDiagnostics(page, ctx, "window-ended-no-order");
      await waitWithAbort(1_000, ctx);
    }
  }

  ctx.log("info", "已到达下单流程节点");
}

async function runBatchBuy(page, ctx) {
  const config = ctx.config;
  if (!config.productUrl) {
    throw new Error("批量购买模式需要填写现有商品链接");
  }

  for (let batch = 1; batch <= config.buyTimes; batch += 1) {
    ctx.assertRunning();
    ctx.log("info", `批次 ${batch}/${config.buyTimes}：打开商品链接`);
    await page.goto(config.productUrl, { waitUntil: "domcontentloaded", timeout: 60_000 });
    await waitWithAbort(500, ctx);

    await dismissBlockingOverlays(page, ctx);
    await setQuantity(page, config.buyQuantity, ctx);

    const clicked = await findAndClickBuyAction(page, page.locator("body"), ctx);
    if (!clicked) {
      ctx.log("warn", `批次 ${batch} 未找到购买按钮`);
    } else {
      await waitWithAbort(ctx.config.postBuyDelayMs, ctx);
      await advanceOrderFlow(page, ctx);
    }

    if (batch < config.buyTimes) {
      await waitWithAbort(randomizedDelayMs(config), ctx);
    }
  }

  ctx.log("info", "批量购买流程已执行完毕");
}

export async function runAutomation(rawConfig = {}, hooks = {}) {
  const config = buildConfig(rawConfig);
  const ctx = createRunContext(config, hooks);
  let context;

  ctx.log("info", `运行模式：${config.mode === "batch" ? "批量购买" : "抢商品监控"}`);
  ctx.log("info", `DRY_RUN：${config.dryRun ? "开启" : "关闭"}`);
  ctx.log("info", `商品关键词：${config.productName || "(未填写)"}`);
  if (config.productId) ctx.log("info", `商品编号：${config.productId}`);
  if (config.targetPrice > 0) {
    ctx.log(
      "info",
      `目标价格：${config.targetPrice}（${config.strictPriceMatch ? "严格匹配" : "仅作为参考"}）`,
    );
  }

  try {
    context = await launchContext(ctx);
    const page = context.pages()[0] || (await context.newPage());
    page.setDefaultTimeout(2_000);
    await captureNetworkEvidence(page, ctx);

    const clockOffsetMs =
      config.mode === "flash" ? await calibrateClock(context, config.liveUrl, ctx) : 0;

    if (config.mode === "batch") {
      await runBatchBuy(page, ctx);
    } else {
      await runFlashSale(page, ctx, clockOffsetMs);
    }

    return { status: "completed" };
  } catch (error) {
    if (error instanceof GracefulStop || error.name === "AbortError") {
      ctx.log("warn", error.message || "任务已停止");
      return { status: "stopped", reason: error.message };
    }
    ctx.log("error", `任务失败：${error.stack || error.message}`);
    throw error;
  } finally {
    if (context && config.closeBrowserOnFinish) {
      await context.close().catch(() => {});
      ctx.log("info", "浏览器上下文已关闭");
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  runAutomation(configFromEnv()).catch(() => {
    process.exitCode = 1;
  });
}
