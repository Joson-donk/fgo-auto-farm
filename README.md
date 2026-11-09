# FGO 自动刷本

克隆之后，最快的用法是：**把下面那段话发给 AI**。让它陪你装好 Python、找到 ADB、连上模拟器、看截图对不对，再告诉你怎么开刷。

这是一份跑在安卓模拟器上的 **FGO 国服**自动刷本脚本：用 **ADB** 截屏点击，用 **OpenCV 模板匹配**认界面。助战、技能、宝具、选卡都写在配置里，开战之后按**固定决策脚本**执行。AI 用在上手和以后换本：安装 Python / 虚拟环境、找 ADB、连通并核对截图、讲清楚怎么跑；要打新关卡时，按 `ADAPT_GUIDE.txt` 改配置和模板。

公开仓附带：完整引擎、通用 UI 模板、**`default` 默认战斗壳**、`ADAPT_GUIDE.txt`、`ONBOARDING.md`、`setup_connect.py`、`start.bat`（没有 `.venv` 时会自动创建并安装依赖）。

不含：私有关卡配队、私有从者模板、本机苹果预算、本机 ADB 绝对路径。

细节以 `ADAPT_GUIDE.txt` 为准。`ONBOARDING.md` 是同一套上手步骤的可读版。

---

## 把这段话发给 AI

复制到 Cursor 或任何能读这个仓库的助手里：

```text
我刚克隆了这个 FGO 自动刷本仓库，请帮我装好环境并连上模拟器。

请先阅读 ADAPT_GUIDE.txt 的「〇、首次克隆上手」和 ONBOARDING.md（细节以 ADAPT_GUIDE.txt 为准），再按那个剧本推进：
1. 用短中文说明怎么跑：连通后双击 start.bat，选「默认战斗」。
2. 确认本机有 Python 3.10+。没有的话，告诉我怎么安装，并勾选 Add to PATH。需要时指导我双击 start.bat，或在项目目录建好 .venv 并安装 requirements.txt。
3. 找到本机 ADB（LDPlayer9/14、MuMu、BlueStacks、platform-tools 都可以），确认设备在线，分辨率是 1920×1080 横屏。
4. 在项目目录运行 python setup_connect.py。截图成功后，请原句反馈「连通 OK，可双击 start.bat」，并告诉我截图路径。只有一台设备、需要记住序列号时，再跑 python setup_connect.py --write。

先保持引擎不动，也不要适配私有配队。连通之后告诉我：游戏里停在助战选择页，双击 start.bat，选「默认战斗（助战页启动）」。
```

---

## 你这边的最短路径

1. 安装 **Python 3.10+**（勾选 Add to PATH），打开模拟器，分辨率设为 **1920×1080 横屏**。
2. 把上面那段话发给 AI，或自己打开 `ONBOARDING.md` 对照。完整提示词在 `ADAPT_GUIDE.txt` 的「〇、首次克隆上手」。
3. 让 AI 按剧本走完：读规范 → 用短中文说明怎么跑 → 配环境 → 连模拟器并截图验证 → 告诉你仓库里已有 default 壳。
4. 游戏里手动点到关卡，**停在助战选择页**。
5. **双击 `start.bat`** → 选「默认战斗（助战页启动）」开刷。公开仓目前只有这一类。
   - 第一次双击如果还没有 `.venv`，脚本会自动创建虚拟环境并安装 `requirements.txt`（优先清华源）。
   - 直接双击即可，不必先配置编辑器。

连通自检（AI 或你自己都可以跑）：

```text
python setup_connect.py
```

看到 **`连通 OK，可双击 start.bat`** 就可以开刷。只有一台设备时，加上 `--write`，把 `DEVICE_SERIAL` 写入 `config.py`。

---

## default 默认战斗壳

停在助战页、选「默认战斗」之后：

- **助战**：点列表第一张（按位置点，不匹配私有名字模板）
- **技能**：一波把 `skill_0`…`skill_8` 全开；出现选目标弹窗时给 **3 号位**
- **宝具**：就绪就放；同一回合多张都就绪时，这一回合先放完
- **选卡**：走引擎默认连携规则
- **苹果**：默认不吃（`APPLE_CONFIG` 里各类 `count` 为 0）

能力边界以 `ADAPT_GUIDE.txt` 第八节为准。

---

## 进阶：命令行（可选）

日常开刷用双击 `start.bat`。想自己敲命令时：

```text
python setup_connect.py
python setup_connect.py --write
python main.py --dry-run
python main.py --list-classes
python main.py --class default --count 1
python main.py --class default --battle-only --count 1
python main.py --serial emulator-5554
```

- 中途停止：在终端按 **Ctrl+C**，或关掉 launcher 窗口。
- `start.bat` 会在没有 `.venv` 时创建虚拟环境并安装 `requirements.txt`，然后启动图形界面 `launcher.py`。
- ADB 写在 `config.py` 的 `ADB_PATH` / `DEVICE_SERIAL`，也可以用环境变量 `FGO_ADB` / `FGO_SERIAL`。`ADB_PATH` 留空时会自动找常见模拟器路径（LDPlayer9/14、MuMu、BlueStacks、platform-tools）。

---

## 配自己的本

按 **`ADAPT_GUIDE.txt`** 来：一次一题问清 Q1–Q8，再裁模板、改配置。校准、日志排障和坏提示词反例都在这份规范里。

最短路径：裁模板 → 写入 `BATTLE_CLASSES` / `BATTLE_CLASS_PROFILES` → 用 `--count 1` 跑一轮，对照第十节看日志。默认只动配置和模板；要改引擎先征得同意。

---

## 分辨率与模板

- 坐标和模板都按 **1920×1080 横屏** 裁切。换分辨率需要整套重裁。
- `templates/` 里是通用 UI。从者相关的 PNG 请自己裁，并在 profile 里引用。
- 模板对不上时，脚本会等待，超时后停止。

---

## 项目结构

| 文件 | 作用 |
|---|---|
| `start.bat` | 主入口：没有 `.venv` 就创建并安装依赖，再打开 GUI |
| `setup_connect.py` | 一键连通：找 adb、列设备、截图验证 |
| `launcher.py` / `main.py` | 图形界面 / 命令行 |
| `config.py` / `config.example.py` | 参数与职阶 profile |
| `adb.py` `vision.py` `fgo_bot.py` … | 引擎 |
| `ADAPT_GUIDE.txt` | 适配与协作规范（权威） |
| `ONBOARDING.md` | 首次上手剧本（可读版） |
| `test_*.py` | 不连模拟器的单元测试 |

---

## 许可

MIT License（见 `LICENSE`）。游戏本身及素材版权归 TYPE-MOON / 官方运营所有；本仓库提供自动化脚本，以及通用 UI 截图的标定示例。
