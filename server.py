# -*- coding: utf-8 -*-
import json, os, socket, mimetypes, ssl
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote
import requests as req
from requests.adapters import HTTPAdapter
import urllib3
from config import *

urllib3.disable_warnings()


class SSLAdapter(HTTPAdapter):
    """Allow older CDN TLS configurations without weakening the browser."""
    def init_poolmanager(self, *args, **kwargs):
        context = ssl.create_default_context()
        context.set_ciphers("DEFAULT@SECLEVEL=1")
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)


# Keep connections alive and use the same tolerant TLS setup as the scraper.
UPSTREAM = req.Session()
UPSTREAM.mount("https://", SSLAdapter())
UPSTREAM.mount("http://", SSLAdapter())

DB = {"films": [], "categories": {}, "updated": "", "total": 0}

def reload():
    global DB
    if os.path.exists(FILMS_JSON):
        try:
            with open(FILMS_JSON, encoding="utf-8") as f:
                DB = json.load(f)
        except Exception:
            DB = {"films": [], "categories": {}, "updated": "", "total": 0}

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        # sessiz log, istersen aç
        pass

    def _send(self, code, ctype, body=None, extra=None, cache=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
        if cache:
            self.send_header("Cache-Control", "public, max-age=60")
        else:
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        if body is not None:
            self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body is not None and self.command != "HEAD":
            try:
                self.wfile.write(body)
            except Exception:
                pass

    def do_OPTIONS(self):
        self._send(200, "text/plain", b"", {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS, HEAD",
            "Access-Control-Allow-Headers": "Range, Content-Type"
        })

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        reload()
        parsed = urlparse(self.path)
        p = unquote(parsed.path)

        # Normalize
        if p in ("/", "", "/index.html"):
            p = "/index.html"

        # API endpoints
        if p == "/api/films":
            # backwards compat: return array, but if ?full=1 return full db
            if "full" in parsed.query:
                data = json.dumps(DB, ensure_ascii=False).encode("utf-8")
            else:
                data = json.dumps(DB.get("films", []), ensure_ascii=False).encode("utf-8")
            return self._send(200, "application/json; charset=utf-8", data, cache=True)

        if p == "/api/categories":
            data = json.dumps(DB.get("categories", {}), ensure_ascii=False).encode("utf-8")
            return self._send(200, "application/json; charset=utf-8", data, cache=True)

        if p == "/api/stats":
            stats = {
                "total": len(DB.get("films", [])),
                "categories": len(DB.get("categories", {})),
                "updated": DB.get("updated", ""),
            }
            return self._send(200, "application/json; charset=utf-8", json.dumps(stats, ensure_ascii=False).encode("utf-8"), cache=True)

        if p == "/m3u" or p == "/playlist.m3u":
            if os.path.exists(PLAYLIST_M3U):
                with open(PLAYLIST_M3U, "rb") as f:
                    return self._send(200, "audio/x-mpegurl", f.read(),
                               {"Content-Disposition": "attachment; filename=playlist.m3u"}, cache=False)
            else:
                return self._send(404, "text/plain", b"playlist not found")

        if p.startswith("/stream/") or p.startswith("/poster/"):
            return self.proxy(p)

        # Static file serving - secure
        # Allow: /films.json, /index.html, favicon, etc.
        # Prevent directory traversal
        base = os.path.abspath(BASE_DIR)
        requested = os.path.abspath(os.path.join(base, p.lstrip("/")))
        # Ensure inside base
        if not requested.startswith(base):
            return self._send(403, "text/plain", b"forbidden")

        if os.path.isfile(requested):
            # mime
            mime, _ = mimetypes.guess_type(requested)
            if not mime:
                if requested.endswith(".json"):
                    mime = "application/json; charset=utf-8"
                elif requested.endswith(".m3u"):
                    mime = "audio/x-mpegurl"
                else:
                    mime = "application/octet-stream"
            if mime.startswith("text/") and "charset" not in mime:
                mime += "; charset=utf-8"
            # cache for json/html?
            is_cacheable = requested.endswith((".jpg",".jpeg",".png",".webp",".svg",".js",".css",".woff2"))
            try:
                with open(requested, "rb") as f:
                    data = f.read()
                return self._send(200, mime, data, cache=is_cacheable)
            except Exception as e:
                return self._send(500, "text/plain", f"read error: {e}".encode())

        # fallback: if file not found but path is /films.json with query, try without query
        if p.startswith("/films.json"):
            if os.path.exists(FILMS_JSON):
                with open(FILMS_JSON, "rb") as f:
                    return self._send(200, "application/json; charset=utf-8", f.read(), cache=True)

        return self._send(404, "text/plain; charset=utf-8", b"404 - Not Found")

    def proxy(self, p):
        par = p.strip("/").split("/")
        if len(par) < 2:
            return self._send(404, "text/plain", b"404")
        tip, fid = par[0], par[1]
        f = next((x for x in DB.get("films", []) if x["id"] == fid), None)
        if not f:
            return self._send(404, "text/plain", b"film not found")
        url = f["stream"] if tip == "stream" else f.get("poster", "")
        if not url:
            return self._send(404, "text/plain", b"no url")
        h = {
            "User-Agent": USER_AGENT,
            "Referer": REFERER,
            "Origin": REFERER.rstrip("/"),
            "Accept": "*/*",
            # Video CDNs sometimes compress responses despite a Range header,
            # which makes byte offsets invalid in browsers.
            "Accept-Encoding": "identity",
        }
        rng = self.headers.get("Range")
        if rng:
            h["Range"] = rng
        try:
            up = UPSTREAM.get(url, headers=h, stream=True, verify=False,
                              allow_redirects=True, timeout=(10, 120))
        except Exception as e:
            return self._send(502, "text/plain", f"upstream error: {e}".encode())
        # pass through status
        self.send_response(up.status_code)
        ctype = up.headers.get("Content-Type", "video/mp4" if tip == "stream" else "image/jpeg")
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges")
        self.send_header("Accept-Ranges", "bytes")
        # Important headers for video
        for hk in ("Content-Length", "Content-Range", "Content-Disposition", "Cache-Control", "ETag", "Last-Modified"):
            hv = up.headers.get(hk)
            if hv:
                self.send_header(hk, hv)
        # If no cache header, set one for poster
        if tip == "poster" and not up.headers.get("Cache-Control"):
            self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        try:
            for chunk in up.iter_content(131072):
                if not chunk:
                    continue
                self.wfile.write(chunk)
        except Exception:
            pass
        finally:
            up.close()

def run():
    try:
        # get local ip
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = socket.gethostbyname(socket.gethostname())
        finally:
            s.close()
    except Exception:
        ip = "127.0.0.1"
    print(f"[✓] EVOLI Premium Sunucu")
    print(f"[✓] Local : http://127.0.0.1:{PORT}")
    print(f"[✓] Network: http://{ip}:{PORT}")
    print(f"[✓] M3U   : http://127.0.0.1:{PORT}/m3u")
    print(f"[✓] API   : http://127.0.0.1:{PORT}/api/films")
    print(f"[✓] JSON  : http://127.0.0.1:{PORT}/films.json")
    print(f"")
    try:
        ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Sunucu durduruldu")

if __name__ == "__main__":
    run()
