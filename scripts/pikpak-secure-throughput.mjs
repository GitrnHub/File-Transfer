import { generateKeyPairSync, privateDecrypt, createDecipheriv, constants } from 'node:crypto';
import { performance } from 'node:perf_hooks';

const repo = process.env.GITHUB_REPOSITORY;
const owner = process.env.GITHUB_REPOSITORY_OWNER;
const issue = Number(process.env.ISSUE_NUMBER);
const token = process.env.GH_TOKEN;
if (!repo || !owner || !issue || !token) throw new Error('Missing GitHub Actions context');
const prefix = '/repos/' + repo + '/issues/' + issue + '/comments';
const markerPublic = 'PIKPAK_BENCH_PUBKEY_V1\n';
const markerCipher = 'PIKPAK_BENCH_CIPHERTEXT_V1\n';
const markerResult = 'PIKPAK_BENCH_RESULT_V1\n';

async function gh(method, route, body) {
  const res = await fetch('https://api.github.com' + route, {
    method,
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json'
    },
    body: body ? JSON.stringify(body) : undefined
  });
  if (!res.ok) throw new Error('GitHub API response ' + res.status);
  return res.json();
}
function safeUrl(url) {
  const parsed = new URL(url);
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('Invalid URL scheme');
  if (!/^[a-z0-9-]+\.mypikpak\.com$/i.test(parsed.hostname)) throw new Error('URL must use the PikPak CDN');
  if (parsed.username || parsed.password) throw new Error('Embedded credentials not allowed');
  return url;
}
async function post(body) { return gh('POST', prefix, { body }); }
function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
function cleanError(e) { return e && typeof e.name === 'string' ? e.name : 'UnknownError'; }

async function waitEncryptedRequest() {
  for (let i = 0; i < 65; i++) {
    const comments = await gh('GET', prefix + '?per_page=100');
    for (const comment of comments) {
      if (comment.user?.login !== owner) continue;
      if (typeof comment.body !== 'string' || !comment.body.startsWith(markerCipher)) continue;
      try { return JSON.parse(comment.body.slice(markerCipher.length)); } catch { continue; }
    }
    await sleep(7000);
  }
  throw new Error('Encrypted request timeout');
}

async function probeSize(url) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 15000);
  try {
    const t0 = performance.now();
    const r = await fetch(url, { headers: { Range: 'bytes=0-0', 'User-Agent': 'Mozilla/5.0' }, signal: ctl.signal });
    const seconds = (performance.now() - t0) / 1000;
    const match = (r.headers.get('content-range') || '').match(/\/(\d+)$/);
    const result = { status: r.status, size_bytes: match ? Number(match[1]) : null, header_seconds: Number(seconds.toFixed(3)) };
    try { await r.body?.cancel(); } catch {}
    return result;
  } finally { clearTimeout(timer); }
}

async function measureRange(url, start, end, deadline, maxBytes) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 29000);
  let reader;
  const t0 = performance.now();
  let first = null, last = t0, bytes = 0, status = null;
  let error = null;
  try {
    const r = await fetch(url, {
      headers: { Range: 'bytes=' + start + '-' + end, 'User-Agent': 'Mozilla/5.0' },
      signal: ctrl.signal,
      redirect: 'follow'
    });
    status = r.status;
    if (![200,206].includes(r.status) || !r.body) return { status, bytes, first, last: performance.now(), error: 'HTTP_' + status };
    reader = r.body.getReader();
    while (performance.now() < deadline && bytes < maxBytes) {
      const chunk = await reader.read();
      if (chunk.done) break;
      const now = performance.now();
      if (first === null) first = now;
      bytes += chunk.value.byteLength;
      last = now;
    }
  } catch (e) {
    error = cleanError(e);
  } finally {
    ctrl.abort();
    clearTimeout(timer);
    try { await reader?.cancel(); } catch {}
  }
  return { status, bytes, first, last, error };
}

