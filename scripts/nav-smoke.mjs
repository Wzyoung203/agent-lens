#!/usr/bin/env node
/**
 * 前端路由冒烟测试：点击左侧导航后，断言内容区真的重新挂载了。
 *
 * 背景（KI-1，2026-09-28 发现 / 2026-09-29 修复）：`<Transition>` 只能包裹单个根节点，
 * 而 views/*.vue 都是多根片段。当时点击导航后新视图完全不挂载，内容区只剩 `<!---->`，
 * 控制台一句报错都没有——纯靠肉眼很难在看构建产物时发现。
 *
 * 为什么不用 playwright 包：验证语义只需要「点一下 + 读 DOM」，用 CDP 直连
 * Playwright 缓存里的 Chrome for Testing 就够了，不必给这个仓库装 JS 测试栈。
 *
 * 用法：
 *   1. 先起服务：uv run agent-lens serve --port 8000   （或 docker compose up web）
 *   2. 再跑本脚本：BASE=http://127.0.0.1:8000 node scripts/nav-smoke.mjs
 *
 * 可选环境变量：CHROME（浏览器可执行文件路径）、CDP_PORT、HEADLESS=0（有界面调试）。
 * 退出码 0 表示所有步骤都有内容区；非 0 表示出现了 KI-1 那类「导航后空白」。
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = process.env.BASE || 'http://127.0.0.1:8000'
const CDP_PORT = Number(process.env.CDP_PORT || 9333)
const HEADLESS = process.env.HEADLESS !== '0'

const STEPS = [
  ['硬加载 总览', null],
  ['点击 项目', '/projects'],
  ['点击 会话', '/sessions'],
  ['点击 工具调用', '/tools'],
  ['点击 上下文成本', '/context'],
  ['点击 设置', '/settings'],
  ['回到 总览', '/'],
  ['再点 项目', '/projects'],
]

const PROBE = `() => {
  const m = document.querySelector('main.content');
  return {
    children: m ? m.children.length : -1,
    textLen: m ? m.innerText.trim().length : -1,
    html: m ? m.innerHTML.trim().slice(0, 60) : '',
  };
}`

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

const profile = mkdtempSync(join(tmpdir(), 'agent-lens-nav-smoke-'))
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
  if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
    browserErrors.push(msg.params.args.map((a) => a.value ?? a.description).join(' '))
  }
})

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++seq
    pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
}

const evaluate = async (expression) =>
  (await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }))
    .result.value

await send('Page.enable')
await send('Runtime.enable')
await send('Page.navigate', { url: `${BASE}/` })
await sleep(2500)

const broken = []
console.log('步骤 | children | textLen | innerHTML 前 60 字符')
for (const [label, href] of STEPS) {
  if (href !== null) {
    await evaluate(`document.querySelector('a[href="${href}"]')?.click()`)
    await sleep(1200)
  }
  const result = await evaluate(`(${PROBE})()`)
  console.log(`${label} | ${result.children} | ${result.textLen} | ${JSON.stringify(result.html)}`)
  if (result.children < 1 || result.textLen < 1) broken.push(label)
}

console.log(`browser errors: ${browserErrors.length}${browserErrors.length ? ` -> ${browserErrors.join(' || ')}` : ''}`)
ws.close()
chrome.kill()

if (broken.length > 0) {
  console.error(`FAIL：以下步骤内容区空白 -> ${broken.join(', ')}`)
  process.exit(1)
}
console.log('PASS：所有步骤内容区都有内容')
process.exit(0)
