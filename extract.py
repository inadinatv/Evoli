# -*- coding: utf-8 -*-
"""HTML / XML ayrıştırma yardımcıları.

Buradaki her fonksiyon *toleranslı*: sitenin teması, oynatıcısı veya gömme
(embed) biçimi değişse bile kaynak bulunabilsin diye birden fazla kalıp
denenir.  Eski sürüm tek bir regex'e bağlıydı (``/pornolar/<id>.html`` ve
``https://...mp4``); kalıp biraz değişince film hiç kaydedilmiyordu.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, Optional
from urllib.parse import urlparse

import config
from net import absolute, unescape_media

MEDIA_EXT = r"(?:mp4|m4v|webm|mov|mkv|ts|m3u8|mpd)"
_URL_IN_TEXT = re.compile(
    r"""(?:https?:)?//[^\s"'<>()\[\]{}\\]+?\.""" + MEDIA_EXT + r"""(?:\?[^\s"'<>()\[\]{}\\]*)?""",
    re.I,
)
_KEYED_URL = re.compile(
    r"""(?:file|src|source|sources|url|video[_-]?url|videoUrl|hls|manifest|mp4|m3u8|playlist|contentUrl)"""
    r"""\s*[:=]\s*["']([^"']{6,500})["']""",
    re.I,
)
_SOURCE_TAG = re.compile(r"""<(?:source|video|embed|iframe)\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']""", re.I)
_DATA_SRC = re.compile(r"""\bdata-(?:src|lazy-src|original|url|video|file|href)\s*=\s*["']([^"']+)["']""", re.I)
_META_CONTENT = re.compile(
    r"""<meta\b[^>]*?\b(?:property|name)\s*=\s*["']([^"']+)["'][^>]*?\bcontent\s*=\s*["']([^"']*)["']""",
    re.I,
)
_META_CONTENT_REV = re.compile(
    r"""<meta\b[^>]*?\bcontent\s*=\s*["']([^"']*)["'][^>]*?\b(?:property|name)\s*=\s*["']([^"']+)["']""",
    re.I,
)
_IFRAME = re.compile(r"""<iframe\b[^>]*>""", re.I)
_ATTR = lambda name: re.compile(r"""\b""" + name + r"""\s*=\s*["']([^"']*)["']""", re.I)  # noqa: E731
_LINK_HREF = re.compile(r"""<a\b[^>]*?\bhref\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", re.I | re.S)
_LINK_FULL = re.compile(r"""<a\b([^>]*)>(.*?)</a>""", re.I | re.S)
_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.I)
_EMBED_HINT = re.compile(
    r"""["'(](https?://[^\s"'()<>]+?/(?:pornolar|embed|player|video|play)/\d{2,8}[^\s"'()<>]*)["')]""",
    re.I,
)
_EMBED_HINT_REL = re.compile(r"""["'(](/(?:pornolar|embed|player|video|play)/\d{2,8}(?:\.html)?[^"'\s()]*)["')]""", re.I)

NON_FILM_HINTS = (
    "/page/", "/author/", "/kanal/", "/arsiv/", "/kategori/", "/tag/", "/etiket/",
    "/wp-content/", "/wp-json/", "/wp-admin/", "/wp-includes/", "/feed", "/comments",
    "iletisim", "hakkimizda", "hakkımızda", "dmca", "privacy", "gizlilik", "kurallar",
    "reklam", "18-uyari", "uyari", "#comment", "#respond", "#", "javascript:",
    "/sitemap", ".xml", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".css", ".js",
    ".ico", ".svg", ".woff", "-o/", "-i/", "/arsiv", "mailto:", "tel:",
)


def clean_text(fragment: str) -> str:
    return _TAGS.sub("", fragment or "").replace("&amp;", "&").replace("&#8217;", "'").strip()


