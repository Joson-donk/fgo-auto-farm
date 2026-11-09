"""FGO 自动刷本图形启动器。"""
import os
import signal
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

import config


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
CONSOLE_PYTHON = os.path.join(os.path.dirname(sys.executable), "python.exe")


class Launcher:
    def __init__(self, root):
        self.root = root
        self.process = None
        self.close_when_stopped = False
        self.class_keys = list(config.BATTLE_CLASSES)
        self.labels_to_keys = {
            info["label"]: key for key, info in config.BATTLE_CLASSES.items()
        }

        root.title("FGO 自动刷本")
        root.geometry("430x375")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)

        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="FGO 自动刷本", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text="选择已校准的副本配置后启动", foreground="#555555").pack(anchor="w", pady=(2, 18))

        ttk.Label(frame, text="副本类型").pack(anchor="w")
        self.class_var = tk.StringVar(value=config.BATTLE_CLASSES["default"]["label"])
        self.class_box = ttk.Combobox(
            frame,
            textvariable=self.class_var,
            values=[info["label"] for info in config.BATTLE_CLASSES.values()],
            state="readonly",
        )
        self.class_box.pack(fill="x", pady=(4, 4))
        self.class_box.bind("<<ComboboxSelected>>", self.update_state)

        self.class_status = ttk.Label(frame, text="")
        self.class_status.pack(anchor="w", pady=(0, 12))

        count_row = ttk.Frame(frame)
        count_row.pack(fill="x")
        ttk.Label(count_row, text="刷本数量（0 = 无限）").pack(side="left")
        self.count_var = tk.StringVar(value="0")
        self.count_box = ttk.Spinbox(
            count_row, from_=0, to=9999, increment=1, width=8,
            textvariable=self.count_var,
        )
        self.count_box.pack(side="right")

        apple_row = ttk.Frame(frame)
        apple_row.pack(fill="x", pady=(8, 0))
        self.apple_var = tk.BooleanVar(value=bool(config.APPLE_CONFIG.get("enabled", False)))
        self.apple_check = ttk.Checkbutton(apple_row, text="AP不足自动吃苹果",
                                           variable=self.apple_var)
        self.apple_check.pack(side="left")
        self.apple_boxes = {}
        for key, label in (("gold", "金"), ("silver", "银"), ("bronze", "铜")):
            ttk.Label(apple_row, text=label).pack(side="left", padx=(10 if key != "gold" else 14, 2))
            var = tk.StringVar(value=str(config.APPLE_CONFIG.get(key, {}).get("count", 0)))
            box = ttk.Spinbox(apple_row, from_=0, to=999, increment=1, width=5,
                              textvariable=var)
            box.pack(side="left")
            self.apple_boxes[key] = (box, var)

        self.battle_only_var = tk.BooleanVar(value=False)
        self.battle_only = ttk.Checkbutton(
            frame, text="我已在战斗中（从当前战斗开始）",
            variable=self.battle_only_var,
        )
        self.battle_only.pack(anchor="w", pady=(12, 12))

        button_row = ttk.Frame(frame)
        button_row.pack(fill="x", pady=(2, 10))
        self.start_button = ttk.Button(button_row, text="▶  启动刷本", command=self.start)
        self.start_button.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=5)
        self.stop_button = ttk.Button(
            button_row, text="■  停止", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", fill="x", expand=True, padx=(6, 0), ipady=5)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(frame, textvariable=self.status_var, wraplength=380).pack(anchor="w")
        self.update_state()

    def selected_key(self):
        return self.labels_to_keys.get(self.class_var.get())

    def update_state(self, _event=None):
        key = self.selected_key()
        ready = bool(key and config.BATTLE_CLASSES[key]["ready"])
        state = "已校准，可启动" if ready else "待配置，当前不可启动"
        self.class_status.configure(text=state)
        self.start_button.configure(state="normal" if ready and self.process is None else "disabled")

    def start(self):
        key = self.selected_key()
        if not key or not config.BATTLE_CLASSES[key]["ready"]:
            return
        try:
            count = int(self.count_var.get())
            if count < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("数量无效", "刷本数量必须是大于或等于 0 的整数。")
            return

        command = [
            CONSOLE_PYTHON, os.path.join(ROOT_DIR, "main.py"),
            "--class", key, "--count", str(count),
        ]
        if self.battle_only_var.get():
            command.append("--battle-only")
        if not self.apple_var.get():
            command.append("--no-apple")
        for apple_key in ("gold", "silver", "bronze"):
            _box, var = self.apple_boxes[apple_key]
            try:
                value = int(var.get())
                if value < 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("苹果数量无效", "苹果数量必须是大于或等于 0 的整数。")
                return
            command += [f"--apple-{apple_key}", str(value)]

        try:
            flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
            self.process = subprocess.Popen(command, cwd=ROOT_DIR, creationflags=flags)
        except (OSError, AttributeError) as error:
            self.process = None
            messagebox.showerror("启动失败", str(error))
            self.update_state()
            return

        self.status_var.set("脚本运行中；日志显示在新开的终端窗口。停止后不会自动吃苹果。")
        self.stop_button.configure(state="normal")
        self.class_box.configure(state="disabled")
        self.count_box.configure(state="disabled")
        self.battle_only.configure(state="disabled")
        self.apple_check.configure(state="disabled")
        for box, _var in self.apple_boxes.values():
            box.configure(state="disabled")
        self.poll_process()

    def stop(self):
        if self.process is None or self.process.poll() is not None:
            return
        if messagebox.askyesno("停止刷本", "向脚本发送停止信号？"):
            self.status_var.set("正在停止脚本…")
            self.stop_button.configure(state="disabled")
            self.process.send_signal(signal.CTRL_BREAK_EVENT)

    def poll_process(self):
        if self.process is not None and self.process.poll() is None:
            self.root.after(500, self.poll_process)
            return

        return_code = self.process.returncode if self.process is not None else 0
        self.process = None
        self.stop_button.configure(state="disabled")
        self.class_box.configure(state="readonly")
        self.count_box.configure(state="normal")
        self.battle_only.configure(state="normal")
        self.apple_check.configure(state="normal")
        for box, _var in self.apple_boxes.values():
            box.configure(state="normal")
        self.status_var.set(f"脚本已结束（退出码 {return_code}）")
        self.update_state()
        if self.close_when_stopped:
            self.root.destroy()

    def close(self):
        if self.process is not None and self.process.poll() is None:
            should_stop = messagebox.askyesno(
                "脚本仍在运行", "退出启动器并停止当前刷本吗？")
            if not should_stop:
                return
            self.close_when_stopped = True
            self.stop_button.configure(state="disabled")
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
            return
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    Launcher(window)
    window.mainloop()