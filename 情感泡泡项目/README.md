# 情缘 💭 — 把心事装进泡泡里

> 一个运行在 Windows 上的情感交流软件。心情以"泡泡"的形式飘在墙上，
> 有人看见、有人回复，你决定它何时炸掉、何时留下。
>
> **v2.0**：PyQt5 桌面客户端 + Flask 中央服务器 + 本地 SQLite 缓存，可离线使用。

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

需要 Python 3.10+（开发环境为 3.12.10）。

### 2. 启动中央服务器（一台电脑即可，也可放在公网服务器上）

双击 `启动服务器.bat`，或：

```bash
python server.py
```

默认监听 `0.0.0.0:5000`，健康检查 `http://127.0.0.1:5000/api/v2/health`。

### 3. 启动客户端

双击 `启动软件.bat`，或：

```bash
python src/main.py
```

输入昵称即可进入，**不需要注册、不需要密码**。

### 4. 多台电脑联机

在客户端机器上把服务器地址指向运行服务器的那台电脑：

```cmd
set QINGYUAN_SERVER_URL=http://<服务器IP>:5000
python src/main.py
```

也可以写在 `~/.Qingyuan/config.json` 的 `server_url` 字段里。

> 旧变量名 `EMOTION_BUBBLE_SERVER_URL` 仍然兼容（新名未设置时生效）。

---

## 目录结构

```
情缘项目/
├── server.py                  服务器入口（waitress，8 线程）
├── 启动软件.bat               客户端一键启动
├── 启动服务器.bat             服务器一键启动
├── requirements.txt           精确锁版依赖
├── src/
│   ├── main.py                客户端入口
│   ├── core/                  与界面无关的核心层（无 PyQt / 无 Flask）
│   │   ├── config.py          配置读写（client_id / 昵称 / 服务器地址 / 窗口位置）
│   │   ├── constants.py       情感、状态、上限等唯一来源
│   │   ├── models.py          Bubble / Reply / ChatMessage + 具名行转换
│   │   ├── migrations.py      建表与旧库无损迁移
│   │   ├── repo.py            本地仓储（只读查询为主）
│   │   ├── permissions.py     权限判定的唯一实现
│   │   ├── api.py             HTTP 出口（全项目唯一 import requests 的地方）
│   │   ├── outbox.py          离线队列
│   │   └── sync.py            同步引擎（拉取 / 入队 / 重放）
│   ├── server_app.py          Flask 应用工厂、限流、统一错误
│   ├── server_api.py          /api/v2/* 路由
│   ├── server_store.py        服务端仓储
│   ├── server_auth.py         设备身份与 token
│   └── ui/
│       ├── app_window.py      主窗口与路由
│       ├── theme.py           配色令牌与 QSS
│       ├── pages/             登录 / 泡泡墙 / 我的 / 发布 / 详情 / 对话
│       └── widgets/           圆形泡泡、流式布局、动画、抽屉、Toast
├── tests/                     unittest 测试（270 个）
├── DOCS/                      需求、审计、PRD、架构、视觉规格
└── tools/                     截图与探针脚本
```

---

## 核心概念

| 概念 | 说明 |
|------|------|
| 泡泡 | 一条情感帖，带 6 种情感标签之一 |
| 情感 | 开心 / 难过 / 孤独 / 焦虑 / 寻求建议 / 找人倾诉，各有专属配色 |
| 单次回复 | 每个人只能回复一次，不建立对话 |
| 短时对话 | 回复后与发布者建立一对一私密对话 |
| 已解决 | 发布者认为需求已解决，泡泡播放爆破动画后消失（记录保留，仅自己可见） |
| 保留 | 公开保留（他人仍可见）或私密保存（仅自己可见，且只存在本地） |

---

## 数据与隐私

- **身份**：首次启动生成设备 ID（`client_id`）存于 `~/.Qingyuan/config.json`，
  昵称只用于展示。两台机器用同一个昵称仍是两个不同用户。
- **私密保存**：内容只留本地，服务器上该泡泡改为"仅作者可见"，**不上传对话内容**。
- **私聊隔离**：只有对话双方能读到消息，第三方请求返回 `403`。
- **单次回复**：由服务端强制，第二次回复返回 `409`。
- **本地缓存**：所有内容在本地 SQLite 留底，服务器挂掉也能照常浏览。

数据位置：

| 内容 | 路径 |
|------|------|
| 客户端数据库 | `~/.Qingyuan/qingyuan.db` |
| 客户端配置 | `~/.Qingyuan/config.json` |
| 客户端日志 | `~/.Qingyuan/app.log`（5MB 轮转） |
| 服务端数据库 | `server_data/qingyuan_server.db` |
| 迁移前备份 | `~/.Qingyuan/backup/`、`server_data/backup/` |

---

## 测试

```bash
python -m unittest discover -s tests -v
```

270 个用例，覆盖：数据库读写与旧库迁移、权限矩阵、服务端 API（含 400/403/404/409/429）、
离线队列与冲突处理、流式布局与刷新 diff、静态卫生检查（无裸 except、无 `SELECT *`、
UI 层不 import requests）。

---

## 常见问题

**Q：服务器明明启动了，客户端却一直显示离线？**
公司/校园网常设置 `HTTP_PROXY` 环境变量，会把 `127.0.0.1` 的请求也劫持走。
客户端已设置 `trust_env = False` 规避；若仍异常，检查 `config.json` 里的 `server_url` 是否正确。

**Q：服务器关掉后客户端卡住？**
v2.0 已把所有网络请求移出 UI 线程，离线时界面照常可交互，发布/回复会进入待同步队列，
联网后自动补发。

**Q：升级后我的旧泡泡还在吗？**
在。首次启动会自动把 v1 数据库迁移到新结构，迁移前会备份到 `~/.Qingyuan/backup/`。

**Q：从「情感泡泡」升级到「情缘」，数据会丢吗？**
不会。旧数据目录 `~/.EmotionBubble` 会在首次启动时**原地改名**为 `~/.Qingyuan`，
库文件 `emotion_bubble.db` 同步改名为 `qingyuan.db`（改名前后都会先做 WAL 落盘，
不会丢已提交但未落盘的消息）。整个过程是重命名，不复制、不删除。

**Q：想让更多人用同一个服务器？**
把 `server.py` 部署到有公网 IP 的机器（或内网穿透），客户端设置
`QINGYUAN_SERVER_URL` 指向它即可。生产环境建议加 HTTPS 反向代理。

---

## 文档

| 文档 | 内容 |
|------|------|
| `DOCS/01_需求文档.md` | v1 原始需求 |
| `DOCS/v2/00_审计报告.md` | v1 缺陷审计（本次改进的依据） |
| `DOCS/v2/01_PRD_v2.md` | v2.0 需求与验收标准 |
| `DOCS/v2/01b_产品决策裁定.md` | 身份、私密、冲突等关键决策 |
| `DOCS/v2/02_视觉规格.md` | 圆形泡泡的视觉规格与实现陷阱 |
| `DOCS/v2/03_架构设计.md` | 分层、数据模型、API 契约、任务分解 |
| `DOCS/联机测试指南.md` | 多机联调步骤 |