def is_generic_image(url: str) -> bool:
    """Site logosu / favicon / yer tutucu görsel mi?

    Gerçek sitenin her sayfasındaki ``og:image`` çoğu zaman sitenin logosu
    (``logo1.png``); eski kod bunu afiş olarak kaydediyordu — 1000+ film aynı
    logoyu gösteriyordu.  Böyle jenerik görseller afiş adayı olarak alınmaz.
    """
    if not url:
        return True
    name = (urlparse(url).path or "").rstrip("/").split("/")[-1].lower()
    name = name.split("?")[0]
    if not name:
        return True
    for hint in getattr(config, "BAD_POSTER_HINTS", ()):
        hint = hint.lower()
        # "logo" -> logo.png, logo1.png, logo-2.jpg ...; "biyoloji.jpg'yi yakalama
        if re.match(r"^" + re.escape(hint) + r"[\d._\-]*(?:\.[a-z0-9]+)?$", name):
            return True
        if re.match(r"^" + re.escape(hint) + r"[-_.]", name):
            return True
    return False


def meta_map(html: str) -> dict:
    """<meta property/name -> content> haritası (her iki yazım sırasıyla)."""
    out = {}
    for key, value in _META_CONTENT.findall(html or ""):
        out.setdefault(key.lower(), value)
    for value, key in _META_CONTENT_REV.findall(html or ""):
        out.setdefault(key.lower(), value)
    return out


# --------------------------------------------------------------------------- #
# Video kaynakları
# --------------------------------------------------------------------------- #
def _score_media(url: str, kind: str, video_id: Optional[str]) -> int:
    score = 0
    low = url.lower()
    if video_id and video_id in low:
        score += 6
    if kind in ("og:video", "source", "video"):
        score += 4
    if kind == "keyed":
        score += 3
    path = urlparse(url).path.lower()
    if path.endswith(".mp4"):
        score += 3
    elif path.endswith(".m3u8"):
        score += 2
    elif path.endswith((".webm", ".m4v", ".mov")):
        score += 2
    host = (urlparse(url).netloc or "").lower()
    if "cdn" in host or "media" in host or "video" in host:
        score += 2
    if any(word in low for word in ("sample", "trailer", "preview", "/ads/", "sponsor", "banner")):
        score -= 6
    if low.startswith("//") or low.startswith("/"):
        score -= 1
    return score


def find_media_urls(html: str, base_url: str = "", video_id: Optional[str] = None) -> list:
    """Sayfa/gömme HTML'inden oynatılabilir medya adreslerini çıkarır.

    Dönen liste önceliğe göre sıralıdır: ``[{"url","kind","score"}, ...]``
    """
    if not html:
        return []
    text = unescape_media(html)
    found: list = []
    seen = set()

    def add(raw_url: str, kind: str, base: str = None):
        url = absolute(base or base_url, (raw_url or "").strip().rstrip("\\"))
        if not url or url in seen:
            return
        if not re.search(r"\." + MEDIA_EXT + r"(?:\?|$)", url, re.I):
            return
        if not url.lower().startswith(("http://", "https://")):
            return
        seen.add(url)
        found.append({"url": url, "kind": kind, "score": _score_media(url, kind, video_id)})

    for m in _META_CONTENT.findall(text):
        key, value = m[0].lower(), m[1]
        if key.startswith(("og:video", "twitter:player", "video:")):
            add(value, "og:video")
    for url in _SOURCE_TAG.findall(text):
        add(url, "source")
    for url in _DATA_SRC.findall(text):
        add(url, "data-src")
    for value in _KEYED_URL.findall(text):
        add(value, "keyed")
    for url in _URL_IN_TEXT.findall(text):
        add(url, "text")

    found.sort(key=lambda item: -item["score"])
    return found


