# -*- coding: utf-8 -*-
"""Evoli yerel sunucusu.

Tarayıcı videoyu *kendi origin'imizden* (``/stream/<id>``) ister; sunucu da
gerekli User-Agent/Referer/Range başlıklarını ekleyip CDN'den akıtır.

Oynatmanın bozulmaması için burada düzeltilen şeyler:

* **Çok adaylı kaynak**: kayıtlı URL 403/404 verirse sırayla diğer Referer
  varyasyonları, öğrenilen CDN alan adları ve son çare olarak filmin
  sayfasından canlı çözüm denenir (``resolver.MediaResolver``).
* **Range doğruluğu**: ``Accept-Ranges: bytes`` artık upstream gerçekten
  destekliyorsa gönderiliyor; desteklemiyorsa sunucu aralığı kendisi taklit
  ediyor (206 + Content-Range), böylece ileri/geri sarma çalışıyor.
* **HTTP/1.1 bütünlüğü**: Content-Length yoksa bağlantı kapatılıyor (eski
  sürümde tarayıcı yanıtın bittiğini anlayamayıp takılıyordu), HEAD isteğinde
  gövde yazılmıyor.
* **Performans**: films.json her istekte yeniden okunmuyor (mtime önbelleği),
  metin yanıtları gzip'leniyor.
"""
from __future__ import annotations

import gzip
import json
import mimetypes
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import re

import config
import net
import resolver as resolver_mod
import scraper

DB_LOCK = threading.RLock()
_STATE = {"mtime": 0.0, "size": -1, "db": scraper.empty_db(), "index": {}, "by_url": {}, "loaded_at": 0}
RESOLVER = resolver_mod.MediaResolver()
HLS_LOCK = threading.Lock()
HLS_PLAYLISTS: dict = {}
JOB = {"running": False, "kind": "", "started": "", "finished": "", "result": None, "error": ""}
JOB_LOCK = threading.Lock()
RESOLVE_LOCKS: dict = {}
RESOLVE_LOCKS_GUARD = threading.Lock()

JSON_CT = "application/json; charset=utf-8"
COMPRESSIBLE = ("application/json", "text/", "audio/x-mpegurl", "application/x-mpegurl", "image/svg+xml")

HLSJS_CACHE: dict = {"data": None, "ts": 0.0}
MAX_IMAGE_BYTES = 15 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Veritabanı erişimi
# --------------------------------------------------------------------------- #
def get_db(force: bool = False) -> dict:
    """films.json'u yalnızca değiştiyse yeniden okur."""
    path = config.FILMS_JSON
    try:
        st = os.stat(path)
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        mtime, size = 0.0, -1
    with DB_LOCK:
        if not force and _STATE["loaded_at"] and _STATE["mtime"] == mtime and _STATE["size"] == size:
            return _STATE["db"]
        db = scraper.load_db(path)
        index, by_url = {}, {}
        for film in db.get("films", []):
            fid = str(film.get("id") or "")
            if fid:
                index[fid] = film
            vid = str(film.get("video_id") or "")
            if vid and vid not in index:
                index[vid] = film
            slug = scraper.slug_of(film.get("url") or "")
            if slug and slug not in by_url:
                by_url[slug] = film
        _STATE.update({"mtime": mtime, "size": size, "db": db, "index": index,
                       "by_url": by_url, "loaded_at": time.time()})
        # Çözücü, taramadan öğrenilen meta bilgileri kullansın.
        meta = db.get("meta") or {}
        if meta.get("site"):
            RESOLVER.note_site(meta["site"])
        for host in (meta.get("cdn_hosts") or []):
            RESOLVER.note_cdn_host("https://" + host)
        return db


def find_film(key: str) -> dict:
    db = get_db()
    key = (key or "").strip()
    with DB_LOCK:
        film = _STATE["index"].get(key) or _STATE["by_url"].get(key)
    if film:
        return film
    if key.isdigit():
        for film in db.get("films", []):
            if str(film.get("video_id") or "") == key or str(film.get("id") or "") == key:
                return film
    return {}


def resolver_lock(film_id: str) -> threading.Lock:
    with RESOLVE_LOCKS_GUARD:
        lock = RESOLVE_LOCKS.get(film_id)
        if lock is None:
            if len(RESOLVE_LOCKS) > 500:
                RESOLVE_LOCKS.clear()
            lock = RESOLVE_LOCKS[film_id] = threading.Lock()
        return lock


