# -*- coding: utf-8 -*-
"""ADB 控制器：连接模拟器/真机，截图、点击、滑动。"""
import os
import re
import subprocess
import sys
import time

import cv2
import numpy as np


# 常见模拟器的 adb 路径（Windows 习惯路径）
def _candidate_adb_paths():
    cands = []
    home = os.path.expanduser("~")
    common = [
        # 安卓 SDK 平台工具
        os.path.join(home, "AppData", "Local", "Android", "Sdk", "platform-tools", "adb.exe"),
        r"C:\Android\platform-tools\adb.exe",
        # MuMu 模拟器
        r"C:\Program Files\Netease\MuMu Player 12\shell\adb.exe",
        r"C:\Program Files\Netease\MuMu\shell\adb.exe",
        r"C:\Program Files (x86)\MuMu\shell\adb.exe",
        r"D:\Program Files\Netease\MuMu Player 12\shell\adb.exe",
        # 雷电模拟器
        r"C:\leidian\LDPlayer9\adb.exe",
        r"D:\leidian\LDPlayer9\adb.exe",
        # 蓝叠
        r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
        # Linux/macOS
        "/usr/bin/adb", "/usr/local/bin/adb", "/opt/homebrew/bin/adb",
        os.path.join(home, "Library", "Android", "sdk", "platform-tools", "adb"),
    ]
    for c in common:
        if os.path.exists(c):
            cands.append(c)
    return cands


def _find_adb():
    if os.environ.get("FGO_ADB"):
        return os.environ["FGO_ADB"]
    # PATH 里找
    from shutil import which
    w = which("adb")
    if w:
        return w
    for c in _candidate_adb_paths():
        if os.path.exists(c):
            return c
    return "adb"


