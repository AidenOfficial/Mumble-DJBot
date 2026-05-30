#!/usr/bin/env bash
#
# ============================================================================
# botamusique(含 Bilibili / Spotify 扩展)ARM 架构 Ubuntu Linux 安装脚本
# ============================================================================
#
# 用途:
#   在一台 ARM 架构(aarch64 / armv7 等)的 Ubuntu Linux 机器上,一键安装
#   本 Mumble 音乐机器人(基于 botamusique,额外支持 Bilibili 与 Spotify 播放)
#   所需的系统依赖与 Python 依赖,并准备好配置文件。
#
# 前提:
#   - 本仓库已经用 git 克隆到某个目录(例如 /opt/botamusique)。
#   - 在该仓库目录下运行本脚本,即:
#         cd /opt/botamusique
#         bash deploy/install.sh
#   - 运行账号需要有 sudo 权限(脚本会用 apt 安装系统软件包)。
#
# 安装完成后:
#   - 仓库目录下会生成 Python 虚拟环境 venv/。
#   - 若不存在 configuration.ini,会从 configuration.example.ini 复制一份。
#   - 按脚本结尾的提示编辑 configuration.ini 并设置开机自启即可。
#
# ============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# 0. 基础信息
# ---------------------------------------------------------------------------
# REPO_DIR 取当前工作目录:本脚本约定在仓库根目录下运行。
REPO_DIR="$(pwd)"

echo "=============================================================="
echo " botamusique (Bilibili/Spotify 扩展) 安装脚本"
echo " 仓库目录: ${REPO_DIR}"
echo " 系统架构: $(uname -m)"
echo "=============================================================="

# 简单校验:确认确实在仓库根目录(应存在 mumbleBot.py 与 requirements.txt)。
if [[ ! -f "${REPO_DIR}/mumbleBot.py" || ! -f "${REPO_DIR}/requirements.txt" ]]; then
    echo "错误:当前目录下找不到 mumbleBot.py / requirements.txt。" >&2
    echo "请先 cd 到本仓库根目录,再运行 bash deploy/install.sh。" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. 安装系统依赖(apt)
# ---------------------------------------------------------------------------
# 各软件包说明:
#   python3 / python3-pip / python3-venv / python3-dev —— Python 运行时、包管理与
#       虚拟环境支持;python3-dev 提供编译 C 扩展所需的头文件。
#   build-essential —— gcc/make 等编译工具链,部分 Python 依赖在 ARM 上无预编译
#       wheel,需要本地编译。
#   ffmpeg —— 音频转码核心;yt-dlp / spotdl 下载后用它转码,机器人推流也依赖它。
#   libopus0 / opus-tools —— Opus 编解码库与工具;Mumble 语音传输使用 Opus 编码,
#       音频推流必须有它。
#   libmagic1 —— 文件类型识别库,是 Python 依赖 python-magic 的底层依赖。
#   libffi-dev —— 编译 cffi 等依赖所需。
#   libjpeg-dev / zlib1g-dev —— 编译 Pillow(图像处理)所需的 JPEG 与 zlib 开发库。
#   git —— 拉取/更新代码。
#   curl —— 下载脚本或文件时使用。
echo
echo ">>> [1/5] 使用 apt 安装系统依赖 ..."
sudo apt-get update
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    ffmpeg \
    libopus0 \
    opus-tools \
    libmagic1 \
    libffi-dev \
    libjpeg-dev \
    zlib1g-dev \
    git \
    curl

# ---------------------------------------------------------------------------
# 2. 创建 Python 虚拟环境
# ---------------------------------------------------------------------------
# 在仓库目录下创建 venv/,与系统 Python 隔离,避免污染系统环境。
echo
echo ">>> [2/5] 创建 Python 虚拟环境 venv/ ..."
if [[ -d "${REPO_DIR}/venv" ]]; then
    echo "venv/ 已存在,跳过创建(如需重建请先手动删除 ${REPO_DIR}/venv)。"
else
    python3 -m venv "${REPO_DIR}/venv"
fi

