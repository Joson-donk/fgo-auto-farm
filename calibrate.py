# -*- coding: utf-8 -*-
"""校准工具：截图、标注点击位置、调试手牌识别、截取模板。

用法:
    python calibrate.py                  # 截当前画面，标注所有按钮点 -> logs/calibrate_map.png
    python calibrate.py --battle-map     # 战斗界面专用：标注技能/宝具/手牌/卡色ROI/目标
    python calibrate.py --debug-cards    # 识别当前手牌并标注 -> logs/card_debug.png（验证识别）
    python calibrate.py --crop battle_attack   # 鼠标框选区域，存为模板（交互式）

常用模板名: battle_attack / skill_confirm / target_dialog / np_card /
           result_next / rerun / quest_start / party_start / support_confirm /
           servant_1 / servant_2 / servant_3
"""
import argparse
import os
import sys
import time

import config


def _logs_dir():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def _mark_positions(img, color=(0, 0, 255)):
    import cv2

    h, w = img.shape[:2]
    for key, (fx, fy) in config.POS.items():
        x, y = int(fx * w), int(fy * h)
        cv2.circle(img, (x, y), 12, color, 3)
        cv2.putText(img, key, (x + 14, y + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)


def draw_map(bot):
    img = bot.read_screen()
    if img is None:
        print("[calibrate] 截图失败，检查 adb 连接。")
        sys.exit(1)
    _mark_positions(img)
    out = os.path.join(_logs_dir(), "calibrate_map.png")
    cv2.imwrite(out, img)
    print(f"[calibrate] 已标注全部点击位置 -> {out}")


def draw_battle_map(bot):
    """战斗界面：标注技能、宝具、手牌、目标，并画出卡色/从者采样框。"""
    import cv2

    from vision import Vision
    from card_recognition import CardRecognizer

    img = bot.read_screen()
    if img is None:
        print("[calibrate] 截图失败，请先进入战斗选卡界面。")
        sys.exit(1)
    vision = Vision(config.TEMPLATE_DIR, config.TEMPLATE_THRESHOLD)
    rec = CardRecognizer(config, vision)

    # 红点：全部点击位置
    _mark_positions(img)
    h, w = img.shape[:2]

    def box_rect(slot, box):
        fx, fy = config.POS[f"card_{slot}"]
        dx, dy, bw, bh = box
        x1 = int((fx + dx - bw / 2) * w)
        y1 = int((fy + dy - bh / 2) * h)
        x2 = int((fx + dx + bw / 2) * w)
        y2 = int((fy + dy + bh / 2) * h)
        return (x1, y1), (x2, y2)

    for slot in range(5):
        p1, p2 = box_rect(slot, config.CARD_COLOR_BOX)
        cv2.rectangle(img, p1, p2, (255, 0, 0), 2)     # 蓝框：卡色采样
        p1, p2 = box_rect(slot, config.CARD_SERVANT_BOX)
        cv2.rectangle(img, p1, p2, (0, 200, 0), 2)     # 绿框：从者头像

    out = os.path.join(_logs_dir(), "battle_map.png")
    cv2.imwrite(out, img)
    print(f"[calibrate] 战斗元素标注 -> {out}")
    print("  红点=点击位置(技能/宝具/手牌/目标)；蓝框=卡色采样区；绿框=从者头像区。")
    print("  位置/采样框不对就改 config.py 的 POS / CARD_COLOR_BOX / CARD_SERVANT_BOX。")


def debug_cards(bot):
    """识别当前手牌并标注，打印结果。"""
    import cv2

    from vision import Vision
    from card_recognition import CardRecognizer

    img = bot.read_screen()
    if img is None:
        print("[calibrate] 截图失败，请先进战斗选卡界面。")
        sys.exit(1)
    vision = Vision(config.TEMPLATE_DIR, config.TEMPLATE_THRESHOLD)
    rec = CardRecognizer(config, vision)
    hand = rec.recognize_hand(img)
    print("[calibrate] 手牌识别结果：")
    for c in hand:
        print("  ", c)
    known = sum(1 for c in hand if c.color != "?")
    print(f"  卡色识别成功 {known}/5；已加载从者模板 {len(rec._servant_tpl)} 个。")
    marked = rec.draw_debug(img, hand)
    out = os.path.join(_logs_dir(), "card_debug.png")
    cv2.imwrite(out, marked)
    print(f"[calibrate] 标注图 -> {out}")
    if known < 5:
        print("  有卡识别成'?'：调 config.py 的 CARD_COLOR_BOX 位置或 CARD_COLOR_RATIO_MIN。")
    if not rec._servant_tpl:
        print("  未放 servant_1/2/3.png 模板：同从者三连不可用，颜色连携不受影响。")


def crop_template(bot, name):
    """交互式截取模板：打开截图窗口，鼠标框选目标区域。"""
    import cv2

    img = bot.read_screen()
    if img is None:
        print("[calibrate] 截图失败。")
        sys.exit(1)
    print(f"[calibrate] 在弹窗用鼠标框住「{name}」区域，按 Enter/空格 确认，Esc 取消。")
    roi = cv2.selectROI("select region - " + name, img, showCrosshair=True)
    cv2.destroyAllWindows()
    x, y, w, h = roi
    if w == 0 or h == 0:
        print("[calibrate] 已取消。")
        return
    crop = img[int(y):int(y + h), int(x):int(x + w)]
    os.makedirs(config.TEMPLATE_DIR, exist_ok=True)
    tpath = os.path.join(config.TEMPLATE_DIR, name)
    cv2.imwrite(tpath, crop)
    print(f"[calibrate] 模板已保存 -> {tpath}  (尺寸 {w}x{h})，建议只框按钮本体。")


def setup_class(bot, class_key):
    """半自动校准向导：交互式引导用户完成一个新副本的全部配置。"""
    import cv2

    from vision import Vision

    if class_key not in config.BATTLE_CLASSES:
        print(f"[setup] 未知的副本 key: {class_key}")
        print(f"  可用: {', '.join(config.BATTLE_CLASSES.keys())}")
        return

    info = config.BATTLE_CLASSES[class_key]
    print(f"\n{'='*60}")
    print(f"  半自动校准向导: {info['label']} ({class_key})")
    print(f"{'='*60}\n")

    profile = {}
    template_dir = config.TEMPLATE_DIR
    os.makedirs(template_dir, exist_ok=True)

    # ---- Step 1: 助战模板 ----
    print("[Step 1/5] 助战从者模板")
    print("  请在游戏中打开助战列表，确保目标从者可见。")
    input("  准备好后按 Enter 截图...")
    img = bot.read_screen()
    if img is None:
        print("  截图失败，请检查 adb 连接。")
        return
    support_name = input(f"  助战从者名称（用于日志显示，如 '光之高扬斯卡娅'）: ").strip()
    if not support_name:
        support_name = class_key
    tpl_name = f"support_{class_key}.png"
    print(f"  请在弹窗中框选「{support_name}」的名称文字区域，按 Enter 确认，Esc 取消。")
    roi = cv2.selectROI("select support name", img, showCrosshair=True)
    cv2.destroyAllWindows()
    if roi[2] == 0 or roi[3] == 0:
        print("  已跳过助战模板。")
    else:
        x, y, w, h = roi
        crop = img[y:y+h, x:x+w]
        tpath = os.path.join(template_dir, tpl_name)
        cv2.imwrite(tpath, crop)
        profile["support_label"] = support_name
        profile["support_template"] = tpl_name
        print(f"  ✓ 已保存 -> {tpath} ({w}x{h})")

    # ---- Step 2: 职阶筛选 ----
    print("\n[Step 2/5] 职阶筛选（可选）")
    need_filter = input("  是否需要先点击职阶筛选图标？(y/N): ").strip().lower()
    if need_filter == 'y':
        filter_class = input("  筛选哪个职阶？(saber/archer/lancer/rider/caster/assassin/berserker/extra): ").strip()
        print("  请在助战列表界面，框选该职阶的筛选图标。")
        img2 = bot.read_screen()
        if img2 is not None:
            roi2 = cv2.selectROI("select class filter icon", img2, showCrosshair=True)
            cv2.destroyAllWindows()
            if roi2[2] > 0 and roi2[3] > 0:
                fx = (roi2[0] + roi2[2] / 2) / img2.shape[1]
                fy = (roi2[1] + roi2[3] / 2) / img2.shape[0]
                profile["support_filter"] = filter_class
                print(f"  ✓ 筛选坐标: ({fx:.3f}, {fy:.3f})")
                print(f"  ⚠ 请手动将以下行添加到 config.py 的 SUPPORT_CLASS_FILTERS:")
                print(f'      "{filter_class}": ({fx:.3f}, {fy:.3f}),')
            else:
                print("  已跳过。")
        else:
            print("  截图失败，已跳过。")
    else:
        print("  已跳过。")

    # ---- Step 3: 宝具模板 ----
    print("\n[Step 3/5] 宝具卡面模板")
    print("  请进入战斗并点攻击按钮，使指令卡/宝具卡完全展开。")
    input("  准备好后按 Enter 截图...")
    img3 = bot.read_screen()
    if img3 is None:
        print("  截图失败。")
    else:
        np_tpl_name = f"np_card_{class_key}.png"
        print(f"  请框选宝具卡的卡面区域（竖卡），按 Enter 确认。")
        roi3 = cv2.selectROI("select NP card", img3, showCrosshair=True)
        cv2.destroyAllWindows()
        if roi3[2] > 0 and roi3[3] > 0:
            x, y, w, h = roi3
            crop = img3[y:y+h, x:x+w]
            tpath = os.path.join(template_dir, np_tpl_name)
            cv2.imwrite(tpath, crop)
            profile["np_template"] = np_tpl_name
            print(f"  ✓ 已保存 -> {tpath} ({w}x{h})")
        else:
            print("  已跳过宝具模板。")

    # ---- Step 4: 技能配置 ----
    print("\n[Step 4/5] 技能配置")
    print("  将依次检测 9 个技能的类型。")
    print("  对于每个技能，脚本会点击它，然后你需要告诉我弹窗类型。")
    print("  如果技能在 CD 中（没有弹窗），输入 'cd' 跳过。\n")

    skills = []
    skill_keys = [f"skill_{i}" for i in range(9)]
    servant_names = ["从者1", "从者2", "从者3"]

    for i, key in enumerate(skill_keys):
        pos = config.POS.get(key)
        if pos is None:
            continue
        print(f"  [{i+1}/9] {key} @ ({pos[0]:.3f}, {pos[1]:.3f})")
        print(f"    即将点击此技能，请注意观察弹窗。")
        confirm = input("    按 Enter 点击，或输入 'skip' 跳过此技能: ").strip()
        if confirm.lower() == 'skip':
            print("    已跳过。")
            continue

        bot.tap(bot.w * pos[0] if hasattr(bot, 'w') else 1920 * pos[0],
                bot.h * pos[1] if hasattr(bot, 'h') else 1080 * pos[1])
        time.sleep(1.5)

        print("    弹窗类型？")
        print("      s / self     = 自身/群体技能（只有「决定」按钮）")
        print("      f / friend   = 己方单体技能（出现「请选择对象」+ 3个从者头像）")
        print("      e / enemy    = 敌方单体技能（出现「请选择对象」+ 敌人）")
        print("      cd           = 技能CD中，无弹窗")
        stype = input("    请输入: ").strip().lower()

        if stype in ('s', 'self'):
            skills.append((key, None))
            print(f"    ✓ {key}: 自身/群体技能")
        elif stype in ('f', 'friend'):
            target_idx = int(input("    目标从者编号 (1/2/3): ").strip()) - 1
            skills.append((key, ("servant", target_idx + 1)))
            print(f"    ✓ {key}: 己方单体 → 从者{target_idx + 1}")
        elif stype in ('e', 'enemy'):
            skills.append((key, ("enemy", 1)))
            print(f"    ✓ {key}: 敌方单体")
        elif stype == 'cd':
            print(f"    ○ {key}: CD中，已跳过")
        else:
            print(f"    ✗ 未识别输入 '{stype}'，已跳过")

        # 关闭可能残留的弹窗
        bot._run(["shell", "input", "keyevent", "4"])
        time.sleep(0.5)

    profile["skills"] = skills

    # ---- Step 5: 生成配置 ----
    print(f"\n[Step 5/5] 生成配置")
    print(f"\n  请将以下内容添加到 config.py 的 BATTLE_CLASS_PROFILES 字典中:\n")
    print(f'    "{class_key}": {{')
    if "support_label" in profile:
        print(f'        "support_label": "{profile["support_label"]}",')
    if "support_template" in profile:
        print(f'        "support_template": "{profile["support_template"]}",')
    if "support_filter" in profile:
        print(f'        "support_filter": "{profile["support_filter"]}",')
    print(f'        "skills": [')
    for key, target in skills:
        if target is None:
            print(f'            ("{key}", None),')
        else:
            print(f'            ("{key}", {target!r}),')
    print(f'        ],')
    if "np_template" in profile:
        print(f'        "np_template": "{profile["np_template"]}",')
    print(f'    }},')

    print(f"\n  然后将 BATTLE_CLASSES[\"{class_key}\"][\"ready\"] 改为 True。")
    print(f"\n{'='*60}")
    print(f"  校准完成！验证无误后即可启动刷本。")
    print(f"{'='*60}\n")


def verify_class(class_key):
    """只读验证某个副本配置的完整性。"""
    if class_key not in config.BATTLE_CLASSES:
        print(f"[verify] 未知 key: {class_key}")
        return

    info = config.BATTLE_CLASSES[class_key]
    profile = config.BATTLE_CLASS_PROFILES.get(class_key)
    errors = []
    warnings = []

    if not info.get("ready"):
        warnings.append("ready=False，当前不可启动")

    if profile is None:
        errors.append("BATTLE_CLASS_PROFILES 中无此 key 的配置")
    else:
        # 检查助战模板
        st = profile.get("support_template")
        if st and not os.path.isfile(os.path.join(config.TEMPLATE_DIR, st)):
            errors.append(f"缺少助战模板: {st}")
        elif not st:
            warnings.append("未配置助战模板")

        # 检查宝具模板
        np_tpl = profile.get("np_template")
        np_templates = profile.get("np_templates")
        if np_templates:
            for entry in np_templates:
                t = entry.get("template")
                if t and not os.path.isfile(os.path.join(config.TEMPLATE_DIR, t)):
                    errors.append(f"缺少宝具模板: {t}")
        elif np_tpl and not os.path.isfile(os.path.join(config.TEMPLATE_DIR, np_tpl)):
            errors.append(f"缺少宝具模板: {np_tpl}")
        elif not np_tpl and not np_templates:
            warnings.append("未配置宝具模板")

        # 检查技能
        skills = profile.get("skills", [])
        if not skills:
            warnings.append("未配置技能列表")
        for key, target in skills:
            if key not in config.POS:
                errors.append(f"技能 {key} 不在 POS 坐标中")

        # 检查职阶筛选
        sf = profile.get("support_filter")
        if sf and config.SUPPORT_CLASS_FILTERS.get(sf) is None:
            warnings.append(f"职阶筛选 {sf} 坐标未校准 (SUPPORT_CLASS_FILTERS)")

    print(f"\n[verify] {info['label']} ({class_key})")
    if errors:
        for e in errors:
            print(f"  ✗ {e}")
    if warnings:
        for w in warnings:
            print(f"  ⚠ {w}")
    if not errors and not warnings:
        print(f"  ✓ 配置完整，可以启动")
    elif not errors:
        print(f"  ○ 有警告但可启动（部分功能受限）")
    else:
        print(f"  ✗ 有错误，请先修复")
    print()


def main():
    p = argparse.ArgumentParser(description="FGO 校准工具")
    p.add_argument("--battle-map", action="store_true", help="标注战斗界面全部元素")
    p.add_argument("--debug-cards", action="store_true", help="识别并标注当前手牌")
    p.add_argument("--crop", type=str, default=None,
                   help="框选并保存指定模板，如 battle_attack / np_ready_1 / servant_1")
    p.add_argument("--setup-class", type=str, default=None, metavar="KEY",
                   help="半自动校准向导：交互式配置新副本（如 assassin/saber/daily）")
    p.add_argument("--verify-class", type=str, default=None, metavar="KEY",
                   help="验证指定副本配置的完整性（只读，不连接设备）")
    args = p.parse_args()

    if args.verify_class:
        verify_class(args.verify_class)
        return

    from adb import Adb

    bot = Adb(config.ADB_PATH, config.DEVICE_SERIAL)
    if args.setup_class:
        setup_class(bot, args.setup_class)
    elif args.crop:
        crop_template(bot, args.crop + ".png")
    elif args.battle_map:
        draw_battle_map(bot)
    elif args.debug_cards:
        debug_cards(bot)
    else:
        draw_map(bot)


if __name__ == "__main__":
    main()
