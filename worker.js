const DEFAULT_REFERER = "https://www.evoolipxnyxzq.shop/";
const ALLOWED_METHODS = "GET, HEAD, OPTIONS";
const EXPOSED = "Content-Length, Content-Range, Accept-Ranges, Content-Type, ETag, Last-Modified";

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": ALLOWED_METHODS,
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Expose-Headers": EXPOSED,
    "Vary": "Origin, Range",
  };
}

function isHttpUrl(value) {
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

function contentTypeFor(url) {
  const path = new URL(url).pathname.toLowerCase();
  if (path.endsWith(".m3u8") || path.endsWith(".m3u")) return "application/vnd.apple.mpegurl";
  if (path.endsWith(".mp4") || path.endsWith(".m4v")) return "video/mp4";
  if (path.endsWith(".webm")) return "video/webm";
  if (path.endsWith(".mov")) return "video/quicktime";
  if (/\.(jpe?g)$/.test(path)) return "image/jpeg";
  if (path.endsWith(".png")) return "image/png";
  if (path.endsWith(".webp")) return "image/webp";
  return "application/octet-stream";
}

function rewritePlaylist(text, playlistUrl, workerUrl) {
  const rewrite = (value) => {
    if (!value || value.startsWith("#")) return value;
    let absolute;
    try { absolute = new URL(value, playlistUrl).href; } catch { return value; }
    return `${workerUrl}?url=${encodeURIComponent(absolute)}`;
  };
  return text.split(/\r?\n/).map((line) => {
    let out = line;
    out = out.replace(/URI="([^"]+)"/g, (_m, uri) => `URI="${rewrite(uri)}"`);
    if (!out.startsWith("#")) out = rewrite(out);
    return out;
  }).join("\n");
}

export default {
  async fetch(request) {
    const cors = corsHeaders();
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: { ...cors, "Access-Control-Max-Age": "86400" } });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", { status: 405, headers: cors });
    }

    const requestUrl = new URL(request.url);
    const target = requestUrl.searchParams.get("url");
    if (!target || !isHttpUrl(target)) {
      return new Response("Geçerli bir ?url= parametresi gerekli", { status: 400, headers: cors });
    }

    const upstreamHeaders = new Headers({
      "User-Agent": "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/151.0 Mobile Safari/537.36",
      "Referer": DEFAULT_REFERER,
      "Accept-Encoding": "identity",
    });
    for (const name of ["Range", "Accept", "If-Range", "If-None-Match", "If-Modified-Since"]) {
      const value = request.headers.get(name);
      if (value) upstreamHeaders.set(name, value);
    }

    try {
      const upstream = await fetch(target, {
        method: request.method,
        headers: upstreamHeaders,
        redirect: "follow",
        // Never cache a partial response as if it were a complete media file.
        cf: { cacheEverything: false, cacheTtl: 0 },
      });

      const responseHeaders = new Headers(cors);
      for (const name of ["Content-Length", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified", "Cache-Control"]) {
        const value = upstream.headers.get(name);
        if (value) responseHeaders.set(name, value);
      }
      let contentType = upstream.headers.get("Content-Type") || contentTypeFor(upstream.url || target);
      responseHeaders.set("Content-Type", contentType);
      responseHeaders.set("X-Evoli-Worker", "range-v3");
      // This proxy serves byte-addressable media. Set it even when the CDN
      // omitted the header, so the browser can request MP4 tail metadata.
      // Without it, browsers cannot request the tail of an MP4 where the moov
      // metadata may live, so the video remains stuck at readyState 0.
      responseHeaders.set("Accept-Ranges", "bytes");
      responseHeaders.set("Cache-Control", request.headers.has("Range") ? "no-store" : "public, max-age=300");

      const isPlaylist = /mpegurl|vnd\.apple\.mpegurl/i.test(contentType) || /\.m3u8?(?:$|\?)/i.test(upstream.url || target);
      if (isPlaylist && request.method === "GET" && upstream.ok) {
        const playlist = await upstream.text();
        responseHeaders.set("Content-Type", "application/vnd.apple.mpegurl; charset=utf-8");
        responseHeaders.delete("Content-Length");
        responseHeaders.set("Cache-Control", "no-store");
        return new Response(rewritePlaylist(playlist, upstream.url || target, requestUrl.origin + requestUrl.pathname), {
          status: upstream.status,
          headers: responseHeaders,
        });
      }

      return new Response(request.method === "HEAD" ? null : upstream.body, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers: responseHeaders,
      });
    } catch (error) {
      return new Response(`Proxy hatası: ${error instanceof Error ? error.message : String(error)}`, {
        status: 502,
        headers: cors,
      });
    }
  },
};
