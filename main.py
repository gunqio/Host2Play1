import os
import sys
import time
import random
import html
import requests
import tempfile
import subprocess
from datetime import datetime, timezone, timedelta
from xvfbwrapper import Xvfb
from DrissionPage import ChromiumPage, ChromiumOptions

try:
    import speech_recognition as sr
    from pydub import AudioSegment
except ImportError:
    pass

# ==============================================================================
# 配置区域
# ==============================================================================
RENEW_URLS = [
    "https://host2play.gratis/server/renew?i=65bd476a-deb6-4585-87fb-7adbe34809e8",
]

MAX_CAPTCHA = 2  # 减少尝试，防止被拉黑
MAX_RENEW_RETRIES_PER_URL = 8  # 智能重试，不是硬冲

# ==============================================================================
# 自定义异常
# ==============================================================================
class CaptchaBlocked(Exception):
    pass

# ==============================================================================
# 统一日志
# ==============================================================================
def log(msg, level="INFO"):
    prefix = {"INFO": "[INFO]", "WARN": "[WARN]", "ERROR": "[ERROR]"}.get(level, "[INFO]")
    print(f"{prefix} {msg}", flush=True)

# ==============================================================================
# Telegram 通知
# ==============================================================================
def send_tg_photo(token, chat_id, photo_path, caption, parse_mode='HTML'):
    if not token or not chat_id:
        log("未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过通知。", "WARN")
        return
    if not photo_path or not os.path.exists(photo_path):
        log("未找到截图文件，跳过通知。", "WARN")
        return
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    try:
        with open(photo_path, "rb") as photo_file:
            response = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption, "parse_mode": parse_mode},
                files={"photo": photo_file},
                timeout=30,
            )
        response.raise_for_status()
        log("Telegram 图片通知发送成功")
    except Exception as e:
        log(f"Telegram 图片通知异常: {e}", "ERROR")

# ==============================================================================
# 页面元素提取
# ==============================================================================
def get_server_name(page):
    try:
        ele = page.ele('#serverName', timeout=2)
        if ele:
            return ele.text.strip()
    except Exception:
        pass
    return "未知"

def get_expire_time(page):
    try:
        ele = page.ele('#expireDate', timeout=2)
        if ele:
            return ele.text.strip()
    except Exception:
        pass
    selectors = ['text:Expires in:', 'text:Deletes on:']
    for selector in selectors:
        try:
            ele = page.ele(selector, timeout=1)
            if ele:
                text = (ele.text or "").strip()
                if ":" in text:
                    return text.split(":", 1)[1].strip()
                if text:
                    return text
        except Exception:
            pass
    return "未知"

# ==============================================================================
# 构建通知
# ==============================================================================
def build_notification(success, url, server_name, old_expire, new_expire=None, failure_reason=""):
    if success:
        lines = [
            "✅ 续订成功",
            "",
            f"服务器：{server_name}",
            f"到期: {old_expire} -> {new_expire}",
            f"URL: {url}",
        ]
    else:
        lines = [
            "❌ 续订失败",
            "",
            f"服务器：{server_name}",
            f"URL: {url}",
        ]
        if failure_reason:
            lines.append(f"失败原因: {failure_reason}")
    lines.append("")
    lines.append("Host2Play Auto Renew")
    return "\n".join(lines)

def capture_page_screenshot(page, file_name):
    try:
        page.get_screenshot(path=file_name)
        return file_name
    except Exception as e:
        log(f"截图失败: {e}", "WARN")
        return None

# ==============================================================================
# WARP 重连（换 IP）
# ==============================================================================
def restart_warp():
    log("正在重启 WARP 更换 IP...", "WARN")
    try:
        subprocess.run(["sudo", "warp-cli", "disconnect"], check=False, timeout=15)
        time.sleep(2)
        subprocess.run(["sudo", "warp-cli", "registration", "delete"], check=False, timeout=15)
        time.sleep(2)
        subprocess.run(["sudo", "warp-cli", "registration", "new"], check=True, timeout=20)
        time.sleep(2)
        subprocess.run(["sudo", "warp-cli", "connect"], check=True, timeout=20)
        time.sleep(5)
        new_ip = requests.get("https://api.ipify.org", timeout=8).text.strip()
        log(f"WARP 重启成功，新IP: {new_ip}")
        return True
    except Exception as e:
        log(f"WARP 重启失败: {e}", "ERROR")
        return False

