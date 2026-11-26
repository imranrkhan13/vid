export async function api(method, path, body) {
  const r = await fetch('/api/v1' + path, { method, headers: { 'X-VGOH': '1', ...(body ? { 'content-type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined, credentials: 'same-origin' })
  if (!r.ok) { let m = r.statusText; try { const j = await r.json(); m = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail) } catch {} ; const e = new Error(m); e.status = r.status; throw e }
  const ct = r.headers.get('content-type') || ''
  return ct.includes('json') ? r.json() : r.text()
}
export const get = (p) => api('GET', p)
export const post = (p, b) => api('POST', p, b || {})
export const patch = (p, b) => api('PATCH', p, b)
export const put = (p, b) => api('PUT', p, b)
export const del = (p) => api('DELETE', p)
