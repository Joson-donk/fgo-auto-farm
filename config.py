# -*- coding: utf-8 -*-
"""
FGO 自动刷本脚本 —— 全局配置（公开示例版）
所有坐标均为「屏幕比例」(0~1)，运行时乘上真实分辨率。
参考分辨率：任意 ADB 模拟器 / 真机，横屏 FGO 国服 1920 x 1080。
公开仓默认只启用 default 战斗壳；自配配队请改 BATTLE_CLASSES / BATTLE_CLASS_PROFILES。
"""
import os

# ---------------------------------------------------------------------------
# ADB 连接配置
# ---------------------------------------------------------------------------
ADB_PATH = os.environ.get("FGO_ADB", "")  # 空=自动在常见模拟器/platform-tools 路径里找 adb
DEVICE_SERIAL = os.environ.get("FGO_SERIAL", "emulator-5554")  # 多设备时改成你的序列号，或启动加 --serial
ADB_CONNECT_RETRY = 3

# ---------------------------------------------------------------------------
# 刷本配置
# ---------------------------------------------------------------------------
# 刷多少次本后自动停止；0 表示无限刷，直到手动 Ctrl+C
NODE_COUNT = 0

# 职阶副本配置状态；后续完成各自阵容、技能、宝具模板和坐标后再启用。
BATTLE_CLASSES = {
    # 内置 default 通用壳 + 可选示例 halloween90pp；其它副本按 ADAPT_GUIDE 新增
    "default": {"label": "默认战斗（助战页启动）", "ready": True},
    "halloween90pp": {"label": "万圣节90++", "ready": True},
    "quest90": {"label": "90级本", "ready": True},
}

# 战斗相关等待时长（秒）
TURN_WAIT = 2.2        # 点完 3 张卡后，等这一回合战斗动画结算
CARD_CLICK_GAP = 0.35  # 连点手牌之间的间隔（异步截图管线下无需大间隔）
CARD_SELECT_WAIT = 1.5 # 点攻击后等待指令卡/宝具卡展开（有重点攻击重试兜底）
CARD_HAND_RETRY = 4    # 手牌识别不足时的重试次数
CARD_HAND_GAP = 0.2    # 手牌识别重试间隔；截图耗时已提供帧间隔
CARD_HAND_CONFIRM = 2  # 连续多少帧识别一致才采纳（防展开动画误判）
BATTLE_POLL = 0.9      # 战斗状态轮询间隔（异步截图管线下可更小）
STATE_POLL = 0.6       # 通用状态轮询间隔

# 单局 watchdog（秒）：进度制——每完成一回合或波次推进都会重置计时，
# 只在真正卡死（420 秒无任何回合推进）时触发，不再限制整局总时长。
BATTLE_TIMEOUT = 420
# 界面等待超时（秒）：等待某个按钮/界面出现
UI_TIMEOUT = 40

