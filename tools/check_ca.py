#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_ca.py —— 离线自检：当前解释器的 OpenSSL 能否找到 CA 证书。

python.org 的 macOS 构建只认自带路径下的 cert.pem，安装后默认不存在，
所有 HTTPS 请求都会 CERTIFICATE_VERIFY_FAILED（ADR 0010 切换记录）。
gate 全离线测不到，所以 `make setup` 建完 venv 后跑这个检查。

用法：.venv/bin/python tools/check_ca.py   # 0 = 找到 CA；1 = 缺失（打印修复命令）
"""
import os
import re
import ssl
import sys


def ca_status(paths=None, environ=None):
    """返回 (cafile, ok)：环境变量覆盖优先，其次 OpenSSL 默认 cafile / capath。"""
    paths = paths or ssl.get_default_verify_paths()
    environ = os.environ if environ is None else environ
    cafile = environ.get(paths.openssl_cafile_env) or paths.openssl_cafile
    capath = environ.get(paths.openssl_capath_env) or paths.openssl_capath
    ok = bool(cafile and os.path.isfile(cafile)) or _hashed_dir(capath)
    return cafile, ok


def _hashed_dir(capath):
    """capath 只有包含 c_rehash 格式（<8 位十六进制>.<n>）的证书才算可用。"""
    if not capath or not os.path.isdir(capath):
        return False
    return any(HASHED.match(name) for name in os.listdir(capath))


HASHED = re.compile(r"^[0-9a-f]{8}\.\d+$")


def main():
    cafile, ok = ca_status()
    if ok:
        print("CA: %s ok" % cafile)
        return 0
    if not cafile:
        print("CA: MISSING —— 该构建未配置默认 cafile，HTTPS 会全部失败；请安装 certifi 并设置 SSL_CERT_FILE 指向其 cacert.pem")
        return 1
    base = getattr(sys, "_base_executable", None) or sys.executable  # 装进解释器本体，不装进 venv
    print("CA: %s MISSING —— HTTPS 会全部失败。python.org 构建请安装 certifi 并链接（ADR 0010）：" % cafile)
    print("  %s -m pip install --upgrade certifi" % base)
    print('  ln -sf "$(%s -c \'import certifi;print(certifi.where())\')" %s' % (base, cafile))
    return 1


if __name__ == "__main__":
    sys.exit(main())
