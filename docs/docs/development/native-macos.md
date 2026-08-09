---
id: native-macos
title: 原生 macOS 版本开发计划
---

## 当前状态

本文档定义 Frigate Apple Silicon 原生 macOS 发行版的实施计划。当前源码基线为
`dev` 分支的 `e73a14db` 提交，对应 Frigate 0.18 beta 开发版本。

原生发行版不依赖 Docker、OrbStack、Linux 虚拟机或 Home Assistant OS。项目继续以
Frigate 的 Python 核心和 Web 应用为基础。macOS 专用代码应尽可能放在独立的运行时
层中，以便持续合并上游更新。

## 项目目标

- 发布经过签名和 Apple 公证的 Apple Silicon `.app` 应用。
- 以原生进程运行 Frigate、nginx、go2rtc、FFmpeg 和目标检测服务。
- 使用 VideoToolbox 完成受支持的视频硬件解码和编码。
- 通过公开的 ONNX Runtime CoreML Execution Provider 使用 Apple Neural Engine。
- 保持 Frigate 配置、API、MQTT、数据库和媒体文件格式兼容。
- 提供 Swift 菜单栏应用，负责首次设置、生命周期、健康状态、日志和更新。
- 支持优雅退出、崩溃恢复、登录启动、睡眠唤醒和网络重连。
- 除非用户明确配置外部集成，否则摄像头流量和媒体数据只保留在本机。

## 首个版本不包含的目标

- 支持 Intel Mac。
- 支持 TensorRT、RKNN、Hailo、Synaptics、OpenVINO、EdgeTPU 等 Linux 专用
  detector。
- 使用原生界面替换 Frigate Web UI。
- 增加商业授权或强制遥测服务。
- 在基础原生运行时稳定之前实现录像自适应码率播放。

## 设计原则

1. 默认保持上游 Frigate 行为不变。平台覆盖必须显式启用并具有测试。
2. 进程管理、目录选择、Keychain、登录启动、更新安装和 macOS 权限处理放在 Swift
   应用中。
3. 平台无关的修改应尽可能小，并在具有通用价值时考虑贡献给上游。
4. 打包所有运行时依赖。安装后的应用首次启动时不能下载 Python 包，也不能要求用户
   安装 Homebrew。
5. 内部服务只绑定 loopback 或本地 IPC。经过认证的 HTTPS 端口默认监听局域网，不能
   通过该入口绕过 Frigate 权限检查。
6. 对兼容的录像流优先保持直接复制。硬件编码主要用于预览、Birdseye、导出、不兼容
   视频源以及后续的自适应码率变体。

## 目标架构

Swift 应用是最上层的 supervisor，负责以下进程树的完整生命周期：

1. go2rtc，根据 Frigate YAML 生成配置，管理端口只绑定本机。
2. Frigate Python 核心，使用应用内置的 Python 和原生 wheel。Frigate 继续通过标准
   `DetectorRunner` 子进程和共享内存管理 Apple Silicon detector。
3. nginx，提供经过认证的 Web UI 和媒体路由。
4. Frigate 按需启动的 detector、FFmpeg 和 FFprobe 工作进程。

Apple Silicon detector 不另行设计私有 TCP 或 ZMQ 服务协议。它沿用 Frigate 的标准
detector 插件边界，因此模型输入输出、进程隔离和崩溃恢复行为与上游实现保持一致。

所有进程按照依赖顺序启动，并进行健康检查。子进程异常退出后使用有上限的指数退避
策略重启。连续失败达到阈值后应停止受影响的服务，并在菜单栏中显示诊断信息，不能
形成无限重启循环。

退出时首先向整个进程组发送 `SIGTERM`，等待录像状态和数据库写入完成。只有超过明确
的超时时间后才能强制结束进程。

## App Bundle 结构

```text
Frigate.app/
  Contents/
    MacOS/
      Frigate
    Frameworks/
      Python.framework/
    Resources/
      bin/
        ffmpeg
        ffprobe
        go2rtc
        nginx
      frigate/
      python/site-packages/
      web/
      models/
      nginx/
```

所有内嵌可执行文件和原生扩展必须先完成签名，之后才能签名和公证最外层 App Bundle。

