"""
西安交通大学数学与统计学院 学术讲座自动推送

- 监控学院学术动态页，发现新讲座后推送至邮箱 / 微信
- 自动解析报告人、时间、地点
- 设计为在 GitHub Actions 上运行，密钥通过 Secrets 注入
- 内置故障告警：抓取异常或连续 0 条时主动通知，避免"静默死亡"

说明：目标网站对非浏览器 UA 直接返回 403，因此使用浏览器 UA 访问。
本工具仅读取公开的学术讲座信息，低频访问（每次运行间隔数小时、
请求间隔 1 秒），不获取任何非公开数据，仅供个人学习使用。
"""

import json
import os
import re
import smtplib
import time
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ====================== 路径与全局配置 ======================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
HISTORY_FILE = os.path.join(BASE_DIR, "lecture_history.json")
HEALTH_FILE = os.path.join(BASE_DIR, ".health.json")

HISTORY_KEEP = 200          # 历史记录最多保留条数，防止文件无限增长
ALERT_COOLDOWN = 86400      # 故障告警冷却时间（秒），避免一天内重复轰炸

# 目标网站对非浏览器 UA 返回 403，需使用浏览器 UA 才能访问公开页面
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# 推送密钥全部从环境变量读取（GitHub Secrets 注入），不在代码中硬编码
SEND_KEY = os.environ.get("SEND_KEY", "")
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "")
EMAIL_AUTH_CODE = os.environ.get("EMAIL_AUTH_CODE", "")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.qq.com")

ENABLE_WECHAT = bool(SEND_KEY)
ENABLE_EMAIL = bool(EMAIL_SENDER and EMAIL_AUTH_CODE and EMAIL_RECEIVER)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ====================== 通用工具 ======================
def request_with_retry(session, method, url, max_retries=3, **kwargs):
    """带指数退避重试的 HTTP 请求。"""
    kwargs.setdefault("timeout", 15)
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = session.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                log(f"  请求失败({e})，{wait}s 后重试({attempt + 1}/{max_retries})")
                time.sleep(wait)
    raise last_exc


