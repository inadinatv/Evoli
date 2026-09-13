# -*- coding: utf-8 -*-
"""Sahte Evooli sitesi — testler için.

Gerçek CDN veri merkezi IP'lerini ve yanlış Referer'ı reddediyor; bu yüzden
uçtan uca testleri aynı davranışı taklit eden yerel bir site üzerinde
koşuyoruz:

* ``/<id>.mp4`` yalnızca *aynı siteden* gelen Referer ile 200 döner
  (gerçek hotlink korumasının taklidi),
* ``?norange=1`` Range isteğini yok sayar (sunucunun aralık taklidini test
  etmek için),
* bir filmin gömme sayfasında hiç mp4 yoktur (şablonla üretme testi),
* bir film ölü bir CDN alan adı verir (öğrenilen alan adına düşme testi),
* bir film HLS (m3u8) yayın verir (playlist yeniden yazma testi).
"""
from __future__ import annotations

import json
import re
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VIDEO_SIZE = 200_000
VIDEO_BODY = (b"EVOLI-MP4-DATA-" + (bytes(range(256)) * 900))[:VIDEO_SIZE]
POSTER_BODY = b"\x89PNG\r\n\x1a\n" + b"EVOLIPOSTER" * 50
SEGMENT_BODY = b"\x47" + b"TSSEGMENT" * 200

FILMS = [
    {"id": "300001", "slug": "deneme-filmi-bir-uzun-baslik", "title": "Deneme Filmi Bir",
     "cats": ["HD", "Amatör"], "kind": "normal"},
    {"id": "300002", "slug": "deneme-filmi-iki-uzun-baslik", "title": "Deneme Filmi İki",
     "cats": ["HD"], "kind": "no-mp4"},
    {"id": "300003", "slug": "deneme-filmi-uc-uzun-baslik", "title": "Deneme Filmi Üç",
     "cats": ["Amatör"], "kind": "dead-host"},
    {"id": "300004", "slug": "deneme-filmi-dort-uzun-baslik", "title": "Deneme Filmi Dört",
     "cats": ["HD", "Amatör"], "kind": "hls"},
    {"id": "300005", "slug": "deneme-filmi-bes-uzun-baslik", "title": "Deneme Filmi Beş",
     "cats": ["HD"], "kind": "normal"},
]
BY_SLUG = {f["slug"]: f for f in FILMS}
BY_ID = {f["id"]: f for f in FILMS}
CATEGORIES = {"HD": "/hd-porno-i/", "Amatör": "/amator-porno-o/"}


class State:
    def __init__(self):
        self.hits = Counter()
        self.referers = Counter()
        self.rejected = Counter()
        self.lock = threading.Lock()

    def hit(self, key, referer=None):
        with self.lock:
            self.hits[key] += 1
            if referer is not None:
                self.referers[referer] += 1

    def reject(self, key):
        with self.lock:
            self.rejected[key] += 1


def _page(shell_title: str, body: str, host: str, canonical: str = "") -> str:
    canonical = canonical or f"http://{host}/"
    return f"""<!DOCTYPE html><html lang="tr"><head>
<meta charset="utf-8">
<link rel="canonical" href="{canonical}">
<meta property="og:url" content="{canonical}">
<meta property="og:site_name" content="Evooli Test">
<title>{shell_title} | Evooli Test</title></head>
<body>
<nav>
  <a href="http://{host}/hd-porno-i/">HD</a>
  <a href="http://{host}/amator-porno-o/">Amatör</a>
  <a href="http://{host}/author/pornosu/">Yazar</a>
  <a href="http://{host}/page/2/">2</a>
</nav>
{body}
</body></html>"""


def film_page(film: dict, host: str) -> str:
    body = f"""<h1>{film['title']}</h1>
<p>Açıklama metni.</p>
<div class="player">
  <iframe src="/pornolar/{film['id']}.html" width="640" height="360" frameborder="0" allowfullscreen></iframe>
</div>
<meta property="og:image" content="http://{host}/img/{film['id']}.jpg">
<div class="cats">{"".join(f'<a href="http://{host}{CATEGORIES[c]}">{c}</a>' for c in film['cats'])}</div>
<div class="related">{"".join(f'<a href="http://{host}/{f["slug"]}/">{f["title"]}</a>' for f in FILMS[:3])}</div>
"""
    return _page(film["title"], body, host, canonical=f"http://{host}/{film['slug']}/")