# ---------------------------------------------------------------------------
# 识别与模板
# ---------------------------------------------------------------------------
USE_TEMPLATES = True
# 识别流程串行执行，限制 OpenCV 工作线程以降低常驻内存。
OPENCV_THREADS = 2
# 模板匹配相似度阈值；本项目模板均来自横屏录屏，对角匹配≈1.0，误匹配<0.64
TEMPLATE_THRESHOLD = 0.82
# 宝具卡单独阈值（卡面会有光效，略放宽）
NP_TEMPLATE_THRESHOLD = 0.82
# 助战列表按从者名称模板定位。短名字（美杜莎 105px）只看相关系数会把
# 「美杜莎〔Lily〕/〔Alter〕」的前缀当成命中，所以还要过结构分、字形和右侧续字检查。
# 0.86 对同屏真模板偏松（真命中通常 > 0.97）。若实机正确目标长期落在 0.86–0.90，
# 只下调这一项，不要关掉 SUPPORT_STRUCTURE_MIN / SUPPORT_TRAILING_MAX。
SUPPORT_TEMPLATE_THRESHOLD = 0.96
SUPPORT_STRUCTURE_MIN = 0.92       # 0.89 的假命中会选错人，抬高结构分
SUPPORT_GLYPH_MIN = 0.88           # 与真名 0.94+ 对齐
SUPPORT_AMBIGUITY_MARGIN = 0.05    # 第二名与第一名分差小于此值则不点
SUPPORT_TRAILING_MAX = 0.08        # 短名字右侧亮字比例上限
SUPPORT_TRAILING_WIDTH_PX = 40     # 向模板右侧看多少像素
SUPPORT_SHORT_NAME_PX = 300        # 伊丽莎白全名也做右侧续字检查，挡变体前缀误匹配
SUPPORT_EDGE_MARGIN_PX = 24        # 名称贴屏幕边缘视为不完整卡片
SUPPORT_PEAK_FLOOR_MARGIN = 0.08   # 比阈值低这么多的峰也收集，用来判断歧义
SUPPORT_NAME_REGION = (0.12, 0.16, 0.62, 0.78)  # 列表里从者名字出现的区域
SUPPORT_STABLE_X_PX = 48           # 连续两帧中心点允许的抖动
SUPPORT_STABLE_Y_PX = 30
SUPPORT_SCROLLS_PER_PASS = 12      # 一轮向下找多少页；到底或用尽后刷新
SUPPORT_STAGNANT_SCROLLS = 2       # 连续几次滑动画面不变，视为列表到底
SUPPORT_REFRESH_MAX = 3            # 刷新次数用尽才停止本次出击
SUPPORT_CLICKS_PER_CANDIDATE = 2   # 同一张已确认的卡：先点名字，再点卡片中部
SUPPORT_LIST_MOVE_DELTA = 3.5      # 缩略图平均绝对差低于此值视为没滑动画
SUPPORT_REFRESH_DIALOG_DROP = 18.0 # 刷新后整屏平均亮度下降超过此值才点确认
SUPPORT_TRANSITION_TIMEOUT = 6.5   # 点击后等待离开助战列表的秒数（含加载黑屏）
SUPPORT_SELECT_TIMEOUT = 180       # 选人总超时（刷新预算之外的保险丝）
SUPPORT_CARD_CENTER_X = 0.22  # 更靠左的立绘区
# 选列表第一个时点「卡片中部」（与 fgo_bot._commit_support_click 的 mid 同 x=0.44；
# y 取列表上方第一张可见卡中部，来自助战页亮度带实测约 0.26~0.33）
SUPPORT_FIRST_CARD = (0.44, 0.30)
SUPPORT_CLICK_Y_OFFSET_PX = 70  # 相对名称上移到本卡立绘，避免点到上一行
SUPPORT_SCROLL_START = (0.50, 0.74)
SUPPORT_SCROLL_END = (0.50, 0.32)
# 歧义时只挪大约一张卡的高度，避免把唯一目标一次滑出屏幕。
SUPPORT_NUDGE_START = (0.50, 0.60)
SUPPORT_NUDGE_END = (0.50, 0.42)
# 助战列表右上角刷新（循环箭头）。国服 1920x1080 约在职阶筛选行最右侧。
# 点偏时只改这里。确认框坐标仅在画面明显变暗时才会点，避免把「决定」点到别的卡上。
SUPPORT_REFRESH_POS = (0.730, 0.185)   # 真「列表更新」按钮 (1401,200)；旧值 (0.955,0.155)=(1834,167) 其实是「降序」排序键，刷不出新候选
SUPPORT_REFRESH_CONFIRM = (0.656, 0.685)

# 名称中心距列表可视区上沿太近时，多半是半截卡，先小幅下移再点。
SUPPORT_NAME_TOP_MARGIN_PX = 90
SUPPORT_NAME_BOTTOM_MARGIN_PX = 200  # 贴底半截卡不点，先上滑（名字偏低时中部易点到下一行）
# 队伍确认页校验助战立绘（防止点错人后仍开打）
PARTY_SUPPORT_TEMPLATE = None  # 自配助战时再写 party_support_xxx.png；default 壳不校验立绘
PARTY_SUPPORT_FACE_THR = 0.70
PARTY_SUPPORT_FACE_REGION = (0.34, 0.24, 0.22, 0.40)


