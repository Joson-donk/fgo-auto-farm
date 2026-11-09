# -*- coding: utf-8 -*-
"""助战选人纯函数测试。不连接模拟器。

    python test_support_select.py
"""
import ast
import os
import unittest

import cv2
import numpy as np

import config
from support_select import (
    SearchProgress,
    SupportCandidate,
    SupportGates,
    SupportRetryPolicy,
    TemplatePeak,
    advance_after_click,
    advance_scan,
    assess_peak,
    click_points,
    describe_rank,
    evaluate_support_screen,
    find_template_peaks,
    gates_from_config,
    list_moved,
    list_signature,
    policy_from_config,
    rank_support_candidates,
    refresh_dialog_open,
    same_card,
)
from vision import Vision

ROOT = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(ROOT, "templates")
GATES = SupportGates()

def _has_fixture(*names):
    return all(os.path.isfile(os.path.join(TEMPLATE_DIR, n)) for n in names)


_HAS_NAMEPLATE_FIXTURES = _has_fixture(
    "support_medusa.png", "support_koyanskaya.png", "support_elizabeth.png")


def _load_gray(name):
    path = os.path.join(TEMPLATE_DIR, name)
    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError(f"无法读取模板 {path}")
    return image


def _canvas(template, x, y, extra_right=None, fill=102):
    height, width = template.shape[:2]
    extra_w = 0 if extra_right is None else extra_right.shape[1]
    screen = np.full((1080, 1920), fill, np.uint8)
    screen[y:y + height, x:x + width] = template
    if extra_right is not None:
        screen[y:y + height, x + width:x + width + extra_w] = extra_right
    return screen


def _candidate(**kwargs):
    base = dict(x=400, y=300, ncc=0.97, structure=0.95, glyph_recall=0.92,
                trailing_ratio=0.0, clipped=False)
    base.update(kwargs)
    return SupportCandidate(**base)


class RankingTests(unittest.TestCase):
    def test_single_confident_candidate_is_accepted(self):
        rank = rank_support_candidates([_candidate()], 105, GATES)
        self.assertEqual(rank.status, "accept")
        self.assertEqual(rank.reason, "accept")
        self.assertIs(rank.chosen, rank.best)

    def test_high_ncc_with_unlike_crop_is_rejected(self):
        rank = rank_support_candidates(
            [_candidate(ncc=0.96, structure=0.31, glyph_recall=0.88)],
            105, GATES)
        self.assertEqual(rank.status, "reject")
        self.assertEqual(rank.reason, "weak_structure")
        self.assertIsNone(rank.chosen)
        self.assertIn("不像模板", describe_rank(rank))

    def test_weak_glyph_is_rejected(self):
        rank = rank_support_candidates(
            [_candidate(glyph_recall=0.22)], 105, GATES)
        self.assertEqual(rank.reason, "weak_glyph")

    def test_below_threshold_is_rejected(self):
        rank = rank_support_candidates(
            [_candidate(ncc=0.86)], 105, GATES)
        self.assertEqual(rank.reason, "below_threshold")

    def test_short_name_with_trailing_text_is_rejected(self):
        rank = rank_support_candidates(
            [_candidate(trailing_ratio=0.22)], 105, GATES)
        self.assertEqual(rank.status, "reject")
        self.assertEqual(rank.reason, "trailing_name")
        self.assertIn("右侧仍有文字", describe_rank(rank))

    def test_long_name_ignores_modest_trailing_text(self):
        # 伊丽莎白模板比短名宽，右侧少一个字也不应被前缀规则误杀。
        rank = rank_support_candidates(
            [_candidate(trailing_ratio=0.16)], 275, GATES)
        self.assertEqual(rank.status, "accept")

    def test_two_qualified_cards_are_ambiguous(self):
        cards = [
            _candidate(y=300, ncc=0.99),
            _candidate(y=560, ncc=0.97),
        ]
        rank = rank_support_candidates(cards, 105, GATES)
        self.assertEqual(rank.status, "ambiguous")
        self.assertIsNone(rank.chosen)
        self.assertIn("多个候选", describe_rank(rank))

    def test_true_card_plus_prefix_lookalike_is_not_clicked(self):
        cards = [
            _candidate(y=300, ncc=0.98, trailing_ratio=0.0),
            _candidate(y=560, ncc=0.97, trailing_ratio=0.24),
        ]
        rank = rank_support_candidates(cards, 105, GATES)
        self.assertEqual(rank.status, "ambiguous")
        self.assertIsNone(rank.chosen)

    def test_clipped_card_is_rejected(self):
        rank = rank_support_candidates(
            [_candidate(clipped=True, ncc=0.99)], 105, GATES)
        self.assertEqual(rank.reason, "clipped")

    def test_clear_winner_is_not_blocked_by_a_weak_peak(self):
        cards = [
            _candidate(ncc=0.98, structure=0.96, glyph_recall=0.93),
            _candidate(y=600, ncc=0.84, structure=0.40, glyph_recall=0.30),
        ]
        rank = rank_support_candidates(cards, 105, GATES)
        self.assertEqual(rank.status, "accept")
        self.assertAlmostEqual(rank.chosen.ncc, 0.98)

    def test_close_second_template_like_peak_is_ambiguous(self):
        cards = [
            _candidate(ncc=0.93, y=300),
            _candidate(ncc=0.89, y=560, trailing_ratio=0.0),
        ]
        rank = rank_support_candidates(cards, 105, GATES)
        self.assertEqual(rank.status, "ambiguous")


class ScreenshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _HAS_NAMEPLATE_FIXTURES:
            raise unittest.SkipTest(
                "公开仓不附带私有助战名条模板；跳过依赖 support_medusa/koyanskaya/elizabeth 的用例")
        cls.medusa = _load_gray("support_medusa.png")
        cls.koyanskaya = _load_gray("support_koyanskaya.png")
        cls.elizabeth = _load_gray("support_elizabeth.png")

    def test_real_medusa_is_unique_and_accepted(self):
        screen = _canvas(self.medusa, 400, 300)
        peaks = find_template_peaks(screen, self.medusa, floor=0.90)
        self.assertEqual(len(peaks), 1)
        rank, _ = evaluate_support_screen(screen, self.medusa, GATES)
        self.assertEqual(rank.status, "accept", describe_rank(rank))
        self.assertAlmostEqual(rank.chosen.y, 300 + self.medusa.shape[0] / 2, delta=2)
        self.assertGreaterEqual(rank.chosen.structure, 0.90)
        self.assertLessEqual(rank.chosen.trailing_ratio, GATES.trailing_max)

    def test_medusa_prefix_with_extra_glyphs_is_rejected(self):
        extra = self.medusa[:, 15:70]
        screen = _canvas(self.medusa, 400, 300, extra_right=extra)
        rank, _ = evaluate_support_screen(screen, self.medusa, GATES)
        self.assertEqual(rank.status, "reject", describe_rank(rank))
        self.assertEqual(rank.reason, "trailing_name")

    def test_two_real_medusas_are_not_clicked(self):
        screen = _canvas(self.medusa, 400, 240)
        height, width = self.medusa.shape[:2]
        screen[520:520 + height, 400:400 + width] = self.medusa
        rank, _ = evaluate_support_screen(screen, self.medusa, GATES)
        self.assertEqual(rank.status, "ambiguous", describe_rank(rank))
        self.assertIsNone(rank.chosen)

    def test_other_servant_nameplates_do_not_match_medusa(self):
        for other in (self.koyanskaya, self.elizabeth):
            screen = _canvas(other, 400, 300)
            rank, _ = evaluate_support_screen(screen, self.medusa, GATES)
            self.assertEqual(rank.status, "reject", describe_rank(rank))
            self.assertIn(rank.reason, ("no_peak", "below_threshold", "weak_structure"))
            if rank.best is not None:
                self.assertLess(rank.best.ncc, GATES.ncc_threshold)

    def test_claimed_score_is_rechecked_against_pixels(self):
        screen = _canvas(self.koyanskaya, 400, 300)
        peak = TemplatePeak(x=400 + self.medusa.shape[1] / 2,
                            y=300 + self.medusa.shape[0] / 2,
                            score=0.99, left=400, top=300)
        assessed = assess_peak(screen, self.medusa, peak, GATES)
        self.assertLess(assessed.ncc, GATES.ncc_threshold)
        rank = rank_support_candidates([assessed], self.medusa.shape[1], GATES)
        self.assertEqual(rank.status, "reject")

    def test_elizabeth_and_koyanskaya_templates_still_match_themselves(self):
        for name, template in (("elizabeth", self.elizabeth),
                               ("koyanskaya", self.koyanskaya)):
            screen = _canvas(template, 380, 280)
            rank, _ = evaluate_support_screen(screen, template, GATES)
            self.assertEqual(rank.status, "accept", f"{name} {describe_rank(rank)}")

    def test_partial_card_at_screen_edge_is_rejected(self):
        screen = _canvas(self.medusa, 400, 0)
        rank, _ = evaluate_support_screen(screen, self.medusa, GATES)
        self.assertEqual(rank.status, "reject", describe_rank(rank))
        self.assertEqual(rank.reason, "clipped")

    def test_name_outside_list_region_is_ignored(self):
        screen = _canvas(self.medusa, 20, 300)
        rank, _ = evaluate_support_screen(
            screen, self.medusa, GATES, region=config.SUPPORT_NAME_REGION)
        self.assertNotEqual(rank.status, "accept", describe_rank(rank))

    def test_vision_find_peaks_returns_scored_peak(self):
        screen = _canvas(self.medusa, 400, 300)
        vision = Vision(TEMPLATE_DIR, threshold=0.9)
        if not _HAS_NAMEPLATE_FIXTURES:
            raise unittest.SkipTest("缺私有助战名条模板")
        peaks = vision.find_peaks(screen, "support_medusa.png", floor=0.9)
        self.assertEqual(len(peaks), 1)
        self.assertGreaterEqual(peaks[0].score, 0.9)


