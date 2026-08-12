# M-Team Box 模式

这是基于原「种控台」新增的 VPS / Seedbox 抢流模式，目标是让小硬盘、有限月流量的盒子尽量只接值得抢的新种。

## 设计

```text
M-Team RSS
    ↓
解析 torrent id
    ↓
M-Team API /torrent/detail
    ↓
体积 + 种龄 + Seeder + Leecher + Demand 评分
    ↓
磁盘 / 活跃下载 / vnStat 流量预算检查
    ↓
qBittorrent Web API
    ↓
完成后按 ratio / idle 自动淘汰
```

原项目的 M-Team API、Transmission、爱好监控、魔力监控等功能不删除。盒子模式使用独立的 `data/box_config.json` 和 `data/box_state.json`。

## 推荐的 VPS 参数

当前默认值就是按约 1C / 1G RAM / 24G 根盘的小 VPS 设计：

- 单种：0.5–6 GB
- 最大种龄：10 分钟
- 同时下载：1
- 数据区上限：16 GB
- 磁盘安全预留：4 GB
- RSS：60 秒轮询
- 流量软预算：1400 GB
- 流量硬停止：1500 GB
- ratio 2.85 自动淘汰
- 完成至少 20 分钟、之后连续 60 分钟无上传则淘汰

这些都可以在 `/box` 页面修改。

## 前置条件

1. 原种控台已经配置 M-Team API Access Token。
2. qBittorrent-nox 正常运行，并开启 WebUI。
3. 推荐把 qBittorrent WebUI 只监听 `127.0.0.1`。
4. 安装并启动 vnStat：

```bash
apt install -y vnstat
systemctl enable --now vnstat
```

5. 创建下载目录，并确保 qBittorrent 运行用户可写：

```bash
mkdir -p /srv/torrents/downloads /srv/torrents/incomplete
chown -R qbittorrent:qbittorrent /srv/torrents
```

## 原生部署

Debian 12 推荐直接用 venv + systemd，不必为了这个功能单独上 Docker。

```bash
cd /opt
git clone https://github.com/YunnXii/pt.git mt-pt
cd mt-pt
git switch feat/mteam-box-qb-rss

apt install -y python3-venv
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

先前台测试：

```bash
DATA_DIR=/opt/mt-pt/data \
DOWNLOAD_DIR=/opt/mt-pt/downloads \
.venv/bin/uvicorn app.box_main:app --host 127.0.0.1 --port 8081
```

本机开 SSH 隧道：

```powershell
ssh -L 8081:127.0.0.1:8081 vps
```

浏览器先打开 `http://127.0.0.1:8081/` 登录种控台，再打开：

```text
http://127.0.0.1:8081/box
```

## systemd

仓库提供 `systemd/mt-pt-box.service.example`。按实际目录修改后：

```bash
cp systemd/mt-pt-box.service.example /etc/systemd/system/mt-pt-box.service
systemctl daemon-reload
systemctl enable --now mt-pt-box
systemctl status mt-pt-box --no-pager
```

## 第一次配置

在 `/box` 中填写：

- M-Team 的个人 RSS 地址；
- qBittorrent WebUI URL，VPS 本机通常是 `http://127.0.0.1:8080`；
- qBittorrent 用户名和密码；
- 下载目录 `/srv/torrents/downloads`；
- vnStat 网卡（通常 `eth0`）；
- VPS 流量账期重置日。

保存后先点「测试 qBittorrent」，再点「立即扫一次 RSS」。确认最近判断中能看到 RSS torrent id、评分和拒绝/观察原因后再启动后台模式。

## 评分逻辑

盒子模式和原项目「魔力监控」不是一套逻辑。

这里优先：

- 越新越好，90 秒内权重最高；
- Leecher 多；
- Seeder 少；
- `Leecher / (Seeder + 1)` 高；
- 1–4 GB 小种额外加分。

以下直接永久跳过：

- 超出体积范围；
- 已超过最大抢流种龄；
- Seeder 已高于上限。

Leecher 暂时太少、需求比暂时不足或评分暂时不足时，不会立刻加入已读列表，而会在抢流时间窗内继续观察。

## 流量钱包

流量统计使用 `vnstat --json` 的累计流量做基线差值，而不是直接使用自然月统计，所以可以配置运营商/VPS 商家的账期重置日。

首次运行会把当时 vnStat 的累计值记为基线；之后只统计盒子模式启用后的新增 VPS 总流量（RX + TX）。

- 到软预算：停止接新种；
- 到硬停止线：暂停所有带 `mteam-box` 标签的任务；
- 「重置流量基线」可以手动从当前时刻重新计数。

## qBittorrent 标签

盒子自动加入的任务默认：

```text
Tag:      mteam-box
Category: mteam-box
```

自动清理、硬停和 Web 页面只管理这个标签下的任务，不会碰你手工添加的其他 torrent。
