# Relay Desk

在本机管理 OpenAI / Claude 中转，手动选择、批量检测，并在同一张表中查看每个中转最近一次的结果。

Relay Desk 是基于 [meow LLM Detector](https://github.com/chen-006/meow-llm-detector) 本地接口开发的**非官方控制台**。检测算法、基准和判定能力来自 chen-006 及上游贡献者；本项目提供配置管理、批次调度和结果界面。感谢上游作者。

> 本项目采用 **PolyForm Noncommercial 1.0.0**，有非商业使用限制。上游及第三方组件保留各自版权与许可证。详见 [LICENSE](LICENSE) 和 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 能做什么

- OpenAI Responses / Claude Messages 两类检测，各自管理配置和历史。
- 在页面新增、修改、删除中转；保存过的 Key 不回显，编辑时留空保留原值。
- 手动勾选中转、单个执行或批量执行；打开程序不会自动检测。
- 1–4 个中转并行，默认 2 个；每个中转内部最多并发 4 个请求。
- 工作台按中转显示最近一次实际检测，单独重测不会清空其他行。
- 查看详情、历史批次、复制汇总、导出 CSV、停止批次及中断后恢复查询。
- 页面资源随源码附带，无 CDN、统计脚本；日常运行不需要 Node.js。

## 安装

需要 **Python 3.11+**。Windows 用户安装 Python 时启用 PATH 或 Python Launcher。
Windows、macOS、Linux 使用同一套控制台；官方检测器需要单独安装。

### 1. 下载控制台

```sh
git clone https://github.com/liuzekuan/relay-desk.git
cd relay-desk
```

也可以在 GitHub 点击 **Code → Download ZIP**，解压到自己的本机目录。

### 2. 安装官方检测器

从 [官方最新正式发行版](https://github.com/chen-006/meow-llm-detector/releases/latest) 下载：

| 系统 | 下载文件 | 首次启动 |
| --- | --- | --- |
| Windows x64 | `windows-x64-portable-zh-CN.zip` | 官方目录内的 `start.bat` |
| macOS / Linux / 其他 Windows | 名称以 `-zh-CN.zip` 结尾且不含 `windows-x64-portable` 的源码发行包 | Windows 运行 `start.bat`；macOS/Linux 运行 `sh start.sh` |

选择 Release 附件中的正式包，不使用 GitHub 自动生成的 Source code 压缩包，以保留官方安装校验及更新能力。
解压后的目录放在控制台根目录下，名称以 `meow-llm-detector` 开头，直接包含 `launch.py`。
源码发行包首次运行会准备本机依赖，需要联网，可能超过一分钟。
先确认官方页面在 `http://127.0.0.1:8765/` 能打开，再启动控制台。

也可以用附带的工具下载最新官方**源码发行包**并验证 GitHub Release 资产的 SHA-256：

```sh
python -B meow-local-tools/fetch_upstream.py --destination meow-llm-detector-source
```

macOS/Linux 的命令使用 `python3`。该工具只安装到新目录，不覆盖已有安装，不自动运行检测；之后仍按上表首次启动。
已有官方安装可以直接复用，只需保持其本机 8765 端口可用。

### 3. 打开控制台

- **Windows**：双击根目录 `start.cmd`。
- **macOS**：首次运行 `chmod +x start.command`，然后双击 `start.command`。也可运行 `sh start.command`。
- **Linux / 命令行**：`python3 -B meow-local-tools/dashboard.py`。

默认打开 `http://127.0.0.1:8766/`；端口被占用时自动选择后续端口，以实际打开的地址为准。
浏览器没打开时，可在终端运行 `python -B meow-local-tools/dashboard.py --no-browser` 查看地址。
官方服务未运行时，控制台会尝试从本地安装目录启动它。
安装在其他位置时，先启动官方服务，或设置环境变量 `MEOW_DETECTOR_DIR` 为包含 `launch.py` 的绝对路径。

## 第一次检测

1. 在顶部选择 **OpenAI 检测** 或 **Claude 检测**。
2. 打开 **中转配置**，新增名称、HTTPS API 基础地址和 Key。地址通常到 `/v1`，不要追加 `/responses` 或 `/messages`。
3. 回到工作台，选择 **验证模型**。页面顶部的请求模型会同步变化，实际请求也使用这个名称。
4. 勾选本次中转，选择档位和并行数，核对请求预算。
5. 点击 **开始检测**，或点击某一行的执行按钮。结果和最近检测日期会自动更新。

新检测的请求模型与验证模型始终一致，例如选择 `gpt-6-sol` 就请求 `gpt-6-sol`。批量、单个执行和重测均遵循此规则。
中转需要接受所选模型名称；目前不提供独立的请求别名覆盖。旧历史保留当时的实际请求模型，恢复查询不会更改或重发旧任务。
可选验证模型取决于官方本地基准，不保证所有 API 模型都有对应基准。
同一家中转提供两种接口时，在两个检测类型下分别配置。一个批次只测试一种类型，运行期间可切换查看另一类数据，但不能同时开启另一批次。

检测请求可能产生费用，由配置的 API 账户承担。打开工作台、修改配置和查看历史不会发起模型请求。
停止操作会取消排队项并请求官方服务停止已发起的任务，不能撤回已计费的请求。

## 配置文件

推荐在页面管理，也可手动编辑根目录文件后刷新页面：

| 文件 | 用途 |
| --- | --- |
| `codex-providers.toml` | OpenAI 配置，保留旧文件名以兼容已有数据 |
| `claude-providers.toml` | Claude 配置，首次保存时创建 |

每个文件使用同一结构，重复 `[[providers]]` 即可添加多个中转。以下均为虚构占位内容：

```toml
# 可选：首次打开页面时优先选择的验证模型；不覆盖本次下拉框选择
model = "gpt-6-astra"

[[providers]]
name = "Example A"
api = "https://a.example.invalid/v1"
key = "replace-with-your-key"

[[providers]]
name = "Example B"
api = "https://b.example.invalid/v1"
key = "replace-with-another-key"
```

同一类型内名称不能重复。名称用于关联历史，改名会被视为新中转。
页面保存会重新整理 TOML 格式；检测期间不能修改配置。多个窗口同时修改时，会拒绝旧表单覆盖新内容。

## 结果与恢复

工作台显示每个中转最近一次实际发起的检测。未开始就取消的排队项不会覆盖旧结果；实际发起后失败会显示本次失败。
详情、复制和导出使用对应行的批次数据。旧批次保存在本机 `batch-results`，可以在历史页查看。

匹配度是行为指纹证据，**不是智商、身份认证或真实模型概率**，也不能单独证明服务商故意替换模型。
同时查看有效样本和失败数；接口失败较多时应先排查连接。
同条件变化只比较同一基准、请求模型、验证模型及档位，不应直接比较不同基准版本的百分比。

关闭网页不会停止后台任务。重新打开可以继续查看。后台中断后，**恢复查询**只查询已发起的任务，不会重新执行排队项。
如果任务启动结果未知且缺少任务编号，先在官方历史中核对，再使用 **核对未知任务** 解除阻塞，避免重复计费。

## 跟随官方更新

控制台与检测器独立维护，本仓库不复制或冻结上游检测算法和基准。

1. 点击左下角 **官方检测器 / 更新**，进入本机官方页面。
2. 官方正式安装在启动时及持续运行每 24 小时检查程序和维护者基准；发现更新后，使用其内置更新入口安装。
3. 等官方服务恢复后回到控制台刷新，重新加载连接和当前基准。已有批次保留发起时的基准版本。

不要修改官方程序目录内的源码，否则可能无法通过官方更新完整性校验。
控制台源码更新使用 `git pull --ff-only`，或将新版公开源码解压到新目录后迁移自己的私有配置和历史。
仅更新控制台不会更新官方检测器；反之亦然。

```sh
# 只查询最新官方正式版，不下载、不修改本地安装
python -B meow-local-tools/fetch_upstream.py
```

GitHub Actions 的 **Latest Upstream Compatibility** 每天及 main 分支推送后，下载最新正式源码包、校验摘要，并用模拟传输测试真实本地接口。
没有真实中转配置，不产生模型请求。检查结果见仓库 **Actions**；失败表示网络、上游发布或接口兼容性需要排查，不会自动覆盖用户安装。
此机制帮助及时发现变化，不承诺未知未来版本永远兼容。当前已验证的上游版本为 **4.5.4**。
GitHub 可能暂停长期无活动仓库的定时任务，可在 Actions 手动恢复或运行。

## 隐私与发布

配置里的 Key **明文保存在本机**。运行时经本机官方检测器发送给你配置的 API 地址，不发送给在线检测网站。
前端不回显已保存 Key，但有本机文件访问权限的人可以读取配置。仅在可信电脑和账户中使用。

以下内容均不应上传：配置及备份、检测历史、日志、CSV、真实截图、官方运行目录和数据库。
报告中的中转名称和地址同样属于私有信息。`.gitignore` 已排除这些目录及常见文件类型；发布审计额外限定可提交的文件。
更多说明及本地提交钩子见 [SECURITY.md](SECURITY.md)。不要通过 `git add -f` 绕过检查。

## 开发与离线验证

```sh
# 演示与真实配置隔离；假 Key、模拟结果，不访问中转
python -B meow-local-tools/dashboard.py --demo --port 8876

# Python 标准库测试
python -B -m unittest discover -s meow-local-tools -p 'test_*.py' -v

# JavaScript 测试；仅开发时需要 Node.js
node --test meow-local-tools/test_results.cjs
node --check meow-local-tools/web/app.js

# 启用提交及推送前的本地隐私检查
git config core.hooksPath .githooks
python -B meow-local-tools/audit_publication.py --history
```

`meow-local-tools/verify_detector.py` 对官方接口做离线集成测试，需要官方依赖及其源码在 Python 导入路径中；CI 展示了具体运行方式。
更新图标资源时，在 `meow-local-tools` 运行 `npm ci --ignore-scripts` 和 `npm run vendor`，并保留 Lucide 许可证。
Windows 已做本机启动验证；macOS/Linux 启动器仍需在实际桌面环境验证，CI 覆盖不等于桌面双击测试。

## 目录

```text
start.cmd / start.command   Windows / macOS 启动入口
meow-local-tools/          控制台、静态网页、离线测试与维护工具
.github/workflows/        跨平台测试与最新上游兼容性检查
.githooks/                可启用的提交和推送前发布检查
codex-providers.toml       私有 OpenAI 配置，不进入 Git
claude-providers.toml      私有 Claude 配置，不进入 Git
batch-results/            私有历史，不进入 Git
meow-local-logs/           私有日志，不进入 Git
meow-llm-detector-*/       独立官方安装及数据，不进入 Git
```

本项目不代表 OpenAI、Anthropic 或 meow LLM Detector 官方。