def find_embed_urls(html: str, base_url: str = "") -> list:
    """Film sayfasındaki oynatıcı gömme (iframe/embed) adresleri."""
    if not html:
        return []
    text = unescape_media(html)
    out: list = []

    def push(url):
        url = (url or "").strip()
        if not url:
            return
        full = absolute(base_url, url)
        if full and full not in out and full.lower().startswith("http"):
            out.append(full)

    for tag in _IFRAME.findall(text):
        for attr in ("src", "data-src", "data-lazy-src", "data-original"):
            m = _ATTR(attr).search(tag)
            if m:
                push(m.group(1))
    for url in _EMBED_HINT.findall(text):
        push(url)
    for url in _EMBED_HINT_REL.findall(text):
        push(url)
    return out


def find_video_id(*texts: str, patterns: Optional[Iterable[str]] = None) -> Optional[str]:
    """Video numarasını birçok kalıptan bulur (deterministik, hash yok)."""
    patterns = list(patterns or config.VIDEO_ID_REGEXES)
    blob = "\n".join(t for t in texts if t)
    if not blob:
        return None
    blob = unescape_media(blob)
    counts: Counter = Counter()
    for pattern in patterns:
        for match in re.findall(pattern, blob):
            if match and match.isdigit():
                counts[match] += 1
    if not counts:
        return None
    # En çok tekrar eden, eşitlikte en büyük (en yeni) numara kazanır.
    return sorted(counts.items(), key=lambda kv: (kv[1], int(kv[0])), reverse=True)[0][0]


def find_title(html: str, fallback: str = "") -> str:
    if not html:
        return fallback
    metas = meta_map(html)
    for key in ("og:title", "twitter:title"):
        if metas.get(key):
            return clean_text(metas[key])
    m = _H1.search(html)
    if m:
        title = clean_text(m.group(1))
        if title:
            return title
    m = _TITLE_TAG.search(html)
    if m:
        title = clean_text(m.group(1))
        title = re.split(r"\s*[|•\-–]\s*(?:Evooli|Porno|HD|İzle|Sex|Sikiş)", title, maxsplit=1)[0]
        if title:
            return title
    return fallback


def find_poster(html: str, base_url: str = "", video_id: Optional[str] = None,
                allow_generic: bool = False) -> list:
    """Afiş adayları (öncelik sırasıyla).  Logo/yer tutucu görseller elenir."""
    if not html:
        return []
    text = unescape_media(html)
    metas = meta_map(text)
    out: list = []

    def push(url):
        url = (url or "").strip()
        if not url:
            return
        full = absolute(base_url, url)
        if not full or full in out or not full.lower().startswith("http"):
            return
        if not allow_generic and is_generic_image(full):
            return
        out.append(full)

    for key in ("og:image", "og:image:secure_url", "twitter:image", "image", "thumbnail"):
        if metas.get(key):
            push(metas[key])
    m = re.search(r"""image\s*:\s*["']([^"']+)["']""", text)
    if m:
        push(m.group(1))
    m = re.search(r"""<img\b[^>]*?\bsrc\s*=\s*["']([^"']+\.(?:jpg|jpeg|png|webp)[^"']*)["']""", text, re.I)
    if m:
        push(m.group(1))
    for url in re.findall(r"""["'](https?://[^"']+/wp-content/uploads/[^"']+\.(?:jpg|jpeg|png|webp))["']""", text, re.I):
        push(url)
    if video_id:
        for host in known_cdn_hosts():
            for tpl in config.CDN_POSTER_TEMPLATES:
                push(tpl.format(host=host, id=video_id))
    return out


