# -*- coding: utf-8 -*-
"""助战选人：纯函数。匹配排序、点选前校验、翻页/刷新预算。

不依赖 ADB。屏幕坐标均为像素；区域 (x, y, w, h) 为相对整屏的比例。

一张卡会被点中，必须同时满足：
  * 相关系数达到阈值；
  * 名称裁块的结构分、字形重合都像模板（不是一块相似的蓝底）；
  * 短名字右侧不能再接着文字（否则是「美杜莎〔Lily〕」这类前缀误命中）；
  * 名称完整落在屏幕内。
同屏多张都完整合格（例如列表里好几个同名助战）时，取分数最高的那张；
只有「一张合格 + 另有近似假命中」才判 ambiguous，避免乱点前缀/假峰。
调用方还要再等一帧确认，并在点击后确认已经离开助战列表。
"""
from dataclasses import dataclass, replace

import cv2
import numpy as np


@dataclass(frozen=True)
class TemplatePeak:
    """matchTemplate 的一个峰。left/top 是模板左上角，x/y 是中心。"""
    x: float
    y: float
    score: float
    left: int
    top: int


@dataclass(frozen=True)
class SupportCandidate:
    x: float
    y: float
    ncc: float
    structure: float
    glyph_recall: float
    trailing_ratio: float
    clipped: bool


@dataclass(frozen=True)
class RankResult:
    """status: accept | reject | ambiguous。reason 是稳定的机器可读原因。"""
    status: str
    chosen: SupportCandidate | None
    best: SupportCandidate | None
    runner_up: SupportCandidate | None
    reason: str


@dataclass(frozen=True)
class SupportGates:
    ncc_threshold: float = 0.90
    structure_min: float = 0.80
    glyph_min: float = 0.70
    ambiguity_margin: float = 0.05
    trailing_max: float = 0.08
    trailing_width_px: int = 40
    short_name_px: int = 140
    edge_margin_px: int = 24


@dataclass(frozen=True)
class SupportRetryPolicy:
    max_refreshes: int = 3
    max_scrolls_per_pass: int = 12
    max_stagnant: int = 2
    max_clicks_per_candidate: int = 2


@dataclass(frozen=True)
class SearchProgress:
    refreshes_used: int = 0
    scrolls_this_pass: int = 0
    stagnant_scrolls: int = 0
    clicks_on_candidate: int = 0


def gates_from_config(cfg):
    return SupportGates(
        ncc_threshold=cfg.SUPPORT_TEMPLATE_THRESHOLD,
        structure_min=getattr(cfg, "SUPPORT_STRUCTURE_MIN", 0.80),
        glyph_min=getattr(cfg, "SUPPORT_GLYPH_MIN", 0.70),
        ambiguity_margin=getattr(cfg, "SUPPORT_AMBIGUITY_MARGIN", 0.05),
        trailing_max=getattr(cfg, "SUPPORT_TRAILING_MAX", 0.08),
        trailing_width_px=getattr(cfg, "SUPPORT_TRAILING_WIDTH_PX", 40),
        short_name_px=getattr(cfg, "SUPPORT_SHORT_NAME_PX", 140),
        edge_margin_px=getattr(cfg, "SUPPORT_EDGE_MARGIN_PX", 10),
    )


def policy_from_config(cfg):
    return SupportRetryPolicy(
        max_refreshes=getattr(cfg, "SUPPORT_REFRESH_MAX", 3),
        max_scrolls_per_pass=getattr(cfg, "SUPPORT_SCROLLS_PER_PASS", 12),
        max_stagnant=getattr(cfg, "SUPPORT_STAGNANT_SCROLLS", 2),
        max_clicks_per_candidate=getattr(cfg, "SUPPORT_CLICKS_PER_CANDIDATE", 2),
    )


def to_gray(image):
    if image is None:
        return None
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _finite(value):
    value = float(value)
    if value != value:  # NaN
        return 0.0
    return value