# ====================== 配置加载 ======================
def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ====================== 历史记录 ======================
def load_history():
    """历史记录为 ["标题|日期", ...] 的列表。"""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 兼容旧版按站点分组的 dict 格式
        if isinstance(data, dict):
            merged = []
            for v in data.values():
                if isinstance(v, list):
                    merged.extend(v)
            return merged
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(history):
    try:
        # 按日期倒序排列（日期在 "标题|日期" 的 | 之后，YYYY-MM-DD 可直接字符串排序）
        ordered = sorted(history, key=lambda x: x.rsplit("|", 1)[-1], reverse=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(ordered[:HISTORY_KEEP], f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"保存历史失败: {e}")


# ====================== 健康状态 / 故障告警 ======================
def load_health():
    if os.path.exists(HEALTH_FILE):
        try:
            with open(HEALTH_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"status": "ok", "consecutive_failures": 0, "last_alert_ts": 0}


def save_health(health):
    try:
        with open(HEALTH_FILE, "w", encoding="utf-8") as f:
            json.dump(health, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"保存健康状态失败: {e}")


def send_alert(subject, message):
    """发送故障 / 恢复告警（走与正常推送相同的渠道）。"""
    title = f"[讲座推送告警] {subject}"
    log(f"发送告警: {subject}")
    wc_ok = push_wechat(title, message) if ENABLE_WECHAT else False
    em_ok = send_email(title, message) if ENABLE_EMAIL else False
    log(f"告警结果 | 微信:{'OK' if wc_ok else '--'} | 邮箱:{'OK' if em_ok else '--'}")


# ====================== 反爬验证 ======================
def pass_challenge(session, config):
    """通过网站的 JS 验证，拿到 client_id cookie。

    页面返回一段 JS：内含 challengeId 与一道算术题（a op b），
    需算出答案并连同浏览器指纹与 hash 一起 POST 到验证接口。
    验证逻辑随网站更新可能变化，失败时由主流程的故障告警捕获。
    """
    verify_url = config.get("verify_url")
    if not verify_url:
        return
    try:
        log("  正在通过网站验证...")
        resp = request_with_retry(session, "GET", config["list_url"])
        resp.encoding = _detect_encoding(resp)

        challenge_id = _extract_challenge_id(resp.text)
        answer = _extract_answer(resp.text)
        if not challenge_id or answer is None:
            log("  未发现验证参数（可能已通过验证），继续")
            return

        ua = HEADERS["User-Agent"]
        browser_info = {
            "userAgent": ua,
            "language": "zh-CN",
            "platform": "Win32",
            "screen": {"width": 1920, "height": 1080, "colorDepth": 24},
            "timezoneOffset": -480,
            "hasTouchEvents": False,
        }
        hash_value = _simple_hash(challenge_id + str(answer) + ua[:10])

        verify = request_with_retry(
            session, "POST", verify_url,
            json={
                "challenge_id": challenge_id,
                "answer": answer,
                "browser_info": browser_info,
                "hash": hash_value,
            },
            headers={"Content-Type": "application/json"},
        )
        if verify.json().get("success"):
            client_id = verify.json()["client_id"]
            session.cookies.set("client_id", client_id, domain=_domain(config["base_url"]))
            log("  验证通过")
        else:
            log(f"  验证未通过: {verify.text[:120]}")
    except Exception as e:
        log(f"  验证异常: {e}")
        raise


def _detect_encoding(resp):
    """优先用响应声明的编码，否则用自动检测，兜底 utf-8。"""
    if resp.encoding and resp.encoding.lower() not in ("iso-8859-1",):
        return resp.encoding
    return resp.apparent_encoding or "utf-8"


def _extract_challenge_id(html):
    m = re.search(r"""var\s+challengeId\s*=\s*['"]([^'"]+)['"]""", html)
    return m.group(1) if m else None


def _extract_answer(html):
    """从验证页 JS 中解析算术题并计算答案。

    支持两种格式：
      新版: var a = 18; var b = 13; var operator = '*';
      旧版: var answer = 4;
    """
    m = re.search(r"var\s+answer\s*=\s*(-?\d+)\s*;", html)
    if m:
        return int(m.group(1))

    am = re.search(r"var\s+a\s*=\s*(-?\d+)\s*;", html)
    bm = re.search(r"var\s+b\s*=\s*(-?\d+)\s*;", html)
    om = re.search(r"""var\s+operator\s*=\s*['"]([+\-*])['"]""", html)
    if am and bm and om:
        a, b, op = int(am.group(1)), int(bm.group(1)), om.group(1)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        return a * b
    return None


def _simple_hash(s):
    """与页面 JS 中 simpleHash 等价的 32 位整型哈希。"""
    h = 0
    for ch in s:
        h = ((h << 5) - h) + ord(ch)
        h &= 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    return abs(h)


def _domain(url):
    return url.split("//", 1)[-1].split("/", 1)[0]


# ====================== 列表抓取 ======================
def fetch_list(session, config):
    """抓取列表页，返回讲座列表。失败时抛出异常以便上层告警。"""
    list_cfg = config.get("list", {})
    resp = request_with_retry(session, "GET", config["list_url"])
    resp.encoding = _detect_encoding(resp)
    soup = BeautifulSoup(resp.text, "html.parser")

    item_selector = list_cfg.get("item_selector", "li")
    date_selector = list_cfg.get("date_selector", "span")
    date_regex = list_cfg.get("date_regex", r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})")
    link_keywords = list_cfg.get("link_keywords", [])
    title_keywords = list_cfg.get("title_keywords", [])
    title_min_length = list_cfg.get("title_min_length", 5)
    max_items = config.get("max_items", list_cfg.get("max_items", 15))

    lectures = []
    for item in soup.select(item_selector):
        a = item.find("a")
        if not a:
            continue

        title = a.get_text(strip=True)
        link = a.get("href", "")
        if not title or len(title) < title_min_length:
            continue

        if title_keywords and not any(k in title for k in title_keywords):
            continue
        if link_keywords and not any(k in link for k in link_keywords):
            continue

        # 解析发布日期
        date = ""
        date_el = item.select_one(date_selector) if date_selector else None
        if date_el:
            date = date_el.get_text(strip=True)
        if not date:
            dm = re.search(date_regex, item.get_text())
            if dm:
                date = dm.group(1)

        link = urljoin(config["base_url"], link)
        if not link.startswith("http"):
            continue

        lectures.append({"title": title, "date": date or "未知", "link": link})

    # 去重
    seen = set()
    unique = []
    for lec in lectures:
        if lec["title"] not in seen:
            seen.add(lec["title"])
            unique.append(lec)

    log(f"  获取到 {len(unique)} 条讲座")
    return unique[:max_items]


