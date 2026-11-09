# 首次上手剧本（给 AI / 给第一次克隆的人）

> 权威全文在 `ADAPT_GUIDE.txt` 的「〇、首次克隆上手」。本文件是同一剧本的可读版。

## 推荐主路径

`clone` → 把 ADAPT_GUIDE 〇节提示词喂给 AI → AI 按剧本推进 → 停助战页 → **双击 `start.bat`** 选 default 或「万圣节90++」开刷。

## AI 必须按序主动推进（缺一步就停并问用户，禁止跳步）

1. **先完整阅读** `ADAPT_GUIDE.txt` 与 `README.md`（铁律、词汇、default 能力以文档为准，禁止编造功能）。
2. **用短中文说明项目怎么运行**：主路径是「连通后双击 `start.bat` → 选默认战斗」；不要一上来堆一长串 CLI。
3. **配置环境**：确认 Python 3.10+；无 `.venv` 时由 `start.bat` 自动创建并 `pip install -r requirements.txt`（也可指导用户双击一次 `start.bat` 完成装依赖）。不要要求先配 VS Code。
4. **连接模拟器**：找 adb（LDPlayer9/14、MuMu、BlueStacks、platform-tools）；确认设备在线；分辨率 **1920×1080 横屏**；跑 `python setup_connect.py`（或 `python main.py --dry-run`），截图成功后明确反馈 **「连通 OK，可双击 start.bat」**。
5. **主动告知**：仓库已自带 **default 默认战斗壳**，以及可选示例 **halloween90pp（万圣节90++）**——default 停助战页即可开刷；halloween90pp 需前排娄希/暗高扬/伊丽莎白+助战伊丽莎白。描述以第八节摘要为准，别编功能。
6. **再问用户要打什么本；若要配新本，按 Q1→Q7 **一次只问一题****：若只想先试 default → 引导停助战页后双击 `start.bat`；若打万圣节90++示例 → 选「万圣节90++」；若要自配新本 → 按铁律问清 Q1–Q7 再改 `config.py` / 模板，默认不动引擎。

## 用户侧最短操作

1. 安装 Python 3.10+（勾选 Add to PATH），打开模拟器并设 1920×1080 横屏。
2. 把 ADAPT_GUIDE 〇节整段提示词发给 AI，让 AI 做完连通。
3. 游戏里点到目标关卡，**停在助战选择页**。
4. 双击 `start.bat` → 选「默认战斗（助战页启动）」→ 开始。
