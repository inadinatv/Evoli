# -*- coding: utf-8 -*-
"""Tam site tarayıcısı.

Keşif sırası (hepsi ``config.DISCOVERY_MODE`` ile kontrol edilir):

1. **Sitemap**  — sitenin ``sitemap.xml`` dosyası tüm arşivi (2016'dan bugüne
   on binlerce film) tek seferde verir.  Eski sürüm bunu hiç kullanmıyordu.
2. **WordPress REST API** — ``/wp-json/wp/v2/posts`` başlık/tarih/kategori
   bilgisini toplu getirir.
3. **HTML tarama** — eski yöntem; sadece ilk ikisi boş dönerse devreye girer.
   (Eski koddaki kategori sayfalama hatası da düzeltildi: ``/page/N/`` yerine
   ``...-opage/N/`` üretiliyordu, bu yüzden kategorilerin 2. sayfadan sonrası
   hiç taranamıyordu.)

Bulunan her film için video numarası çıkarılır, kaynak adresi şablondan
üretilir ve örneklem üzerinden doğrulanır.  CDN alan adı döndüğünde tüm eski
kayıtlar otomatik olarak yeni alan adına taşınır (``retarget_streams``).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Optional
from urllib.parse import quote, urlparse

import config
import extract
import net
import resolver as resolver_mod

_print_lock = threading.Lock()


def log(message: str = ""):
    with _print_lock:
        print(message, flush=True)


# --------------------------------------------------------------------------- #
# Veritabanı
# --------------------------------------------------------------------------- #
def empty_db() -> dict:
    return {"updated": "", "total": 0, "films": [], "categories": {}, "meta": {}}


def load_db(path: Optional[str] = None) -> dict:
    path = path or config.FILMS_JSON
    if not os.path.exists(path):
        return empty_db()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        # Bozuk dosya her şeyi sıfırlamasın: yedekten dön
        backup = path + ".bak"
        if os.path.exists(backup):
            try:
                with open(backup, encoding="utf-8") as handle:
                    data = json.load(handle)
            except Exception:
                return empty_db()
        else:
            return empty_db()
    if isinstance(data, list):  # çok eski biçim
        data = {"films": data}
    db = empty_db()
    db.update({k: v for k, v in data.items() if k in db})
    db["films"] = [f for f in (db.get("films") or []) if isinstance(f, dict) and f.get("id")]
    db.setdefault("meta", {})
    db.setdefault("categories", {})
    return db


def save_db(db: dict, path: Optional[str] = None):
    """films.json'u atomik yazar (sunucu okurken dosya bozulmaz)."""
    path = path or config.FILMS_JSON
    db["total"] = len(db.get("films", []))
    db["updated"] = time.strftime("%Y-%m-%d %H:%M")
    payload = json.dumps(db, ensure_ascii=False,
                         indent=1 if getattr(config, "PRETTY_JSON", False) else None,
                         separators=None if getattr(config, "PRETTY_JSON", False) else (",", ":"))
    directory = os.path.dirname(path) or "."
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".films-", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.chmod(tmp, 0o644)
        if os.path.exists(path):
            try:
                os.replace(path, path + ".bak")
            except OSError:
                pass
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def slug_of(url: str) -> str:
    return (url or "").rstrip("/").split("/")[-1]


def stable_id(value: str) -> str:
    """Deterministik yedek kimlik (eski ``hash()`` her çalıştırmada değişiyordu)."""
    return "s" + hashlib.blake2b((value or "").encode("utf-8"), digest_size=6).hexdigest()


# --------------------------------------------------------------------------- #
# Keşif: sitemap
# --------------------------------------------------------------------------- #
def _sitemap_roots(meta: dict) -> list:
    roots = []
    for base in [meta.get("site"), config.SITE_HOME, config.BASE_URL]:
        base = (base or "").rstrip("/")
        if base and base not in roots:
            roots.append(base)
    return roots