# 按模拟器分辨率校准职阶筛选图标后填写比例坐标；None 时不会点击。
# 例：目标助战是 Rider 时，把 rider 坐标填好，并在对应 profile 设 support_filter="rider"。
SUPPORT_CLASS_FILTERS = {}
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

# 区域限定搜索（比例坐标 x,y,w,h）；缩小搜索范围可提速 3-5x
# None 表示全屏搜索（兼容旧行为）
UI_SEARCH_REGIONS = {
    "attack":      (0.75, 0.70, 0.25, 0.25),   # 右下角攻击按钮区
    "skill_ok":    (0.20, 0.40, 0.60, 0.35),   # 中央偏上弹窗区
    "target_dlg":  (0.15, 0.12, 0.70, 0.35),   # 「请选择对象」横幅约在 y≈0.23，旧区从0.30起会漏
    "loot":        (0.75, 0.75, 0.25, 0.25),   # 右下角下一步
    "rerun":       (0.40, 0.65, 0.40, 0.30),   # 中央偏下连续出击弹窗
    "quest":       (0.40, 0.65, 0.40, 0.30),   # 中央任务开始弹窗
    "support":     (0.75, 0.15, 0.25, 0.25),   # 右上角助战确认
    "party":       (0.78, 0.80, 0.22, 0.20),   # 右下角开始任务
    "np":          (0.15, 0.10, 0.70, 0.45),   # 上半区宝具卡
}

# 战斗技能阶段（攻击按钮）检测方式："template" 模板 / "color" 主色 / "fixed" 不判断
BATTLE_DETECT_MODE = "template"
# color 模式下攻击按钮的参考 BGR（亮蓝）
ATTACK_COLOR = (210, 160, 60)

