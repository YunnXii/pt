import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlparse

import httpx


class BoxRssError(RuntimeError):
    pass


def _localname(tag: str) -> str:
    return str(tag or "").split("}")[-1].lower()


def _node_text(node, names) -> str:
    names = {x.lower() for x in names}
    for child in list(node):
        if _localname(child.tag) in names:
            text = (child.text or "").strip()
            if text:
                return text
            href = (child.attrib.get("href") or child.attrib.get("url") or "").strip()
            if href:
                return href
    return ""


def _extract_torrent_id(*values) -> str:
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        if text.isdigit():
            return text
        try:
            q = parse_qs(urlparse(text).query)
            for key in ("id", "tid", "torrent_id", "torrentId"):
                vals = q.get(key)
                if vals and str(vals[0]).isdigit():
                    return str(vals[0])
        except Exception:
            pass
        for pat in (
            r"/(?:detail|torrent|torrents)/(\d+)(?:\D|$)",
            r"[?&](?:id|tid|torrent_id|torrentId)=(\d+)",
            r"\b(?:torrent|tid|id)[:=_-](\d+)\b",
        ):
            m = re.search(pat, text, re.I)
            if m:
                return m.group(1)
    return ""


def _safe_kind(body: bytes, content_type: str) -> str:
    probe = body[:256].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    ctype = (content_type or "").lower()
    if probe.startswith((b"<!doctype html", b"<html", b"<head", b"<body")) or "text/html" in ctype:
        return "HTML 页面"
    if probe.startswith((b"{", b"[")) or "application/json" in ctype:
        return "JSON 响应"
    if probe.startswith(b"<"):
        return "XML/标记文本"
    return "纯文本/未知响应"


def _normalize_xml_bytes(body: bytes) -> bytes:
    if not body:
        return body
    # UTF-8 BOM 与普通空白可以安全去掉。NUL 不盲目删除，因为它可能意味着 UTF-16；
    # ElementTree 对带 BOM 的 UTF-16 本来就能正确识别。
    if body.startswith(b"\xef\xbb\xbf"):
        body = body[3:]
    return body.lstrip(b"\t\r\n ")


def fetch_rss(url: str) -> dict:
    if not (url or "").strip():
        raise BoxRssError("请先配置 M-Team RSS 地址")

    try:
        r = httpx.get(
            url.strip(),
            headers={
                "User-Agent": "MTeamBox/0.2 (+RSS)",
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.8",
            },
            timeout=30.0,
            follow_redirects=True,
        )
    except Exception as e:
        raise BoxRssError(f"RSS 请求失败：{type(e).__name__}: {e}") from e

    content_type = r.headers.get("content-type") or ""
    if r.status_code >= 400:
        raise BoxRssError(
            f"RSS HTTP {r.status_code}；返回类型 {_safe_kind(r.content, content_type)}；Content-Type={content_type or '-'}"
        )

    body = _normalize_xml_bytes(r.content or b"")
    if not body:
        raise BoxRssError(f"RSS 返回空内容；Content-Type={content_type or '-'}")

    kind = _safe_kind(body, content_type)
    probe = body[:256].lstrip().lower()
    if kind in ("HTML 页面", "JSON 响应") or not probe.startswith(b"<"):
        raise BoxRssError(
            f"RSS 返回的不是可解析 XML，而是{kind}；HTTP {r.status_code}；Content-Type={content_type or '-'}。"
            "请检查 RSS 私有地址是否仍有效，或站点是否返回了登录/风控/错误页面"
        )

    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        # 不回显响应正文，避免错误页里意外包含私有参数或账号信息。
        raise BoxRssError(
            f"RSS XML 解析失败：{e}；HTTP {r.status_code}；Content-Type={content_type or '-'}；响应判定为{kind}"
        ) from e

    feed_title = ""
    for node in root.iter():
        if _localname(node.tag) == "title" and (node.text or "").strip():
            feed_title = (node.text or "").strip()
            break

    entries = [x for x in root.iter() if _localname(x.tag) in ("item", "entry")]
    items = []
    for node in entries:
        title = _node_text(node, {"title"})
        link = _node_text(node, {"link"})
        guid = _node_text(node, {"guid", "id"})
        published = _node_text(node, {"pubdate", "published", "updated", "date"})
        enclosure = ""
        for child in list(node):
            if _localname(child.tag) == "enclosure":
                enclosure = (child.attrib.get("url") or child.attrib.get("href") or "").strip()
                break
        tid = _extract_torrent_id(guid, link, enclosure)
        items.append({
            "id": tid,
            "title": title,
            "link": link,
            "guid": guid,
            "enclosure": enclosure,
            "published": published,
        })

    return {
        "title": feed_title,
        "items": items,
        "http_status": r.status_code,
        "content_type": content_type,
    }
