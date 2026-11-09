# -*- coding: utf-8 -*-
"""
固定决策：选卡与连携判定。

每回合宝具优先（宝具先点，占出卡位），剩余手牌再凑连携，连携优先级：
同从者三连(brave) > 三色(tricolor) > 红(buster) > 蓝(arts) > 绿(quick)。
不足 3 张或无连携时，按颜色/同色/同从者打分取最优。

preferred_servant：手牌归属识别成功时，优先选该从者的指令卡（权重大于连携）。
"""
from itertools import combinations

from card_recognition import COLOR_NAME, Card


def skills_to_check(profile, turn, used_skill_keys=()):
    """Return configured skills that should be checked this turn.

    check_skills_each_turn=True 时：每回合整表重检，能否放交由亮度 CD 判断，
    不再用「本局曾放过」永久跳过（否则 CD 转好了也不会再点）。
    首回合仍用 used_skill_keys，避免同一回合内重复点。
    """
    if turn == 0:
        skills = profile.get("opening_skills", profile["skills"])
        used = set(used_skill_keys)
        return [(key, target) for key, target in skills if key not in used]
    if profile.get("check_skills_each_turn", False):
        return list(profile["skills"])
    return []


def chain_type_of(combo):
    """返回 3 张卡组合的连携类型；不构成任何连携返回 None。"""
    if len(combo) != 3:
        return None
    colors = [c.color for c in combo]
    servants = [c.servant for c in combo]

    if all(s is not None for s in servants) and len(set(servants)) == 1:
        return "brave"
    if set(colors) == {"B", "A", "Q"}:
        return "tricolor"
    if colors.count("B") == 3:
        return "buster"
    if colors.count("A") == 3:
        return "arts"
    if colors.count("Q") == 3:
        return "quick"
    return None


CHAIN_LABEL = {
    "brave": "同从者三连(Brave)",
    "tricolor": "三色连携",
    "buster": "红卡三连(Buster)",
    "arts": "蓝卡三连(Arts)",
    "quick": "绿卡三连(Quick)",
}


def score_of(combo, cfg):
    """无连携/不足3张时的兜底打分。"""
    sc = sum(cfg.COLOR_SCORE.get(c.color, 0) for c in combo)
    for col in ("B", "A", "Q"):
        k = sum(1 for c in combo if c.color == col)
        sc += (k * (k - 1) // 2) * cfg.SAME_COLOR_PAIR_BONUS
    sv = [c.servant for c in combo if c.servant is not None]
    for s in set(sv):
        k = sv.count(s)
        sc += (k * (k - 1) // 2) * cfg.SAME_SERVANT_PAIR_BONUS
    return sc


def choose_cards(hand, n, cfg, np_cards=None, preferred_servant=None):
    """
    从 5 张手牌里选 n 张。n = 3 - 宝具数（宝具已占出卡位）。

    排序键（min）：
      1) 优先从者手牌数量（越多越好）
      2) 连携类型（按 CHAIN_PRIORITY）
      3) 颜色/同从者打分
    返回的 slots 会把优先从者的卡排在前面，保证点击顺序靠前。
    """
    np_cards = list(np_cards or [])
    n = 3 - len(np_cards)
    if n <= 0:
        return [], None, "宝具已占满 3 个出卡位，不选手牌"
    if n > len(hand):
        n = len(hand)

    priority = {name: i for i, name in enumerate(cfg.CHAIN_PRIORITY)}
    last = len(priority)

    combos = list(combinations(hand, n))

    def rank_key(combo):
        full = np_cards + list(combo)
        ct = chain_type_of(full) if len(full) == 3 else None
        cr = priority.get(ct, last)
        preferred_count = 0
        if preferred_servant is not None:
            preferred_count = sum(1 for c in combo if c.servant == preferred_servant)
        # 优先从者数量绝对优先于连携
        return (-preferred_count, cr, -score_of(full, cfg))

    best = min(combos, key=rank_key)
    # 点击顺序：优先从者卡在前，其余保持原相对顺序
    if preferred_servant is not None:
        ordered = sorted(best, key=lambda c: (0 if c.servant == preferred_servant else 1, c.slot))
    else:
        ordered = list(best)
    slots = [c.slot for c in ordered]
    full = np_cards + list(ordered)
    ct = chain_type_of(full) if len(full) == 3 else None

    colors = "".join(c.color if c.color != "?" else "?" for c in full)
    np_part = f"宝具{len(np_cards)}张+手牌{n}张 " if np_cards else ""
    if ct:
        desc = f"{np_part}凑出【{CHAIN_LABEL[ct]}】，卡色={colors}，手牌槽位 {slots}"
    else:
        desc = f"{np_part}无连携，按打分选卡 卡色={colors}，手牌槽位 {slots}"
    if preferred_servant is not None:
        pc = sum(1 for c in ordered if c.servant == preferred_servant)
        desc += f"，优先从者{preferred_servant + 1}x{pc}"
    return slots, ct, desc


def describe_hand(hand):
    """打印手牌概览。"""
    return " | ".join(
        f"{c.color}" + (f"/S{c.servant + 1}" if c.servant is not None else "")
        for c in hand
    )