# ==============================================================================
# reCAPTCHA
# ==============================================================================
def find_recaptcha_frame(page, kind):
    try:
        for frame in page.get_frames():
            u = (frame.url or "").lower()
            if "recaptcha" in u and kind in u:
                return frame
    except:
        pass
    return None

def is_recaptcha_solved(page):
    try:
        for f in page.get_frames():
            t = f.run_js('return document.querySelector("#g-recaptcha-response")?.value || ""')
            if len(t) > 30:
                return True
    except:
        pass
    a = find_recaptcha_frame(page, "anchor")
    if a:
        try:
            return a.run_js("return document.querySelector('#recaptcha-anchor')?.getAttribute('aria-checked')") == "true"
        except:
            pass
    return False

def is_blocked(page):
    b = find_recaptcha_frame(page, "bframe")
    if not b:
        return False
    try:
        return b.run_js("""
            document.querySelector('.rc-doscaptcha-header-text') ||
            document.querySelector('.rc-audiochallenge-error-message')
        """) is not None
    except:
        return False

def click_recaptcha_checkbox(page):
    a = find_recaptcha_frame(page, "anchor")
    if not a:
        for _ in range(60):
            a = find_recaptcha_frame(page, "anchor")
            if a: break
            time.sleep(0.5)
    if not a:
        raise RuntimeError("找不到验证码")
    box = a.ele("#recaptcha-anchor", timeout=3)
    if not box:
        raise RuntimeError("找不到复选框")
    page.actions.move_to(box, duration=random.uniform(0.2,0.6))
    time.sleep(random.uniform(0.2,0.4))
    try:
        box.click()
    except:
        box.click(by_js=True)
    time.sleep(2)
    if is_blocked(page):
        raise CaptchaBlocked("IP 被封")

def switch_to_audio(page):
    b = find_recaptcha_frame(page, "bframe")
    if not b:
        return False
    for _ in range(2):
        try:
            btn = b.ele("#recaptcha-audio-button", timeout=2)
            if btn:
                btn.click(by_js=True)
                time.sleep(2)
                return b.ele("#audio-response", timeout=2) is not None
        except:
            pass
    return False

def get_audio_url(page):
    b = find_recaptcha_frame(page, "bframe")
    if not b:
        return None
    for _ in range(6):
        try:
            el = b.ele(".rc-audiochallenge-tdownload-link", timeout=1) or b.ele("#audio-source", timeout=1)
            if el:
                return html.unescape(el.attr("href") or el.attr("src"))
        except:
            pass
        time.sleep(0.5)
    return None

def download_audio(url):
    try:
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        p = tempfile.mktemp(suffix=".mp3")
        with open(p,"wb") as f:
            f.write(r.content)
        return p
    except:
        return None

def recognize_audio(mp3):
    try:
        wav = mp3.replace(".mp3",".wav")
        AudioSegment.from_mp3(mp3).export(wav, format="wav")
        rec = sr.Recognizer()
        with sr.AudioFile(wav) as s:
            t = rec.recognize_google(rec.record(s))
        os.remove(wav)
        return t.lower().strip()
    except:
        return None

def fill_and_verify(page, text):
    b = find_recaptcha_frame(page, "bframe")
    if not b:
        return False
    inp = b.ele("#audio-response", timeout=2)
    if not inp:
        return False
    inp.click()
    inp.input(text)
    time.sleep(random.uniform(0.3,0.8))
    try:
        b.ele("#recaptcha-verify-button").click(by_js=True)
    except:
        pass
    time.sleep(3)
    return True

def solve_recaptcha(page):
    for _ in range(60):
        if find_recaptcha_frame(page, "anchor"):
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("验证码加载超时")

    for attempt in range(MAX_CAPTCHA):
        if is_recaptcha_solved(page):
            return True
        if is_blocked(page):
            raise CaptchaBlocked("IP 被 Google 拉黑")

        if attempt == 0:
            click_recaptcha_checkbox(page)
            time.sleep(2)
            if is_recaptcha_solved(page):
                return True

        if not switch_to_audio(page):
            time.sleep(2)
            continue

        url = get_audio_url(page)
        if not url:
            time.sleep(2)
            continue

        mp3 = download_audio(url)
        if not mp3:
            time.sleep(2)
            continue

        text = recognize_audio(mp3)
        os.remove(mp3)
        if not text:
            time.sleep(2)
            continue

        log(f"识别: {text}")
        fill_and_verify(page, text)
        if is_recaptcha_solved(page):
            return True
        time.sleep(random.uniform(1,2))

    raise RuntimeError("验证码尝试次数超限")