async function benchmark(url, size, connections, durationMs) {
  const started = performance.now();
  const deadline = started + durationMs;
  const stride = size && size > 0 ? Math.floor(size / connections) : 128 * 1024 * 1024;
  const tasks = [];
  for (let i = 0; i < connections; i++) {
    const start = i * stride;
    const end = size && size > 0 ? Math.min(size - 1, start + stride - 1) : start + stride - 1;
    if (start >= end) continue;
    tasks.push(measureRange(url, start, end, deadline, 96 * 1024 * 1024));
  }
  const result = await Promise.all(tasks);
  const finished = performance.now();
  const firsts = result.map(r => r.first).filter(x => x !== null);
  const first = firsts.length ? Math.min(...firsts) : null;
  const bytes = result.reduce((n, x) => n + x.bytes, 0);
  const bodySeconds = first !== null ? (finished - first) / 1000 : null;
  const overallSeconds = (finished - started) / 1000;
  return {
    connections_requested: connections,
    connections_active: result.filter(x => x.bytes > 0).length,
    http_statuses: result.map(x => x.status),
    error_types: result.map(x => x.error).filter(Boolean),
    bytes,
    sample_mib: Number((bytes / 1048576).toFixed(3)),
    overall_seconds: Number(overallSeconds.toFixed(3)),
    body_seconds: bodySeconds === null ? null : Number(bodySeconds.toFixed(3)),
    body_mib_per_s: bodySeconds ? Number((bytes / 1048576 / bodySeconds).toFixed(3)) : null,
    body_mbps: bodySeconds ? Number((bytes * 8 / 1000000 / bodySeconds).toFixed(3)) : null
  };
}

async function verifyPat() {
  const raw = (process.env.PIKPAK_PAT || '').trim().replace(/^Bearer\s+/i, '');
  if (!raw) return { mcp_http_status: null, upload: 'blocked_missing_pat' };
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch('https://api-open.mypikpak.com/mcp', {
      method: 'POST',
      signal: controller.signal,
      headers: {
        'Authorization': 'Bearer ' + raw,
        'Accept': 'application/json, text/event-stream',
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        jsonrpc: '2.0', id: 1, method: 'initialize',
        params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'gha-pikpak-speed-check', version: '1.0.0' } }
      })
    });
    return { mcp_http_status: response.status, upload: response.ok ? 'authentication_available_but_upload_not_yet_tested' : 'blocked_authentication' };
  } catch (e) {
    return { mcp_http_status: null, upload: 'blocked_' + cleanError(e) };
  } finally { clearTimeout(timeout); }
}

let final = { version: 1, provider: 'GitHub-hosted ubuntu-latest', date: new Date().toISOString(), download: {}, upload: {} };
try {
  const keys = generateKeyPairSync('rsa', {
    modulusLength: 3072,
    publicKeyEncoding: { type: 'spki', format: 'pem' },
    privateKeyEncoding: { type: 'pkcs8', format: 'pem' }
  });
  await post(markerPublic + JSON.stringify({
    algorithm: 'RSA-OAEP-SHA256 / AES-256-GCM',
    pem_base64: Buffer.from(keys.publicKey).toString('base64')
  }));
  console.log('One-time encryption public key posted. Waiting for owner-only encrypted request.');
  const msg = await waitEncryptedRequest();
  const aesKey = privateDecrypt(
    { key: keys.privateKey, padding: constants.RSA_PKCS1_OAEP_PADDING, oaepHash: 'sha256' },
    Buffer.from(msg.wrapped_key, 'base64')
  );
  const decipher = createDecipheriv('aes-256-gcm', aesKey, Buffer.from(msg.nonce, 'base64'));
  decipher.setAuthTag(Buffer.from(msg.tag, 'base64'));
  const decrypted = Buffer.concat([decipher.update(Buffer.from(msg.ciphertext, 'base64')), decipher.final()]);
  const request = JSON.parse(decrypted.toString('utf8'));
  const url = safeUrl(request.url);
  console.log('Encrypted download URL decrypted in runner memory; never written to Git or logs.');
  const probe = await probeSize(url);
  final.download.probe = probe;
  if (probe.status !== 206 || !probe.size_bytes) throw new Error('Range probe failed');
  final.download.single = await benchmark(url, probe.size_bytes, 1, 15000);
  final.download.parallel8 = await benchmark(url, probe.size_bytes, 8, 15000);
  final.download.status = 'completed';
} catch (e) {
  final.download.status = 'failed';
  final.download.error_type = cleanError(e);
  final.download.error_label = e.message === 'Encrypted request timeout' ? 'encrypted_request_timeout' : 'no_verified_download';
  console.log('Download testing unsuccessful: ' + final.download.error_label);
}
final.upload = await verifyPat();
await post(markerResult + JSON.stringify(final));
console.log(markerResult + JSON.stringify(final));
if (final.download.status !== 'completed') process.exitCode = 1;