# ---------------------------------------------------------------------------
# 关键按钮坐标（比例）。雷电横屏 1920x1080，录屏实测。
# ---------------------------------------------------------------------------
POS = {
    # ---- 进本 / 支援 ----
    # 关卡确认弹窗「是否挑战该关卡」
    "quest_start":    (0.655, 0.785),  # 右「任务开始」
    "quest_cancel":   (0.344, 0.784),  # 左「取消」
    # 助战卡片中央坐标仅作兼容保留；选择流程按目标名称动态定位
    "support_confirm": (0.883, 0.280),
    # 队伍确认：右下角「开始任务」
    "party_start":    (0.912, 0.931),

    # ---- 战斗：攻击 ----
    "attack": (0.887, 0.842),
    # 选卡界面右下角「返回」
    "card_back": (0.930, 0.950),

    # ---- 战斗：技能方块（9 个，y≈0.806）----
    # 冈田以藏(从者1)：红光 / 黑影 / 蓝X
    "skill_0": (0.058, 0.800), "skill_1": (0.127, 0.800), "skill_2": (0.196, 0.800),
    # 光之高扬斯卡娅(从者2)：金剑(兔子革新者·单体) / 红光 / 红爆
    "skill_3": (0.306, 0.800), "skill_4": (0.375, 0.800), "skill_5": (0.444, 0.800),
    # 光之高扬斯卡娅(从者3)：金剑(兔子革新者·单体) / 红光 / 红爆
    "skill_6": (0.554, 0.800), "skill_7": (0.623, 0.800), "skill_8": (0.692, 0.800),

    # ---- 技能使用确认弹窗 ----
    "skill_confirm": (0.665, 0.588),  # 「决定」
    # 选目标窗底部的「决定」：选中目标后必须点它，技能才真正生效（实测 (0.497,0.893)）
    "target_confirm": (0.497, 0.893),
    "skill_cancel":  (0.331, 0.587),  # 「取消」
    # ---- 单体技能「请选择对象」弹窗：3 名从者头像（y≈0.538）----
    "servant_target_1": (0.250, 0.550),
    "servant_target_2": (0.500, 0.550),
    "servant_target_3": (0.750, 0.550),
    # 单体技能选敌人（本队用不到，给坐标备用；敌人在画面上方）
    "enemy_target_1": (0.250, 0.250),

    # ---- 宝具卡（竖卡，点攻击后出现；优先用 np_card.png 模板定位）----
    # 1 号位（冈田）实测中心；2/3 号位未在录屏出现，为推算值，多宝具时请校准
    "noble_1": (0.310, 0.300),
    "noble_2": (0.500, 0.300),
    "noble_3": (0.690, 0.300),

    # ---- 手牌 5 张（选卡阶段，水平一排，y≈0.66）----
    "card_0": (0.100, 0.700),
    "card_1": (0.300, 0.700),
    "card_2": (0.500, 0.700),
    "card_3": (0.700, 0.700),
    "card_4": (0.900, 0.700),

    # ---- 结算 / 连续出击 ----
    # 羁绊、经验结算页：点屏幕中央即可推进
    "result_tap":     (0.500, 0.500),
    # 战利品页右下「下一步」
    "result_next":    (0.867, 0.889),
    # 「是否连续进行同一关卡」弹窗
    "rerun":          (0.656, 0.809),  # 「连续出击」
    "rerun_close":    (0.346, 0.810),  # 「关闭」
    # 活动次数/风暴罐不足弹窗的「关闭」（实测 1920x1080 居中偏下）
    "event_quota_close": (0.500, 0.780),
    # 战败续关弹窗（实测 1920x1080）：撤退/使用令咒/使用圣晶石 三按钮 + 确认覆盖层
    "continue_command": (0.500, 0.444),
    "continue_quartz": (0.750, 0.444),
    "continue_confirm": (0.693, 0.596),
    # 宝具详情覆盖层（误点 NP 条/卡弹出）右上角 X 关闭按钮
    # 已废弃：实测 (1645,110) 在无面板时就是令咒命中区，点它必弹令咒（bt7.png）。
    # 关闭面板/令咒一律用返回键，见 _close_servant_panel / _close_command_spell。
    "np_detail_close": (0.857, 0.102),  # 仅保留数值，代码不再点击
    # 御主头像按钮（战斗中误开御主菜单后再点它关闭）
    "master_emblem": (0.913, 0.100),
    # 御主菜单托盘的 X 关闭按钮（实测 run14：点头像不关托盘，X 才是关闭键）
    "master_menu_close": (0.859, 0.215),
}

# ---------------------------------------------------------------------------
# 固定决策逻辑
# ---------------------------------------------------------------------------
SMART_DECISION = True

