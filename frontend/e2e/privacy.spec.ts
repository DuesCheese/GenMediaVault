import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

test('private upload → batch publish → anonymous read-only share → revoke → superadmin transfer', async ({ browser, baseURL }) => {
  test.setTimeout(120000)
  const password = process.env.GMV_E2E_PASSWORD || JSON.parse(fs.readFileSync(path.resolve('../data/test-environment.json'), 'utf8')).admin_password
  const admin = await browser.newContext({ baseURL }), alice = await browser.newContext({ baseURL }), bob = await browser.newContext({ baseURL }), guest = await browser.newContext({ baseURL })
  try {
    const login = await admin.request.post('/api/v1/auth/login', { data: { username: 'admin', password } })
    const session = await login.json()
    expect(session.user.role).toBe('superadmin')
    const suffix = Date.now(), memberPassword = 'synthetic-test-password'
    const members: Record<string, { id: string; username: string }> = {}
    for (const name of ['alice', 'bob']) {
      const response = await admin.request.post('/api/v1/users', { headers: { 'X-CSRF-Token': session.csrf }, data: { username: `${name}${suffix}`, password: memberPassword, role: 'user' } })
      expect(response.status()).toBe(201)
      members[name] = await response.json()
    }
    await alice.request.post('/api/v1/auth/login', { data: { username: members.alice.username, password: memberPassword } })
    await bob.request.post('/api/v1/auth/login', { data: { username: members.bob.username, password: memberPassword } })
    const a = await alice.newPage(), b = await bob.newPage(), g = await guest.newPage(), s = await admin.newPage()
    const errors: string[] = []
    for (const page of [a, b, g, s]) page.on('pageerror', error => errors.push(error.message))
    await a.goto('/')
    await a.getByRole('button', { name: '导入文件', exact: true }).click()
    await a.getByLabel('选择上传文件', { exact: true }).setInputFiles(path.resolve('../backend/tests/fixtures/plain.webp'))
    await a.getByRole('button', { name: '提交导入', exact: true }).click()
    await expect(a.getByRole('status')).toContainText('导入任务已提交')
    await a.getByRole('button', { name: '我的私人空间', exact: true }).click()
    await expect(a.getByRole('button', { name: '查看 plain.webp', exact: true })).toBeVisible({ timeout: 20000 })
    await expect(a.locator('.asset-caption')).toContainText(`上传者 ${members.alice.username} · 私人`)
    await b.goto('/?view=public')
    await expect(b.getByRole('button', { name: '查看 plain.webp', exact: true })).toHaveCount(0)
    await a.getByRole('button', { name: '选择 plain.webp', exact: true }).click()
    await a.getByRole('button', { name: '公开', exact: true }).click()
    await expect(a.locator('.asset-caption')).toContainText('公开')
    await b.reload()
    await expect(b.getByRole('button', { name: '查看 plain.webp', exact: true })).toBeVisible()
    await a.getByRole('button', { name: '查看 plain.webp', exact: true }).click()
    await a.getByLabel('分享有效期').selectOption('1')
    await a.getByRole('button', { name: '生成分享图链', exact: true }).click()
    const share = a.getByLabel('分享图链', { exact: true })
    await expect(share).toBeVisible()
    await g.goto(await share.inputValue())
    await expect(g.getByRole('heading', { name: 'plain.webp', exact: true })).toBeVisible()
    await expect(g.getByRole('link', { name: '下载原图片', exact: true })).toBeVisible()
    await expect(g.locator('input,textarea,select')).toHaveCount(0)
    await expect(g.getByRole('button', { name: '收藏', exact: true })).toHaveCount(0)
    await expect(g.getByRole('button', { name: '复制完整参数', exact: true })).toBeVisible()
    await g.screenshot({ path: '../test-artifacts/v06-share.png', fullPage: true })
    await a.getByRole('dialog').getByRole('button', { name: '取消公开', exact: true }).click()
    await g.reload()
    await expect(g.getByRole('heading', { name: '分享链接不可用', exact: true })).toBeVisible()
    await b.reload()
    await expect(b.getByRole('button', { name: '查看 plain.webp', exact: true })).toHaveCount(0)
    await s.goto('/?view=all_private')
    await s.getByLabel('筛选上传者').selectOption(members.alice.id)
    await s.getByRole('button', { name: '查看 plain.webp', exact: true }).click()
    await s.getByLabel('修改上传者', { exact: true }).selectOption(members.bob.id)
    await expect(s.getByRole('dialog').locator('.privacy-panel')).toContainText(members.bob.username)
    await a.reload()
    await expect(a.getByRole('dialog').getByRole('alert')).toContainText('无权访问')
    await b.goto('/?view=private')
    await expect(b.getByRole('button', { name: '查看 plain.webp', exact: true })).toBeVisible()
    await s.goto('/?view=settings')
    await expect(s.getByRole('heading', { name: 'GitHub 在线更新', exact: true })).toBeVisible()
    await expect(s.getByRole('button', { name: '检查更新', exact: true })).toBeVisible()
    await s.screenshot({ path: '../test-artifacts/v06-settings.png', fullPage: true })
    expect(errors).toEqual([])
  } finally {
    for (const context of [admin, alice, bob, guest]) await context.close()
  }
})
