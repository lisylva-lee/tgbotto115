# 115 ShareBot

一个基于 Telegram 的 115 网盘转存与云下载机器人。

## ✨ 功能

- **115 分享链接转存** - 自动解析并转存
- **磁力/电驴离线下载** - 支持 magnet 和 ed2k
- **分离目录设置** - 分享和离线可设置**不同**默认目录
- **目录 UI 交互** - 目录管理与默认目录全部通过按钮/录入交互完成：
  - 录入 **CID** → 自动查询网盘上对应的文件夹名称
  - 录入 **网盘路径**（如 `/电影/动作`）→ 自动解析，不存在则自动创建
  - 目录选择、删除、设为默认均按钮化
- **转发消息支持** - 直接转发包含链接的消息
- **批量消息缓冲** - 快速转发多条消息时自动合并处理（3秒缓冲）
- **用户自定义目录** - 超时自动使用用户/系统默认目录
- **状态卡片** - 转存进度实时更新同一张进度卡片（不刷屏），结果结构化展示，失败项自动列出

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 创建配置（必填）
Copy-Item config.example.yaml config.yaml
#    编辑 config.yaml：填写 telegram.bot_token 与 p115.cookie

# 3.（可选）旧版数据迁移到 SQLite（首次升级时执行）
python migrate_legacy.py

# 4. 启动
python bot.py
```

## 📁 配置（config.yaml）

所有配置集中在一个文件，**运行前必须配置**：

```yaml
telegram:
  bot_token: "123456:AAH..."   # 必填：Telegram Bot Token
p115:
  cookie: "UID=...; ..."        # 必填：115 网盘 Cookie
runtime:
  cid_selection_timeout: 10     # 可选：目录选择超时（秒）
  message_buffer_timeout: 3.0   # 可选：批量消息缓冲（秒）
  # ... 更多运行时参数见 config.example.yaml
```

## 📱 Bot 命令

| 命令 | 说明 |
|------|------|
| `/start` | 查看帮助 |
| `/set_share_cid` | 设置分享默认目录（按钮选择 / 录 CID / 录路径） |
| `/set_offline_cid` | 设置离线默认目录（按钮选择 / 录 CID / 录路径） |
| `/add_cid` | 添加新目录（录 CID 自动显示文件夹名 / 录路径自动创建） |
| `/del_cid` | 删除目录（按钮） |
| `/current_cid` | 查看当前设置 |
| `/list_cid` | 列出可用目录 |
| `/reset_cid` | 重置为系统默认目录 |

## 🗄️ 数据存储（share.db）

所有业务数据统一存本地 SQLite（`data/share.db`），不再使用散落的 txt/json：

| 表 | 内容 | 旧文件 |
|----|------|--------|
| `dirs` | 目录映射（名称 → CID） | cid_mapping.txt |
| `default_cids` | 系统默认目录（share/offline） | cid_default.txt |
| `user_cid` | 每用户自定义目录 | user_cid.json |
| `links` | 接收过的分享链接 | links.txt |
| `transfer_log` | 转存/离线记录 | transfer_log.json |

旧文件迁移：运行 `python migrate_legacy.py`（幂等，迁移成功后会重命名旧文件为 `.migrated` 备份）。

## 🐳 Docker 部署

镜像发布到 GitHub Container Registry：`ghcr.io/lisylva-lee/tgbotto115`（GitHub Actions 自动构建）。

```bash
# 1. 准备配置（绝不提交 config.yaml 到 git）
cp config.example.yaml config.yaml   # 然后填写真实 token / cookie

# 2. 用 compose 启动（config.yaml 挂载进容器，数据持久化到 ./data）
docker compose -f docker-compose.example.yaml up -d
```

> ⚠️ **安全说明**：`config.yaml`（含 Telegram token + 115 cookie）已在 `.gitignore` 排除，不会进入仓库或镜像。部署时通过 volume 挂载到容器 `/app/config.yaml`。

## 📝 License

MIT