import os
import sys
import time
import requests
import tempfile
from DrissionPage import ChromiumPage, ChromiumOptions

# ---------------- 配置区 ----------------
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
H2P_USER = os.getenv("H2P_USER", "")
H2P_PASS = os.getenv("H2P_PASS", "")
PROXY_SOCKS5 = os.getenv("PROXY_SOCKS5", "")
BASE_URL = "https://host2play.com"
SCREENSHOT_DIR = "output/screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# ---------------- TG 通知 ----------------
def tg_send(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=data, timeout=15)
    except Exception as e:
        print("TG发送失败:", e)

# ---------------- 日志+截图 ----------------
def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def save_screenshot(page, name):
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    try:
        page.get_screenshot(path=path, full_page=True)
        log(f"截图保存: {path}")
    except Exception as e:
        log("截图失败: " + str(e))

# ---------------- 核心续期 ----------------
def renew():
    co = ChromiumOptions()
    co.set_browser_path("/usr/bin/google-chrome")
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument("--disable-gpu")
    co.set_argument("--window-size=1280,720")
    co.set_user_data_path(tempfile.mkdtemp())
    co.auto_port()
    co.headless(False)

    # 关键：走代理
    if PROXY_SOCKS5:
        co.set_proxy(PROXY_SOCKS5)
        log(f"使用代理: {PROXY_SOCKS5}")

    page = ChromiumPage(co)
    try:
        log("打开 Host2Play")
        page.get(BASE_URL)
        time.sleep(3)
        save_screenshot(page, "01-home")

        # 登录（根据你实际页面改选择器）
        log("尝试登录")
        page.ele('xpath://input[@name="username"]').input(H2P_USER)
        page.ele('xpath://input[@name="password"]').input(H2P_PASS)
        time.sleep(1)
        page.ele('xpath://button[@type="submit"]').click()
        time.sleep(5)
        save_screenshot(page, "02-after-login")

        # 进入续期页面（根据你实际页面改）
        log("进入续期页面")
        page.get(f"{BASE_URL}/user/renew")
        time.sleep(3)
        save_screenshot(page, "03-renew-page")

        # 点击续期（根据你实际页面改）
        page.ele('xpath://button[contains(text(),"Renew") or contains(text(),"续期")]').click()
        time.sleep(5)
        save_screenshot(page, "04-after-renew")

        log("✅ 续期流程完成")
        tg_send("✅ Host2Play 续期成功")
        return True

    except Exception as e:
        log(f"❌ 失败: {e}")
        save_screenshot(page, "error")
        tg_send(f"❌ Host2Play 续期失败\n{e}")
        return False
    finally:
        page.quit()

if __name__ == "__main__":
    if not H2P_USER or not H2P_PASS:
        log("❌ 缺少 H2P_USER / H2P_PASS")
        sys.exit(1)
    ok = renew()
    sys.exit(0 if ok else 1)
