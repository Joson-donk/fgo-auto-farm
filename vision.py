# -*- coding: utf-8 -*-
"""OpenCV 视觉识别：模板匹配 + 区域主色检测。"""
import os

import cv2
import numpy as np


class Vision:
    def __init__(self, template_dir, threshold=0.82):
        self.template_dir = template_dir
        self.threshold = threshold
        self.cache = {}

    # ------------------------------------------------------------------
    def load_template(self, name):
        """加载模板图（灰度），带缓存。"""
        if name in self.cache:
            return self.cache[name]
        path = os.path.join(self.template_dir, name)
        if not os.path.exists(path):
            return None
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        self.cache[name] = img
        return img

    def find(self, screen_bgr, template_name, threshold=None, region=None):
        """在截图（或指定区域）里找模板，返回全图坐标 (中心x, 中心y) 或 None。
        region: (x, y, w, h) 比例坐标，限定搜索范围以提速。"""
        tpl = self.load_template(template_name)
        if tpl is None:
            return None
        thr = threshold if threshold is not None else self.threshold
        gray = (screen_bgr if screen_bgr.ndim == 2
                else cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY))

        # 区域裁剪
        offset_x, offset_y = 0, 0
        if region is not None:
            rx, ry, rw, rh = region
            h_scr, w_scr = gray.shape[:2]
            x1 = int(rx * w_scr)
            y1 = int(ry * h_scr)
            x2 = min(w_scr, int((rx + rw) * w_scr))
            y2 = min(h_scr, int((ry + rh) * h_scr))
            gray = gray[y1:y2, x1:x2]
            offset_x, offset_y = x1, y1

        h, w = tpl.shape[:2]
        if h > gray.shape[0] or w > gray.shape[1]:
            return None
        try:
            res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
        except cv2.error as exc:
            # 内存不足（模拟器占用过高时常见）不应让整局崩掉，按未命中处理
            print(f"[vision] 模板匹配失败（可能内存不足）: {exc}")
            return None
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val >= thr:
            return max_loc[0] + w / 2 + offset_x, max_loc[1] + h / 2 + offset_y
        return None

    def find_peaks(self, screen_bgr, template_name, floor=0.5, max_peaks=6,
                   region=None):
        """多个互不重叠的模板峰，含分数。助战选人用它拒绝歧义命中。"""
        from support_select import find_template_peaks
        tpl = self.load_template(template_name)
        if tpl is None:
            return []
        return find_template_peaks(
            screen_bgr, tpl, floor=floor, max_peaks=max_peaks, region=region)

    def find_all(self, screen_bgr, template_name, threshold=None):
        """返回屏幕中所有不重叠的模板中心坐标。"""
        tpl = self.load_template(template_name)
        if tpl is None:
            return []
        thr = threshold if threshold is not None else self.threshold
        gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
        h, w = tpl.shape[:2]
        if h > gray.shape[0] or w > gray.shape[1]:
            return []
        scores = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
        matches = []
        while True:
            _, score, _, loc = cv2.minMaxLoc(scores)
            if score < thr:
                break
            x, y = loc
            matches.append((x + w / 2, y + h / 2))
            left = max(0, x - w // 2)
            top = max(0, y - h // 2)
            right = min(scores.shape[1], x + w // 2 + 1)
            bottom = min(scores.shape[0], y + h // 2 + 1)
            scores[top:bottom, left:right] = -1
        return matches

    # ------------------------------------------------------------------
    def region_mean_color(self, screen_bgr, x, y, w, h):
        """返回 (r,g,b) 区域平均色（BGR）。"""
        roi = screen_bgr[int(y):int(y + h), int(x):int(x + w)]
        if roi.size == 0:
            return None
        bgr = roi.mean(axis=(0, 1))
        return tuple(round(v, 1) for v in bgr)

    def color_close(self, c1, c2, tol=18):
        """两个 BGR 颜色是否接近。"""
        if c1 is None or c2 is None:
            return False
        return all(abs(a - b) <= tol for a, b in zip(c1, c2))

    # ------------------------------------------------------------------
    def find_np_card_contour(self, screen_bgr, cfg):
        """通过轮廓检测在画面上半区查找宝具竖卡。
        返回 (中心fx, 中心fy) 比例坐标，或 None。
        原理：宝具卡是画面中唯一的竖向矩形卡片，宽高比 > 1.2。"""
        h_scr, w_scr = screen_bgr.shape[:2]
        region = getattr(cfg, "NP_CONTOUR_SEARCH_REGION", (0.15, 0.10, 0.70, 0.45))
        rx, ry, rw, rh = region
        x1 = int(rx * w_scr)
        y1 = int(ry * h_scr)
        x2 = int((rx + rw) * w_scr)
        y2 = int((ry + rh) * h_scr)
        roi = screen_bgr[y1:y2, x1:x2]
        if roi.size == 0:
            return None

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        canny_lo, canny_hi = getattr(cfg, "NP_CONTOUR_CANNY", (50, 150))
        edges = cv2.Canny(gray, canny_lo, canny_hi)
        # 膨胀使边缘闭合
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        edges = cv2.dilate(edges, kernel, iterations=1)

        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        min_area = getattr(cfg, "NP_CONTOUR_MIN_AREA", 3000)
        max_area = getattr(cfg, "NP_CONTOUR_MAX_AREA", 80000)
        min_aspect = getattr(cfg, "NP_CONTOUR_MIN_ASPECT", 1.2)

        best = None
        best_area = 0
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                continue
            x, y, w, h = cv2.boundingRect(cnt)
            if h == 0:
                continue
            aspect = h / w  # 竖卡：高 > 宽
            if aspect < min_aspect:
                continue
            # 取面积最大的竖卡候选
            if area > best_area:
                best_area = area
                # 转换为全图比例坐标
                cx = (x1 + x + w / 2) / w_scr
                cy = (y1 + y + h / 2) / h_scr
                best = (cx, cy)

        return best