def discover_sitemap(session, meta: dict, limit: int = 0, verbose: bool = True) -> list:
    """Sitemap'lerden tüm film adreslerini çıkarır: ``[(url, lastmod), ...]``."""
    index_urls = []
    for root in _sitemap_roots(meta):
        for path in config.SITEMAP_PATHS:
            index_urls.append(root + path)

    index_text = None
    for url in index_urls:
        result = net.fetch(session, url, referer=meta.get("site") or config.SITE_HOME,
                           accept="application/xml,text/xml,*/*")
        if result.ok and "<loc>" in result.text:
            index_text = result.text
            meta["sitemap_index"] = result.url or url
            if verbose:
                log(f"  [✓] Sitemap bulundu: {result.url or url}")
            break
    if not index_text:
        return []

    submaps, pages = extract.split_sitemap_urls(extract.sitemap_locs(index_text))
    # Film listeleri en yeni aydan başlanır; kategori/etiket sitemap'leri atlanır.
    def rank(name: str) -> tuple:
        match = re.search(r"(\d{4})-(\d{2})", name)
        if match:
            return (0, -int(match.group(1)) * 100 - int(match.group(2)))
        return (1, 0)

    skip = ("tax", "category", "kategori", "author", "page-sitemap", "misc", "tag", "etiket")
    postmaps = [u for u in submaps if not any(s in u.lower() for s in skip)]
    postmaps.sort(key=lambda u: (rank(u), u))
    others = [u for u in submaps if u not in postmaps]

    found: list = []
    seen = set()

    def collect(url: str, text: str):
        entries = extract.sitemap_entries(text)
        if extract.is_sitemap_index(text):
            return [loc for loc, _mod in entries if loc.lower().endswith(".xml")]
        hosts = meta.get("site_hosts") or config.MIRROR_HOSTS
        for loc, lastmod in entries:
            if loc in seen:
                continue
            if not extract.looks_like_film_url(loc, hosts):
                continue
            seen.add(loc)
            found.append((loc, lastmod))
            if not hosts or len(hosts) < 6:
                host = net.host_of(loc)
                if host and host not in hosts:
                    hosts = list(hosts) + [host]
                    meta["site_hosts"] = hosts
        return []

    def fetch_map(url: str) -> tuple:
        result = net.fetch(session, url, referer=meta.get("site") or config.SITE_HOME,
                           accept="application/xml,text/xml,*/*")
        return url, (result.text if result.ok else "")

    queue = list(postmaps) + list(others)
    if config.SITEMAP_MAX_FILES:
        queue = queue[: config.SITEMAP_MAX_FILES]
    if not queue:
        # Tek dosyalık sitemap
        collect(meta.get("sitemap_index", ""), index_text)
    workers = max(1, min(config.SCAN_WORKERS, 8))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_map, url) for url in queue]
        done = 0
        for future in as_completed(futures):
            url, text = future.result()
            done += 1
            if text:
                nested = collect(url, text)
                for extra in nested[: config.SITEMAP_MAX_FILES]:
                    if extra not in queue:
                        queue.append(extra)
            if verbose and done % 10 == 0:
                log(f"    ~ sitemap {done}/{len(queue)} • film {len(found)}")
            if limit and len(found) >= limit:
                for pending in futures:
                    pending.cancel()
                break
    if verbose:
        log(f"  [✓] Sitemap'ten {len(found)} film adresi")
    if limit:
        found = found[:limit]
    return found


def _lastmod_for(text: str, loc: str) -> str:  # pragma: no cover - geriye dönük uyum
    for entry_loc, lastmod in extract.sitemap_entries(text):
        if entry_loc == loc:
            return lastmod
    return ""


# --------------------------------------------------------------------------- #
# Keşif: WordPress REST API
# --------------------------------------------------------------------------- #
def discover_rest(session, meta: dict, limit: int = 0, verbose: bool = True) -> list:
    """REST API'den ``[{url,title,date,categories,poster,wp_id}, ...]``."""
    roots = _sitemap_roots(meta)
    base = None
    for root in roots:
        probe = net.fetch(session, root.rstrip("/") + config.REST_API_PATH + "?per_page=1&_fields=id,link",
                          referer=root, accept="application/json")
        if probe.ok and probe.text.strip().startswith(("[", "{")):
            base = root.rstrip("/")
            meta["rest_base"] = base
            break
    if not base:
        return []

    cat_names = _rest_categories(session, base, meta)
    posts: list = []
    seen = set()
    page = 1
    total_pages = 0
    while True:
        url = (f"{base}{config.REST_API_PATH}?per_page={config.REST_PER_PAGE}&page={page}"
               f"&_fields=id,link,title,date,categories,slug")
        result = net.fetch(session, url, referer=base + "/", accept="application/json")
        if not result.ok:
            break
        try:
            data = json.loads(result.text)
        except Exception:
            break
        if not isinstance(data, list) or not data:
            break
        if not total_pages:
            try:
                total_pages = int(result.headers.get("X-WP-TotalPages") or 0)
            except Exception:
                total_pages = 0
        for item in data:
            link = net.unescape_media(item.get("link") or "")
            if not link or link in seen:
                continue
            seen.add(link)
            title = extract.clean_text((item.get("title") or {}).get("rendered") or "")
            posts.append({
                "url": link,
                "title": title,
                "date": (item.get("date") or "")[:19].replace("T", " "),
                "categories": [cat_names.get(c, "") for c in (item.get("categories") or []) if cat_names.get(c)],
                "wp_id": item.get("id"),
                "slug": item.get("slug") or slug_of(link),
            })
        if verbose and page % 5 == 0:
            log(f"    ~ REST sayfa {page}{'/' + str(total_pages) if total_pages else ''} • {len(posts)} film")
        if limit and len(posts) >= limit:
            break
        if total_pages and page >= total_pages:
            break
        if config.REST_MAX_PAGES and page >= config.REST_MAX_PAGES:
            break
        page += 1
    if limit:
        posts = posts[:limit]
    if verbose:
        log(f"  [✓] REST API'den {len(posts)} film")
    return posts