# --------------------------------------------------------------------------- #
# HLS (m3u8) vekili
# --------------------------------------------------------------------------- #
def rewrite_playlist(body: str, base_url: str, fid: str) -> str:
    """m3u8 içindeki tüm adresleri kendi /hls/<fid>/<n> uçlarımıza çevirir."""
    with HLS_LOCK:
        urls = HLS_PLAYLISTS.setdefault(fid, [])
        known = {u: i for i, u in enumerate(urls)}
    out = []
    for raw in body.splitlines():
        line = raw.rstrip("\r")
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        if stripped.startswith("#"):
            def swap(match):
                target = match.group(2)
                full = urljoin(base_url, target)
                with HLS_LOCK:
                    if full in known:
                        idx = known[full]
                    else:
                        idx = len(urls)
                        urls.append(full)
                        known[full] = idx
                return f'{match.group(1)}/hls/{fid}/{idx}{match.group(3)}'
            out.append(re.sub(r'(URI=")([^"]+)(")', swap, line))
            continue
        full = urljoin(base_url, stripped)
        with HLS_LOCK:
            if full in known:
                idx = known[full]
            else:
                idx = len(urls)
                urls.append(full)
                known[full] = idx
        out.append(f"/hls/{fid}/{idx}")
    return "\n".join(out) + "\n"


def hls_target(fid: str, index: int):
    with HLS_LOCK:
        urls = HLS_PLAYLISTS.get(fid) or []
        if 0 <= index < len(urls):
            return urls[index]
    return None


