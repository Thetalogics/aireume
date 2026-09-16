"""
Smart video downloader — handles Zoom, Microsoft Teams, Google Drive,
Loom, Dropbox, YouTube (yt-dlp), and any direct video URL.
"""
import os
import re
import asyncio
import tempfile
import httpx
from pathlib import Path
from urllib.parse import urlparse

from app.backend.services.url_safety import UnsafeURLError, safe_request_async

DOWNLOAD_TIMEOUT = 300      # 5 minutes for large files
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024  # 500 MB

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8",
}


# ─── Platform detection ───────────────────────────────────────────────────────

def detect_platform(url: str) -> str:
    u = url.lower()
    if "zoom.us" in u or "zoom.com" in u:
        return "zoom"
    if "sharepoint.com" in u or "teams.microsoft.com" in u or "1drv.ms" in u or "onedrive.live.com" in u:
        return "teams"
    if "drive.google.com" in u or "docs.google.com/file" in u:
        return "google_drive"
    if "loom.com" in u:
        return "loom"
    if "dropbox.com" in u:
        return "dropbox"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    ext = Path(urlparse(url).path).suffix.lower()
    if ext in (".mp4", ".webm", ".avi", ".mov", ".mkv", ".m4v", ".mp3", ".m4a"):
        return "direct"
    return "unknown"


def platform_display_name(platform: str) -> str:
    names = {
        "zoom": "Zoom",
        "teams": "Microsoft Teams",
        "google_drive": "Google Drive",
        "loom": "Loom",
        "dropbox": "Dropbox",
        "youtube": "YouTube",
        "direct": "Direct URL",
        "unknown": "Unknown",
    }
    return names.get(platform, platform.title())


# ─── URL transformers ─────────────────────────────────────────────────────────

def transform_google_drive_url(url: str) -> str:
    """Convert Google Drive share/view URL → direct download URL."""
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}&confirm=t"
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}&confirm=t"
    return url


def transform_dropbox_url(url: str) -> str:
    """Convert Dropbox share URL → direct download URL."""
    url = re.sub(r"[?&]dl=0", "", url)
    sep = "&" if "?" in url else "?"
    return url + sep + "dl=1"


async def resolve_zoom_url(url: str) -> str:
    """Try to extract the direct MP4 URL from a Zoom recording page."""
    try:
        resp = await safe_request_async("GET", url, timeout=20.0, headers=BROWSER_HEADERS)
        if resp.status_code == 200:
            for pattern in [
                r'"viewMp4FileWithWatermark"\s*:\s*"([^"]+)"',
                r'"viewMp4Url"\s*:\s*"([^"]+)"',
                r'(https?://[^\s"\']+\.mp4[^\s"\']*)',
            ]:
                m = re.search(pattern, resp.text)
                if m:
                    return m.group(1).replace("\\u0026", "&")
    except UnsafeURLError:
        raise ValueError("URL is not allowed")
    except Exception:
        pass
    return url  # fall back to original


