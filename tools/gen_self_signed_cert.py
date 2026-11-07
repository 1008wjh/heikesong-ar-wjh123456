"""生成一张自签 TLS 证书，给本机/局域网 Demo 用（浏览器会提示不受信任，属正常）。

    python tools/gen_self_signed_cert.py
    python tools/gen_self_signed_cert.py --out-dir certs --cn 192.168.1.20

然后：

    # PowerShell
    $env:AR_TLS_CERT="certs/cert.pem"; $env:AR_TLS_KEY="certs/key.pem"; python src/server.py
    # bash
    AR_TLS_CERT=certs/cert.pem AR_TLS_KEY=certs/key.pem python src/server.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def find_openssl():
    p = shutil.which("openssl")
    if p:
        return p
    for c in (r"D:\git\Git\usr\bin\openssl.exe",
              r"C:\Program Files\Git\usr\bin\openssl.exe",
              r"D:\Program Files\Git\usr\bin\openssl.exe"):
        if os.path.isfile(c):
            return c
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=os.path.join(ROOT, "certs"))
    parser.add_argument("--cn", default="localhost")
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()

    openssl = find_openssl()
    if not openssl:
        print("没找到 openssl。装 Git for Windows 后它自带，或改走 Nginx/Caddy 反代。")
        return 1

    out = os.path.abspath(args.out_dir)
    os.makedirs(out, exist_ok=True)
    cert = os.path.join(out, "cert.pem")
    key = os.path.join(out, "key.pem")
    cmd = [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
           "-keyout", key, "-out", cert, "-days", str(args.days),
           "-subj", f"/CN={args.cn}"]
    subprocess.run(cmd, check=True)
    print(f"证书: {cert}")
    print(f"私钥: {key}")
    print("\nPowerShell 启动：")
    print(f'  $env:AR_TLS_CERT="{cert}"; $env:AR_TLS_KEY="{key}"; python src/server.py')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