def ncc_same_size(template_gray, crop_gray):
    """两张同尺寸图的相关系数。尺寸不同或方差过低时返回 0。"""
    if crop_gray is None or template_gray is None:
        return 0.0
    if crop_gray.shape != template_gray.shape:
        return 0.0
    if float(crop_gray.std()) < 1e-3 or float(template_gray.std()) < 1e-3:
        return 0.0
    return _finite(cv2.matchTemplate(
        crop_gray, template_gray, cv2.TM_CCOEFF_NORMED)[0, 0])


def structure_score(template_gray, crop_gray):
    """去掉大块底色后的相关系数。相似蓝底、不同白字会明显低于原图 NCC。"""
    if crop_gray is None or template_gray is None:
        return 0.0
    if crop_gray.shape != template_gray.shape:
        return 0.0
    template_hp = template_gray.astype(np.float32)
    crop_hp = crop_gray.astype(np.float32)
    template_hp = template_hp - cv2.GaussianBlur(template_hp, (0, 0), 1.2)
    crop_hp = crop_hp - cv2.GaussianBlur(crop_hp, (0, 0), 1.2)
    if float(template_hp.std()) < 1e-3 or float(crop_hp.std()) < 1e-3:
        return 0.0
    return _finite(cv2.matchTemplate(
        crop_hp, template_hp, cv2.TM_CCOEFF_NORMED)[0, 0])


def _glyph_threshold(template_gray):
    low = float(np.percentile(template_gray, 50))
    high = float(np.percentile(template_gray, 90))
    return (low + high) / 2.0


def glyph_recall(template_gray, crop_gray):
    """模板里亮字像素，在裁块同一位置也亮的比例。"""
    if crop_gray is None or template_gray is None:
        return 0.0
    if crop_gray.shape != template_gray.shape:
        return 0.0
    threshold = _glyph_threshold(template_gray)
    template_mask = template_gray >= threshold
    count = int(template_mask.sum())
    if count < 20:
        return 0.0
    crop_mask = crop_gray >= threshold
    return float(np.logical_and(template_mask, crop_mask).sum()) / count


