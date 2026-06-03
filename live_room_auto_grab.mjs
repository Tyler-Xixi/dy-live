#!/usr/bin/env node

/*
 * CLI entry for the sandbox live-room automation runner.
 *
 * Examples:
 *   node live_room_auto_grab.mjs
 *   MODE=batch PRODUCT_URL=https://example.test/item BUY_QUANTITY=2 BUY_TIMES=3 node live_room_auto_grab.mjs
 */

import { configFromEnv, runAutomation } from "./automation_runner.mjs";

runAutomation(configFromEnv()).catch(() => {
  process.exitCode = 1;
});
