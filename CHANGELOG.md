## 提示词

- 缺模板时：AI **一次只向用户要一张截图**，验收后再要下一张。

## 提示词

- 新本适配：AI 必须按 Q1→Q7 **一次只问一题**，禁止一次甩整张问卷。

## 副本类型

- 公开仓仅保留 `default` 默认战斗；不再附带其它职介/日常占位项。

## 2026-11-09 — 公开上手主路径

- 新增 `start.bat`：无 `.venv` 时自动创建并安装依赖，再启动 `launcher.py`；`start.bat` 兼容转发
- 新增 `setup_connect.py`：找 adb / 列设备 / 截图，不依赖 OpenCV
- 新增 `ONBOARDING.md`；`ADAPT_GUIDE` 〇节改为完整 6 步 AI 上手剧本；去掉重复粘贴段落
- `README` 主路径改为：AI 连通 → 双击 start.bat

# Changelog（公开示例版）

## 2026-11-09 — 公开可用首版整理

- 独立公开目录：空引擎 + 规范 + `default` 战斗壳 + 通用 UI 模板
- `config.py`：仅 `default` ready；苹果 count 默认 0；ADB 路径占位/自动查找，不含本机绝对路径
- 文档：README 改为 ADB 通用 + default 上手；ADAPT_GUIDE 补克隆提示词、坏提示词反例、日志词典；第八节改为公开示例向
- 测试：缺私有助战名条夹具时跳过截图类用例；ConfigTests 对齐 default-only profile
- 增加 `LICENSE`（MIT）、`.gitignore`、`config.example.py`

私有实战配队（daily/saber/assassin）、从者专属模板与本机苹果预算不在本公开树中。
