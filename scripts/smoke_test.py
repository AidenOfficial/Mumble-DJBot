#!/usr/bin/env python3
# coding=utf-8
"""端到端冒烟测试 —— 验证 Spotify / Bilibili 下载链路与运行环境是否正常。

这个脚本**不连接 Mumble 服务器**,只验证"拿到音频文件"为止的全部链路
(这正是最容易因为 spotdl / Deno / yt-dlp / B 站反爬而出问题的部分),
所以可以在 NAS 上、机器人正常运行的同时单独跑。

它会真实走机器人自己的代码(util.get_bilibili_url_from_input、
media.spotify.download_tracks),而不是另写一套,所以通过即代表机器人
对应功能可用。

在 NAS 上(Docker)运行:
    docker compose build
    docker compose run --rm --entrypoint venv/bin/python \
        botamusique scripts/smoke_test.py

只测某一项 / 跳过真实下载等,见 --help。
"""

import argparse
import configparser
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

# 默认测试样本(都是长期稳定存在的内容,可用命令行覆盖)
DEFAULT_SPOTIFY_QUERY = "周杰伦 晴天"
DEFAULT_BILIBILI_INPUT = "BV1GJ411x7h7"   # 经典测试视频

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
results = []  # (name, status, detail)  status in {PASS, FAIL, SKIP}


def record(name, status, detail=""):
    color = {"PASS": GREEN, "FAIL": RED, "SKIP": YELLOW}[status]
    print(f"  [{color}{status}{RESET}] {name}" + (f" — {detail}" if detail else ""))
    results.append((name, status, detail))


def section(title):
    print(f"\n{'='*70}\n{title}\n{'='*70}")


def bootstrap_config():
    """像 mumbleBot.py 那样加载分层配置,设置 var.config。"""
    import variables as var
    config = configparser.ConfigParser(interpolation=None, allow_no_value=True)
    read = config.read(
        [os.path.join(ROOT, "configuration.default.ini"),
         os.path.join(ROOT, "configuration.ini")],
        encoding="utf-8")
    var.config = config
    return var, config, read


# --------------------------------------------------------------------------
# 1. 运行环境:外部工具是否齐全
# --------------------------------------------------------------------------
def check_tools():
    section("1. 运行环境 / 外部工具")
    py = sys.executable

    def ver(args, name, critical=True):
        try:
            out = subprocess.run(args, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, timeout=60)
            line = (out.stdout or b"").decode("utf-8", "replace").strip().splitlines()
            v = line[0] if line else "(no output)"
            if out.returncode == 0:
                record(name, "PASS", v)
            else:
                record(name, "FAIL" if critical else "SKIP",
                       f"exit {out.returncode}: {v}")
        except FileNotFoundError:
            record(name, "FAIL" if critical else "SKIP", "未安装 / 不在 PATH")
        except subprocess.TimeoutExpired:
            record(name, "FAIL" if critical else "SKIP", "调用超时")

    record("python", "PASS", sys.version.split()[0])
    ver([py, "-m", "spotdl", "--version"], "spotdl (Spotify 下载器)")
    ver(["deno", "--version"], "deno (spotdl 解析 YouTube JS 必需)")
    ver([py, "-m", "yt_dlp", "--version"], "yt-dlp (实际下载器)")
    ver(["ffmpeg", "-version"], "ffmpeg")
    ver(["ffprobe", "-version"], "ffprobe")


# --------------------------------------------------------------------------
# 2. 机器人自身代码可正常导入(验证 spotify 可选导入守卫等)
# --------------------------------------------------------------------------
def check_imports():
    section("2. 机器人模块导入")
    try:
        import util  # noqa
        record("import util", "PASS")
    except Exception as e:
        record("import util", "FAIL", repr(e))
    try:
        import command
        record("import command (全部命令接线)", "PASS")
        avail = getattr(command, "_spotify_available", None)
        if avail is True:
            record("media.spotify 模块可用", "PASS")
        elif avail is False:
            record("media.spotify 模块可用", "FAIL",
                   "命令层能启动但 !spotify 会被禁用(缺 spotdl?)")
        else:
            record("media.spotify 模块可用", "SKIP", "无 _spotify_available 标志")
    except Exception as e:
        record("import command", "FAIL", repr(e))


