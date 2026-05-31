# 🎙️ 语音日历助手

> 用说话的方式管理你的日程——创建、修改、删除，一句话搞定。

---

## 简介

语音日历助手是一个基于 [Pipecat](https://github.com/pipecat-ai/pipecat) 构建的实时语音 AI 应用。你只需对着麦克风说出你的日程安排，助手会自动识别、理解，并同步到你的日历中。

📺 **视频演示**：[点击查看 B 站演示视频](#)（【语音日历工具-哔哩哔哩】 https://b23.tv/omxs2I9）

---

## 功能特性

- 🎤 **语音交互**：基于 Deepgram 的实时中文语音识别，支持自然口语表达
- 🧠 **智能理解**：由 GLM-4-Flash 大模型驱动，理解模糊时间表达（如"后天下午三点"）
- 📅 **日程管理**：支持语音创建、修改、删除日程，含提醒时间设置
- 🔄 **多端同步**：
  - **QQ 邮箱日历**：通过 CalDAV 协议实时同步，自动添加/更新/删除日程并支持提醒
  - **企业微信 / Outlook**：PC 端双击 `.ics` 文件一键导入
  - **手机系统日历**：保存 `.ics` 文件后手动导入
  - **邮件通知**：同步发送邮件到 QQ 邮箱，随时查看日程变更记录

---

## 技术栈

| 模块 | 技术 |
|------|------|
| 语音框架 | [Pipecat](https://github.com/pipecat-ai/pipecat) |
| 语音识别 | [Deepgram](https://deepgram.com/) Nova-2（中文） |
| 大语言模型 | [智谱 GLM-4-Flash](https://open.bigmodel.cn/) |
| 日历同步 | CalDAV（`caldav` 库）|
| 日历格式 | iCalendar（`.ics`） |
| 邮件发送 | SMTP over SSL（QQ 邮箱）|
| 传输协议 | SmallWebRTC / Daily |

---

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/你的用户名/你的仓库名.git
cd 你的仓库名
```

### 2. 安装依赖

```bash
uv sync
```

### 3. 配置环境变量

复制 `.env.example` 并填入你的配置：

```bash
cp .env.example .env
```

`.env` 文件内容说明：

```env
DEEPGRAM_API_KEY=        # Deepgram 语音识别 API Key
GLM_API_KEY=             # 智谱 AI API Key
GLM_BASE_URL=            # 默认：https://open.bigmodel.ai/api/paas/v4/
GLM_MODEL=               # 默认：glm-4-flash
QQ_EMAIL=                # 你的 QQ 邮箱地址
QQ_EMAIL_AUTH_CODE=      # QQ 邮箱授权码（非登录密码）
```

> QQ 邮箱授权码获取方式：邮箱设置 → 账户 → POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV 服务 → 生成授权码

### 4. 启动

```bash
uv run bot.py
```

---

## 使用示例

| 你说的话 | 助手行为 |
|----------|----------|
| "明天下午三点开会，提前半小时提醒我" | 创建日程，设置 30 分钟提醒 |
| "把开会改成四点" | 更新已有日程时间 |
| "取消明天的会议" | 删除对应日程 |

---

## 项目结构

```
.
├── bot.py                  # 主程序：语音管道 + 工具调用
├── calendar_storage.py     # 本地日程存储（JSON）与模糊查询
├── calendar_events/        # 生成的 .ics 文件目录
├── calendar_db.json        # 本地日程数据库
└── .env                    # 环境变量配置（不上传）
```

---

## 注意事项

- 本项目使用 QQ 邮箱 CalDAV 服务同步日历，服务器地址为 `https://dav.qq.com`
- `.env` 文件包含敏感信息，已加入 `.gitignore`，请勿上传
- 语音识别目前以中文为主，简单英文词汇可正常识别

---

## 已知限制与后续研究方向

### QQ 邮箱日历同步限制

经过测试，QQ 邮箱对 iCalendar 协议的支持存在以下限制：

- `METHOD:PUBLISH`：**支持**，可通过邮件附件一键导入新日程 ✅
- `METHOD:REQUEST`：**不支持**，发送后日历无反应，无法覆盖更新已有日程 ❌
- `METHOD:CANCEL`：**不支持**，发送后日历无反应，无法自动删除日程 ❌

因此目前**修改和删除**日程时，用户会收到邮件通知，但需要手动在日历中操作（删除旧日程、点击附件导入新日程）。

### CalDAV 方向（未成功）

尝试通过 QQ 邮箱的 CalDAV 服务（`https://dav.qq.com`）直接读写日历，理论上可实现真正的增删改同步。使用 Python `caldav` 库连接后，终端无报错，但日历实际未发生变化，原因未明（可能是 QQ 邮箱 CalDAV 接口存在兼容性问题或权限限制）。

**建议后续研究方向：**

- 深入排查 QQ 邮箱 CalDAV 接口的认证方式与请求格式
- 改用 **Google Calendar API** 或 **苹果 iCloud CalDAV**，两者对增删改的支持更完善
- 或接入**企业微信日历 API**，更适合国内用户场景

---

## License

MIT
