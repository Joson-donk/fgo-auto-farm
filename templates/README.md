# 模板目录说明（公开示例版）

本目录只保留 **通用 UI** 模板，按 **1920×1080 横屏 / FGO 国服** 裁剪。
公开仓**不附带**具体从者的助战名条、宝具竖卡、队伍立绘校验图——那些请按 `ADAPT_GUIDE.txt` 第九节自己裁。

模板匹配阈值默认见 `config.py` 的 `TEMPLATE_THRESHOLD`（约 0.82）。

## 已随仓提供（通用 UI）

| 文件名 | 用途 |
|---|---|
| `battle_attack.png` | 战斗技能阶段「攻击」钮 |
| `skill_confirm.png` | 「技能使用」窗的决定 |
| `target_dialog.png` | 「请选择对象」标题 |
| `result_next.png` / `result_exp.png` | 结算推进 |
| `rerun.png` | 「连续出击」 |
| `quest_start.png` / `party_start.png` | 进本 |
| `support_confirm.png` | 识别助战页（不用于点选具体人） |
| `support_page_btn.png` | 助战相关控件 |
| `select_back.png` | 返回 |
| `servant_panel.png` | 从者详情/面板（误开时关闭） |
| `master_menu.png` / `command_spell.png` | 御主菜单 / 令咒相关识别 |
| `ap_empty.png` / `ap_short.png` / `apple_list.png` | AP 不足与苹果列表 |
| `wave_1.png` `wave_2.png` `wave_3.png` | HUD「场次 x/3」 |
| `np_detail.png` | 宝具详情类界面（通用） |

## `default` 壳需要你自备的吗？

**不需要。** `default` 使用 `support_pick_first=True`（点列表第一张），宝具靠号位存在性采样，不依赖私有名条/宝具模板。

## 自配新本时建议补充

| 文件名 | 用途 |
|---|---|
| `support_<key>.png` | 助战列表上的从者**名称**一行 |
| `party_support_<key>.png` | 队伍确认页助战立绘校验（推荐） |
| `np_card_<name>.png` | 点攻击后的宝具**竖卡**（必须从选卡视图裁） |
| `servant_1/2/3.png` | 手牌底部小头像（可选，用于 Brave 同从者三连） |

```text
python calibrate.py --crop battle_attack
python calibrate.py --crop support_你的助战名
python calibrate.py --crop np_card_你的宝具
```

## 注意

- 模板必须用**你自己的游戏界面**截（同分辨率、同服务器）。
- 匹配不上先查裁剪与分辨率，不要一上来就降全局阈值（见 ADAPT_GUIDE 坏提示词反例）。