# --------------------------------------------------------------------------- #
# HTTP işleyici
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Evoli/6.0"
    timeout = 90

    # ------------------------------------------------------------------ #
    def log_message(self, *_args):
        pass  # sessiz log

    # ------------------------------------------------------------------ #
    def _send(self, code: int, ctype: str, body: bytes = None, extra: dict = None,
              cache: bool = False, head_only: bool = False):
        body = body or b""
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Content-Type, If-None-Match, If-Modified-Since",
            "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, X-Evoli-Source, X-Evoli-Upstream",
            # Sayfamız başka sitelere gömülüyorsa / doğrudan kaynak oynatılıyorsa
            # tarayıcı kaynak engeline (CORP/CORB) takılmamalı:
            "Cross-Origin-Resource-Policy": "cross-origin",
            "Access-Control-Allow-Private-Network": "true",
        }
        if cache:
            headers["Cache-Control"] = "public, max-age=60"
        else:
            headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        accept_enc = (self.headers.get("Accept-Encoding") or "").lower()
        if (body and "gzip" in accept_enc and len(body) >= config.GZIP_MIN_BYTES
                and any(ctype.startswith(prefix) for prefix in COMPRESSIBLE)):
            body = gzip.compress(body, 6)
            headers["Content-Encoding"] = "gzip"
            headers["Vary"] = "Accept-Encoding"
        headers.update(extra or {})
        if self._client_wants_close():
            headers["Connection"] = "close"
            self.close_connection = True
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for key, value in headers.items():
            if value is not None:
                self.send_header(key, str(value))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and not head_only:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.close_connection = True

    def _client_wants_close(self) -> bool:
        """İstemci ``Connection: close`` istediyse yanıtta da belirt.

        Belirtilmezse keep-alive yapan istemciler sunucunun kapattığı
        soketi yeniden kullanmaya çalışıp boş yanıt (RemoteDisconnected)
        alıyor; video oynatıcılarda bu takılma olarak görünüyor.
        """
        value = (self.headers.get("Connection") or "").lower() if self.headers else ""
        return "close" in value

    def _json(self, code: int, payload, cache: bool = False, extra: dict = None,
              head_only: bool = None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if head_only is None:
            head_only = self.command == "HEAD"
        self._send(code, JSON_CT, body, extra=extra, cache=cache, head_only=head_only)

    def do_OPTIONS(self):
        # Tarayıcının istediği başlıkları yankıla (preflight her zaman geçsin).
        requested = (self.headers.get("Access-Control-Request-Headers") or "").strip()
        extra = {}
        if requested:
            extra["Access-Control-Allow-Headers"] = requested
        self._send(204, "text/plain", b"", cache=False, extra=extra)

    def do_HEAD(self):
        self.do_GET()

    # ------------------------------------------------------------------ #
    def do_GET(self):
        head_only = self.command == "HEAD"
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query or "")
        parts = [p for p in path.split("/") if p]

        try:
            if path in ("/", "", "/index.html"):
                return self.serve_file(config.BASE_DIR + "/index.html", head_only, cache=False)

            if path == "/proxy":
                return self.api_proxy(query, head_only)
            if path in ("/hls.js", "/hls.min.js"):
                return self.serve_hlsjs(head_only)

            if path == "/api/films":
                return self.api_films(query, head_only)
            if path == "/api/categories":
                db = get_db()
                return self._json(200, db.get("categories", {}), cache=True)
            if path == "/api/stats":
                return self.api_stats(head_only)
            if path == "/api/status":
                return self.api_status(head_only)
            if path == "/films.json" or path.startswith("/films.json?"):
                return self.serve_file(config.FILMS_JSON, head_only, cache=False, ctype=JSON_CT)
            if path == "/api/meta":
                db = get_db()
                return self._json(200, db.get("meta", {}), cache=True, head_only=head_only)
            if path in ("/m3u", "/playlist.m3u"):
                return self.serve_file(config.PLAYLIST_M3U, head_only, cache=False,
                                       ctype="audio/x-mpegurl",
                                       download_name="playlist.m3u")
            if path in ("/playlist_local.m3u", "/local.m3u"):
                return self.serve_file(config.LOCAL_PLAYLIST_M3U, head_only, cache=False,
                                       ctype="audio/x-mpegurl",
                                       download_name="playlist_local.m3u")
            if len(parts) >= 2 and parts[0] in ("stream", "poster"):
                return self.proxy_media(parts[0], parts[1], query, head_only)
            if len(parts) >= 3 and parts[0] == "hls":
                return self.proxy_hls(parts[1], parts[2], head_only)
            if len(parts) >= 2 and parts[0] == "api" and parts[1] == "resolve":
                return self.api_resolve(parts[2] if len(parts) > 2 else "", query, head_only)
            if len(parts) >= 2 and parts[0] == "api" and parts[1] == "repair":
                return self.api_repair(parts[2:] , query, head_only)
            if len(parts) >= 2 and parts[0] == "api" and parts[1] == "health":
                return self.api_health(parts[2] if len(parts) > 2 else "", head_only)

            return self.serve_static(path, head_only)
        except Exception as exc:  # noqa: BLE001 - sunucu ayakta kalmalı
            try:
                self._json(500, {"error": repr(exc)[:200]})
            except Exception:
                self.close_connection = True

    # ------------------------------------------------------------------ #
    # API
    # ------------------------------------------------------------------ #
    def api_films(self, query: dict, head_only: bool):
        db = get_db()
        if "full" in query:
            return self._json(200, db, cache=True, head_only=head_only)
        films = db.get("films", [])
        search = (query.get("q") or [""])[0].strip().lower()
        cat = (query.get("cat") or [""])[0].strip()
        if search:
            films = [f for f in films
                     if search in (f.get("title") or "").lower()
                     or any(search in (c or "").lower() for c in (f.get("categories") or []))]
        if cat and cat.lower() not in ("tümü", "tumu", "all"):
            films = [f for f in films if cat in (f.get("categories") or [])]
        try:
            limit = int((query.get("limit") or ["0"])[0])
            offset = int((query.get("offset") or ["0"])[0])
        except ValueError:
            limit, offset = 0, 0
        total = len(films)
        if limit > 0:
            films = films[offset: offset + limit]
        payload = films if not limit else {"total": total, "offset": offset, "films": films}
        return self._json(200, payload, cache=True, head_only=head_only)

    def api_stats(self, head_only: bool):
        db = get_db()
        films = db.get("films", [])
        meta = db.get("meta") or {}
        payload = {
            "total": len(films),
            "playable": sum(1 for f in films if f.get("ok")),
            "broken": sum(1 for f in films if f.get("ok") is False),
            "unknown": sum(1 for f in films if f.get("ok") is None),
            "without_stream": sum(1 for f in films if not f.get("stream")),
            "categories": len(db.get("categories", {})),
            "updated": db.get("updated", ""),
            "cdn_host": meta.get("cdn_host", ""),
            "site": meta.get("site", ""),
            "last_scan": meta.get("last_scan", {}),
        }
        return self._json(200, payload, cache=True, head_only=head_only)

    def api_status(self, head_only: bool):
        db = get_db()
        with JOB_LOCK:
            job = dict(JOB)
        payload = {
            "films": len(db.get("films", [])),
            "updated": db.get("updated", ""),
            "meta": db.get("meta", {}),
            "resolver": RESOLVER.export_meta(),
            "blocked_hosts": RESOLVER.blocked_hosts(),
            "job": job,
            "cache_items": len(RESOLVER._cache.get("items", {})),
        }
        return self._json(200, payload, head_only=head_only)

    def api_resolve(self, key: str, query: dict, head_only: bool):
        film = find_film(key)
        if not film:
            return self._json(404, {"ok": False, "error": "film bulunamadı"}, head_only=head_only)
        refresh = bool(query.get("refresh")) or bool(query.get("force"))
        fid = str(film.get("id") or "")
        lock = resolver_lock(fid)
        acquired = lock.acquire(timeout=25)
        try:
            result = RESOLVER.resolve(film, force=refresh, max_candidates=config.MAX_RESOLVE_CANDIDATES)
        finally:
            if acquired:
                lock.release()
        payload = {
            "ok": bool(result.get("ok")),
            "id": fid,
            "url": result.get("url") if result.get("ok") else "",
            "kind": result.get("kind"),
            "status": result.get("status"),
            "reachable": RESOLVER.tried_reachable(result.get("tried")),
            "blocked_hosts": RESOLVER.blocked_hosts(),
            "stream_url": f"/stream/{fid}" if result.get("ok") else "",
            "tried": result.get("tried", [])[-6:] if query.get("debug") else [],
        }
        if result.get("ok"):
            film["stream"] = result["url"]
            if result.get("referer"):
                film["ref"] = result["referer"]
            film["ok"] = True
            film["host"] = net.host_of(result["url"])
        return self._json(200 if result.get("ok") else 502, payload, head_only=head_only)

    def api_repair(self, rest: list, query: dict, head_only: bool):
        action = (rest[0] if rest else "").lower()
        if action == "status":
            with JOB_LOCK:
                return self._json(200, dict(JOB), head_only=head_only)
        try:
            limit = int((query.get("limit") or ["0"])[0])
        except ValueError:
            limit = 0
        only_broken = (query.get("all") or ["0"])[0] not in ("1", "true", "yes")
        kind_req = (query.get("kind") or [""])[0].strip().lower()
        is_meta = (kind_req in ("posters", "covers", "metadata", "fixmeta", "kapak")
                   or "posters" in query or "covers" in query)
        job_kind = "metadata" if is_meta else "repair"
        with JOB_LOCK:
            if JOB["running"]:
                return self._json(200, {"started": False, "reason": "zaten çalışıyor", **JOB}, head_only=head_only)
            JOB.update({"running": True, "kind": job_kind, "started": time.strftime("%H:%M:%S"),
                        "finished": "", "result": None, "error": ""})

        def run():
            try:
                if is_meta:
                    force = (query.get("all") or ["0"])[0] in ("1", "true", "yes")
                    result = scraper.fix_metadata(limit=limit or None, force=force, verbose=True)
                else:
                    result = scraper.repair(limit=limit or None, only_broken=only_broken, verbose=True)
                with JOB_LOCK:
                    JOB.update({"running": False, "finished": time.strftime("%H:%M:%S"), "result": result})
                get_db(force=True)
            except Exception as exc:  # noqa: BLE001
                with JOB_LOCK:
                    JOB.update({"running": False, "finished": time.strftime("%H:%M:%S"), "error": repr(exc)[:200]})

        threading.Thread(target=run, daemon=True).start()
        return self._json(202, {"started": True, "limit": limit, "kind": job_kind,
                                "only_broken": only_broken}, head_only=head_only)

    def api_health(self, key: str, head_only: bool):
        """/api/health -> genel durum; /api/health/<id> -> tek filmin kaynağı."""
        if not key:
            db = get_db()
            films = db.get("films", []) or []
            verified = sum(1 for f in films if f.get("ok") is True)
            broken = sum(1 for f in films if f.get("ok") is False)
            blocked = RESOLVER.blocked_hosts()
            return self._json(200, {
                "ok": not blocked,
                "version": Handler.server_version,
                "films": len(films),
                "verified": verified,
                "broken": broken,
                "unknown": max(0, len(films) - verified - broken),
                "categories": len(db.get("categories") or {}),
                "updated": db.get("updated", ""),
                "cache_items": len(RESOLVER._cache.get("items", {})),
                "resolver_stats": dict(RESOLVER.stats),
                "blocked_hosts": blocked,
                "hint": ("Sunucu bu ağdan CDN'e ulaşamıyor (VPN/DNS/güvenlik duvarı "
                         "ya da IP engeli)." if blocked else ""),
            }, head_only=head_only)
        film = find_film(key)
        if not film:
            return self._json(404, {"ok": False, "error": "film bulunamadı"}, head_only=head_only)
        result = RESOLVER.resolve(film, force=True, max_candidates=8)
        return self._json(200, {"ok": bool(result.get("ok")), "kind": result.get("kind"),
                                "url": result.get("url"), "status": result.get("status"),
                                "tried": result.get("tried", [])[-6:]}, head_only=head_only)

    # ------------------------------------------------------------------ #
    # CORS / proxy engellerini aşma: genel vekil + aynı origin hls.js
    # ------------------------------------------------------------------ #
    def api_proxy(self, query: dict, head_only: bool):
        """/proxy?url=... [&ref=...]

        Tarayıcıdaki CORS / hotlink / karışık içerik engellerini tamamen
        ortadan kaldırır: uzak kaynak sunucumuz üzerinden (gereken Referer /
        User-Agent / Range başlıklarıyla) akıtılır, yanıt kendi origin'imiz +
        ``Access-Control-Allow-Origin: *`` ile döner.  SSRF koruması aktiftir
        (özel ağ hedefleri ``PROXY_ALLOW_PRIVATE`` kapalıyken reddedilir).
        """
        url = (query.get("url") or [""])[0].strip()
        ref = (query.get("ref") or [""])[0].strip()
        ua = (query.get("ua") or [""])[0].strip() or None
        error = net.validate_proxy_url(url)
        if error:
            return self._json(400, {"ok": False, "error": error}, head_only=head_only)
        require_public = not getattr(config, "PROXY_ALLOW_PRIVATE", False)
        if require_public and not net.is_public_host(urlparse(url).hostname or ""):
            return self._json(403, {"ok": False, "error": "özel/yerel hedefler vekletilemez"},
                             head_only=head_only)
        headers = net.browser_headers(
            referer=ref or None,
            ua=ua,
            range_header=self.headers.get("Range"),
            accept=self.headers.get("Accept") or "*/*",
        )
        for hdr in ("If-None-Match", "If-Modified-Since", "If-Range"):
            value = self.headers.get(hdr)
            if value:
                headers[hdr] = value
        result = net.open_proxy_stream(RESOLVER.session, url, headers=headers,
                                       require_public=require_public)
        if result.resp is None:
            return self._json(502, {"ok": False, "error": result.error or "uzak sunucuya ulaşılamadı",
                                    "url": url}, head_only=head_only)
        resp = result.resp
        try:
            ctype = resp.headers.get("Content-Type") or "application/octet-stream"
            status = resp.status_code
            extra = {"X-Evoli-Proxy": "1", "X-Evoli-Upstream": net.host_of(result.url),
                     "Cache-Control": resp.headers.get("Cache-Control") or "no-cache"}
            for hdr in ("Content-Length", "Content-Range", "Accept-Ranges", "ETag",
                        "Last-Modified", "Content-Disposition", "Content-Type"):
                if hdr == "Content-Type":
                    continue
                if resp.headers.get(hdr):
                    extra[hdr] = resp.headers.get(hdr)
            want_close = (not extra.get("Content-Length")) or self._client_wants_close()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
            self.send_header("Access-Control-Expose-Headers",
                             "Content-Length, Content-Range, Accept-Ranges, X-Evoli-Proxy, X-Evoli-Upstream")
            for key, value in extra.items():
                if value is not None:
                    self.send_header(key, str(value))
            if want_close:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if head_only:
                return
            for chunk in resp.iter_content(config.PROXY_CHUNK):
                if not chunk:
                    continue
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True
        except Exception:  # noqa: BLE001
            self.close_connection = True
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def serve_hlsjs(self, head_only: bool):
        """/hls.js — hls.js kütüphanesini kendi origin'imizden servis eder.

        Oynatıcının uzak CDN'e (jsdelivr vb.) bağımlılığını kaldırır: engelli
        ağlarda bile HLS oynatma çalışır.  İlk istekten sonra bellekte
        saklanır; sunucu kendi indirme yedeklerini (config.HLSJS_SOURCES) dener.
        """
        cached = None
        with HLS_LOCK:
            if HLSJS_CACHE["data"] and time.time() - HLSJS_CACHE["ts"] < getattr(config, "HLSJS_CACHE_TTL", 21600):
                cached = HLSJS_CACHE["data"]
        data = cached
        if not data:
            for src in getattr(config, "HLSJS_SOURCES", []) or []:
                body, _ctype, _err = net.fetch_bytes(RESOLVER.session, src, max_bytes=4 * 1024 * 1024)
                if body and len(body) > 8 and not body.lstrip().startswith((b"<", b"{")):
                    data = body
                    break
            if data:
                with HLS_LOCK:
                    HLSJS_CACHE["data"] = data
                    HLSJS_CACHE["ts"] = time.time()
        if not data:
            return self._json(502, {"ok": False,
                                    "error": "hls.js indirilemedi (uzak CDN'ler engelli olabilir)"},
                              head_only=head_only)
        return self._send(200, "application/javascript; charset=utf-8", data,
                          extra={"X-Evoli-Source": "hlsjs-cache" if cached else "hlsjs-fetch"},
                          cache=True, head_only=head_only)

    # ------------------------------------------------------------------ #
    # Statik
    # ------------------------------------------------------------------ #
    def serve_static(self, path: str, head_only: bool):
        base = os.path.abspath(config.BASE_DIR)
        requested = os.path.abspath(os.path.join(base, path.lstrip("/")))
        if not (requested == base or requested.startswith(base + os.sep)):
            return self._send(403, "text/plain; charset=utf-8", b"forbidden", head_only=head_only)
        if os.path.isfile(requested):
            return self.serve_file(requested, head_only,
                                   cache=requested.endswith((".jpg", ".jpeg", ".png", ".webp",
                                                             ".svg", ".js", ".css", ".woff2", ".ico")))
        if path.startswith("/films.json"):
            return self.serve_file(config.FILMS_JSON, head_only, cache=False, ctype=JSON_CT)
        return self._send(404, "text/plain; charset=utf-8",
                          ("404 — bulunamadı: %s" % path).encode("utf-8", "replace"),
                          head_only=head_only)

    def serve_file(self, path: str, head_only: bool, cache: bool = False,
                   ctype: str = "", download_name: str = ""):
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            return self._send(404, "text/plain; charset=utf-8", b"404 - dosya yok", head_only=head_only)
        if not ctype:
            ctype = mimetypes.guess_type(path)[0] or ""
            if not ctype:
                if path.endswith(".json"):
                    ctype = JSON_CT
                elif path.endswith(".m3u") or path.endswith(".m3u8"):
                    ctype = "audio/x-mpegurl"
                else:
                    ctype = "application/octet-stream"
            elif ctype.startswith("text/") and "charset" not in ctype:
                ctype += "; charset=utf-8"
            elif ctype == "application/json":
                ctype = JSON_CT
        extra = {}
        if download_name:
            extra["Content-Disposition"] = f'attachment; filename="{download_name}"'
        return self._send(200, ctype, data, extra=extra, cache=cache, head_only=head_only)

    # ------------------------------------------------------------------ #
    # Medya vekili
    # ------------------------------------------------------------------ #
    def proxy_media(self, kind: str, key: str, query: dict, head_only: bool):
        film = find_film(key)
        if not film:
            return self._json(404, {"ok": False, "error": "film bulunamadı", "id": key},
                              head_only=head_only)
        if kind == "poster":
            return self.proxy_poster(film, head_only)

        range_header = self.headers.get("Range")
        upstream, cand, tried = RESOLVER.open_stream(film, range_header=range_header)
        if upstream is None:
            # Son bir şans: eşzamanlı kilitle canlı çözüm dene.
            fid = str(film.get("id") or "")
            lock = resolver_lock(fid)
            if lock.acquire(timeout=20):
                try:
                    resolved = RESOLVER.resolve(film, force=True)
                finally:
                    lock.release()
                if resolved.get("ok"):
                    upstream, cand, tried2 = RESOLVER.open_stream(film, range_header=range_header)
                    tried = tried + tried2
            if upstream is None:
                get_db(force=True)
                reachable = RESOLVER.tried_reachable(tried)
                return self._json(503 if not reachable else 502, {
                    "ok": False,
                    "error": ("ağa/CDN'e ulaşılamadı" if not reachable
                              else "çalışan video kaynağı bulunamadı"),
                    "id": fid,
                    "title": film.get("title"),
                    "reachable": reachable,
                    "tried": tried[-8:],
                    "hint": ("VPN/DNS/güvenlik duvarı veya CDN'in bu IP'yi engellemesi olabilir"
                             if not reachable else
                             "/api/repair ile toplu onarım başlatabilirsin"),
                }, head_only=head_only)
        try:
            return self.stream_upstream(upstream, cand, film, range_header, head_only)
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    def _fetch_image(self, url: str, referer):
        """Tek adaydan tam görsel gövdesi indirir ve doğrular.

        Dönüş: ``(data, ctype, status, extra)`` — data ``None`` ise aday
        görsel değil/erişilemedi; status 304 ise data boş string'dir.
        """
        headers = net.browser_headers(referer=referer or None, accept="image/*,*/*;q=0.8")
        for hdr in ("If-None-Match", "If-Modified-Since"):
            value = self.headers.get(hdr)
            if value:
                headers[hdr] = value
        try:
            resp = RESOLVER.session.get(url, headers=headers, stream=True, verify=False,
                                        timeout=config.PROBE_TIMEOUT, allow_redirects=True)
        except Exception:  # noqa: BLE001
            return None, "", 0, {}
        try:
            status = resp.status_code
            if status == 304:
                extra = {"Cache-Control": "public, max-age=86400"}
                for hdr in ("ETag", "Last-Modified"):
                    if resp.headers.get(hdr):
                        extra[hdr] = resp.headers.get(hdr)
                return b"", resp.headers.get("Content-Type") or "image/jpeg", 304, extra
            rheaders = dict(resp.headers)
            head = b""
            chunks = []
            size = 0
            for chunk in resp.iter_content(65536):
                if not chunk:
                    continue
                if not head:
                    head = chunk[:32]
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    return None, "", status, {}
                chunks.append(chunk)
            if not resolver_mod.is_image_response(status, rheaders, head):
                return None, "", status, {}
            ctype = rheaders.get("Content-Type") or ""
            if not ctype or ctype.lower().startswith("text/"):
                ctype = "image/jpeg"
            extra = {"Cache-Control": "public, max-age=86400", "Accept-Ranges": "none"}
            for hdr in ("ETag", "Last-Modified"):
                if rheaders.get(hdr):
                    extra[hdr] = rheaders.get(hdr)
            return b"".join(chunks), ctype, 200, extra
        finally:
            resp.close()

    def proxy_poster(self, film: dict, head_only: bool):
        """Afiş vekili: logo/yer tutucular elenmiş, öncelik sıralı adaylar.

        Önbellek -> film kaydı -> sayfa/ögme görseli -> CDN şablonları.
        Doğrulanan adres önbelleğe yazılır; sonraki istekler tek atışta döner.
        """
        fid = str(film.get("id") or "")
        item = RESOLVER.get_item(fid) if fid else None
        candidates = []
        if item and item.get("poster"):
            candidates.append((item["poster"], item.get("poster_ref") or None))
        for pair in RESOLVER.poster_candidates(film):
            if pair[0] not in [c[0] for c in candidates]:
                candidates.append(pair)
        tried = set()
        for url, referer in candidates[:16]:
            if url in tried:
                continue
            tried.add(url)
            data, ctype, status, extra = self._fetch_image(url, referer)
            if data is None:
                continue
            if fid and status == 200:
                RESOLVER.remember_poster(fid, url, referer)
            return self._send(status, ctype, data, extra=extra, cache=(status == 200),
                              head_only=head_only)
        # Son çare: sayfa/ögme görselini indirip dene (logo reddi uygulanmış)
        referers = [r for r in net.referer_candidates(film, RESOLVER.meta) if r][:3] or [None]
        for url in RESOLVER.refresh_posters_from_page(film):
            if url in tried:
                continue
            tried.add(url)
            for referer in referers:
                data, ctype, status, extra = self._fetch_image(url, referer)
                if data is None:
                    continue
                if fid and status == 200:
                    RESOLVER.remember_poster(fid, url, referer)
                return self._send(status, ctype, data, extra=extra, cache=(status == 200),
                                  head_only=head_only)
        return self._send(404, "text/plain; charset=utf-8", b"poster yok", head_only=head_only)

    # ------------------------------------------------------------------ #
    def stream_upstream(self, upstream, cand, film: dict, range_header, head_only: bool):
        headers = upstream.headers
        status = upstream.status_code
        url_is_hls = (cand.url or "").lower().split("?")[0].endswith(".m3u8")
        ctype = headers.get("Content-Type") or (
            "application/vnd.apple.mpegurl" if url_is_hls else "video/mp4")
        if url_is_hls or "mpegurl" in ctype.lower():
            return self.stream_hls(upstream, cand, film, head_only)

        try:
            total = int(headers.get("Content-Length") or 0)
        except ValueError:
            total = 0
        content_range = headers.get("Content-Range")
        accept_ranges = (headers.get("Accept-Ranges") or "").lower()
        send_status = status
        send_range = content_range
        send_length = total or None
        byte_budget = None
        leftover = b""

        # Upstream Range isteğini yok saydıysa (200 döndü) aralığı biz üretiriz;
        # aksi halde tarayıcıda ileri/geri sarma çalışmıyor.
        if range_header and status == 200 and total > 0 and config.EMULATE_RANGE:
            parsed_range = resolver_mod.parse_range(range_header, total)
            if parsed_range:
                start, end = parsed_range
                end = total - 1 if end is None else min(end, total - 1)
                if 0 <= start <= end < total:
                    skipped = self._skip_bytes(upstream, start)
                    if skipped is not None:
                        _skipped_bytes, pending = skipped
                        send_status = 206
                        send_range = f"bytes {start}-{end}/{total}"
                        send_length = end - start + 1
                        byte_budget = send_length
                        leftover = pending

        extra = {
            "X-Evoli-Source": cand.kind,
            "X-Evoli-Upstream": net.host_of(cand.url),
            "Cache-Control": headers.get("Cache-Control") or "no-cache",
        }
        if send_range:
            extra["Content-Range"] = send_range
        if accept_ranges == "bytes" or send_status == 206:
            extra["Accept-Ranges"] = "bytes"
        elif accept_ranges and accept_ranges != "none":
            extra["Accept-Ranges"] = accept_ranges
        for hdr in ("ETag", "Last-Modified", "Content-Disposition"):
            if headers.get(hdr):
                extra[hdr] = headers.get(hdr)

        self.send_response(send_status)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers",
                         "Content-Length, Content-Range, Accept-Ranges, X-Evoli-Source")
        for key, value in extra.items():
            if value is not None:
                self.send_header(key, str(value))
        if send_length:
            self.send_header("Content-Length", str(send_length))
        else:
            # Uzunluk bilinmiyorsa keep-alive yanıltıcı olur: bağlantıyı kapat.
            self.send_header("Connection", "close")
            self.close_connection = True
        if self._client_wants_close() and "Connection" not in extra:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if head_only:
            return

        remaining = byte_budget
        try:
            if leftover:
                if remaining is None:
                    self.wfile.write(leftover)
                else:
                    piece = leftover[:remaining]
                    self.wfile.write(piece)
                    remaining -= len(piece)
            if remaining is None or remaining > 0:
                for chunk in upstream.iter_content(config.PROXY_CHUNK):
                    if not chunk:
                        continue
                    if remaining is not None:
                        if len(chunk) > remaining:
                            chunk = chunk[:remaining]
                        remaining -= len(chunk)
                    self.wfile.write(chunk)
                    if remaining is not None and remaining <= 0:
                        break
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Tarayıcı sekmeyi/sekmeyi kapattı ya da seek için bağlantıyı attı.
            self.close_connection = True
        except Exception:
            self.close_connection = True

    def _skip_bytes(self, upstream, count: int):
        """Upstream Range desteklemiyorsa ``count`` baytı atla.

        Dönüş: ``(okunan_fazlalik, kalan_tampon)`` ya da hata durumunda ``None``.
        """
        if count <= 0:
            return (0, b"")
        skipped = 0
        try:
            for chunk in upstream.iter_content(min(65536, config.PROXY_CHUNK)):
                if not chunk:
                    continue
                if skipped + len(chunk) >= count:
                    take = count - skipped
                    return (skipped + take, chunk[take:])
                skipped += len(chunk)
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------ #
    def stream_hls(self, upstream, cand, film: dict, head_only: bool):
        fid = str(film.get("id") or "")
        try:
            body = upstream.content.decode("utf-8", "replace")
        finally:
            upstream.close()
        rewritten = rewrite_playlist(body, cand.url, fid)
        data = rewritten.encode("utf-8")
        return self._send(200, "application/vnd.apple.mpegurl", data,
                          extra={"X-Evoli-Source": cand.kind}, head_only=head_only)

    def proxy_hls(self, fid: str, index_raw: str, head_only: bool):
        try:
            index = int(index_raw)
        except ValueError:
            return self._send(400, "text/plain", b"bad index")
        film = find_film(fid)
        url = hls_target(fid, index)
        if not url:
            return self._send(404, "text/plain", b"segment yok")
        referer = (film or {}).get("embed") or (film or {}).get("url")
        headers = net.browser_headers(referer=referer, accept="*/*")
        range_header = self.headers.get("Range")
        if range_header:
            headers["Range"] = range_header
        try:
            resp = RESOLVER.session.get(url, headers=headers, stream=True, verify=False,
                                        timeout=config.TIMEOUT, allow_redirects=True)
        except Exception as exc:  # noqa: BLE001
            return self._send(502, "text/plain", f"upstream error: {exc}".encode()[:200])
        ctype = (resp.headers.get("Content-Type") or "").lower()
        try:
            if "mpegurl" in ctype or url.lower().split("?")[0].endswith(".m3u8"):
                body = resp.content.decode("utf-8", "replace")
                data = rewrite_playlist(body, url, fid).encode("utf-8")
                return self._send(200, "application/vnd.apple.mpegurl", data, head_only=head_only)
            data = resp.content
            extra = {}
            for hdr in ("Content-Range", "Accept-Ranges", "ETag", "Last-Modified"):
                if resp.headers.get(hdr):
                    extra[hdr] = resp.headers.get(hdr)
            return self._send(resp.status_code, resp.headers.get("Content-Type") or "video/mp2ts",
                              data, extra=extra, head_only=head_only)
        finally:
            resp.close()


