# -*- coding: utf-8 -*-
"""决策逻辑单测（无需连接设备/模拟器）：验证连携判定、选卡优先级与宝具连携。
运行:  python test_decision.py
"""
import config
from card_recognition import Card
from decision import choose_cards, chain_type_of, skills_to_check


def C(slot, color, servant=None):
    return Card(slot, color, servant)


def hand(*spec):
    """spec: [(color, servant), ...]，长度5，slot 从0起。"""
    return [C(i, col, sv) for i, (col, sv) in enumerate(spec)]


def np_(*colors):
    """构造已选宝具的虚拟卡（slot=-1，servant 从者0起）。"""
    return [C(-1, col, i) for i, col in enumerate(colors)]


CASES = [
    # (名称, 手牌, 宝具卡列表, 期望连携类型, 期望手牌槽位)
    ("同从者3连优先于三色",
     hand(("B", 0), ("A", 0), ("Q", 0), ("B", 1), ("Q", 2)),
     [], "brave", [0, 1, 2]),

    ("三色优先于红卡三连",
     hand(("B", None), ("A", None), ("Q", None), ("B", None), ("B", None)),
     [], "tricolor", [0, 1, 2]),

    ("红卡三连(无Q凑不成三色)",
     hand(("B", None), ("B", None), ("B", None), ("B", None), ("A", None)),
     [], "buster", [0, 1, 2]),

    ("蓝卡三连(无Q凑不成三色)",
     hand(("A", None), ("A", None), ("A", None), ("A", None), ("B", None)),
     [], "arts", [0, 1, 2]),

    ("绿卡三连(无A凑不成三色)",
     hand(("Q", None), ("Q", None), ("Q", None), ("Q", None), ("B", None)),
     [], "quick", [0, 1, 2]),

    # 蓝宝具(A) + 红手牌(B) + 绿手牌(Q) = 三色：应选 B(slot0) 与 Q(slot4)
    ("1蓝宝具后选2张凑三色",
     hand(("B", None), ("B", None), ("A", None), ("A", None), ("Q", None)),
     np_("A"), "tricolor", [0, 4]),

    # 蓝+红宝具(A,B) 再补一张绿 Q(slot2) 凑三色
    ("2宝具后选1张补三色",
     hand(("A", None), ("B", None), ("Q", None), ("A", None), ("B", None)),
     np_("A", "B"), "tricolor", [2]),

    # 3 宝具占满行动，不选手牌
    ("3宝具不选手牌",
     hand(("B", None), ("B", None), ("B", None), ("A", None), ("Q", None)),
     np_("A", "B", "Q"), None, []),

    # 宝具颜色未知(?)，不参与颜色连携：退化为按打分选2张，红分最高选 slot0,1
    ("宝具颜色未知则按打分选2张",
     hand(("B", None), ("B", None), ("A", None), ("A", None), ("Q", None)),
     np_("?"), None, [0, 1]),

    ("识别有未知卡且无三连：按打分兜底",
     hand(("B", None), ("B", None), ("A", None), ("A", None), ("?", None)),
     [], None, [0, 1, 2]),
]


def main():
    passed = 0
    for name, hd, npc, want_chain, want_slots in CASES:
        n = 3 - len(npc)
        slots, ct, desc = choose_cards(hd, n, config, np_cards=npc)
        ok = (ct == want_chain) and (slots == want_slots)
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        print(f"       {desc}")
        if not ok:
            print(f"       期望: chain={want_chain}, slots={want_slots}")
    print(f"\n{passed}/{len(CASES)} 通过")
    if passed != len(CASES):
        raise SystemExit(1)

    priority_hand = hand(("B", 0), ("A", 2), ("Q", 2), ("B", 1), ("Q", 0))
    slots, _, _ = choose_cards(
        priority_hand, 3, config, preferred_servant=2)
    preferred_count = sum(priority_hand[slot].servant == 2 for slot in slots)
    if preferred_count != 2:
        print(f"[FAIL] 优先伊吹指令卡: 期望2张伊吹指令卡，实际槽位 {slots}")
        raise SystemExit(1)
    print("[PASS] 优先伊吹指令卡")

    profile = {
        "skills": [("s1", None), ("s2", ("servant", 3))],
        "opening_skills": [("s2", ("servant", 3))],
        "check_skills_each_turn": True,
    }
    assert skills_to_check(profile, 0) == [("s2", ("servant", 3))]
    assert skills_to_check(profile, 1) == profile["skills"]
    # each_turn：每波整表重检，used 不永久过滤（CD 由亮度判断）
    assert skills_to_check(profile, 2, {"s1"}) == profile["skills"]
    assert skills_to_check(profile, 2, {"s1", "s2"}) == profile["skills"]
    # 首回合仍尊重 used，避免同回合连点
    assert skills_to_check(profile, 0, {"s2"}) == []
    # 关闭 each_turn 时，非首回合不检
    profile_off = dict(profile, check_skills_each_turn=False)
    assert skills_to_check(profile_off, 2) == []
    print("[PASS] 技能按回合调度且已成功技能不重复尝试")


if __name__ == "__main__":
    main()
