# -*- coding: utf-8 -*-
"""
FGO 固定阵容自动刷本 —— 横屏状态机主逻辑（雷电模拟器 1920x1080）

一整局流程：
  关卡确认(任务开始) -> 助战(助战编队确认) -> 队伍确认(开始任务) -> 战斗
    战斗每回合：(首回合)开技能 -> 点攻击 -> 有宝具先放宝具 -> 按连携优先级点手牌
  战斗结束 -> 羁绊/经验(点画面) -> 战利品(下一步) -> 连续出击弹窗 -> 连续出击(循环)

界面判断全部用模板匹配（模板从真实刷本录屏截取），点击用固定坐标。
模板没匹配到不会乱点，会等待并最终超时截图停止，避免误触。
"""
import os
import random
import time

import cv2

import config
from adb import Adb, AsyncScreenshot
from vision import Vision
from card_recognition import CardRecognizer, Card
from decision import choose_cards, describe_hand, skills_to_check
from support_select import (
    SearchProgress,
    advance_after_click,
    advance_scan,
    describe_rank,
    evaluate_support_screen,
    gates_from_config,
    policy_from_config,
    click_points,
    list_moved,
    refresh_dialog_open,
    same_card,
    to_gray,
)

def _ts_print(*args, **kwargs):
    print(f"[{time.strftime('%H:%M:%S')}]", *args, **kwargs)


logger = _ts_print


class EmulatorDown(RuntimeError):
    """模拟器连续无响应（截图全超时/失败）时抛出，用于优雅停机。"""

# 各界面模板：(模板文件, 阈值, 界面标识)
UI_TEMPLATES = {
    "attack":      ("battle_attack.png", None),
    "skill_ok":    ("skill_confirm.png", None),
    "target_dlg":  ("target_dialog.png", None),
    "np":          ("np_card.png", "np_thr"),
    "loot":        ("result_next.png", None),
    "rerun":       ("rerun.png", None),
    "quest":       ("quest_start.png", None),
    "support":     ("support_confirm.png", None),
    "party":       ("party_start.png", None),
}


def _abs(w, h, fx, fy):
    return w * fx, h * fy


