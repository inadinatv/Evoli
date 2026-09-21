# -*- coding: utf-8 -*-
"""Medya çözücü (resolver).

Bir filmin *çalışan* video adresini bulmaktan sorumlu tek yer.  Eski kod
``films.json`` içine tarama anında bulunan tek bir URL yazıyor ve oynatma
sırasında o URL 403/404 verirse hiçbir şey yapılamıyordu.  CDN alan adı
döndüğü (rotating) ve Referer kontrolü yaptığı için eski kayıtlar zamanla
ölüyor; sonuç: "bazı videolar oynatmıyor".

Burada her film için öncelik sıralı *aday* listesi üretilir:

1. daha önce çalıştığı doğrulanmış önbellek kaydı
2. ``films.json``'daki adres + farklı Referer varyasyonları
3. öğrenilen CDN alan adlarıyla şablondan üretilen adresler (``{host}/{id}.mp4``)
4. film sayfası -> gömme sayfası -> oynatıcı kaynağı (canlı yeniden çözüm)

İlk çalışan aday önbelleğe yazılır; sonraki istekler tek atışta döner.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from collections import Counter
from typing import Iterable, Optional

import config
import extract
import net

VIDEO_CT = ("video/", "application/octet-stream", "mpegurl", "dash+xml", "audio/")
BAD_CT = ("text/html", "application/json", "text/xml", "text/plain")
IMAGE_CT = ("image/",)
IMAGE_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),
    (b"BM", "image/bmp"),
)


def is_image_response(status: int, headers: dict, head: bytes = b"") -> bool:
    """Yanıt gerçekten bir görsel mi?  Bazı CDN'ler yanlış Content-Type
    döndürdüğü için imza (magic bytes) da kontrol edilir."""
    if status not in (200, 206):
        return False
    ctype = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
    if any(bad in ctype for bad in BAD_CT):
        return False
    if any(good in ctype for good in IMAGE_CT):
        return True
    if head:
        for magic, _name in IMAGE_MAGIC:
            if head.startswith(magic):
                return True
    return False


class Candidate:
    __slots__ = ("url", "referer", "ua", "kind")

    def __init__(self, url: str, referer: Optional[str] = None, ua: Optional[str] = None, kind: str = "stored"):
        self.url = url
        self.referer = referer
        self.ua = ua
        self.kind = kind

    def __repr__(self) -> str:  # pragma: no cover - hata ayıklama
        return f"<Candidate {self.kind} {self.url} ref={self.referer!r}>"


def is_media_response(status: int, headers: dict) -> bool:
    """Upstream yanıtı gerçekten video mu, yoksa HTML hata sayfası mı?"""
    if status not in (200, 206):
        return False
    ctype = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
    if any(bad in ctype for bad in BAD_CT):
        return False
    if any(good in ctype for good in VIDEO_CT):
        return True
    try:
        length = int(headers.get("Content-Length") or headers.get("content-length") or 0)
    except ValueError:
        length = 0
    if length >= 4096:
        return True
    return bool(ctype)


def parse_range(value: Optional[str], total: Optional[int]) -> Optional[tuple]:
    """``bytes=100-200`` -> (start, end).  Toplam bilinmiyorsa end None olur."""
    if not value:
        return None
    m = re.match(r"bytes\s*=\s*(\d*)\s*-\s*(\d*)", value.strip(), re.I)
    if not m:
        return None
    start_s, end_s = m.group(1), m.group(2)
    if start_s == "" and end_s == "":
        return None
    if start_s == "":  # son N bayt
        suffix = int(end_s)
        if not total:
            return None
        return max(0, total - suffix), total - 1
    start = int(start_s)
    end = int(end_s) if end_s else (total - 1 if total else None)
    if end is not None and total:
        end = min(end, total - 1)
    if end is not None and end < start:
        return None
    return start, end


class MediaResolver:
    """Film -> çalışan medya adresi eşlemesi (önbellekli, çok adaylı)."""

    def __init__(self, session=None, cache_file: Optional[str] = None, autosave: bool = True):
        # Kaynak denemelerinde tekrar (retry) istemiyoruz: bir aday başarısızsa
        # vakit kaybetmeden sıradakine geçmek oynatma gecikmesini azaltır.
        self.session = session or net.make_session(retries=config.RESOLVER_RETRIES)
        self.cache_file = cache_file or config.MEDIA_CACHE
        self.autosave = autosave
        self._lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._cache = {"meta": {}, "items": {}}
        self._dirty = False
        self.stats = Counter()
        self._host_failures: dict = {}
        self.load()

    # ------------------------------------------------------------------ #
    # Devre kesici: erişilemeyen alan adlarını kısa süreliğine atla
    # ------------------------------------------------------------------ #
    def host_blocked(self, url: str) -> bool:
        host = net.host_of(url)
        if not host:
            return False
        with self._lock:
            entry = self._host_failures.get(host)
            if not entry:
                return False
            count, stamp = entry
            if time.time() - stamp > config.HOST_COOLDOWN:
                self._host_failures.pop(host, None)
                return False
            return count >= config.HOST_FAIL_THRESHOLD

    def note_host_result(self, url: str, status: int):
        """status=0 → bağlantı kurulamadı (alan adını say); >0 → sunucu cevap verdi."""
        host = net.host_of(url)
        if not host:
            return
        with self._lock:
            if status:
                self._host_failures.pop(host, None)
                return
            count, stamp = self._host_failures.get(host, (0, time.time()))
            if time.time() - stamp > config.HOST_COOLDOWN:
                count, stamp = 0, time.time()
            self._host_failures[host] = (count + 1, stamp)

    def blocked_hosts(self) -> dict:
        with self._lock:
            now = time.time()
            return {host: {"fails": count, "cooldown_left": max(0, int(config.HOST_COOLDOWN - (now - stamp)))}
                    for host, (count, stamp) in self._host_failures.items()
                    if now - stamp <= config.HOST_COOLDOWN}

    # ------------------------------------------------------------------ #
    # Önbellek
    # ------------------------------------------------------------------ #
    def load(self):
        if not self.cache_file or not os.path.exists(self.cache_file):
            return
        try:
            with open(self.cache_file, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                self._cache = {
                    "meta": data.get("meta") or {},
                    "items": {str(k): v for k, v in (data.get("items") or {}).items()},
                }
        except Exception:
            self._cache = {"meta": {}, "items": {}}

    def save(self, force: bool = False):
        if not self.cache_file:
            return
        with self._lock:
            if not (self._dirty or force):
                return
            payload = json.dumps(self._cache, ensure_ascii=False)
            self._dirty = False
        tmp = None
        try:
            with self._save_lock:
                directory = os.path.dirname(self.cache_file) or "."
                fd, tmp = tempfile.mkstemp(prefix=".media_cache-", dir=directory)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                os.chmod(tmp, 0o644)
                os.replace(tmp, self.cache_file)
                tmp = None
        except Exception:
            pass
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    @property
    def meta(self) -> dict:
        return self._cache.setdefault("meta", {})

    def get_item(self, film_id: str) -> Optional[dict]:
        with self._lock:
            item = self._cache["items"].get(str(film_id))
            return dict(item) if item else None

    def remember(self, film_id: str, url: str, referer: Optional[str], ua: Optional[str],
                 ok: bool, status: int = 0, kind: str = ""):
        if not film_id or not url:
            return
        with self._lock:
            item = self._cache["items"].setdefault(str(film_id), {})
            if ok:
                item.update({
                    "url": url,
                    "ref": referer or "",
                    "ua": ua or "",
                    "ok": True,
                    "status": status,
                    "kind": kind,
                    "ts": int(time.time()),
                    "host": net.host_of(url),
                    "fails": 0,
                })
                self.note_cdn_host(url)
            else:
                item["ok"] = False
                item["fails"] = int(item.get("fails") or 0) + 1
                item["fail_ts"] = int(time.time())
                item["last_status"] = status
                if url and not item.get("url"):
                    item["url"] = url
            self._dirty = True
        if self.autosave:
            self.save()

    def remember_poster(self, film_id: str, url: str, referer: Optional[str]):
        """Doğrulanan afiş adresini önbelleğe yazar (/poster/<id> tek atışta dönsün)."""
        if not film_id or not url:
            return
        with self._lock:
            item = self._cache["items"].setdefault(str(film_id), {})
            item["poster"] = url
            item["poster_ref"] = referer or ""
            item["poster_ts"] = int(time.time())
            self._dirty = True
        if self.autosave:
            self.save()

    def note_cdn_host(self, url: str):
        host = net.host_of(url)
        if not host:
            return
        with self._lock:
            hosts = self.meta.setdefault("cdn_hosts", [])
            if host in hosts:
                hosts.remove(host)
            hosts.insert(0, host)
            self.meta["cdn_host"] = host
            del hosts[12:]
            self._dirty = True

    def note_site(self, site: str):
        if not site:
            return
        with self._lock:
            self.meta["site"] = site
            hosts = self.meta.setdefault("site_hosts", [])
            host = net.host_of(site)
            if host and host not in hosts:
                hosts.insert(0, host)
                del hosts[8:]
            self._dirty = True

    def cache_fresh(self, item: dict, ok_ttl=None, fail_ttl=None) -> bool:
        now = int(time.time())
        if item.get("ok"):
            ttl = ok_ttl if ok_ttl is not None else config.CACHE_TTL_OK
            stamp = item.get("ts") or 0
        else:
            ttl = fail_ttl if fail_ttl is not None else config.CACHE_TTL_FAIL
            stamp = item.get("fail_ts") or item.get("ts") or 0
        return bool(ttl) and (now - stamp) < ttl

    # ------------------------------------------------------------------ #
    # Aday üretimi
    # ------------------------------------------------------------------ #
    def cdn_hosts(self, film: Optional[dict] = None) -> list:
        hosts: list = []

        def push(host):
            host = (host or "").strip().lower()
            if host.startswith("http"):
                host = net.host_of(host)
            host = host.split("/")[0]
            if host and host not in hosts:
                hosts.append(host)

        with self._lock:
            push(self.meta.get("cdn_host"))
            for host in self.meta.get("cdn_hosts") or []:
                push(host)
        if film:
            push(net.host_of(film.get("stream") or ""))
            push(net.host_of(film.get("host") or ""))
            push(film.get("cdn_host"))
        for host in config.CDN_HOSTS:
            push(host)
        return hosts

    @staticmethod
    def video_id(film: dict) -> str:
        for key in ("video_id", "id"):
            value = str(film.get(key) or "").strip()
            if value.isdigit():
                return value
        stream = film.get("stream") or ""
        match = re.search(r"/(\d{3,8})\.(?:mp4|m3u8|webm)", stream)
        if match:
            return match.group(1)
        return str(film.get("id") or "").strip()

    def _template_urls(self, film: dict) -> list:
        vid = self.video_id(film)
        if not vid or not vid.isdigit():
            return []
        urls = []
        for host in self.cdn_hosts(film)[:4]:
            for template in config.CDN_VIDEO_TEMPLATES[:2]:
                url = template.format(host=host, id=vid)
                if url not in urls:
                    urls.append(url)
        return urls

    def candidates(self, film: dict, include_page: bool = False,
                   max_candidates: Optional[int] = None) -> list:
        """Öncelik sıralı (url, referer, ua) adayları."""
        max_candidates = max_candidates or config.MAX_RESOLVE_CANDIDATES
        fid = str(film.get("id") or "")
        referers = net.referer_candidates(film, self.meta)
        primary_refs = [r for r in referers if r][:3] + [""]
        out: list = []
        seen = set()

        def push(url, referer=None, ua=None, kind="stored"):
            if not url:
                return
            key = (url, referer or "", ua or "")
            if key in seen:
                return
            seen.add(key)
            out.append(Candidate(url, referer, ua, kind))

        item = self.get_item(fid)
        if item and item.get("url"):
            if item.get("ok") and self.cache_fresh(item):
                push(item["url"], item.get("ref"), item.get("ua") or None, "cache")
            elif not item.get("ok"):
                # başarısız kayıt: en sona bırakılır ama listede kalır
                pass

        stored = film.get("stream") or ""
        if stored:
            for ref in primary_refs:
                push(stored, ref, film.get("ua") or None, "stored")
            if film.get("ref") and film.get("ref") not in primary_refs:
                push(stored, film.get("ref"), None, "stored-ref")

        for alt in film.get("alt_streams") or []:
            for ref in primary_refs[:2]:
                push(alt, ref, None, "alt")

        for url in self._template_urls(film):
            if url == stored:
                continue
            push(url, primary_refs[0] if primary_refs else None, None, "template")
            if item and item.get("ok") and self.cache_fresh(item):
                break  # önbellek taze ise şablonları fazladan denemeye gerek yok

        if item and item.get("url") and not item.get("ok"):
            push(item["url"], item.get("ref"), item.get("ua") or None, "cache-fail")

        if include_page and fid:
            # Canlı yeniden çözüm (sayfa -> gömme -> oynatıcı) en son çare.
            for url, referer in self.refresh_from_page(film, probe=False):
                push(url, referer, None, "page")

        return out[:max(1, max_candidates)]

    # ------------------------------------------------------------------ #
    # Ağ işlemleri
    # ------------------------------------------------------------------ #
    def probe(self, cand: Candidate, timeout=None) -> tuple:
        """Küçük bir Range isteğiyle adayın çalışıp çalışmadığını dener."""
        if self.host_blocked(cand.url):
            self.stats["probe_skipped_cooldown"] += 1
            return False, 0, {"skipped": "host-cooldown"}
        timeout = timeout or config.PROBE_TIMEOUT
        headers = net.browser_headers(
            referer=cand.referer or None,
            ua=cand.ua or None,
            range_header=f"bytes=0-{max(0, config.PROBE_BYTES - 1)}",
            accept="video/*,*/*;q=0.8",
        )
        started = time.time()
        try:
            resp = self.session.get(cand.url, headers=headers, timeout=timeout,
                                    verify=False, allow_redirects=True, stream=True)
        except Exception as exc:  # noqa: BLE001
            self.stats["probe_error"] += 1
            self.note_host_result(cand.url, 0)
            return False, 0, {"error": repr(exc)[:160], "elapsed": time.time() - started}
        try:
            status = resp.status_code
            self.note_host_result(cand.url, status)
            rheaders = dict(resp.headers)
            ok = is_media_response(status, rheaders)
            if ok:
                try:
                    resp.raw.read(1, decode_content=False)
                except Exception:
                    pass
            rheaders["elapsed"] = time.time() - started
            return ok, status, rheaders
        finally:
            resp.close()

    def probe_image(self, url: str, referer: Optional[str] = None,
                    ua: Optional[str] = None, timeout=None) -> tuple:
        """Bir afiş/görsel adresinin gerçekten görsel döndürüp döndürmediğini dener.

        Dönüş: ``(ok, status, content_type, head_bytes)``.
        """
        if self.host_blocked(url):
            return False, 0, "", b""
        timeout = timeout or config.PROBE_TIMEOUT
        headers = net.browser_headers(referer=referer or None, ua=ua or None,
                                      range_header="bytes=0-2047",
                                      accept="image/*,*/*;q=0.8")
        try:
            resp = self.session.get(url, headers=headers, timeout=timeout,
                                    verify=False, allow_redirects=True, stream=True)
        except Exception:  # noqa: BLE001
            self.note_host_result(url, 0)
            return False, 0, "", b""
        try:
            status = resp.status_code
            self.note_host_result(url, status)
            rheaders = dict(resp.headers)
            head = b""
            if status in (200, 206):
                try:
                    head = resp.raw.read(32, decode_content=True) or b""
                except Exception:
                    head = b""
            ctype = rheaders.get("Content-Type") or ""
            return is_image_response(status, rheaders, head), status, ctype, head
        finally:
            resp.close()

    def poster_candidates(self, film: dict) -> list:
        """Film için denenecek *ucuz* afiş adayları ``[(url, referer), ...]``.

        Önbellek -> film kaydı -> CDN şablonları.  Sayfa indirme yoktur; o iş
        (son çare) ``resolve_poster`` / ``refresh_posters_from_page`` tarafında.
        """
        out: list = []
        seen = set()
        referers = [r for r in net.referer_candidates(film, self.meta) if r][:3] + [None]

        def push(url):
            url = (url or "").strip()
            if not url or not url.lower().startswith("http") or url in seen:
                return
            if extract.is_generic_image(url):
                return
            seen.add(url)
            for ref in referers:
                out.append((url, ref))

        item = self.get_item(str(film.get("id") or ""))
        if item and item.get("poster"):
            push(item["poster"])
        push(film.get("poster"))
        push(film.get("poster_alt"))
        vid = self.video_id(film)
        if vid and vid.isdigit():
            for host in self.cdn_hosts(film)[:4]:
                for tpl in config.CDN_POSTER_TEMPLATES:
                    push(tpl.format(host=host, id=vid))
        return out

    def resolve_poster(self, film: dict, allow_page: bool = True) -> dict:
        """Çalışan afiş adresini bulur ve önbelleğe yazar.

        Sıra: ucuz adaylar (önbellek/kayıt/şablon) -> son çare sayfa/ögme
        görselleri.  Dönüş: ``{ok, url, referer, content_type, tried}``.
        """
        result = {"ok": False, "url": "", "referer": None, "content_type": "", "tried": []}
        referers = [r for r in net.referer_candidates(film, self.meta) if r][:3] + [None]

        def attempt(url, referer) -> bool:
            ok, status, ctype, _head = self.probe_image(url, referer)
            result["tried"].append({"url": url, "referer": referer, "status": status, "ok": ok})
            if ok:
                result.update({"ok": True, "url": url, "referer": referer, "content_type": ctype})
                fid = str(film.get("id") or "")
                if fid:
                    self.remember_poster(fid, url, referer)
                self.stats["poster_ok"] += 1
                return True
            return False

        for url, referer in self.poster_candidates(film):
            if attempt(url, referer):
                return result
        if allow_page:
            for url in self.refresh_posters_from_page(film):
                for referer in referers:
                    if attempt(url, referer):
                        return result
        self.stats["poster_fail"] += 1
        return result

    def refresh_posters_from_page(self, film: dict) -> list:
        """Film/gömme sayfasından gerçek afış adayları (logo vb. elenmiş)."""
        page_url = film.get("url") or ""
        if not page_url or self.host_blocked(page_url):
            return []
        result = net.fetch_first_working(self.session, net.mirror_urls(page_url),
                                         referer=self.meta.get("site") or config.SITE_HOME)
        if not result.ok:
            return []
        vid = extract.find_video_id(result.text, result.url or page_url) or self.video_id(film)
        posters = extract.find_poster(result.text, result.url or page_url, vid)
        for embed in extract.find_embed_urls(result.text, result.url or page_url)[:2]:
            embed_result = net.fetch(self.session, embed, referer=page_url, tries=1)
            if not embed_result.ok:
                continue
            for poster in extract.find_poster(embed_result.text, embed_result.url or embed, vid):
                if poster not in posters:
                    posters.append(poster)
        return posters

    def _try_open(self, cand: Candidate, film: dict, range_header: Optional[str], tried: list):
        if self.host_blocked(cand.url):
            tried.append({"url": cand.url, "kind": cand.kind, "referer": cand.referer,
                          "status": 0, "skipped": "host-cooldown"})
            self.stats["open_skipped_cooldown"] += 1
            return None
        headers = net.browser_headers(
            referer=cand.referer or None,
            ua=cand.ua or None,
            range_header=range_header,
            accept="video/*,*/*;q=0.8",
        )
        try:
            resp = self.session.get(cand.url, headers=headers, stream=True, verify=False,
                                    allow_redirects=True, timeout=config.OPEN_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            tried.append({"url": cand.url, "kind": cand.kind, "referer": cand.referer,
                          "error": repr(exc)[:120]})
            self.stats["open_error"] += 1
            self.note_host_result(cand.url, 0)
            self.remember(str(film.get("id") or ""), cand.url, cand.referer, cand.ua,
                          ok=False, status=0, kind=cand.kind)
            return None
        self.note_host_result(cand.url, resp.status_code)
        if is_media_response(resp.status_code, dict(resp.headers)):
            self.stats["open_ok:" + cand.kind] += 1
            self.remember(str(film.get("id") or ""), cand.url, cand.referer, cand.ua,
                          ok=True, status=resp.status_code, kind=cand.kind)
            return resp
        tried.append({"url": cand.url, "kind": cand.kind, "referer": cand.referer,
                      "status": resp.status_code})
        self.stats["open_fail"] += 1
        self.remember(str(film.get("id") or ""), cand.url, cand.referer, cand.ua,
                      ok=False, status=resp.status_code, kind=cand.kind)
        resp.close()
        return None

    def open_stream(self, film: dict, range_header: Optional[str] = None,
                    include_page: bool = True, max_candidates: Optional[int] = None):
        """Sunucu için: ilk çalışan upstream yanıtını açık halde döner.

        Dönüş: ``(response, candidate, tried)``.  Ucuz adaylar (önbellek,
        kayıtlı adres, şablon) önce denenir; hiçbiri çalışmazsa *son çare*
        olarak filmin sayfası yeniden indirilir.  Böylece normal oynatma tek
        istekte başlar.
        """
        tried: list = []
        seen = set()
        for cand in self.candidates(film, include_page=False, max_candidates=max_candidates):
            key = (cand.url, cand.referer or "")
            if key in seen:
                continue
            seen.add(key)
            resp = self._try_open(cand, film, range_header, tried)
            if resp is not None:
                return resp, cand, tried

        if include_page:
            for url, referer in self.refresh_from_page(film, probe=False):
                key = (url, referer or "")
                if key in seen:
                    continue
                seen.add(key)
                cand = Candidate(url, referer, None, "page")
                resp = self._try_open(cand, film, range_header, tried)
                if resp is not None:
                    return resp, cand, tried
        return None, None, tried

    def resolve(self, film: dict, force: bool = False, max_candidates: Optional[int] = None) -> dict:
        """Film için çalışan kaynağı bul ve önbelleğe yaz (doğrulama/onarım)."""
        fid = str(film.get("id") or "")
        item = self.get_item(fid)
        if item and item.get("ok") and not force and self.cache_fresh(item):
            return {"ok": True, "url": item["url"], "referer": item.get("ref"), "ua": item.get("ua"),
                    "status": item.get("status", 200), "kind": "cache", "cached": True}

        result = {"ok": False, "url": "", "referer": None, "ua": None, "status": 0,
                  "kind": "", "tried": [], "unreachable": True}

        def attempt(cand: Candidate) -> bool:
            ok, status, headers = self.probe(cand)
            if status:
                # Gerçek bir HTTP cevabı aldık: ağ erişilebilir demektir.
                result["unreachable"] = False
            result["tried"].append({"url": cand.url, "kind": cand.kind, "referer": cand.referer,
                                    "status": status, "ok": ok})
            self.remember(fid, cand.url, cand.referer, cand.ua, ok=ok, status=status, kind=cand.kind)
            if ok:
                result.update({"ok": True, "url": cand.url, "referer": cand.referer,
                               "ua": cand.ua, "status": status, "kind": cand.kind,
                               "content_type": headers.get("Content-Type", ""),
                               "content_length": headers.get("Content-Length", "")})
                self.stats["resolve_ok:" + cand.kind] += 1
                return True
            return False

        seen = set()
        for cand in self.candidates(film, include_page=False, max_candidates=max_candidates):
            key = (cand.url, cand.referer or "")
            if key in seen:
                continue
            seen.add(key)
            if attempt(cand):
                return result

        # Ucuz adaylar tükendi: sayfadan canlı çözüm (son çare).
        for url, referer in self.refresh_from_page(film, probe=False):
            key = (url, referer or "")
            if key in seen:
                continue
            seen.add(key)
            if attempt(Candidate(url, referer, None, "page")):
                return result
        self.stats["resolve_fail"] += 1
        return result

    # ------------------------------------------------------------------ #
    # Canlı yeniden çözüm
    # ------------------------------------------------------------------ #
    def refresh_from_page(self, film: dict, probe: bool = True) -> list:
        """Film sayfasını yeniden indirip oynatıcı kaynağını bulur.

        ``[(url, referer), ...]`` döner (öncelik sıralı).  Bulunan gömme
        adresi ve video numarası film sözlüğüne de yazılır.
        """
        page_url = film.get("url") or ""
        if not page_url:
            return []
        if self.host_blocked(page_url):
            self.stats["page_skipped_cooldown"] += 1
            return []
        result = net.fetch_first_working(self.session, net.mirror_urls(page_url),
                                         referer=self.meta.get("site") or config.SITE_HOME)
        self.note_host_result(page_url, result.status if result else 0)
        if not result.ok:
            self.stats["page_fetch_fail"] += 1
            return []
        home = extract.find_site_home(result.text, result.url or page_url)
        if home:
            self.note_site(home)

        embeds = extract.find_embed_urls(result.text, result.url or page_url)
        vid = extract.find_video_id(result.text, result.url or page_url) or self.video_id(film)
        media = extract.find_media_urls(result.text, result.url or page_url, vid)
        poster_page = result.url or page_url

        for embed in embeds[:3]:
            embed_result = net.fetch(self.session, embed, referer=page_url, tries=2)
            if not embed_result.ok:
                embed_result = net.fetch_first_working(self.session, net.mirror_urls(embed), referer=page_url)
            if not embed_result.ok:
                continue
            if not vid:
                vid = extract.find_video_id(embed_result.text, embed, page_url)
            film.setdefault("embed", embed_result.url or embed)
            for item in extract.find_media_urls(embed_result.text, embed_result.url or embed, vid):
                if item["url"] not in [m["url"] for m in media]:
                    item["score"] += 3  # gömme sayfasındaki kaynak daha güvenilirdir
                    media.append(item)
            for poster in extract.find_poster(embed_result.text, embed_result.url or embed, vid)[:2]:
                film.setdefault("poster_alt", poster)

        if vid:
            film["video_id"] = vid
            if not film.get("id") or not str(film.get("id")).isdigit():
                film["id"] = vid
            for host in self.cdn_hosts(film)[:3]:
                for template in config.CDN_VIDEO_TEMPLATES:
                    url = template.format(host=host, id=vid)
                    if url not in [m["url"] for m in media]:
                        media.append({"url": url, "kind": "template", "score": 1})

        media.sort(key=lambda item: -item["score"])
        referer = film.get("embed") or poster_page
        pairs = [(item["url"], referer) for item in media[: config.MAX_PROXY_CANDIDATES]]
        if not pairs:
            return []
        if probe:
            for url, ref in pairs:
                ok, status, _headers = self.probe(Candidate(url, ref, None, "page"))
                if ok:
                    self.remember(str(film.get("id") or ""), url, ref, None, ok=True,
                                  status=status, kind="page")
                    return [(url, ref)]
                self.remember(str(film.get("id") or ""), url, ref, None, ok=False,
                              status=status, kind="page")
            return []
        return pairs

    # ------------------------------------------------------------------ #
    # Toplu onarım
    # ------------------------------------------------------------------ #
    def repair(self, films: Iterable[dict], limit: Optional[int] = None,
               workers: Optional[int] = None, progress=None,
               only_broken: bool = True) -> dict:
        """Filmleri doğrular, ölü kayıtları yeniden çözer."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        films = list(films)
        if only_broken:
            pending = [f for f in films if not f.get("ok") or self._needs_recheck(f)]
        else:
            pending = list(films)
        if limit:
            pending = pending[:limit]
        workers = max(1, min(workers or config.SCAN_WORKERS, 16))
        stats = {"checked": 0, "fixed": 0, "dead": 0, "unreachable": 0, "total": len(pending)}
        lock = threading.Lock()

        def work(film):
            resolved = self.resolve(film, force=True)
            with lock:
                stats["checked"] += 1
                stamp = time.strftime("%Y-%m-%d %H:%M")
                if resolved["ok"]:
                    stats["fixed"] += 1
                    film["stream"] = resolved["url"]
                    film["ref"] = resolved.get("referer") or ""
                    film["host"] = net.host_of(resolved["url"])
                    film["ok"] = True
                    film["checked"] = stamp
                elif resolved.get("unreachable"):
                    # Ağ/CDN bu makineden hiç cevap vermedi (ör. CI, VPN, DNS).
                    # Kaydı "bozuk" diye işaretlemek yanlış olur — bilinmiyor bırak.
                    stats["unreachable"] = stats.get("unreachable", 0) + 1
                    film.pop("ok", None)
                    film["checked"] = stamp
                else:
                    stats["dead"] += 1
                    film["ok"] = False
                    film["checked"] = stamp
                    film["last_status"] = resolved.get("status") or 0
                if progress and stats["checked"] % 10 == 0:
                    progress(stats)
            return resolved["ok"]

        if not pending:
            return stats
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(work, film) for film in pending]
                for _future in as_completed(futures):
                    pass
        except KeyboardInterrupt:  # pragma: no cover
            pass
        self.save(force=True)
        return stats

    def _needs_recheck(self, film: dict) -> bool:
        """Başarısız kayıtlar her zaman, başarılılar haftada bir yeniden denenir."""
        item = self.get_item(str(film.get("id") or ""))
        if not item:
            return True
        if not item.get("ok"):
            return True
        return (time.time() - int(item.get("ts") or 0)) > 7 * 24 * 3600

    # ------------------------------------------------------------------ #
    # films.json ile bütünleşme
    # ------------------------------------------------------------------ #
    def best_referer(self, default: str = "") -> str:
        """Önbellekte en çok işe yaramış Referer (M3U ve varsayılanlar için)."""
        counts: Counter = Counter()
        with self._lock:
            for item in self._cache["items"].values():
                if item.get("ok") and item.get("ref"):
                    counts[item["ref"]] += 1
            meta_ref = self.meta.get("referer")
        if counts:
            return counts.most_common(1)[0][0]
        return meta_ref or default

    @staticmethod
    def tried_reachable(tried: Iterable[dict]) -> bool:
        """Denemelerden en az biri gerçek HTTP cevabı aldı mı?"""
        return any(bool(item.get("status")) for item in (tried or []))

    def apply_cache_to_films(self, films: Iterable[dict]) -> int:
        """Önbellekteki doğrulanmış adresleri film kayıtlarına işler."""
        changed = 0
        for film in films:
            item = self.get_item(str(film.get("id") or ""))
            if not item or not item.get("ok") or not item.get("url"):
                continue
            if film.get("stream") != item["url"]:
                film["stream"] = item["url"]
                changed += 1
            if item.get("ref") and film.get("ref") != item["ref"]:
                film["ref"] = item["ref"]
                changed += 1
            host = net.host_of(item["url"])
            if host and film.get("host") != host:
                film["host"] = host
            film["ok"] = True
        return changed

    def export_meta(self) -> dict:
        with self._lock:
            meta = dict(self.meta)
        meta["stats"] = dict(self.stats)
        return meta
