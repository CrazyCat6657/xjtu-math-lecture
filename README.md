# 学术讲座自动推送

自动监控学院官网的学术动态页面，发现新讲座后第一时间推送到邮箱 / 微信，包含标题、报告人、时间、地点和详情链接。

基于 GitHub Actions 云端运行，**无需服务器、电脑关机也能收到**。监控目标通过 `config.json` 配置，不限学校，任何学院的通知列表页都可以接入。

## 功能特点

- 🔍 **自动监控**：定时抓取学院学术动态页面，自动发现新讲座
- 📋 **详情提取**：自动解析报告人、时间、地点等结构化信息
- 📧 **多渠道推送**：支持 QQ 邮箱（SMTP）和微信（Server 酱）
- 🔁 **自动去重**：基于历史记录，同一条讲座不会重复推送
- 🌐 **网站可配置**：在 `config.json` 中增删监控站点，适配不同学校
- ☁️ **云端运行**：GitHub Actions 托管，免费、免运维、关机照推

## 工作原理

```
config.json (监控站点配置)
       │
       ▼
lecture_spider.py  ──►  抓取列表页 ──►  逐条抓取详情页
       │                                       │
       │  对比 lecture_history.json 去重        │
       ▼                                       ▼
  新讲座 ──────────────────────────────►  邮件 / 微信推送
       │
       ▼
GitHub Actions 自动提交更新后的历史记录
```

GitHub Actions 按 cron 计划触发（每天若干次）。由于公共仓库的定时任务为**尽力而为调度**，实际触发时间会有一定漂移，并非精确整点，但足以保证新讲座当天送达。

## 快速开始

### 1. Fork 本仓库

点击右上角 **Fork**，复制一份到你自己的 GitHub 账号下。

### 2. 配置推送渠道（Secrets）

在你的仓库中进入 **Settings → Secrets and variables → Actions → New repository secret**，按需添加：

| Secret 名 | 说明 | 是否必填 |
|---|---|---|
| `EMAIL_SENDER` | 发件邮箱（如 `123456@qq.com`） | 邮箱渠道必填 |
| `EMAIL_AUTH_CODE` | 邮箱 SMTP 授权码（非登录密码） | 邮箱渠道必填 |
| `EMAIL_RECEIVER` | 收件邮箱（可与发件邮箱相同） | 邮箱渠道必填 |
| `SEND_KEY` | Server 酱 SendKey（微信推送） | 微信渠道必填 |

**QQ 邮箱授权码获取**：登录 QQ 邮箱网页版 → 设置 → 账户 → 开启「POP3/IMAP/SMTP 服务」→ 按提示发短信获取 16 位授权码。

