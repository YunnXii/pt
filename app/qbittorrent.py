import time
from typing import Iterable

import httpx


class QBittorrentError(RuntimeError):
    pass


# 按“地址 + 用户名 + 密码”做短时退避。这样旧密码失败时不会被状态轮询连续轰炸，
# 用户改成新密码后又可以立即重试，不必等退避时间结束。
_AUTH_BACKOFF = {}


class QBittorrentClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self.base = (self.cfg.get("qbit_url") or "http://127.0.0.1:8080").rstrip("/")
        self.username = (self.cfg.get("qbit_username") or "admin").strip()
        self.password = self.cfg.get("qbit_password") or ""
        self.tag = (self.cfg.get("qbit_tag") or "mteam-box").strip()
        self.category = (self.cfg.get("qbit_category") or "mteam-box").strip()
        self.download_dir = (self.cfg.get("download_dir") or "").strip()

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=30.0, follow_redirects=True)

    def _auth_key(self):
        return (self.base, self.username, self.password)

    def _set_backoff(self, seconds: int, reason: str):
        _AUTH_BACKOFF[self._auth_key()] = (time.monotonic() + max(1, int(seconds)), reason)

    def _clear_backoff(self):
        _AUTH_BACKOFF.pop(self._auth_key(), None)

    def _login(self, c: httpx.Client):
        if not self.password:
            raise QBittorrentError(
                "qBittorrent 密码尚未配置；为避免 WebUI 连续失败触发 IP 封禁，已跳过登录"
            )

        blocked = _AUTH_BACKOFF.get(self._auth_key())
        if blocked:
            until, reason = blocked
            left = int(until - time.monotonic())
            if left > 0:
                raise QBittorrentError(f"{reason}；{left}s 后自动允许重试，修改账号/密码可立即重试")
            self._clear_backoff()

        r = c.post(
            self.base + "/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
            headers={"Referer": self.base + "/"},
        )
        if r.status_code == 403:
            msg = (
                "qBittorrent 登录 HTTP 403：当前请求 IP 已因连续登录失败被 WebUI 临时封禁。"
                "先停止重复测试，重启 qBittorrent 可立即清除本次内存封禁，再用正确账号密码重试"
            )
            self._set_backoff(300, msg)
            raise QBittorrentError(msg)
        if r.status_code >= 400:
            self._set_backoff(60, f"qBittorrent 登录 HTTP {r.status_code}")
            raise QBittorrentError(f"qBittorrent 登录 HTTP {r.status_code}")
        text = (r.text or "").strip().lower()
        if text not in ("ok.", "ok"):
            msg = "qBittorrent 登录失败，请检查 WebUI 用户名/密码"
            self._set_backoff(60, msg)
            raise QBittorrentError(msg)
        self._clear_backoff()

    def _request(self, method: str, path: str, **kwargs):
        with self._client() as c:
            self._login(c)
            r = c.request(method, self.base + path, **kwargs)
            if r.status_code >= 400:
                body = (r.text or "")[:300]
                raise QBittorrentError(f"qBittorrent HTTP {r.status_code}: {body}")
            return r

    def _ensure_labels(self):
        """qBittorrent 不同版本对不存在的分类/标签处理不完全一致，先显式创建。"""
        with self._client() as c:
            self._login(c)
            if self.tag:
                r = c.post(self.base + "/api/v2/torrents/createTags", data={"tags": self.tag})
                if r.status_code not in (200, 409):
                    raise QBittorrentError(f"创建 qBittorrent 标签失败 HTTP {r.status_code}: {(r.text or '')[:200]}")
            if self.category:
                data = {"category": self.category, "savePath": self.download_dir or ""}
                r = c.post(self.base + "/api/v2/torrents/createCategory", data=data)
                if r.status_code not in (200, 409):
                    raise QBittorrentError(f"创建 qBittorrent 分类失败 HTTP {r.status_code}: {(r.text or '')[:200]}")

    def test(self) -> dict:
        with self._client() as c:
            self._login(c)
            vr = c.get(self.base + "/api/v2/app/version")
            tr = c.get(self.base + "/api/v2/transfer/info")
            if vr.status_code >= 400:
                raise QBittorrentError(f"qBittorrent 版本查询 HTTP {vr.status_code}")
            info = tr.json() if tr.status_code < 400 else {}
            return {
                "ok": True,
                "version": (vr.text or "").strip(),
                "dl_info_speed": int(info.get("dl_info_speed") or 0),
                "up_info_speed": int(info.get("up_info_speed") or 0),
                "connection_status": info.get("connection_status") or "",
                "url": self.base,
            }

    def add_torrent(self, torrent_bytes: bytes, filename: str = "mteam.torrent") -> dict:
        if not torrent_bytes or len(torrent_bytes) < 50:
            raise QBittorrentError("种子内容为空或无效")
        self._ensure_labels()
        data = {
            "paused": "false",
            "skip_checking": "false",
            "root_folder": "true",
        }
        if self.download_dir:
            data["savepath"] = self.download_dir
        if self.category:
            data["category"] = self.category
        if self.tag:
            data["tags"] = self.tag
        r = self._request(
            "POST",
            "/api/v2/torrents/add",
            data=data,
            files={"torrents": (filename, torrent_bytes, "application/x-bittorrent")},
        )
        text = (r.text or "").strip().lower()
        if text not in ("", "ok.", "ok"):
            raise QBittorrentError(f"qBittorrent 添加种子失败: {r.text[:200]}")
        return {"ok": True, "filename": filename}

    def torrents(self, tagged_only: bool = True) -> list:
        params = {}
        if tagged_only and self.tag:
            params["tag"] = self.tag
        r = self._request("GET", "/api/v2/torrents/info", params=params)
        items = r.json() or []
        return [self._normalize_torrent(x) for x in items]

    def _normalize_torrent(self, t: dict) -> dict:
        size = int(t.get("size") or t.get("total_size") or 0)
        downloaded = int(t.get("downloaded") or 0)
        uploaded = int(t.get("uploaded") or 0)
        progress = float(t.get("progress") or 0)
        return {
            "hash": t.get("hash") or "",
            "name": t.get("name") or "-",
            "state": t.get("state") or "",
            "progress": progress,
            "progress_pct": round(progress * 100, 1),
            "size": size,
            "downloaded": downloaded,
            "uploaded": uploaded,
            "ratio": float(t.get("ratio") or 0),
            "dlspeed": int(t.get("dlspeed") or 0),
            "upspeed": int(t.get("upspeed") or 0),
            "num_seeds": int(t.get("num_seeds") or 0),
            "num_leechs": int(t.get("num_leechs") or 0),
            "added_on": int(t.get("added_on") or 0),
            "completion_on": int(t.get("completion_on") or 0),
            "last_activity": int(t.get("last_activity") or 0),
            "save_path": t.get("save_path") or "",
            "tags": t.get("tags") or "",
            "category": t.get("category") or "",
        }

    def pause(self, hashes: Iterable[str]) -> dict:
        hs = [str(x) for x in hashes if x]
        if not hs:
            return {"ok": True, "count": 0}
        self._request("POST", "/api/v2/torrents/pause", data={"hashes": "|".join(hs)})
        return {"ok": True, "count": len(hs)}

    def resume(self, hashes: Iterable[str]) -> dict:
        hs = [str(x) for x in hashes if x]
        if not hs:
            return {"ok": True, "count": 0}
        self._request("POST", "/api/v2/torrents/resume", data={"hashes": "|".join(hs)})
        return {"ok": True, "count": len(hs)}

    def delete(self, hashes: Iterable[str], delete_files: bool = True) -> dict:
        hs = [str(x) for x in hashes if x]
        if not hs:
            return {"ok": True, "count": 0}
        self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={"hashes": "|".join(hs), "deleteFiles": "true" if delete_files else "false"},
        )
        return {"ok": True, "count": len(hs), "delete_files": bool(delete_files)}

    @staticmethod
    def summary(items: list) -> dict:
        total_size = sum(int(x.get("size") or 0) for x in items)
        downloaded = sum(int(x.get("downloaded") or 0) for x in items)
        uploaded = sum(int(x.get("uploaded") or 0) for x in items)
        dl_speed = sum(int(x.get("dlspeed") or 0) for x in items)
        up_speed = sum(int(x.get("upspeed") or 0) for x in items)
        active_downloads = len([
            x for x in items
            if float(x.get("progress") or 0) < 0.9999 and x.get("state") not in ("pausedDL", "stoppedDL")
        ])
        seeding = len([x for x in items if float(x.get("progress") or 0) >= 0.9999])
        return {
            "count": len(items),
            "active_downloads": active_downloads,
            "seeding": seeding,
            "total_size": total_size,
            "downloaded": downloaded,
            "uploaded": uploaded,
            "dl_speed": dl_speed,
            "up_speed": up_speed,
        }
