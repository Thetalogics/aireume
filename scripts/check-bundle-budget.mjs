import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

export function checkBundleBudget(manifestPath, limits) {
  const { files } = JSON.parse(readFileSync(manifestPath, 'utf8'))
  return files.filter((file) => file.bytes > (file.initial ? limits.initialBytes : limits.chunkBytes))
    .map((file) => ({
      file: file.file,
      bytes: file.bytes,
      limit: file.initial ? limits.initialBytes : limits.chunkBytes,
    }))
}

const INITIAL_BYTES = 600 * 1024
const CHUNK_BYTES = 600 * 1024

function collect(distDir) {
  const html = readFileSync(join(distDir, 'index.html'), 'utf8')
  const initial = new Set([...html.matchAll(/assets\/([^"'?]+\.js)/g)].map((match) => match[1]))
  const assets = join(distDir, 'assets')
  return {
    files: readdirSync(assets).filter((name) => name.endsWith('.js')).map((name) => ({
      file: name,
      bytes: statSync(join(assets, name)).size,
      initial: initial.has(name),
    })),
  }
}

if (process.argv[1] && process.argv[1].endsWith('check-bundle-budget.mjs')) {
  const distDir = process.argv[2] ?? join(process.cwd(), 'app', 'frontend', 'dist')
  const manifestPath = join(distDir, 'bundle-manifest.json')
  writeFileSync(manifestPath, JSON.stringify(collect(distDir)))
  const violations = checkBundleBudget(manifestPath, { initialBytes: INITIAL_BYTES, chunkBytes: CHUNK_BYTES })
  if (violations.length) {
    for (const violation of violations) {
      console.error(`${violation.file} is ${violation.bytes} bytes; limit is ${violation.limit}`)
    }
    process.exitCode = 1
  } else {
    console.log(`Bundle budget held: initial <= ${INITIAL_BYTES}, chunk <= ${CHUNK_BYTES}`)
  }
}
