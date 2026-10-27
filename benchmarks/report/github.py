"""Small bounded GitHub.com client. Signed downloads never receive API credentials."""

import io
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from http.client import HTTPException

from .render import json_text
from .validate import MAX_DOCUMENT_BYTES, InvalidResult, bounded_json, require

API_ORIGIN = "https://api.github.com"
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    def __init__(self, token: str):
        require(bool(token) and not any(c in token for c in "\r\n"), "GH_TOKEN is required")
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())
        self.requests = 0
        self.deadline = time.monotonic() + 180

    def _request(self, url: str, *, authenticated: bool, method="GET", body=None, limit=MAX_DOCUMENT_BYTES):
        require(self.requests < 100 and time.monotonic() < self.deadline, "GitHub request budget exceeded")
        self.requests += 1
        parsed = urllib.parse.urlsplit(url)
        require(
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.fragment,
            "Unsafe download URL",
        )
        headers = {"User-Agent": "posthog-sdk-benchmark-report", "Accept": "application/vnd.github+json"}
        if authenticated:
            require(f"{parsed.scheme}://{parsed.netloc}" == API_ORIGIN, "Untrusted API origin")
            headers.update(Authorization=f"Bearer {self.token}", **{"X-GitHub-Api-Version": "2022-11-28"})
        data = json_text(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, headers=headers, data=data, method=method)
        try:
            response = self.opener.open(request, timeout=min(20, max(1, self.deadline - time.monotonic())))
        except urllib.error.HTTPError as exc:
            response = exc
        except (OSError, ValueError, HTTPException):
            raise InvalidResult("GitHub transport failed") from None
        try:
            with response:
                status = response.code
                location = response.headers.get("Location")
                content = response.read(limit + 1)
                require(len(content) <= limit, "GitHub response exceeds limit")
                return status, location, content
        except (OSError, ValueError, HTTPException) as exc:
            if isinstance(exc, InvalidResult):
                raise
            raise InvalidResult("GitHub response could not be read") from None

    def api(self, path: str, method="GET", body=None):
        require(path.startswith("/repos/") and not any(c in path for c in "\r\n#"), "Invalid API path")
        status, _, data = self._request(API_ORIGIN + path, authenticated=True, method=method, body=body)
        require(200 <= status < 300, f"GitHub API request failed (HTTP {status})")
        # Compare responses may contain large patches; only their merge-base metadata is consumed.
        comparison = method == "GET" and re.fullmatch(
            r"/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/compare/[0-9a-f]{40}\.\.\.[0-9a-f]{40}", path
        )
        return bounded_json(data, text_limit=MAX_DOCUMENT_BYTES if comparison else 65536) if data else None

    def pages(self, path: str, key: str | None = None) -> list:
        result = []
        for page in range(1, 11):
            response = self.api(f"{path}?per_page=100&page={page}")
            items = response[key] if key else response
            require(isinstance(items, list) and len(items) <= 100, "Invalid paginated API response")
            result.extend(items)
            if len(items) < 100:
                return result
        raise InvalidResult("GitHub pagination limit exceeded")

    def download(self, path: str) -> bytes:
        require(
            re.fullmatch(r"/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/artifacts/[1-9][0-9]*/zip", path),
            "Invalid artifact endpoint",
        )
        status, location, _ = self._request(API_ORIGIN + path, authenticated=True)
        require(status == 302 and location, "Expected signed artifact download redirect")
        for _ in range(3):
            status, next_location, data = self._request(location, authenticated=False, limit=MAX_ARCHIVE_BYTES)
            if status == 200:
                return data
            require(status in (301, 302, 303, 307, 308) and next_location, "Artifact download failed")
            location = urllib.parse.urljoin(location, next_location)
        raise InvalidResult("Artifact redirect limit exceeded")


def read_archive(data: bytes) -> dict[str, bytes]:
    require(len(data) <= MAX_ARCHIVE_BYTES, "Compressed artifact exceeds size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            require(len(entries) == 3, "Artifact must contain exactly three result files")
            require(
                {entry.filename for entry in entries} == {"base.json", "head.json", "revisions.json"},
                "Unexpected or duplicate artifact filename",
            )
            require(
                sum(entry.file_size for entry in entries) <= MAX_ARCHIVE_BYTES,
                "Uncompressed artifact exceeds size limit",
            )
            result = {}
            for entry in entries:
                require(entry.orig_filename == entry.filename, "Invalid artifact filename")
                mode = stat.S_IFMT(entry.external_attr >> 16)
                require(mode in (0, stat.S_IFREG) and not entry.is_dir(), "Artifact contains a non-regular file")
                require(not entry.flag_bits & 1, "Encrypted artifacts are unsupported")
                require(
                    entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), "Unsupported ZIP compression"
                )
                limit = 8192 if entry.filename == "revisions.json" else MAX_DOCUMENT_BYTES
                require(entry.file_size <= limit, "Artifact member exceeds size limit")
                require(
                    entry.file_size <= max(1, entry.compress_size) * 200, "Artifact compression ratio exceeds limit"
                )
                with archive.open(entry) as stream:
                    content = stream.read(limit + 1)
                require(len(content) == entry.file_size and len(content) <= limit, "Invalid ZIP member size")
                result[entry.filename] = content
            return result
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, NotImplementedError, UnicodeError, zlib.error):
        raise InvalidResult("Malformed ZIP artifact") from None