# ---- 技能 ----
# 开局(第一回合)把下列技能全部放掉。每项 (技能POS键, 目标)：
#   None                      = 群体/自身技能，点技能→决定即可
#   ("servant", 1/2/3)        = 单体技能，点决定后在「请选择对象」里点对应从者
#   ("enemy", 1)              = 单体技能选敌人
# CD 中 / 不想开的技能直接从列表删除。脚本会等弹窗出现再点，避免误触。
USE_SKILLS = True
# 目标类型已经真实弹窗描述逐一核实（probe_grid.png）：
#   冈田 skill_0 人斩 / skill_1 心眼(伪) / skill_2 击剑矫捷如鹰隼 均为「自身」
#   高扬 skill_3/6 兔子革新者（己方单体·宝具值增加）
#   高扬 skill_4/7 杀戮技巧(人)（己方单体·人类特攻+宝具值增加）
#   高扬 skill_5/8 NFF特别服务（己方单体·力指令/红卡性能）
#   => 6 个高扬技能全部为「己方单体」，目标统一选冈田(从者1)。
# 公开示例不附带私有实战 SKILLS 列表；技能顺序写在各 class 的 profile["skills"] 里。
BATTLE_CLASS_PROFILES = {
    "default": {
        "support_label": "列表第一个",
        "support_pick_first": True,   # 不按名字模板找人，点助战列表上方第一张卡
        "support_filter": None,
        # 无 party_support_template：队伍页不做立绘校验（通用壳无法预知助战是谁）
        "skills": [
            ("skill_0", ("servant_if_prompt", 3)),
            ("skill_1", ("servant_if_prompt", 3)),
            ("skill_2", ("servant_if_prompt", 3)),
            ("skill_3", ("servant_if_prompt", 3)),
            ("skill_4", ("servant_if_prompt", 3)),
            ("skill_5", ("servant_if_prompt", 3)),
            ("skill_6", ("servant_if_prompt", 3)),
            ("skill_7", ("servant_if_prompt", 3)),
            ("skill_8", ("servant_if_prompt", 3)),
        ],
        "check_skills_each_turn": True,
        "skill_reclick": 1,
        "servant_target_tap_mode": "double_frame",
        "require_servant_recognition": False,
        # 三槽位均可放；无 min_wave；多就绪时由 np_release_all_ready 同回合先放完
        "np_templates": [
            {"servant": 0, "from_turn": 0},
            {"servant": 1, "from_turn": 0},
            {"servant": 2, "from_turn": 0},
        ],
        "np_release_all_ready": True,
        # 不设 preferred_servant：选卡走 decision 原有默认连携规则
    },
    "halloween90pp": {
        "support_label": "伊丽莎白·巴托里",
        "support_template": "support_elizabeth.png",
        "party_support_template": "party_support_elizabeth.png",
        # 助战列表默认职阶即可，不点筛选
        "support_filter": None,
        "opening_skills": [
            ("skill_2", None),              # 娄希第3技能，开局优先充能
        ],
        "skills": [
            # 主力=伊丽莎白（3号位）。单体 buff 一律给她。
            # servant_if_prompt=只在弹出「请选择对象」窗时才点目标；
            # double_frame 模式保证点得到且不误点。
            # skill_3=高扬斯卡娅第1技能「NFF特别服务」是己方单体卡性能/集星 buff；给 3 号位。
            ("skill_0", ("servant_if_prompt", 3)),
            ("skill_1", ("servant_if_prompt", 3)),
            ("skill_2", ("servant_if_prompt", 3)),
            ("skill_3", ("servant", 3)),
            ("skill_4", ("servant_if_prompt", 3)),
            ("skill_5", ("servant_if_prompt", 3)),
            ("skill_6", ("servant_if_prompt", 3)),
            ("skill_7", ("servant_if_prompt", 3)),
            ("skill_8", ("servant_if_prompt", 3)),
        ],
        # 2号位(暗之高扬斯卡娅)单体 buff 需选目标；double_frame 防误点详情面板
        "servant_target_tap_mode": "double_frame",
        "check_skills_each_turn": True,
        "skill_reclick": 1,
        "require_servant_recognition": False,
        "np_templates": [
            {"template": "np_card_louhi.png", "servant": 0, "from_turn": 0},
            # 伊丽莎白无专用模板：按 3 号位 + 存在性采样；min_wave=3 留到末波
            {"servant": 2, "from_turn": 0, "min_wave": 3},
        ],
        "preferred_servant": 2,
        "prefer_servant_every_turn": True,
    },
    "quest90": {
        # Q1-Q8: 3波；娄希伤+光高扬辅+卡斯特辅；助战卡斯特；主攻1；全技能指向1；宝具全放
        "support_label": "阿尔托莉雅・卡斯特",
        "support_template": "support_castoria.png",
        "support_filter": None,
        "skills": [
            ("skill_0", ("servant_if_prompt", 1)),
            ("skill_1", ("servant_if_prompt", 1)),
            ("skill_2", ("servant_if_prompt", 1)),
            ("skill_3", ("servant_if_prompt", 1)),
            ("skill_4", ("servant_if_prompt", 1)),
            ("skill_5", ("servant_if_prompt", 1)),
            ("skill_6", ("servant_if_prompt", 1)),
            ("skill_7", ("servant_if_prompt", 1)),
            ("skill_8", ("servant_if_prompt", 1)),
        ],
        "check_skills_each_turn": True,
        "skill_reclick": 1,
        "servant_target_tap_mode": "double_frame",
        "require_servant_recognition": False,
        "np_templates": [
            {"template": "np_card_louhi.png", "servant": 0, "from_turn": 0},
            {"servant": 1, "from_turn": 0},
            {"servant": 2, "from_turn": 0},
        ],
        "np_release_all_ready": True,
        "preferred_servant": 0,
        "prefer_servant_every_turn": True,
    }

}


