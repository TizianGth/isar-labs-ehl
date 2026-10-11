// For a platform operator in the eval world's environment, or the local dev
// stand-in. Never copy the world's token outside its environment. No answer call.
import { readFile } from 'node:fs/promises'
const path = process.argv[2]
if (!path || !process.env.ATIRA_API_URL || !process.env.ATIRA_EVAL_TOKEN) {
  throw new Error('Usage: node probe_node_verify.mjs PRIVATE_LOGIN_JSON; requires eval-world environment')
}
const payload = JSON.parse(await readFile(path, 'utf8'))
const response = await fetch(process.env.ATIRA_API_URL.replace(/\/+$/, '') + '/v1/eval-worlds/verify', {
  method: 'POST',
  headers: { authorization: 'Bearer ' + process.env.ATIRA_EVAL_TOKEN, 'content-type': 'application/json' },
  body: JSON.stringify({ site: payload.site, username: payload.username, password: payload.password }),
  signal: AbortSignal.timeout(8000),
})
const body = await response.json().catch(() => ({}))
let message = typeof body.error?.message === 'string' ? body.error.message : ''
for (const secret of [process.env.ATIRA_EVAL_TOKEN, payload.username, payload.password]) {
  if (typeof secret === 'string' && secret) message = message.replaceAll(secret, '[redacted]')
}
console.log(JSON.stringify({ status: response.status, errorType: body.error?.type ?? null,
  message: message.slice(0, 500), successIdentityKind: body.run_id ? 'run' : body.preview_id ? 'preview' : null }))
