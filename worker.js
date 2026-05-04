// PikDrop CORS Proxy — Cloudflare Worker
// Deploy: https://workers.cloudflare.com/ → New Worker → cole este código → Save & Deploy

addEventListener('fetch', event => {
  event.respondWith(handleRequest(event.request))
})

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
  'Access-Control-Max-Age': '86400',
}

async function handleRequest(request) {
  // Handle preflight
  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 200, headers: CORS_HEADERS })
  }

  const url = new URL(request.url)
  // Expect: https://your-worker.workers.dev/?https://api-drive.mypikpak.com/...
  const target = url.searchParams.get('url') ||
                 decodeURIComponent(url.pathname.slice(1) + url.search.replace(/^\?url=/,''))

  // Security: only allow PikPak domains
  const ALLOWED = ['mypikpak.com', 'user.mypikpak.com', 'api-drive.mypikpak.com']
  let targetUrl
  try {
    targetUrl = new URL(target)
  } catch(e) {
    return new Response('Invalid URL', { status: 400, headers: CORS_HEADERS })
  }
  if (!ALLOWED.some(d => targetUrl.hostname.endsWith(d))) {
    return new Response('Domain not allowed', { status: 403, headers: CORS_HEADERS })
  }

  // Forward request
  const fwdHeaders = new Headers()
  for (const [k, v] of request.headers.entries()) {
    if (!['host','origin','referer','cf-connecting-ip','x-forwarded-for'].includes(k.toLowerCase())) {
      fwdHeaders.set(k, v)
    }
  }

  const body = ['GET','HEAD'].includes(request.method) ? undefined : await request.arrayBuffer()

  const resp = await fetch(target, {
    method: request.method,
    headers: fwdHeaders,
    body: body,
  })

  const outHeaders = new Headers(CORS_HEADERS)
  outHeaders.set('Content-Type', resp.headers.get('Content-Type') || 'application/json')

  return new Response(resp.body, {
    status: resp.status,
    headers: outHeaders,
  })
}