# ==============================================================================
# 续期逻辑
# ==============================================================================
def renew_single_url(url):
    success = False
    server = old = new = "未知"
    screen = None
    reason = ""
    os.makedirs("output/screenshots", exist_ok=True)

    vdisplay = Xvfb(width=1280, height=720, colordepth=24)
    vdisplay.start()

    try:
        for attempt in range(1, MAX_RENEW_RETRIES_PER_URL+1):
            log(f"\n===== 第 {attempt} 次尝试 =====")
            page = None

            try:
                co = ChromiumOptions()
                co.set_browser_path("/usr/bin/google-chrome")
                co.set_argument("--no-sandbox")
                co.set_argument("--disable-dev-shm-usage")
                co.set_argument("--disable-gpu")
                co.set_argument("--disable-popup-blocking")
                co.set_argument("--window-size=1280,720")
                co.set_user_data_path(tempfile.mkdtemp())
                co.auto_port()
                co.headless(False)

                # 超强反检测
                co.set_argument("--disable-blink-features=AutomationControlled")
                co.add_extension_args("--disable-features=IsolateOrigins,site-per-process")

                page = ChromiumPage(co)
                page.set.timeouts(20)

                # 隐藏自动化痕迹
                page.add_init_js("""
                    Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                    Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
                    Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
                """)

                log("打开页面...")
                page.get(url, retry=2)
                time.sleep(random.uniform(4,6))

                server = get_server_name(page)
                old = get_expire_time(page)
                log(f"服务器: {server} | 到期: {old}")

                # 清理广告
                page.run_js("""
                    document.querySelectorAll('ins.adsbygoogle,iframe[src*=ads],.modal').forEach(i=>i.remove());
                """)
                time.sleep(1)

                # 模拟真人行为
                for _ in range(2):
                    page.scroll.down(random.randint(200,500))
                    time.sleep(random.uniform(0.5,1.2))
                    page.actions.move(random.randint(200,700), random.randint(200,500))

                # 打开续期弹窗
                log("打开续期弹窗")
                page.run_js("""
                    document.querySelectorAll('button').forEach(b=>{
                        if(b.innerText.includes('Renew server')) b.click();
                    });
                """)
                time.sleep(random.uniform(4,6))

                # 验证码
                if find_recaptcha_frame(page, "anchor"):
                    log("处理验证码...")
                    try:
                        solve_recaptcha(page)
                    except CaptchaBlocked:
                        reason = "IP 被封，换IP重试"
                        log(reason, "WARN")
                        if page: page.quit()
                        restart_warp()
                        continue

                # 最终确认
                log("点击确认续期")
                page.run_js("""
                    document.querySelectorAll('button').forEach(b=>{
                        if(b.innerText.trim()==='Renew') b.click();
                    });
                """)
                time.sleep(random.uniform(6,9))

                new = get_expire_time(page)
                if new != old and new != "未知":
                    success = True
                    log(f"✅ 成功: {old} → {new}")
                else:
                    page_text = page.html.lower()
                    if "successfully" in page_text or "renewed" in page_text:
                        success = True
                break

            except CaptchaBlocked:
                reason = "IP 被封"
                if page: page.quit()
                restart_warp()
                continue
            except Exception as e:
                reason = str(e)[:200]
                log(f"异常: {e}", "ERROR")
                if attempt >= MAX_RENEW_RETRIES_PER_URL:
                    break
                restart_warp()
            finally:
                if page:
                    fname = f"host2play-{server}-{'ok' if success else 'fail'}.png"
                    screen = capture_page_screenshot(page, f"output/screenshots/{fname}")
                    page.quit()

    finally:
        vdisplay.stop()

    return success, server, old, new, screen, reason

# ==============================================================================
# 主入口
# ==============================================================================
def main():
    tg_token = os.getenv("TG_BOT_TOKEN")
    tg_chat_id = os.getenv("TG_CHAT_ID")
    ok = 0
    for u in RENEW_URLS:
        s, sv, o, n, sc, r = renew_single_url(u)
        if s:
            ok +=1
        txt = build_notification(s, u, sv, o, n, r)
        send_tg_photo(tg_token, tg_chat_id, sc, txt)
    log(f"完成：成功 {ok}/{len(RENEW_URLS)}")
    sys.exit(0 if ok == len(RENEW_URLS) else 1)

if __name__ == "__main__":
    main()