## 运行时目录

未提供覆盖参数时继续使用现有 Docker 路径。原生启动器通过以下环境变量提供绝对路径：

| 环境变量 | Docker 默认值 | 原生 macOS 默认值 |
| --- | --- | --- |
| `FRIGATE_INSTALL_DIR` | `/opt/frigate` | App Bundle 的 Resources 目录 |
| `FRIGATE_CONFIG_DIR` | `/config` | `~/Library/Application Support/Frigate/config` |
| `FRIGATE_MEDIA_DIR` | `/media/frigate` | 用户选择的媒体目录 |
| `FRIGATE_CACHE_DIR` | `/tmp/cache` | `~/Library/Caches/Frigate/cache` |
| `FRIGATE_LOG_DIR` | `/dev/shm/logs` | `~/Library/Logs/Frigate` |
| `FRIGATE_RUNTIME_DIR` | `/tmp/cache` | `~/Library/Caches/Frigate/runtime` |

运行时目录用于保存 IPC socket 和其他临时协调文件。该目录必须位于本地磁盘、仅当前
用户可访问，并在启动前清理失效的 IPC 文件。媒体目录可以位于本地磁盘、移动磁盘或
网络存储。

## 工作流与开发阶段

### 阶段 0：基线与兼容性测试框架

- 记录上游提交以及支持的 macOS、Xcode 和 Python 版本。
- 增加不依赖 Docker 的原生平台测试。
- 准备一个使用本地媒体文件且不包含真实摄像头凭据的测试夹具。
- 建立上游同步策略和补丁清单。

验收标准：

- 现有 Linux 默认行为保持不变。
- 原生专用测试能够在 Apple Silicon CI 或有明确说明的本地环境中运行。
- 每项平台补丁都有负责人和验收测试。

### 阶段 1：运行时路径抽象

- 使用统一且经过验证的运行时路径对象替换固定的安装、配置、媒体、缓存、日志和运行
  时目录。
- 所有内部 ZMQ IPC 地址通过运行时目录生成。
- 使用自定义配置目录时，错误信息不能继续固定显示 `/config`。
- 启动阶段以严格权限创建所需目录。

验收标准：

- 使用旧默认路径时，Frigate 导入和路径单元测试通过。
- 测试进程可以使用临时配置、媒体、缓存、日志和运行时目录，不接触 Docker 路径。
- 后端代码中不再存在硬编码的 `ipc:///tmp/cache`。

### 阶段 2：原生命令行启动器

- 将必要的 s6 prepare 逻辑转换为 Python bootstrap 模块。
- 使用解析后的运行时路径生成 go2rtc 和 nginx 配置。
- 构建或准备原生开发版本的 Python、FFmpeg、go2rtc 和 nginx。
- 使用一个本地测试视频，在终端中启动最小 Frigate 实例。
- 使用平台适配器替换 `/proc`、cgroup、`vainfo` 和 `/dev/shm` 假设；无法支持的
  指标必须显式返回不支持，不能静默失败。

验收标准：

- Frigate 不依赖 Docker 即可启动，并通过认证 Web UI 提供服务。
- 本地 H.264 测试视频可以完成检测、录像、Review 和回放。
- 优雅退出后所有子进程都已结束。

#### 阶段 2 验证记录（2026-08-08）

阶段 2 已在本机 Apple Silicon 环境完成原生验收，全程没有启动生产 Frigate 容器。
生产容器仅以停止状态读取元数据和备份配置，用于兼容性验证。当前容器状态仍为
`exited`，媒体卷 `frigate_frigate_media` 保留。

生产容器内的 `/usr/bin/python3` 最终指向 CPython 3.11.2。原生开发环境使用 Python
3.13 是为了遵守当前上游仓库的开发基线，并不是 CoreML 或 Apple Neural Engine 的
要求。运行时兼容目标明确为 Python 3.11 和 3.13，后续 App Bundle 可以根据原生 wheel
覆盖情况选择其中一个固定版本。

已完成的原生组件和验证如下：

- 原生 bootstrap 生成经过路径适配的 Frigate、go2rtc 和 nginx 私有配置，并由同一个
  supervisor 按依赖顺序启动、检查就绪和反向停止。
