# 西交数院讲座自动推送

自动监控 [西安交通大学数学与统计学院 · 学术动态](https://math.xjtu.edu.cn/index/xsdt1.htm)，发现新讲座时推送到 **微信（Server 酱）** 和 **邮箱（SMTP）**。基于 GitHub Actions 定时运行，无需自己的服务器。

## 功能

- 定时抓取学院学术动态列表，自动去重，只推送新讲座
- 自动解析详情页的 **报告人 / 时间 / 地点**
- 同时推送到微信和邮箱，两个渠道独立工作
- **故障告警**：抓取异常或连续 0 条时主动发通知，恢复时再通知一次，避免"悄悄停了都不知道"
- 网络请求自动重试（指数退避），历史记录自动裁剪防止无限增长
- 解析逻辑有单元测试覆盖，每次提交自动跑 CI

## 部署步骤

1. **Fork 本仓库**
2. 进入 Fork 后的仓库 `Settings → Secrets and variables → Actions`，添加以下 Repository secrets：

   | Secret 名 | 说明 |
   |---|---|
   | `SEND_KEY` | Server 酱 SendKey（微信推送，[sct.ftqq.com](https://sct.ftqq.com/) 免费申请） |
   | `EMAIL_SENDER` | 发件邮箱，如 `123456@qq.com` |
   | `EMAIL_AUTH_CODE` | 邮箱 SMTP 授权码（**不是**登录密码，QQ 邮箱在设置里开启 SMTP 后获取） |
   | `EMAIL_RECEIVER` | 收件邮箱，可与发件邮箱相同 |

   > 两个渠道至少配置一个。只用微信就只填 `SEND_KEY`，只用邮箱就填三个 `EMAIL_*`。

3. 进入 `Actions` 页面，启用 GitHub Actions（Fork 后默认可能是关闭的）
4. 工作流每天定时运行若干次，也可以在 Actions 页面手动点 `Run workflow` 立即测试一次

> GitHub Actions 公共仓库的定时任务是**尽力而为调度**，高峰期可能延迟几十分钟，不保证整点执行，但每天会触发多次，足以保证新讲座及时送达。

## 项目结构

```
├── lecture_spider.py        # 主程序：抓取、解析、推送、故障告警
├── config.json              # 监控目标与解析规则配置
├── lecture_history.json     # 已推送记录（自动维护）
├── .health.json             # 运行健康状态（自动维护，用于故障告警去重）
├── requirements.txt         # 生产依赖
├── tests/                   # 单元测试
└── .github/workflows/
    ├── run.yml              # 定时抓取与推送
    └── test.yml             # 提交时自动跑测试
```

## 本地开发

```bash
pip install -r requirements.txt pytest
python -m pytest tests/ -v
```

## 使用声明

- 本项目**仅针对西安交通大学数学与统计学院**的公开学术动态页，不是通用爬虫框架。
- 仅读取公开的讲座信息，不获取任何非公开或个人数据，不写入目标网站。
- 请求频率低（每次运行间隔数小时，请求间隔 1 秒），不对目标网站造成负担。
- 代码仅供个人学习交流使用。如目标网站方有异议，请联系作者，将及时停止使用。

## License

[MIT](LICENSE)
