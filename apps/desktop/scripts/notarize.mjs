import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFile } from 'node:child_process'

function run(command, args) {
  return new Promise((resolve, reject) => {
    execFile(command, args, (error, stdout, stderr) => {
      if (error) {
        reject(
          new Error(
            `${command} ${args.join(' ')} failed: ${stderr?.trim() || stdout?.trim() || error.message}`
          )
        )
        return
      }
      resolve({ stdout, stderr })
    })
  })
}

function inlineKeyLooksValid(value) {
  return value.includes('BEGIN PRIVATE KEY') && value.includes('END PRIVATE KEY')
}

function resolveApiKeyPath(rawValue) {
  const value = String(rawValue || '').trim()
  if (!value) return { keyPath: '', cleanup: () => {} }

  if (fs.existsSync(value)) {
    return { keyPath: value, cleanup: () => {} }
  }

  if (!inlineKeyLooksValid(value)) {
    throw new Error('APPLE_API_KEY must be a file path or inline .p8 key content')
  }

  // What lands here is the Apple Developer API private key in the clear, and it
  // stays on disk for the whole `notarytool submit --wait`, which is minutes.
  // The old form concatenated a predictable name (`Date.now()` + pid) into the
  // temp dir and wrote it with the default mode, i.e. world-readable under a
  // typical umask. On macOS `os.tmpdir()` honours the per-user TMPDIR launchd
  // sets (`/var/folders/…`, mode 0700), so that was not the open exposure it
  // looks like — but the moment TMPDIR is unset the fallback is `/tmp`, which
  // is mode 1777. `mkdtempSync` removes both the guessable name and the
  // permissive mode regardless of which directory is in play: the container is
  // created 0700 with random suffix, and 0o600 pins the file itself.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-notary-'))
  const tempPath = path.join(dir, 'key.p8')
  fs.writeFileSync(tempPath, value, { encoding: 'utf8', mode: 0o600 })
  return {
    keyPath: tempPath,
    cleanup: () => {
      try {
        // Remove the directory, not just the key, or the 0700 container leaks.
        fs.rmSync(dir, { recursive: true, force: true })
      } catch {
        // Best-effort cleanup.
      }
    }
  }
}

export default async function notarize(context) {
  const { electronPlatformName, appOutDir, packager } = context
  if (electronPlatformName !== 'darwin') return

  const appName = packager.appInfo.productFilename
  const appPath = path.join(appOutDir, `${appName}.app`)
  if (!fs.existsSync(appPath)) {
    throw new Error(`Cannot notarize missing app bundle: ${appPath}`)
  }

  const profile = String(process.env.APPLE_NOTARY_PROFILE || '').trim()
  if (profile) {
    const zipPath = path.join(appOutDir, `${appName}.zip`)
    await run('ditto', ['-c', '-k', '--sequesterRsrc', '--keepParent', appPath, zipPath])
    await run('xcrun', ['notarytool', 'submit', zipPath, '--keychain-profile', profile, '--wait'])
    await run('xcrun', ['stapler', 'staple', '-v', appPath])
    try {
      fs.rmSync(zipPath, { force: true })
    } catch {
      // Best-effort cleanup.
    }
    return
  }

  const keyId = String(process.env.APPLE_API_KEY_ID || '').trim()
  const issuer = String(process.env.APPLE_API_ISSUER || '').trim()
  const rawApiKey = process.env.APPLE_API_KEY
  if (!rawApiKey || !keyId || !issuer) {
    console.log(
      'Skipping notarization: APPLE_API_KEY, APPLE_API_KEY_ID, and APPLE_API_ISSUER are not fully configured.'
    )
    return
  }

  const { keyPath, cleanup } = resolveApiKeyPath(rawApiKey)
  const zipPath = path.join(appOutDir, `${appName}.zip`)
  try {
    await run('ditto', ['-c', '-k', '--sequesterRsrc', '--keepParent', appPath, zipPath])
    await run('xcrun', ['notarytool', 'submit', zipPath, '--key', keyPath, '--key-id', keyId, '--issuer', issuer, '--wait'])
    await run('xcrun', ['stapler', 'staple', '-v', appPath])
  } finally {
    try {
      fs.rmSync(zipPath, { force: true })
    } catch {
      // Best-effort cleanup.
    }
    cleanup()
  }
}