# ====================== 详情抓取 ======================
def fetch_detail(session, config, url):
    """抓取详情页，提取报告人 / 时间 / 地点。"""
    detail_cfg = config.get("detail", {})
    try:
        resp = request_with_retry(session, "GET", url)
        resp.encoding = _detect_encoding(resp)
        content = BeautifulSoup(resp.text, "html.parser").get_text("\n", strip=True)

        result = {"speaker": "详见正文", "time": "详见正文", "location": "详见正文"}

        speaker = _extract_field(
            content, detail_cfg.get("speaker_labels", ["报告人", "主讲人", "嘉宾", "演讲者"]))
        if speaker:
            result["speaker"] = speaker

        time_info = _extract_field(
            content, detail_cfg.get("time_labels", ["时间", "讲座时间", "报告时间", "日期"]))
        if time_info:
            result["time"] = time_info

        location = _extract_field(
            content, detail_cfg.get("location_labels", ["地点", "举办地点", "报告地点", "地址", "会场"]))
        if location:
            result["location"] = location

        return result
    except Exception as e:
        log(f"  获取详情失败: {e}")
        return {"speaker": "获取失败", "time": "获取失败", "location": "获取失败"}


def _extract_field(content, labels):
    """根据一组标签（如 报告人/主讲人）从正文中提取对应值。

    兼容标签字间空白（如 "时 间"）、冒号后换行（表格布局）等情况。
    值截取到换行 / 句号 / 分号为止，保留逗号以支持多人或多地点。
    """
    for label in labels:
        flexible = r"\s*".join(re.escape(ch) for ch in label)
        # 标签与冒号之间不留空白，避免"时间"误匹配到"发布时间 :"；
        # 冒号后的 \s* 可跨行，兼容表格中标签与值分处两行的情况
        pattern = rf"{flexible}[:：]\s*([^\n。；;]+)"
        m = re.search(pattern, content)
        if m:
            return m.group(1).strip()[:80]
    return ""


# ====================== 推送 ======================
def push_wechat(title, content):
    if not ENABLE_WECHAT:
        return False
    try:
        r = requests.post(
            f"https://sctapi.ftqq.com/{SEND_KEY}.send",
            data={"title": title, "desp": content},
            timeout=10,
        )
        return r.json().get("code") == 0
    except Exception as e:
        log(f"  微信推送失败: {e}")
        return False


def send_email(title, content):
    if not ENABLE_EMAIL:
        return False
    try:
        msg = MIMEText(content, "plain", "utf-8")
        msg["From"] = formataddr((str(Header("讲座通知", "utf-8")), EMAIL_SENDER))
        msg["To"] = EMAIL_RECEIVER
        msg["Subject"] = Header(title, "utf-8")

        try:
            server = smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=8)
        except Exception:
            try:
                server = smtplib.SMTP(SMTP_HOST, 587, timeout=8)
                server.starttls()
            except Exception:
                return False

        server.login(EMAIL_SENDER, EMAIL_AUTH_CODE)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        log(f"  邮箱推送失败: {e}")
        return False


