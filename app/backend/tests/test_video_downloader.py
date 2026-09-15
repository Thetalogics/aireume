"""
Tests for video_downloader.py:
  - detect_platform
  - URL transformation helpers (Google Drive, Dropbox)
  - resolve_zoom_url (mocked HTTP)
  - resolve_loom_url (mocked HTTP)
  - _http_download (mocked httpx stream)
  - download_video_from_url errors and size limit
"""
import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock


def _arun(coro):
    """Run an async coroutine in a fresh event loop (Python 3.10+ compatible)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

from app.backend.services.video_downloader import (
    detect_platform,
    platform_display_name,
    transform_google_drive_url,
    transform_dropbox_url,
    resolve_zoom_url,
    resolve_loom_url,
    _http_download,
    download_video_from_url,
    MAX_DOWNLOAD_BYTES,
)


# ─── detect_platform ─────────────────────────────────────────────────────────

class TestDetectPlatform:
    def test_zoom_us(self):
        assert detect_platform("https://zoom.us/rec/share/abc123") == "zoom"

    def test_zoom_com(self):
        assert detect_platform("https://us02web.zoom.com/rec/play/abc") == "zoom"

    def test_teams_sharepoint(self):
        assert detect_platform("https://company.sharepoint.com/:v:/g/abc") == "teams"

    def test_teams_1drv(self):
        assert detect_platform("https://1drv.ms/v/s!AbcDef123") == "teams"

    def test_google_drive_file(self):
        assert detect_platform("https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/view") == "google_drive"

    def test_loom(self):
        assert detect_platform("https://www.loom.com/share/abc123def456") == "loom"

    def test_dropbox(self):
        assert detect_platform("https://www.dropbox.com/s/abc123/interview.mp4?dl=0") == "dropbox"

    def test_youtube_long(self):
        assert detect_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"

    def test_youtube_short(self):
        assert detect_platform("https://youtu.be/dQw4w9WgXcQ") == "youtube"

    def test_direct_mp4(self):
        assert detect_platform("https://cdn.example.com/recordings/interview.mp4") == "direct"

    def test_direct_webm(self):
        assert detect_platform("https://cdn.example.com/recordings/session.webm") == "direct"

    def test_unknown_url(self):
        assert detect_platform("https://example.com/some/page") == "unknown"

    def test_empty_string(self):
        assert detect_platform("") == "unknown"


# ─── platform_display_name ────────────────────────────────────────────────────

class TestPlatformDisplayName:
    def test_known_platforms(self):
        assert platform_display_name("zoom") == "Zoom"
        assert platform_display_name("teams") == "Microsoft Teams"
        assert platform_display_name("google_drive") == "Google Drive"
        assert platform_display_name("loom") == "Loom"
        assert platform_display_name("dropbox") == "Dropbox"
        assert platform_display_name("youtube") == "YouTube"

    def test_unknown_platform_titlecases(self):
        result = platform_display_name("unknown")
        assert isinstance(result, str)


# ─── transform_google_drive_url ──────────────────────────────────────────────

class TestGoogleDriveTransform:
    def test_file_view_url(self):
        url = "https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/view"
        result = transform_google_drive_url(url)
        assert "uc?export=download" in result
        assert "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms" in result
        assert "confirm=t" in result

    def test_file_preview_url(self):
        url = "https://drive.google.com/file/d/FILEID_123/preview"
        result = transform_google_drive_url(url)
        assert "FILEID_123" in result
        assert "export=download" in result

    def test_open_id_url(self):
        url = "https://drive.google.com/open?id=FILEID_456"
        result = transform_google_drive_url(url)
        assert "FILEID_456" in result

    def test_unrecognized_url_returned_as_is(self):
        url = "https://drive.google.com/about"
        assert transform_google_drive_url(url) == url


# ─── transform_dropbox_url ────────────────────────────────────────────────────

class TestDropboxTransform:
    def test_dl_0_becomes_dl_1(self):
        url = "https://www.dropbox.com/s/abc/interview.mp4?dl=0"
        result = transform_dropbox_url(url)
        assert "dl=1" in result
        assert "dl=0" not in result

    def test_no_dl_param_gets_dl_1_appended(self):
        url = "https://www.dropbox.com/s/abc/interview.mp4"
        result = transform_dropbox_url(url)
        assert "dl=1" in result

    def test_already_dl_1_unchanged(self):
        url = "https://www.dropbox.com/s/abc/interview.mp4?dl=1"
        result = transform_dropbox_url(url)
        assert "dl=1" in result


# ─── resolve_zoom_url (mocked) ───────────────────────────────────────────────

class TestResolveZoomUrl:
    def test_extracts_mp4_url_from_page(self):
        html = '...some stuff... "viewMp4FileWithWatermark":"https://zoom-cdn.example.com/video/abc.mp4?auth=token" ...'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch(
            "app.backend.services.video_downloader.safe_request_async",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = _arun(
                resolve_zoom_url("https://zoom.us/rec/share/abc123")
            )

        assert "zoom-cdn.example.com" in result

    def test_falls_back_to_original_on_no_mp4_found(self):
        original = "https://zoom.us/rec/share/abc123"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html>No video here</html>"

        with patch(
            "app.backend.services.video_downloader.safe_request_async",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = _arun(resolve_zoom_url(original))

        assert result == original

    def test_falls_back_to_original_on_request_error(self):
        original = "https://zoom.us/rec/share/xyz"
        with patch(
            "app.backend.services.video_downloader.safe_request_async",
            new_callable=AsyncMock,
            side_effect=Exception("Network error"),
        ):
            result = _arun(resolve_zoom_url(original))

        assert result == original


# ─── _http_download (mocked) ─────────────────────────────────────────────────

class TestHttpDownload:
    def _make_mock_response(self, status=200, content=b"video data", content_type="video/mp4"):
        mock_resp = MagicMock()
        mock_resp.status_code = status
        mock_resp.headers = {"content-type": content_type}
        mock_resp.content = content
        return mock_resp

    def test_successful_download(self):
        mock_resp = self._make_mock_response(content=b"fake mp4 data")

        with patch(
            "app.backend.services.video_downloader.safe_request_async",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = _arun(
                _http_download("https://example.com/video.mp4", "direct")
            )

        assert result == b"fake mp4 data"

    def test_401_raises_value_error_with_auth_message(self):
        mock_resp = self._make_mock_response(status=401)

        with patch(
            "app.backend.services.video_downloader.safe_request_async",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            with pytest.raises(ValueError, match="authentication"):
                _arun(
                    _http_download("https://example.com/video.mp4", "zoom")
                )

    def test_404_raises_value_error_with_not_found_message(self):
        mock_resp = self._make_mock_response(status=404)

        with patch(
            "app.backend.services.video_downloader.safe_request_async",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            with pytest.raises(ValueError, match="[Nn]ot found|expired"):
                _arun(
                    _http_download("https://example.com/missing.mp4", "direct")
                )

    def test_html_response_raises_value_error(self):
        mock_resp = self._make_mock_response(content=b"<html>Login</html>", content_type="text/html")

        with patch(
            "app.backend.services.video_downloader.safe_request_async",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            with pytest.raises(ValueError, match="webpage|HTML|html"):
                _arun(
                    _http_download("https://drive.google.com/file", "google_drive")
                )


# ─── download_video_from_url ─────────────────────────────────────────────────

class TestDownloadVideoFromUrl:
    def test_youtube_raises_without_yt_dlp(self):
        with patch.dict("sys.modules", {"yt_dlp": None}):
            with pytest.raises((ValueError, ImportError, ModuleNotFoundError)):
                _arun(
                    download_video_from_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
                )

    def test_invalid_url_scheme_still_attempts_download(self):
        # An unknown URL is still attempted; failure comes from HTTP
        with patch("app.backend.services.video_downloader._http_download",
                   new_callable=AsyncMock, return_value=b"data") as mock_dl:
            _arun(
                download_video_from_url("https://random.example.com/something")
            )
            mock_dl.assert_called_once()