def _rest_categories(session, base: str, meta: dict) -> dict:
    names: dict = {}
    page = 1
    while page < 50:
        url = f"{base}{config.REST_CATEGORIES_PATH}?per_page=100&page={page}&_fields=id,name,link,count"
        result = net.fetch(session, url, referer=base + "/", accept="application/json")
        if not result.ok:
            break
        try:
            data = json.loads(result.text)
        except Exception:
            break
        if not isinstance(data, list) or not data:
            break
        for item in data:
            if item.get("id") and item.get("name"):
                names[int(item["id"])] = extract.clean_text(item["name"])
                link = net.unescape_media(item.get("link") or "")
                if link:
                    meta.setdefault("categories", {})[extract.clean_text(item["name"])] = link
        page += 1
    return names


# --------------------------------------------------------------------------- #
# Keşif: HTML tarama (eski yöntem, yedek)
# --------------------------------------------------------------------------- #
def discover_crawl(session, meta: dict, max_pages: int = 0, scan_categories: bool = True,
                   limit: int = 0, verbose: bool = True) -> list:
    urls: list = []
    seen = set()
    hosts = meta.get("site_hosts") or config.MIRROR_HOSTS
    base = (meta.get("site") or config.BASE_URL).rstrip("/")

    def add_page(page_url: str):
        result = net.fetch(session, page_url, referer=base + "/", tries=2)
        if not result.ok:
            return 1
        for link in extract.find_film_links(result.text, result.url or page_url, hosts):
            if link not in seen:
                seen.add(link)
                urls.append((link, ""))
        return extract.total_pages_hint(result.text)

    max_pages = max_pages or config.PAGES_PER_SCAN
    if verbose:
        log(f"  [~] Ana sayfa taranıyor (ilk {max_pages} sayfa)")
    for page in range(1, max_pages + 1):
        page_url = base + "/" if page == 1 else f"{base}/page/{page}/"
        add_page(page_url)
        if limit and len(urls) >= limit:
            break

    categories = fetch_all_categories(session, meta)
    meta.setdefault("categories", {}).update(categories)
    if scan_categories and categories:
        for idx, (name, cat_url) in enumerate(list(categories.items()), 1):
            if limit and len(urls) >= limit:
                break
            total = add_page(cat_url)
            pages = total
            if config.MAX_PAGES_PER_CATEGORY > 0:
                pages = min(total, config.MAX_PAGES_PER_CATEGORY)
            for page in range(2, pages + 1):
                # DÜZELTME: eski kod "...-opage/2/" üretiyordu (eksik "/").
                page_url = f"{cat_url.rstrip('/')}/page/{page}/"
                add_page(page_url)
                if limit and len(urls) >= limit:
                    break
            if verbose and idx % 10 == 0:
                log(f"    ~ kategori {idx}/{len(categories)} • {len(urls)} film")
    if limit:
        urls = urls[:limit]
    if verbose:
        log(f"  [✓] HTML taramasından {len(urls)} film adresi")
    return urls


def fetch_all_categories(session, meta: dict = None) -> dict:
    """Ana sayfadaki kategori bağlantıları (ad -> adres)."""
    meta = meta or {}
    cats: dict = {}
    for base in [meta.get("site"), config.SITE_HOME, config.BASE_URL]:
        if not base:
            continue
        result = net.fetch(session, base.rstrip("/") + "/", referer=base, tries=2)
        if result.ok:
            cats.update(extract.find_category_links(result.text, result.url or base))
            home = extract.find_site_home(result.text, result.url or base)
            if home:
                meta.setdefault("site", home)
                host = net.host_of(home)
                hosts = meta.setdefault("site_hosts", [])
                if host and host not in hosts:
                    hosts.insert(0, host)
            if cats:
                break
    return cats