# 截图实测约 1.3s/张；短暂等待后立即截图，首帧仍约在点击后 1.5s 到达。
SKILL_DIALOG_WAIT = 1.0 # 点技能图标后等待确认窗
SKILL_DIALOG_RETRY  = 6    # 弹窗未识别到时的补截图次数（实测确认窗可晚到 1.5-2s）
SKILL_DIALOG_GAP    = 0.35 # 补截图额外等待；截图耗时已形成间隔
SKILL_RECLICK       = 1    # 弹窗始终不出现时，重新点一次技能图标的次数（防点击被动画吞掉）
SKILL_CONFIRM_WAIT  = 1.0  # 点「决定」后等待目标弹窗稳定
SKILL_TARGET_WAIT   = 0.6  # 点目标后短暂等待，再轮询攻击按钮
SKILL_TARGET_RETRY  = 5    # 「请选择对象」未识别到时的补截图次数
SKILL_TARGET_GAP    = 0.35 # 补截图额外等待
SKILL_RETURN_TIMEOUT = 10.0 # 单个技能操作后，等待回到技能阶段(攻击按钮)的最长时间
# 异步截图管线：后台线程持续截图，主线程读取最新帧（减少串行等待）
ASYNC_SCREENSHOT = True            # 后台截图管线：_shot 直接读最新帧，点击后自动等新帧
ASYNC_SCREENSHOT_INTERVAL = 0.5    # 后台截图间隔秒数（0.3→0.5，减轻模拟器内存/CPU 压力）
SCREENSHOT_FAIL_LIMIT = 5           # 连续几次截图失败/超时就判定模拟器无响应并停止脚本

# 技能 CD 检测：点击前检查图标亮度，低于阈值视为 CD 中直接跳过
SKILL_CD_CHECK = True
SKILL_AVAILABLE_BRIGHTNESS = 82   # 灰度均值阈值；run10 实测可用最低90、CD最高76，取82留双侧余量
SKILL_CD_ROI = (0.0, 0.0, 0.045, 0.055)  # 技能图标采样框（相对中心偏移+宽高，比例）

# 阵亡换人检测：每回合比较前排三头像，头像变化则清空已用技能记录让后备成员放技能
SLOT_SWAP_DETECT = True
SLOT_SWAP_THRESHOLD = 0.55  # 原0.8：动画闪动0.69~0.80会误清空已用技能
SLOT_FACE_ROIS = [
    (0.0417, 0.9037, 0.0573, 0.0963),
    (0.2917, 0.9037, 0.0573, 0.0963),
    (0.5417, 0.9037, 0.0573, 0.0963),
]