def known_cdn_hosts(extra: Optional[Iterable[str]] = None) -> list:
    hosts = []
    for host in list(extra or []) + list(config.CDN_HOSTS):
        host = (host or "").strip().lower()
        if host.startswith("http"):
            host = urlparse(host).netloc or host
        host = host.split("/")[0]
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def find_category_links(html: str, base_url: str = "", scope: str = "all") -> dict:
    """Kategori adı -> kategori adresi.

    ``scope="all"``      → sayfadaki tüm ``-o``/``-i`` kategori bağlantıları
    (kategori keşfi için; menü/nav dahil).
    ``scope="content"``  → yalnızca içeriğe ait kategoriler (film sayfası).
    Sitede menü/yan sütunda TÜM kategoriler listelendiği için film kaydına
    eski kod 52 kategorinin tamamını yazıyordu; ``content`` kapsamı nav/header/
    footer/aside/yan sütun bloklarını atar, WordPress'in gerçek film
    kategorisi olan ``rel="category"`` bağlantılarına öncelik verir.
    """
    if scope == "content":
        html = _strip_chrome(html or "")
    plain: dict = {}
    rel_cats: dict = {}
    for attrs, inner in _LINK_FULL.findall(html or ""):
        href_m = _ATTR("href").search(attrs)
        if not href_m:
            continue
        href = href_m.group(1)
        if not (href.rstrip("/").endswith("-o") or href.rstrip("/").endswith("-i")):
            continue
        name = clean_text(inner)
        if not name or len(name) < 2 or len(name) > 60:
            continue
        rel_m = _ATTR("rel").search(attrs)
        is_rel = bool(rel_m and re.search(r"\bcategory\b", rel_m.group(1), re.I))
        bucket = rel_cats if is_rel else plain
        if name not in bucket:
            bucket[name] = absolute(base_url, href)
    if scope == "content":
        # rel="category" bağlantıları varsa gerçek film kategorileri bunlardır.
        return rel_cats or plain
    # "all" kapsamı: menü/nav dahil her şey (kategori sayfası keşfi için)
    out = dict(rel_cats)
    out.update({k: v for k, v in plain.items() if k not in out})
    return out


_CHROME_BLOCK = re.compile(
    r"<(?:nav|header|footer|aside)\b[^>]*>.*?</(?:nav|header|footer|aside)>",
    re.I | re.S,
)
_CHROME_DIV = re.compile(
    r"""<(div|ul|section|aside)\b[^>]*(?:class|id)\s*=\s*["'][^"']*"""
    r"""(?:sidebar|widget|menu|navbar|footer|header|tag-?cloud|kategori-?liste|cat-?liste|side-?bar)[^"']*["'][^>]*>"""
    r""".*?</\1>""",
    re.I | re.S,
)


def _strip_chrome(html: str) -> str:
    """Menü/yan sütun/altbilgi bloklarını kaldırır (film içeriği kalır)."""
    out = _CHROME_BLOCK.sub(" ", html or "")
    for _ in range(3):
        stripped = _CHROME_DIV.sub(" ", out)
        if stripped == out:
            break
        out = stripped
    return out


def looks_like_film_url(url: str, allowed_hosts: Optional[Iterable[str]] = None) -> bool:
    """Bir bağlantının film sayfası olup olmadığını sezgisel olarak belirler."""
    if not url or not url.lower().startswith("http"):
        return False
    low = url.lower()
    if any(hint in low for hint in NON_FILM_HINTS):
        return False
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if allowed_hosts:
        if not any(host == h or host.endswith("." + h) or h in host for h in allowed_hosts):
            return False
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) != 1:
        return False
    slug = parts[0]
    if len(slug) < 12 or "-" not in slug:
        return False
    if not re.fullmatch(r"[a-z0-9\-']+", slug.lower()):
        return False
    if parsed.query:
        return False
    return True


def find_film_links(html: str, base_url: str = "", allowed_hosts: Optional[Iterable[str]] = None,
                    limit: int = 0) -> list:
    """Sayfadaki film bağlantılarını (sıra korunarak, tekilleştirilmiş) döner."""
    text = unescape_media(html or "")
    hosts = list(allowed_hosts or [])
    if base_url:
        base_host = (urlparse(base_url).netloc or "").lower()
        if base_host and base_host not in hosts:
            hosts.append(base_host)
    hosts.extend(h.lower() for h in config.MIRROR_HOSTS if h.lower() not in hosts)
    out: list = []
    seen = set()
    for href, _inner in _LINK_HREF.findall(text):
        full = absolute(base_url, href)
        if not full or full in seen:
            continue
        if not looks_like_film_url(full, hosts):
            continue
        seen.add(full)
        out.append(full)
        if limit and len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
