// Runs the reference's highlighter tests.
//
//   npm run test:docs
//
// _highlight.ts is TypeScript that the site imports, and node:test cannot read
// TypeScript on Node 20, so this compiles that one file to a scratch directory
// first and runs the tests against the output. The scratch directory gets its
// own package.json because the app is CommonJS and the emitted module is ESM.

import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const UI = dirname(dirname(fileURLToPath(import.meta.url)))
const OUT = join(UI, '.docs-test-build')
const run = (cmd, args) =>
  execFileSync(cmd, args, { cwd: UI, stdio: 'inherit', shell: process.platform === 'win32' })

rmSync(OUT, { recursive: true, force: true })
// `--moduleResolution bundler` and `--skipLibCheck` because naming one file on
// the command line bypasses tsconfig.json, and the ambient @types in node_modules
// do not resolve under the classic default.
run('npx', ['tsc', 'app/docs/_highlight.ts', '--outDir', '.docs-test-build',
            '--module', 'es2022', '--target', 'es2022', '--strict',
            '--moduleResolution', 'bundler', '--skipLibCheck'])
mkdirSync(OUT, { recursive: true })
writeFileSync(join(OUT, 'package.json'), '{ "type": "module" }\n')
run('node', ['--test', 'app/docs/_highlight.test.mjs'])
