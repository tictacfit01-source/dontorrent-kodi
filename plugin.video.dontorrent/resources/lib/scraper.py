import re
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from . import domain

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 15

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
}

# --- URL paths confirmed against dontorrent.reisen (Nov 2026) ---------------
SECTION_PATH = {
    "movie":       "peliculas",
    "movie_hd":    "peliculas/hd",
    "movie_4k":    "peliculas/4K",
    "tvshow":      "series",
    "tvshow_hd":   "series/hd",
    "tvshow_4k":   "series/4k",
    "documentary": "documentales",
    "estrenos":    "",
}

ITEM_PATTERNS = {
    "movie":       re.compile(r"^/pelicula/\d+/"),
    "tvshow":      re.compile(r"^/serie/\d+/\d+/"),
    "documentary": re.compile(r"^/documental/\d+/\d+/"),
}


def _classify(href):
    for kind, pat in ITEM_PATTERNS.items():
        if pat.match(href):
            return kind
    return None


def _get(path):
    url = urljoin(domain.base_url() + "/", path.lstrip("/"))
    r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser"), r.url


_TITLE_CACHE = {}


def fetch_detail_title(url):
    """Fetch the detail page's <h1> text, which (unlike the URL slug) keeps
    the original Spanish accents. Cached per URL to keep listing cheap."""
    if not url:
        return None
    if url in _TITLE_CACHE:
        return _TITLE_CACHE[url]
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        r.raise_for_status()
        s = BeautifulSoup(r.text, "html.parser")
        h1 = s.select_one("h1.descargarTitulo, h1")
        txt = h1.get_text(" ", strip=True) if h1 else None
    except Exception:
        txt = None
    _TITLE_CACHE[url] = txt
    return txt


def _post(path, data):
    url = urljoin(domain.base_url() + "/", path.lstrip("/"))
    r = requests.post(url, data=data, timeout=TIMEOUT, headers=HEADERS)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser"), r.url


_SLUG_RE = re.compile(r"/(?:pelicula|serie|documental)/\d+/(?:\d+/)?(.+?)/?$")


def _title_from_slug(href):
    m = _SLUG_RE.search(href)
    if not m:
        return None
    slug = m.group(1).split("/")[-1]
    txt = slug.replace("-", " ").replace("_", " ").strip()
    return re.sub(r"\s+", " ", txt) or None


def _upgrade_weserv(src, width=500):
    """Rewrite an images.weserv.nl URL to request a larger poster.

    The site embeds thumbnails at w=120&h=165. Weserv can reproxy the same
    original image at any size, so we swap the resize params and URL-encode
    any brackets in the upstream URL (weserv rejects raw brackets).
    """
    if "images.weserv.nl" not in src:
        return src
    src = re.sub(r"([?&])w=\d+", r"\g<1>w=" + str(width), src)
    src = re.sub(r"([?&])h=\d+&?", r"\g<1>", src).rstrip("&?")
    src = src.replace("[", "%5B").replace("]", "%5D")
    return src


def _img_src(a):
    img = a.find("img")
    if not img:
        return None
    src = img.get("src") or img.get("data-src") or img.get("data-original")
    if not src:
        return None
    if src.startswith("//"):
        src = "https:" + src
    return _upgrade_weserv(src, width=500)


_QUALITY_RE = re.compile(
    r"\(([^)]*(?:1080p|720p|2160p|4K|HDRip|BluRay|BDRip|BDremux|BRRip|WEB-?DL|WEBRip|microHD|HDTV|x264|x265|HEVC|DVDRip)[^)]*)\)",
    re.IGNORECASE,
)


def _quality_near(a):
    """On search pages the quality is a sibling <span>(BDremux-1080p)</span>
    right after the anchor. List pages don't have it; return None there."""
    for sib in a.next_siblings:
        # Tag nodes: check badge class to stop before "Película/Serie".
        get_attr = getattr(sib, "get", None)
        if callable(get_attr):
            classes = get_attr("class") or []
            if "badge" in classes:
                break
            txt = sib.get_text(" ", strip=True)
        else:
            # NavigableString
            txt = str(sib).strip()
        if not txt:
            continue
        m = _QUALITY_RE.search(txt)
        if m:
            return m.group(1).strip()
    parent = a.parent
    if parent is not None:
        m = _QUALITY_RE.search(parent.get_text(" ", strip=True))
        if m:
            return m.group(1).strip()
    return None


