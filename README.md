# FGO 自动刷本（固定决策 · 公开示例版）

用 **ADB + OpenCV 模板匹配** 在安卓模拟器（或能 ADB 的设备）上自动刷 FGO 国服固定关卡。
**不依赖任何 AI 决策**：技能顺序、选卡连携、助战策略都写在配置里。

公开仓附带：完整引擎、通用 UI 模板、**`default` 战斗壳**、适配规范 `ADAPT_GUIDE.txt`。
不附带：作者本机私有配队、私有从者模板、本机苹果预算与模拟器绝对路径。

---

## 给第一次克隆的人 / 给 AI（主路径）

推荐顺序（**不要一上来堆 CLI**）：

1. 安装 **Python 3.10+**（安装时勾选 Add to PATH），打开模拟器，分辨率设为 **1920×1080 横屏**。
2. 把 `ADAPT_GUIDE.txt` 里 **「〇、首次克隆上手」** 整段提示词发给 AI（或自己打开 `ONBOARDING.md` 对照）。
3. 让 AI **按剧本推进**：读规范 → 短中文说明怎么跑 → 配环境 → 连模拟器并截图验证 → 告知已有 default 壳 → 再问你要打什么本。
4. 连通成功后，游戏里手动点到关卡并 **停在助战选择页**。
5. **双击 `start.bat`** → 选「默认战斗（助战页启动）」开刷。  
   - 第一次双击若没有 `.venv`，脚本会自动创建虚拟环境并安装 `requirements.txt`（优先清华源）。  
   - 不需要先配 VS Code。

连通自检（AI 或你自己跑均可）：

```text
python setup_connect.py
```

看到 **`连通 OK，可双击 start.bat`** 即可。仅一台设备时可加 `--write` 把 `DEVICE_SERIAL` 写入 `config.py`。

更细的铁律、Q1–Q7、校准与日志排障见 **`ADAPT_GUIDE.txt`**（唯一权威）。上手剧本可读版见 `ONBOARDING.md`。

---

## default 默认战斗壳能做什么

停在助战页后选「默认战斗」：

- 助战：点列表 **第一张**（不匹配私有名字模板）
- 技能：一波 `skill_0..8` 全开；指向技有弹窗则给 **3 号位**
- 宝具：三槽无 `min_wave` 限制；多槽就绪时可同回合先放完
- 选卡：走引擎默认连携规则
- 苹果：公开默认 **不吃**（`APPLE_CONFIG` 各类 count=0）

---

## 进阶：命令行（可选）

```text
python setup_connect.py
python setup_connect.py --write
python main.py --dry-run
python main.py --list-classes
python main.py --class default --count 1
python main.py --class default --battle-only --count 1
python main.py --serial emulator-5554
```

- 中途停止：终端 **Ctrl+C**（或关闭 launcher）。
- `start_farm.bat` 仍可用，内部转发到 `start.bat`。
- ADB：`config.py` 的 `ADB_PATH` / `DEVICE_SERIAL`，或环境变量 `FGO_ADB` / `FGO_SERIAL`；`ADB_PATH` 留空会自动找常见模拟器路径（含 LDPlayer9/14、MuMu、BlueStacks、platform-tools）。

---

## 配自己的本

见 **`ADAPT_GUIDE.txt`**：强制问答 Q1–Q7、提示词模板、校准、日志排障、坏提示词反例。

最短路径：裁模板 → 写 `BATTLE_CLASSES` / `BATTLE_CLASS_PROFILES` → `--count 1` 对照第十节读日志。默认只动配置/模板；动引擎须先征得同意。

---

## 分辨率与模板

- 坐标与模板均按 **1920×1080 横屏**；换分辨率必须整套重裁。
- `templates/` 只放通用 UI；从者相关 PNG 请自己裁并在 profile 里引用。
- 模板对不上时脚本会等待并超时停，**不会瞎点**。

---

## 项目结构

| 文件 | 作用 |
|---|---|
| `start.bat` | 主入口：无 venv 则自建并装依赖，再开 GUI |
| `start_farm.bat` | 兼容旧名，转发到 `start.bat` |
| `setup_connect.py` | 一键连通：adb / 设备 / 截图 |
| `launcher.py` / `main.py` | GUI / CLI |
| `config.py` / `config.example.py` | 参数与职阶 profile |
| `adb.py` `vision.py` `fgo_bot.py` … | 引擎 |
| `ADAPT_GUIDE.txt` | 适配与协作规范（权威） |
| `ONBOARDING.md` | 首次上手剧本（可读版） |
| `test_*.py` | 不连模拟器的单测 |

---

## 许可

MIT License（见 `LICENSE`）。游戏本身及素材版权归 TYPE-MOON / 官方运营所有；本仓库只提供自动化脚本与通用 UI 截图标定示例。