**微信推送**：打开 [sct.ftqq.com](https://sct.ftqq.com) 微信扫码登录，复制 SendKey。

两个渠道至少配置一个，否则脚本不会推送。

### 3. 配置监控站点

编辑仓库根目录的 `config.json`，默认已配置西安交通大学数学与统计学院。其他学校的同学可以修改或新增站点，详见下方 [配置监控站点](#配置监控站点)。

### 4. 启用 Actions 并测试

1. 进入仓库 **Actions** 页面，如提示启用则点击 **I understand my workflows, go ahead and enable them**
2. 选择左侧 **学术讲座推送** → 点击 **Run workflow** 手动触发一次
3. 运行成功后即可在邮箱 / 微信收到通知

之后 Actions 会按计划自动运行，无需人工干预。

## 配置监控站点

所有监控目标都在 `config.json` 的 `sites` 数组中配置，每个站点一个对象：

```json
{
  "name": "西安交通大学数学与统计学院",
  "list_url": "https://math.xjtu.edu.cn/index/xsdt1.htm",
  "base_url": "https://math.xjtu.edu.cn",
  "enabled": true,
  "list": {
    "item_selector": "li",
    "date_selector": "span",
    "date_regex": "(\\d{4}[-/]\\d{1,2}[-/]\\d{1,2})",
    "link_keywords": ["info", "xsdt"],
    "title_keywords": [],
    "title_min_length": 5,
    "max_items": 15
  },
  "detail": {
    "speaker_labels": ["报告人", "主讲人", "嘉宾"],
    "time_labels": ["时间", "讲座时间", "报告时间"],
    "location_labels": ["地点", "举办地点", "报告地点"]
  },
  "challenge": {
    "enabled": true,
    "verify_url": "https://math.xjtu.edu.cn/dynamic_challenge"
  }
}
```

### 字段说明

| 字段 | 说明 |
|---|---|
| `name` | 站点名称，会显示在推送标题中，同时作为历史记录的分组键 |
| `list_url` | 讲座列表页地址 |
| `base_url` | 站点根地址，用于把相对链接补全为绝对链接 |
| `enabled` | 是否启用该站点 |
| `list.item_selector` | 列表条目的 CSS 选择器，通常是 `"li"` |
| `list.date_selector` | 条目内日期元素的 CSS 选择器，通常是 `"span"`；留空则只用正则提取日期 |
| `list.date_regex` | 从条目文本中提取日期的正则 |
| `list.link_keywords` | 链接中必须包含的关键词（用于过滤导航链接等），留空数组表示不过滤 |
| `list.title_keywords` | 标题中必须包含的关键词（如 `["讲座", "报告"]`），留空数组表示不过滤 |
| `list.title_min_length` | 标题最短长度，短于此长度的条目会被忽略 |
| `list.max_items` | 每次最多抓取的条目数 |
| `detail.speaker_labels` | 详情页中「报告人」字段的可能写法 |
| `detail.time_labels` | 详情页中「时间」字段的可能写法 |
| `detail.location_labels` | 详情页中「地点」字段的可能写法 |
| `challenge.enabled` | 站点是否有 JS 反爬验证（仅特定站点需要） |
| `challenge.verify_url` | 验证接口地址 |

### 接入其他学校网站

大多数学校学院的通知页是标准的 `<ul><li>` 列表结构，只需改三个字段即可接入：

```json
{
  "name": "XX大学XX学院",
  "list_url": "https://xxxy.xxx.edu.cn/tzgg.htm",
  "base_url": "https://xxxy.xxx.edu.cn",
  "enabled": true,
  "list": {
    "item_selector": "li",
    "date_selector": "span",
    "link_keywords": [],
    "title_keywords": ["讲座", "报告", "学术"],
    "title_min_length": 5,
    "max_items": 15
  },
  "detail": {
    "speaker_labels": ["报告人", "主讲人", "嘉宾"],
    "time_labels": ["时间", "讲座时间", "报告时间"],
    "location_labels": ["地点", "举办地点", "报告地点"]
  },
  "challenge": { "enabled": false }
}
```

如果列表结构不同，可以用浏览器开发者工具查看列表条目的 HTML 标签，调整 `item_selector` 和 `date_selector`。

## 文件结构

```
.
├── lecture_spider.py        # 主程序：抓取、解析、去重、推送
├── config.json              # 监控站点配置（可自行增删）
├── requirements.txt         # Python 依赖
├── lecture_history.json     # 已推送历史（Actions 自动维护）
├── README.md
└── .github/workflows/run.yml  # GitHub Actions 工作流
```

## 常见问题

**收不到邮件？** 先检查垃圾邮件箱，确认 SMTP 授权码正确（不是邮箱登录密码）。

**微信收不到？** 确认已关注「方糖」服务号，且 SendKey 正确。

**详情里显示「详见正文」？** 部分通知的字段写法不在默认标签列表中，可在 `config.json` 的 `detail` 里补充对应的标签写法。

**想调整检查频率？** 编辑 `.github/workflows/run.yml` 中的 cron 表达式。注意公共仓库的定时任务为尽力而为调度，实际触发时间会有漂移。

**Actions 没有自动运行？** 公开仓库的 Actions 定时任务在仓库长期无活动时可能被 GitHub 暂停，进入 Actions 页面手动触发一次即可恢复。

## 技术栈

- Python 3 · requests · BeautifulSoup4
- GitHub Actions（cron 调度 + 自动提交历史）
- SMTP 邮件 · Server 酱微信推送