def trailing_glyph_ratio(screen_gray, template_gray, left, top, width):
    """模板右缘外侧的亮字比例。短名字后面如果还接着字，就是另一个从者。"""
    if width <= 0:
        return 0.0
    name_h, name_w = template_gray.shape[:2]
    screen_h, screen_w = screen_gray.shape[:2]
    x1 = left + name_w
    y1 = top
    y2 = min(screen_h, y1 + name_h)
    x2 = min(screen_w, x1 + width)
    visible = x2 - x1
    if y2 <= y1 or visible < max(8, width // 3):
        # 右侧已经出屏，无法证明名字到此结束。短名宁可拒绝。
        return 1.0
    band = screen_gray[y1:y2, x1:x2]
    threshold = _glyph_threshold(template_gray)
    return float((band >= threshold).mean())


def is_clipped(left, top, template_w, template_h, screen_w, screen_h, margin):
    if left < margin or top < margin:
        return True
    if left + template_w > screen_w - margin:
        return True
    if top + template_h > screen_h - margin:
        return True
    return False


def find_template_peaks(screen_bgr, template_gray, floor=0.5, max_peaks=6,
                        region=None):
    """返回不超过 max_peaks 个互不重叠的峰，坐标换算回整图。"""
    gray = to_gray(screen_bgr)
    template_gray = to_gray(template_gray)
    if gray is None or template_gray is None or max_peaks <= 0:
        return []
    offset_x, offset_y = 0, 0
    if region is not None:
        rx, ry, rw, rh = region
        screen_h, screen_w = gray.shape[:2]
        x1 = max(0, int(rx * screen_w))
        y1 = max(0, int(ry * screen_h))
        x2 = min(screen_w, int((rx + rw) * screen_w))
        y2 = min(screen_h, int((ry + rh) * screen_h))
        if x2 <= x1 or y2 <= y1:
            return []
        gray = gray[y1:y2, x1:x2]
        offset_x, offset_y = x1, y1
    template_h, template_w = template_gray.shape[:2]
    if template_h > gray.shape[0] or template_w > gray.shape[1]:
        return []
    scores = cv2.matchTemplate(gray, template_gray, cv2.TM_CCOEFF_NORMED)
    peaks = []
    pad_x = max(2, int(template_w * 0.75))
    pad_y = max(2, int(template_h * 0.75))
    for _ in range(max_peaks):
        _, score, _, loc = cv2.minMaxLoc(scores)
        score = _finite(score)
        if score < floor:
            break
        left, top = int(loc[0]), int(loc[1])
        peaks.append(TemplatePeak(
            x=left + template_w / 2.0 + offset_x,
            y=top + template_h / 2.0 + offset_y,
            score=score,
            left=left + offset_x,
            top=top + offset_y,
        ))
        y_lo = max(0, top - pad_y)
        y_hi = min(scores.shape[0], top + pad_y + 1)
        x_lo = max(0, left - pad_x)
        x_hi = min(scores.shape[1], left + pad_x + 1)
        scores[y_lo:y_hi, x_lo:x_hi] = -1
    return peaks


def assess_peak(screen_bgr, template_gray, peak, gates):
    """用裁块重新计算相关系数，不单独相信调用方传入的分数。"""
    screen_gray = to_gray(screen_bgr)
    template_gray = to_gray(template_gray)
    template_h, template_w = template_gray.shape[:2]
    screen_h, screen_w = screen_gray.shape[:2]
    clipped = is_clipped(
        peak.left, peak.top, template_w, template_h,
        screen_w, screen_h, gates.edge_margin_px)
    crop = None
    if (0 <= peak.left and 0 <= peak.top
            and peak.left + template_w <= screen_w
            and peak.top + template_h <= screen_h):
        crop = screen_gray[peak.top:peak.top + template_h,
                           peak.left:peak.left + template_w]
    crop_ncc = ncc_same_size(template_gray, crop)
    ncc = min(_finite(peak.score), crop_ncc) if crop is not None else 0.0
    return SupportCandidate(
        x=peak.x,
        y=peak.y,
        ncc=ncc,
        structure=structure_score(template_gray, crop),
        glyph_recall=glyph_recall(template_gray, crop),
        trailing_ratio=trailing_glyph_ratio(
            screen_gray, template_gray, peak.left, peak.top,
            gates.trailing_width_px),
        clipped=clipped,
    )


def _rejection_reason(candidate, template_w, gates):
    if candidate.clipped:
        return "clipped"
    if candidate.ncc < gates.ncc_threshold:
        return "below_threshold"
    if candidate.structure < gates.structure_min:
        return "weak_structure"
    if candidate.glyph_recall < gates.glyph_min:
        return "weak_glyph"
    if (template_w < gates.short_name_px
            and candidate.trailing_ratio > gates.trailing_max):
        return "trailing_name"
    return None


def _looks_like_template(candidate, gates):
    """达到阈值，且裁块确实像这张名称模板（不论尾部是否还有字）。"""
    if candidate.clipped:
        return False
    return (candidate.ncc >= gates.ncc_threshold
            and candidate.structure >= gates.structure_min
            and candidate.glyph_recall >= gates.glyph_min)


def rank_support_candidates(candidates, template_w, gates):
    """合格卡取最高分 accept。多张同名都过线时点最好的那张；
    一张合格却另有近似假命中时仍 ambiguous，避免乱点前缀/假峰。"""
    if not candidates:
        return RankResult("reject", None, None, None, "no_peak")
    ordered = sorted(candidates, key=lambda item: (item.ncc, item.structure),
                     reverse=True)
    best = ordered[0]
    runner_up = ordered[1] if len(ordered) > 1 else None
    qualified = [item for item in ordered
                 if _rejection_reason(item, template_w, gates) is None]
    # 列表里多个同名助战：全部完整合格 → 点最高分，不再因「不唯一」拒点
    if len(qualified) >= 2:
        return RankResult(
            "accept", qualified[0], qualified[0], qualified[1], "accept")
    if len(qualified) == 1:
        chosen = qualified[0]
        rivals_of = [
            item for item in ordered
            if item is not chosen and (
                _looks_like_template(item, gates)
                or (
                    not item.clipped
                    and item.structure >= gates.structure_min
                    and item.glyph_recall >= gates.glyph_min
                    and chosen.ncc - item.ncc <= gates.ambiguity_margin
                    and item.ncc >= gates.ncc_threshold - gates.ambiguity_margin
                )
            )
        ]
        if rivals_of:
            return RankResult(
                "ambiguous", None, chosen, rivals_of[0], "ambiguous")
        return RankResult("accept", chosen, chosen, runner_up, "accept")
    return RankResult(
        "reject", None, best, runner_up,
        _rejection_reason(best, template_w, gates) or "below_threshold")



def too_close_to_top(candidate, region, screen_h, top_margin_px):
    """名称太靠列表上沿：卡片多半被裁切，点击容易落到下一张。"""
    if candidate is None or not top_margin_px:
        return False
    if region is None:
        min_y = float(top_margin_px)
    else:
        min_y = region[1] * screen_h + float(top_margin_px)
    return candidate.y < min_y


def too_close_to_bottom(candidate, screen_h, bottom_margin_px):
    """名称太靠列表下沿：半截卡，点中部容易点到下一张或底栏。"""
    if candidate is None or not bottom_margin_px:
        return False
    return candidate.y > (screen_h - float(bottom_margin_px))


def evaluate_support_screen(screen_bgr, template_gray, gates, region=None,
                            max_peaks=6, floor_margin=0.08, top_margin_px=0,
                            bottom_margin_px=0):
    """整屏评估。返回 (RankResult, 列表签名)。"""
    template_gray = to_gray(template_gray)
    floor = max(0.5, gates.ncc_threshold - floor_margin)
    peaks = find_template_peaks(
        screen_bgr, template_gray, floor=floor, max_peaks=max_peaks,
        region=region)
    candidates = [assess_peak(screen_bgr, template_gray, peak, gates)
                  for peak in peaks]
    rank = rank_support_candidates(candidates, template_gray.shape[1], gates)
    if (rank.status == "accept" and rank.chosen is not None
            and too_close_to_top(rank.chosen, region, screen_bgr.shape[0],
                                top_margin_px)):
        rank = RankResult(
            status="reject",
            chosen=None,
            best=rank.chosen,
            runner_up=rank.runner_up,
            reason="near_top_partial",
        )
    if (rank.status == "accept" and rank.chosen is not None
            and too_close_to_bottom(rank.chosen, screen_bgr.shape[0],
                                    bottom_margin_px)):
        rank = RankResult(
            status="reject",
            chosen=None,
            best=rank.chosen,
            runner_up=rank.runner_up,
            reason="near_bottom_partial",
        )
    signature = list_signature(to_gray(screen_bgr), region)
    return rank, signature


def click_points(candidate, screen_w, center_x_ratio, y_offset_px=0, screen_h=None):
    """先点同一行立绘/卡片中部（真正选人）；名字偏卡面下方，原 Y 容易点到下一行。

    y_offset_px：相对名称中心上移的像素（国服助战卡名字在立绘下方）。
    名字点击只作备选，且同样上移，避免误开详情或点到邻行。
    """
    name_x = int(round(candidate.x))
    click_y = int(round(candidate.y - float(y_offset_px or 0)))
    if screen_h is not None:
        click_y = max(10, min(int(screen_h) - 10, click_y))
    name = (name_x, click_y)
    center = (int(round(screen_w * center_x_ratio)), click_y)
    if abs(center[0] - name[0]) <= 12:
        return (center,)
    return (center, name)


def same_card(first, second, x_tol, y_tol):
    if first is None or second is None:
        return False
    return abs(first.x - second.x) <= x_tol and abs(first.y - second.y) <= y_tol


def list_signature(gray, region=None, size=(32, 18)):
    if gray is None or gray.size == 0:
        return np.zeros(size[0] * size[1], np.float32)
    if region is not None:
        rx, ry, rw, rh = region
        screen_h, screen_w = gray.shape[:2]
        x1 = max(0, int(rx * screen_w))
        y1 = max(0, int(ry * screen_h))
        x2 = min(screen_w, int((rx + rw) * screen_w))
        y2 = min(screen_h, int((ry + rh) * screen_h))
        if x2 > x1 and y2 > y1:
            gray = gray[y1:y2, x1:x2]
    if gray.size == 0:
        return np.zeros(size[0] * size[1], np.float32)
    small = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
    return small.astype(np.float32).ravel()


def list_moved(previous, current, min_delta):
    """previous 为空表示还没有可对比的一帧，不把列表判成卡在底部。"""
    if previous is None or current is None:
        return True
    if previous.shape != current.shape:
        return True
    delta = float(np.mean(np.abs(previous.astype(np.float32)
                                  - current.astype(np.float32))))
    return delta >= min_delta


def refresh_dialog_open(before_gray, after_gray, drop):
    """刷新后若整屏明显变暗，才认为弹出了「是否更新」确认框。"""
    if before_gray is None or after_gray is None:
        return False
    if before_gray.shape != after_gray.shape:
        return False
    return float(after_gray.mean()) <= float(before_gray.mean()) - drop


def advance_scan(progress, status, page_advanced, policy):
    """看完一页且不能点时的下一步：scroll / nudge / refresh / give_up。

    page_advanced=False 表示刚刚那次滑动画面几乎没变（列表到底）。
    status 只用 reject 与 ambiguous；ambiguous 用小幅滑动，避免一次滑过目标。
    """
    if page_advanced:
        progress = replace(progress, stagnant_scrolls=0,
                           scrolls_this_pass=progress.scrolls_this_pass + 1)
    else:
        progress = replace(
            progress, stagnant_scrolls=progress.stagnant_scrolls + 1)
    stuck = (progress.stagnant_scrolls >= policy.max_stagnant
             or progress.scrolls_this_pass >= policy.max_scrolls_per_pass)
    if stuck:
        if progress.refreshes_used >= policy.max_refreshes:
            return "give_up", progress
        progress = replace(
            progress,
            refreshes_used=progress.refreshes_used + 1,
            scrolls_this_pass=0,
            stagnant_scrolls=0,
            clicks_on_candidate=0,
        )
        return "refresh", progress
    if status == "ambiguous":
        return "nudge", progress
    return "scroll", progress


def advance_after_click(progress, ui_advanced, same_card_still_valid,
                        page_advanced, policy):
    """点击已经发生。界面没离开助战列表时，绝不能返回 success。"""
    if ui_advanced:
        return "success", progress
    clicks = progress.clicks_on_candidate + 1
    progress = replace(progress, clicks_on_candidate=clicks)
    if same_card_still_valid and clicks < policy.max_clicks_per_candidate:
        return "retry_click", progress
    progress = replace(progress, clicks_on_candidate=0)
    return advance_scan(progress, "reject", page_advanced, policy)


_REASON_TEXT = {
    "no_peak": "无达到下限的匹配",
    "below_threshold": "相关系数不够",
    "weak_structure": "名称区域不像模板",
    "weak_glyph": "字形对不上",
    "trailing_name": "名称右侧仍有文字，疑似同名前缀（如 Lily / Alter）",
    "clipped": "名称贴边，卡片不完整",
    "ambiguous": "多个候选，不能唯一确定是配置的助战",
    "accept": "高置信匹配",
}


def _fmt_candidate(candidate):
    if candidate is None:
        return "无"
    return (f"ncc={candidate.ncc:.2f} structure={candidate.structure:.2f} "
            f"glyph={candidate.glyph_recall:.2f} "
            f"trailing={candidate.trailing_ratio:.2f} y={candidate.y:.0f}")


def describe_rank(rank):
    text = _REASON_TEXT.get(rank.reason, rank.reason)
    if rank.status == "accept":
        return f"采纳（{text}） {_fmt_candidate(rank.chosen)}"
    if rank.status == "ambiguous":
        return (f"歧义（{text}） best[{_fmt_candidate(rank.best)}] "
                f"second[{_fmt_candidate(rank.runner_up)}]")
    return f"拒绝（{text}） best[{_fmt_candidate(rank.best)}]"