def push_lecture(site_name, lec, detail):
    title = f"【新讲座】{site_name} - {lec['title']}"
    wc_content = (
        f"## {lec['title']}\n"
        f"**发布：** {lec['date']}\n"
        f"**报告人：** {detail['speaker']}\n"
        f"**时间：** {detail['time']}\n"
        f"**地点：** {detail['location']}\n"
        f"[查看详情]({lec['link']})"
    )
    em_content = (
        f"【讲座通知】\n"
        f"标题：{lec['title']}\n"
        f"发布日期：{lec['date']}\n"
        f"报告人：{detail['speaker']}\n"
        f"时间：{detail['time']}\n"
        f"地点：{detail['location']}\n"
        f"链接：{lec['link']}"
    )
    wc_ok = push_wechat(title, wc_content)
    em_ok = send_email(title, em_content)
    log(f"  推送结果 | 微信:{'OK' if wc_ok else '--'} | 邮箱:{'OK' if em_ok else '--'}")


# ====================== 主流程 ======================
def main():
    log("=== 开始检查讲座 ===")

    if not ENABLE_WECHAT and not ENABLE_EMAIL:
        log("未配置任何推送渠道（SEND_KEY / 邮箱环境变量均为空），退出")
        return

    config = load_config()
    site_name = config["name"]
    max_push_once = config.get("max_push_once", 5)
    history = load_history()
    health = load_health()
    history_set = set(history)
    push_count = 0

    try:
        session = requests.Session()
        session.headers.update(HEADERS)
        pass_challenge(session, config)

        lectures = fetch_list(session, config)
        if not lectures:
            raise RuntimeError("获取到 0 条讲座（可能是网站结构变化、验证失效或网络异常）")

        new_lectures = [
            l for l in lectures
            if f"{l['title']}|{l['date']}" not in history_set
        ]

        if not new_lectures:
            log("  暂无新讲座")
        else:
            log(f"  发现 {len(new_lectures)} 条新讲座")
            for lec in new_lectures:
                if push_count >= max_push_once:
                    log("  达到单次推送上限，剩余的下次推送")
                    break

                log(f"  处理: {lec['title'][:30]}...")
                detail = fetch_detail(session, config, lec["link"])
                push_lecture(site_name, lec, detail)

                history_set.add(f"{lec['title']}|{lec['date']}")
                push_count += 1
                time.sleep(1)

        # 运行成功：若此前处于故障状态，发送恢复通知
        if health.get("status") == "fail":
            send_alert("服务已恢复", f"讲座推送已恢复正常，本次获取到 {len(lectures)} 条讲座。")
        health["status"] = "ok"
        health["consecutive_failures"] = 0

    except Exception as e:
        log(f"运行失败: {e}")
        health["consecutive_failures"] = health.get("consecutive_failures", 0) + 1
        now = time.time()
        # 首次失败立即告警；持续故障时每 ALERT_COOLDOWN 秒最多告警一次
        should_alert = (
            health.get("status") == "ok"
            or now - health.get("last_alert_ts", 0) > ALERT_COOLDOWN
        )
        if should_alert:
            send_alert(
                "推送服务异常",
                f"讲座推送运行失败：\n\n"
                f"错误：{e}\n"
                f"连续失败次数：{health['consecutive_failures']}\n\n"
                f"请检查网站结构或验证逻辑是否变化。",
            )
            health["last_alert_ts"] = now
        health["status"] = "fail"

    # 持久化（按时间倒序，新的在前）
    save_history(list(history_set))
    save_health(health)
    log(f"=== 检查结束，共推送 {push_count} 条 ===")


if __name__ == "__main__":
    main()
