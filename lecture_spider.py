"""
学术讲座自动推送脚本
- 从 config.json 读取待监控的网站列表（支持任意学校/学院页面）
- 自动抓取列表与详情，去重后通过邮件 / 微信推送
- 设计为在 GitHub Actions 上运行，密钥通过 Secrets 注入
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


# ====================== 配置加载 ======================
def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ====================== 历史记录 ======================
def load_history():
    """历史记录按站点名称分组，格式: {site_name: ["title|date", ...]}"""
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 兼容旧版扁平列表格式
        if isinstance(data, list):
            return {"_legacy": data}
        return data
    except Exception:
        return {}


def save_history(history):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"保存历史失败: {e}")


# ====================== 网页抓取 ======================
def pass_challenge(session, site):
    """处理部分网站的反爬验证（默认关闭，仅在 config 中启用时执行）。

    页面会返回一段 JS，内含 challengeId 与一道算术题（a op b），
    需算出答案并连同浏览器指纹与 hash 一起 POST 到验证接口，
    通过后拿到 client_id cookie 才能访问真实内容。
    """
    challenge_cfg = site.get("challenge", {})
    if not challenge_cfg.get("enabled"):
        return
    try:
        log(f"  [{site['name']}] 正在通过网站验证...")
        resp = session.get(site["list_url"], timeout=10)
        resp.encoding = "utf-8"

        challenge_id = _extract_challenge_id(resp.text)
        answer = _extract_answer(resp.text)
        if not challenge_id or answer is None:
            log(f"  [{site['name']}] 未发现验证参数，跳过")
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

        verify = session.post(
            challenge_cfg["verify_url"],
            json={
                "challenge_id": challenge_id,
                "answer": answer,
                "browser_info": browser_info,
                "hash": hash_value,
            },
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if verify.json().get("success"):
            client_id = verify.json()["client_id"]
            session.cookies.set("client_id", client_id, domain=_domain(site["base_url"]))
            log(f"  [{site['name']}] 验证通过")
        else:
            log(f"  [{site['name']}] 验证未通过: {verify.text[:120]}")
    except Exception as e:
        log(f"  [{site['name']}] 验证异常: {e}")


def _extract_challenge_id(html):
    # 兼容单双引号: var challengeId = 'xxx' / "xxx"
    m = re.search(r"""var\s+challengeId\s*=\s*['"]([^'"]+)['"]""", html)
    return m.group(1) if m else None


def _extract_answer(html):
    """从验证页 JS 中解析算术题并计算答案。

    新版页面形如: var a = 18; var b = 13; var operator = '*';
    旧版页面形如: var answer = 4;
    """
    m = re.search(r"var\s+answer\s*=\s*(\d+)\s*;", html)
    if m:
        return int(m.group(1))

    am = re.search(r"var\s+a\s*=\s*(-?\d+)\s*;", html)
    bm = re.search(r"var\s+b\s*=\s*(-?\d+)\s*;", html)
    om = re.search(r"var\s+operator\s*=\s*['\"]([+\-*])['\"]", html)
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


def fetch_list(session, site):
    """抓取列表页，返回讲座列表。"""
    list_cfg = site.get("list", {})
    try:
        resp = session.get(site["list_url"], timeout=10)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        item_selector = list_cfg.get("item_selector", "li")
        date_selector = list_cfg.get("date_selector", "span")
        date_regex = list_cfg.get("date_regex", r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})")
        link_keywords = list_cfg.get("link_keywords", [])
        title_keywords = list_cfg.get("title_keywords", [])
        title_min_length = list_cfg.get("title_min_length", 5)
        max_items = list_cfg.get("max_items", 15)

        lectures = []
        for item in soup.select(item_selector):
            a = item.find("a")
            if not a:
                continue

            title = a.get_text(strip=True)
            link = a.get("href", "")
            if not title or len(title) < title_min_length:
                continue

            # 标题关键词过滤（如只保留含"讲座/报告"的条目）
            if title_keywords and not any(k in title for k in title_keywords):
                continue

            # 链接关键词过滤
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

            # 解析为绝对链接
            link = urljoin(site["base_url"], link)
            if not link.startswith("http"):
                continue

            lectures.append(
                {"title": title, "date": date or "未知", "link": link}
            )

        # 去重
        seen = set()
        unique = []
        for lec in lectures:
            if lec["title"] not in seen:
                seen.add(lec["title"])
                unique.append(lec)

        log(f"  [{site['name']}] 获取到 {len(unique)} 条讲座")
        return unique[:max_items]
    except Exception as e:
        log(f"  [{site['name']}] 获取列表失败: {e}")
        return []


def fetch_detail(session, site, url):
    """抓取详情页，提取报告人 / 时间 / 地点。"""
    detail_cfg = site.get("detail", {})
    try:
        resp = session.get(url, timeout=10)
        resp.encoding = "utf-8"
        content = BeautifulSoup(resp.text, "html.parser").get_text("\n", strip=True)

        result = {"speaker": "详见正文", "time": "详见正文", "location": "详见正文"}

        speaker = _extract_field(content, detail_cfg.get("speaker_labels", ["报告人", "主讲人", "嘉宾"]))
        if speaker:
            result["speaker"] = speaker

        time_info = _extract_field(content, detail_cfg.get("time_labels", ["时间", "讲座时间", "报告时间"]))
        if time_info:
            result["time"] = time_info

        location = _extract_field(content, detail_cfg.get("location_labels", ["地点", "举办地点", "报告地点"]))
        if location:
            result["location"] = location

        return result
    except Exception as e:
        log(f"  获取详情失败: {e}")
        return {"speaker": "获取失败", "time": "获取失败", "location": "获取失败"}


def _extract_field(content, labels):
    """根据一组标签（如 报告人/主讲人）从正文中提取对应值。"""
    for label in labels:
        # 允许标签字间出现空白，如 "时 间"
        flexible = r"\s*".join(re.escape(ch) for ch in label)
        pattern = rf"{flexible}[:：]\s*(.+?)(?:\n|。|；|;|,|，)"
        m = re.search(pattern, content)
        if m:
            return m.group(1).strip()[:50]
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
        f"**来源：** {site_name}\n"
        f"**发布：** {lec['date']}\n"
        f"**报告人：** {detail['speaker']}\n"
        f"**时间：** {detail['time']}\n"
        f"**地点：** {detail['location']}\n"
        f"[查看详情]({lec['link']})"
    )
    em_content = (
        f"【讲座通知】\n"
        f"来源：{site_name}\n"
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
    max_push_once = config.get("max_push_once", 5)
    history = load_history()

    push_count = 0

    for site in config.get("sites", []):
        if not site.get("enabled", True):
            continue

        log(f"检查站点: {site['name']}")
        session = requests.Session()
        session.headers.update(HEADERS)
        pass_challenge(session, site)

        lectures = fetch_list(session, site)
        if not lectures:
            continue

        site_history = set(history.get(site["name"], []))
        new_lectures = [
            l for l in lectures
            if f"{l['title']}|{l['date']}" not in site_history
        ]

        if not new_lectures:
            log(f"  [{site['name']}] 暂无新讲座")
            continue

        log(f"  [{site['name']}] 发现 {len(new_lectures)} 条新讲座")

        for lec in new_lectures:
            if push_count >= max_push_once:
                log("达到单次推送上限，剩余的下次推送")
                break

            log(f"  处理: {lec['title'][:30]}...")
            detail = fetch_detail(session, site, lec["link"])
            push_lecture(site["name"], lec, detail)

            site_history.add(f"{lec['title']}|{lec['date']}")
            push_count += 1
            time.sleep(1)

        history[site["name"]] = list(site_history)

        if push_count >= max_push_once:
            break

    save_history(history)
    log(f"=== 检查结束，共推送 {push_count} 条 ===")


if __name__ == "__main__":
    main()