# --------------------------------------------------------------------------- #
# Film çıkarma
# --------------------------------------------------------------------------- #
def build_film(session, url: str, res: resolver_mod.MediaResolver, meta: dict,
               post: Optional[dict] = None, verify: bool = False) -> Optional[dict]:
    """Tek bir film adresinden tam kayıt üretir."""
    post = post or {}
    referer = meta.get("site") or config.SITE_HOME
    result = net.fetch(session, url, referer=referer, tries=2)
    if not result.ok:
        result = net.fetch_first_working(session, net.mirror_urls(url), referer=referer)
    if not result.ok:
        return None
    page_url = result.url or url
    html = result.text
    home = extract.find_site_home(html, page_url)
    if home and home != meta.get("site"):
        meta["site"] = home
        host = net.host_of(home)
        hosts = meta.setdefault("site_hosts", [])
        if host and host not in hosts:
            hosts.insert(0, host)

    slug = post.get("slug") or slug_of(page_url)
    fallback_title = post.get("title") or slug.replace("-", " ").strip().title()
    title = extract.find_title(html, fallback_title)

    cats = list(post.get("categories") or [])
    cat_links = extract.find_category_links(html, page_url)
    if cat_links:
        meta.setdefault("categories", {}).update(
            {name: url for name, url in cat_links.items() if name not in meta.get("categories", {})})
    for name in cat_links:
        if name not in cats:
            cats.append(name)

    embeds = extract.find_embed_urls(html, page_url)
    vid = extract.find_video_id(html, page_url, *embeds[:2])
    media = extract.find_media_urls(html, page_url, vid)
    posters = extract.find_poster(html, page_url, vid)
    embed_url = ""

    if embeds and (not media or not vid):
        for embed in embeds[:3]:
            embed_result = net.fetch(session, embed, referer=page_url, tries=2)
            if not embed_result.ok:
                continue
            embed_url = embed_result.url or embed
            vid = vid or extract.find_video_id(embed_result.text, embed_url, page_url)
            for item in extract.find_media_urls(embed_result.text, embed_url, vid):
                if item["url"] not in [m["url"] for m in media]:
                    item["score"] += 3
                    media.append(item)
            for poster in extract.find_poster(embed_result.text, embed_url, vid)[:3]:
                if poster not in posters:
                    posters.append(poster)
            if media and vid:
                break
        media.sort(key=lambda item: -item["score"])

    if embeds and not embed_url:
        embed_url = embeds[0]
    if embed_url and not vid:
        vid = extract.find_video_id(embed_url)
    if vid and not embed_url:
        pattern = config.EMBED_PATTERNS[0] if config.EMBED_PATTERNS else "/pornolar/{id}.html"
        embed_url = (meta.get("site") or config.SITE_HOME).rstrip("/") + pattern.format(id=vid)
        if vid and embed_url:
            meta.setdefault("embed_base", net.site_origin(embed_url))

    film_id = vid if (vid and vid.isdigit()) else stable_id(slug or url)
    if not media and not (vid and res.cdn_hosts()):
        return None  # oynatılabilir hiçbir ipucu yok

    stream = media[0]["url"] if media else ""
    if not stream and vid:
        hosts = res.cdn_hosts()
        if hosts:
            stream = config.CDN_VIDEO_TEMPLATES[0].format(host=hosts[0], id=vid)

    poster = ""
    for candidate in posters:
        if candidate.startswith("http"):
            poster = candidate
            break
    if not poster and vid:
        hosts = res.cdn_hosts()
        if hosts and config.CDN_POSTER_TEMPLATES:
            poster = config.CDN_POSTER_TEMPLATES[0].format(host=hosts[0], id=vid)

    film = {
        "id": film_id,
        "video_id": vid or "",
        "title": title or fallback_title,
        "stream": stream,
        "poster": poster,
        "poster_alt": next((p for p in posters if p != poster), ""),
        "categories": cats,
        "url": page_url,
        "embed": embed_url,
        "date": post.get("date") or _lastmod_hint(post.get("date")) or time.strftime("%Y-%m-%d %H:%M"),
        "added": time.strftime("%Y-%m-%d %H:%M"),
        "host": net.host_of(stream),
    }
    # Kaynak doğrudan sayfa/gömme HTML'inden geldiyse CDN alan adını öğren.
    if media and stream:
        res.note_cdn_host(stream)
        res.note_site(net.site_origin(page_url))

    if verify and stream:
        probe_ok = False
        reachable = False
        for cand in res.candidates(film, include_page=False, max_candidates=6):
            ok, status, _headers = res.probe(cand)
            reachable = reachable or bool(status)
            res.remember(film_id, cand.url, cand.referer, cand.ua, ok=ok, status=status, kind=cand.kind)
            if ok:
                film["stream"] = cand.url
                film["ref"] = cand.referer or ""
                film["host"] = net.host_of(cand.url)
                film["ok"] = True
                probe_ok = True
                break
        if not probe_ok and reachable:
            film["ok"] = False
    return film


def _lastmod_hint(value) -> str:
    return (value or "")[:19].replace("T", " ")


