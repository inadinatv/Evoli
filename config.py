# -*- coding: utf-8 -*-
BASE_URL = "https://1ppa99.evooli.com"
SITENAME = "Evooli"

USER_AGENT = "Mozilla/5.0 (Linux; Android 15; 2412DPC0AG Build/AP3A.240905.015.A2; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 Mobile Safari/537.36"
REFERER = "https://www.evoolipxnyxzq.shop/"

PORT = 8000
SCAN_INTERVAL_HOURS = 6
PAGES_PER_SCAN = 5
REQUEST_DELAY = 0.6

import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILMS_JSON = os.path.join(BASE_DIR, "films.json")
PLAYLIST_M3U = os.path.join(BASE_DIR, "playlist.m3u")