# Sitemap
# --------------------------------------------------------------------------- #
_URL_BLOCK = re.compile(r"<url\b[^>]*>(.*?)</url>", re.I | re.S)
_LASTMOD = re.compile(r"<lastmod>\s*([^<]+?)\s*</lastmod>", re.I)


def sitemap_entries(xml_text: str) -> list:
    """``[(loc, lastmod), ...]`` — sitemap <url> bloklarını sırayla çıkarır."""
    out = []
    text = xml_text or ""
    blocks = _URL_BLOCK.findall(text)
    if not blocks:  # <url> etiketi yoksa ham <loc> listesine düş
        for loc in _LOC.findall(text):
            loc = unescape_media(loc).strip()
            if loc:
                out.append((loc, ""))
        return out
    for block in blocks:
        match = _LOC.search(block)
        if not match:
            continue
        loc = unescape_media(match.group(1)).strip()
        if not loc:
            continue
        mod = _LASTMOD.search(block)
        out.append((loc, mod.group(1).strip()[:19].replace("T", " ") if mod else ""))
    return out


def sitemap_locs(xml_text: str) -> list:
    return [loc for loc, _mod in sitemap_entries(xml_text)]


def is_sitemap_index(xml_text: str) -> bool:
    return bool(re.search(r"<sitemapindex", xml_text or "", re.I))


def split_sitemap_urls(locs: Iterable[str]) -> tuple:
    """(alt sitemap dosyaları, içerik adresleri) ayrımı."""
    submaps, pages = [], []
    for loc in locs:
        if loc.lower().endswith(".xml") or "sitemap" in loc.lower():
            submaps.append(loc)
        else:
            pages.append(loc)
    return submaps, pages


# --------------------------------------------------------------------------- #
# Site keşfi
# --------------------------------------------------------------------------- #
def find_site_home(html: str, page_url: str = "") -> str:
    """Sayfadaki kanonik adresden sitenin güncel ana alan adını bulur."""
    metas = meta_map(html or "")
    for key in ("og:url", "og:site_url", "twitter:url"):
        value = metas.get(key)
        if value:
            parsed = urlparse(absolute(page_url, value))
            if parsed.netloc:
                return f"{parsed.scheme or 'https'}://{parsed.netloc}"
    m = re.search(r"""<link\b[^>]*?\brel\s*=\s*["'](?:canonical|home|alternate)["'][^>]*?\bhref\s*=\s*["']([^"']+)["']""",
                  html or "", re.I)
    if m:
        parsed = urlparse(absolute(page_url, m.group(1)))
        if parsed.netloc:
            return f"{parsed.scheme or 'https'}://{parsed.netloc}"
    hosts = Counter()
    for href, _ in _LINK_HREF.findall(unescape_media(html or "")):
        if href.startswith("http"):
            host = (urlparse(href).netloc or "").lower()
            if host and "evooli" in host or host:
                hosts[host] += 1
    if page_url:
        parsed = urlparse(page_url)
        if parsed.netloc:
            return f"{parsed.scheme or 'https'}://{parsed.netloc}"
    if hosts:
        top = hosts.most_common(1)[0][0]
        return "https://" + top
    return ""


def total_pages_hint(html: str) -> int:
    """Listeleme sayfasındaki toplam sayfa sayısı."""
    m = re.search(r"(?:Sayfa|Page)\s*\d+\s*(?:/|of)\s*(\d+)", html or "", re.I)
    if m:
        return int(m.group(1))
    pages = [int(n) for n in re.findall(r"/page/(\d+)/?", html or "")]
    return max(pages) if pages else 1
