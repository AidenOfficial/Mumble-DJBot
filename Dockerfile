ARG ARCH=
# 新版 Web UI(webui/)在独立 stage 里用 node 构建,产出 webui/dist。
# 仓库里也提交了 dist,这个 stage 保证镜像总是带着与源码一致的最新构建。
FROM node:22-slim AS webui-builder
WORKDIR /webui
COPY webui/package.json webui/package-lock.json ./
RUN npm ci
COPY webui/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS python-builder
ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /botamusique

RUN apt-get update \
    && apt-get install --no-install-recommends -y gcc g++ git ca-certificates ffmpeg libjpeg-dev libmagic-dev opus-tools zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
COPY . /botamusique
RUN python3 -m venv venv \
    && venv/bin/pip install wheel \
    && venv/bin/pip install -r requirements.txt

FROM python:3.12-slim-bookworm
ENV DEBIAN_FRONTEND noninteractive
EXPOSE 8181
RUN apt update && \
    apt install --no-install-recommends -y opus-tools ffmpeg libmagic-dev curl tar unzip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Deno:spotdl 下载 YouTube 音频时用它破解 JS 校验,缺它 Spotify 播放会失败。
# 装到 /usr/local/bin,确保任何进程都能在 PATH 找到。
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

COPY --from=python-builder /botamusique /botamusique
COPY --from=webui-builder /webui/dist /botamusique/webui/dist
WORKDIR /botamusique
# 修掉可能存在的 Windows CRLF 换行(否则容器内会报 bad interpreter),再赋可执行权限。
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh

ENTRYPOINT [ "/botamusique/entrypoint.sh" ]
CMD ["venv/bin/python", "mumbleBot.py"]