class FgoBot:
    def __init__(self, node_count=None, class_key="default"):
        self.cfg = config
        self.class_key = class_key
        self.class_profile = self.cfg.BATTLE_CLASS_PROFILES[class_key]
        if node_count is not None:
            self.cfg.NODE_COUNT = node_count
        self.adb = Adb(self.cfg.ADB_PATH, self.cfg.DEVICE_SERIAL)
        self.w, self.h = self.adb.get_size()
        logger(f"[启动] 屏幕分辨率: {self.w} x {self.h}")
        self.vision = Vision(self.cfg.TEMPLATE_DIR, self.cfg.TEMPLATE_THRESHOLD)
        self.card_rec = CardRecognizer(self.cfg, self.vision)
        stpls = self.class_profile.get("servant_templates")
        if stpls:
            self._load_servant_templates(stpls)
        self._debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "logs", "debug")
        os.makedirs(self._debug_dir, exist_ok=True)
        self._debug_shot_saved_at = {}
        self.count = 0
        self.turn = 0
        self._battle_start_ts = None
        self._wave = None  # 当前波次（HUD「场次 x/3」），None=未识别
        self._wave_tpl_cache = {}
        self._used_skill_keys = set()
        self._skill_base_brightness = {}  # 每技能可用亮度基线（CD 判定用差值）
        self._skills_need_target = set()  # 已实测会弹「请选择对象」的技能（never 模式直接跳过）
        self._slot_faces = None
        self._continues_used = 0
        self._np_slot_memory = {}
        # 苹果消耗计数
        self._apples_used = {"gold": 0, "silver": 0, "bronze": 0}
        # 网络错误重试计数
        self._network_retries = 0
        # 连续截图失败计数（模拟器卡死/崩溃保护）
        self._shot_fails = 0
        # 异步截图管线（可选）
        self._async_shot = None
        if getattr(self.cfg, "ASYNC_SCREENSHOT", False):
            interval = getattr(self.cfg, "ASYNC_SCREENSHOT_INTERVAL", 0.5)
            self._async_shot = AsyncScreenshot(self.adb, interval=interval)
            logger(f"[性能] 异步截图已启用 (间隔={interval}s)")

    # ------------------------------------------------------------------
    # 基础操作
    def _tap(self, key, allow_cmd_zone=False):
        # 统一走 _tap_ratio：这样 POS 名的点击也受「令咒热区」保护（用户 m05040：
        # 之前只有 _tap_ratio 有保护，_tap 直接 adb.tap 绕过了它）
        fx, fy = self.cfg.POS[key]
        return self._tap_ratio(fx, fy, allow_cmd_zone=allow_cmd_zone)

    # 右上角令咒图标热区（实测中心 1560,140，半径约 80px）：任何点击落到这里都可能
    # 弹出令咒菜单，必须显式确认当前就是要关的覆盖层才允许（用户 m04892）
    _CS_ZONE = (0.74, 0.03, 0.885, 0.24)

    def _tap_ratio(self, fx, fy, allow_cmd_zone=False):
        z = self._CS_ZONE
        if (not allow_cmd_zone and z[0] <= fx <= z[2] and z[1] <= fy <= z[3]):
            logger(f"[保护] 点击点 ({fx:.3f},{fy:.3f}) 落在令咒热区，已拦下（防误开令咒）")
            return False
        self.adb.tap(self.w * fx, self.h * fy)
        return True

    def _shot(self, tag=""):
        # 优先使用异步截图管线（如已启用）；点击后自动等待新帧，避免旧画面误判
        if self._async_shot is not None:
            img = self._async_shot.read(
                min_ts=getattr(self.adb, "last_action_ts", 0.0))
        else:
            img = self.adb.read_screen()
        # 模拟器卡死/崩溃保护：连续多次拿不到画面就别再空转，直接停
        if img is None:
            self._shot_fails += 1
            limit = int(getattr(self.cfg, "SCREENSHOT_FAIL_LIMIT", 5))
            if self._shot_fails >= limit:
                logger(f"[异常] 连续 {self._shot_fails} 次截图失败/超时 —— "
                       "模拟器可能已卡死或崩溃，脚本停止（不再空转等待）")
                raise EmulatorDown(f"连续 {self._shot_fails} 次截图失败")
        else:
            self._shot_fails = 0
        now = time.monotonic()
        if (img is not None and tag and self.cfg.SAVE_DEBUG_SHOT
                and now - self._debug_shot_saved_at.get(tag, float("-inf"))
                >= self.cfg.DEBUG_SHOT_MIN_INTERVAL):
            p = os.path.join(self._debug_dir, f"{time.time_ns()}_{tag}.png")
            if cv2.imwrite(p, img):
                self._debug_shot_saved_at[tag] = now
                self._prune_debug_shots()
            else:
                logger(f"[截图] 无法保存调试截图: {p}")
        return img

    def _prune_debug_shots(self):
        max_files = self.cfg.DEBUG_SHOT_MAX_FILES
        shots = sorted(
            (os.path.join(self._debug_dir, name)
             for name in os.listdir(self._debug_dir)
             if name.endswith(".png") and name.split("_", 1)[0].isdigit()),
            key=os.path.getmtime,
            reverse=True)
        for path in shots[max_files:]:
            try:
                os.remove(path)
            except OSError as exc:
                logger(f"[截图] 无法清理旧调试截图 {path}: {exc}")

    # 模板文件名 → UI 标识的反向映射（用于自动查找搜索区域）
    _TPL_TO_UI = {
        "battle_attack.png": "attack",
        "skill_confirm.png": "skill_ok",
        "target_dialog.png": "target_dlg",
        "result_next.png": "loot",
        "rerun.png": "rerun",
        "quest_start.png": "quest",
        "support_confirm.png": "support",
        "party_start.png": "party",
    }

    def _find(self, screen, tpl, thr=None):
        """返回模板中心的比例坐标 (fx, fy)，否则 None。
        自动使用 UI_SEARCH_REGIONS 中的区域限定以提速。"""
        if screen is None or self.vision is None:
            return None
        if thr == "np_thr":
            t = self.cfg.NP_TEMPLATE_THRESHOLD
        elif isinstance(thr, (int, float)):
            t = float(thr)
        else:
            t = self.cfg.TEMPLATE_THRESHOLD
        # 自动查找搜索区域
        region = None
        regions = getattr(self.cfg, "UI_SEARCH_REGIONS", {})
        ui_key = self._TPL_TO_UI.get(tpl)
        if ui_key and ui_key in regions:
            region = regions[ui_key]
        elif thr == "np_thr" and "np" in regions:
            region = regions["np"]
        loc = self.vision.find(screen, tpl, threshold=t, region=region)
        if loc is None and region is not None:
            # 区域未命中时回退全屏搜索，防止区域配置偏差导致漏检
            loc = self.vision.find(screen, tpl, threshold=t)
        if loc is None:
            return None
        return loc[0] / self.w, loc[1] / self.h

    def _detect(self, screen):
        """识别当前界面，返回 (界面标识, 比例坐标或None)；一次只返回一个。"""
        # 战斗/弹窗优先，结算/进本次之；support 放最后：战斗名牌/队伍页名字文本
        # 都会命中助战名字模板，必须让 attack/party 先认领界面（run22 第二轮教训）
        order = ["skill_ok", "target_dlg", "rerun", "loot",
             "party", "quest", "attack", "support"]
        for name in order:
            tpl, thr = UI_TEMPLATES[name]
            if name == "np":
                tpl = self.class_profile["np_template"]
            loc = self._find(screen, tpl, thr)
            if loc is not None:
                return name, loc
        # 助战页的行卡片会被手牌识别误读成 ≥4 张有色卡（run26 教训：
        # enter_quest 把助战页误判成选卡视图，battle 静默空转 7 分钟）。
        # 助战页「助战编队确认」按钮是最稳的页面特征（分离度 1.0 vs 0.35）。
        if self._find(screen, "support_page_btn.png", 0.8) is not None:
            return "support", None
        # 结果经验页（「获得经验值」，需点击屏幕继续）——不识别会一直「未识别」到超时
        if self._find(screen, "result_exp.png", 0.90) is not None:
            return "result_exp", None
        # 令咒（Command Spell）菜单：误点右上角令咒图标会弹出，覆盖后攻击键不可见
        if self._find(screen, "command_spell.png", 0.85) is not None:
            return "command_spell", None
        # 从者详情/宝具详情面板（用户说的「2/3 号位状态栏」）：全屏覆盖层，
        # 攻击键可能仍然可见，必须最先判定，否则会被当成技能栏继续点
        if self._find(screen, "servant_panel.png", 0.85) is not None:
            return "servant_panel", None
        # 行动力不足 / 果实交换弹窗：连续出击打空 AP 时会弹（run-AP38 卡死 50s）
        if self._find(screen, "ap_short.png", 0.85) is not None:
            return "ap_short", None
        # 苹果/行动力恢复列表已展开（手工留下或上轮中断）
        if self._find(screen, "apple_list.png", 0.8) is not None:
            return "apple_list", None
        # 事件地图：点横幅直接进助战页。战斗/选卡/结算帧分离度 0.23-0.25，
        # 用于「上一局点关闭后回到地图」时自动重新进本（run38 卡死在地图）
        if self._find(screen, "event_banner.png", 0.8) is not None:
            return "map", None
        tpl_s, thr_s = UI_TEMPLATES["support"]
        loc_s = self._find(screen, tpl_s, thr_s)
        if loc_s is not None:
            return "support", loc_s
        if sum(card.color != "?" for card in self.card_rec.recognize_hand(screen)) >= 4:
            # 强特征：选卡视图右下角的蓝色「◀返回」按钮（分离度 1.00 vs 其它界面 0.51-0.60）。
            # 战斗加载/波次登场动画会读出假手牌（甚至全 A），点卡位会点到从者精灵开详情页
            if self._find(screen, "select_back.png", 0.85) is not None:
                return "card_select", None
            return "unknown", None
        np_templates = self.class_profile.get("np_templates")
        if np_templates:
            for entry in reversed(np_templates):
                tpl = entry.get("template")
                if not tpl:
                    continue  # 位置式条目（无模板）不参与界面检测
                loc = self._find(screen, tpl, "np_thr")
                if loc is not None:
                    return "np", loc
        else:
            np_single = self.class_profile.get("np_template")
            if np_single:
                loc = self._find(screen, np_single, "np_thr")
                if loc is not None:
                    return "np", loc
        return "unknown", None

    # ------------------------------------------------------------------
    # 异常检测与恢复
    # ------------------------------------------------------------------
    def _detect_anomaly(self, screen):
        """检测异常状态，返回 (异常类型, 描述) 或 (None, None)。
        优先级：维护 > 网络错误 > AP不足 > 战斗失败 > 好友点满。"""
        if screen is None:
            return None, None
        # 这些模板需要用户用 calibrate.py --crop 截取后放入 templates/
        # 未截取的模板会自动跳过（find 返回 None）
        checks = [
            ("ap_short", "ap_short.png", "行动力不足（果实交换窗）"),
            ("maintenance", "maintenance.png", "检测到维护公告"),
            ("network_error", "network_error.png", "检测到网络错误"),
            ("event_quota", "event_quota.png", "活动次数/风暴罐不足"),
            ("ap_empty", "ap_empty.png", "AP 不足"),
            ("battle_lost", "battle_lost.png", "战斗失败"),
            ("np_detail", "servant_panel.png", "误开宝具详情覆盖层"),
            ("friend_point_full", "friend_point_full.png", "友情点已满"),
        ]
        for kind, tpl, desc in checks:
            if self._find(screen, tpl) is not None:
                return kind, desc
        # 已停在苹果列表（AP不足框被点过回复，或脚本重启）也按吃苹果处理
        if self._apple_list_open(screen):
            return "ap_empty", "AP 不足（苹果列表）"
        # 已停在消耗确认窗（脚本重启/上轮遗留）同样按吃苹果处理
        if self._apple_confirm_dialog_open(screen):
            return "ap_empty", "AP 不足（消耗确认窗）"
        return None, None

    def _handle_ap_empty(self):
        """AP 不足时按配置吃苹果；返回 True=已吃并继续，False=不吃/已耗尽应停止。"""
        apple_cfg = getattr(self.cfg, "APPLE_CONFIG", {})
        if not apple_cfg.get("enabled", False):
            logger("[苹果] AP 不足且苹果功能未启用，停止刷本")
            return False

        kinds = sorted(
            [(k, v) for k, v in apple_cfg.items() if k != "enabled"],
            key=lambda kv: kv[1].get("priority", 0), reverse=True)

        if not self._open_apple_list():
            logger("[苹果] 未能打开苹果列表，停止本次回复")
            return False

        gray_skipped = []
        confirm_failed = []
        budget_kinds = []
        attempted = False

        for kind, info in kinds:
            limit = info.get("count", 0)
            used = self._apples_used.get(kind, 0)
            if used >= limit:
                if limit > 0:
                    budget_kinds.append(kind)
                continue
            pos = self.cfg.APPLE_POSITIONS.get(kind)
            if pos is None:
                logger(f"[苹果] {kind} 坐标未配置，跳过")
                continue
            attempted = True
            # 不再用饱和度置灰跳过：银苹果天然低饱和，误判会跳过有货白银
            sat = self._apple_row_saturation(pos)
            logger(f"[苹果] 吃{kind}苹果 ({used + 1}/{limit}) sat={sat:.1f}")
            if not self._select_and_confirm_apple(kind, pos):
                logger(f"[苹果] {kind} 未出确认窗或决定未生效，尝试下一种")
                confirm_failed.append(kind)
                if not self._open_apple_list():
                    break
                continue
            self._apples_used[kind] = used + 1
            self._network_retries = 0
            return True

        if not attempted and budget_kinds:
            logger("[苹果] 配置预算已用尽(%s)，停止刷本" % ",".join(budget_kinds))
        else:
            parts = []
            if gray_skipped:
                parts.append("置灰/无库存:%s" % ",".join(gray_skipped))
            if confirm_failed:
                parts.append("坐标未出确认:%s" % ",".join(confirm_failed))
            detail = "；".join(parts) if parts else "无可用苹果"
            logger("[苹果] 停止刷本（%s）" % detail)
        return False

    def _open_apple_list(self):
        """从 AP 不足框点「回复」并等到苹果列表出现；已在列表/确认窗则不再点回复。"""
        if self._apple_confirm_dialog_open():
            logger("[苹果] 已在消耗确认窗（行已点过），直接进入确认步骤")
            return True
        if self._apple_list_open():
            logger("[苹果] 已在苹果列表")
            return True
        recover = getattr(self.cfg, "AP_RECOVER_BUTTON", None)
        if recover is None:
            return False
        self._tap_ratio(recover[0], recover[1])
        for _ in range(14):
            time.sleep(0.35)
            if self._apple_list_open():
                logger("[苹果] 已打开苹果列表")
                return True
        # 再点一次回复（有时第一次被吞）
        self._tap_ratio(recover[0], recover[1])
        for _ in range(10):
            time.sleep(0.35)
            if self._apple_list_open():
                logger("[苹果] 已打开苹果列表")
                return True
        logger("[苹果] 等待苹果列表超时")
        return False

    def _confirm_buttons_present(self, screen):
        """底栏 (0.352/0.648, 0.808) 两枚大白钮＝消耗确认窗特征。"""
        h, w = screen.shape[:2]
        ok = 0
        for fx in (0.352, 0.648):
            cx, cy = int(w * fx), int(h * 0.808)
            patch = screen[max(0, cy - 18):min(h, cy + 18),
                           max(0, cx - 50):min(w, cx + 50)]
            if patch.size == 0:
                continue
            frac = float(((patch[:, :, 0] > 200) &
                          (patch[:, :, 1] > 200) &
                          (patch[:, :, 2] > 200)).mean())
            if frac >= 0.25:
                ok += 1
        return ok >= 2

    def _apple_list_open(self, screen=None):
        """苹果列表：标题栏模板命中且底栏不是确认双白钮；否则亮带启发式兜底。"""
        if screen is None:
            screen = self.adb.read_screen()
        if screen is None:
            return False
        # 模板优先（启发式亮带在银苹果置灰/行距变化时会漏判，run22 首局教训）；
        # 确认窗背后也透出同一标题栏，靠双白钮区分（run22 第二局教训）
        if self._find(screen, "apple_list.png", 0.8) is not None:
            return not self._confirm_buttons_present(screen)
        # 队伍确认/助战/战斗界面也会有亮带，绝不能当成苹果列表
        if self._template_score(screen, "party_start.png") >= 0.75:
            return False
        if self._template_score(screen, "support_confirm.png") >= 0.75:
            return False
        if self._template_score(screen, "battle_attack.png") >= 0.75:
            return False
        h, w = screen.shape[:2]
        bright = 0
        for yf in (0.40, 0.52, 0.62):
            y = int(h * yf)
            strip = screen[max(0, y - 8):min(h, y + 8), int(w * 0.25):int(w * 0.75)]
            if strip.size and float(strip.mean()) > 170:
                bright += 1
        if bright < 2:
            return False
        # 确认窗底栏左右大白钮；列表页没有，避免把确认窗当列表
        white_btns = 0
        for fx in (0.352, 0.648):
            cx, cy = int(w * fx), int(h * 0.808)
            patch = screen[max(0, cy - 18):min(h, cy + 18),
                           max(0, cx - 50):min(w, cx + 50)]
            if patch.size == 0:
                continue
            frac = float(((patch[:, :, 0] > 200) &
                          (patch[:, :, 1] > 200) &
                          (patch[:, :, 2] > 200)).mean())
            if frac >= 0.25:
                white_btns += 1
        return white_btns < 2

    def _apple_confirm_dialog_open(self, screen=None):
        """消耗确认弹窗：底栏双白钮 + 中部偏暗为主判定。

        银确认窗会挡住 apple_list 标题栏（模板分可掉到 ~0.27），不能再硬依赖
        apple_list>=0.8；金确认窗仍可能透出标题。列表页无双白钮。连续出击等
        其它双钮窗靠中部亮度/其它界面模板互斥。
        """
        if screen is None:
            screen = self.adb.read_screen()
        if screen is None:
            return False
        if not self._confirm_buttons_present(screen):
            return False
        # 已在其它明确界面时，不要当成苹果确认窗
        if self._template_score(screen, "party_start.png") >= 0.75:
            return False
        if self._template_score(screen, "support_confirm.png") >= 0.75:
            return False
        if self._template_score(screen, "battle_attack.png") >= 0.75:
            return False
        if self._template_score(screen, "rerun.png") >= 0.75:
            return False
        h, w = screen.shape[:2]
        mid = screen[int(h * 0.42):int(h * 0.58), int(w * 0.30):int(w * 0.70)]
        if mid.size == 0:
            return False
        mid_mean = float(mid.mean())
        # 银/金确认窗中部偏暗；列表页中部偏亮（列表无双白钮，本函数前面已要求双白钮）
        if mid_mean >= 160:
            return False
        # 双白钮 + 暗中部即认。银确认窗常把 apple_list 标题挡住到 ~0.27，
        # 故不再用标题模板否决；金确认窗仍可能透出高分标题，但不作硬依赖。
        return True

    def _apple_row_saturation(self, pos, screen=None):
        """苹果行中部 HSV 饱和度（仅诊断日志用）。

        注意：白银果实天然低饱和（sat≈20–25），不能用 sat 阈值判断库存/置灰。
        """
        if screen is None:
            screen = self.adb.read_screen()
        if screen is None or pos is None:
            return -1.0
        h, w = screen.shape[:2]
        y = int(h * float(pos[1]))
        patch = screen[max(0, y - 20):min(h, y + 20), int(w * 0.35):int(w * 0.65)]
        if patch.size == 0:
            return -1.0
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        return float(hsv[:, :, 1].mean())

    def _apple_row_available(self, pos, screen=None):
        """行是否可点。

        暂无可靠置灰特征：银苹果中部饱和度天然偏低，sat>=35 会误判有货银为置灰。
        一律允许点击，由点后是否出确认窗判定成败。
        """
        return pos is not None

    def _select_and_confirm_apple(self, kind, pos):
        """点苹果行 → 等到确认窗 → 再点决定。确认前绝不点，避免点到青铜树苗。"""
        confirm = getattr(self.cfg, "APPLE_CONFIRM", (0.648, 0.808))
        for attempt in range(2):
            if not self._apple_list_open():
                if not self._open_apple_list():
                    return False
            self._tap_ratio(pos[0], pos[1])
            opened = False
            for _ in range(14):
                time.sleep(0.3)
                if self._apple_confirm_dialog_open():
                    opened = True
                    break
                # 还在列表说明没点上，不要去点确认
                if self._apple_list_open():
                    continue
            if not opened:
                logger(f"[苹果] {kind} 第{attempt + 1}次未出确认窗，不点决定")
                # 若已不在列表也不在确认，按返回回到可恢复状态
                if not self._apple_list_open():
                    self.adb._run(["shell", "input", "keyevent", "4"])
                    time.sleep(0.6)
                continue
            self._tap_ratio(confirm[0], confirm[1])
            time.sleep(1.8)
            # 确认窗应消失；若还在，补点一次
            if self._apple_confirm_dialog_open():
                logger("[苹果] 确认窗仍在，补点决定")
                self._tap_ratio(confirm[0], confirm[1])
                time.sleep(1.5)
            if self._apple_confirm_dialog_open():
                logger(f"[苹果] {kind} 点决定后确认窗仍在，视为失败")
                return False
            logger(f"[苹果] {kind} 已确认消耗")
            return True
        return False


    def _handle_network_error(self):
        """网络错误重试；返回 True=已重试应继续等待，False=超过上限应停止。"""
        max_retry = getattr(self.cfg, "NETWORK_RETRY_MAX", 3)
        wait = getattr(self.cfg, "NETWORK_RETRY_WAIT", 5.0)
        self._network_retries += 1
        if self._network_retries > max_retry:
            logger(f"[网络] 重试 {max_retry} 次仍失败，停止")
            return False
        logger(f"[网络] 第 {self._network_retries}/{max_retry} 次重试，等待 {wait}s")
        time.sleep(wait)
        # 点屏幕中央尝试关闭弹窗 / 触发重试
        self._tap("result_tap")
        return True

    def _handle_battle_lost(self):
        """战斗失败处理；返回 True=重试/续关继续，False=停止。"""
        action = getattr(self.cfg, "BATTLE_LOST_ACTION", "stop")
        if action == "retry":
            logger("[战斗] 败北，按配置重新进本")
            self._tap("result_tap")
            time.sleep(1.0)
            return True
        if action in ("continue_command", "continue_quartz"):
            max_cont = getattr(self.cfg, "BATTLE_LOST_CONTINUE_MAX", 1)
            if self._continues_used >= max_cont:
                logger(f"[战斗] 败北，本局已续关{self._continues_used}次达上限，停止")
                return False
            label = "令咒" if action == "continue_command" else "圣晶石"
            logger(f"[战斗] 败北，按配置使用{label}续关")
            self._tap("continue_command" if action == "continue_command" else "continue_quartz")
            time.sleep(1.2)
            self._tap("continue_confirm")
            time.sleep(2.5)
            self._continues_used += 1
            # 续关后复活/在场成员技能状态全部重置，清空使用记录与头像基准
            self._used_skill_keys.clear()
            self._skill_base_brightness = {}
            self._slot_faces = None
            return True
        logger("[战斗] 败北，按配置停止")
        return False


    def _load_servant_templates(self, names):
        """按职阶配置重载 servant_1/2/3 对应模板，供指令卡归属识别。"""
        rec = getattr(self, "card_rec", None)
        if rec is None:
            return
        loaded = {}
        for i, name in enumerate(names[:3]):
            tpl = self.vision.load_template(name) if hasattr(self, "vision") else None
            if tpl is None:
                # fallback via cv2
                import os
                path = os.path.join(self.cfg.TEMPLATE_DIR, name)
                if os.path.exists(path):
                    import cv2
                    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                    if img is not None:
                        loaded[i] = img
                continue
            if len(tpl.shape) == 3:
                import cv2
                tpl = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
            loaded[i] = tpl
        if loaded:
            rec._servant_tpl = loaded
            logger(f"[识别] 已加载职阶从者模板 {list(loaded.keys())}")

    def _check_slot_swap(self, screen):
        """后备支援：比较本回合与上回合前排三头像，某槽头像变化（阵亡换入/续关复活）
        时清空已用技能记录，让新成员的技能得以释放；CD 检测开启时存活成员的
        CD 中技能图标偏暗会被亮度检查挡住，不会误放。"""
        rois = getattr(self.cfg, "SLOT_FACE_ROIS", None)
        if not rois:
            return
        cur = []
        for (rx, ry, rw, rh) in rois:
            x, y = int(rx * self.w), int(ry * self.h)
            cur.append(screen[y:y + int(rh * self.h), x:x + int(rw * self.w)])
        prev = self._slot_faces
        self._slot_faces = cur
        if not prev:
            return
        thr = getattr(self.cfg, "SLOT_SWAP_THRESHOLD", 0.8)
        for i, (a, b) in enumerate(zip(prev, cur)):
            if a.shape != b.shape or a.size == 0:
                continue
            score = float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0][0])
            if score < thr:
                logger(f"[战斗] 槽{i + 1}头像变化(相似度{score:.2f})，疑似阵亡换入/复活，清空已用技能记录")
                self._used_skill_keys.clear()
                self._skill_base_brightness = {}
                break

    def _handle_maintenance(self):
        """检测到维护公告。"""
        if getattr(self.cfg, "MAINTENANCE_STOP", True):
            logger("[维护] 检测到维护公告，立即停止")
            return False
        logger("[维护] 检测到维护公告，等待 60s 后重试")
        time.sleep(60)
        self._tap("result_tap")
        return True

    def _process_anomaly(self, screen):
        """统一异常处理入口；返回 True=已处理应继续循环，False=应停止。"""
        kind, desc = self._detect_anomaly(screen)
        if kind is None:
            return True  # 无异常
        logger(f"[异常] {desc}")
        if kind == "maintenance":
            return self._handle_maintenance()
        if kind == "network_error":
            return self._handle_network_error()
        if kind == "ap_empty":
            return self._handle_ap_empty()
        if kind == "battle_lost":
            return self._handle_battle_lost()
        if kind == "friend_point_full":
            logger("[异常] 友情点已满，自动关闭弹窗")
            self._tap("result_tap")
            time.sleep(0.8)
            return True
        if kind == "np_detail":
            self._close_servant_panel(screen)
            return True
        if kind == "ap_short":
            logger("[异常] 行动力不足弹窗 → 点「取消」后走苹果恢复")
            self._tap_ratio(0.370, 0.826)
            time.sleep(1.2)
            return self._handle_ap_empty()
        if kind == "event_quota":
            logger("[异常] 活动次数/风暴罐不足，关闭弹窗并停止刷本")
            close = getattr(self.cfg, "POS", {}).get("event_quota_close")
            if close:
                self._tap_ratio(close[0], close[1])
                time.sleep(1.0)
            return False
        return True

    def preview_current_screen(self):
        """Print read-only battle diagnostics and the current card decision."""
        screen = self.adb.read_screen()
        if screen is None:
            raise RuntimeError("无法读取模拟器画面，预览中止")

        name, _ = self._detect(screen)
        print(f"[预览] 当前界面: {name}")
        if name == "card_select":
            hand = self.card_rec.recognize_hand(screen)
            print(f"[预览] 手牌: {describe_hand(hand)}")
            np_loc, np_color, np_servant = self._recognize_np_on_screen(screen)
            np_cards = ([Card(-1, np_color, np_servant)]
                        if np_loc is not None else [])
            slots, _, desc = choose_cards(
                hand, 3 - len(np_cards), self.cfg, np_cards,
                preferred_servant=self.class_profile.get("preferred_servant"))
            print(f"[预览] 宝具识别: {'有' if np_cards else '无/模板未识别'}")
            print(f"[预览] 选卡建议: {desc}; slots={slots}")
        elif name == "attack":
            print("[预览] 技能可用状态尚无可靠图像校准，本次不会尝试点击技能")
        else:
            print("[预览] 当前画面无可生成的技能/选卡建议；本次不会点击")

        missing = []
        for entry in self.class_profile.get("np_templates", []):
            template = entry.get("template")
            if not template:
                continue  # 位置式条目无需模板文件
            if not os.path.isfile(os.path.join(self.cfg.TEMPLATE_DIR, template)):
                missing.append(template)
        support_template = self.class_profile.get("support_template")
        if support_template and not os.path.isfile(
                os.path.join(self.cfg.TEMPLATE_DIR, support_template)):
            missing.append(support_template)
        if missing:
            print(f"[预览] 缺少模板: {', '.join(missing)}")
        print("[预览] 只读模式结束，未执行点击")

    def _wait_any(self, want, timeout, gap=0.5, advance=False):
        """轮询直到出现 want 中的某界面。
        want: 界面标识列表；advance: 等待期间是否定期点屏幕中央（加速/推进结算）。
        返回 (命中的标识, screen, loc)；超时返回 (None, screen, None)。"""
        deadline = time.time() + timeout
        last_tap = 0.0
        screen = None
        while time.time() < deadline:
            screen = self._shot()
            if screen is not None:
                name, loc = self._detect(screen)
                if name in want:
                    return name, screen, loc
                if advance and time.time() - last_tap > 2.0:
                    self._tap("result_tap")
                    last_tap = time.time()
            time.sleep(gap + random.uniform(0, 0.3))
        return None, screen, None

    # ------------------------------------------------------------------
    # 进本：从当前界面（关卡确认/助战/队伍/已在战斗）一路推进到战斗
    def enter_quest(self, require_support=False):
        """推进到战斗。

        require_support=True：刚点过「连续出击」时使用。过场里攻击键/选卡模板
        容易误匹配，在真正选过助战（或校验过队伍助战立绘）之前，不得把
        attack/card_select/np 当成已进战斗，否则会跳过助战页带着默认人开打。
        """
        logger("[进本] 推进到战斗（任务开始/助战/队伍确认）…")
        if require_support:
            logger("[进本] 本局要求先选助战（连续出击后），忽略进战误判")
        want = ["attack", "quest", "support", "party", "rerun", "loot",
                "card_select", "np", "map", "ap_short", "apple_list",
                "result_exp", "skill_ok", "target_dlg"]
        deadline = time.time() + self.cfg.UI_TIMEOUT
        last_unknown_log = 0.0
        unknown_streak = 0
        # 未要求选助战时视为已满足；要求时要等 _select_support 或队伍校验通过
        support_done = not require_support
        while time.time() < deadline:
            screen = self._shot()
            if screen is None:
                time.sleep(self.cfg.STATE_POLL)
                continue
            # 先处理 AP 不足等异常，避免被 _wait_any 整段超时挡住
            kind, _desc = self._detect_anomaly(screen)
            if kind is not None:
                if not self._process_anomaly(screen):
                    return False
                deadline = time.time() + self.cfg.UI_TIMEOUT
                time.sleep(1.0)
                continue
            name, loc = self._detect(screen)
            if name not in want:
                unknown_streak += 1
                if unknown_streak == 6:
                    # 连续多帧未识别：可能是遗留选卡视图（宝具放大态模板全部失配），
                    # 点右下角返回箭头退回技能栏。但战斗开场/波次登场动画也会读到假手牌，
                    # 那种情况下点 card_back 会误触「攻击」抢跑（run39 教训）→ 必须确认是真选卡视图
                    # 连续出击后尚未选助战时绝不能点 card_back，避免误触攻击抢跑
                    colored = sum(c.color != "?" for c in
                                  self.card_rec.recognize_hand(screen))
                    if colored >= 3 and self._frame_static(screen) and support_done:
                        logger("[进本] 连续6帧未识别且确认是选卡视图，点返回箭头退出遗留界面")
                        self._tap("card_back")
                        time.sleep(1.2)
                        deadline = time.time() + self.cfg.UI_TIMEOUT
                    else:
                        logger("[进本] 连续6帧未识别（非选卡视图，可能是战斗开场动画），继续等待")
                        deadline = time.time() + self.cfg.UI_TIMEOUT
                now = time.time()
                if now - last_unknown_log > 5.0:
                    logger("[进本] 未识别到可操作界面，截图等待…")
                    self._shot("enter_unknown")
                    last_unknown_log = now
                time.sleep(self.cfg.STATE_POLL)
                continue
            unknown_streak = 0
            if name == "attack":
                if not support_done:
                    logger("[进本] 连续出击后尚未选助战，忽略「攻击键」误判，继续等助战页")
                    self._shot("enter_attack_before_support")
                    time.sleep(self.cfg.STATE_POLL)
                    continue
                logger("[进本] 已进入战斗")
                return True
            if name == "card_select":
                if not support_done:
                    logger("[进本] 连续出击后尚未选助战，忽略选卡误判，继续等助战页")
                    time.sleep(self.cfg.STATE_POLL)
                    continue
                # 上一进程被中止时可能停在选卡视图；battle() 支持 already_selecting 接管
                logger("[进本] 已在选卡视图（遗留局面），直接接管战斗")
                return True
            if name == "np":
                if not support_done:
                    logger("[进本] 连续出击后尚未选助战，忽略宝具竖卡误判，继续等助战页")
                    time.sleep(self.cfg.STATE_POLL)
                    continue
                # 选卡视图带宝具竖卡（遮挡手牌→card_select 判不出，走 np 模板）
                logger("[进本] 已在选卡视图（宝具竖卡在场），直接接管战斗")
                return True
            if name in ("skill_ok", "target_dlg"):
                if not support_done:
                    logger(f"[进本] 连续出击后尚未选助战，先关掉残留窗（{name}）再等助战")
                    if name == "skill_ok":
                        self._tap("skill_cancel")
                    else:
                        self.adb._run(["shell", "input", "keyevent", "4"])
                    time.sleep(0.8)
                    continue
                # 上次运行中途被杀时可能停在「技能使用」或「请选择对象」窗；
                # 不点掉的话后面每一步都被它挡住，只能一路「未识别」到超时
                logger(f"[进本] 发现残留确认窗（{name}）→ 取消后直接接管战斗")
                if name == "skill_ok":
                    self._tap("skill_cancel")
                else:
                    self.adb._run(["shell", "input", "keyevent", "4"])
                time.sleep(0.8)
                return True

            if name == "quest":
                logger("[进本] 关卡确认 → 点「任务开始」")
                self._tap("quest_start")
            elif name == "support":
                # 双保险：攻击键在画面说明其实在战斗，绝不是助战页
                # 但连续出击后要求选助战时，攻击键也可能是过场误匹配，不能直接放行
                if self._find(screen, UI_TEMPLATES["attack"][0],
                              UI_TEMPLATES["attack"][1]) is not None:
                    if support_done:
                        logger("[进本] 已进入战斗")
                        return True
                    logger("[进本] 助战页同时检出攻击键，但本局仍需选助战，继续选人")
                # 连续出击后助战页刚露头时先等列表亮起来，再交给选人逻辑
                if require_support and not support_done:
                    ready = self._wait_support_list_ready(timeout=8.0)
                    if not ready:
                        logger("[进本] 助战列表未在时限内稳定加载，继续轮询")
                        time.sleep(0.8)
                        deadline = time.time() + self.cfg.UI_TIMEOUT
                        continue
                if not self._select_support():
                    return False
                support_done = True
            elif name == "party":
                # 有本职阶立绘模板才校验；不过就返回助战重选，避免点错人开打
                if not self._verify_party_support(screen):
                    logger("[进本] 助战立绘不符，返回助战重选")
                    self.adb._run(["shell", "input", "keyevent", "4"])
                    time.sleep(1.0)
                    support_done = False
                    continue
                # 队伍页能校验通过，说明已经过了助战选择
                support_done = True
                logger("[进本] 队伍确认 → 点「开始任务」")
                self._tap("party_start")
            elif name == "rerun":
                # 连续出击后助战页会叠上来；若此时再点「连续出击」坐标，
                # 容易点到助战卡片（用户反馈：进了助战页但点太快）。
                # 已要求先选助战时：若助战页特征已出现，不再点 rerun，直接等选人。
                if require_support and self._find(
                        screen, "support_page_btn.png", 0.75) is not None:
                    logger("[进本] 连续出击后已到助战页，跳过重复点击，等待列表稳定")
                    time.sleep(1.2)
                    deadline = time.time() + self.cfg.UI_TIMEOUT
                    continue
                logger("[进本] 发现连续出击弹窗 → 点「连续出击」")
                self._tap("rerun")
                # 给助战列表加载留时间，避免下一帧就开选/误点
                time.sleep(2.0)
            elif name == "loot":
                logger("[进本] 发现上一局战利品 → 点「下一步」")
                self._tap("result_next")
            elif name == "result_exp":
                logger("[进本] 结果经验页 → 点击屏幕继续")
                self._tap_ratio(0.30, 0.45)
                time.sleep(1.2)
            elif name == "apple_list":
                logger("[进本] 行动力恢复列表已展开 → 按配置吃苹果")
                if not self._handle_ap_empty():
                    return False
                continue
            elif name == "ap_short":
                logger("[进本] 行动力不足弹窗 → 点「取消」后走苹果恢复")
                self._tap_ratio(0.370, 0.826)
                time.sleep(1.5)
                if not self._handle_ap_empty():
                    return False
                continue
            elif name == "map":
                logger("[进本] 停留在事件地图 → 点本关横幅进入助战选择")
                self._tap_ratio(0.729, 0.269)
                time.sleep(2.0)
            deadline = time.time() + self.cfg.UI_TIMEOUT
            time.sleep(1.4)
        self._shot("enter_fail")
        logger("[进本] 进入战斗超时，请检查当前界面。")
        return False


    def _template_score(self, screen, template_name, region=None):
        """在整图或区域里对模板做 TM_CCOEFF_NORMED，返回最高分。"""
        import os
        import cv2
        path = os.path.join(self.cfg.TEMPLATE_DIR, template_name)
        tpl = cv2.imread(path, cv2.IMREAD_COLOR)
        if screen is None or tpl is None:
            return 0.0
        hay = screen
        if region is not None:
            rx, ry, rw, rh = region
            x1 = int(rx * self.w)
            y1 = int(ry * self.h)
            x2 = int((rx + rw) * self.w)
            y2 = int((ry + rh) * self.h)
            hay = screen[y1:y2, x1:x2]
        if hay.size == 0 or hay.shape[0] < tpl.shape[0] or hay.shape[1] < tpl.shape[1]:
            return 0.0
        try:
            res = cv2.matchTemplate(hay, tpl, cv2.TM_CCOEFF_NORMED)
        except cv2.error:
            return 0.0
        return float(res.max())


    def _verify_party_support(self, screen):
        """队伍确认页：用助战立绘模板校验，防止点错人后仍开打。"""
        # 只认本职阶配置的队伍立绘；不要用全局模板（会把美杜莎模板套到日常伊丽莎白上）
        template_name = self.class_profile.get("party_support_template")
        label = self.class_profile.get("support_label", "助战")
        if not template_name:
            return True
        thr = float(getattr(self.cfg, "PARTY_SUPPORT_FACE_THR", 0.70))
        region = getattr(self.cfg, "PARTY_SUPPORT_FACE_REGION", (0.34, 0.24, 0.22, 0.40))
        score = self._template_score(screen, template_name, region=region)
        if score < thr:
            score = max(score, self._template_score(screen, template_name))
        ok = score >= thr
        logger(f"[进本] 队伍助战校验「{label}」立绘分={score:.2f}（阈值{thr:.2f}）→ "
               f"{'通过' if ok else '不通过'}")
        if not ok:
            self._shot("party_support_mismatch")
        return ok

    def _wait_support_list_ready(self, timeout=8.0):
        """连续出击后助战页加载：等到画面不太暗，再允许选人/滑动。"""
        deadline = time.time() + timeout
        stable = 0
        while time.time() < deadline:
            screen = self._shot()
            if screen is None:
                time.sleep(0.4)
                continue
            try:
                bright = float(cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY).mean())
            except cv2.error:
                bright = 0.0
            if bright < 50.0:
                stable = 0
                logger(f"[进本] 助战页仍在加载（亮度={bright:.0f}），等待…")
                time.sleep(0.7)
                continue
            det, _ = self._detect(screen)
            if det in ("attack", "card_select", "np", "party", "rerun", "loot"):
                return det == "party"
            stable += 1
            if stable >= 2:
                logger(f"[进本] 助战页已稳定（亮度={bright:.0f}，界面={det}），开始选人")
                return True
            time.sleep(0.5)
        return False

    def _select_support_first(self):
        """通用默认：不按名字模板找人，点助战列表上方第一张可见卡的卡片中部。

        点击坐标默认 SUPPORT_FIRST_CARD=(0.44, 0.30)，与模板选人时的「卡片中部」
        同 x（避免点左侧立绘打开详情）。离开助战列表才算成功。
        """
        support_filter = self.class_profile.get("support_filter")
        if support_filter:
            filter_pos = self.cfg.SUPPORT_CLASS_FILTERS.get(support_filter)
            if filter_pos is None:
                logger(f"[助战] {support_filter} 职阶筛选坐标尚未校准，停止以避免误点")
                self._shot("support_filter_unconfigured")
                return False
            logger(f"[助战] 先选择{support_filter}职阶筛选")
            self._tap_ratio(*filter_pos)
            time.sleep(0.8)

        label = self.class_profile.get("support_label", "列表第一个")
        pos = getattr(self.cfg, "SUPPORT_FIRST_CARD", (0.44, 0.30))
        logger(f"[助战] 本副本按「选列表第一个」：{label} @({pos[0]:.2f},{pos[1]:.2f})")

        # 给列表一帧稳定时间（连续出击后可能刚亮起）
        self._wait_support_list_ready(timeout=5.0)

        for attempt in range(3):
            screen = self._shot()
            if screen is not None:
                det, _ = self._detect(screen)
                if det == "party":
                    logger("[助战] 已在队伍确认页，视为选人成功")
                    return True
            logger(f"[助战] 点击列表第一张卡（第{attempt + 1}次）"
                   f"@({pos[0]:.2f},{pos[1]:.2f})")
            self._tap_ratio(pos[0], pos[1])
            advanced, uname, screen = self._wait_support_cleared()
            if advanced:
                logger(f"[助战] 点击后已离开助战列表（{uname}），选择成功")
                return True
            if uname == "unknown" and screen is not None:
                soft = self._template_score(screen, "party_start.png")
                if soft >= 0.75:
                    logger(f"[助战] 未识别但开始任务按钮分={soft:.2f}，视为已进编队")
                    return True
                logger("[助战] 点击后停在未识别画面，按返回关闭后重试")
                self.adb._run(["shell", "input", "keyevent", "4"])
                time.sleep(0.8)
            else:
                logger("[助战] 点击第一张卡后仍在助战列表，重试")
            time.sleep(0.5)

        logger("[助战] 选列表第一个失败：多次点击后仍未离开助战页")
        self._shot("support_first_failed")
        return False

    def _select_support(self):
        """点到配置的助战，并且确认界面已经离开助战列表，才返回 True。"""
        if self.class_profile.get("support_pick_first"):
            return self._select_support_first()
        template_name = self.class_profile["support_template"]
        template = self.vision.load_template(template_name)
        if template is None:
            logger(f"[助战] 缺少目标名称模板：{template_name}，请先校准")
            self._shot("support_template_missing")
            return False

        support_filter = self.class_profile.get("support_filter")
        if support_filter:
            filter_pos = self.cfg.SUPPORT_CLASS_FILTERS.get(support_filter)
            if filter_pos is None:
                logger(f"[助战] {support_filter} 职阶筛选坐标尚未校准，停止以避免误点")
                self._shot("support_filter_unconfigured")
                return False
            logger(f"[助战] 先选择{support_filter}职阶筛选")
            self._tap_ratio(*filter_pos)
            time.sleep(0.8)


        gates = gates_from_config(self.cfg)
        policy = policy_from_config(self.cfg)
        progress = SearchProgress()
        label = self.class_profile["support_label"]
        logger(f"[助战] 本副本要找的助战：{label}（模板 {template_name}）"
               "—— 列表里没有这个人就会一路往下翻，请先确认副本类型选对了")
        region = getattr(self.cfg, "SUPPORT_NAME_REGION", None)
        floor_margin = getattr(self.cfg, "SUPPORT_PEAK_FLOOR_MARGIN", 0.08)
        x_tol = getattr(self.cfg, "SUPPORT_STABLE_X_PX", 48)
        y_tol = getattr(self.cfg, "SUPPORT_STABLE_Y_PX", 30)
        move_delta = getattr(self.cfg, "SUPPORT_LIST_MOVE_DELTA", 3.5)
        timeout = getattr(self.cfg, "SUPPORT_SELECT_TIMEOUT", 180)
        deadline = time.time() + timeout
        pending = None
        anchor_sig = None
        awaiting_move = False
        no_peak = 0
        load_waits = 0

        first_frame = True
        while time.time() < deadline:
            screen = self._shot()
            if screen is None:
                time.sleep(0.4)
                continue
            if first_frame:
                first_frame = False
                try:
                    cv2.imwrite(os.path.join(
                        self._debug_dir,
                        f"{time.time_ns()}_support_scan_start.png"), screen)
                except OSError:
                    pass
            rank, signature = evaluate_support_screen(
                screen, template, gates, region=region,
                floor_margin=floor_margin,
                top_margin_px=getattr(self.cfg, "SUPPORT_NAME_TOP_MARGIN_PX", 90),
                    bottom_margin_px=getattr(self.cfg, "SUPPORT_NAME_BOTTOM_MARGIN_PX", 140))
            if awaiting_move:
                page_advanced = list_moved(anchor_sig, signature, move_delta)
                awaiting_move = False
            else:
                page_advanced = True
            # 连续多帧一个候选峰都没有 = 根本不在助战列表（详情页/战斗/编队页），
            # 不再盲扫盲刷（run22 局5 在错误界面扫满 3 次刷新的教训）
            if getattr(rank, "best", None) is None:
                # best is None = 本帧没有目标名字峰。
                # 只有真在加载（偏暗、且还不是 support）才空等；
                # 已经是助战页却无峰 = 目标不在本页，应下滑/刷新，绝不能按返回。
                det0, _loc0 = self._detect(screen)
                try:
                    bright = float(cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY).mean())
                except cv2.error:
                    bright = 0.0
                if (load_waits < 16
                        and bright < 50.0
                        and det0 not in ("support", "attack", "card_select",
                                         "np", "party", "quest", "rerun",
                                         "loot")):
                    load_waits += 1
                    logger(f"[助战] 列表尚未加载出卡片（当前={det0}，"
                           f"亮度={bright:.0f}），等待中 ({load_waits}/16)")
                    time.sleep(0.8)
                    continue
                if (load_waits < 8
                        and det0 not in ("support", "attack", "card_select",
                                         "np", "party", "quest", "rerun",
                                         "loot")):
                    load_waits += 1
                    logger(f"[助战] 界面过渡中（当前={det0}，亮度={bright:.0f}），"
                           f"等待中 ({load_waits}/8)")
                    time.sleep(0.8)
                    continue
                if det0 in ("attack", "card_select", "np"):
                    logger("[助战] 无候选且界面像战斗，但本局未确认助战 → 选人失败")
                    self._shot("support_unconfirmed_battle")
                    return False
                if det0 in ("party", "quest", "rerun", "loot"):
                    logger(f"[助战] 无候选，当前界面={det0}，不在助战页，放弃选人")
                    return False
                if det0 == "support":
                    no_peak = 0
                    load_waits = 0
                    pending = None
                    if not page_advanced:
                        logger("[助战] 滑动后画面无变化，视为列表已到底")
                    action, progress = advance_scan(
                        progress, "reject", page_advanced, policy)
                    logger(f"[助战] {label} 本页无匹配 → "
                           f"{self._support_action_text(action)}")
                    if action == "give_up":
                        self._shot("support_not_found")
                        logger(f"[助战] 刷新预算已用尽（已刷新 "
                               f"{progress.refreshes_used}/{policy.max_refreshes} 次），"
                               f"列表中未找到{label}，停止本次出击")
                        return False
                    anchor_sig, awaiting_move = self._apply_support_action(
                        action, progress, policy, signature)
                    continue
                no_peak += 1
                if no_peak >= 4:
                    logger(f"[助战] 连续4帧无候选且界面={det0}，按返回尝试退出覆盖层")
                    self.adb._run(["shell", "input", "keyevent", "4"])
                    time.sleep(0.6)
                    no_peak = 0
                    continue
                time.sleep(0.5)
                continue
            else:
                no_peak = 0
                load_waits = 0

            if rank.status == "accept":
                if same_card(pending, rank.chosen, x_tol, y_tol):
                    ok, progress, action, signature = self._commit_support_click(
                        rank.chosen, template, gates, policy, progress, label,
                        region, floor_margin, x_tol, y_tol)
                    pending = None
                    if ok:
                        return True
                    if action == "give_up":
                        self._shot("support_not_found")
                        logger(f"[助战] 刷新预算已用尽（已刷新 "
                               f"{progress.refreshes_used}/{policy.max_refreshes} 次），"
                               f"列表中未找到{label}，停止本次出击")
                        return False
                    anchor_sig, awaiting_move = self._apply_support_action(
                        action, progress, policy, signature)
                    continue
                pending = rank.chosen
                logger(f"[助战] {label} 本帧{describe_rank(rank)}，"
                       f"再截一帧确认后才点击")
                time.sleep(0.4)
                continue

            pending = None
            if not page_advanced:
                logger("[助战] 滑动后画面无变化，视为列表已到底")
            if getattr(rank, "reason", None) in (
                    "near_top_partial", "near_bottom_partial"):
                # 贴顶/贴底半截卡：小幅滑动把整卡移进可视区再匹配，禁止直接点
                action, progress = "nudge", progress
            else:
                action, progress = advance_scan(
                    progress, rank.status, page_advanced, policy)
            logger(f"[助战] {label} {describe_rank(rank)} → "
                   f"{self._support_action_text(action)}")
            if action == "give_up":
                self._shot("support_not_found")
                logger(f"[助战] 刷新预算已用尽（已刷新 "
                       f"{progress.refreshes_used}/{policy.max_refreshes} 次），"
                       f"列表中未找到{label}，停止本次出击")
                return False
            anchor_sig, awaiting_move = self._apply_support_action(
                action, progress, policy, signature)

        self._shot("support_not_found")
        logger(f"[助战] 选人超过 {timeout:.0f}s 仍未确认{label}，停止本次出击")
        return False

    def _support_action_text(self, action):
        return {
            "scroll": "向下查找",
            "nudge": "半截卡，小幅滑动后再选",
            "refresh": "刷新列表",
            "give_up": "停止",
        }.get(action, action)

    def _apply_support_action(self, action, progress, policy, signature):
        """执行翻页或刷新。返回 (下一帧用来对比的签名, 是否要检查这次滑动有没有生效)。"""
        if action == "refresh":
            self._refresh_support_list(progress.refreshes_used, policy.max_refreshes)
            return None, False
        if action == "nudge":
            self._swipe_support(nudge=True)
            return signature, True
        self._swipe_support(nudge=False)
        return signature, True

    def _swipe_support(self, nudge=False):
        if nudge:
            start = getattr(self.cfg, "SUPPORT_NUDGE_START", (0.50, 0.60))
            end = getattr(self.cfg, "SUPPORT_NUDGE_END", (0.50, 0.42))
            duration = 280
        else:
            start = self.cfg.SUPPORT_SCROLL_START
            end = self.cfg.SUPPORT_SCROLL_END
            duration = 450
        self.adb.swipe(self.w * start[0], self.h * start[1],
                       self.w * end[0], self.h * end[1], duration_ms=duration)
        time.sleep(0.85)

    def _refresh_support_list(self, refresh_index, refresh_max):
        logger(f"[助战] 本轮列表已扫完，刷新助战列表 ({refresh_index}/{refresh_max})")
        before = self._shot()
        pos = getattr(self.cfg, "SUPPORT_REFRESH_POS", (0.955, 0.155))
        self._tap_ratio(pos[0], pos[1])
        time.sleep(0.7)
        after = self._shot()
        drop = getattr(self.cfg, "SUPPORT_REFRESH_DIALOG_DROP", 18.0)
        opened = False
        if before is not None and after is not None:
            opened = refresh_dialog_open(to_gray(before), to_gray(after), drop)
        if opened:
            logger("[助战] 检测到更新确认框，点「决定」")
            confirm = getattr(self.cfg, "SUPPORT_REFRESH_CONFIRM", (0.656, 0.685))
            self._tap_ratio(confirm[0], confirm[1])
            time.sleep(1.8)
        else:
            logger("[助战] 未检测到更新确认框，不点「决定」，只等待列表重载")
            time.sleep(1.2)

    def _wait_support_cleared(self):
        """等到界面不再是助战列表。超时仍停在助战或未知画面则不算成功。
        过渡/加载黑屏也视为已离开列表（否则会误判失败重复点击 -> 选错人）。"""
        timeout = getattr(self.cfg, "SUPPORT_TRANSITION_TIMEOUT", 6.0)
        deadline = time.time() + timeout
        last_name = "unknown"
        last_screen = None
        while time.time() < deadline:
            screen = self._shot()
            last_screen = screen
            if screen is None:
                time.sleep(0.3)
                continue
            name, _ = self._detect(screen)
            last_name = name
            if name not in ("support", "unknown"):
                return True, name, screen
            # 加载/过场：整屏压暗，说明已经离开列表
            try:
                gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
                dark = float(gray.mean()) < 45.0
            except cv2.error:
                dark = False
            if dark:
                time.sleep(0.6)
                s2 = self._shot()
                if s2 is not None:
                    n2, _ = self._detect(s2)
                    if n2 not in ("support", "unknown"):
                        return True, n2, s2
                    if float(cv2.cvtColor(s2, cv2.COLOR_BGR2GRAY).mean()) < 45.0:
                        return True, "loading", s2
                    if self._find(s2, "support_page_btn.png", 0.8) is None:
                        return True, "loading", s2
            time.sleep(0.3)
        return False, last_name, last_screen

    def _commit_support_click(self, candidate, template, gates, policy, progress,
                              label, region, floor_margin, x_tol, y_tol):
        """点已确认的卡。离开助战列表才返回成功；否则换点法或交给翻页/刷新。
        每次点击前都会重新复核列表，确保坐标仍指向同一张卡。"""
        taps = 0
        while True:
            # 点击前复核：列表可能已刷新/滚动，旧坐标会点到别的从者（run31 选成兰斯洛特）
            fresh = self._shot()
            if fresh is not None:
                rank_f, _sig_f = evaluate_support_screen(
                    fresh, template, gates, region=region,
                    floor_margin=floor_margin,
                    top_margin_px=getattr(self.cfg, "SUPPORT_NAME_TOP_MARGIN_PX", 90),
                    bottom_margin_px=getattr(self.cfg, "SUPPORT_NAME_BOTTOM_MARGIN_PX", 140))
                if (rank_f.status != "accept"
                        or rank_f.chosen is None
                        or not same_card(candidate, rank_f.chosen, x_tol, y_tol)):
                    logger("[助战] 点击前复核失败（列表已刷新或位置变化），放弃本次点击，重新扫描")
                    return False, progress, "rescan", None
                candidate = rank_f.chosen
            mid = (int(self.w * 0.44), int(candidate.y - self.h * 0.083))
            name_pt = (int(candidate.x),
                       int(candidate.y - getattr(self.cfg, "SUPPORT_CLICK_Y_OFFSET_PX", 0)))
            btn = (int(self.w * 0.894), int(candidate.y - self.h * 0.083))
            # 卡片中部 → 名称文本 → 编队确认按钮；不点左侧立绘（会开从者详情页）
            points = [mid, name_pt, btn]
            index = min(progress.clicks_on_candidate, len(points) - 1)
            x, y = points[index]
            where = ("卡片中部" if index == 0
                     else "名称" if index == 1 else "编队确认按钮")
            logger(f"[助战] 点击已确认的{label}（{where}）@({x},{y}) "
                   f"ncc={candidate.ncc:.2f} structure={candidate.structure:.2f} "
                   f"glyph={candidate.glyph_recall:.2f} "
                   f"trailing={candidate.trailing_ratio:.2f}")
            try:
                import cv2, os
                th, tw = to_gray(template).shape[:2] if False else template.shape[:2]
                # template may be BGR or gray
                import numpy as np
                tg = template if len(getattr(template, 'shape', ())) == 2 else None
                if tg is None:
                    tg = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY) if len(template.shape) == 3 else template
                th, tw = tg.shape[:2]
                left = int(round(candidate.x - tw / 2)); top = int(round(candidate.y - th / 2))
                frame = self.adb.read_screen()
                if frame is not None and left >= 0 and top >= 0 and top + th <= frame.shape[0] and left + tw <= frame.shape[1]:
                    os.makedirs("logs", exist_ok=True)
                    cv2.imwrite("logs/support_match_last.png", frame[top:top + th, left:left + tw])
                    ann = frame.copy()
                    cv2.rectangle(ann, (left, top), (left + tw, top + th), (0, 255, 0), 2)
                    cv2.circle(ann, (x, y), 22, (0, 0, 255), 3)
                    cv2.imwrite("logs/support_click_last.png", ann)
            except Exception as exc:
                logger(f"[助战] 匹配裁块保存失败: {exc}")
            self.adb.tap(x, y)
            advanced, uname, screen = self._wait_support_cleared()
            if advanced:
                logger(f"[助战] 点击后已离开助战列表（{uname}），选择成功")
                return True, progress, None, None
            if uname == "unknown":
                soft = self._template_score(screen, "party_start.png")
                if soft >= 0.75:
                    logger(f"[助战] 未识别但开始任务按钮分={soft:.2f}，视为已进编队")
                    return True, progress, None, None
                logger("[助战] 点击后停在未识别画面，按返回关闭，不视为选人成功")
                self.adb._run(["shell", "input", "keyevent", "4"])
                time.sleep(0.8)
                screen = self._shot()
            still = False
            signature = None
            if screen is not None:
                rank, signature = evaluate_support_screen(
                    screen, template, gates, region=region,
                    floor_margin=floor_margin,
                    top_margin_px=getattr(self.cfg, "SUPPORT_NAME_TOP_MARGIN_PX", 90),
                    bottom_margin_px=getattr(self.cfg, "SUPPORT_NAME_BOTTOM_MARGIN_PX", 140))
                still = (rank.status == "accept"
                         and same_card(candidate, rank.chosen, x_tol, y_tol))
                if still:
                    candidate = rank.chosen
            # 点击失败不等于列表到底，按「这一页还在」继续计次，避免立刻刷新。
            action, progress = advance_after_click(
                progress, ui_advanced=False, same_card_still_valid=still,
                page_advanced=True, policy=policy)
            if action == "retry_click":
                taps += 1
                if taps >= 3:
                    logger("[助战] 同一张卡已连续点击3次仍未离开列表，判定该点击无效，交给翻页/刷新")
                    return False, progress, "give_up_click", signature
                logger("[助战] 点击后仍在助战列表，同一张卡仍匹配，换位置重试")
                continue
            logger("[助战] 点击后界面没有进入编队，放弃这次点击并继续查找")
            return False, progress, action, signature

    # ------------------------------------------------------------------
    # 战斗
    def battle(self):
        self.turn = 0
        self._used_skill_keys.clear()
        self._skill_base_brightness = {}
        self._battle_start_ts = time.time()
        self._wave = None
        unknown_streak = 0
        while True:
            if time.time() - self._battle_start_ts > self.cfg.BATTLE_TIMEOUT:
                logger("[战斗] 单局超时，疑似卡死，停止。")
                self._shot("battle_timeout")
                return False

            # 御主菜单若被误开会挡住右侧点击/污染判断，每回合开头检测并关闭
            self._close_master_menu()

            # 残留的技能确认窗/选目标窗（例如上次 kill 遗留）会模态挡一切，先关
            if self._dismiss_blocking_dialog():
                continue

            # 1) 等技能阶段（攻击按钮）；若直接进了结算说明已结束
            name, screen, _ = self._wait_any(
                ["attack", "loot", "rerun", "card_select", "np",
                 "support", "party", "quest", "servant_panel", "command_spell",
                 "result_exp"], timeout=30,
                gap=self.cfg.STATE_POLL)
            if name == "result_exp":
                logger("[战斗] 结果经验页 → 点击继续")
                self._tap_ratio(0.30, 0.45)
                time.sleep(1.2)
                continue
            if name == "command_spell":
                self._close_command_spell(screen)
                continue
            if name == "servant_panel":
                # 战斗中误点从者/宝具详情页：直接点 X 关闭再继续，不要按返回键
                self._close_servant_panel(screen)
                continue
            if name in ("loot", "rerun"):
                logger("[战斗] 检测到结算，本局结束")
                return True
            if name == "support":
                # 状态漂移：其实在助战页（run26 教训：旧 want 列表不含 support，
                # 助战页被当 unknown 静默空转到单局超时）
                logger("[战斗] 实际在助战页，重选助战")
                if not self._select_support():
                    return False
                continue
            if name == "party":
                if not self._verify_party_support(screen):
                    logger("[战斗] 助战立绘不符，返回助战重选")
                    self.adb._run(["shell", "input", "keyevent", "4"])
                    time.sleep(1.0)
                    continue
                logger("[战斗] 队伍确认 → 点「开始任务」")
                self._tap("party_start")
                continue
            if name == "quest":
                logger("[战斗] 关卡确认 → 点「任务开始」")
                self._tap("quest_start")
                continue
            if name is None:
                unknown_streak += 1
                if unknown_streak == 2:
                    logger("[战斗] 连续未识别界面，点返回箭头尝试退出残留界面")
                    self._tap("card_back")
                    time.sleep(1.0)
                elif unknown_streak >= 4:
                    logger("[战斗] 仍未识别，按返回键尝试退出残留界面")
                    self.adb._run(["shell", "input", "keyevent", "4"])
                    time.sleep(1.0)
                    unknown_streak = 0
                # 检查异常状态（网络错误等）
                if screen is not None:
                    kind, _desc = self._detect_anomaly(screen)
                    if kind is not None:
                        if not self._process_anomaly(screen):
                            return False
                        # 异常已处理，重置单局计时避免误判卡死
                        self._battle_start_ts = time.time()
                # 可能在过场/加载，继续等
                continue
            unknown_streak = 0

            if name == "attack" and screen is not None:
                self._update_wave(screen)

            already_selecting = name in ("card_select", "np")
            if already_selecting:
                logger("[战斗] 检测到已展开的指令卡，从当前选卡界面继续")
            elif getattr(self.cfg, "SLOT_SWAP_DETECT", False) and screen is not None:
                self._check_slot_swap(screen)

            # 2) 技能：首回合开技能；check_skills_each_turn 时每回合按 CD 再检
            if not already_selecting and getattr(self.cfg, "USE_SKILLS", False):
                if (self.turn > 0
                        and self.class_profile.get("check_skills_each_turn", False)):
                    # 每回合重测冷却，清空「已用」以免误过滤
                    self._used_skill_keys.clear()
                skills = skills_to_check(
                    self.class_profile, self.turn, self._used_skill_keys)
                if skills:
                    self._cast_skills(skills)

            # 3) 点攻击，等指令卡完全展开（避免在展开动画中识别/点击）
            if not already_selecting:
                for _try in range(3):
                    if _try:
                        # 重点前先看一眼：选卡视图已开就绝不再点攻击键
                        # （攻击键坐标落在选卡视图=手牌4号位，会误选卡，m02255 取证）
                        pre = self._shot()
                        if pre is not None:
                            tpl0, thr0 = UI_TEMPLATES["attack"]
                            if self._find(pre, tpl0, thr0) is None:
                                break
                    self._tap("attack")
                    time.sleep(self.cfg.CARD_SELECT_WAIT)
                    opened = False
                    miss_streak = 0
                    for _chk in range(8):
                        chk = self._shot()
                        if chk is None:
                            time.sleep(0.3)
                            continue
                        tpl, thr = UI_TEMPLATES["attack"]
                        if self._find(chk, tpl, thr) is not None:
                            miss_streak = 0
                            break  # 攻击按钮还在 → 外层重点攻击
                        miss_streak += 1
                        if miss_streak < 2:
                            # 单帧未见攻击键可能是 NP 扇形/动画闪烁，双帧确认才算消失
                            time.sleep(0.3)
                            continue
                        hand_chk = self.card_rec.recognize_hand(chk)
                        if sum(1 for c in hand_chk if c.color != "?") >= 4:
                            opened = True
                            break
                        # 攻击键消失但无手牌：技能施放动画/卡未展开，稍等复查
                        time.sleep(0.4)
                    if opened or miss_streak >= 2:
                        break
                    logger(f"[战斗] 攻击按钮仍在画面(第{_try + 1}次)，重点攻击")
                sv = self._shot("select_view")  # 存档选卡视图，便于核对 1/2/3 号位宝具坐标
                self._update_wave(sv)

            # 4) 稳定识别手牌（连续多帧一致才采纳）；识别到宝具即点击作为行动一
            np_cards, hand = self._recognize_hand_stable()
            if hand is None:
                cur = self._shot()
                tpl, thr = UI_TEMPLATES["attack"]
                if cur is not None and self._find(cur, tpl, thr) is not None:
                    logger("[战斗] 未截到手牌但攻击键在画面(技能栏)，自动重点攻击")
                    self._tap("attack")
                    time.sleep(self.cfg.CARD_SELECT_WAIT)
                else:
                    logger("[战斗] 未截到手牌且无攻击键，按一次返回清理残留界面")
                    self.adb._run(["shell", "input", "keyevent", "4"])
                    time.sleep(0.8)
                continue
            n = 3 - len(np_cards)
            logger(f"[T{self.turn + 1}] 手牌: {describe_hand(hand)}")

            if (self.class_profile.get("require_servant_recognition", False)
                    and any(card.servant is None for card in hand)):
                logger("[战斗] 当前策略要求识别从者归属，但至少一张手牌未识别；停止选卡")
                self._shot("servant_card_unrecognized")
                return False

            known = sum(1 for c in hand if c.color != "?")
            if known >= max(1, n):
                preferred = self.class_profile.get("preferred_servant")
                if (preferred is not None
                        and self.turn == 0
                        and not self.class_profile.get("prefer_servant_every_turn", False)):
                    preferred = None
                slots, ct, desc = choose_cards(
                    hand, n, self.cfg, np_cards, preferred_servant=preferred)
            else:
                slots = [i for i in range(5)][:n]
                desc = f"手牌识别不足(仅{known}张)，降级盲点 {slots}"
            logger(f"[T{self.turn + 1}] 宝具x{len(np_cards)}，{desc}")

            # 5) 点手牌并校验选满 3 张（未选满自动补点，绝不卡死）
            self._pick_cards(slots, n)
            self.turn += 1

            # 6) 等本回合结算：回到技能阶段（下一回合）或进入结算
            state, _, _ = self._wait_any(
                ["attack", "loot", "rerun"],
                timeout=self.cfg.BATTLE_TIMEOUT, gap=self.cfg.BATTLE_POLL,
                advance=True)
            if state in ("loot", "rerun"):
                logger("[战斗] 本回合后进入结算")
                return True
            if state == "attack":
                logger("[T] 进入下一回合")
                # 回合推进=有进度，重置 watchdog（超时只用于真正卡死）
                self._battle_start_ts = time.time()
                continue

    # ------------------------------------------------------------------
    # 技能：点技能 → 决定 → （单体）选目标
    # 截图实测约 1.3s/张，弹窗在点击后约 1.1~1.5s 才稳定，故：
    #   先固定等待让弹窗稳定再截图识别；点击被技能动画吞掉时重发；
    #   单体技能点决定后等「请选择对象」并点目标；每个技能完成后确认回到技能阶段。
    def _wait_for(self, want, initial_wait, retries, gap):
        """先固定等待（让弹窗/动画稳定），再截图检测；未命中则补截。
        返回 (命中标识, screen, loc)；未命中返回 (None, screen, None)。"""
        time.sleep(initial_wait)
        screen = None
        for _ in range(retries):
            screen = self._shot()
            if screen is not None:
                name, loc = self._detect(screen)
                if name in want:
                    return name, screen, loc
            time.sleep(gap)
        return None, screen, None

    def _wait_template(self, tpl_file, thr, timeout, initial=0.0, gap=0.8):
        """专门轮询某一个模板（不受 _detect 优先级干扰）。命中返回 screen。"""
        if initial:
            time.sleep(initial)
        deadline = time.time() + timeout
        screen = None
        while time.time() < deadline:
            screen = self._shot()
            if screen is not None and self._find(screen, tpl_file, thr) is not None:
                return screen
            time.sleep(gap)
        return None

    def _wait_skill_phase(self, timeout=None, initial=0.0, gap=0.8):
        """等待回到技能阶段（攻击按钮重新出现）。
        若期间被从者详情面板挡住，先自动点 X 关闭再继续等（用户 m04690 的「2号位状态栏」）。"""
        timeout = timeout or self.cfg.SKILL_RETURN_TIMEOUT
        if initial:
            time.sleep(initial)
        deadline = time.time() + timeout
        closed = 0
        while time.time() < deadline:
            screen = self._shot()
            if screen is not None:
                if self._find(screen, "battle_attack.png", None) is not None:
                    return True
                if closed < 2 and self._find(screen, "servant_panel.png", 0.85) is not None:
                    self._close_servant_panel(screen)
                    closed += 1
                    continue
            time.sleep(gap)
        return False

    def _dismiss_residual_dialog(self):
        """兜底：若画面残留技能窗/选目标窗，按返回键关闭，避免污染下一个技能。"""
        screen = self._shot()
        if screen is None:
            return
        name, _ = self._detect(screen)
        if name in ("target_dlg", "skill_ok"):
            self.adb._run(["shell", "input", "keyevent", "4"])
            time.sleep(0.8)

    def _close_command_spell(self, screen=None):
        """关闭误开的令咒菜单（右上角 X，实测坐标 1672,254）。"""
        if screen is None:
            screen = self._shot()
        if screen is None:
            return False
        if self._find(screen, "command_spell.png", 0.85) is None:
            return False
        logger("[战斗] 检测到令咒菜单 → 返回键关闭")
        self.adb._run(["shell", "input", "keyevent", "4"])
        time.sleep(0.7)
        chk = self._shot()
        if chk is not None and self._find(chk, "command_spell.png", 0.85) is None:
            return True
        logger("[战斗] 令咒菜单仍在 → 再按一次返回键")
        self.adb._run(["shell", "input", "keyevent", "4"])
        time.sleep(0.7)
        return True

    def _close_servant_panel(self, screen=None):
        """关闭从者详情/宝具详情面板（面板右上角的 X）：
        返回 True 表示原本就在面板上并已点关闭。"""
        if screen is None:
            screen = self._shot()
        if screen is None:
            return False
        if self._find(screen, "servant_panel.png", 0.85) is None:
            return False
        # 覆盖层是模态的：先按返回键关闭（不会误点右上角的令咒图标），失败再点 X
        logger("[战斗] 检测到从者详情面板（状态/宝具页）→ 返回键关闭")
        self.adb._run(["shell", "input", "keyevent", "4"])
        time.sleep(0.7)
        chk = self._shot()
        if chk is not None and self._find(chk, "servant_panel.png", 0.85) is None:
            return True
        # 绝不点右上角：实测 (1645,110) 在「无面板」时正好是令咒图标的命中区，
        # 点下去就弹令咒菜单（本机复现 bt7.png）。再按一次返回键兜底。
        logger("[战斗] 面板仍在 → 再按一次返回键（不点右上角，避免误开令咒）")
        self.adb._run(["shell", "input", "keyevent", "4"])
        time.sleep(0.7)
        return True

    def _dismiss_blocking_dialog(self):
        """关闭残留的技能确认窗/选目标窗（手工 kill 后常见），返回是否关了一个。
        确认窗点「取消」放弃该技能（安全）；目标窗按返回键取消选目标。"""
        screen = self._shot()
        if screen is None:
            return False
        name, _ = self._detect(screen)
        if name == "skill_ok":
            logger("[战斗] 检测到残留技能确认窗，点取消关闭")
            fx, fy = getattr(self.cfg, "SKILL_CANCEL_POS", (0.332, 0.588))
            self._tap_ratio(fx, fy)
            time.sleep(0.6)
            return True
        if name == "target_dlg":
            logger("[战斗] 检测到残留选目标窗，返回键关闭")
            self.adb._run(["shell", "input", "keyevent", "4"])
            time.sleep(0.6)
            return True
        return False

    def _close_master_menu(self, screen=None):
        """战斗中 Android 返回键会打开御主菜单（战斗菜单/御主技能两菱形），
        它会挡住右侧点击并污染状态判断；检测到就再点御主头像关闭。返回是否关闭过。"""
        if screen is None:
            screen = self._shot()
        if screen is None:
            return False
        h, w = screen.shape[:2]
        region = screen[0:int(h * 0.55), int(w * 0.82):w]
        mm_thr = getattr(self.cfg, "MASTER_MENU_THRESHOLD", 0.7)
        if self._find(region, "master_menu.png", mm_thr) is None:
            return False
        # 真机实验（m01930-60）：X / 御主头像 / 返回键 / 屏幕中心 均关不掉托盘；
        # 且 run16 证实托盘不阻挡攻击与选卡（T1 宝具+三卡选择成功）。
        # 故只记录不点击，避免无效点击浪费回合时间。
        logger("[战斗] 检测到御主菜单展开（已知关闭手势无效且不影响攻击/选卡，忽略）")
        try:
            cv2.imwrite(os.path.join(self._debug_dir, "master_menu_open.png"), screen)
        except OSError:
            pass
        return True

    def _cast_skills(self, skills):
        if not self._skill_base_brightness:
            self._record_skill_baselines()
        logger("[技能] 检查并释放配置中的可用技能")
        if self.turn == 0:
            # 进场/回合开始动画期间点击会被吞，开局的充能技尤其关键（娄希3技→宝具）
            time.sleep(0.8)
            self._wait_skill_phase(timeout=10, initial=0.0, gap=0.35)
        for key, target in skills:
            # 只在技能阶段（攻击按钮在）点技能，避免过场/选卡时点飞
            if not self._wait_skill_phase(timeout=6, initial=0.0, gap=0.35):
                logger(f"[技能] 未回到技能阶段，尝试关闭残留界面后再继续（下一个是 {key}）")
                self._dismiss_residual_dialog()
                # 返回键只在确有弹窗时按；战斗中无弹窗按返回会打开御主菜单（m01809 实锤）
                p = self._shot()
                nm, _ = self._detect(p) if p is not None else (None, None)
                if nm in ("target_dlg", "skill_ok"):
                    self.adb._run(["shell", "input", "keyevent", "4"])
                    time.sleep(0.6)
                else:
                    self._close_master_menu(p)
                if not self._wait_skill_phase(timeout=8, initial=0.2, gap=0.35):
                    logger(f"[技能] 仍未回到技能阶段，停止后续技能（下一个本应是 {key}）")
                    break
            if self._cast_one_skill(key, target):
                # 回报成功后须回到技能栏才记已用；未回栏多为假成功/目标窗残留
                # （run_20261109_203756 skill_5：半暗假 CD 后下一技能「未回到技能阶段」）。
                if self._wait_skill_phase(timeout=6, initial=0.8, gap=0.25):
                    self._used_skill_keys.add(key)
                else:
                    logger(f"[技能] {key} 回报成功但未回技能阶段，不记已用（疑假成功/残留层）")
        logger("[技能] 本轮技能检查完毕")
        self._wait_skill_phase(timeout=10)
    def _skill_brightness(self, screen, key):
        """技能图标 ROI 灰度均值；失败返回 None。"""
        if screen is None:
            return None
        fx, fy = self.cfg.POS[key]
        roi_box = getattr(self.cfg, "SKILL_CD_ROI", (0, 0, 0.045, 0.055))
        h_scr, w_scr = screen.shape[:2]
        dx, dy, bw, bh = roi_box
        cx, cy = fx + dx, fy + dy
        x1 = max(0, int((cx - bw / 2) * w_scr))
        y1 = max(0, int((cy - bh / 2) * h_scr))
        x2 = min(w_scr, int((cx + bw / 2) * w_scr))
        y2 = min(h_scr, int((cy + bh / 2) * h_scr))
        roi = screen[y1:y2, x1:x2]
        if roi.size == 0:
            return None
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        return float(gray.mean())

    def _skill_cd_limit(self, key):
        """该技能 CD 判定下限：有基线用 基线-25（图标底色差异大，全局阈值会重叠）。
        无基线时用 85——冈田 skill_1（心眼）可用时常只有 97-112，旧阈值 100 会假 CD。"""
        base = self._skill_base_brightness.get(key)
        if base is None:
            return 85.0
        return base - 25.0

    def _skill_hard_cd_limit(self, key):
        """确认后「已转真 CD / 自身·群体成功」门槛：比可用检测更严。
        依据 run_20261109_203756：真自身成功亮度约 54–82；skill_5 假半暗=120
        （基线156→lim=131，旧逻辑 b<lim 误判成功）。取 min(基线-25, 100)
        放过 54–82、拦住 120。不改 _skill_cd_limit，以免影响点击前可用检测。"""
        return min(self._skill_cd_limit(key), 100.0)

    def _record_skill_baselines(self):
        """开局/换人后第一帧记录全部技能图标亮度作为可用基线。
        必须在技能栏亮背景（攻击键可见）时采样，过场/弹窗压暗会污染基线。"""
        screen = None
        for _ in range(5):
            s = self._shot()
            if s is not None and self._find(s, "battle_attack.png", None) is not None:
                screen = s
                break
            time.sleep(0.4)
        if screen is None:
            screen = self._shot()
        if screen is None:
            return
        for i in range(9):
            key = f"skill_{i}"
            b = self._skill_brightness(screen, key)
            # 只把「非明显 CD」的读数当基线：CD 常见 54-88，冈田 skill_1 可用可低至 ~97，
            # 门槛 95 才能给偏暗图标建基线；中途若误采到 CD，base-25 仍可区分更暗的真 CD。
            if b is not None and b >= 95.0:
                self._skill_base_brightness[key] = b
        logger(f"[技能] 记录亮度基线: "
               + ", ".join(f"{k}={v:.0f}" for k, v in sorted(self._skill_base_brightness.items())))

    def _is_skill_available(self, screen, key, log=True):
        """检查技能图标是否处于可用状态（非 CD 中）。
        优先与该局该技能可用基线比差值（<基线-25 视为 CD），无基线回落全局阈值。"""
        if not getattr(self.cfg, "SKILL_CD_CHECK", False):
            return True  # 未启用则默认跳过检测
        threshold = self._skill_cd_limit(key)
        brightness = self._skill_brightness(screen, key)
        if brightness is None:
            return True
        # 刚放完技能时图标区可能短暂偏暗；多等几帧再判 CD，避免误跳过
        # （杀阶 skill_3 曾出现基线150、读数101 的假 CD）
        if brightness <= threshold:
            for _ in range(3):
                time.sleep(0.55)
                screen = self._shot()
                brightness = self._skill_brightness(screen, key)
                if brightness is None:
                    return True
                if brightness > threshold:
                    break
        available = brightness > threshold
        if available and brightness > self._skill_base_brightness.get(key, 0.0):
            self._skill_base_brightness[key] = brightness  # 基线单调上修，抗漂移
        if (not available) and log:
            logger(f"[技能] {key} CD中 (亮度={brightness:.0f} < {threshold:.0f})")
        return available

    def _select_skill_target(self, key, target, after_confirm=False):
        """点固定目标位。模板只做辅助；确认后/已离技能栏时直接点坐标。"""
        kind, idx = target
        thr = getattr(self.cfg, "SKILL_TARGET_THRESHOLD", 0.72)
        # 真正要点肖像位时用更严的阈值：实测真目标窗 0.98，施放动画/暗场假匹配在 0.72-0.80，
        # 阈值太低会在动画期误点 3 号位把从者状态栏点出来（剑阶/狂阶同源问题）
        thr_click = max(float(thr), float(getattr(self.cfg, "SKILL_TARGET_CLICK_THRESHOLD", 0.88)))
        # 给目标条幅一点点出现时间；命中与否不阻挡固定点击
        if kind == "servant_if_prompt":
            # 条件目标技能：目标窗可能在确认后 1~3 秒才淡入，
            # 因此同时盯两件事——目标窗出现（攻击键隐藏）就返回；
            # 攻击键连续约 6 帧稳定可见（≈1.8s）才提前退出，避免闪 3 帧就放弃等窗
            # （run_20261109_203756 skill_5：过早结束等待 + 半暗假 CD 跳过选目标）。
            deadline = time.time() + (4.0 if after_confirm else
                                      getattr(self.cfg, "SKILL_TARGET_TIMEOUT", 3.0))
            atk_seen = 0
            screen = None
            while time.time() < deadline:
                s = self._shot()
                if s is not None:
                    atk = self._find(s, "battle_attack.png", None) is not None
                    if not atk:
                        atk_seen = 0
                        if self._find(s, "target_dialog.png", thr_click) is not None:
                            screen = s
                            break
                    else:
                        atk_seen += 1
                        if after_confirm and atk_seen >= 6:
                            break
                time.sleep(0.3)
        else:
            if after_confirm:
                win_timeout = 1.2
            else:
                win_timeout = getattr(self.cfg, "SKILL_TARGET_TIMEOUT", 3.0)
            screen = self._wait_template(
                "target_dialog.png", thr,
                timeout=win_timeout,
                initial=0.25 if after_confirm else self.cfg.SKILL_CONFIRM_WAIT,
                gap=0.25)
        probe = screen if screen is not None else self._shot()
        on_skill_bar = (
            probe is not None
            and self._find(probe, "battle_attack.png", None) is not None)
        # 真·选目标界面攻击按钮是隐藏的；攻击键还在画面却"命中目标窗"= 假匹配，
        # 此时点 servant_target 会开 3 号位状态面板（run12 误点根因），丢弃该命中
        if kind in ("servant", "servant_if_prompt") and on_skill_bar and screen is not None:
            logger(f"[技能] {key} 目标窗命中但攻击按钮仍在画面，疑似假匹配，按无窗处理")
            screen = None
        # 攻击按钮还在 = 仍在技能栏；未确认时点己方头像会开详情，绝对不能点；
        # 已确认时见到攻击键=施放动画已结束，交给下面亮度判定（run11：快技能在此被误报）
        if kind in ("servant", "servant_if_prompt") and on_skill_bar and not after_confirm:
            logger(f"[技能] {key} 仍在技能栏且未确认，禁止点己方目标（防开三号状态）")
            return False
        if kind in ("servant", "servant_if_prompt"):
            mode = self.class_profile.get(
                "servant_target_tap_mode",
                getattr(self.cfg, "SERVANT_TARGET_TAP_MODE", "never"))
            if screen is None:
                if not after_confirm:
                    logger(f"[技能] {key} 无目标窗且未确认，取消己方点击")
                    return False
                # 确认后没目标窗：既等「图标转真 CD」，也盯着迟到目标窗（可能 1~3s 才淡入）
                deadline = time.time() + 9.0
                bright_since = None
                dlg_hits = 0
                cd_hits = 0
                while time.time() < deadline:
                    p = self._shot()
                    if p is not None:
                        atk = self._find(p, "battle_attack.png", None) is not None
                        dlg = (not atk) and self._find(
                            p, "target_dialog.png", thr_click) is not None
                        if dlg:
                            dlg_hits += 1
                            cd_hits = 0
                            if mode == "never":
                                self._skills_need_target.add(key)
                                logger(f"[技能] {key} 目标窗出现但配置不点肖像位，返回键取消选目标")
                                self.adb._run(["shell", "input", "keyevent", "4"])
                                time.sleep(0.5)
                                return False
                            if dlg_hits >= 2:
                                logger(f"[技能] {key} 目标窗迟到确认（两帧、攻击键隐藏）→ 点 {idx} 号位")
                                screen = p
                                break
                            time.sleep(0.25)
                            continue
                        dlg_hits = 0
                        b = self._skill_brightness(p, key)
                        lim = self._skill_cd_limit(key)
                        hard = self._skill_hard_cd_limit(key)
                        # 半暗（如 120）可低于 soft lim(131) 但仍远高于真 CD(54–82)；
                        # 必须硬门槛 + 连续两帧，且优先让迟到目标窗有机会。
                        if b is not None and atk and b < hard:
                            cd_hits += 1
                            if cd_hits >= 2:
                                logger(
                                    f"[技能] {key} 确认后图标转CD(亮度={b:.0f} < {hard:.0f})，"
                                    f"判定自身/群体施放成功")
                                return True
                            time.sleep(0.25)
                            continue
                        cd_hits = 0
                        if b is not None and atk and b >= lim:
                            # 刚确认时图标还没变暗，给 3 秒宽限，避免把「施放中」误判成「点击被吞」
                            if bright_since is None:
                                bright_since = time.time()
                            elif time.time() - bright_since >= 3.0:
                                logger(f"[技能] {key} 确认后 3 秒图标仍亮，判定点击未生效，本回合跳过")
                                self._mark_skill_suspect_cd(key, b)
                                self._used_skill_keys.add(key)
                                return False
                        else:
                            bright_since = None
                    time.sleep(0.3)
                if screen is None:
                    p = self._shot()
                    modal = (p is not None
                             and self._find(p, "battle_attack.png", None) is None)
                    if modal and self._find(p, "target_dialog.png", thr_click) is not None:
                        if mode == "never":
                            self._skills_need_target.add(key)
                            logger(f"[技能] {key} 目标窗迟到但 never 模式不点肖像位，返回键取消")
                            self.adb._run(["shell", "input", "keyevent", "4"])
                            time.sleep(0.5)
                            return False
                        logger(f"[技能] {key} 目标窗迟到确认（攻击键隐藏），判真窗补点")
                        screen = p
                    elif modal:
                        logger(f"[技能] {key} 攻击键久不可见且无目标窗，返回键清场后等技能阶段")
                        self.adb._run(["shell", "input", "keyevent", "4"])
                        time.sleep(0.5)
                        return bool(self._wait_skill_phase(timeout=6, initial=0.2, gap=0.3))
                    else:
                        logger(f"[技能] {key} 确认后无目标窗且暂无法判定，以回技能阶段为准")
                        return bool(self._wait_skill_phase(timeout=6, initial=0.2, gap=0.3))
            if mode == "never":
                # 用户 m01922：彻底不点肖像位；真窗出现也取消，杜绝 3 号位状态面板误点
                self._skills_need_target.add(key)
                logger(f"[技能] {key} 目标窗出现但配置不点肖像位，返回键取消选目标")
                self.adb._run(["shell", "input", "keyevent", "4"])
                time.sleep(0.5)
                return False
            if mode == "double_frame":
                p2 = self._shot()
                ok2 = (p2 is not None
                       and self._find(p2, "target_dialog.png", thr_click) is not None
                       and self._find(p2, "battle_attack.png", None) is None)
                if not ok2:
                    logger(f"[技能] {key} 第二帧未复现目标窗或攻击键可见，判假匹配不点")
                    return False
            tap_key = f"servant_target_{idx}"
        elif kind == "enemy":
            tap_key = f"enemy_target_{idx}"
        else:
            logger(f"[技能] {key} 未知目标类型 {kind}")
            return False
        if screen is None:
            logger(f"[技能] {key} 目标窗未识别，仍点固定位 {tap_key}")
        else:
            logger(f"[技能] {key} → 固定目标 {tap_key}")
            time.sleep(0.45)  # 等目标窗淡入动画结束，避免首点落在动画上失效
            # 点之前用当前帧复核一次：目标窗已经消失就不要再点，
            # 否则这一点会落到从者立绘上 -> 弹出从者详情面板（用户 m05330 看到的"状态栏"）
            _fresh = self._shot()
            if _fresh is not None:
                _dlg = self._find(_fresh, "target_dialog.png", thr_click)
                _atk = self._find(_fresh, "battle_attack.png", thr)
                if _dlg is None and _atk is not None:
                    logger(f"[技能] {key} 点击前复核：目标窗已不在（攻击键可见），"
                           "跳过这次点目标，避免误开从者面板")
                    return False
            try:
                cv2.imwrite(os.path.join(
                    self._debug_dir, f"{time.time_ns()}_servant_tap_pre.png"), screen)
            except OSError:
                pass
        self._tap(tap_key)
        time.sleep(0.8)
        try:
            post = self._shot()
            if post is not None:
                cv2.imwrite(os.path.join(
                    self._debug_dir, f"{time.time_ns()}_servant_tap_post.png"), post)
        except OSError:
            pass
        # 校验目标窗确实关闭（点击落在淡入动画上会无效，弹窗残留会阻塞
        # 后续全部操作——用户 m02255 看到的"状态栏"即残留目标窗）
        if screen is not None and kind in ("servant", "servant_if_prompt"):
            dialog_gone = False
            for attempt in range(2):
                time.sleep(0.35)
                chk = self._shot()
                if chk is None or self._find(chk, "target_dialog.png", thr_click) is None:
                    dialog_gone = True
                    break
                # 点「决定」前再复核一次：窗口可能刚好自己关上，此时这一下
                # 会落到从者身上，把「从者详情/宝具详情」面板点出来
                pre = self._shot()
                if pre is None or self._find(pre, "target_dialog.png", thr_click) is None:
                    dialog_gone = True
                    break
                # 选中目标后还要点「决定」确认，否则窗口留着、技能永远不会放出
                logger(f"[技能] {key} 点目标后目标窗仍在(第{attempt + 1}次)，点「决定」确认")
                self._tap("target_confirm")
                time.sleep(0.6)
                chk2 = self._shot()
                if chk2 is None or self._find(chk2, "target_dialog.png", thr_click) is None:
                    dialog_gone = True
                    break
                # 补点前必须确认弹窗此刻真的还在：否则这一点会落到从者身上，
                # 把「从者详情/宝具详情」面板点出来（用户 m04837 看到的多余覆盖层）
                fresh = self._shot()
                if fresh is None or self._find(fresh, "target_dialog.png", thr_click) is None:
                    dialog_gone = True
                    break
                logger(f"[技能] {key} 决定后仍在，补点 {tap_key}")
                self._tap(tap_key)
                time.sleep(0.6)
            if not dialog_gone:
                chk = self._shot()
                if chk is not None and self._find(chk, "target_dialog.png", thr_click) is not None:
                    logger(f"[技能] {key} 两次点击目标窗仍未关闭，按返回取消选目标")
                    self.adb._run(["shell", "input", "keyevent", "4"])
                    time.sleep(0.5)
                    return False
            else:
                logger(f"[技能] {key} 目标窗已关闭（选目标完成）")
        if self._wait_skill_phase(initial=self.cfg.SKILL_TARGET_WAIT, gap=0.25):
            return True
        logger(f"[技能] {key} 点目标后未回技能栏，先查是否被从者详情面板挡住")
        if self._close_servant_panel():
            ok = self._wait_skill_phase(timeout=6, initial=0.2, gap=0.3)
            if ok:
                logger(f"[技能] {key} 关闭从者详情面板后已回技能栏")
                return True
        logger(f"[技能] {key} 仍不在技能栏，按返回关闭残留")
        p = self._shot()
        nm, _ = self._detect(p) if p is not None else (None, None)
        if nm in ("target_dlg", "skill_ok"):
            self.adb._run(["shell", "input", "keyevent", "4"])
        else:
            self._close_master_menu(p)
        time.sleep(0.5)
        ok = self._wait_skill_phase(timeout=6, initial=0.2, gap=0.3)
        return bool(ok)

    def _cast_one_skill(self, key, target):
        # never 模式 + 已实测该技能会弹「请选择对象」：直接跳过，不点击也不开窗
        if target:
            kind = str(target[0])
            mode = self.class_profile.get(
                "servant_target_tap_mode",
                getattr(self.cfg, "SERVANT_TARGET_TAP_MODE", "never"))
            learned = getattr(self, "_skills_need_target", set())
            if mode == "never" and kind.startswith("servant") and key in learned:
                logger(f"[技能] {key} 已知需选己方目标（never 模式），跳过不点")
                return False
        # CD 检测：点击前先检查技能是否可用
        screen = self._shot()
        before = self._skill_brightness(screen, key)
        if not self._is_skill_available(screen, key):
            return False
        logger(f"[技能] 尝试 {key} target={target} 亮度={before:.0f}" if before is not None
               else f"[技能] 尝试 {key} target={target}")
        retries = max(3, int(self.class_profile.get(
            "skill_reclick", self.cfg.SKILL_RECLICK)))
        for attempt in range(retries + 1):
            if attempt:
                # 上一段施放动画会吞掉点击：先等动画结束、攻击键重新出现再点
                time.sleep(0.7)
                self._wait_skill_phase(timeout=5, initial=0.0, gap=0.3)
            else:
                # 首次点击前也等画面静止，避免落在上一个技能的动画里被吞
                self._wait_static(timeout=2.0)
            self._tap(key)
            # 固定流程：点技能 →（有确认就点确认）→（有目标就点固定目标位）
            name, _, _ = self._wait_for(
                ["skill_ok", "target_dlg"], self.cfg.SKILL_DIALOG_WAIT,
                self.cfg.SKILL_DIALOG_RETRY, self.cfg.SKILL_DIALOG_GAP)
            if name is None and attempt < retries:
                # 点击被动画吞掉（实测 9 个技能里 3 个首点无响应）：立刻补点一次，
                # 比走外层「重试」省 5-10 秒
                self._wait_static(timeout=0.8)
                self._tap(key)
                name, _, _ = self._wait_for(
                    ["skill_ok", "target_dlg"], 0.9, 4, 0.3)
            if name == "skill_ok":
                self._tap("skill_confirm")
                time.sleep(self.cfg.SKILL_CONFIRM_WAIT)
                if target:
                    return self._select_skill_target(key, target, after_confirm=True)
                self._wait_skill_phase(timeout=6)
                logger(f"[技能] {key} 已确认释放")
                return True
            if name == "target_dlg":
                if target:
                    return self._select_skill_target(key, target, after_confirm=True)
                logger(f"[技能] {key} 出现目标窗但未配置目标，取消")
                self._dismiss_residual_dialog()
                self._wait_skill_phase(timeout=4)
                return False
            # 无弹窗：留证，便于定位「点了没反应」（截图 + 当前亮度/坐标）
            try:
                dbg = self._shot()
                if dbg is not None:
                    os.makedirs("logs/debug", exist_ok=True)
                    fn = f"logs/debug/{time.time_ns()}_skill_{key}_nodlg.png"
                    cv2.imwrite(fn, dbg)
                    fx, fy = self.cfg.POS.get(key, (0.0, 0.0))
                    logger(f"[技能] {key} 点击后未见弹窗，留证 {fn}（点击点 {int(fx*self.w)},{int(fy*self.h)}）")
            except Exception as _exc:
                logger(f"[技能] 留证失败: {_exc}")
            if target is not None:
                kind = target[0] if isinstance(target, (tuple, list)) else None
                if kind in ("servant", "servant_if_prompt"):
                    # 无弹窗：可能是无确认窗的自身技，轮询图标 CD 判定；
                    # 图标仍亮且攻击键在 = 点击被吞，才进入重试
                    casted = False
                    cd_hits = 0
                    deadline = time.time() + 3.0
                    while time.time() < deadline:
                        p = self._shot()
                        if p is not None:
                            b = self._skill_brightness(p, key)
                            lim = self._skill_cd_limit(key)
                            hard = self._skill_hard_cd_limit(key)
                            atk = self._find(p, "battle_attack.png", None) is not None
                            if b is not None and atk and b < hard:
                                cd_hits += 1
                                if cd_hits >= 2:
                                    casted = True
                                    break
                            elif b is not None and atk and b >= lim:
                                break
                            else:
                                cd_hits = 0
                        time.sleep(0.3)
                    if casted:
                        logger(f"[技能] {key} 无确认窗但图标转CD，视为已放")
                        self._wait_skill_phase(timeout=4)
                        return True
                    logger(f"[技能] {key} 未出确认/目标窗，不点己方，准备重试")
                    self._mark_skill_suspect_cd(key, before)
                else:
                    probe = self._shot()
                    still_bar = (
                        probe is not None
                        and self._find(probe, "battle_attack.png", None) is not None)
                    if still_bar:
                        logger(f"[技能] {key} 未出弹窗且仍在技能栏，准备重试")
                        self._mark_skill_suspect_cd(key, before)
                    else:
                        logger(f"[技能] {key} 未识别弹窗但已离技能栏，按敌方固定位补点")
                        if self._select_skill_target(key, target, after_confirm=True):
                            return True
            else:
                time.sleep(0.35)
                after_screen = self._shot()
                after = self._skill_brightness(after_screen, key)
                if after is not None and before is not None and after + 8 < before:
                    logger(f"[技能] {key} 无确认窗但亮度 {before:.0f}→{after:.0f}，视为已放")
                    self._wait_skill_phase(timeout=4)
                    return True
                if after is not None and not self._is_skill_available(after_screen, key, log=False):
                    logger(f"[技能] {key} 无确认窗且已变暗，视为已放")
                    self._wait_skill_phase(timeout=4)
                    return True
            if attempt < retries:
                logger(f"[技能] {key} 第{attempt + 1}次未确认放出，重试")
                time.sleep(0.5)  # 等上一段动画收尾，防下一次点击被吞
                chk = self._shot()
                if chk is not None and not self._is_skill_available(chk, key, log=False):
                    logger(f"[技能] {key} 重试前复检判 CD，放弃重试")
                    break
        logger(f"[技能] {key} 未确认放出，跳过本回合")
        self._mark_skill_suspect_cd(key, before)
        self._dismiss_residual_dialog()
        self._wait_skill_phase(timeout=4)
        return False

    def _mark_skill_suspect_cd(self, key, reading):
        """点击无响应（无弹窗、图标不变暗）时：把当前观察到的"图标仍亮"读数
        作为基线证据（base=max(base, reading)）——CD 暗读数会被 base-25 拦住，
        而真可用读数(≈reading)仍能通过；旧版 +25 会上修过头把技能后半局禁用。
        CD 结束后若出现更亮读数，经单调上修恢复真基线。"""
        if reading is None:
            return
        base = self._skill_base_brightness.get(key)
        if base is not None and reading > base:
            self._skill_base_brightness[key] = reading
            logger(f"[技能] {key} 亮度基线上修至 {reading:.0f}（观察到亮而未放读数）")

    def _read_wave(self, screen):
        """返回当前波次 1/2/3，识别失败返回 None。"""
        region = getattr(self.cfg, "WAVE_COUNTER_REGION", None)
        if screen is None or not region:
            return None
        if not self._wave_tpl_cache:
            for n in (1, 2, 3):
                path = os.path.join(self.cfg.TEMPLATE_DIR, f"wave_{n}.png")
                self._wave_tpl_cache[n] = (cv2.imread(path)
                                           if os.path.exists(path) else None)
        h, w = screen.shape[:2]
        x0, y0 = int(region[0] * w), int(region[1] * h)
        x1, y1 = int((region[0] + region[2]) * w), int((region[1] + region[3]) * h)
        crop = screen[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        thr = getattr(self.cfg, "WAVE_MATCH_THRESHOLD", 0.85)
        best, best_score = None, thr
        for n, tpl in self._wave_tpl_cache.items():
            if tpl is None or tpl.shape[0] > crop.shape[0] or tpl.shape[1] > crop.shape[1]:
                continue
            res = cv2.matchTemplate(crop, tpl, cv2.TM_CCOEFF_NORMED)
            _, mx, _, _ = cv2.minMaxLoc(res)
            if mx > best_score:
                best, best_score = n, mx
        return best

    def _update_wave(self, screen):
        """读取波次；波次推进视为进度并重置 watchdog（单调递增防抖）。"""
        w = self._read_wave(screen)
        if w is None:
            return
        if self._wave is None or w > self._wave:
            self._wave = w
            if self._battle_start_ts is not None:
                self._battle_start_ts = time.time()
            logger(f"[战斗] 波次检测: 第 {w} 波")

    def _wave_allowed(self, entry):
        """np_templates 条目的 min_wave 门控：未识别到波次时不放（遵守用户规则）。"""
        min_wave = entry.get("min_wave")
        if min_wave is None:
            return True
        return self._wave is not None and self._wave >= min_wave

    # ------------------------------------------------------------------
    # 宝具 / 手牌：展开稳定后识别，宝具优先作为行动一
    def _np_slot_brightness(self, screen, pos):
        """采样宝具槽灰度均值；失败返回 0。"""
        rw, rh = getattr(self.cfg, "NP_SLOT_ROI", (0.07, 0.15))
        x, y = int(pos[0] * self.w), int(pos[1] * self.h)
        gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        roi = gray[max(0, y - int(rh * self.h)):y + int(rh * self.h),
                   max(0, x - int(rw * self.w)):x + int(rw * self.w)]
        if roi.size == 0:
            return 0.0
        return float(roi.mean())

    def _np_slot_present(self, screen, pos):
        """选卡视图上层某号位是否有宝具卡。

        硬阈值：均值 >= NP_SLOT_MIN_BRIGHTNESS。
        软判定：均值 >= NP_SLOT_SOFT_BRIGHTNESS，且比其它两个槽至少亮 25，
        用来捞偏暗但仍明显凸起的真宝具卡（避免再漏 100-118）。
        """
        val = self._np_slot_brightness(screen, pos)
        hard = float(getattr(self.cfg, "NP_SLOT_MIN_BRIGHTNESS", 100))
        if val >= hard:
            return True
        soft = float(getattr(self.cfg, "NP_SLOT_SOFT_BRIGHTNESS", 95))
        if val < soft:
            return False
        others = []
        for p in self._fixed_np_positions():
            if not p:
                continue
            if abs(p[0] - pos[0]) < 0.02 and abs(p[1] - pos[1]) < 0.02:
                continue
            others.append(self._np_slot_brightness(screen, p))
        peer = max(others) if others else 0.0
        return val >= peer + 25.0

    def _fixed_np_positions(self):
        """1/2/3 号位宝具固定坐标（与任意本无关）。优先 POS noble_*，否则 NP_SLOT_POSITIONS。"""
        pos = []
        for i in (1, 2, 3):
            key = f"noble_{i}"
            if key in self.cfg.POS:
                pos.append(self.cfg.POS[key])
            else:
                pos.append(None)
        fallback = list(getattr(self.cfg, "NP_SLOT_POSITIONS", []) or [])
        for i in range(3):
            if pos[i] is None and i < len(fallback):
                pos[i] = fallback[i]
        return pos

    def _np_click_pos(self, servant, raw_loc=None):
        """宝具点击点：固定槽位。模板命中坐标只用来辅助判定，不直接拿去点。"""
        positions = self._fixed_np_positions()
        mode = getattr(self.cfg, "NP_CLICK_MODE", "fixed_slot")
        if mode == "fixed_slot" and servant is not None and 0 <= servant < 3 and positions[servant]:
            return positions[servant]
        if raw_loc is not None and positions[0]:
            # 把飘移的模板点吸附到最近的固定槽
            fx, fy = raw_loc[0], raw_loc[1]
            best_i, best_d = 0, 1e9
            for i, p in enumerate(positions):
                if not p:
                    continue
                d = (p[0] - fx) ** 2 + (p[1] - fy) ** 2
                if d < best_d:
                    best_i, best_d = i, d
            return positions[best_i]
        return raw_loc

    def _recognize_np_on_screen(self, screen, require_hand=False,
                                exclude_servants=None):
        """只识别（不点击）当前策略优先的宝具卡，返回 (loc, color, servant)。
        优先级：模板匹配（命中即记忆该从者槽位坐标）→ 1/2/3 号位记忆位置+存在性采样
        → 轮廓检测 fallback。require_hand=True 时先确认手牌已展开（≥3 张有色），
        防止在 HUD/过场画面对亮背景误判宝具在场。
        exclude_servants: 已点过的号位（0-based），多宝具同放时跳过。"""
        if not getattr(self.cfg, "USE_NOBLE_PHANTASM", False):
            return None, None, None
        if screen is None:
            return None, None, None
        if require_hand:
            known = sum(1 for c in self.card_rec.recognize_hand(screen)
                        if c.color != "?")
            if known < 3:
                return None, None, None

        exclude = set(exclude_servants or ())
        mode = getattr(self.cfg, "NP_DETECT_MODE", "template")
        np_templates = self.class_profile.get("np_templates")
        if np_templates:
            eligible = [entry for entry in np_templates
                        if self.turn >= entry.get("from_turn", 0)]
            eligible = eligible or np_templates[:1]
            # min_wave 门控（如伊丽莎白宝具只在第三波放）；顺带尝试读一次波次
            if self._wave is None:
                self._update_wave(screen)
            eligible = [entry for entry in eligible if self._wave_allowed(entry)]
            if exclude:
                eligible = [entry for entry in eligible
                            if entry.get("servant") not in exclude]
            # 主力宝具优先：preferred_servant 排前面，避免娄希模板抢先于伊丽莎白
            pref = self.class_profile.get("preferred_servant")
            if pref is not None and len(eligible) > 1:
                eligible = sorted(
                    eligible,
                    key=lambda e: (0 if e.get("servant") == pref else 1))
        else:
            eligible = [{"template": self.class_profile.get("np_template"),
                         "servant": 0}]
            if exclude:
                eligible = [entry for entry in eligible
                            if entry.get("servant") not in exclude]

        # 模板匹配路径（命中即记忆槽位坐标，供无模板从者/后续回合使用）
        if mode in ("template", "auto"):
            for candidate in eligible:
                tpl = candidate.get("template")
                if tpl is None:
                    continue
                loc = self._find(screen, tpl, "np_thr")
                if loc is not None:
                    servant = candidate.get("servant", 0)
                    click = self._np_click_pos(servant, loc)
                    self._np_slot_memory[servant] = click
                    color = self.card_rec.recognize_np_color(screen, click)
                    return click, color, servant

        # 位置记忆路径：按配置 1/2/3 号位（或模板记忆修正过的坐标）+ 存在性采样
        positions = getattr(self.cfg, "NP_SLOT_POSITIONS", None)
        if positions:
            for candidate in eligible:
                servant = candidate.get("servant", 0)
                pos = self._np_slot_memory.get(servant)
                if pos is None and servant < len(positions):
                    pos = positions[servant]
                if pos is None:
                    continue
                click = self._np_click_pos(servant, pos)
                if self._np_slot_present(screen, click):
                    color = self.card_rec.recognize_np_color(screen, click)
                    logger(f"[宝具] {servant + 1}号位固定槽命中 @({click[0]:.2f},{click[1]:.2f})")
                    return click, color, servant

        # 轮廓检测 fallback
        if mode in ("contour", "auto"):
            loc = self.vision.find_np_card_contour(screen, self.cfg)
            if loc is not None:
                color = self.card_rec.recognize_np_color(screen, loc)
                logger(f"[宝具] 轮廓检测命中 @({loc[0]:.2f},{loc[1]:.2f})")
                return loc, color, 0  # servant 未知，默认为 0

        return None, None, None

    def _accept_hand(self, np_loc, np_color, np_servant, hand):
        """采纳识别结果：若有宝具先点对应号位固定坐标（行动一）。

        若 class_profile.np_release_all_ready=True：同回合继续点其它已就绪宝具
        （最多占满 3 个出卡位）；默认 False，保持旧配置每回合只放 1 张宝具。
        """
        np_cards = []
        # 点卡前必须确认真的在选卡视图（攻击键消失 + 右下角「◀返回」在场），
        # 否则固定槽坐标会落到从者精灵上打开详情页（run27 卡死教训）
        probe = self._shot()
        tpl_atk, thr_atk = UI_TEMPLATES["attack"]
        in_select = (probe is not None
                     and self._find(probe, tpl_atk, thr_atk) is None
                     and self._find(probe, "select_back.png", 0.85) is not None)
        if not in_select:
            logger("[宝具] 当前不在选卡视图（右下返回键不在画面），跳过宝具点击")
            return np_cards, hand

        def _click_one(loc, color, servant):
            click = self._np_click_pos(servant, loc)
            if click is None:
                return False
            if servant is not None and 0 <= servant < 3:
                key = f"noble_{servant + 1}"
                if key in self.cfg.POS:
                    self._tap(key)
                else:
                    self._tap_ratio(click[0], click[1])
            else:
                self._tap_ratio(click[0], click[1])
            time.sleep(self.cfg.NOBLE_CLICK_WAIT)
            np_cards.append(Card(-1, color, servant))
            slot = (servant + 1) if servant is not None else "?"
            logger(f"[宝具] 已选{slot}号位固定槽 @({click[0]:.2f},{click[1]:.2f})，卡色={color}")
            return True

        if np_loc is not None or np_servant is not None:
            if not _click_one(np_loc, np_color, np_servant):
                return np_cards, hand

        # 仅 profile 显式开启时才继续捞同回合其它就绪宝具（不影响 daily/assassin 旧行为）
        if self.class_profile.get("np_release_all_ready", False):
            used = {c.servant for c in np_cards if c.servant is not None}
            while len(np_cards) < 3:
                screen = self._shot()
                if screen is None:
                    break
                still = (self._find(screen, tpl_atk, thr_atk) is None
                         and self._find(screen, "select_back.png", 0.85) is not None)
                if not still:
                    break
                loc2, color2, serv2 = self._recognize_np_on_screen(
                    screen, require_hand=False, exclude_servants=used)
                if loc2 is None and serv2 is None:
                    break
                if not _click_one(loc2, color2, serv2):
                    break
                if serv2 is not None:
                    used.add(serv2)
        return np_cards, hand

    def _settle_np(self, screen, np_loc, np_color, np_servant, hand):
        """手牌稳定后若没认到宝具，等卡 pop-in 动画再重认（run10：pop-in 延迟导致模板漏检）。"""
        if np_loc is not None or np_servant is not None:
            return np_loc, np_color, np_servant, hand
        for _ in range(2):
            time.sleep(0.35)
            screen2 = self._shot()
            if screen2 is None:
                continue
            loc2, color2, serv2 = self._recognize_np_on_screen(
                screen2, require_hand=True)
            if loc2 is not None or serv2 is not None:
                logger("[宝具] pop-in 延迟后补识别到宝具卡")
                return loc2, color2, serv2, hand
        return np_loc, np_color, np_servant, hand

    def _recognize_hand_stable(self):
        """手牌完全展开后，连续 CARD_HAND_CONFIRM 帧识别一致才采纳；
        多帧都不一致时采用「识别出卡色最多」的一帧兜底。返回 (np_cards, hand)。"""
        prev_colors = None
        consistent = 0
        best = (-1, None, None)  # (known, hand, (np_loc,np_color,np_servant))
        for _ in range(self.cfg.CARD_HAND_RETRY):
            screen = self._shot()
            if screen is None:
                time.sleep(self.cfg.CARD_HAND_GAP)
                continue
            np_loc, np_color, np_servant = self._recognize_np_on_screen(
                screen, require_hand=True)
            hand = self.card_rec.recognize_hand(screen)
            colors = [c.color for c in hand]
            known = sum(1 for c in colors if c != "?")
            if known > best[0]:
                best = (known, hand, (np_loc, np_color, np_servant))
            if known >= 3:
                if prev_colors is not None and colors == prev_colors:
                    consistent += 1
                else:
                    consistent = 0
                if consistent >= self.cfg.CARD_HAND_CONFIRM - 1:
                    np_loc, np_color, np_servant, hand = self._settle_np(
                        screen, np_loc, np_color, np_servant, hand)
                    return self._accept_hand(np_loc, np_color, np_servant, hand)
            prev_colors = colors
            time.sleep(self.cfg.CARD_HAND_GAP)
        known, hand, (np_loc, np_color, np_servant) = best
        if hand is None:
            return [], None
        if known < 2:
            logger(f"[手牌] 识别不足（仅{known}张），放弃本次选卡避免盲点误触")
            return [], None
        logger(f"[手牌] 未完全稳定，采用识别最多的一帧（{known}张）")
        np_loc, np_color, np_servant, hand = self._settle_np(
            None, np_loc, np_color, np_servant, hand)
        return self._accept_hand(np_loc, np_color, np_servant, hand)

    def _frame_static(self, screen, wait=0.45, max_diff=1.5):
        """判断画面是否静止（亚采样灰度差）。战斗加载/波次登场动画会被排除。"""
        if screen is None:
            return False
        time.sleep(wait)
        nxt = self._shot()
        if nxt is None or nxt.shape != screen.shape:
            return False
        try:
            a = cv2.resize(screen, (240, 135))
            b = cv2.resize(nxt, (240, 135))
            return float(cv2.absdiff(a, b).mean()) <= max_diff
        except cv2.error:
            return False

    def _wait_static(self, timeout=2.5, wait=0.30, max_diff=1.2):
        """等画面静止再点技能图标：上一个技能的施放动画期点击会被吞，
        实测每次被吞要白等 5-9 秒重试（run43 skill_5/6/8）。"""
        deadline = time.time() + timeout
        s = self._shot()
        while time.time() < deadline:
            if s is not None and self._frame_static(s, wait=wait, max_diff=max_diff):
                return True
            s = self._shot()
        return False

    def _still_in_card_select(self, screen=None):
        """仍停在选卡界面：攻击键不可见 + 有色卡 ≥3 + 右下角「◀返回」按钮在场。
        返回按钮是选卡视图独有的强特征（选满 3 张后会变成橙色攻击键）——
        加载画面/波次登场动画/从者详情页都不会命中，避免误点从者精灵。"""
        if screen is None:
            screen = self._shot()
        if screen is None:
            return False
        tpl, thr = UI_TEMPLATES["attack"]
        if self._find(screen, tpl, thr) is not None:
            return False
        if self._find(screen, "select_back.png", 0.85) is None:
            return False
        hand = self.card_rec.recognize_hand(screen)
        return sum(1 for c in hand if c.color != "?") >= 3

    def _pick_cards(self, slots, n):
        """点 n 张指令卡并校验：点完等待，若仍停在选卡界面则补点未点过的卡，直到选满。"""
        if not self._still_in_card_select():
            logger("[选卡] 未检测到选卡界面，放弃本次点击避免误触 HUD/弹窗")
            return
        picked = list(slots)
        for slot in slots:
            key = f"card_{slot}"
            fx, fy = self.cfg.POS[key]
            logger(f"[选卡] 点固定槽 {key} @({fx:.3f},{fy:.3f})")
            self._tap(key)
            time.sleep(self.cfg.CARD_CLICK_GAP)
        # 轮询等待选卡视图消失（选满进入攻击），比固定 2.5s 更早继续
        for _ in range(8):
            time.sleep(0.3)
            if not self._still_in_card_select():
                logger("[选卡] 已选满 3 张，进入攻击")
                return
        # 没点满：补点尚未点过的槽位（再点已选卡会取消，故只点没点过的）
        logger(f"[选卡] 未点满（计划{slots}），开始补点")
        for s in [x for x in range(5) if x not in picked]:
            self._tap(f"card_{s}")
            picked.append(s)
            time.sleep(0.6)
            if not self._still_in_card_select():
                logger(f"[选卡] 补点 card_{s} 后选满")
                return
        logger("[选卡] 补点后仍未离开选卡界面，按返回键关闭残留界面（可能是从者详情页）")
        self._shot("pick_not_full")
        self.adb._run(["shell", "input", "keyevent", "4"])
        time.sleep(1.0)

    # ------------------------------------------------------------------
    # 结算：点画面推进羁绊/经验，战利品点下一步，直到连续出击弹窗
    def settle(self):
        logger("[结算] 推进到连续出击弹窗…")
        deadline = time.time() + self.cfg.RESULT_TIMEOUT
        while time.time() < deadline:
            name, _, _ = self._wait_any(["rerun", "loot", "support", "party"],
                                        timeout=6, gap=self.cfg.STATE_POLL,
                                        advance=True)
            if name == "rerun":
                logger("[结算] 出现连续出击弹窗")
                return True
            if name == "loot":
                self._tap("result_next")
                time.sleep(1.0)
            elif name in ("support", "party"):
                # 某些情况下直接跳到了助战（说明已自动重开）
                return True
            else:
                # 检查异常状态后再点屏幕推进
                if name is None:
                    # _wait_any 返回 None 时 screen 可能为 None，重新截一张
                    check_screen = self._shot()
                    if check_screen is not None:
                        kind, _desc = self._detect_anomaly(check_screen)
                        if kind is not None:
                            if not self._process_anomaly(check_screen):
                                return False
                            deadline = time.time() + self.cfg.RESULT_TIMEOUT
                # 羁绊/经验/战斗结束：点屏幕推进
                self._tap("result_tap")
                time.sleep(1.0)
        self._shot("settle_timeout")
        logger("[结算] 等待连续出击超时。")
        return False

    # ------------------------------------------------------------------
    # 主循环
    def run(self, start_in_battle=False):
        target = self.cfg.NODE_COUNT
        mode = "从当前战斗开始" if start_in_battle else "自动进入关卡"
        logger(f"[启动] {mode}，目标 {target if target else '无限'} 本。Ctrl+C 随时停止。")
        try:
            if not start_in_battle:
                if not self.enter_quest():
                    return
            while True:
                ok = self.battle()
                if not ok:
                    # 检查是否因为 AP 耗尽而停止（非故障）
                    apple_cfg = getattr(self.cfg, "APPLE_CONFIG", {})
                    all_exhausted = all(
                        self._apples_used.get(k, 0) >= v.get("count", 0)
                        for k, v in apple_cfg.items()
                        if k != "enabled" and v.get("count", 0) > 0)
                    if all_exhausted and any(v.get("count", 0) > 0
                                             for k, v in apple_cfg.items()
                                             if k != "enabled"):
                        logger("[完成] 苹果已按计划消耗完毕，正常停止")
                    else:
                        self._stop_and_dump()
                    break
                if not self.settle():
                    logger("[结算] 结算流程未完成（可能活动次数不足或界面异常），停止刷本")
                    self._stop_and_dump()
                    break
                self.count += 1
                logger(f"[完成] 第 {self.count} 本刷完，累计 {self.count} 本")

                if target and self.count >= target:
                    logger(f"[完成] 已达目标 {target} 本，点「关闭」结束。")
                    self._tap("rerun_close")
                    break
                logger("[循环] 点「连续出击」继续下一本")
                self._tap("rerun")
                # 等助战页出来再进 enter_quest，避免弹窗坐标二次误点助战卡
                time.sleep(2.5)
                # 连续出击后过场易误判攻击键；必须先走完助战再认进战
                if not self.enter_quest(require_support=True):
                    break
        except EmulatorDown as exc:
            logger(f"[停止] {exc} —— 已刷 {self.count} 本，请检查雷电模拟器是否还在运行")
        except KeyboardInterrupt:
            logger(f"\n[停止] 手动中断。已刷 {self.count} 本。")
        finally:
            # 停止异步截图线程
            if self._async_shot is not None:
                self._async_shot.stop()
            # 打印苹果消耗统计
            used_parts = [f"{k}:{v}" for k, v in self._apples_used.items() if v > 0]
            if used_parts:
                logger(f"[统计] 本次消耗苹果: {', '.join(used_parts)}")
            logger("[结束] 脚本已退出。")

    def _stop_and_dump(self):
        self._shot("stuck_or_ap")
        logger("[停止] 调试截图按配置写入 logs/debug/，并受同类截图限频和目录保留上限控制。")