# --------------------------------------------------------------------------- #
# CDN alan adı rotasyonu
# --------------------------------------------------------------------------- #
def retarget_streams(db: dict, host: str) -> int:
    """Tüm kayıtları güncel CDN alan adına taşır.

    Video adresleri ``https://<cdn>/<id>.mp4`` biçiminde; alan adı döndüğünde
    eski kayıtların tamamı 403/404 vermeye başlıyor.  Doğrulanmış güncel alan
    adı bulunduğunda hepsini tek seferde yeniliyoruz.
    """
    if not host:
        return 0
    changed = 0
    for film in db.get("films", []):
        stream = film.get("stream") or ""
        if not stream.startswith("http"):
            continue
        current = net.host_of(stream)
        if current == host:
            continue
        vid = str(film.get("video_id") or film.get("id") or "")
        new_url = ""
        if vid.isdigit():
            for template in config.CDN_VIDEO_TEMPLATES:
                if template.format(host=current, id=vid) == stream:
                    new_url = template.format(host=host, id=vid)
                    break
        if not new_url:
            # Şablonla eşleşmedi: yolu koru, sadece alan adını değiştir.
            new_url = net.swap_host(stream, host)
        if not new_url or new_url == stream:
            continue
        alts = film.setdefault("alt_streams", [])
        if stream not in alts:
            alts.insert(0, stream)
            del alts[3:]
        film["stream"] = new_url
        film["host"] = host
        film.pop("ok", None)  # yeniden doğrulanana kadar "bilinmiyor"
        changed += 1
    return changed


# --------------------------------------------------------------------------- #
# M3U
# --------------------------------------------------------------------------- #
def write_m3u(db: dict, path: Optional[str] = None):
    """Harici oynatıcılar (VLC/TV) için doğrudan adresli liste."""
    path = path or config.PLAYLIST_M3U
    meta = db.get("meta") or {}
    default_ref = meta.get("referer") or config.REFERER
    default_ua = meta.get("ua") or config.USER_AGENT
    lines = ["#EXTM3U"]
    for film in db.get("films", []):
        stream = film.get("stream")
        if not stream:
            continue
        cats = film.get("categories") or []
        group = ",".join(cats[:3]) if cats else "Genel"
        title = (film.get("title") or film.get("id") or "film").replace('"', "'")
        poster = film.get("poster") or film.get("poster_alt") or ""
        lines.append(f'#EXTINF:-1 group-title="{group}" tvg-logo="{poster}",{title}')
        ref = film.get("ref") or default_ref
        ua = film.get("ua") or default_ua
        lines.append(f"{stream}|User-Agent={quote(ua)}|Referer={quote(ref or '')}")
    _write_text(path, "\n".join(lines) + "\n")


def write_local_m3u(db: dict, port: Optional[int] = None, path: Optional[str] = None):
    """Yerel sunucu üzerinden oynatan liste — Referer/UA derdi yok."""
    port = port or config.PORT
    path = path or config.LOCAL_PLAYLIST_M3U
    lines = ["#EXTM3U"]
    for film in db.get("films", []):
        cats = film.get("categories") or []
        group = ",".join(cats[:3]) if cats else "Genel"
        title = (film.get("title") or film.get("id") or "film").replace('"', "'")
        lines.append(f'#EXTINF:-1 group-title="{group}",{title}')
        lines.append(f"http://127.0.0.1:{port}/stream/{film.get('id')}")
    _write_text(path, "\n".join(lines) + "\n")


def _write_text(path: str, text: str):
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".m3u-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


# --------------------------------------------------------------------------- #
# Ana tarama
# --------------------------------------------------------------------------- #
def _persist(db: dict, existing: dict, meta: dict):
    """Ara kayıt — tarama yarıda kesilirse bulunan filmler kaybolmasın."""
    db["films"] = list(existing.values())
    db["categories"] = {**(db.get("categories") or {}), **(meta.get("categories") or {})}
    db["meta"] = meta
    save_db(db)