# --------------------------------------------------------------------------- #
# Başlatma
# --------------------------------------------------------------------------- #
class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


def local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        sock.close()


def run(port: int = None, host: str = None, quiet: bool = False):
    port = port or config.PORT
    host = host or config.HOST
    db = get_db(force=True)
    films = db.get("films", [])
    meta = db.get("meta") or {}
    ip = local_ip()
    if not quiet:
        print("=" * 58)
        print("   EVOLI Premium Sunucu • v6")
        print("=" * 58)
        print(f"[✓] Katalog   : {len(films)} film • {len(db.get('categories', {}))} kategori")
        print(f"[✓] Oynatılabilir: {sum(1 for f in films if f.get('ok'))} • doğrulanmamış "
              f"{sum(1 for f in films if f.get('ok') is None)}")
        print(f"[✓] Site      : {meta.get('site') or config.SITE_HOME}")
        print(f"[✓] CDN       : {meta.get('cdn_host') or (meta.get('cdn_hosts') or ['?'])[0]}")
        print(f"[✓] Arayüz    : http://127.0.0.1:{port}")
        print(f"[✓] Ağ        : http://{ip}:{port}")
        print(f"[✓] M3U       : http://127.0.0.1:{port}/m3u  (harici)  •  /playlist_local.m3u (proxy)")
        print(f"[✓] CORS vekil: /proxy?url=…&ref=…  •  hls.js: /hls.js")
        print(f"[✓] API       : /api/films • /api/stats • /api/status • /api/resolve/<id> • /api/repair")
        print("")
    try:
        Server((host, port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Sunucu durduruldu")
    except OSError as exc:
        print(f"[!] Sunucu başlatılamadı: {exc}")


if __name__ == "__main__":
    run()