# 选卡视图上层宝具卡：1/2/3 号位（前排槽位）固定中心坐标。
# 1 号位实测 (0.34,0.30)（user_open.png 模板命中 (655,315)）；2/3 号位按等距推断，
# 实战由存在性采样验证（有卡均值149 vs 空槽32-40）。模板命中时会自动记忆修正。
NP_SLOT_POSITIONS = [(0.310, 0.300), (0.500, 0.300), (0.690, 0.300)]  # 与 noble_1/2/3 完全一致，点击只走固定槽
# 波次计数器（HUD 右上「场次 x/3」数字）检测区域与阈值；模板 templates/wave_1..3.png
# 离线验证 7/7 正确（置信度 0.90-0.92）。用于 np_templates 条目的 min_wave 门控。
WAVE_COUNTER_REGION = (0.685, 0.0, 0.045, 0.06)
WAVE_MATCH_THRESHOLD = 0.85
NP_SLOT_ROI = (0.07, 0.15)  # 存在性采样半宽/半高（比例）
NP_SLOT_MIN_BRIGHTNESS = 100  # 真宝具卡常见 102-155；空槽约 30-65。原 120 会漏检偏暗宝具卡（select_view 实测 102/116 被跳过）
NP_SLOT_SOFT_BRIGHTNESS = 95  # 低于硬阈值但明显高于其它空槽时也认
# 兼容旧字段名（保留，防止其他模块引用报错）
SKILL_CLICK_WAIT = SKILL_DIALOG_WAIT
SKILL_DIALOG_TIMEOUT = 6.0
SKILL_TARGET_TIMEOUT = 4.0
SKILL_TARGET_THRESHOLD = 0.72
# 点肖像位（选己方目标）时用的更严阈值：真窗口 0.98，假匹配 0.72-0.80
SKILL_TARGET_CLICK_THRESHOLD = 0.88  # 请选择对象条幅；假匹配由「攻击键可见=丢弃命中」门禁兜底（F9），阈值回 0.72 保真窗召回
# 己方单体目标点击策略（用户 m01922 要求彻底删除肖像位点击）：
#   "never"       = 永不点 servant_target_N；真弹出目标窗时按返回键取消选目标（弹窗在时返回键安全），技能放弃
#   "double_frame"= 连续两帧命中目标窗且攻击键隐藏才点（saber/assassin 依赖单体 buff 的副本用）
SERVANT_TARGET_TAP_MODE = "never"
MASTER_MENU_THRESHOLD = 0.7  # 御主菜单托盘检测阈值：展开帧实测 0.72-0.78，干净帧 0.18-0.25
# 技能使用确认窗「取消」按钮（与 skill_confirm 同一行）；用于关闭 kill 残留的确认窗
SKILL_CANCEL_POS = (0.332, 0.588)


# ---- 宝具 ----
USE_NOBLE_PHANTASM = True
# 宝具卡检测：用 np_card.png 模板在画面上半部找竖卡；找不到则视为本回合无宝具
# 注意：公开仓不附带具体从者宝具竖卡；有模板时写入 profile.np_templates，否则靠号位存在性采样
NP_DETECT_MODE = "template"  # "template" / "contour" / "auto"；实测 contour 会在过渡动画帧误检(0.63,0.40)，默认纯模板
# 宝具点击：fixed_slot=只点 noble_1/2/3 固定坐标（模板只负责“有没有/是谁”）
NP_CLICK_MODE = "fixed_slot"
# 全局默认不禁用任何人宝具；职阶 profile.np_disabled_servants 可覆盖
NP_DISABLED_SERVANTS = []
# 用手牌大图与「该号位宝具卡面」比对，认指令卡归属（HUD 头像对不上卡面）
CARD_NP_FACE_MATCH_THR = 0.45
CARD_ART_BOX = (0.0, -0.02, 0.12, 0.22)
NOBLE_CLICK_WAIT = 0.7
# 宝具竖卡轮廓检测参数（NP_DETECT_MODE 为 contour/auto 时生效）
NP_CONTOUR_SEARCH_REGION = (0.15, 0.10, 0.70, 0.45)  # (x, y, w, h) 比例，画面上半区
NP_CONTOUR_MIN_AREA = 3000    # 最小面积（像素），过滤噪声
NP_CONTOUR_MAX_AREA = 120000  # 最大面积（实测宝具竖卡约 270x330=89k px²，原 80000 会滤掉）
NP_CONTOUR_MIN_ASPECT = 1.2   # 最小宽高比（竖卡 > 1）
NP_CONTOUR_CANNY = (50, 150)  # Canny 边缘检测阈值

# ---- 手牌识别（横屏 1920x1080）----
# 卡名采样框：相对每个手牌槽中心的偏移 (dx, dy, 宽, 高)，占屏比例
# 卡名「力击/技击/迅击」彩色星形位于卡槽中心偏下
CARD_COLOR_BOX = (0.0, 0.14, 0.06, 0.12)
# 卡牌上半部立绘区（识别从者，需要 servant_1/2/3.png 模板）
CARD_SERVANT_BOX = (0.0, -0.06, 0.07, 0.14)
CARD_COLOR_RATIO_MIN = 0.18
# 宝具卡色采样框：相对宝具卡中心（模板定位点）的偏移；卡色星形在卡中下部
NP_COLOR_BOX = (0.0, 0.08, 0.09, 0.12)
USE_SERVANT_TEMPLATES = True

