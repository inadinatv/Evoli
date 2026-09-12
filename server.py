# -*- coding: utf-8 -*-
"""HTML sunucu + video proxy (header enjeksiyonu)."""
import json, os, socket
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import requests as req
import urllib3
from config import *

urllib3.disable_warnings()

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
DB = {"films": []}
HTML_CACHE = ""


def reload():
    global DB, HTML_CACHE
    if os.path.exists(FILMS_JSON):
        try:
            with open(FILMS_JSON, encoding="utf-8") as f: DB = json.load(f)
        except Exception: DB = {"films": []}
    import urllib.parse as up
    with open(os.path.join(TEMPLATES_DIR, "index.html"), encoding="utf-8") as f:
        tpl = f.read()
    import urllib.parse
    ua_enc = urllib.parse.quote(USER_AGENT)
    ref_enc = urllib.parse.quote(REFERER)
    HTML_CACHE = (tpl.replace("__FILMS__", json.dumps(DB.get("films", []), ensure_ascii=False))
                     .replace("__UA__", ua_enc).replace("__REF__", ref_enc))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def _send(self, code, ctype, body=None, extra=None):
        self.send_response(code); self.send_header("Content-Type", ctype)
        if body is not None: self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers()
        if body is not None:
            try: self.wfile.write(body)
            except Exception: pass

    def do_GET(self):
        reload()  # Her istekte en taze veriyi yükle
        p = urlparse(self.path).path
        if p in ("/", "/index.html", ""):
            self._send(200, "text/html; charset=utf-8", HTML_CACHE.encode("utf-8"))
        elif p == "/api/films":
            self._send(200, "application/json",
                       json.dumps(DB.get("films", []), ensure_ascii=False).encode("utf-8"))
        elif p == "/m3u":
            if os.path.exists(PLAYLIST_M3U):
                with open(PLAYLIST_M3U, "rb") as f: body = f.read()
                self._send(200, "audio/x-mpegurl", body,
                           {"Content-Disposition": "attachment; filename=playlist.m3u"})
            else: self._send(404, "text/plain", b"404")
        elif p.startswith("/stream/") or p.startswith("/poster/"):
            self.proxy(p)
        else:
            self._send(404, "text/plain", b"404")

    def proxy(self, p):
        par = p.strip("/").split("/")
        if len(par) < 2: return self._send(404, "text/plain", b"404")
        tip, fid = par[0], par[1]
        films = DB.get("films", [])
        f = next((x for x in films if x["id"] == fid), None)
        if not f: return self._send(404, "text/plain", b"404")
        url = f["stream"] if tip == "stream" else f.get("poster", "")
        if not url: return self._send(404, "text/plain", b"404")
        h = {"User-Agent": USER_AGENT, "Referer": REFERER}
        rng = self.headers.get("Range")
        if rng: h["Range"] = rng
        try:
            up = req.get(url, headers=h, stream=True, verify=False, timeout=30)
        except Exception:
            return self._send(502, "text/plain", b"upstream error")
        self.send_response(up.status_code)
        self.send_header("Content-Type", up.headers.get("Content-Type",
                        "video/mp4" if tip == "stream" else "image/jpeg"))
        cl = up.headers.get("Content-Length")
        if cl: self.send_header("Content-Length", cl)
        cr = up.headers.get("Content-Range")
        if cr: self.send_header("Content-Range", cr)
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        try:
            for chunk in up.iter_content(131072): self.wfile.write(chunk)
        except Exception: pass
        finally: up.close()


def run():
    reload()
    try: ip = socket.gethostbyname(socket.gethostname())
    except: ip = "127.0.0.1"
    print(f"[✓] Sunucu: http://127.0.0.1:{PORT}")
    print(f"[✓] Ağ IP : http://{ip}:{PORT}")
    print(f"[✓] M3U   : http://127.0.0.1:{PORT}/m3u")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