def scan(max_pages: Optional[int] = None,
         scan_categories: Optional[bool] = None,
         limit: Optional[int] = None,
         full: bool = False,
         verify: Optional[bool] = None,
         workers: Optional[int] = None,
         mode: Optional[str] = None,
         verbose: bool = True) -> dict:
    """Kataloğu günceller.  Dönüş: istatistik sözlüğü."""
    started = time.time()
    db = load_db()
    meta = db.setdefault("meta", {})
    session = net.make_session()
    # Çözücü kendi oturumunu kullanır (tekrar denemesiz, hızlı aday geçişi).
    res = resolver_mod.MediaResolver()
    if res.meta.get("site"):
        meta.setdefault("site", res.meta["site"])
    if res.meta.get("cdn_hosts"):
        meta["cdn_hosts"] = list(dict.fromkeys((res.meta.get("cdn_hosts") or []) + (meta.get("cdn_hosts") or [])))

    existing = {str(f.get("id")): f for f in db.get("films", []) if f.get("id")}
    by_url = {f.get("url", "").rstrip("/"): f for f in db.get("films", []) if f.get("url")}
    stats = {"new": 0, "updated": 0, "failed": 0, "verified_ok": 0, "verified_bad": 0,
             "discovered": 0, "source": "", "retargeted": 0}

    if limit is None:
        limit = 0 if full else config.MAX_FILMS_PER_SCAN
    max_pages = max_pages if max_pages is not None else config.PAGES_PER_SCAN
    scan_categories = config.SCAN_ALL_CATEGORIES if scan_categories is None else scan_categories
    verify = config.VERIFY_STREAMS if verify is None else verify
    workers = max(1, min(workers or config.SCAN_WORKERS, 24))
    mode = (mode or config.DISCOVERY_MODE or "auto").lower()

    if verbose:
        log("\n[1/3] Film adresleri keşfediliyor...")
    discovered: list = []          # [(url, post_bilgisi|None), ...]
    discovered_urls: set = set()
    want = 0 if full or not limit else limit * 4   # keşifte biraz fazla topla

    def push(url, post=None):
        key = (url or "").rstrip("/")
        if not key or key in discovered_urls:
            return False
        discovered_urls.add(key)
        discovered.append((url, post))
        return True

    if mode in ("auto", "sitemap"):
        for url, lastmod in discover_sitemap(session, meta, limit=want, verbose=verbose):
            push(url, {"date": lastmod})
        if discovered:
            stats["source"] = "sitemap"

    if mode in ("auto", "rest") and (not discovered or mode == "rest"):
        posts = discover_rest(session, meta, limit=want or 100, verbose=verbose)
        if posts:
            if not discovered:
                stats["source"] = "rest"
            else:
                stats["source"] += "+rest"
            posts_by_url = {p["url"].rstrip("/"): p for p in posts}
            # Sitemap'ten gelen kayıtları REST bilgisiyle zenginleştir.
            enriched = [(url, posts_by_url.get(url.rstrip("/")) or post) for url, post in discovered]
            discovered[:] = enriched
            for post in posts:
                push(post["url"], post)

    if not discovered and (mode in ("auto", "crawl") or config.CRAWL_ENABLED):
        for url, _last in discover_crawl(session, meta, max_pages=max_pages,
                                        scan_categories=scan_categories,
                                        limit=want or 100, verbose=verbose):
            push(url, None)
        stats["source"] = (stats["source"] + "+crawl") if stats["source"] else "crawl"

    if not discovered:
        log("  [!] Hiç film adresi bulunamadı — mevcut katalog korunuyor.")
        db["meta"] = meta
        save_db(db)
        stats["elapsed"] = round(time.time() - started, 1)
        return stats

    # Henüz katalogda olmayanlar önce (yeni filmler); hızlı taramada eskiler atlanır.
    fresh, known = [], []
    for url, post in discovered:
        if url.rstrip("/") in by_url:
            known.append((url, post, by_url[url.rstrip("/")]))
        else:
            fresh.append((url, post))
    if not full and limit:
        fresh = fresh[:limit]
        known = []
    stats["discovered"] = len(discovered)
    if verbose:
        log(f"  [✓] {len(discovered)} adres keşfedildi • yeni {len(fresh)} • bilinen {len(known)}")

    if verbose:
        log(f"\n[2/3] Filmler çözülüyor ({len(fresh)} yeni, {workers} işçi)...")
    verify_budget = [0 if not verify else (config.VERIFY_SAMPLE or 0)]
    lock = threading.Lock()
    save_counter = [0]

    def work(item):
        url, post = item
        with lock:
            do_verify = bool(verify) and (
                verify_budget[0] == 0
                or verify_budget[0] > stats["verified_ok"] + stats["verified_bad"]
            )
        film = build_film(session, url, res, meta, post=post or None, verify=do_verify)
        with lock:
            save_counter[0] += 1
            if film:
                key = str(film["id"])
                if key in existing:
                    film["added"] = existing[key].get("added") or film.get("added")
                    stats["updated"] += 1
                else:
                    stats["new"] += 1
                # Aynı filmin eski/kimliği farklı kaydı varsa kaldır (çift kayıt önleme).
                old_url = (film.get("url") or url).rstrip("/")
                stale = [fid for fid, other in existing.items()
                         if fid != key and (other.get("url") or "").rstrip("/") == old_url]
                for fid in stale:
                    existing.pop(fid, None)
                existing[key] = film
                by_url[old_url] = film
                if do_verify:
                    stats["verified_ok" if film.get("ok") else "verified_bad"] += 1
            else:
                stats["failed"] += 1
            if save_counter[0] % config.SAVE_EVERY == 0:
                _persist(db, existing, meta)
            if save_counter[0] % 25 == 0 and verbose:
                log(f"    ~ {save_counter[0]}/{len(fresh)} • yeni {stats['new']} • hata {stats['failed']}")
        return film

    if fresh:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work, item) for item in fresh]
            try:
                for _ in as_completed(futures):
                    pass
            except KeyboardInterrupt:  # pragma: no cover
                log("\n  [!] Durduruldu — bulunanlar kaydediliyor")
                for future in futures:
                    future.cancel()

    # Bilinen filmlerin eksik tarih/kategori bilgisini tamamla.
    for url, post, film in (known if full else known[:200]):
        if not post:
            continue
        if post.get("date") and not film.get("date"):
            film["date"] = post["date"]
        if post.get("categories") and not film.get("categories"):
            film["categories"] = post["categories"]

    if verbose:
        log("\n[3/3] Katalog düzenleniyor...")
    if not meta.get("categories"):
        meta["categories"] = fetch_all_categories(session, meta)
    db["films"] = list(existing.values())
    db["categories"] = {**(db.get("categories") or {}), **(meta.get("categories") or {})}

    # Öğrenilen bilgileri meta'ya ve films.json'a işle
    if res.meta.get("cdn_hosts"):
        meta["cdn_hosts"] = list(dict.fromkeys(res.meta["cdn_hosts"] + (meta.get("cdn_hosts") or [])))[:10]
    if res.meta.get("cdn_host"):
        meta["cdn_host"] = res.meta["cdn_host"]
    if meta.get("site"):
        res.note_site(meta["site"])
    if stats["verified_ok"]:
        meta["cdn_host_verified"] = stats["verified_ok"]
        meta["verified_at"] = time.strftime("%Y-%m-%d %H:%M")
    current_host = meta.get("cdn_host")
    if current_host:
        moved = retarget_streams(db, current_host)
        stats["retargeted"] = moved
        if moved and verbose:
            log(f"  [↻] {moved} kayıt güncel CDN'e taşındı: {current_host}")
    # Çalıştığı kanıtlanmış Referer'ı öğren (M3U + harici oynatıcılar için).
    meta["referer"] = res.best_referer(meta.get("referer") or config.REFERER)
    meta["ua"] = meta.get("ua") or config.USER_AGENT
    res.apply_cache_to_films(db["films"])
    meta["last_scan"] = {
        "at": time.strftime("%Y-%m-%d %H:%M"),
        "source": stats["source"],
        "discovered": stats["discovered"],
        "new": stats["new"],
        "failed": stats["failed"],
        "verified_ok": stats["verified_ok"],
        "verified_bad": stats["verified_bad"],
        "retargeted": stats["retargeted"],
        "elapsed_sec": round(time.time() - started, 1),
    }
    db["meta"] = meta
    db["films"] = sorted(db["films"], key=lambda f: (f.get("date") or f.get("added") or ""), reverse=True)
    save_db(db)
    write_m3u(db)
    write_local_m3u(db)
    res.save(force=True)

    stats["total"] = len(db["films"])
    stats["elapsed"] = round(time.time() - started, 1)
    if verbose:
        log(f"\n{'=' * 58}")
        log("[✓] TARAMA TAMAMLANDI")
        log(f"    Kaynak          : {stats['source'] or '-'}")
        log(f"    Keşfedilen adres: {stats['discovered']}")
        log(f"    Yeni film       : {stats['new']}")
        log(f"    Güncellenen     : {stats['updated']}")
        log(f"    Çözülemeyen     : {stats['failed']}")
        log(f"    Doğrulanan (ok) : {stats['verified_ok']} • sorunlu {stats['verified_bad']}")
        log(f"    CDN'e taşınan   : {stats['retargeted']}")
        log(f"    Toplam film     : {stats['total']}")
        log(f"    Süre            : {stats['elapsed'] / 60:.1f} dakika")
        log(f"{'=' * 58}\n")
    return stats