async def resolve_loom_url(url: str) -> str:
    """Attempt to get a direct Loom video download URL."""
    m = re.search(r"loom\.com/(?:share|embed)/([a-zA-Z0-9]+)", url)
    if not m:
        return url
    video_id = m.group(1)
    try:
        resp = await safe_request_async(
            "GET",
            f"https://www.loom.com/v1/videos/{video_id}/transcoded-url",
            timeout=15.0,
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 200:
            cdn = resp.json().get("url")
            if cdn:
                return cdn
    except UnsafeURLError:
        raise ValueError("URL is not allowed")
    except Exception:
        pass
    return url


# ─── Core downloader ──────────────────────────────────────────────────────────

async def download_video_from_url(url: str) -> tuple[bytes, str, str]:
    """
    Download a video from any supported public URL.

    Returns:
        (video_bytes, filename, platform_name)

    Raises:
        ValueError with a human-friendly message on failure.
    """
    platform = detect_platform(url)
    filename = "recording.mp4"
    download_url = url

    if platform == "youtube":
        video_bytes, filename = await _download_youtube(url)
        return video_bytes, filename, "YouTube"

    if platform == "google_drive":
        download_url = transform_google_drive_url(url)
        filename = "drive_recording.mp4"

    elif platform == "dropbox":
        download_url = transform_dropbox_url(url)
        filename = "dropbox_recording.mp4"

    elif platform == "zoom":
        download_url = await resolve_zoom_url(url)
        filename = "zoom_recording.mp4"

    elif platform == "loom":
        download_url = await resolve_loom_url(url)
        filename = "loom_recording.mp4"

    elif platform == "teams":
        filename = "teams_recording.mp4"
        # SharePoint/OneDrive links: append download parameter
        if "sharepoint.com" in url.lower():
            sep = "&" if "?" in url else "?"
            download_url = url + sep + "download=1"

    elif platform == "direct":
        parsed = urlparse(url)
        filename = Path(parsed.path).name or "recording.mp4"

    elif platform == "unknown":
        # Try anyway — might be a raw video URL without extension
        filename = "recording.mp4"

    video_bytes = await _http_download(download_url, platform)
    return video_bytes, filename, platform_display_name(platform)


async def _http_download(url: str, platform: str) -> bytes:
    """Download a file, respecting size limit."""
    try:
        resp = await safe_request_async(
            "GET",
            url,
            timeout=DOWNLOAD_TIMEOUT,
            max_bytes=MAX_DOWNLOAD_BYTES,
            headers=BROWSER_HEADERS,
        )
        if resp.status_code == 401:
            raise ValueError(
                "This recording requires authentication. "
                "Ensure the sharing link is set to 'Anyone with the link can view' (no login required)."
            )
        if resp.status_code == 403:
            raise ValueError(
                "Access denied. Make sure the recording is shared publicly without a password."
            )
        if resp.status_code == 404:
            raise ValueError("Recording not found. The link may have expired or been removed.")
        if resp.status_code != 200:
            raise ValueError(f"Failed to download recording (HTTP {resp.status_code}).")

        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type:
            raise ValueError(
                f"The URL returned a webpage instead of a video file. "
                f"For {platform_display_name(platform)} recordings, ensure:\n"
                "• The recording is shared publicly (no sign-in required)\n"
                "• For Zoom: use the direct share link from 'Cloud Recordings'\n"
                "• For Teams: use SharePoint → share → 'Anyone with the link'"
            )

        return resp.content

    except UnsafeURLError as e:
        if "exceeds maximum size" in str(e):
            raise ValueError(
                "Recording exceeds the 500 MB download limit. "
                "Please trim the recording or upload the file directly."
            ) from e
        raise ValueError("URL is not allowed") from e
    except httpx.TimeoutException:
        raise ValueError("Download timed out. The recording server is too slow. Try uploading the file directly.")
    except httpx.RequestError as e:
        raise ValueError(f"Network error while downloading: {str(e)}")


async def _download_youtube(url: str) -> tuple[bytes, str]:
    """Download audio track from YouTube using yt-dlp (optional dependency)."""
    try:
        import yt_dlp
    except ImportError:
        raise ValueError(
            "YouTube downloads require yt-dlp. "
            "Add 'yt-dlp' to requirements.txt and rebuild the container."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        output_tpl = os.path.join(tmpdir, "%(id)s.%(ext)s")
        opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": output_tpl,
            "quiet": True,
            "no_warnings": True,
        }
        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, _yt_dlp_download, opts, url)
            # Find the downloaded file
            for fname in os.listdir(tmpdir):
                fpath = os.path.join(tmpdir, fname)
                with open(fpath, "rb") as f:
                    return f.read(), fname
            raise ValueError("yt-dlp finished but output file not found.")
        except Exception as e:
            raise ValueError(f"Failed to download YouTube video: {str(e)}")


def _yt_dlp_download(opts: dict, url: str):
    import yt_dlp
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)
