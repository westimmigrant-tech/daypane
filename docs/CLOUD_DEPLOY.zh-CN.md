# 免费云端看板：外部定时触发 + Actions + Pages

[English](CLOUD_DEPLOY.md) | **简体中文** · [返回 README](../README.zh-CN.md)

## 结构

- `main`：源码、布局、配置模板、测试。
- `runtime-data`：独立的额度输入分支，根目录 `quotas.json`；不触发源码 CI。
- Pages 部署产物：`screen.png`、`status.json`、状态网页，不写进任何源码提交。
- 原始 Dashboard JSON 仅在运行任务中使用，不公开发布。

已有 `gh-pages` 留作迁移回退；新流程不再向它提交。

## 外部触发配置

在 cron-job.org 创建任务：

- 名称：DayPane half-hour render
- URL：`https://api.github.com/repos/shenliucn-prog/daypane/actions/workflows/render.yml/dispatches`
- 方法：POST
- 请求正文：`{"ref":"main"}`
- 每小时第 25、55 分钟运行。当前任务时区为 America/Los_Angeles，这两个分钟位置与 UTC 一致。留几分钟给生成和发布，让 Kindle 在整点/半点取图。
- 请求头 `Accept: application/vnd.github+json`
- 请求头 `Content-Type: application/json`
- 请求头 `Authorization: Bearer <专用令牌>`

专用 GitHub fine-grained token 应只选 `daypane` 仓库，授予 Actions: write 和必需的 Metadata 读取权限，用于触发工作流。设置有效期并在到期前更新。不要把现有全账户令牌交给外部定时平台。凭证只填入定时器的认证请求头，不放 URL、不写进 Git。

API 成功接受触发与图片发布成功是两件事。定时器请求很快返回，不等待生成；生成和部署结果在 GitHub Actions 检查。

GitHub 自带第 17、47 分钟定时仅为备份；`cancel-in-progress: true` 是**必须**，不是风格选择：卡在 Pages 部署队列的运行（2026-10-05 实测停摆 5 天）会被下一次触发取消，从而不阻塞后续所有渲染。若为 false，卡住的运行会占住队头，之后每 30 分钟的新 run 只能 pending 再被逐个 cancel —— 表现为"全线 cancelled 但没有任何失败"，流水线静默停摆。外部定时也不能保证 GitHub 运行器绝对准点。

## Pages

Settings → Pages → Source 设为 GitHub Actions。工作流使用 upload-pages-artifact / deploy-pages；图源地址不变：

`https://shenliucn-prog.github.io/daypane/screen.png`

状态页：`https://shenliucn-prog.github.io/daypane/`

机器状态：`https://shenliucn-prog.github.io/daypane/status.json`

每次运行先读取上一张图片和校验和。公开数据全部失败、字体不可用或渲染失败时保留上一张好图，并发布失败状态。若安装依赖等更早步骤失败，则不产生新部署，已有 Pages 保持不动。状态网页按真实生成时间计算年龄，超过 45 分钟显示过期。

`status.json` 保留最近 256 次生成结果、生成时间、任务 ID、触发类型。精确触发/排队/部署时间另由 GitHub run / job API 记录，不能把 API 接受触发当作发布完成。

## 中英文输出

每轮采集一次数据，分别生成两种语言图片。现有 `screen.png` 与根目录预览保持中文；`screen-en.png` 为英文图，英文预览和独立状态记录位于 `en/`。生成失败时按语言保留旧图；首次生成失败且没有旧图时不发布。

仓库默认展示英文，设备语言通过插件 **Language / 语言** 单独选择。英文新闻使用 HN，中文新闻源保持原样；自定义名称不会自动翻译。

## 本机额度上报

### Mac 上的 Codex 账户额度

在本仓库根目录运行 `python3 tools/report_codex_quota.py`，用当前已登录的 Codex CLI 查看五小时和每周额度。若 Codex 来自 Mac 应用且不在 PATH，可用 `--codex` 指定可执行文件的绝对路径。

加入 `--publish` 后，采集器把采集时间、两档已用百分比与重置时间写到 `westimmigrant-tech/daypane` 的 `runtime-data` 分支根目录 `quotas.json`。上传使用仅授权这个仓库、拥有 Contents 写权限的细粒度 GitHub 令牌，从 Mac 本机 `~/.config/daypane/github-token` 读取（权限 `0600`）。采集器通过 Codex app-server 的标准输入输出读取额度，发布内容按字段白名单生成。

可用 macOS LaunchAgent 每五分钟运行一次，给 Python、脚本与 Codex 都传绝对路径。采集失败时保留最后成功的快照，看板在额度过期后显示「待更新」。请只用一个上报器写这个文件。旧版 WorkBuddy 上报器会写入自己的快照，两个上报器交替运行会互相覆盖。

### 原有 WorkBuddy 上报器

上报已并入看板服务，不再是第二个后台进程：服务每次出图会顺带推一次带节流的快照，所以上报只发生在看板（也就是 WorkBuddy）真正被使用的时候。若之前在启动目录注册过 `DayPaneQuotaReporter.vbs`，删掉即可——服务自己的开机自启项已经够了。手动补推仍可用 `report_quota.bat`。

更新上报器，并将现有私有配置中的 `GITHUB_BRANCH=main` 改为 `GITHUB_BRANCH=runtime-data`。默认远端路径改为 `quotas.json`。敏感配置每次上报时重新读取 `tools/report_secrets.bat`，改完不需要重启服务。

本地文件放在 `SHAWN_DATA_DIR`，默认 `~/.local/share/shawn-kanban/`。成功上传记录与本地采集分开，失败下轮重试并按 10→20→30 分钟退避。后台运行绝不触碰 git 凭据，令牌失效只会写一条日志而不会弹窗；只有手动 `report_quota.bat --force` 才允许回退到 `git push`。

WorkBuddy 区块的数据来自最后一次上传的快照。三行分别是 上下文（已用/上下文窗口——WorkBuddy 自动压缩时会回落、换会话会归零，是水位不是余额）、已消耗（`credit_json` 累计，单调不减）与 本周活动，都不是账户真实额度。不要把敏感任务文本加入运行输入；runtime-data 与现有仓库同样公开。

## 48 小时验收

从外部定时器启用后开始计时：检查每次外部请求是否被接受、Actions 是否执行、Pages 是否发布，以及图片生成时间是否超过 45 分钟。源码中的备份 cron 不算外部定时器验收完成。Kindle 休眠/唤醒仍需单独实机验证。
