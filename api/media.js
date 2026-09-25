const DEFAULT_REFERER = "https://www.evoolipxnyxzq.shop/";

function allowedTarget(value) {
  try {
    const u = new URL(value);
    // Only public HTTP(S) sources; never proxy local/private network targets.
    if (!/^https?:$/.test(u.protocol)) return false;
    const host = u.hostname.toLowerCase();
    if (host === "localhost" || host === "127.0.0.1" || host === "::1") return false;
    if (/^(10|127)\./.test(host) || /^192\.168\./.test(host) || /^172\.(1[6-9]|2\d|3[0-1])\./.test(host)) return false;
    return true;
  } catch { return false; }
}

function setCors(res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Range, Content-Type, If-Range, If-None-Match, If-Modified-Since");
  res.setHeader("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges, Content-Type, ETag, Last-Modified");
}

export default async function handler(req, res) {
  setCors(res);
  if (req.method === "OPTIONS") return res.status(204).end();
  if (!['GET', 'HEAD'].includes(req.method)) return res.status(405).send('Method Not Allowed');

  const target = String(req.query?.url || "");
  if (!allowedTarget(target)) return res.status(400).json({ ok: false, error: "Geçerli bir public http(s) url gerekli" });

  const headers = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/151.0 Mobile Safari/537.36",
    "Referer": String(req.query?.ref || DEFAULT_REFERER),
    "Accept-Encoding": "identity",
  };
  for (const name of ["range", "accept", "if-range", "if-none-match", "if-modified-since"]) {
    if (req.headers[name]) headers[name] = req.headers[name];
  }

  try {
    const upstream = await fetch(target, { method: req.method, headers, redirect: "follow" });
    res.statusCode = upstream.status;
    for (const name of ["content-type", "content-length", "content-range", "etag", "last-modified"]) {
      const value = upstream.headers.get(name);
      if (value) res.setHeader(name, value);
    }
    res.setHeader("Accept-Ranges", "bytes");
    res.setHeader("Cache-Control", req.headers.range ? "no-store" : "public, max-age=300");
    res.setHeader("X-Evoli-Proxy", "vercel-range-v1");
    if (req.method === "HEAD" || !upstream.body) return res.end();

    const reader = upstream.body.getReader();
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        res.write(Buffer.from(value));
      }
    } finally {
      reader.releaseLock();
    }
    res.end();
  } catch (error) {
    if (!res.headersSent) {
      setCors(res);
      res.status(502).json({ ok: false, error: error instanceof Error ? error.message : String(error) });
    } else {
      res.end();
    }
  }
}