class Adb:
    def __init__(self, adb_path="", serial=""):
        self.adb = adb_path or _find_adb()
        self.serial = serial
        self._verify()

    def _run(self, args, timeout=20):
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += args
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=timeout)
            # 不用 text=True：中文 Windows 下 adb 输出是 GBK，text 模式解码失败会
            # 让 stdout 变成 None（表现为 AttributeError/线程异常），这里统一按字节取再容错解码
            so = (out.stdout or b"").decode("utf-8", errors="replace")
            se = (out.stderr or b"").decode("utf-8", errors="replace")
            return so.strip(), se.strip(), out.returncode
        except subprocess.TimeoutExpired:
            return "", "timeout", -1
        except FileNotFoundError:
            print(f"[adb] 找不到 adb 可执行文件: {self.adb}\n"
                  "请在 config.py 的 ADB_PATH 里填写你模拟器自带 adb 的完整路径。")
            sys.exit(1)

    def _verify(self):
        out, err, rc = self._run(["version"], timeout=10)
        if rc != 0 or "Android Debug Bridge" not in out:
            print(f"[adb] 无法运行 adb: {self.adb}\n{err}")
            sys.exit(1)
        self._pick_device()

    def _pick_device(self):
        if self.serial:
            # 校验该设备是否在线
            out, err, rc = self._run(["devices"])
            if self.serial in out:
                print(f"[adb] 使用设备: {self.serial}")
                return
            print(f"[adb] 设备 {self.serial} 不在线，尝试 adb connect ...")
            self._run(["connect", self.serial], timeout=15)
            out, _, _ = self._run(["devices"])
            if self.serial in out:
                print(f"[adb] 已连接设备: {self.serial}")
                return
            print(f"[adb] 设备 {self.serial} 仍不可用，请确认模拟器已启动。")
            sys.exit(1)

        out, _, _ = self._run(["devices"])
        serials = [l.split("\t")[0] for l in out.splitlines()[1:]
                   if "\tdevice" in l]
        if not serials:
            print("[adb] 没有检测到在线设备。请先启动模拟器，或在 config.py 的 DEVICE_SERIAL 里填写设备号。")
            sys.exit(1)
        if len(serials) > 1:
            print("[adb] 检测到多个设备，请在 config.py 的 DEVICE_SERIAL 指定，本次自动使用第一个:")
            for s in serials:
                print("   -", s)
        self.serial = serials[0]
        print(f"[adb] 使用设备: {self.serial}")

    # ------------------------------------------------------------------
    def get_size(self):
        """返回 (width, height)"""
        out, _, _ = self._run(["shell", "wm", "size"])
        m = re.search(r"(\d+)x(\d+)", out)
        if not m:
            out2, _, _ = self._run(["shell", "dumpsys", "display"])
            m = re.search(r"mCurrentBaseDisplayInfo.*?(\d+) x (\d+)", out2)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            return w, h
        return 1080, 1920

    def screenshot(self, path):
        """截图到本地 path，返回该路径；失败返回 None。"""
        data = self._capture_screenshot()
        if data is None:
            return None
        try:
            with open(path, "wb") as shot_file:
                shot_file.write(data)
        except OSError as exc:
            print(f"[adb] 无法保存截图到 {path}: {exc}")
            return None
        return path

    def read_screen(self):
        """返回 BGR numpy 数组；失败返回 None。"""
        data = self._capture_screenshot()
        if data is None:
            return None
        return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)

    def _capture_screenshot(self):
        """通过 ADB 二进制流获取 PNG，避免设备端临时文件和 adb pull。"""
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += ["exec-out", "screencap", "-p"]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            print("[adb] 截图超时。")
            return None
        except FileNotFoundError:
            print(f"[adb] 找不到 adb 可执行文件: {self.adb}\n"
                  "请在 config.py 的 ADB_PATH 里填写你模拟器自带 adb 的完整路径。")
            sys.exit(1)
        if result.returncode != 0 or not result.stdout:
            error = result.stderr.decode(errors="replace").strip()
            print(f"[adb] 截图失败: {error or 'ADB 未返回图像数据'}")
            return None
        return result.stdout

    def tap(self, x, y):
        # 点击审计：所有点击都从这一个入口发出，写 logs/debug/taps.log，
        # 便于事后定位「哪一下把令咒/面板点出来了」（用户 m05040 追问）
        try:
            import traceback
            fr = traceback.extract_stack(limit=3)[0]
            with open("logs/debug/taps.log", "a", encoding="utf-8") as fh:
                fh.write("%s %d,%d @ %s:%d\n" % (
                    time.strftime("%H:%M:%S"), int(x), int(y), fr.name, fr.lineno))
        except OSError:
            pass
        self._run(["shell", "input", "tap", str(int(x)), str(int(y))], timeout=15)
        self.last_action_ts = time.monotonic()

    def swipe(self, x1, y1, x2, y2, duration_ms=300):
        self._run(["shell", "input", "swipe", str(int(x1)), str(int(y1)),
                   str(int(x2)), str(int(y2)), str(int(duration_ms))], timeout=15)
        self.last_action_ts = time.monotonic()

    def tap_ratio(self, w, h, fx, fy, jitter=0):
        """按比例点击；jitter 为 0~1 的随机扰动，避免每次都点同一点。"""
        import random
        x = w * fx
        y = h * fy
        if jitter:
            x += random.uniform(-jitter, jitter) * w * 0.01
            y += random.uniform(-jitter, jitter) * h * 0.01
        self.tap(x, y)


class AsyncScreenshot:
    """后台截图管线：在独立线程中持续截图，主线程读取最新帧。
    减少截图与识别的串行等待，每轮循环可节省 ~200ms。"""

    def __init__(self, adb, interval=0.5):
        import threading
        self._adb = adb
        self._interval = interval
        self._frame = None
        self._frame_ts = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            data = self._adb._capture_screenshot()
            if data is not None:
                img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                with self._lock:
                    self._frame = img
                    self._frame_ts = time.monotonic()
            self._stop.wait(self._interval)

    def read(self, min_ts=0.0, timeout=2.0):
        """返回最新一帧 BGR numpy 数组；无帧返回 None。
        min_ts>0 时等待时间戳不早于 min_ts 的帧（点击后避免读到旧画面），
        超时则返回当前最新帧兜底。"""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                if self._frame is not None and self._frame_ts >= min_ts:
                    return self._frame.copy()
            if time.monotonic() >= deadline:
                with self._lock:
                    return self._frame.copy() if self._frame is not None else None
            time.sleep(0.03)

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3)


def wait(sec):
    time.sleep(sec)
