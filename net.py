# -*- coding: utf-8 -*-
"""HTTP katmanı.

Tek bir yerde toplanmasının sebebi: sitenin ve CDN'in eski TLS ayarları,
dönüşümlü (rotating) alan adları ve Referer/User-Agent tabanlı hotlink
koruması var.  Eski kod bu bilgileri ``config`` içine sabit yazıyordu; alan
adı değiştiği anda tüm videolar 403 yiyordu.  Burada her istek için *aday*
başlık kümesi üretiliyor ve başarısızlıkta otomatik olarak bir sonraki
deneniyor.
"""
from __future__ import annotations

import gzip
import io
import ssl
import time
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse, urlunparse

import requests
import urllib3
from requests.adapters import HTTPAdapter

try:  # urllib3 v1/v2 uyumu
    from urllib3.util.retry import Retry
except Exception:  # pragma: no cover
    Retry = None

import config

urllib3.disable_warnings()


class TolerantTLSAdapter(HTTPAdapter):
    """Eski CDN'lerin TLS yapılandırmasını kabul eden adaptör."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        except ssl.SSLError:  # bazı OpenSSL sürümlerinde gerekli değil
            pass
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def make_session(pool_maxsize: int = 16, retries: Optional[int] = None) -> requests.Session:
    """``retries=None`` -> ``config.HTTP_RETRIES``; 0 verilirse tekrar deneme yapılmaz."""
    attempts = config.HTTP_RETRIES if retries is None else retries
    retry_obj = None
    if Retry is not None and attempts:
        retry_obj = Retry(
            total=attempts,
            connect=attempts,
            read=1,
            status=1,
            backoff_factor=config.BACKOFF_FACTOR,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD", "OPTIONS"]),
            raise_on_status=False,
        )
    session = requests.Session()
    session.mount("https://", TolerantTLSAdapter(pool_maxsize=pool_maxsize, max_retries=retry_obj))
    session.mount("http://", HTTPAdapter(pool_maxsize=pool_maxsize, max_retries=retry_obj))
    session.headers.update({
        "User-Agent": config.USER_AGENT,
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    session.trust_env = False  # sistem proxy'leri taramayı bozmasın
    return session


# --------------------------------------------------------------------------- #
# Başlık adayları
# --------------------------------------------------------------------------- #
def browser_headers(referer: Optional[str] = None,
                    ua: Optional[str] = None,
                    origin: Optional[str] = None,
                    range_header: Optional[str] = None,
                    accept: str = "*/*") -> dict:
    """Gerçek bir tarayıcı isteğini taklit eden başlıklar.

    ``Accept-Encoding: identity`` şart: bazı video CDN'leri Range isteğine
    rağmen gzip'li yanıt dönüyor, bu da bayt ofsetlerini bozup oynatmayı
    tamamen durduruyor.
    """
    headers = {
        "User-Agent": ua or config.USER_AGENT,
        "Accept": accept,
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if referer:
        headers["Referer"] = referer
        headers["Origin"] = origin or site_origin(referer)
    if range_header:
        headers["Range"] = range_header
    return headers


def site_origin(url: str) -> str:
    p = urlparse(url or "")
    if not p.scheme or not p.netloc:
        return ""
    return f"{p.scheme}://{p.netloc}"


def referer_candidates(film: dict, meta: Optional[dict] = None) -> list:
    """Bir film için denenecek Referer değerleri (öncelik sırasıyla).

    Sitenin kendi oynatıcısı videoyu ``/pornolar/<id>.html`` gömme sayfası
    içinden istiyor; dolayısıyla CDN'in beyaz listesinde büyük olasılıkla o
    adres var.  Eski sabit ``config.REFERER`` en sona bırakılır.
    """
    meta = meta or {}
    out: list = []

    def push(value):
        if value and value not in out:
            out.append(value)

    push(film.get("embed"))
    push(film.get("url"))
    push(film.get("ref"))
    push(meta.get("embed_base"))
    push(meta.get("site"))
    push(meta.get("referer"))
    push(config.SITE_HOME)
    push(config.BASE_URL)
    for host in config.MIRROR_HOSTS:
        push("https://" + host + "/")
    push(config.REFERER)
    if "" not in out:
        out.append("")  # referer'sız deneme: bazı CDN'ler sadece bunu kabul eder
    return out


def ua_candidates() -> list:
    return [u for u in (config.USER_AGENT, config.DESKTOP_USER_AGENT) if u]


# --------------------------------------------------------------------------- #
# İstek yardımcıları
# --------------------------------------------------------------------------- #
def absolute(base: str, url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        scheme = urlparse(base or "https://x").scheme or "https"
        return f"{scheme}:{url}"
    return urljoin(base or config.SITE_HOME, url)


def unescape_media(text: str) -> str:
    """JS/JSON içinde kaçışlanmış URL'leri okunabilir hale getirir.

    Oynatıcı kaynakları çoğunlukla ``https:\\/\\/cdn\\/123.mp4`` veya
    ``https:\\u002F\\u002Fcdn...`` biçiminde gömülü geliyor; eski regex'ler
    bunları hiç görmüyordu.
    """
    if not text:
        return text
    out = text.replace("\\/", "/")
    for seq, ch in (("\\u002F", "/"), ("\\u002f", "/"), ("\\u003A", ":"), ("\\u003a", ":"),
                    ("\\x2F", "/"), ("\\x2f", "/"), ("\\x3A", ":"), ("\\x3a", ":")):
        if seq in out:
            out = out.replace(seq, ch)
    out = (out.replace("&amp;", "&").replace("&#038;", "&").replace("&#x2F;", "/")
              .replace("&quot;", '"'))
    return out


def swap_host(url: str, host: str) -> str:
    p = urlparse(url)
    if not p.netloc:
        return url
    netloc = host
    if "@" in p.netloc:  # kullanıcı bilgisi varsa korunur (pratikte yok)
        netloc = p.netloc.split("@", 1)[0] + "@" + host
    return urlunparse(p._replace(netloc=netloc))


def host_of(url: str) -> str:
    return (urlparse(url or "").netloc or "").lower()


class FetchResult:
    __slots__ = ("ok", "status", "text", "url", "error", "elapsed", "headers")

    def __init__(self, ok=False, status=0, text="", url="", error=None, elapsed=0.0, headers=None):
        self.ok = ok
        self.status = status
        self.text = text
        self.url = url
        self.error = error
        self.elapsed = elapsed
        self.headers = headers or {}

    def __bool__(self):
        return self.ok


def _decode_body(resp) -> str:
    raw = resp.content or b""
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    if "gzip" in enc:
        try:
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        except Exception:
            pass
    ctype = (resp.headers.get("Content-Type") or "").lower()
    charset = resp.encoding
    if not charset or charset.lower() == "iso-8859-1":
        charset = "utf-8" if "charset" not in ctype else charset
    try:
        return raw.decode(charset, "replace")
    except Exception:
        return raw.decode("utf-8", "replace")


def fetch(session: requests.Session,
          url: str,
          referer: Optional[str] = None,
          ua: Optional[str] = None,
          timeout=None,
          tries: int = 1,
          max_bytes: int = 0,
          accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8") -> FetchResult:
    """Basit GET + metin gövde.  Hata durumunda boş sonuç döner (exception yok)."""
    timeout = timeout or config.TIMEOUT
    last_err = None
    started = time.time()
    for attempt in range(max(1, tries)):
        try:
            resp = session.get(
                url,
                headers=browser_headers(referer=referer, ua=ua, accept=accept),
                timeout=timeout,
                verify=False,
                allow_redirects=True,
                stream=bool(max_bytes),
            )
            try:
                if max_bytes:
                    body = resp.raw.read(max_bytes, decode_content=True) or b""
                    text = body.decode(resp.encoding or "utf-8", "replace")
                else:
                    text = _decode_body(resp)
                status = resp.status_code
                headers = dict(resp.headers)
                final_url = resp.url
            finally:
                resp.close()
            return FetchResult(
                ok=200 <= status < 400 and bool(text is not None),
                status=status,
                text=text or "",
                url=final_url or url,
                elapsed=time.time() - started,
                headers=headers,
            )
        except Exception as exc:  # noqa: BLE001 - ağ hataları bekleniyor
            last_err = exc
            if attempt + 1 < max(1, tries):
                time.sleep(config.BACKOFF_FACTOR * (attempt + 1))
    return FetchResult(ok=False, status=0, text="", url=url, error=last_err,
                       elapsed=time.time() - started)


def fetch_first_working(session: requests.Session,
                        urls: Iterable[str],
                        referer: Optional[str] = None,
                        timeout=None,
                        max_bytes: int = 0) -> FetchResult:
    """Birkaç aday adresi (ayna/alternatif) sırayla dener, ilk çalışanı döner."""
    result = FetchResult()
    for url in urls:
        if not url:
            continue
        result = fetch(session, url, referer=referer, timeout=timeout, tries=1, max_bytes=max_bytes)
        if result.ok:
            return result
    return result


def mirror_urls(url: str) -> list:
    """Aynı yolun bilinen aynalar üzerindeki karşılıkları."""
    if not url:
        return []
    out = [url]
    p = urlparse(url)
    for host in config.MIRROR_HOSTS:
        candidate = urlunparse(p._replace(netloc=host))
        if candidate not in out:
            out.append(candidate)
    return out