# ---- 连携优先级（数字越小越优先）：同从者三连 > 三色 > 红 > 蓝 > 绿 ----
CHAIN_PRIORITY = ["brave", "tricolor", "buster", "arts", "quick"]
# 无连携时单卡打分（B>A>Q）及同色/同从者奖励
COLOR_SCORE = {"B": 3, "A": 2, "Q": 1, "?": 0}
SAME_COLOR_PAIR_BONUS = 2
SAME_SERVANT_PAIR_BONUS = 1

# ---- 结算 ----
# 结算页（羁绊/经验/战利品）整体最长推进时间
RESULT_TIMEOUT = 40
# 等待「连续出击」弹窗出现的最长时间
RERUN_TIMEOUT = 15

# ---------------------------------------------------------------------------
# 苹果消耗配置
# ---------------------------------------------------------------------------
# AP 不足时自动吃苹果；设 count=0 表示不吃该种苹果。
# priority 决定优先吃哪种（数字越大越优先）；金苹果通常留给高难本。
# 所有种类累计达到各自 count 上限后停止刷本。
APPLE_CONFIG = {
    "enabled": True,
    # 公开默认不吃苹果；需要时自行改 count，或启动时加 --apple-gold N 等
    "gold":   {"count": 0, "priority": 2},   # 金苹果（+140 AP）
    "silver": {"count": 0, "priority": 1},   # 银苹果
    "bronze": {"count": 0, "priority": 0},   # 铜苹果
}
# AP 不足弹窗中各苹果的点击坐标（比例）；国服横屏 1920x1080 实测 2026-10-29
# 行动力回复弹窗行序（从上到下）：圣晶石 → 黄金果实 → 白银果实 → 青铜果实(需下滑)
APPLE_POSITIONS = {
    "quartz": (0.500, 0.245),
    "gold":   (0.500, 0.454),
    # 白银行中心（标题区 y≈0.60，蓝条≈0.66）；偏下易点到列表底部/滚动条
    "silver": (0.500, 0.620),
    "bronze": None,   # 青铜果实需滚动列表，暂不支持自动点击
}
# AP不足确认弹窗上的「回复AP」按钮（进入苹果选择弹窗的入口）
AP_RECOVER_BUTTON = (0.654, 0.782)
# 消耗确认弹窗「决定」中心（1920x1080 实测 2026-11-05：取消0.352/决定0.648，y=0.808）
# 注意：列表页同 Y 附近是「兑换青铜树苗」，必须等确认弹窗出现后再点
APPLE_CONFIRM = (0.648, 0.808)
APPLE_CANCEL = (0.352, 0.808)
# 单次副本消耗的 AP 值（用于预估剩余次数，非必须）
AP_PER_RUN = 40

# ---------------------------------------------------------------------------
# 异常恢复配置
# ---------------------------------------------------------------------------
# 网络错误重试
NETWORK_RETRY_MAX = 3
NETWORK_RETRY_WAIT = 5.0     # 每次重试前等待秒数
# 战斗失败处理
BATTLE_LOST_ACTION = "continue_command"  # "retry" 重新进本 / "stop" 停止 / "continue_command" 令咒续关 / "continue_quartz" 圣晶石续关（用户禁用）
BATTLE_LOST_CONTINUE_MAX = 0  # quest90 要求败北不续；万圣节若要续令咒改回 1
# 维护检测
MAINTENANCE_STOP = True       # 检测到维护公告立即停止

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
SAVE_DEBUG_SHOT = True
DEBUG_SHOT_MAX_FILES = 10
DEBUG_SHOT_MIN_INTERVAL = 60.0
# 启动时自动清理旧运行日志；只删 logs/run_*.log，不动当前即将创建的文件
LOG_KEEP_COUNT = 15          # 最多保留最近 N 份 run_*.log；0=不自动删
LOG_KEEP_DAYS = 7            # 同时丢掉超过 N 天的 run_*.log；0=不按天删
