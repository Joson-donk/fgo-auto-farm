# -*- coding: utf-8 -*-
"""
手牌识别：识别 5 张手牌的卡色 (B/A/Q) 与从者归属。

卡色：对每个手牌槽的色块采样区做 HSV 颜色统计（Buster 红 / Arts 蓝 / Quick 绿）。
从者：对卡牌底部从者头像区做模板匹配 (servant_1/2/3.png)，无模板则识别不到、
      此时"同从者三连"自动跳过，但颜色连携仍可用。
"""
import os

import cv2
import numpy as np


# 卡色 -> 中文名
COLOR_NAME = {"B": "红卡(Buster)", "A": "蓝卡(Arts)", "Q": "绿卡(Quick)", "?": "未知"}


class Card:
    __slots__ = ("slot", "color", "servant", "click_pos")

    def __init__(self, slot, color, servant=None, click_pos=None):
        self.slot = slot          # 0~4 从左到右
        self.color = color        # 'B' / 'A' / 'Q' / '?'
        self.servant = servant    # 0/1/2 从者编号，或 None
        self.click_pos = click_pos  # 宝具点击坐标 (fx,fy)，普通手牌为 None

    def __repr__(self):
        s = f"槽{self.slot}:{self.color}"
        if self.servant is not None:
            s += f"/从者{self.servant + 1}"
        return s


# HSV 阈值（OpenCV H 为 0~179；S/V 0~255），按横屏卡名星形实测校准
_HSV_RANGES = {
    # 力击 Buster（红/橙），H 跨 0
    "B": [((0, 100, 100), (12, 255, 255)),
          ((168, 100, 100), (179, 255, 255))],
    # 技击 Arts（蓝）
    "A": [((95, 90, 70), (130, 255, 255))],
    # 迅击 Quick（绿）
    "Q": [((35, 80, 70), (90, 255, 255))],
}


class CardRecognizer:
    def __init__(self, cfg, vision):
        self.cfg = cfg
        self.vision = vision
        self._servant_tpl = {}
        if getattr(cfg, "USE_SERVANT_TEMPLATES", True) and vision is not None:
            for i in range(3):
                tpl = vision.load_template(f"servant_{i + 1}.png")
                if tpl is not None:
                    self._servant_tpl[i] = tpl

    # ------------------------------------------------------------------
    def _roi_at(self, screen, fx, fy, box):
        """以 (fx,fy) 为中心 + 偏移框，返回像素 ROI（已裁到图像边界内）。"""
        h, w = screen.shape[:2]
        dx, dy, bw, bh = box
        cx, cy = fx + dx, fy + dy
        x1 = int((cx - bw / 2) * w)
        y1 = int((cy - bh / 2) * h)
        x2 = int((cx + bw / 2) * w)
        y2 = int((cy + bh / 2) * h)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        return screen[y1:y2, x1:x2]

    def _roi(self, screen, slot, box):
        """根据手牌槽中心 + 偏移框，返回像素 ROI。"""
        fx, fy = self.cfg.POS[f"card_{slot}"]
        return self._roi_at(screen, fx, fy, box)

    def recognize_np_color(self, screen, center):
        """识别宝具卡中心处的卡色（B/A/Q/?）。center=(fx, fy)。"""
        roi = self._roi_at(screen, center[0], center[1], self.cfg.NP_COLOR_BOX)
        return self._recognize_color(roi)

    def _recognize_color(self, roi):
        """统计 ROI 内 B/A/Q 像素占比，返回占比最高且过阈值的卡色。"""
        if roi is None or roi.size == 0:
            return "?"
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        total = roi.shape[0] * roi.shape[1]
        best, best_ratio = "?", 0.0
        for color, ranges in _HSV_RANGES.items():
            cnt = 0
            for lo, hi in ranges:
                mask = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
                cnt += int(cv2.countNonZero(mask))
            ratio = cnt / max(1, total)
            if ratio > best_ratio:
                best, best_ratio = color, ratio
        if best_ratio >= self.cfg.CARD_COLOR_RATIO_MIN:
            return best
        return "?"

    def _recognize_servant(self, roi):
        """对从者头像 ROI 做模板匹配，返回 0/1/2 或 None。"""
        if roi is None or roi.size == 0 or not self._servant_tpl:
            return None
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        best_idx, best_val = None, -1.0
        for idx, tpl in self._servant_tpl.items():
            if tpl.shape[0] > gray.shape[0] or tpl.shape[1] > gray.shape[1]:
                continue
            res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            if max_val > best_val:
                best_idx, best_val = idx, max_val
        if best_val >= self.cfg.TEMPLATE_THRESHOLD:
            return best_idx
        return None

    # ------------------------------------------------------------------
    def recognize_hand(self, screen):
        """识别 5 张手牌，返回长度 5 的 Card 列表。"""
        hand = []
        for slot in range(5):
            color_roi = self._roi(screen, slot, self.cfg.CARD_COLOR_BOX)
            color = self._recognize_color(color_roi)
            servant = None
            if self._servant_tpl:
                sv_roi = self._roi(screen, slot, self.cfg.CARD_SERVANT_BOX)
                servant = self._recognize_servant(sv_roi)
            hand.append(Card(slot, color, servant))
        return hand

    def draw_debug(self, screen, hand):
        """在截图上标注识别结果，返回带标注的图（用于 calibrate 调试）。"""
        img = screen.copy()
        h, w = img.shape[:2]
        for card in hand:
            fx, fy = self.cfg.POS[f"card_{card.slot}"]
            x, y = int(fx * w), int(fy * h)
            col = {"B": (0, 0, 255), "A": (255, 0, 0), "Q": (0, 200, 0), "?": (128, 128, 128)}[card.color]
            cv2.rectangle(img, (x - 60, y - 70), (x + 60, y + 90), col, 3)
            txt = card.color + (f"/S{card.servant + 1}" if card.servant is not None else "")
            cv2.putText(img, txt, (x - 40, y - 80),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2)
        return img
