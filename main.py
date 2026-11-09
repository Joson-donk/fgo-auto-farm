# -*- coding: utf-8 -*-
"""FGO 自动刷本脚本入口。

用法:
    python main.py --count 50            # 刷 50 本
    python main.py --count 0             # 无限刷（Ctrl+C 停止）
    python main.py --class default       # 使用公开默认战斗壳
    python main.py --list-classes        # 查看已配置/待配置职阶
    python main.py --dry-run --shot      # 只截图不点击，用于校准/检查连接
    python main.py --battle-only --count 1  # 手动进入战斗后，自动打一场
    python main.py --serial 127.0.0.1:7555  # 指定设备
"""
import argparse
import sys

import config


def main():
    p = argparse.ArgumentParser(description="FGO 固定阵容自动刷本")
    p.add_argument("--class", dest="class_key", choices=config.BATTLE_CLASSES,
                   default="default", help="选择副本职阶配置（默认 default）")
    p.add_argument("--list-classes", action="store_true",
                   help="列出各职阶配置状态，不连接模拟器")
    p.add_argument("--count", type=int, default=None,
                   help="刷本次数，0=无限；默认取 config.py 的 NODE_COUNT")
    p.add_argument("--serial", type=str, default=None, help="指定设备序列号")
    p.add_argument("--dry-run", action="store_true",
                   help="只建立连接并截一张图，不执行任何点击（先跑这个确认连通）")
    p.add_argument("--preview", action="store_true",
                   help="只读识别当前画面并预览战斗决策，不执行点击")
    p.add_argument("--battle-only", action="store_true",
                   help="从当前战斗界面开始，不自动进入关卡或选择助战")
    p.add_argument("--no-apple", action="store_true",
                   help="本次运行 AP 不足时不吃苹果（覆盖 config.APPLE_CONFIG.enabled）")
    p.add_argument("--apple-gold", type=int, default=None, help="本次运行金苹果数量上限")
    p.add_argument("--apple-silver", type=int, default=None, help="本次运行银苹果数量上限")
    p.add_argument("--apple-bronze", type=int, default=None, help="本次运行铜苹果数量上限")
    p.add_argument("--shot-out", type=str, default="logs/check.png",
                   help="--dry-run 截图输出路径")
    # 单实例保护：同机只允许一个 main.py 操作模拟器，避免两个实例抢点击
    import socket as _socket
    _guard = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        _guard.bind(("127.0.0.1", 47654))
        _guard.listen(1)
    except OSError:
        print("[保护] 检测到另一个 main.py 实例正在运行，本次启动取消（避免抢控制权）。")
        return 3
    args = p.parse_args()

    # 运行日志落盘：控制台 + logs/run_<时间戳>.log 双写，便于事后排查「没打完就停」
    import os as _os
    import time as _time
    _log_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "logs")
    _os.makedirs(_log_dir, exist_ok=True)
    # 先清旧日志再开新文件：绝不碰即将创建的路径，也不删正在写的文件
    def _cleanup_old_run_logs(log_dir):
        keep_count = int(getattr(config, "LOG_KEEP_COUNT", 15) or 0)
        keep_days = int(getattr(config, "LOG_KEEP_DAYS", 7) or 0)
        if keep_count <= 0 and keep_days <= 0:
            return
        try:
            runs = [
                _os.path.join(log_dir, name)
                for name in _os.listdir(log_dir)
                if name.startswith("run_") and name.endswith(".log")
            ]
        except OSError:
            return
        runs.sort(key=lambda path: _os.path.getmtime(path), reverse=True)
        doomed = set()
        if keep_count > 0:
            doomed.update(runs[keep_count:])
        if keep_days > 0:
            cutoff = _time.time() - keep_days * 86400
            doomed.update(path for path in runs if _os.path.getmtime(path) < cutoff)
        removed = 0
        for path in doomed:
            try:
                _os.remove(path)
                removed += 1
            except OSError:
                pass
        if removed:
            print(f"[日志] 已清理旧运行日志 {removed} 份"
                  f"（保留最近 {keep_count or '不限'} 份"
                  f" / {keep_days or '不限'} 天内）")

    _cleanup_old_run_logs(_log_dir)
    _log_path = _os.path.join(_log_dir, f"run_{_time.strftime('%Y%m%d_%H%M%S')}.log")

    class _Tee:
        def __init__(self, *streams):
            self._streams = streams

        def write(self, data):
            for stream in self._streams:
                try:
                    stream.write(data)
                except Exception:
                    pass
            return len(data)

        def flush(self):
            for stream in self._streams:
                try:
                    stream.flush()
                except Exception:
                    pass

    try:
        _log_file = open(_log_path, "w", encoding="utf-8", errors="replace", buffering=1)
        sys.stdout = _Tee(sys.__stdout__, _log_file)
        sys.stderr = _Tee(sys.__stderr__, _log_file)
        print(f"[日志] 运行日志: {_log_path}")
    except OSError:
        pass

    # 苹果配置覆盖（GUI 启动器通过参数同步用户设置）
    if args.no_apple:
        config.APPLE_CONFIG["enabled"] = False
    for _key, _val in (("gold", args.apple_gold), ("silver", args.apple_silver),
                       ("bronze", args.apple_bronze)):
        if _val is not None:
            config.APPLE_CONFIG.setdefault(_key, {"count": 0, "priority": 0})["count"] = max(0, _val)

    if args.list_classes:
        for key, battle_class in config.BATTLE_CLASSES.items():
            status = "已配置" if battle_class["ready"] else "待配置"
            print(f"{key:10} {battle_class['label']} [{status}]")
        return

    selected_class = config.BATTLE_CLASSES[args.class_key]
    if not selected_class["ready"] and not args.preview:
        print(f"[配置] {selected_class['label']} 尚未校准，已停止；不会连接设备或执行点击。")
        print("[配置] 请先补齐该职阶的阵容、技能、坐标和宝具模板，再启用配置。")
        return 2
    print(f"[配置] 副本类型：{selected_class['label']}")

    from fgo_bot import FgoBot

    if args.serial:
        config.DEVICE_SERIAL = args.serial

    bot = FgoBot(node_count=args.count, class_key=args.class_key)

    if args.preview:
        bot.preview_current_screen()
        return

    if args.dry_run:
        import os
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.shot_out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if bot.adb.screenshot(out):
            print(f"[dry-run] 截图成功: {out}")
            print("  请打开该图确认是 FGO 游戏画面。之后记得先把手动停在关卡选择/准备页。")
        else:
            print("[dry-run] 截图失败，请检查 adb 连接。")
        return

    bot.run(start_in_battle=args.battle_only)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\n[退出] 用户中断（Ctrl+C）")
        raise SystemExit(130)
    except Exception:
        import traceback
        traceback.print_exc()
        print("[错误] 脚本异常退出，请查看上方日志。")
        try:
            input("按回车退出…")  # 控制台窗口停留，便于查看报错（无 tty 时自动跳过）
        except (EOFError, OSError):
            pass
        raise SystemExit(1)