def repair(limit: Optional[int] = None, workers: Optional[int] = None,
           only_broken: bool = True, verbose: bool = True) -> dict:
    """Kataloğu doğrular: ölü kaynakları yeniden çözer, films.json'u günceller."""
    db = load_db()
    meta = db.setdefault("meta", {})
    session = net.make_session()
    res = resolver_mod.MediaResolver(session=session)
    films = db.get("films", [])
    if not films:
        log("[!] Katalog boş — önce tarama yapın.")
        return {"checked": 0, "fixed": 0, "dead": 0, "total": 0}
    if verbose:
        log(f"[~] {len(films)} film doğrulanıyor (sadece sorunlular={only_broken})...")

    def progress(stats):
        if verbose:
            log(f"    ~ {stats['checked']}/{stats['total']} • düzelen {stats['fixed']} • ölü {stats['dead']}")

    stats = res.repair(films, limit=limit, workers=workers, progress=progress, only_broken=only_broken)
    res.apply_cache_to_films(films)
    if res.meta.get("cdn_host"):
        meta["cdn_host"] = res.meta["cdn_host"]
    if res.meta.get("cdn_hosts"):
        meta["cdn_hosts"] = list(dict.fromkeys(res.meta["cdn_hosts"] + (meta.get("cdn_hosts") or [])))[:10]
    db["meta"] = meta
    save_db(db)
    write_m3u(db)
    write_local_m3u(db)
    stats["total_films"] = len(films)
    stats["ok"] = sum(1 for f in films if f.get("ok"))
    if verbose:
        log(f"[✓] Onarım bitti: {stats['fixed']} düzeltildi, {stats['dead']} ölü, "
            f"{stats['ok']}/{stats['total_films']} oynatılabilir")
    return stats