# --------------------------------------------------------------------------
# 3. Bilibili 链路:输入 -> 规范化为 av 链接 -> yt-dlp 取元数据(验证不被 412)
# --------------------------------------------------------------------------
def check_bilibili(raw_input, do_download):
    section("3. Bilibili 链路")
    try:
        import util
    except Exception as e:
        record("Bilibili", "FAIL", f"无法 import util: {e!r}")
        return

    url = util.get_bilibili_url_from_input(raw_input)
    if not url:
        record("BV/链接 规范化", "FAIL", f"get_bilibili_url_from_input('{raw_input}') 返回空")
        return
    record("BV/链接 规范化", "PASS", f"{raw_input} -> {url}")

    # 关键:取元数据。BV 形态会被 B 站反爬返回 412,转 av 后应正常。
    try:
        import yt_dlp
    except Exception as e:
        record("yt-dlp 取 B 站元数据", "SKIP", f"无 yt_dlp: {e!r}")
        return
    try:
        opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                "noplaylist": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        title = info.get("title", "?")
        dur = info.get("duration")
        record("yt-dlp 取 B 站元数据(验证不被 412)", "PASS",
               f"标题《{title}》时长 {dur}s")
    except Exception as e:
        record("yt-dlp 取 B 站元数据", "FAIL",
               f"{type(e).__name__}: {str(e)[:200]}")
        return

    if not do_download:
        record("B 站真实下载", "SKIP", "默认跳过,加 --full 启用")
        return

    tmp = tempfile.mkdtemp(prefix="smoke_bili_")
    try:
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
                "format": "bestaudio/best",
                "outtmpl": os.path.join(tmp, "%(id)s.%(ext)s")}
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        files = [f for f in os.listdir(tmp) if os.path.getsize(os.path.join(tmp, f)) > 0]
        if files:
            f = os.path.join(tmp, files[0])
            record("B 站真实下载", "PASS", f"{files[0]} ({os.path.getsize(f)//1024} KiB)")
            _probe(f, "B 站文件可被 ffprobe 解码")
        else:
            record("B 站真实下载", "FAIL", "没有产出文件")
    except Exception as e:
        record("B 站真实下载", "FAIL", f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# 4. Spotify 链路:关键词 -> Spotify 元数据 -> YouTube 下载 -> 文件 -> 可解码
#    真实调用机器人自己的 media.spotify.download_tracks()
# --------------------------------------------------------------------------
def check_spotify(query, do_download):
    section("4. Spotify 链路(关键词 -> Spotify -> YouTube 下载)")
    var, config, _ = bootstrap_config()

    cid = config.get("spotify", "client_id", fallback="").strip()
    sec = config.get("spotify", "client_secret", fallback="").strip()
    if not cid or not sec:
        record("Spotify 凭据", "FAIL",
               "configuration.ini 的 [spotify] 未配置 client_id/client_secret")
        return
    record("Spotify 凭据", "PASS", "client_id / client_secret 已配置")

    try:
        import media.spotify as sp
    except Exception as e:
        record("import media.spotify", "FAIL", repr(e))
        return

    if not do_download:
        record("Spotify 真实下载", "SKIP",
               "默认跳过(需联网+Deno,耗时)。加 --full 启用")
        return

    # 把下载目录改到独立临时目录,避免污染真实缓存。绝对路径可绕过 solve_filepath。
    tmp = tempfile.mkdtemp(prefix="smoke_spotify_")
    config.set("spotify", "download_folder", tmp + os.sep)
    try:
        print(f"  {DIM}正在用 spotdl 下载「{query}」,首次可能较慢(拉取 Deno/YouTube)…{RESET}")
        paths = sp.download_tracks([query])
        if paths:
            f = paths[0]
            ok = os.path.isfile(f) and os.path.getsize(f) > 0
            if ok:
                record("Spotify 真实下载", "PASS",
                       f"{os.path.basename(f)} ({os.path.getsize(f)//1024} KiB)")
                _probe(f, "Spotify 文件可被 ffprobe 解码")
            else:
                record("Spotify 真实下载", "FAIL", "返回了路径但文件不存在/为空")
        else:
            record("Spotify 真实下载", "FAIL",
                   "download_tracks 返回空(spotdl 没匹配到 / Deno 缺失 / 被风控)")
    except sp.SpotifyError as e:
        record("Spotify 真实下载", "FAIL", f"SpotifyError: {str(e)[:200]}")
    except Exception as e:
        record("Spotify 真实下载", "FAIL", f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _probe(path, name):
    """用 ffprobe 确认文件确实是可解码的音频。"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name", "-of",
             "default=nw=1:nk=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        codec = (out.stdout or b"").decode("utf-8", "replace").strip()
        if out.returncode == 0 and codec:
            record(name, "PASS", f"音频编码 {codec}")
        else:
            record(name, "FAIL", f"ffprobe 未识别出音频流 (exit {out.returncode})")
    except Exception as e:
        record(name, "FAIL", f"{type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser(description="Mumble 机器人下载链路冒烟测试")
    ap.add_argument("--only", choices=["tools", "imports", "bili", "spotify"],
                    action="append", help="只跑指定测试(可多次)")
    ap.add_argument("--full", action="store_true",
                    help="执行真实下载(默认只测元数据/解析,加此项才真正下歌)")
    ap.add_argument("--spotify-query", default=DEFAULT_SPOTIFY_QUERY)
    ap.add_argument("--bilibili", default=DEFAULT_BILIBILI_INPUT)
    args = ap.parse_args()
    only = set(args.only or [])

    print(f"{DIM}项目根目录: {ROOT}{RESET}")
    print(f"{DIM}真实下载: {'开启 (--full)' if args.full else '关闭(只测解析,加 --full 下真歌)'}{RESET}")

    if not only or "tools" in only:
        check_tools()
    if not only or "imports" in only:
        check_imports()
    if not only or "bili" in only:
        check_bilibili(args.bilibili, args.full)
    if not only or "spotify" in only:
        check_spotify(args.spotify_query, args.full)

    section("结果汇总")
    n_pass = sum(1 for _, s, _ in results if s == "PASS")
    n_fail = sum(1 for _, s, _ in results if s == "FAIL")
    n_skip = sum(1 for _, s, _ in results if s == "SKIP")
    print(f"  {GREEN}PASS {n_pass}{RESET}   {RED}FAIL {n_fail}{RESET}   {YELLOW}SKIP {n_skip}{RESET}")
    if n_fail:
        print(f"\n{RED}存在失败项,请按上面的 detail 排查。{RESET}")
        for name, s, detail in results:
            if s == "FAIL":
                print(f"  - {name}: {detail}")
    else:
        print(f"\n{GREEN}全部通过(SKIP 的项可加 --full 做真实下载验证)。{RESET}")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
