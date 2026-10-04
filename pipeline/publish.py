"""Instagram publishing via the official Content Publishing API.

The flow is deliberately two-step and cannot be collapsed:

    POST /{ig}/media        -> container id        (Instagram cURLs image_url)
    GET  /{container}       -> status_code         (poll until FINISHED)
    POST /{ig}/media_publish-> media id            (this is what counts
                                                    against the daily quota)

Containers expire 24h after creation, so a failed publish must rebuild the
container rather than retry the same id. Rate limit is checked first so a
near-cap account degrades gracefully instead of hard-failing mid-run.
"""
from __future__ import annotations

import time
import urllib.parse

import requests


class InstagramError(RuntimeError):
    """Carries the Graph API error code/subcode for logging."""

    def __init__(self, message: str, code=None, subcode=None):
        super().__init__(message)
        self.code = code
        self.subcode = subcode


# Graph error 2207042: daily publishing limit reached.
RATE_LIMIT_CODE = 2207042


class InstagramClient:
    def __init__(self, token: str, cfg: dict, log=print):
        ig = cfg["instagram"]
        self.token = token
        self.log = log
        self.version = ig["api_version"]
        self.host = ig["hosts"][ig["login_path"]]
        self.container_timeout = int(ig["container_timeout_sec"])
        self.container_poll = int(ig["container_poll_sec"])
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        )

    # ---------------------------------------------------------------- api
    def _url(self, path: str) -> str:
        return f"{self.host}/{self.version}/{path}"

    def _raise(self, resp: requests.Response) -> None:
        try:
            err = resp.json().get("error", {})
        except ValueError:
            err = {}
        code = err.get("code")
        sub = err.get("error_subcode")
        msg = err.get("message", resp.text[:300])
        if resp.status_code == 429:
            msg = f"rate limited: {msg}"
        raise InstagramError(
            f"{msg} (code={code}, subcode={sub}, http={resp.status_code})",
            code=code,
            subcode=sub,
        )

    def _post(self, path: str, **data) -> dict:
        r = self.session.post(self._url(path), data=data, timeout=45)
        if r.status_code >= 400:
            self._raise(r)
        return r.json()

    def _get(self, path: str, **params) -> dict:
        r = self.session.get(self._url(path), params=params, timeout=45)
        if r.status_code >= 400:
            self._raise(r)
        return r.json()

    # ------------------------------------------------------------ identity
    def user_id(self) -> str:
        """Resolve the IG user id from the token."""
        data = self._get("me", fields="id,username,account_type")
        self.log(
            f"  [ig] connected: @{data.get('username')} "
            f"({data.get('account_type')}) id={data.get('id')}"
        )
        return str(data["id"])

    def quota(self, user_id: str) -> tuple[int, int]:
        """(used, total) in the current 24h window."""
        data = self._get(f"{user_id}/content_publishing_limit")
        rows = data.get("data") or []
        if not rows:
            return 0, 50
        row = rows[0]
        cfg = row.get("config") or {}
        return int(row.get("quota_usage", 0)), int(cfg.get("quota_total", 50))

    # ----------------------------------------------------------- publishing
    def create_container(self, user_id: str, image_url: str, caption: str) -> str:
        data = self._post(
            f"{user_id}/media",
            media_type="IMAGE",
            image_url=image_url,
            caption=caption,
        )
        cid = str(data.get("id", ""))
        if not cid:
            raise InstagramError(f"container creation returned no id: {data}")
        self.log(f"  [ig] container {cid} created")
        return cid

    def wait_ready(self, container_id: str) -> dict:
        """Poll until the container is FINISHED (or fails/expires)."""
        deadline = time.time() + self.container_timeout
        last = {}
        while time.time() < deadline:
            last = self._get(container_id, fields="status_code,status")
            status = last.get("status_code")
            if status == "FINISHED":
                return last
            if status in ("ERROR", "EXPIRED"):
                raise InstagramError(
                    f"container {container_id} {status}: {last.get('status')}"
                )
            time.sleep(self.container_poll)
        raise InstagramError(
            f"container {container_id} not ready within "
            f"{self.container_timeout}s (last={last})"
        )

    def publish(self, container_id: str) -> str:
        try:
            data = self._post(f"media_publish", creation_id=container_id)
        except InstagramError as e:
            if e.code == RATE_LIMIT_CODE:
                raise InstagramError(
                    "daily publishing limit reached — back off until tomorrow",
                    code=e.code,
                    subcode=e.subcode,
                ) from e
            raise
        mid = str(data.get("id", ""))
        if not mid:
            raise InstagramError(f"publish returned no media id: {data}")
        return mid

    def permalink(self, media_id: str) -> str:
        data = self._get(media_id, fields="permalink")
        return data.get("permalink", "")

    def publish_image(self, user_id: str, image_url: str, caption: str) -> dict:
        """Full create -> wait -> publish -> permalink sequence."""
        cid = self.create_container(user_id, image_url, caption)
        self.wait_ready(cid)
        media_id = self.publish(cid)
        try:
            link = self.permalink(media_id)
        except InstagramError:
            link = ""
        self.log(f"  [ig] published {media_id}  {link}")
        return {"media_id": media_id, "permalink": link, "container_id": cid}


def image_url(base_url: str, filename: str) -> str:
    return f"{base_url.rstrip('/')}/images/{urllib.parse.quote(filename)}"
