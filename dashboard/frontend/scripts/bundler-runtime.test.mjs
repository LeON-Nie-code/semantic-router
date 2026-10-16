import assert from 'node:assert/strict'
import { mkdtemp, readdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

// Exercise Rolldown's supported fallback even on CI hosts with native bindings.
process.env.NAPI_RS_FORCE_WASI = 'error'

test('the portable bundler produces deployable frontend assets', async () => {
  const { build } = await import('vite')
  const root = await mkdtemp(join(tmpdir(), 'dashboard-bundler-'))
  try {
    await writeFile(join(root, 'index.html'), '<script type="module" src="/main.js"></script>')
    await writeFile(join(root, 'main.js'), 'document.body.textContent = "portable dashboard";')
    await build({ configFile: false, root, logLevel: 'silent' })
    const assets = await readdir(join(root, 'dist', 'assets'))
    assert.ok(assets.some((name) => name.endsWith('.js')))
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