class PolicyTests(unittest.TestCase):
    def test_miss_scrolls_then_refreshes_then_stops_after_budget(self):
        policy = SupportRetryPolicy(max_refreshes=3, max_scrolls_per_pass=2,
                                    max_stagnant=9, max_clicks_per_candidate=2)
        progress = SearchProgress()
        actions = []
        for _ in range(12):
            action, progress = advance_scan(progress, "reject", True, policy)
            actions.append(action)
            if action == "give_up":
                break
        self.assertEqual(actions.count("refresh"), 3)
        self.assertEqual(actions[-1], "give_up")
        self.assertNotIn("success", actions)
        self.assertEqual(progress.refreshes_used, 3)

    def test_stuck_list_refreshes_without_burning_the_scroll_budget(self):
        policy = SupportRetryPolicy(max_refreshes=3, max_scrolls_per_pass=12,
                                    max_stagnant=2)
        progress = SearchProgress()
        action, progress = advance_scan(progress, "reject", False, policy)
        self.assertEqual(action, "scroll")
        self.assertEqual(progress.stagnant_scrolls, 1)
        action, progress = advance_scan(progress, "reject", False, policy)
        self.assertEqual(action, "refresh")
        self.assertEqual(progress.refreshes_used, 1)
        self.assertEqual(progress.stagnant_scrolls, 0)

    def test_ambiguous_page_nudges_instead_of_clicking(self):
        policy = SupportRetryPolicy()
        action, _ = advance_scan(SearchProgress(), "ambiguous", True, policy)
        self.assertEqual(action, "nudge")

    def test_click_that_stays_on_support_is_not_success(self):
        policy = SupportRetryPolicy(max_clicks_per_candidate=2)
        progress = SearchProgress()
        action, progress = advance_after_click(
            progress, ui_advanced=False, same_card_still_valid=True,
            page_advanced=True, policy=policy)
        self.assertEqual(action, "retry_click")
        self.assertEqual(progress.clicks_on_candidate, 1)
        action, progress = advance_after_click(
            progress, ui_advanced=False, same_card_still_valid=True,
            page_advanced=True, policy=policy)
        self.assertNotEqual(action, "success")
        self.assertNotEqual(action, "retry_click")
        self.assertIn(action, ("scroll", "nudge", "refresh", "give_up"))

    def test_click_that_leaves_support_is_success(self):
        action, _ = advance_after_click(
            SearchProgress(), ui_advanced=True, same_card_still_valid=False,
            page_advanced=False, policy=SupportRetryPolicy())
        self.assertEqual(action, "success")

    def test_lost_candidate_after_click_does_not_retry(self):
        action, progress = advance_after_click(
            SearchProgress(), ui_advanced=False, same_card_still_valid=False,
            page_advanced=True, policy=SupportRetryPolicy())
        self.assertEqual(action, "scroll")
        self.assertEqual(progress.clicks_on_candidate, 0)

    def test_same_card_tolerance(self):
        first = _candidate(x=400, y=300)
        self.assertTrue(same_card(first, _candidate(x=420, y=318), 48, 30))
        self.assertFalse(same_card(first, _candidate(x=400, y=360), 48, 30))
        self.assertFalse(same_card(None, first, 48, 30))

    def test_click_points_hit_the_card_center_before_the_name(self):
        points = click_points(_candidate(x=420, y=310), 1920, 0.50)
        self.assertEqual(points[0], (960, 310))
        self.assertEqual(points[1], (420, 310))

    def test_click_points_shift_up_onto_portrait(self):
        points = click_points(
            _candidate(x=678, y=965), 1920, 0.22,
            y_offset_px=70, screen_h=1080)
        self.assertEqual(points[0], (422, 895))
        self.assertEqual(points[1], (678, 895))


    def test_list_moved_and_refresh_dialog(self):
        base = np.full((90, 160), 40, np.uint8)
        base[20:40, 30:80] = 180
        same = list_signature(base)
        shifted = list_signature(np.roll(base, 30, axis=1))
        self.assertFalse(list_moved(same, list_signature(base), 3.5))
        self.assertTrue(list_moved(same, shifted, 3.5))
        self.assertTrue(list_moved(None, same, 3.5))
        dim = np.full_like(base, 10)
        self.assertTrue(refresh_dialog_open(base, dim, 18))
        self.assertFalse(refresh_dialog_open(base, base, 18))
        noisy = np.clip(base.astype(np.int16) + 3, 0, 255).astype(np.uint8)
        self.assertFalse(refresh_dialog_open(base, noisy, 18))


