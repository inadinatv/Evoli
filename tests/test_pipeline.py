# -*- coding: utf-8 -*-
"""Uçtan uca boru hattı testleri (sahte site üzerinde, internet gerektirmez).

Çalıştırma::

    python -m unittest discover -s tests -v
    python tests/test_pipeline.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

import config
import extract
import net
import resolver as resolver_mod
import scraper
import server
try:
    from tests.fakesite import (FILMS, POSTER_BODY, SEGMENT_BODY, VIDEO_BODY, VIDEO_SIZE,
                                quiet_handle_error, start_fake_site)
except ImportError:  # `python tests/test_pipeline.py` ile doğrudan çalıştırma
    from fakesite import (FILMS, POSTER_BODY, SEGMENT_BODY, VIDEO_BODY, VIDEO_SIZE,
                          quiet_handle_error, start_fake_site)

HTTPD = None
BASE = ""
STATE = None


def setUpModule():
    global HTTPD, BASE, STATE
    HTTPD, _thread, BASE, STATE = start_fake_site()


def tearDownModule():
    if HTTPD is not None:
        HTTPD.shutdown()
        HTTPD.server_close()


def patch_config(tmpdir: str, mode: str = "sitemap"):
    host = urlparse(BASE).netloc
    config.BASE_URL = BASE
    config.SITE_HOME = BASE
    config.MIRROR_HOSTS = [host]
    config.CDN_HOSTS = [host]
    config.CDN_VIDEO_TEMPLATES = ["http://{host}/{id}.mp4"]
    config.CDN_POSTER_TEMPLATES = ["http://{host}/img/{id}.jpg"]
    config.EMBED_PATTERNS = ["/pornolar/{id}.html"]
    # Bilerek *yanlış* referer: gerçek hayattaki bayat config.REFERER senaryosu.
    config.REFERER = "https://stale-referer-domain.invalid/"
    config.FILMS_JSON = os.path.join(tmpdir, "films.json")
    config.PLAYLIST_M3U = os.path.join(tmpdir, "playlist.m3u")
    config.LOCAL_PLAYLIST_M3U = os.path.join(tmpdir, "playlist_local.m3u")
    config.MEDIA_CACHE = os.path.join(tmpdir, "media_cache.json")
    config.REQUEST_DELAY = 0
    config.SCAN_WORKERS = 4
    config.VERIFY_STREAMS = True
    config.VERIFY_SAMPLE = 0
    config.DISCOVERY_MODE = mode
    config.TIMEOUT = (3, 10)
    config.PROBE_TIMEOUT = (3, 10)
    config.HTTP_RETRIES = 0
    config.SAVE_EVERY = 2
    config.MAX_FILMS_PER_SCAN = 0
    config.CACHE_TTL_OK = 3600
    config.CACHE_TTL_FAIL = 1
    config.EMULATE_RANGE = True
    config.PORT = 0


class PipelineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="evoli-test-")
        patch_config(self.tmp)
        with STATE.lock:
            STATE.hits.clear()
            STATE.referers.clear()
            STATE.rejected.clear()
        server.RESOLVER = resolver_mod.MediaResolver(cache_file=config.MEDIA_CACHE)
        server._STATE.update({"mtime": 0.0, "size": -1, "db": scraper.empty_db(),
                              "index": {}, "by_url": {}, "loaded_at": 0})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ #
    def scan(self, **kwargs):
        kwargs.setdefault("verbose", False)
        kwargs.setdefault("full", True)
        return scraper.scan(**kwargs)

    def load_films(self):
        db = scraper.load_db(config.FILMS_JSON)
        return {f["id"]: f for f in db["films"]}, db


class TestExtractors(unittest.TestCase):
    def test_escaped_jwplayer_source(self):
        html = ('<script>player.setup({sources:[{file:"https:\\/\\/cdn.example.com\\/358120.mp4",'
                'type:"video\\/mp4",image:"https:\\/\\/cdn.example.com\\/img\\/358120.jpg"}]});</script>')
        media = extract.find_media_urls(html, "https://site.example/film/", "358120")
        self.assertTrue(media)
        self.assertEqual(media[0]["url"], "https://cdn.example.com/358120.mp4")
        posters = extract.find_poster(html, "https://site.example/film/", "358120")
        self.assertIn("https://cdn.example.com/img/358120.jpg", posters)

    def test_hls_and_source_tag(self):
        html = ('<video controls><source src="//cdn.example.com/v/12/index.m3u8" type="application/x-mpegURL">'
                '</video><meta property="og:video" content="https://cdn.example.com/12.mp4">')
        urls = [item["url"] for item in extract.find_media_urls(html, "https://site.example/")]
        self.assertIn("https://cdn.example.com/12.mp4", urls)
        self.assertIn("https://cdn.example.com/v/12/index.m3u8", urls)

    def test_video_id_patterns(self):
        self.assertEqual(extract.find_video_id('<iframe src="/pornolar/358120.html"></iframe>'), "358120")
        self.assertEqual(extract.find_video_id('https://cdn/x/778899.mp4'), "778899")
        self.assertEqual(extract.find_video_id('<a href="/player/1234567">'), "1234567")
        self.assertIsNone(extract.find_video_id("hiç numara yok"))

    def test_film_url_filter(self):
        hosts = ["www.evooli.com"]
        self.assertTrue(extract.looks_like_film_url("https://www.evooli.com/uzun-film-basligi-bu/", hosts))
        for bad in ["https://www.evooli.com/hd-porno-i/",
                    "https://www.evooli.com/page/2/",
                    "https://www.evooli.com/author/pornosu/",
                    "https://www.evooli.com/wp-content/uploads/2026/09/x.jpg",
                    "https://www.evooli.com/sitemap-pt-post-2026-09.xml",
                    "https://baska-site.com/uzun-film-basligi-bu/",
                    "https://www.evooli.com/kisa/"]:
            self.assertFalse(extract.looks_like_film_url(bad, hosts), bad)

    def test_embed_and_site_discovery(self):
        html = ('<html><head><link rel="canonical" href="https://www.evooli.com/film-basligi-burada/">'
                '</head><body><iframe data-src="/pornolar/123456.html"></iframe></body></html>')
        embeds = extract.find_embed_urls(html, "https://ayna.evooli.com/film-basligi-burada/")
        self.assertEqual(embeds, ["https://ayna.evooli.com/pornolar/123456.html"])
        self.assertEqual(extract.find_site_home(html, "https://ayna.evooli.com/x/"), "https://www.evooli.com")

    def test_sitemap_entries(self):
        xml = ('<urlset><url><loc>https://x.com/a-b-c/</loc><lastmod>2026-09-13T19:01:32+00:00</lastmod></url>'
               '<url><loc>https://x.com/d-e-f/</loc></url></urlset>')
        entries = extract.sitemap_entries(xml)
        self.assertEqual(entries[0], ("https://x.com/a-b-c/", "2026-09-13 19:01:32"))
        self.assertEqual(entries[1][0], "https://x.com/d-e-f/")

    def test_parse_range(self):
        self.assertEqual(resolver_mod.parse_range("bytes=100-199", 1000), (100, 199))
        self.assertEqual(resolver_mod.parse_range("bytes=100-", 1000), (100, 999))
        self.assertEqual(resolver_mod.parse_range("bytes=-50", 1000), (950, 999))
        self.assertIsNone(resolver_mod.parse_range(None, 1000))

    def test_referer_candidate_order(self):
        film = {"embed": "https://site/pornolar/1.html", "url": "https://site/film-bir/"}
        refs = net.referer_candidates(film, {"site": "https://site"})
        self.assertEqual(refs[0], film["embed"])
        self.assertEqual(refs[1], film["url"])
        self.assertIn("", refs)                       # referer'sız deneme listede
        self.assertLess(refs.index(film["embed"]), refs.index(config.REFERER))


class TestResolverBehaviour(PipelineCase):
    def test_host_circuit_breaker(self):
        """Erişilemeyen sunucu tekrar tekrar beklenmemeli (oynatma gecikmesi)."""
        res = resolver_mod.MediaResolver(cache_file=config.MEDIA_CACHE)
        url = "http://127.0.0.1:9/x.mp4"
        self.assertFalse(res.host_blocked(url))
        for _ in range(config.HOST_FAIL_THRESHOLD):
            res.note_host_result(url, 0)
        self.assertTrue(res.host_blocked(url))
        # sunucu cevap verirse (403 bile olsa) engel kalkar: Referer denemeleri sürmeli
        res.note_host_result(url, 403)
        self.assertFalse(res.host_blocked(url))
        # cooldown bitince kendiliğinden sıfırlanır
        res.note_host_result(url, 0)
        res.note_host_result(url, 0)
        res._host_failures["127.0.0.1:9"] = (config.HOST_FAIL_THRESHOLD,
                                             time.time() - config.HOST_COOLDOWN - 1)
        self.assertFalse(res.host_blocked(url))

    def test_candidates_prefer_cache_then_stored_then_templates(self):
        res = resolver_mod.MediaResolver(cache_file=config.MEDIA_CACHE)
        film = {"id": "300001", "video_id": "300001",
                # bayat CDN alan adı: şablon adayları devreye girmeli
                "stream": "https://old-cdn-host.invalid/300001.mp4",
                "url": f"{BASE}/deneme-filmi-bir-uzun-baslik/",
                "embed": f"{BASE}/pornolar/300001.html"}
        cands = res.candidates(film)
        kinds = [c.kind for c in cands]
        self.assertEqual(kinds[0], "stored")
        self.assertIn("template", kinds)
        # Öğrenilen CDN alan adı, filmin bayat alan adından önce gelmeli.
        res.note_cdn_host(f"{BASE}/anything.mp4")
        cands = res.candidates(film)
        first_template = next(c for c in cands if c.kind == "template")
        self.assertEqual(first_template.url, f"{BASE}/300001.mp4")
        # ilk adayın referer'ı gömme sayfası olmalı (CDN bunu beyaz listeye alır)
        self.assertEqual(cands[0].referer, film["embed"])
        # aynı adres farklı refererlarla tekrarlanır ama birebir kopya üretilmez
        self.assertEqual(len({(c.url, c.referer) for c in cands}), len(cands))
        res.remember("300001", f"{BASE}/300001.mp4", film["embed"], None, ok=True, status=206, kind="probe")
        self.assertEqual(res.candidates(film)[0].kind, "cache")
        self.assertEqual(res.candidates(film)[0].url, f"{BASE}/300001.mp4")


class TestScanDiscovery(PipelineCase):
    def test_sitemap_scan_finds_and_verifies_every_film(self):
        stats = self.scan(mode="sitemap")
        films, db = self.load_films()
        self.assertEqual(stats["source"], "sitemap")
        self.assertEqual(len(films), len(FILMS), f"beklenen {len(FILMS)} film, bulunan {len(films)}")
        for film in FILMS:
            self.assertIn(film["id"], films, f"{film['id']} katalogda yok")
        # doğrulama: hepsi oynatılabilir işaretli
        for film in FILMS:
            self.assertTrue(films[film["id"]].get("ok"), f"{film['id']} oynatılabilir değil")
        # gömme sayfasında mp4 olmayan film şablondan üretildi
        self.assertEqual(films["300002"]["stream"], f"{BASE}/300002.mp4")
        # ölü CDN alan adı veren film çalışan alan adına düştü
        self.assertEqual(urlparse(films["300003"]["stream"]).netloc, urlparse(BASE).netloc)
        # HLS filmi m3u8 olarak kaydedildi
        self.assertTrue(films["300004"]["stream"].endswith(".m3u8"))
        # başlık/kategori/afiş çıkarımı
        self.assertEqual(films["300001"]["title"], "Deneme Filmi Bir")
        self.assertIn("HD", films["300001"]["categories"])
        self.assertEqual(films["300001"]["poster"], f"{BASE}/img/300001.jpg")
        self.assertIn("HD", db["categories"])
        # çalışan Referer film kaydına işlendi (harici oynatıcılar/M3U için)
        ref = films["300001"].get("ref") or ""
        self.assertTrue(ref, "referer kaydedilmedi")
        self.assertEqual(urlparse(ref).netloc, urlparse(BASE).netloc)
        # playlistler üretildi
        with open(config.PLAYLIST_M3U, encoding="utf-8") as handle:
            m3u = handle.read()
        self.assertIn("/300001.mp4", m3u)
        self.assertIn("Referer=", m3u)
        with open(config.LOCAL_PLAYLIST_M3U, encoding="utf-8") as handle:
            local = handle.read()
        self.assertIn("/stream/300001", local)
        # meta bilgisi öğrenildi
        self.assertEqual(db["meta"].get("cdn_host"), urlparse(BASE).netloc)
        self.assertEqual(db["meta"]["last_scan"]["source"], "sitemap")

    def test_rest_scan_uses_api_metadata(self):
        stats = self.scan(mode="rest")
        films, db = self.load_films()
        self.assertIn("rest", stats["source"])
        self.assertEqual(len(films), len(FILMS))
        self.assertTrue(all(f.get("ok") for f in films.values()))
        self.assertTrue(all(f.get("date") for f in films.values()))
        self.assertEqual(db["categories"].get("HD"), f"{BASE}/hd-porno-i/")

    def test_crawl_scan_builds_category_pagination_correctly(self):
        config.MAX_PAGES_PER_CATEGORY = 3
        stats = self.scan(mode="crawl", max_pages=2)
        films, _db = self.load_films()
        # ESKİ HATA: ".../hd-porno-ipage/2/" üretiliyordu. Doğrusu istenmiş olmalı.
        self.assertIn("/hd-porno-i/page/2/", STATE.hits,
                      f"kategori 2. sayfası hiç istenmedi: {list(STATE.hits)[:20]}")
        self.assertNotIn("/hd-porno-ipage/2/", STATE.hits)
        # kategori 2. sayfasında listelenen filmler bulunmuş olmalı
        self.assertTrue(len(films) >= 4, f"crawl sadece {len(films)} film buldu")
        self.assertIn("crawl", stats["source"])

    def test_incremental_scan_keeps_existing_and_adds_new(self):
        self.scan(mode="sitemap")
        films_before, db_before = self.load_films()
        first_added = films_before["300001"]["added"]
        # ikinci tarama: aynı filmler tekrar bulunur, çift kayıt oluşmaz
        config.DISCOVERY_MODE = "sitemap"
        scraper.scan(verbose=False, full=False, limit=10)
        films_after, db_after = self.load_films()
        self.assertEqual(len(films_after), len(films_before))
        self.assertEqual(db_after["total"], len(films_after))
        self.assertEqual(films_after["300001"]["added"], first_added)
        urls = [f["url"] for f in db_after["films"]]
        self.assertEqual(len(urls), len(set(urls)), "çift kayıt oluştu")

    def test_retarget_streams_moves_stale_cdn_host(self):
        db = {"films": [
            {"id": "1", "video_id": "300001", "stream": "https://old-cdn.invalid/300001.mp4"},
            {"id": "2", "video_id": "300002", "stream": "https://old-cdn.invalid/videos/300002.mp4"},
        ], "meta": {}}
        moved = scraper.retarget_streams(db, "cdn.new-host.com")
        self.assertEqual(moved, 2)
        self.assertEqual(db["films"][0]["stream"], "https://cdn.new-host.com/300001.mp4")
        self.assertEqual(db["films"][1]["stream"], "https://cdn.new-host.com/videos/300002.mp4")
        self.assertIn("https://old-cdn.invalid/300001.mp4", db["films"][0]["alt_streams"])
        # Taşınan kayıt "bozuk" değil "henüz doğrulanmadı" sayılmalı.
        self.assertNotIn("ok", db["films"][0])

    def test_stable_ids_are_deterministic(self):
        self.assertEqual(scraper.stable_id("a-b-c"), scraper.stable_id("a-b-c"))
        self.assertNotEqual(scraper.stable_id("a-b-c"), scraper.stable_id("x-y-z"))
        self.assertTrue(scraper.stable_id("a").startswith("s"))


class TestRepair(PipelineCase):
    def test_repair_fixes_dead_streams(self):
        self.scan(mode="sitemap")
        films, _db = self.load_films()
        # bir filmin kaynağını bilerek boz
        target = films["300001"]
        target["stream"] = "http://127.0.0.1:9/300001.mp4"   # kapalı port
        target["ok"] = False
        target["alt_streams"] = []
        scraper.save_db({"films": list(films.values()), "meta": {}, "categories": {}})
        # önbelleği de temizle ki gerçekten yeniden çözsün
        os.remove(config.MEDIA_CACHE)
        server.RESOLVER = resolver_mod.MediaResolver(cache_file=config.MEDIA_CACHE)

        stats = scraper.repair(limit=5, workers=2, verbose=False)
        self.assertGreaterEqual(stats["checked"], 1)
        films_after, _db = self.load_films()
        fixed = films_after["300001"]
        self.assertTrue(fixed.get("ok"), f"onarım başarısız: {stats}")
        self.assertEqual(urlparse(fixed["stream"]).netloc, urlparse(BASE).netloc)
        self.assertEqual(fixed["stream"], f"{BASE}/300001.mp4")

    def test_unreachable_network_does_not_mark_films_broken(self):
        """CDN'e hiç ulaşılamıyorsa (CI/VPN/DNS) kayıtlar bozuk işaretlenmemeli."""
        db = {"films": [{
            "id": "888001", "video_id": "888001", "title": "Erişilemeyen film",
            # kapalı port + ulaşılamayan alan adı: hiçbir aday HTTP cevabı vermez
            "stream": "http://127.0.0.1:9/888001.mp4",
            "url": "http://127.0.0.1:9/erisilemeyen-film-bu/",
            "embed": "", "categories": [], "ok": True,
        }], "meta": {}, "categories": {}}
        scraper.save_db(db)
        config.CDN_HOSTS = []
        config.MIRROR_HOSTS = []
        server.RESOLVER = resolver_mod.MediaResolver(cache_file=config.MEDIA_CACHE)
        stats = scraper.repair(workers=1, verbose=False)
        self.assertEqual(stats.get("unreachable"), 1, stats)
        self.assertEqual(stats["dead"], 0, stats)
        films, _ = self.load_films()
        self.assertIsNone(films["888001"].get("ok"), "erişilemeyen ağ 'bozuk' diye işaretlendi")

    def test_repair_marks_unplayable_film(self):
        db = {"films": [{"id": "999999", "video_id": "999999",
                         "title": "Olmayan film",
                         "stream": "http://127.0.0.1:9/999999.mp4",
                         "url": f"{BASE}/olmayan-film-bu-slug/", "categories": []}],
              "meta": {}, "categories": {}}
        scraper.save_db(db)
        stats = scraper.repair(workers=1, verbose=False)
        self.assertEqual(stats["dead"], 1)
        films, _ = self.load_films()
        self.assertFalse(films["999999"].get("ok"))