def migrate_db(verbose: bool = True) -> dict:
    """Var olan ``films.json``'u yeni şemaya taşır — internet gerektirmez.

    Eski kayıtlarda ``video_id``/``embed``/``host``/``meta`` yoktu.  Bunlar
    olmadan çözücü doğru Referer adayını üretemiyor ve CDN 403 dönüyordu;
    bu yüzden mevcut katalog yerinde zenginleştirilir.
    """
    from collections import Counter

    db = load_db()
    films = db.get("films", [])
    meta = db.setdefault("meta", {})
    site = meta.get("site") or config.SITE_HOME
    cdn_hosts: Counter = Counter()
    site_hosts: Counter = Counter()
    changed = 0

    for film in films:
        before = tuple(sorted((k, str(v)) for k, v in film.items()))
        vid = str(film.get("video_id") or "")
        if not vid:
            match = re.search(r"/(\d{3,8})\.(?:mp4|m3u8|webm)", film.get("stream") or "")
            if match:
                vid = match.group(1)
            elif str(film.get("id") or "").isdigit():
                vid = str(film.get("id"))
        if vid:
            film["video_id"] = vid
        if not film.get("embed") and vid:
            pattern = config.EMBED_PATTERNS[0] if config.EMBED_PATTERNS else "/pornolar/{id}.html"
            film["embed"] = site.rstrip("/") + pattern.format(id=vid)
        host = net.host_of(film.get("stream") or "")
        if host:
            cdn_hosts[host] += 1
            film.setdefault("host", host)
        page_host = net.host_of(film.get("url") or "")
        if page_host:
            site_hosts[page_host] += 1
        film.setdefault("poster_alt", "")
        film.setdefault("categories", [])
        film.setdefault("alt_streams", [])
        if not film.get("date"):
            film["date"] = film.get("added", "")
        if tuple(sorted((k, str(v)) for k, v in film.items())) != before:
            changed += 1

    if site_hosts:
        site = "https://" + site_hosts.most_common(1)[0][0]
    meta["site"] = site
    meta["site_hosts"] = [h for h, _ in site_hosts.most_common()] or list(config.MIRROR_HOSTS)
    if cdn_hosts:
        meta["cdn_hosts"] = [h for h, _ in cdn_hosts.most_common()]
        meta.setdefault("cdn_host", meta["cdn_hosts"][0])
    meta.setdefault("embed_base", site)
    meta.setdefault("referer", config.REFERER)
    meta.setdefault("ua", config.USER_AGENT)
    meta["migrated_at"] = time.strftime("%Y-%m-%d %H:%M")
    db["meta"] = meta
    save_db(db)
    write_m3u(db)
    write_local_m3u(db)
    result = {"total": len(films), "changed": changed, "site": site,
              "cdn_hosts": meta.get("cdn_hosts", []), "bytes": os.path.getsize(config.FILMS_JSON)}
    if verbose:
        log(f"[✓] Katalog taşındı: {result['total']} film • {result['changed']} kayıt güncellendi")
        log(f"    site      : {result['site']}")
        log(f"    cdn       : {', '.join(result['cdn_hosts'][:5]) or '-'}")
        log(f"    films.json: {result['bytes'] / 1024:.0f} KB")
    return result


def status() -> dict:
    """Katalog özeti (bot menüsü / API için)."""
    db = load_db()
    films = db.get("films", [])
    ok = sum(1 for f in films if f.get("ok"))
    unknown = sum(1 for f in films if f.get("ok") is None)
    meta = db.get("meta") or {}
    return {
        "total": len(films),
        "ok": ok,
        "broken": sum(1 for f in films if f.get("ok") is False),
        "unknown": unknown,
        "categories": len(db.get("categories") or {}),
        "updated": db.get("updated"),
        "site": meta.get("site") or config.SITE_HOME,
        "cdn_host": meta.get("cdn_host") or "-",
        "last_scan": meta.get("last_scan") or {},
    }


if __name__ == "__main__":
    import sys

    args = [a.lower() for a in sys.argv[1:]]
    if "repair" in args or "onar" in args:
        repair()
    elif "status" in args or "durum" in args:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    else:
        full = "full" in args or "tam" in args or "--full" in args
        log("Tam tarama başlıyor... (Ctrl+C ile durdurabilirsin)")
        log("Durdurduğunda kaydedilmiş veriler kaybolmaz.\n")
        try:
            scan(full=full)
        except KeyboardInterrupt:
            log("\n\n[!] Durduruldu. Veriler kaydedildi.")
