#!/bin/bash
sed -i 's@http://archive.ubuntu.com/ubuntu@http://mirrors.aliyun.com/ubuntu@g; s@http://security.ubuntu.com/ubuntu@http://mirrors.aliyun.com/ubuntu@g' /etc/apt/sources.list
apt-get update >/tmp/upd.log 2>&1 || { tail -5 /tmp/upd.log; exit 1; }
apt-get install -y --no-install-recommends \
  ca-certificates curl xz-utils git python3 openssl binutils \
  libegl1 libgles2 libgl1-mesa-dri libglx-mesa0 mesa-utils \
  libgtk-3-0 libmpg123-0 libatomic1 libbrotli1 libbz2-1.0 libcairo2 \
  libfontconfig1 libgdk-pixbuf-2.0-0 libharfbuzz0b libpango-1.0-0 \
  libpng16-16 zlib1g libstdc++6 libx11-6 libxext6 libxkbcommon0 \
  xvfb x11-utils xauth imagemagick fonts-noto-cjk fonts-noto-color-emoji \
  pulseaudio-utils >/tmp/inst.log 2>&1
echo "exit=$?"
tail -25 /tmp/inst.log