class ConfigTests(unittest.TestCase):
    def test_config_knobs_match_the_stricter_defaults(self):
        gates = gates_from_config(config)
        policy = policy_from_config(config)
        self.assertGreaterEqual(gates.ncc_threshold, 0.96)
        self.assertGreaterEqual(gates.structure_min, 0.92)
        self.assertLessEqual(gates.trailing_max, 0.08)
        self.assertGreaterEqual(policy.max_refreshes, 1)
        self.assertEqual(policy.max_clicks_per_candidate, 2)
        self.assertIsNotNone(config.SUPPORT_REFRESH_POS)
        # 公开仓只附带 default 壳；私有配队 profile 不随仓发布
        self.assertIn("default", config.BATTLE_CLASS_PROFILES)
        self.assertTrue(config.BATTLE_CLASS_PROFILES["default"].get("support_pick_first"))
        self.assertTrue(config.BATTLE_CLASSES["default"]["ready"])

    def test_bot_sources_parse(self):
        for name in ("fgo_bot.py", "vision.py", "support_select.py", "config.py"):
            path = os.path.join(ROOT, name)
            with open(path, encoding="utf-8") as handle:
                ast.parse(handle.read(), filename=name)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(
        __import__(__name__))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failed = len(result.failures) + len(result.errors)
    print(f"{result.testsRun - failed}/{result.testsRun} 通过")
    raise SystemExit(0 if result.wasSuccessful() else 1)
