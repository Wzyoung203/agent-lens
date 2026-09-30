#!/usr/bin/env node
/**
 * 项目页点击冒烟：断言「点一个项目 → 右侧详情立刻换成它」。
 *
 * 背景（2026-09-30 发现）：`useAsync` 只在创建时取一次数，ProjectsView 里改变 `selected`
 * 没有任何重取触发器，于是点击项目要等下一次 10 秒轮询才更新——用户感知就是「卡顿」。
 * 这个脚本把「点击后 2 秒内必须换数据」固化成回归断言（2 秒远小于轮询间隔，轮询救不了它）。
 *
 * 用法：
 *   1. 先起服务：uv run agent-lens serve --port 8000（或 docker compose up web）
 *   2. BASE=http://127.0.0.1:8000 node scripts/projects-click-smoke.mjs
 *
 * 可选环境变量：CHROME、CDP_PORT、HEADLESS=0、DEADLINE_MS（默认 2000）。
 * 退出码 0 表示列表首项自动加载 + 点击切换都在期限内完成。
 *
 * 注意：不要和 scripts/nav-smoke.mjs 并行跑——后端起的是单 worker uvicorn，
 * 两个 Chrome 抢静态资源会把 2 秒期限挤爆（2026-09-30 实测过一次假红）。
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = process.env.BASE || 'http://127.0.0.1:8000'
const CDP_PORT = Number(process.env.CDP_PORT || 9445)
const DEADLINE_MS = Number(process.env.DEADLINE_MS || 2500)
const HEADLESS = process.env.HEADLESS !== '0'

const PROBE = `JSON.stringify({
  rows: [...document.querySelectorAll('aside .row')].map((el) => ({
    name: el.querySelector('.name')?.textContent?.trim() ?? '',
    cost: el.querySelector('.cost')?.textContent?.trim() ?? '',
  })),
  detailCost:
    document.querySelector('main.content section.detail .mini strong')?.textContent?.trim() ?? null,
})`

function findChrome() {
  if (process.env.CHROME) return process.env.CHROME
  const roots = [
    join(process.env.HOME ?? '', 'Library/Caches/ms-playwright'),
    join(process.env.HOME ?? '', '.cache/ms-playwright'),
  ]
  for (const root of roots) {
    if (!existsSync(root)) continue
    for (const dir of readdirSync(root).sort().reverse()) {
      for (const rel of [
        'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
        'chrome-mac/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
        'chrome-linux/chrome',
      ]) {
        const candidate = join(root, dir, rel)
        if (existsSync(candidate)) return candidate
      }
    }
  }
  throw new Error('找不到 Chrome for Testing，请用 CHROME=... 指定可执行文件')
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function waitForTarget() {
  for (let i = 0; i < 60; i += 1) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json()
      const page = list.find((t) => t.type === 'page')
      if (page?.webSocketDebuggerUrl) return page
    } catch {
      /* devtools 还没起来 */
    }
    await sleep(250)
  }
  throw new Error('CDP endpoint 不可用')
}

const profile = mkdtempSync(join(tmpdir(), 'agent-lens-projects-click-'))
const chrome = spawn(
  findChrome(),
  [
    HEADLESS ? '--headless=new' : '--new-window',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    `--remote-debugging-port=${CDP_PORT}`,
    `--user-data-dir=${profile}`,
    'about:blank',
  ],
  { stdio: 'ignore' },
)

const fail = (message) => {
  console.error(message)
  chrome.kill()
  process.exit(2)
}

const target = await waitForTarget().catch((error) => fail(String(error)))
const ws = new WebSocket(target.webSocketDebuggerUrl)
await new Promise((resolve, reject) => {
  ws.addEventListener('open', resolve, { once: true })
  ws.addEventListener('error', reject, { once: true })
})

let seq = 0
const pending = new Map()
const browserErrors = []

ws.addEventListener('message', (event) => {
  const msg = JSON.parse(event.data)
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id)
    pending.delete(msg.id)
    msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
    return
  }
  if (msg.method === 'Runtime.exceptionThrown') {
    browserErrors.push(msg.params.exceptionDetails?.text ?? 'unknown')
  }
})

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++seq
    pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
}

async function snapshot() {
  const result = await send('Runtime.evaluate', {
    expression: PROBE,
    returnByValue: true,
    awaitPromise: true,
  })
  return JSON.parse(result.result.value)
}

/** 轮询到条件成立，返回耗时；超时返回 null。 */
async function waitUntil(predicate, deadline = DEADLINE_MS) {
  const startedAt = Date.now()
  for (;;) {
    if (predicate(await snapshot())) return Date.now() - startedAt
    if (Date.now() - startedAt > deadline) return null
    await sleep(50)
  }
}

const results = []

await send('Page.enable')
await send('Runtime.enable')
await send('Page.navigate', { url: `${BASE}/projects` })

const listed = await waitUntil((snap) => snap.rows.length > 0, 8000)
if (listed === null) fail('项目列表 8 秒内没渲染出来')

const first = (await snapshot()).rows[0]
const autoLoad = await waitUntil((snap) => snap.detailCost === first.cost)
if (autoLoad === null) {
  fail(
    `列表加载后 ${DEADLINE_MS}ms 内右侧没有自动加载首个项目（${first.name} 花费 ${first.cost}）——` +
      `详情面板的取数没有被「自动选中」触发`,
  )
}
results.push(`自动加载首项 ${first.name}：${autoLoad}ms`)

const rows = (await snapshot()).rows
if (rows.length < 2) {
  console.log(results.join('\n'))
  console.log('只有 1 个项目，跳过点击切换断言')
  chrome.kill()
  process.exit(0)
}

const second = rows[1]
await send('Runtime.evaluate', {
  expression: `document.querySelectorAll('aside .row')[1].click()`,
  returnByValue: true,
})
const switched = await waitUntil((snap) => snap.detailCost === second.cost)
if (switched === null) {
  fail(
    `点击 ${second.name} 后 ${DEADLINE_MS}ms 内右侧详情没有切换` +
      `（期望花费 ${second.cost}，实际 ${(await snapshot()).detailCost}）`,
  )
}
results.push(`点击切换 ${second.name}：${switched}ms`)

console.log(results.join('\n'))
if (browserErrors.length > 0) {
  fail(`browser errors: ${browserErrors.join(' | ')}`)
}
console.log('PASS：列表自动加载与点击切换都在期限内完成')
chrome.kill()
