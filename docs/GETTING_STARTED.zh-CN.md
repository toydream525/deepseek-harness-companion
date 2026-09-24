# DSH 伴航：入门与完整配置

[返回首页](../README.md) · [English](GETTING_STARTED.en.md) · [交给 AI 配置](AI_SETUP.zh-CN.md)

这份说明从空配置开始。DSH 伴航管理本地 GGUF 模型和 DeepSeek Harness（DSH）连接；发布包自带桌面程序，不包含模型权重、llama.cpp、Node.js、DSH 或显卡运行环境。已有文件可以直接选择或复制到独立目录，无需再下载。

## 1. 安装桌面程序

从 [v0.1.0 Releases](https://github.com/toydream525/deepseek-harness-companion/releases/tag/v0.1.0)下载 Windows x64 Setup，按提示安装；或者完整解压便携 ZIP 后运行 `DSH-Companion.exe`。不要在压缩包内直接运行。首次说明可选择不再显示，之后能在「设置与诊断」重开。默认不开机自启，也不会自动加载模型。

## 2. 准备运行引擎与 GGUF

从 [llama.cpp 官方发布页](https://github.com/ggml-org/llama.cpp/releases)下载完整 Windows 引擎包。先按实际显卡、驱动选择；不确定时可以先用 CPU 版。解压后在「设置与诊断 → 运行环境」选择 `llama-server.exe` 并检测。CUDA、Vulkan、ROCm 是否可用须以设备探测和一次真实推理为准；不是每台机器都需安装完整 CUDA Toolkit。

模型可从 Hugging Face 自行下载完整 GGUF，或在「模型库与下载」添加已存在文件。分片模型需要整组文件齐全；投影器文件不能作为独立语言模型启动。下载入库不会自动启动。模型与模板可以放在任意可访问目录，路径含空格也可使用。

在「工作台」选模型与参数预设，再启动模型。首页显示本地 API 就绪后，到「试聊」让模型实际回答一句话；只看到文件入库或进程存在都不算推理验证通过。接口地址在「设置与诊断 → 接口与网络」可选中并复制。

## Qwen 参考方案

原始配方来自 Windows 11、i7-13700K、RTX 4080 16 GB 和 64 GB RAM。这是一套**面向 NVIDIA 16GB 显存与 64GB 系统内存的推荐参考方案**，并非所有 16GB 显存设备都能原样运行；也不限制软件支持其他 GGUF。其他硬件请按实际情况调低参数。完整历史目标与高级实验顺序见 [内置从零配置指南](../resources/guides/从零配置指南.md)和[进阶实验路线](../resources/guides/进阶实验路线.md)。

| 方案 | 手动下载的准确文件 | Hugging Face 页面 | 状态 |
|---|---|---|---|
| Huihui 思考 32K | `Huihui-Qwen3.8-27B-abliterated-GSQ-RCO-IQ3_S.gguf` | [Huihui 仓库](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) | 原目标机已测 |
| Huihui Direct 32K | 同一个 Huihui IQ3_S 文件 | [Huihui 仓库](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) | 原目标机已测 |
| 原版思考 32K | `Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf` | [ISTA 仓库](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF/tree/main) | 原目标机已测 |
| 写作 8K | `Huihui-Qwen3.8-27B-abliterated-UD-IQ4_XS.gguf` | [Huihui 仓库](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) | 可选，推理尚未验收 |

稳定配方还需要 [froggeric 固定提交 855bffc](https://huggingface.co/froggeric/Qwen-Fixed-Chat-Templates/tree/855bffc) 的 `chat_template.jinja`。准备模型与模板后，先入库，再在「工作台」或「参数预设」点「一键导入原提示词配置」，按预览匹配本机路径；缺少的写作文件可保持待绑定。导入不会启动或下载模型，也不会覆盖现有自定义预设。其他模型可先用 GGUF 内嵌模板走通用配置。

发布包另有四个 [模式脚本](../scripts/deploy/)。双击所需 `.cmd`，按提示选择伴航 EXE、`llama-server.exe` 和你已手动下载的 GGUF；脚本只导入对应预设，不启动模型，也不下载模型权重。`-TemplatePath` 可复用已有模板。若未指定模板，**正式导入**会从上述固定提交下载约 28 KB 模板并校验；`-DryRun` 不下载，需提供已有模板。命令行只读预检示例：

```powershell
powershell.exe -NoProfile -File .\scripts\deploy\Deploy.ps1 -Mode Uncensored32K -DryRun
```

脚本使用已安装 EXE，不需额外 Python。它会拒绝无法确认满足原目标机条件的电脑；其他硬件可在应用里手工建通用预设。若 PowerShell 策略阻止脚本，先核对来源与内容；可以在**当前窗口**自行设置 `Set-ExecutionPolicy -Scope Process RemoteSigned`，或者直接使用应用内导入，不修改全局策略。

## 3. 接入 DeepSeek Harness

只有想用 DSH 时才需要 [Node.js 官方下载](https://nodejs.org/en/download) 和 [DeepSeek Harness 官方项目](https://github.com/deepseek-ai/deepseek-harness)。按上游说明安装完整 DSH 本地目录；一次性的 `npx` 临时运行不能代替可绑定的本地目录。也可以在应用「组件更新」主动准备经过审核的固定版本。

在「设置与诊断 → DSH 组件」选择 DSH 目录与 `node.exe`，直到两者通过检测。到「DSH 与提供方」启动本应用管理的 DSH，或粘贴你**现有外部 DSH 的原认证链接**连接；外部未认证实例不能自动取得令牌。启动本地模型后，首页「一键启动」会逐步检查模型、DSH、注册本地提供方并设为新会话默认；也可在 DSH 页逐项完成。最终在 DSH 中创建新会话并实际对话。模型单独试聊成功，并不代表 DSH 接入成功。

本地 API、DSH 页面和已认证链接在对应页面可复制。认证链接包含令牌，只在可信环境使用；诊断导出不会包含它。关闭程序只收尾本应用启动的 DSH，外部实例保留。

## 日常使用与迁移

- 默认最小化留在任务栏。设置里可单独开启「最小化到托盘」；首次按 X 可选退出并停止本应用服务、收起到托盘继续运行或取消，也可记住并在设置里修改。开机启动、启动时加载模型、启动时收托盘是互相独立的选项。
- 参数预设与原提示词配套配置可以一键导入/导出；导入前查看差异，新电脑上的模型和模板路径可重新绑定。配置包不含权重、密钥、聊天或日志。
- 网络代理可选直连、系统、HTTP/HTTPS 或 SOCKS5。Hugging Face 请求按选择走；本应用启动的 DSH 只支持 HTTP/HTTPS 代理，外部 DSH 由其自身环境负责；本地管理接口直连。
- 「组件更新」可主动准备清单内的 llama.cpp 与 DSH 固定版本，在新目录验证后切换并保留回滚；它不声称追踪最新上游。Node.js 与显卡驱动仅给官方入口和更新后复检。
- 「试聊」的临时系统提示词与对话只在当前进程内存；导出诊断仅含状态摘要。进阶 64K、KV Streaming、MTP、视觉等须按[进阶实验路线](../resources/guides/进阶实验路线.md)逐项验证，默认不启用。

## 常见问题

| 现象 | 先检查 |
|---|---|
| 检测不到引擎 | 是否选中了完整解压包内真正的 `llama-server.exe`；实际 `--help`/设备探测是否通过 |
| 模型不能启动 | GGUF 是否完整，分片是否齐全，所选预设的内存/显存需求是否超出本机 |
| API 已就绪但 DSH 不能用 | Node/DSH 组件、认证连接、本地提供方及新会话默认是否分别通过 |
| 外部 DSH 提示认证 | 用该实例原启动链接连接；软件不会读取外部日志来寻找令牌 |
| 脚本未导入 | 核对引擎、模型、模板真实路径；执行策略受限时用软件内导入 |
| 下载或组件更新失败 | 检查代理模式和实际网络；组件更新失败后沿用旧版本或回滚 |

问题排查需要实测输出时，请保留最早出现的错误，同时隐藏密钥和认证链接。许可见 [MIT](../LICENSE) 与 [第三方声明](../THIRD-PARTY-NOTICES.md)。
