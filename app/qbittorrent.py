from typing import Iterable, Optional

import httpx


class QBittorrentError(RuntimeError):
    pass


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

    def _login(self, c: httpx.Client):
        r = c.post(
            self.base + "/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
            headers={"Referer": self.base + "/"},
        )
        if r.status_code >= 400:
            raise QBittorrentError(f"qBittorrent 登录 HTTP {r.status_code}")
        text = (r.text or "").strip().lower()
        if text not in ("ok.", "ok"):
            raise QBittorrentError("qBittorrent 登录失败，请检查 WebUI 用户名/密码")

    def _request(self, method: str, path: str, **kwargs):
        with self._client() as c:
            self._login(c)
            r = c.request(method, self.base + path, **kwargs)
            if r.status_code >= 400:
                body = (r.text or "")[:300]
                raise QBittorrentError(f"qBittorrent HTTP {r.status_code}: {body}")
            return r

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
