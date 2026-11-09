# -*- coding: utf-8 -*-
"""一键连通检查：找 adb、列设备、可选写入 DEVICE_SERIAL、截图验证。

不依赖 OpenCV（clone 后系统 Python 也能跑）；正式刷本请用 start.bat / .venv。

用法:
    python setup_connect.py              # 检查 + 截图，不改 config
    python setup_connect.py --write      # 仅一台在线设备时，写入 config.py 的 DEVICE_SERIAL
    python setup_connect.py --write-adb  # 同时把找到的 adb 路径写入 ADB_PATH（一般不必）

成功标准打印: 连通 OK，可双击 start.bat
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from shutil import which

ROOT = Path(__file__).resolve().parent


def _candidate_adb_paths():
    home = os.path.expanduser("~")
    common = [
        os.path.join(home, "AppData", "Local", "Android", "Sdk", "platform-tools", "adb.exe"),
        r"C:\Android\platform-tools\adb.exe",
        r"C:\Program Files\Netease\MuMu Player 12\shell\adb.exe",
        r"C:\Program Files\Netease\MuMu\shell\adb.exe",
        r"C:\Program Files (x86)\MuMu\shell\adb.exe",
        r"D:\Program Files\Netease\MuMu Player 12\shell\adb.exe",
        r"C:\leidian\LDPlayer9\adb.exe",
        r"D:\leidian\LDPlayer9\adb.exe",
        r"C:\leidian\LDPlayer14\adb.exe",
        r"D:\leidian\LDPlayer14\adb.exe",
        r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
        "/usr/bin/adb",
        "/usr/local/bin/adb",
        "/opt/homebrew/bin/adb",
        os.path.join(home, "Library", "Android", "sdk", "platform-tools", "adb"),
    ]
    return [c for c in common if os.path.exists(c)]


def _find_adb(configured: str = "") -> str:
    if configured and os.path.exists(configured):
        return configured
    env = os.environ.get("FGO_ADB", "").strip()
    if env:
        return env
    w = which("adb")
    if w:
        return w
    cands = _candidate_adb_paths()
    if cands:
        return cands[0]
    return configured or "adb"


def _run(adb: str, args, serial: str = "", timeout: int = 20, raw: bool = False):
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return (b"" if raw else ""), f"找不到 adb: {adb}", -1
    except subprocess.TimeoutExpired:
        return (b"" if raw else ""), "timeout", -1
    if raw:
        return out.stdout or b"", (out.stderr or b"").decode("utf-8", errors="replace").strip(), out.returncode
    so = (out.stdout or b"").decode("utf-8", errors="replace").strip()
    se = (out.stderr or b"").decode("utf-8", errors="replace").strip()
    return so, se, out.returncode


def _read_config_values():
    """Parse ADB_PATH / DEVICE_SERIAL without importing config (avoids cv2)."""
    text = (ROOT / "config.py").read_text(encoding="utf-8")
    adb_path = ""
    serial = ""
    # Prefer already-expanded env if set
    if os.environ.get("FGO_ADB"):
        adb_path = os.environ["FGO_ADB"]
    if os.environ.get("FGO_SERIAL"):
        serial = os.environ["FGO_SERIAL"]
    m = re.search(r'(?m)^ADB_PATH\s*=\s*os\.environ\.get\("FGO_ADB",\s*(r?)(["\'])(.*?)\2\)', text)
    if m and not adb_path:
        adb_path = m.group(3)
    m = re.search(r'(?m)^ADB_PATH\s*=\s*(r?)(["\'])(.*?)\2', text)
    if m and not adb_path and "environ" not in text[m.start():m.start()+40]:
        adb_path = m.group(3)
    # Fallback: simple get default
    m = re.search(r'(?m)^DEVICE_SERIAL\s*=\s*os\.environ\.get\("FGO_SERIAL",\s*(r?)(["\'])(.*?)\2\)', text)
    if m and not serial:
        serial = m.group(3)
    return adb_path.strip(), serial.strip()


def _list_devices(adb: str):
    out, err, rc = _run(adb, ["devices"])
    if rc != 0:
        print(f"[失败] adb devices 失败: {err or out}")
        return []
    serials = []
    for line in out.splitlines()[1:]:
        if "\tdevice" in line:
            serials.append(line.split("\t")[0].strip())
    return serials


def _patch_config(serial=None, adb_path=None):
    path = ROOT / "config.py"
    text = path.read_text(encoding="utf-8")
    changed = []
    if serial is not None:
        new_text, n = re.subn(
            r'(?m)^DEVICE_SERIAL\s*=\s*.*$',
            f'DEVICE_SERIAL = os.environ.get("FGO_SERIAL", "{serial}")  # 由 setup_connect.py 写入',
            text,
            count=1,
        )
        if n:
            text = new_text
            changed.append(f"DEVICE_SERIAL={serial}")
        else:
            print("[警告] config.py 里没找到 DEVICE_SERIAL 行，未写入。")
    if adb_path is not None:
        escaped = adb_path.replace("\\", "\\\\")
        new_text, n = re.subn(
            r'(?m)^ADB_PATH\s*=\s*.*$',
            f'ADB_PATH = os.environ.get("FGO_ADB", r"{escaped}")  # 由 setup_connect.py 写入',
            text,
            count=1,
        )
        if n:
            text = new_text
            changed.append(f"ADB_PATH={adb_path}")
        else:
            print("[警告] config.py 里没找到 ADB_PATH 行，未写入。")
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def _get_size(adb: str, serial: str):
    out, _, _ = _run(adb, ["shell", "wm", "size"], serial=serial)
    m = re.search(r"(\d+)x(\d+)", out or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _screenshot(adb: str, serial: str, dest: Path) -> bool:
    data, err, rc = _run(adb, ["exec-out", "screencap", "-p"], serial=serial, timeout=30, raw=True)
    if rc != 0 or not data:
        print(f"[失败] screencap 失败: {err or rc}")
        return False
    # Some Windows adb builds replace \n with \r\n in PNG; fix if header broken
    if data.startswith(b"\x89PNG\r\n"):
        data = data.replace(b"\r\n", b"\n")
    if not data.startswith(b"\x89PNG"):
        print("[失败] 截图数据不是 PNG，请确认模拟器在线。")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True


def main() -> int:
    p = argparse.ArgumentParser(description="FGO 公开仓 · 模拟器连通检查")
    p.add_argument("--write", action="store_true",
                   help="仅一台在线设备时，写入 config.py 的 DEVICE_SERIAL")
    p.add_argument("--write-adb", action="store_true",
                   help="把自动找到的 adb 路径写入 config.py 的 ADB_PATH")
    p.add_argument("--shot-out", default="logs/check.png", help="截图输出路径")
    args = p.parse_args()

    print("==== FGO 连通检查 ====")
    cfg_adb, cfg_serial = _read_config_values()
    adb = _find_adb(cfg_adb)
    print(f"[adb] 路径: {adb}")
    out, err, rc = _run(adb, ["version"])
    if rc != 0 or "Android Debug Bridge" not in (out or ""):
        print(f"[失败] 无法运行 adb。\n{err or out}")
        print("请安装模拟器或 Android platform-tools，或在 config.py 填写 ADB_PATH。")
        return 1
    ver = out.splitlines()[0] if out else "?"
    print(f"[adb] {ver}")

    serials = _list_devices(adb)
    if not serials:
        print("[失败] 没有在线设备。请先启动模拟器，再重跑本脚本。")
        print("提示: 雷电/MuMu 需开启 ADB；多开时记下设备号填 DEVICE_SERIAL。")
        return 1
    print("[adb] 在线设备:")
    for s in serials:
        print(f"   - {s}")

    serial = cfg_serial
    if serial and serial in serials:
        print(f"[adb] 将使用 config 中的 DEVICE_SERIAL: {serial}")
    elif len(serials) == 1:
        serial = serials[0]
        print(f"[adb] 仅一台设备，使用: {serial}")
        if args.write:
            changed = _patch_config(serial=serial)
            if changed:
                print(f"[写入] config.py ← {', '.join(changed)}")
        else:
            print(f"[提示] 可加 --write 把 DEVICE_SERIAL={serial} 写入 config.py")
    else:
        if serial and serial not in serials:
            print(f"[警告] config.DEVICE_SERIAL={serial} 不在线，改用第一台: {serials[0]}")
        serial = serials[0]
        print(f"[adb] 多设备，本次使用第一台: {serial}")
        print("      请手动改 config.py / 设 FGO_SERIAL；多设备时 --write 不会自动写入。")
        if args.write:
            print("[跳过写入] 多设备时拒绝自动写入，请手动指定。")

    if args.write_adb:
        changed = _patch_config(adb_path=adb)
        if changed:
            print(f"[写入] config.py ← {', '.join(changed)}")

    shot = ROOT / args.shot_out
    if not _screenshot(adb, serial, shot):
        return 1
    print(f"[截图] 成功: {shot}")
    w, h = _get_size(adb, serial)
    if w and h:
        print(f"[分辨率] wm size = {w}x{h}")
        if (w, h) == (1920, 1080):
            print("[分辨率] OK（1920x1080 横屏）")
        elif (w, h) == (1080, 1920):
            print("[警告] 当前是竖屏 1080x1920，请改为横屏 1920x1080。")
        else:
            print("[警告] 非 1920x1080。本仓库模板按 1920x1080 横屏校准，请改模拟器分辨率。")
    else:
        print("[警告] 未能读取 wm size，请打开截图人工确认分辨率。")

    print()
    print("连通 OK，可双击 start.bat")
    print("下一步: 游戏里停在助战选择页 → 双击 start.bat → 选「默认战斗」开刷。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
