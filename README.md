# FGO 自动刷本（固定决策 · 公开示例版）

用 **ADB + OpenCV 模板匹配** 在安卓模拟器（或能 ADB 的设备）上自动刷 FGO 国服固定关卡。
**不依赖任何 AI 决策**：技能顺序、选卡连携、助战策略都写在配置里。

公开仓附带：

- 完整引擎（截图 / 点击 / 状态机 / 助战 / 选卡）
- 通用 UI 模板（攻击钮、确认窗、结算、连续出击、苹果列表等）
- **`default` 战斗壳**：停在助战页即可开刷（点列表第一张助战 → 一波开全技能 → 就绪宝具同回合放完）
- 适配规范 `ADAPT_GUIDE.txt`（给人 / 给 AI 配新本用）

不附带：作者本机私有配队（daily/saber/assassin 实战）、私有从者立绘/宝具/助战名条模板、本机苹果预算与模拟器绝对路径。

---

## 一、环境

1. 任意能用 ADB 的安卓模拟器（雷电 / MuMu / BlueStacks 等）或真机；游戏分辨率设为 **1920×1080 横屏**。
2. Python 3.10+（推荐 3.13）。
3. 安装依赖：

```text
python -m pip install -r requirements.txt
```

（可用国内镜像：`-i https://pypi.tuna.tsinghua.edu.cn/simple`）

4. 配置 ADB（任选其一）：
   - 改 `config.py` 里的 `ADB_PATH` / `DEVICE_SERIAL`
   - 或设环境变量 `FGO_ADB`、`FGO_SERIAL`
   - 或启动时加 `--serial emulator-XXXX`
   - `ADB_PATH` 留空时，会在常见模拟器目录和 `platform-tools` 里自动找 `adb`

---

## 二、先确认连通（只截图，不点击）

```text
python main.py --dry-run
```

看到 `截图成功: logs/check.png` 即表示 ADB 通、分辨率可读。打开该图确认是 FGO 画面。

---

## 三、用默认壳跑一局

1. 游戏里手动点到目标关卡，**停在助战选择页**（或任务开始 / 队伍确认页）。
2. 运行：

```text
python main.py --class default --count 1
```

或双击 `start_farm.bat` 打开图形启动页，选「默认战斗（助战页启动）」。

常用参数：

```text
python main.py --list-classes
python main.py --class default --count 10
python main.py --class default --battle-only --count 1
python main.py --class default --no-apple
python main.py --serial emulator-5554
```

- 中途停止：终端 **Ctrl+C**。
- 公开默认 **不吃苹果**（`APPLE_CONFIG` 各类 count=0）。需要时改配置或加 `--apple-gold N` 等。
- 调试截图在 `logs/debug/`；运行日志 `logs/run_*.log`。

---

## 四、配自己的本

完整规范见 **`ADAPT_GUIDE.txt`**（强制问答、提示词模板、校准、日志排障、坏提示词反例）。

最短路径：

1. 在助战页用 `calibrate.py --crop ...` 裁自己的助战名条 / 宝具竖卡（如需）。
2. 在 `config.py` 增加 `BATTLE_CLASSES["你的key"]` 与 `BATTLE_CLASS_PROFILES["你的key"]`。
3. `python main.py --class 你的key --count 1`，对照 `ADAPT_GUIDE` 第十节读日志。

给 AI 适配时：把 `ADAPT_GUIDE.txt` 全文 + 你的配队答案发给模型，并要求遵守文件里的协作铁律。

---

## 五、分辨率与模板

- 坐标与模板均按 **1920×1080 横屏** 校准；换分辨率必须整套重裁，不要只改一两个点。
- `templates/` 只放通用 UI；从者相关 PNG 请自己裁进本目录并在 profile 里引用。
- 模板对不上时脚本会等待并超时停，**不会瞎点**。

---

## 六、项目结构

| 文件 | 作用 |
|---|---|
| `config.py` / `config.example.py` | 参数与职阶 profile |
| `adb.py` | 截图 / 点击 / 滑动 |
| `vision.py` | 模板匹配 |
| `card_recognition.py` / `decision.py` | 手牌与连携选卡 |
| `support_select.py` | 助战匹配与翻页 |
| `fgo_bot.py` | 状态机 |
| `calibrate.py` | 裁模板 / 校验 |
| `main.py` / `launcher.py` / `start_farm.bat` | CLI / GUI |
| `ADAPT_GUIDE.txt` | 适配与协作规范 |
| `test_*.py` | 不连模拟器的单测 |

---

## 七、许可

MIT License（见 `LICENSE`）。游戏本身及素材版权归 TYPE-MOON / 官方运营所有；本仓库只提供自动化脚本与通用 UI 截图标定示例。