def _items(soup, page_url, kind_filter=None):
    items, seen = [], set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        kind = _classify(href)
        if not kind:
            continue
        if kind_filter and kind != kind_filter:
            continue
        if href in seen:
            continue
        seen.add(href)
        title = (a.get("title") or "").strip()
        if not title:
            title = (a.get_text() or "").strip()
        if not title:
            title = _title_from_slug(href) or ""
        if not title:
            continue
        title = re.sub(r"\s+", " ", title).rstrip(" .")
        items.append({
            "title": title,
            "url": urljoin(page_url, href),
            "kind": kind,
            "image": _img_src(a),
            "quality": _quality_near(a),
        })
    return items


def latest(kind="movie", page=1):
    # "Estrenos" pulls from the homepage, optionally filtered by kind
    if kind in ("estrenos", "estrenos_movie", "estrenos_tvshow"):
        path = "" if page <= 1 else f"page/{page}"
        soup, url = _get(path)
        kf = None
        if kind == "estrenos_movie":
            kf = "movie"
        elif kind == "estrenos_tvshow":
            kf = "tvshow"
        return _items(soup, url, kind_filter=kf)
    section = SECTION_PATH.get(kind, "peliculas")
    path = section if page <= 1 else f"{section}/page/{page}"
    soup, url = _get(path)
    return _items(soup, url, kind_filter=_base_kind(kind))


def _base_kind(kind):
    """Quality variants (movie_hd, movie_4k, tvshow_hd, tvshow_4k) collapse
    back to their root kind for item classification."""
    if kind.startswith("movie"):
        return "movie"
    if kind.startswith("tvshow"):
        return "tvshow"
    return kind


def search(query):
    soup, url = _post("buscar", data={"valor": query, "Buscar": "Buscar"})
    return _items(soup, url)


def detail(url):
    r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Title
    title = None
    h1 = soup.select_one("h1.descargarTitulo, h1, h2.descargarTitulo")
    if h1:
        title = h1.get_text(" ", strip=True)

    # Description
    plot = None
    for p in soup.select("p.text-justify, p"):
        txt = p.get_text(" ", strip=True)
        if txt.lower().startswith("descripci") or txt.lower().startswith("sinopsis"):
            plot = re.sub(r"^[^:]+:\s*", "", txt)
            break
        if not plot and len(txt) > 140:
            plot = txt

    # Poster from og:image (images.weserv.nl proxy - upgrade to HD)
    image = None
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        image = _upgrade_weserv(og["content"], width=500)

    # Year/genre from labelled <p> blocks
    year = None
    m = re.search(r"\b(19|20)\d{2}\b", soup.get_text(" ", strip=True))
    if m:
        year = m.group(0)

    # Downloads: each <a class="protected-download" data-content-id data-tabla>.
    # For series, each row is wrapped in <tr> with the episode label in a <td>.
    downloads = []
    for a in soup.select("a.protected-download"):
        cid = a.get("data-content-id")
        tabla = a.get("data-tabla")
        if not cid or not tabla:
            continue
        label = "Descargar"
        season = episode = None
        tr = a.find_parent("tr")
        if tr:
            tds = [t.get_text(" ", strip=True) for t in tr.find_all("td")]
            for t in tds:
                m = re.match(r"^(\d{1,2})\s*x\s*(\d{1,3})\b", t)
                if m:
                    season, episode = int(m.group(1)), int(m.group(2))
                    label = f"{season:02d}x{episode:02d}"
                    extra = t[m.end():].strip(" -()[]")
                    if extra:
                        label += f" {extra}"
                    break
            extras = [t for t in tds if t and t != label and not t.lower().startswith("descargar")]
            if extras:
                label = f"{label} - " + " · ".join(extras[:2]) if season is not None else " · ".join(extras[:2])
        downloads.append({
            "content_id": cid,
            "tabla": tabla,
            "label": label,
            "season": season,
            "episode": episode,
        })

    return {
        "title": title,
        "plot": plot,
        "image": image,
        "year": year,
        "downloads": downloads,
    }