# ---------------------------------------------------------------------------
# 3. 安装 Python 依赖
# ---------------------------------------------------------------------------
# 先升级 pip(ARM 上旧 pip 可能无法正确解析 wheel),再安装 requirements.txt。
# requirements.txt 已包含 spotdl;之后再把 yt-dlp 与 spotdl 升级到最新版,
# 因为它们依赖的站点经常改版,装最新版更可靠。
echo
echo ">>> [3/5] 升级 pip 并安装 Python 依赖 ..."
"${REPO_DIR}/venv/bin/pip" install --upgrade pip
"${REPO_DIR}/venv/bin/pip" install -r "${REPO_DIR}/requirements.txt"
"${REPO_DIR}/venv/bin/pip" install --upgrade yt-dlp spotdl

# spotdl 需要 Deno(一个 JS 运行时)来破解 YouTube 的下载校验,缺了它 Spotify
# 下载会失败。装到 /usr/local/bin,任何用户运行机器人时都能在 PATH 找到。
if ! command -v deno >/dev/null 2>&1; then
    echo ">>> 安装 Deno (spotdl 下载 YouTube 音频所需) ..."
    sudo apt-get install -y unzip
    curl -fsSL https://deno.land/install.sh | sudo DENO_INSTALL=/usr/local sh \
        || echo "警告:Deno 安装失败,Spotify 播放可能不可用,可稍后手动安装。"
fi

# ---------------------------------------------------------------------------
# 4. 安装后验证
# ---------------------------------------------------------------------------
# 逐项检查关键组件是否可用。任一项失败时给出提示,但不直接终止脚本,
# 方便运维人员看到全部检查结果。
echo
echo ">>> [4/5] 验证关键组件 ..."

echo "--- spotdl 版本 ---"
"${REPO_DIR}/venv/bin/python" -m spotdl --version \
    || echo "警告:spotdl 调用失败,请检查 Python 依赖是否安装成功。"

echo "--- yt-dlp 版本 ---"
# 优先使用 venv 内的 yt-dlp 可执行文件;若不存在则回退到 `python -m yt_dlp`。
if [[ -x "${REPO_DIR}/venv/bin/yt-dlp" ]]; then
    "${REPO_DIR}/venv/bin/yt-dlp" --version \
        || echo "警告:yt-dlp 调用失败。"
else
    "${REPO_DIR}/venv/bin/python" -m yt_dlp --version \
        || echo "警告:yt-dlp 调用失败。"
fi

echo "--- ffmpeg 版本 ---"
if command -v ffmpeg >/dev/null 2>&1; then
    ffmpeg -version 2>&1 | sed -n '1p'
else
    echo "警告:ffmpeg 未正确安装。"
fi

# ---------------------------------------------------------------------------
# 5. 准备配置文件
# ---------------------------------------------------------------------------
# 若仓库目录下不存在 configuration.ini,则从示例配置复制一份。
echo
echo ">>> [5/5] 准备配置文件 ..."
if [[ -f "${REPO_DIR}/configuration.ini" ]]; then
    echo "configuration.ini 已存在,保留现有配置不覆盖。"
else
    if [[ -f "${REPO_DIR}/configuration.example.ini" ]]; then
        cp "${REPO_DIR}/configuration.example.ini" "${REPO_DIR}/configuration.ini"
        echo "已从 configuration.example.ini 生成 configuration.ini。"
        echo "请编辑 ${REPO_DIR}/configuration.ini,至少填写以下内容:"
        echo "  - [server]  Mumble 服务器地址 host / port"
        echo "  - [bot]     admin(管理员用户名)"
        echo "  - [spotify] client_id / client_secret(启用 Spotify 播放时需要)"
    else
        echo "警告:未找到 configuration.example.ini,无法自动生成 configuration.ini。" >&2
    fi
fi

# ---------------------------------------------------------------------------
# 完成,输出后续步骤提示
# ---------------------------------------------------------------------------
echo
echo "=============================================================="
echo " 安装完成!后续步骤:"
echo
echo "  1. 编辑配置文件并填写 Mumble 服务器、管理员、Spotify 等信息:"
echo "       nano ${REPO_DIR}/configuration.ini"
echo
echo "  2. 手动测试启动(确认能连上 Mumble 服务器):"
echo "       ${REPO_DIR}/venv/bin/python3 mumbleBot.py --config configuration.ini"
echo
echo "  3. 设置开机自启:参考 deploy/ 目录下的 systemd 单元文件"
echo "       - botamusique.service              主服务"
echo "       - botamusique-ytdlp-update.service 每日更新 yt-dlp/spotdl"
echo "       - botamusique-ytdlp-update.timer   每日更新的定时器"
echo "     具体安装方法见各文件顶部注释。"
echo "=============================================================="