- go2rtc 1.9.14、FFmpeg 8.1.1 和 Python Frigate 后端均为 ARM64 原生进程。nginx
  1.27.4 使用 Clang 构建，并静态包含 OpenSSL 3.3.2、nginx-vod-module 1.31、
  secure-token、set-misc 和 NDK 模块。
- nginx 和 go2rtc 的管理接口只绑定 loopback。Web UI、API、WebSocket、媒体和 VOD
  路由继续使用 Frigate 认证。
- 本地 320×180 H.264 运动夹具配合测试专用 ZMQ detector 完成真实检测、目标跟踪、
  录像落盘、Review 和事件入库。验收运行产生 25 条录像记录、1 个 Review 和 1 个
  事件，SQLite `quick_check` 通过。
- 经过认证的 HLS `master.m3u8`、`index.m3u8` 和实际媒体分片均返回 HTTP 200，媒体
  分片包含非零字节。
- 多轮运行均能优雅退出，go2rtc、Frigate、nginx、detector 和所有子进程全部结束，
  1984、5001、5002、8554、8555、8971 端口没有残留监听。

macOS 适配修复包括 Darwin 上禁用不安全的 `setproctitle` 原生扩展调用、Python 3.13
共享内存 `track=False`、16 KiB 页面对齐大小、逻辑帧切片以及关闭前显式释放
`memoryview`。Linux 专用的 `/proc`、cgroup、`vainfo`、nethogs 和 NVIDIA 指标通过
平台适配器显式报告支持状态。

停止容器备份的 Frigate 0.17 配置副本已通过官方迁移器升级到 0.18，并在原生运行时
完整校验 3 个摄像头、ZMQ detector 和路径映射。迁移只发生在临时副本中；备份配置的
SHA-256 仍为
`28dd74d744bacf938880d775ccc86261a5646cf4aa902fb11786758c504ff86c`，备份归档校验通过。

### 阶段 3：Apple Silicon 目标检测

- 实现标准 Frigate detector 插件，使用固定版本的 ARM64 ONNX Runtime 和公开的
  CoreML Execution Provider，不依赖 Fregata 的专有运行时。
- detector 继续由 Frigate 的 `DetectorRunner` 启动，并使用现有共享内存交换张量；不
  增加局域网端口，也不增加第二套进程间协议。
- 为静态输入的 YOLO 模型显式选择 `NeuralNetwork` CoreML 模型格式。`ane` 后端映射
  到 `CPUAndNeuralEngine`，`gpu` 后端映射到 `ALL`，并保留可诊断的 CPU 兜底策略。
- 启动时执行预热和小型自检，记录 CoreML provider 是否加载、模型格式、计算单元、
  节点分配摘要、首帧延迟和稳定推理延迟。
- 在 Frigate 统计信息中报告当前模型、推理延迟、CoreML provider 状态和意外 CPU
  回退。provider 出现在列表中不能单独作为硬件加速成功的证据。
- 增加模型转换、编译缓存失效、缓存恢复、provider 初始化失败和 detector 重启测试。

验收标准：

- 当前支持的 Apple Silicon Mac 上，目标检测通过 CoreML 执行，并由 Compute Plan 或
  Instruments 证明主要计算位于预期的 ANE 或 GPU，而不是只检查 provider 名称。
- 应用能够发现并报告超出已知允许节点清单的 CPU 回退。
- 相同模型、输入张量和阈值下，CoreML 路径的检测框、类别和置信度与 CPU 基准处于
  预先定义的容差内。
- 强制结束 detector 后能够由 Frigate 在限制范围内自动恢复，不需要 Swift 应用重启
  整个服务。

#### 阶段 3 调研基线（2026-08-08）