def embed_page(film: dict, host: str) -> str:
    vid = film["id"]
    kind = film["kind"]
    if kind == "no-mp4":
        # Kaynak HTML'de hiç yok: sadece oynatıcı kurulumu ve numara var.
        player = f"""<script>jwplayer("p").setup({{ "pid": "{vid}", width:"100%" }});</script>"""
    elif kind == "dead-host":
        player = ("""<script>var player = jwplayer("p");player.setup({sources:[{"""
                  f"""file:"https://old-cdn-host.invalid/{vid}.mp4",type:"video/mp4"}}]}});</script>""")
    elif kind == "hls":
        player = ("""<script>var player = jwplayer("p");player.setup({sources:[{"""
                  f"""file:"http:\\/\\/{host}\\/hls\\/{vid}\\/index.m3u8",type:"hls"}}]}});</script>""")
    else:
        # Kaçışlanmış (escaped) URL — eski regex bunu hiç görmüyordu.
        player = ("""<script>var player = jwplayer("p");player.setup({"""
                  f"""file:"http:\\/\\/{host}\\/{vid}.mp4",image:"http:\\/\\/{host}\\/img\\/{vid}.jpg",type:"video/mp4"}});</script>""")
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Porno</title></head>
<body><div id="p">Error loading media: File could not be played</div>{player}</body></html>"""


def sitemap_index(host: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>http://{host}/sitemap-pt-post-2026-09.xml</loc><lastmod>2026-09-13T19:01:32+00:00</lastmod></sitemap>
<sitemap><loc>http://{host}/sitemap-pt-post-2026-08.xml</loc><lastmod>2026-09-05T07:24:49+00:00</lastmod></sitemap>
<sitemap><loc>http://{host}/sitemap-tax-category.xml</loc><lastmod>2026-09-13T19:01:32+00:00</lastmod></sitemap>
</sitemapindex>"""


def sitemap_posts(host: str, month: str = "2026-09") -> str:
    urls = FILMS if month == "2026-09" else FILMS[-1:]
    items = "".join(
        f"<url><loc>http://{host}/{f['slug']}/</loc><lastmod>{month}-13T19:01:32+00:00</lastmod>"
        f"<changefreq>always</changefreq><priority>0.7</priority></url>" for f in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + items + "</urlset>")


def sitemap_tax(host: str) -> str:
    items = "".join(f"<url><loc>http://{host}{path}</loc></url>" for path in CATEGORIES.values())
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + items + "</urlset>")


class FakeSiteHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: State = None

    def log_message(self, *_a):
        pass

    # ------------------------------------------------------------------ #
    def _host(self):
        return self.headers.get("Host") or f"127.0.0.1:{self.server.server_address[1]}"

    def _send(self, code, body: bytes, ctype="text/html; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for key, value in (extra or {}).items():
            self.send_header(key, str(value))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except OSError:
                self.close_connection = True

    def _json(self, code, payload, extra=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8", extra)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query or "")
        host = self._host()
        referer = self.headers.get("Referer") or ""
        self.state.hit(path, referer)

        # --- video / poster (hotlink korumalı) ------------------------- #
        match = re.fullmatch(r"/(\d{6})\.mp4", path)
        if match:
            return self._video(match.group(1), query, referer, host)
        match = re.fullmatch(r"/img/(\d{6})\.jpg", path)
        if match:
            if not self._referer_ok(referer, host):
                self.state.reject("poster")
                return self._send(403, b"403 Forbidden", "text/plain")
            return self._send(200, POSTER_BODY, "image/jpeg", {"Cache-Control": "public, max-age=3600"})
        if path.startswith("/hls/"):
            return self._hls(path, referer, host)

        # --- sayfalar -------------------------------------------------- #
        if path in ("/", "/index.html"):
            body = "".join(f'<div class="item"><a href="http://{host}/{f["slug"]}/">'
                           f'<img src="http://{host}/img/{f["id"]}.jpg"><h2>{f["title"]}</h2></a></div>'
                           for f in FILMS[:3])
            body += f'<div class="paging">Sayfa 1 / 3 <a href="http://{host}/page/2/">2</a></div>'
            return self._send(200, _page("Ana Sayfa", body, host).encode())
        if re.fullmatch(r"/page/(\d+)/", path):
            body = "".join(f'<a href="http://{host}/{f["slug"]}/">{f["title"]}</a>' for f in FILMS[3:])
            return self._send(200, _page("Sayfa 2", body, host).encode())
        for name, cat_path in CATEGORIES.items():
            if path == cat_path:
                body = "".join(f'<a href="http://{host}/{f["slug"]}/">{f["title"]}</a>'
                               for f in FILMS if name in f["cats"][:1])
                body += f'<div class="paging">Sayfa 1 / 2 <a href="http://{host}{cat_path}page/2/">2</a></div>'
                return self._send(200, _page(name, body, host).encode())
            if path == f"{cat_path}page/2/":
                body = "".join(f'<a href="http://{host}/{f["slug"]}/">{f["title"]}</a>'
                               for f in FILMS if name in f["cats"])
                return self._send(200, _page(name + " 2", body, host).encode())

        if path == "/sitemap.xml":
            return self._send(200, sitemap_index(host).encode(), "application/xml")
        if path == "/sitemap-pt-post-2026-09.xml":
            return self._send(200, sitemap_posts(host, "2026-09").encode(), "application/xml")
        if path == "/sitemap-pt-post-2026-08.xml":
            return self._send(200, sitemap_posts(host, "2026-08").encode(), "application/xml")
        if path == "/sitemap-tax-category.xml":
            return self._send(200, sitemap_tax(host).encode(), "application/xml")

        if path == "/wp-json/wp/v2/posts":
            per_page = int((query.get("per_page") or ["10"])[0])
            page = int((query.get("page") or ["1"])[0])
            start = (page - 1) * per_page
            chunk = FILMS[start:start + per_page]
            total_pages = max(1, -(-len(FILMS) // per_page))
            payload = [{
                "id": 60000 + int(f["id"]) - 300000,
                "date": "2026-09-13T19:00:00",
                "slug": f["slug"],
                "link": f"http://{host}/{f['slug']}/",
                "title": {"rendered": f["title"]},
                "categories": [i + 1 for i in range(len(f["cats"]))],
                "content": {"rendered": "<p>Açıklama.</p>"},
            } for f in chunk]
            return self._json(200, payload, {"X-WP-Total": len(FILMS), "X-WP-TotalPages": total_pages})
        if path == "/wp-json/wp/v2/categories":
            payload = [{"id": i + 1, "name": name, "link": f"http://{host}{cat_path}", "count": 5}
                       for i, (name, cat_path) in enumerate(CATEGORIES.items())]
            return self._json(200, payload)

        match = re.fullmatch(r"/pornolar/(\d{6})\.html", path)
        if match:
            film = BY_ID.get(match.group(1))
            if not film:
                return self._send(404, b"yok", "text/plain")
            return self._send(200, embed_page(film, host).encode())

        match = re.fullmatch(r"/([a-z0-9\-]+)/", path)
        if match:
            film = BY_SLUG.get(match.group(1))
            if film:
                return self._send(200, film_page(film, host).encode())

        return self._send(404, b"404 - Not Found", "text/plain")

    # ------------------------------------------------------------------ #
    def _referer_ok(self, referer: str, host: str) -> bool:
        """Gerçek CDN gibi: sadece aynı siteden gelen Referer kabul edilir."""
        if not referer:
            return False
        parsed = urlparse(referer)
        return parsed.netloc == host

    def _video(self, vid: str, query: dict, referer: str, host: str):
        if vid not in BY_ID:
            return self._send(404, b"404 Not Found", "text/plain")
        if not self._referer_ok(referer, host):
            self.state.reject("video")
            return self._send(403, b"403 Forbidden", "text/plain",
                              {"X-Reject-Reason": "bad-referer"})
        body = VIDEO_BODY
        ctype = "video/mp4"
        extra = {"Accept-Ranges": "bytes", "Last-Modified": "Sat, 13 Sep 2026 10:00:00 GMT"}
        range_header = self.headers.get("Range")
        honor_range = "norange" not in query
        if range_header and honor_range:
            match = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if match:
                start = int(match.group(1) or 0)
                end = int(match.group(2) or 0) or len(body) - 1
                end = min(end, len(body) - 1)
                if start <= end:
                    chunk = body[start:end + 1]
                    extra["Content-Range"] = f"bytes {start}-{end}/{len(body)}"
                    return self._send(206, chunk, ctype, extra)
        return self._send(200, body, ctype, extra)

    def _hls(self, path: str, referer: str, host: str):
        if not self._referer_ok(referer, host):
            self.state.reject("hls")
            return self._send(403, b"403 Forbidden", "text/plain")
        match = re.fullmatch(r"/hls/(\d{6})/index\.m3u8", path)
        if match:
            vid = match.group(1)
            playlist = ("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:6\n"
                        "#EXT-X-MEDIA-SEQUENCE:0\n"
                        f'#EXT-X-KEY:METHOD=AES-128,URI="http://{host}/hls/{vid}/key.bin"\n'
                        "#EXTINF:5.0,\n"
                        f"http://{host}/hls/{vid}/seg0.ts\n"
                        "#EXTINF:5.0,\n"
                        f"seg1.ts\n"
                        "#EXT-X-ENDLIST\n")
            return self._send(200, playlist.encode(), "application/vnd.apple.mpegurl")
        if re.fullmatch(r"/hls/\d{6}/seg\d+\.ts", path):
            return self._send(200, SEGMENT_BODY, "video/mp2ts")
        if re.fullmatch(r"/hls/\d{6}/key\.bin", path):
            return self._send(200, b"0123456789abcdef", "application/octet-stream")
        return self._send(404, b"yok", "text/plain")


def quiet_handle_error(*_args):
    """İstemci bağlantıyı erken kapattığında çıkan gürültüyü sustur."""
    import sys
    exc = sys.exc_info()[1]
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
        return
    import traceback
    traceback.print_exc()


def start_fake_site():
    state = State()

    class Bound(FakeSiteHandler):
        pass

    Bound.state = state
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Bound)
    httpd.daemon_threads = True
    httpd.handle_error = quiet_handle_error
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"
    return httpd, thread, base, state