class TestServerPlayback(PipelineCase):
    @classmethod
    def _start_server(cls):
        httpd = server.Server(("127.0.0.1", 0), server.Handler)
        httpd.daemon_threads = True
        httpd.handle_error = quiet_handle_error
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"

    def setUp(self):
        super().setUp()
        self.scan(mode="sitemap")
        server.get_db(force=True)
        self.httpd, self.site = self._start_server()
        self.session = requests.Session()
        self.session.headers.update({"Connection": "close"})

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.session.close()
        super().tearDown()

    def get(self, path, **kwargs):
        url = self.site + path
        kwargs.setdefault("timeout", 20)
        return self.session.get(url, **kwargs)

    # ------------------------------------------------------------------ #
    def test_index_and_json_endpoints(self):
        r = self.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("EVOLI", r.text)

        r = self.get("/films.json", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(len(data["films"]), len(FILMS))

        r = self.get("/api/stats")
        payload = r.json()
        self.assertEqual(payload["total"], len(FILMS))
        self.assertEqual(payload["playable"], len(FILMS))

        r = self.get("/api/films?q=Deneme")
        self.assertEqual(len(r.json()), len(FILMS))

        r = self.get("/api/films?limit=2&offset=0")
        payload = r.json()
        self.assertEqual(payload["total"], len(FILMS))
        self.assertEqual(len(payload["films"]), 2)

        r = self.get("/api/categories")
        self.assertIn("HD", r.json())

        r = self.get("/api/status")
        self.assertIn("resolver", r.json())

        r = self.get("/api/films?full=1")
        self.assertIn("meta", r.json())

    def test_m3u_endpoints(self):
        r = self.get("/m3u")
        self.assertEqual(r.status_code, 200)
        self.assertIn("#EXTM3U", r.text)
        r = self.get("/playlist_local.m3u")
        self.assertEqual(r.status_code, 200)
        self.assertIn("/stream/", r.text)

    def test_stream_full_body(self):
        r = self.get("/stream/300001")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, VIDEO_BODY)
        self.assertEqual(int(r.headers["Content-Length"]), VIDEO_SIZE)
        self.assertEqual(r.headers.get("Accept-Ranges"), "bytes")
        self.assertTrue(r.headers.get("X-Evoli-Source"))

    def test_stream_range_passthrough(self):
        r = self.get("/stream/300001", headers={"Range": "bytes=1000-1999"})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.headers["Content-Range"], f"bytes 1000-1999/{VIDEO_SIZE}")
        self.assertEqual(r.content, VIDEO_BODY[1000:2000])
        self.assertEqual(int(r.headers["Content-Length"]), 1000)

    def test_stream_range_emulation_when_upstream_ignores_range(self):
        # Kaynağı Range'i yok sayan uç noktaya çevir.
        films, db = self.load_films()
        films["300005"]["stream"] = f"{BASE}/300005.mp4?norange=1"
        films["300005"]["ref"] = f"{BASE}/{FILMS[4]['slug']}/"
        scraper.save_db(db)
        server.get_db(force=True)
        server.RESOLVER = resolver_mod.MediaResolver(cache_file=config.MEDIA_CACHE)

        r = self.get("/stream/300005", headers={"Range": "bytes=5000-5999"})
        self.assertEqual(r.status_code, 206, r.text[:200])
        self.assertEqual(r.headers["Content-Range"], f"bytes 5000-5999/{VIDEO_SIZE}")
        self.assertEqual(r.content, VIDEO_BODY[5000:6000])
        self.assertEqual(int(r.headers["Content-Length"]), 1000)

    def test_head_request_has_no_body(self):
        r = self.session.head(self.site + "/stream/300001", timeout=20)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"")
        self.assertEqual(int(r.headers["Content-Length"]), VIDEO_SIZE)

    def test_dead_stream_falls_back_and_plays(self):
        films, db = self.load_films()
        films["300002"]["stream"] = "http://127.0.0.1:9/300002.mp4"   # ölü kaynak
        films["300002"]["ok"] = False
        scraper.save_db(db)
        server.get_db(force=True)
        server.RESOLVER = resolver_mod.MediaResolver(cache_file=config.MEDIA_CACHE)

        r = self.get("/stream/300002")
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(r.content, VIDEO_BODY)
        self.assertNotEqual(r.headers.get("X-Evoli-Source"), "", "hangi kaynaktan geldiği bilinmiyor")

    def test_stale_referer_falls_back_to_working_one(self):
        """Gerçek dünyadaki asıl hata: config'teki bayat Referer 403 üretiyor.

        Kayıtta sadece bayat referer varsa sunucu aday listesinde ilerleyip
        çalışan refereri bulmalı ve videoyu yine de oynatmalı.
        """
        films, db = self.load_films()
        films["399001"] = {
            "id": "399001",
            "video_id": "300005",
            "title": "Bayat Refererlı Film",
            "stream": f"{BASE}/300005.mp4",
            "ref": config.REFERER,          # bilerek bayat/yanlış alan adı
            "poster": f"{BASE}/img/300005.jpg",
            "categories": ["HD"],
            "url": "",
            "embed": "",
            "ok": False,
        }
        db["films"] = list(films.values())
        scraper.save_db(db)
        server.get_db(force=True)
        server.RESOLVER = resolver_mod.MediaResolver(cache_file=config.MEDIA_CACHE)
        with STATE.lock:
            STATE.rejected.clear()

        r = self.get("/stream/399001")
        self.assertEqual(r.status_code, 200, r.text[:200])
        self.assertEqual(r.content, VIDEO_BODY)
        self.assertGreaterEqual(STATE.rejected["video"], 1,
                                "bayat referer hiç denenmedi — aday sırası yanlış")

    def test_unknown_film_returns_json_404(self):
        r = self.get("/stream/yok-boyle-bir-film")
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.json()["ok"])

    def test_poster_proxy(self):
        r = self.get("/poster/300001")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, POSTER_BODY)
        self.assertIn("image", r.headers["Content-Type"])

    def test_hls_playlist_is_rewritten_and_segments_play(self):
        r = self.get("/stream/300004")
        self.assertEqual(r.status_code, 200, r.text[:200])
        self.assertIn("mpegurl", r.headers["Content-Type"])
        body = r.text
        self.assertNotIn("/hls/300004/seg0.ts", body, "playlist ham adresle sızdı")
        self.assertRegex(body, r"/hls/300004/\d+")
        segment_paths = [line for line in body.splitlines() if line.startswith("/hls/")]
        self.assertGreaterEqual(len(segment_paths), 2)
        seg = self.get(segment_paths[0])
        self.assertEqual(seg.status_code, 200)
        self.assertEqual(seg.content, SEGMENT_BODY)
        # EXT-X-KEY URI da yeniden yazılmış olmalı
        key_line = [line for line in body.splitlines() if "EXT-X-KEY" in line]
        self.assertTrue(key_line and "/hls/300004/" in key_line[0])

    def test_api_resolve_refresh(self):
        r = self.get("/api/resolve/300001?refresh=1")
        payload = r.json()
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["url"], f"{BASE}/300001.mp4")
        self.assertEqual(payload["stream_url"], "/stream/300001")

    def test_api_repair_job(self):
        r = self.get("/api/repair?limit=2")
        self.assertIn(r.status_code, (200, 202))
        # iş durumu uç noktası her zaman cevap verir
        for _ in range(40):
            status = self.get("/api/repair/status").json()
            if not status.get("running"):
                break
            time.sleep(0.25)
        self.assertFalse(status.get("running"))

    def test_static_security_and_missing_files(self):
        r = self.get("/../../etc/passwd")
        self.assertIn(r.status_code, (403, 404))
        r = self.get("/olmayan-dosya.xyz")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