本阶段参考了 [Fregata 文档](https://docs.fregata.app/) 和本机提供的
`Fregata-0.17.2.4.dmg`。DMG 的 SHA-256 为
`0f31cff0fea21727a70a5f1fb548222c303c14296d5e823012e351bd38c1ef35`。只进行了只读
挂载、静态检查和隔离的 ONNX Runtime 模型探测，没有启动 Fregata 服务或访问用户
配置。

可复用的架构结论：

- Fregata 是 ARM64 菜单栏应用，内置 Python 3.11、Frigate 源码、FFmpeg、go2rtc、
  nginx、默认 ONNX 模型和 ONNX Runtime 1.22.1。
- Fregata 的原生 nginx 为 1.27.4，使用 Clang ARM64 构建，并包含 nginx-vod-module
  1.31、secure-token 1.5、set-misc 0.33、NDK 0.3.3 和静态 OpenSSL 3.3.2。本项目只
  参考其公开可观察的构建参数，使用固定校验和的公开源码独立构建，不复制 DMG 内的
  已签名二进制。
- 它的 CoreML detector 使用 Frigate 标准 `DetectorRunner` 子进程和共享内存，而不是
  supervisor 预先启动的独立 ZMQ detector 服务。这一发现修正了本计划早期的进程
  架构假设。
- 配置表面提供 `type: coreml` 和 `inference_backend: ane | gpu`。这些字段可以作为
  配置兼容性的参考，但本项目将以独立实现和明确的公开依赖为准。
- Fregata 的 runtime、CoreML detector、状态扩展和 supervisor 包含专有编译模块。
  本项目不复制、反编译复刻或重新分发这些实现，只使用公开文档描述的外部行为作为
  兼容性参考。

在当前 Apple Silicon M4 机器上，用 DMG 内置的公开 ONNX Runtime 接口和默认模型进行
隔离探测，得到以下阶段性结果：

| CoreML 模型格式 | 计算单元 | 节点分配摘要 | 稳定推理延迟 |
| --- | --- | --- | --- |
| `MLProgram` | `CPUAndNeuralEngine` | 653 个算子位于 CPU | 约 10 至 13 ms |
| `MLProgram` | `ALL` | 653 个算子位于 GPU | 约 13 至 25 ms |
| `NeuralNetwork` | `CPUAndNeuralEngine` | CoreML 支持 650/653 个节点 | 约 2.1 至 2.9 ms |

因此，阶段 3 的首个实现以 `NeuralNetwork + CPUAndNeuralEngine` 为 ANE 路径的候选
基线。该结果只证明当前机器、当前模型和当前 ORT 构建的行为，最终验收仍必须使用
[ONNX Runtime CoreML EP 配置](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html)
中的 Compute Plan 能力或 Apple Instruments 验证实际执行位置。

#### 阶段 3 验证记录（2026-08-09）

阶段 3 已在本机 Apple Silicon M4 环境完成。实现使用固定版本的 ARM64 ONNX Runtime
1.22.1 及其公开 CoreML Execution Provider，没有复制 Fregata 的 detector、runtime
或状态模块。生产 Frigate 容器在验证前后均保持 `exited`，测试只读使用现有 ONNX 模型，
没有读取摄像头地址、账号或媒体数据。

实现和验证结果如下：

- 新增标准 `type: coreml` detector 插件，继续由 Frigate `DetectorRunner` 子进程和
  共享内存驱动。它没有监听端口，也没有引入新的 ZMQ 或网络检测协议。
- `inference_backend: ane` 映射到 `NeuralNetwork + CPUAndNeuralEngine`，`gpu` 映射
  到 `NeuralNetwork + ALL`。静态输入形状、模型编译缓存和 Compute Plan 开关均通过
  ONNX Runtime 的公开 provider options 配置。
- 编译缓存目录包含模型内容 SHA-256、ONNX Runtime 版本、模型格式和 backend。缓存
  初始化失败时，旧缓存会被隔离并重新编译，不会覆盖原始模型。
- detector 启动时生成 ONNX Runtime profile。当前 YOLO 模型被分为 3 个 CoreML 分区，
  650/653 个图节点由 CoreML 支持；剩余 3 个 CPU 执行节点均为已知 `Concat` 操作。
  任何不在允许清单中的 CPU 操作会在 `/stats` 中设置
  `unexpected_cpu_fallback: true`。
- CoreML detector 默认关闭 ONNX Runtime 工作线程在两次推理之间的忙轮询，同时保留
  推理期间对 3 个 `Concat` 的并行执行。可通过 `allow_thread_spinning: true` 恢复
  ONNX Runtime 默认行为，运行状态中的 `thread_spinning` 会报告实际设置。
- 运行状态通过运行时目录中的私有原子 JSON 文档传回 Frigate 统计接口，包含子进程
  PID、模型哈希、provider、计算单元、分区、CPU 操作、预热和最近推理延迟。状态文件
  不包含模型路径或配置凭据，父进程会拒绝 PID 不匹配的失效状态。
- 使用相同模型和合成视频帧比较 CPU 与 CoreML 路径，检测输出在
  `rtol=1e-3, atol=1e-3` 容差内一致。本机验收的最大绝对误差为 0，稳定探针推理约
  2.4 至 3.2 ms。为避免无检测结果形成弱验收，探针还比较置信度超过 0.005 的原始
  候选：10 个候选的类别全部一致，置信度最大误差约 0.00218，候选框最大误差约
  0.318 像素。
- 最终回归中，Xcode Instruments 的 `Core ML` 模板记录到 5707 个
  `Neural Engine Prediction` 区间，直接证明推理在 Apple Neural Engine 上执行，
  而不是只依赖 provider 名称或加载日志。
- 2026-08-09 对生产模型的进程采样再次记录到 `ANEServices` 和 `_ANEClient` 调用，
  同时发现 ONNX Runtime 工作线程在检测间隔内持续忙轮询。使用相同模型、相同
  `CPUAndNeuralEngine` 和每秒 15 次推理进行 10 秒隔离基准，关闭忙轮询后进程 CPU
  时间占比从 30.5% 降至 2.9%；平均推理延迟从 2.431 ms 变为 2.803 ms，P95 为
  3.525 ms。该优化没有降低摄像头检测帧率，也没有改变 ANE provider 或模型精度。
- 原生 Frigate 集成运行中，CoreML detector 在收到 `SIGKILL` 后由 Frigate watchdog
  从 PID 77381 重建为 PID 99746，并重新进入 `ready` 状态。watchdog 对 60 秒内的
  重启次数设置上限，没有请求 Swift supervisor 重启整个 Frigate 服务。183.94 秒的
  原生 supervisor 验收最终 clean shutdown，所有服务和监听端口均已结束。

阶段 3 的自动化验收命令为：

```console
.venv-macos/bin/python -u -m unittest frigate.test.test_coreml_detector
macos/scripts/check-coreml-hardware.sh /absolute/path/to/static-yolo-model.onnx
```

### 阶段 4：VideoToolbox

- 构建支持 VideoToolbox、AVFoundation、CoreMedia、CoreVideo 和所需音频能力的
  ARM64 FFmpeg。
- 增加 `preset-videotoolbox` 硬件解码预设。
- 为 Birdseye、预览、导出和显式转码增加 VideoToolbox 编码预设。
- 对兼容录像流继续使用 stream copy。
- 增加运行时验证，确认硬件解码确实被成功选择。

验收标准：

- 支持的 H.264 和 HEVC 测试视频通过 VideoToolbox 解码。
- Birdseye 和预览生成使用预期的硬件编码器。
- 解码器回退必须在日志和状态中可见，不能静默发生。

#### 阶段 4 验证记录（2026-08-09）

阶段 4 已在本机 Apple Silicon M4 环境完成。生产 Frigate 容器在验证前后均保持
`exited (143)`，没有启动容器或读取摄像头媒体。实现和验收结果如下：

- ARM64 FFmpeg 8.1.1 已链接 VideoToolbox、AVFoundation、CoreMedia、CoreVideo 和
  AudioToolbox，并公开 H.264、HEVC 的 VideoToolbox 解码及编码能力。
- `preset-videotoolbox` 使用 `videotoolbox_vld` 硬件帧，并通过 `scale_vt` 在硬件帧上
  缩放。Darwin ARM64 的 `auto` 硬件加速会解析为该预设。
- Birdseye、预览和 timelapse 导出使用 `h264_videotoolbox`，并设置
  `-allow_sw 0`，硬件编码不可用时会明确失败。兼容录像流仍然使用 stream copy，
  不进行无意义的二次编码。
- 硬件探针分别通过 VideoToolbox 解码 10 帧 H.264 和 10 帧 HEVC。它还使用 Frigate
  生成的真实命令完成 Birdseye、预览和 timelapse 各 10 帧的 H.264 硬件编码，输出
  均通过像素格式、分辨率和非零帧数检查。
- 原生 Frigate 产品级集成中，摄像头输入、处理和检测均稳定在约 5.1 FPS。
  `/stats` 报告 `apple-videotoolbox` 已配置，摄像头硬件解码状态为 `active`。运行产生
  4 条录像、1 个 Review 和 1 个事件，SQLite `quick_check` 通过，supervisor 在
  58.65 秒后 clean shutdown，所有监听端口均已释放。
- 停止容器的 0.17 配置只在私有临时副本中迁移到 0.18 并完整校验。3 个已启用摄像头
  的默认硬件加速均解析为 `videotoolbox`，ZMQ detector 和 2 个容器路径映射保持兼容。
  原备份 SHA-256 仍为
  `28dd74d744bacf938880d775ccc86261a5646cf4aa902fb11786758c504ff86c`。

阶段 4 的自动化验收命令为：

```console
.venv-macos/bin/python -u -m unittest frigate.test.test_videotoolbox_presets
macos/scripts/check-videotoolbox-hardware.sh
```

### 阶段 5：Swift 应用和 Supervisor

- 在 `macos/` 目录新增 Xcode 工程。
- 实现菜单栏、首次启动流程、目录选择和 Local Network 权限处理。
- 使用 Keychain 保存密钥，使用 Application Support 保存非敏感设置。
- 实现子进程管理、日志、健康检查和崩溃诊断。
- 使用 `SMAppService` 实现登录启动。
- 摄像头配置和录像查看继续使用现有 Frigate Web UI。

验收标准：

- 在全新 Mac 上将 App 拖入 Applications 即可安装。
- 首次启动不需要终端、Homebrew、包管理器或外部运行时。
- 登录启动和正常退出不会损坏录像或数据库。

#### 阶段 5 验证记录（2026-08-09）

阶段 5 已完成 Swift 菜单栏应用、supervisor 和自包含开发版 App 的本机验收。实现没有
调用 Docker，也没有将容器作为应用运行后端。

- SwiftUI `MenuBarExtra` 提供启动、停止、打开 Web UI、首次配置、查看日志和退出入口。
  第一次启动选择现有配置文件和录像目录，并使用 security-scoped bookmark 保留目录
  访问权。
- Local Network 权限通过受控 Bonjour browser 触发。JWT secret 使用
  `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` 写入 Keychain，普通设置以 0600
  权限写入 Application Support，日志以 0600 权限轮转。
- `SMAppService.mainApp` 管理登录启动。菜单退出和系统退出都先向原生 Python
  supervisor 发送终止请求，等待其按反向依赖顺序结束服务，20 秒后才允许有界强制
  结束。
- Swift supervisor 通过 `http://127.0.0.1:5001/version` 检查内部后端就绪状态，
  “打开 Web UI”使用 `https://127.0.0.1:8971`。意外退出使用 1、2、4 秒退避，5 分钟
  内最多重启 3 次，超过上限后进入明确的故障状态。
- 对外 Web 入口默认使用 nginx 在 `0.0.0.0:8971` 提供 TLS，局域网设备可以通过
  `https://<Mac 的地址>:8971` 访问。Frigate API `5001`、MQTT WebSocket `5002`、
  JSMPEG `8082` 和 go2rtc 管理端口 `1984` 仍只监听回环地址，局域网只暴露经过认证的
  nginx 入口。
- 第一次启动会在配置目录的 `certs/fullchain.pem` 和 `certs/privkey.pem` 生成有效期
  365 天的自签名证书，SAN 包含 `localhost`、本机名称和当时可用的本机 IP。证书目录为
  0700，证书和私钥为 0600；以后启动复用同一证书，也允许用户预先放置匹配的自定义
  证书对。只存在其中一个文件或证书与私钥不匹配时会拒绝启动，避免静默覆盖用户材料。
- TLS 默认开启，并把认证 Cookie 默认设置为 Secure。开发版自签名证书不会被浏览器
  自动信任，首次访问会显示证书警告；可将 `fullchain.pem` 导入 macOS 钥匙串并由用户
  明确信任。上游 `tls.enabled: false` 仍可显式关闭 TLS，但不会恢复为仅回环监听。
- App 内嵌固定 SHA-256 的 ARM64 CPython 3.11.15、Frigate 源码与 Web UI、全部 Python
  依赖、FFmpeg 8.1.1、go2rtc 和 nginx。选择 3.11 系列是为了贴近生产容器的 CPython
  3.11.2，不是因为 CoreML 要求某个 Python 版本。
- 构建器将非系统 Mach-O 依赖复制到 App，并使用 `@rpath` 或 `@loader_path` 重写。
  最终 1.4 GiB 开发 App 中检查了 489 个 Mach-O 文件，没有 Homebrew、用户目录、临时
  构建目录依赖，也没有逃逸 App 的符号链接。所有代码在重定位后重新签名。
- App 内嵌 Python 的 doctor 报告 `darwin/arm64/3.11.15`，FFmpeg、ffprobe、go2rtc 和
  nginx 均可用。生产 0.17 配置的临时副本再次迁移并验证为 3 个摄像头、ZMQ detector
  和 VideoToolbox；原备份哈希保持不变。
- 6 个 Swift 测试覆盖设置文件权限、内嵌路径、HTTPS 地址、bootstrap 参数、重启
  上限和真实子进程优雅停止。菜单栏 App 本体 smoke test 成功启动并结束，结束后 1984、
  5001、5002、8554、8555、8971 均无监听。
- 使用生产备份配置和 NFS 媒体目录完成了安装版 App 验收。原生 bootstrap 会将配置中
  的容器路径转换为本机路径；前端媒体 URL 转换同时识别 `/media/frigate` 和任意原生
  媒体根目录，避免把 `/Volumes/.../clips` 等文件系统路径直接作为浏览器 URL。
- 已登录 Web UI 的警报和检测页面完成真实图片验收。抽查的 Review 缩略图均请求
  `/clips/review/...`，WebP 图片加载完成且自然尺寸约为 320×180；NFS 中原文件和数据库
  路径保持不变。nginx 同时替换 Web 构建中的 `/BASE_PATH/` 占位符，避免原生根路径
  部署出现空白页面。
- macOS 通过 NFS 写入分类图片时可能为每个 WebP 生成 `._*.webp` AppleDouble 元数据
  sidecar。分类 API 必须排除隐藏文件，否则“最近分类记录”会把真实图片和 sidecar
  各返回一次，形成成功图片与破图交错的网格，并把训练数据计数翻倍。原生实现只过滤
  API 和计数结果，不删除 NFS 上的 sidecar 数据。
- 系统存储页不再固定读取 `/media/frigate/recordings` 和 `/tmp/cache`。前端优先使用
  Docker 兼容键，并按 `recordings`、`cache` 目录名识别原生实际路径。本机验收已显示
  NFS 录像、用户缓存、POSIX 共享内存以及三台摄像头的存储和带宽统计，页面无控制台
  错误。
- macOS 的 POSIX 共享内存统计通过 `libproc` 读取 Frigate 主进程持有的 `PSXSHM`
  描述符，并按对象名去重汇总逻辑分配量。界面显示“逻辑分配 / Frigate 管理预算”，
  不再把动态可用内存的 5% 描述为 `/dev/shm` 固定容量。管理预算由 8 MiB 原生开销、
  启用摄像头的帧大小和 `SHM_MAX_FRAMES` 计算，不包含 Linux 日志 tmpfs 的 50 MiB，
  也不为 macOS 的弹性 POSIX SHM 虚构两个备用摄像头。生产配置验收显示 156 个对象，
  逻辑分配 315 MiB，管理预算 358 MiB，原 254 MiB 容量告警已消失。
- 原生 App 验收使用内嵌 Python 的 isolated 模式时同时传入 `-B`，避免验收导入在已签名
  App 内生成新的 `.pyc` 文件并破坏资源封印。验收完成后的第二次严格签名检查通过。

当前 App 使用 ad hoc 开发签名。全新 Mac 的无 Homebrew 结构性要求已经通过依赖扫描和
可移动路径验证；Developer ID 签名、公证、DMG 和更新清单属于阶段 6，不能把当前开发
构建误称为已公证发行版。

阶段 5 的自动化验收命令为：

```console
swift test --package-path macos
macos/scripts/build-native-app.sh "$PWD/macos/build/Frigate.app"
macos/scripts/check-native-app.sh "$PWD/macos/build/Frigate.app"
```

### 阶段 6：打包、更新与安全

- 为所有内嵌二进制和原生扩展签名，并启用 Hardened Runtime。
- 公证 DMG 并附加公证票据。
- 发布包含校验和、版本信息和发行说明的签名更新清单。
- 实现原子安装、上一版本回滚和数据备份提示。
- 审计 nginx 路由、go2rtc 管理接口、静态文件服务和摄像头 ACL。

验收标准：

- Gatekeeper 在全新 Mac 上接受 DMG。
- 更新过程被中断后，旧版本或新版本中至少有一个能够正常运行。
- 内部 API 和管理端口不能从局域网访问。

### 阶段 7：自适应码率播放

只有在原生媒体管线通过长期多摄像头测试后才能开始本阶段。

- 增加全局和单摄像头自适应码率配置。
- 增加有并发上限的 VideoToolbox 转码任务池和分片缓存。
- 生成经过认证的 HLS 主播放列表和多码率变体。
- 在 React 播放器中增加自动和手动清晰度选择。
- 对每个播放列表和分片请求执行摄像头 ACL。

验收标准：

- 在受控带宽下降测试中能够自动切换码率。
- 配置允许时，本地客户端可以使用原始视频流。
- 重启和不同用户角色下，缓存清理与访问控制保持正确。

## 初始源码改动范围

| 范围 | 主要源码位置 |
| --- | --- |
| 运行时路径 | `frigate/const.py`、`frigate/runtime/paths.py` |
| IPC | `frigate/comms/*.py`、`frigate/detectors/plugins/zmq_ipc.py` |
| Linux 服务假设 | `frigate/util/services.py`、`frigate/app.py` |
| 视频预设 | `frigate/ffmpeg_presets.py`、`frigate/config/camera/ffmpeg.py` |
| 视频输出 | `frigate/output/*`、`frigate/record/export.py` |
| 运行指标 | `frigate/stats/*` |
| 原生启动器 | `docker/main/rootfs/etc/s6-overlay/`、新增原生运行时模块 |
| macOS 界面 | 新增 `macos/` Xcode 工程 |

## 测试策略

- 单元测试覆盖路径解析、IPC 地址、配置迁移、进程状态转换、重启退避和服务配置生成。
- 集成测试只使用本地媒体夹具和 loopback 服务。
- 硬件测试至少覆盖一台 M1 级别设备和一台当前型号 Apple Silicon Mac，验证 CoreML
  执行位置和 VideoToolbox 行为。
- 长期测试覆盖进程崩溃、摄像头断线、网络切换、睡眠唤醒、外接媒体移除、磁盘空间
  不足和应用更新。
- 安全测试覆盖 UI、API、WebSocket、录像、导出、HLS 和 go2rtc 代理路由的认证及
  摄像头权限。

## 上游同步策略

- 保留指向 `blakeblackshear/frigate` 的 `upstream` remote。
- 在明确的同步节点 rebase 或 merge 上游 `dev`，发行稳定阶段不合并大规模上游变更。
- 维护机器可读的 macOS 专用补丁清单，并记录每项补丁对应的测试。
- 优先使用环境变量驱动的抽象和独立平台模块，避免修改无关核心行为。
- 不复制 Fregata 的专有代码或二进制实现。相似能力必须基于 Frigate 与 macOS 平台
  API 独立实现。

## 首个开发切片

首个代码切片只实现经过验证的运行时路径覆盖，同时保留全部现有 Docker 默认值。内部
后端 IPC 地址也将通过统一运行时目录生成。这一切片为原生命令行 bootstrap 和 Swift
supervisor 提供基础，但不修改媒体处理行为。
