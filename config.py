# -*- coding: utf-8 -*-
"""Tüm ayarlar tek dosyada. İstediğini buradan değiştir."""

# Kaynak site
BASE_URL = "https://1ppa99.evooli.com"
SITENAME = "Evooli"

# IPTV oynatıcılar için header bilgileri
USER_AGENT = "Mozilla/5.0 (Linux; Android 15; 2412DPC0AG Build/AP3A.240905.015.A2; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 Mobile Safari/537.36"
REFERER = "https://www.evoolipxnyxzq.shop/"

# Sunucu
PORT = 8000

# Otomatik tarama: kaç saatte bir site kontrol edilsin?
SCAN_INTERVAL_HOURS = 6

# Her taramada kaç sayfa gezilsin?
PAGES_PER_SCAN = 5

# İstekler arası bekleme (saniye) - IP ban yememek için
REQUEST_DELAY = 0.6

# Veri yolları (otomatik oluşturulur)
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
FILMS_JSON = os.path.join(DATA_DIR, "films.json")
PLAYLIST_M3U = os.path.join(DATA_DIR, "playlist.m3u")

os.makedirs(DATA_DIR, exist_ok=True)
