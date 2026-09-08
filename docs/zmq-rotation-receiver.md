# ZMQ 融合转速数据接收指南

本文面向负责把 Galvanometer System Terminal（以下简称 Terminal）接入客户系统的
工程师。按本文操作，可以完成 Terminal 端配置、接收机网络检查、数据抓取、实时可视化、
程序接入和首次联调验收，不需要阅读 Terminal 源码。

## 1. 交付内容和适用范围

Terminal 通过 ZeroMQ `PUB` socket 发布当前有效的融合旋转结果。客户程序使用 ZeroMQ
`SUB` socket 主动连接 Terminal，并订阅双方约定的 Topic。

本独立客户工程包含以下接收工具：

| 文件 | 用途 |
| --- | --- |
| `python/capture_rotation_stream.py` | 严格校验并把全部接收记录保存为 NDJSON，适合联调取证和离线分析 |
| `python/visualize_rotation_stream.py` | 实时显示 RPM、角速度向量、旋转轴、接收率和异常统计 |
| `cpp/src/main.cpp` | C++17 ZeroMQ SUB、严格协议校验与 watchdog 例程 |
| `third_party/` | 固定版本的 libzmq、cppzmq、nlohmann-json 源码与许可证 |
| `CMakeLists.txt` | Windows / Ubuntu 共用的 C++ 构建入口 |
| `README.md` | C++ / Python 构建运行速查 |

当前协议只发布融合旋转信息，不发布球位置、Fusion 状态、时间戳或消息序号。

## 2. 网络结构

```text
有效 Node Tracking 数据
          │
          ▼
Terminal Fusion ── ZeroMQ PUB（绑定 Terminal 的具体 IPv4:端口）
                                  ▲
                                  │ TCP 连接
                                  │
客户接收机 / Robot Gateway ── ZeroMQ SUB（connect 到 Terminal）
```

必须记住以下方向：

- Terminal 是发布端和 TCP 监听端；
- 客户接收机是订阅端，主动连接 Terminal；
- `endpoint` 中填写的是 Terminal 的 IPv4 地址，不是接收机地址；
- Terminal 只绑定所选网卡的具体 IPv4，不绑定 `0.0.0.0`；
- ZeroMQ PUB/SUB 没有请求响应，Terminal 无法据此判断某个客户是否已收到消息。

## 3. 准备信息

联调前由双方确认并记录：

| 项目 | 默认值/示例 | 说明 |
| --- | --- | --- |
| Terminal IPv4 | `192.168.2.10` | 必须是客户接收机能够访问的 Terminal 网卡地址 |
| TCP 端口 | `5556` | Terminal 默认值；允许范围 `1–65535` |
| Topic | `terminal/rotation/v1` | Terminal 默认值；发送与接收必须完全一致 |
| 接收时长 | `60 s` | 首次联调建议至少抓取 60 秒 |
| 无数据阈值 | `1 s` | 示例 watchdog；应根据现场预期发布频率调整 |

建议 Terminal 与接收机使用固定地址或 DHCP 保留地址。地址变化后，接收机原 endpoint
不会自动变成新地址。

## 4. 配置 Terminal

1. 启动 Terminal。
2. 点击标题栏的“设置”按钮。
3. 如果这是首次使用，只能看到 Node 网络设置，请先选择 Node 所在网络并按界面提示保存、
   重启 Terminal；完成首次网络设置后才能进入“数据转发”。
4. 在设置左侧选择“数据转发”。
5. 打开“ZeroMQ 发布”开关。
6. 在“绑定网卡”中选择能够到达客户接收机的网卡。下拉项会显示
   `网卡名称 · IPv4/前缀长度`。
7. 输入端口。未协商其他端口时保留默认值 `5556`。
8. 输入 Topic。未协商其他 Topic 时保留默认值 `terminal/rotation/v1`。
9. 检查底部摘要，应显示类似 `应用后绑定 tcp://192.168.2.10:5556`。
10. 点击“应用设置”。
11. 确认状态显示“运行中”，并确认界面显示的 endpoint 和 Topic 与联调记录一致。

如果应用失败，Terminal 继续运行旧配置，不会静默切换到一半配置。先记录界面错误，再按
“故障排查”处理。

关闭发布时，重新进入“数据转发”，关闭“ZeroMQ 发布”并应用。关闭后 Terminal 不再监听
该 ZeroMQ 端口。

## 5. 配置网络和防火墙

Terminal 所在网络必须是可信、受控网络。当前 TCP ZeroMQ 不提供认证、访问控制或加密，
禁止直接暴露到互联网或不可信办公网络。

在 Terminal 主机防火墙中，仅对客户接收机所在网段或指定 IP 放行配置的 TCP 入站端口。
接收机通常只需允许到 Terminal 的 TCP 出站连接。

在 Windows 接收机检查端口：

```powershell
Test-NetConnection 192.168.2.10 -Port 5556
```

期望看到：

```text
TcpTestSucceeded : True
```

在 Linux 接收机可使用：

```bash
nc -vz 192.168.2.10 5556
```

端口检查只能证明 TCP 可达，不能证明 Topic 正确或当前存在有效 Fusion 数据。

## 6. 安装接收工具运行环境

接收工具要求 Python 3.10 或更高版本。推荐使用 `uv`，它会根据脚本内声明自动安装固定版本
的 `pyzmq` 和可视化依赖，不需要手工创建虚拟环境。

Windows 可从 PowerShell 安装：

```powershell
winget install --id astral-sh.uv -e
uv --version
```

Linux 可使用 uv 官方安装脚本：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version
```

把整个 `galvanometer_zmq_receiver` 工程复制到接收机。两个 Python 文件必须保留在
`python/` 目录中。运行 Python 例程前先进入该目录；构建 C++ 例程则在工程根目录执行
README 中的 CMake 命令。

如果现场不能访问互联网，应在部署前按客户的软件供应链流程准备 Python、依赖 wheel 或内部
镜像；不要在生产网临时绕过客户的包校验和代理策略。

## 7. 首次抓取数据

Windows PowerShell：

```powershell
cd C:\path\to\galvanometer_zmq_receiver\python
uv run capture_rotation_stream.py `
  --endpoint tcp://192.168.2.10:5556 `
  --topic terminal/rotation/v1 `
  --duration-seconds 60
```

Linux shell：

```bash
cd /path/to/galvanometer_zmq_receiver/python
uv run capture_rotation_stream.py \
  --endpoint tcp://192.168.2.10:5556 \
  --topic terminal/rotation/v1 \
  --duration-seconds 60
```

不传 `--duration-seconds` 时，按 `Ctrl+C` 停止。默认输出保存在当前目录的
`rotation-captures/rotation-<时间>.ndjson`。也可以指定新文件：

```powershell
uv run capture_rotation_stream.py `
  --endpoint tcp://192.168.2.10:5556 `
  --output C:\captures\acceptance-001.ndjson
```

工具不会覆盖已有输出文件。如果路径已存在，请换一个文件名，以免破坏既有验收证据。

结束时会打印摘要。首次联调至少确认：

- `received > 0`；
- `validPayload == received`；
- `invalid == 0`；
- endpoint 和 Topic 与 Terminal 界面一致；
- 抓取时 Terminal 正在运行 Tracking，并且存在有效 Fusion 结果。

不要把 `frameGaps == 0` 作为网络验收的强制条件，原因见“传输语义和限制”。

## 8. 实时可视化

启动实时窗口：

```powershell
uv run visualize_rotation_stream.py `
  --endpoint tcp://192.168.2.10:5556 `
  --topic terminal/rotation/v1
```

界面包含：

- `Speed (RPM)`：根据 `omegaWorldRadPerSecond` 的模长计算，以散点显示；
- `Omega (rad/s)`：世界坐标系下 X/Y/Z 三个角速度分量，以不同形状的散点显示；
- `Axis component`：世界坐标系下旋转轴 X/Y/Z 分量，以不同形状的散点显示；
- 最新 frame、最近一秒接收率、累计接收数、invalid、frame gap、repeat 和 regression。

状态含义：

| 状态 | 含义 | 操作 |
| --- | --- | --- |
| `WAITING FOR VALID DATA` | 启动后尚未收到严格有效的消息 | 检查网络、Topic、Tracking/Fusion 状态 |
| `LIVE` | 最近收到有效数据 | 正常 |
| `STALE` | 超过 `--stale-seconds` 未收到有效数据 | 检查 Terminal、网络和 Fusion 数据源 |
| `PAUSED` | 图表暂停，但后台仍继续接收 | 按 Space 或点击 Resume 恢复 |

按 Space 或点击 Pause/Resume 暂停和恢复；按 Q 退出。默认显示最近 30 秒，可调整：

```powershell
uv run visualize_rotation_stream.py `
  --endpoint tcp://192.168.2.10:5556 `
  --window-seconds 60 `
  --stale-seconds 2 `
  --max-fps 20
```

无桌面环境可生成 PNG 快照：

```bash
uv run visualize_rotation_stream.py \
  --endpoint tcp://192.168.2.10:5556 \
  --duration-seconds 10 \
  --snapshot rotation-captures/rotation-dashboard.png
```

## 9. Wire 协议

每条 ZeroMQ 消息必须恰好包含两个 multipart frame：

| Frame | 类型 | 内容 |
| --- | --- | --- |
| `0` | UTF-8 bytes | Topic，例如 `terminal/rotation/v1` |
| `1` | UTF-8 JSON bytes | `RotationStateV1` payload |

示例 payload：

```json
{
  "axisWorld": [1, 0, 0],
  "coordinateFrame": "mocap_world",
  "mocapFrameNo": 55564,
  "omegaWorldRadPerSecond": [3, 0, 0]
}
```

字段定义：

| 字段 | 类型 | 单位/坐标系 | 约束与说明 |
| --- | --- | --- | --- |
| `axisWorld` | 长度为 3 的 number 数组 | `mocap_world` | 旋转轴方向分量；每个值必须有限 |
| `coordinateFrame` | string | — | 当前版本固定为 `mocap_world` |
| `mocapFrameNo` | 非负安全整数 | Mocap frame | contributor 中的权威 frame cursor；不是网络消息序号 |
| `omegaWorldRadPerSecond` | 长度为 3 的 number 数组 | rad/s，`mocap_world` | 世界坐标系角速度向量；每个值必须有限 |

payload 必须只包含以上四个字段。多字段、少字段、非法 UTF-8、非法 JSON、`NaN`、
`Infinity`、错误坐标系或不安全 frame number 都应被拒绝。

协议不直接发送标量转速。客户如需计算：

```text
speed_rad_s = sqrt(omega_x² + omega_y² + omega_z²)
speed_rpm   = speed_rad_s × 60 / (2π)
```

## 10. 最小 Python 接入示例

下面代码包含 exact Topic、multipart 数量、字段集合、数值有限性和 receipt-time watchdog
检查，可作为客户程序的最小起点：

```python
import json
import math
import os
import time

import zmq

ENDPOINT = os.environ.get("TERMINAL_ZMQ_ENDPOINT", "tcp://192.168.2.10:5556")
TOPIC = os.environ.get("TERMINAL_ZMQ_TOPIC", "terminal/rotation/v1")
EXPECTED_KEYS = {
    "axisWorld",
    "coordinateFrame",
    "mocapFrameNo",
    "omegaWorldRadPerSecond",
}


def is_vector3(value):
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(
            isinstance(component, (int, float))
            and not isinstance(component, bool)
            and math.isfinite(component)
            for component in value
        )
    )


def is_rotation_state(value):
    if not isinstance(value, dict) or set(value) != EXPECTED_KEYS:
        return False
    frame_no = value["mocapFrameNo"]
    return (
        value["coordinateFrame"] == "mocap_world"
        and isinstance(frame_no, int)
        and not isinstance(frame_no, bool)
        and 0 <= frame_no <= 9_007_199_254_740_991
        and is_vector3(value["axisWorld"])
        and is_vector3(value["omegaWorldRadPerSecond"])
    )


context = zmq.Context()
subscriber = context.socket(zmq.SUB)
subscriber.setsockopt(zmq.RCVHWM, 10_000)
subscriber.setsockopt(zmq.LINGER, 0)
subscriber.setsockopt(zmq.SUBSCRIBE, TOPIC.encode("utf-8"))
subscriber.connect(ENDPOINT)
poller = zmq.Poller()
poller.register(subscriber, zmq.POLLIN)
last_valid_at = time.monotonic()
stale_reported = False

try:
    while True:
        if subscriber not in dict(poller.poll(200)):
            if time.monotonic() - last_valid_at > 1.0 and not stale_reported:
                print("STALE: no valid rotation payload for more than 1 second")
                stale_reported = True
            continue

        frames = subscriber.recv_multipart()
        if len(frames) != 2 or frames[0] != TOPIC.encode("utf-8"):
            print("INVALID: unexpected multipart shape or topic")
            continue

        try:
            payload = json.loads(frames[1].decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            print(f"INVALID: {error}")
            continue
        if not is_rotation_state(payload):
            print("INVALID: payload does not match RotationStateV1")
            continue

        last_valid_at = time.monotonic()
        stale_reported = False
        omega = payload["omegaWorldRadPerSecond"]
        speed_rad_s = math.sqrt(sum(component * component for component in omega))
        speed_rpm = speed_rad_s * 60 / (2 * math.pi)
        print(payload["mocapFrameNo"], speed_rad_s, speed_rpm, payload)
except KeyboardInterrupt:
    pass
finally:
    subscriber.close()
    context.term()
```

运行时安装依赖并启动：

```powershell
$env:TERMINAL_ZMQ_ENDPOINT = "tcp://192.168.2.10:5556"
$env:TERMINAL_ZMQ_TOPIC = "terminal/rotation/v1"
uv run --with pyzmq==27.1.0 python customer_rotation_subscriber.py
```

Linux：

```bash
TERMINAL_ZMQ_ENDPOINT=tcp://192.168.2.10:5556 \
TERMINAL_ZMQ_TOPIC=terminal/rotation/v1 \
uv run --with pyzmq==27.1.0 python customer_rotation_subscriber.py
```

生产集成应把日志、重连状态、watchdog 状态和 invalid 原因接入客户自身监控系统，而不是只
打印到终端。

## 11. C++ / CMake 接入示例

工程根目录的 `cpp/src/main.cpp` 提供与 Python 工具相同严格度的 C++17 SUB 示例。
固定版本依赖源码已经放入 `third_party/`；默认构建不要求 vcpkg、apt 开发包、Git 或联网。
准备 CMake 3.21+ 与 C++17 编译器后，在工程根目录构建：

```powershell
cmake -S . -B build
cmake --build build --config Release
```

启动 Windows Release 程序：

```powershell
./build/Release/rotation_receiver.exe `
  --endpoint tcp://192.168.2.10:5556 `
  --topic terminal/rotation/v1 `
  --stale-ms 1000
```

Ubuntu 安装基础编译工具后使用同一 `CMakeLists.txt`：

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel
./build/rotation_receiver \
  --endpoint tcp://192.168.2.10:5556 \
  --topic terminal/rotation/v1
```

单配置生成器的可执行文件通常位于 `build/rotation_receiver`。例程会拒绝同前缀但不完全相等的 Topic、错误 multipart 数量、
多余/缺失字段、错误类型、非有限数值、错误坐标系和超出 JSON 安全整数范围的 frame，
并打印 frame、omega、axis、rad/s 与 RPM。按 Ctrl+C 停止。

该程序是接入起点，不是可靠消息落盘服务。生产集成仍需把日志、watchdog、进程监督和客户
业务处理接入自身运行环境。

## 12. 传输语义和限制

### 12.1 哪些 Fusion 结果会发布

- `SINGLE_SOURCE` 和 `CONSENSUS`：发布四字段 `RotationStateV1`；
- `NONE` 和 `CONFLICT`：不发布消息；
- Tracking 停止、Terminal 关闭、网卡失效或发布禁用：不发送额外 stop/unavailable 消息。

因此，客户必须使用本地接收时间 watchdog 判断数据是否停止，不能等待一个“停止包”。

### 12.2 latest-only，不是可靠消息队列

发布端为实时控制数据采用 latest-only 策略：发送阻塞时只保留一个最新 pending 值，新值可能
合并掉旧 pending 值。新订阅者连接前的数据不会重放。该设计防止慢客户阻塞 Terminal，但不
保证每个 Fusion 结果都抵达。

如果业务要求逐条可靠、可重放或可审计传输，需要另行定义协议和交付方式，不能把当前
PUB/SUB 当作消息队列使用。

### 12.3 frame number 不是消息序号

`mocapFrameNo` 来自 Fusion contributor，并非 Terminal ZMQ 自增 sequence。Fusion 输入可能跳过
Mocap frame，latest-only 也可能合并中间值。因此：

- frame 增长可用于观察数据是否前进；
- frame regression/repeat 值值得调查；
- frame gap 不能单独证明网络丢包；
- wire 不含 `jobId/runId/effectSequence`，客户不能仅凭当前 payload 对 Terminal 内部 Fusion
  事件逐条关联。

### 12.4 Topic 必须精确比较

ZeroMQ `SUBSCRIBE` 使用字节前缀匹配。订阅 `terminal/rotation/v1` 也可能接收到以该字符串为
前缀的其他 Topic。客户代码必须像交付工具一样，再对 frame 0 做完整字节相等检查。

## 13. 首次现场联调验收

建议由 Terminal 操作员和客户接入工程师共同完成：

1. 记录 Terminal 版本/构建、Terminal IPv4、端口、Topic、接收机地址和测试时间。
2. Terminal 界面显示 ZeroMQ“运行中”，endpoint 与记录一致。
3. 接收机端口检查成功。
4. 启动真实 Node Tracking，确认 Terminal 有有效融合旋转结果。
5. 运行 60 秒 NDJSON 抓取，保存完整文件和终端 summary。
6. 确认 `received > 0`、`invalid == 0`，抽查 payload 只有四个字段且单位解释正确。
7. 启动可视化，确认旋转时 RPM/omega 散点分布响应，停止有效数据后进入 `STALE`。
8. 关闭 ZeroMQ 发布，确认端口停止监听和接收端进入 `STALE`。
9. 恢复发布，确认接收端重新获得有效数据。
10. 把构建信息、命令、通过/失败数、NDJSON、截图和所有限制写入项目验收记录。

本地 loopback 或 synthetic 数据只能证明软件路径和工具行为，不能替代真实 Node、真实
Terminal 主机网卡/防火墙、现场交换网络和 Robot Gateway 的端到端验收。

## 14. 故障排查

| 现象 | 常见原因 | 检查和处理 |
| --- | --- | --- |
| 设置中看不到“数据转发” | 首次 Node 网络设置未完成 | 先完成 Node 网络设置并按提示重启 Terminal |
| Terminal 显示“失败” | 绑定地址不存在、端口占用、native addon 或 socket 错误 | 记录错误；确认网卡/IP 仍存在；换空闲端口后重新应用 |
| 显示“运行中”但端口不通 | 防火墙、VLAN/路由、地址或端口错误 | 对照界面 endpoint；执行 `Test-NetConnection`/`nc`；最小化放行规则 |
| 端口可达但 `received == 0` | Topic 不一致、Tracking 未运行、Fusion 为 NONE/CONFLICT、新订阅者仍在建立连接 | 精确核对 Topic；确认有效 Tracking/Fusion；等待数秒再观察 |
| 持续 `WAITING` | 尚未收到严格有效 payload | 先用抓取脚本确认 invalid；核对 endpoint、Topic 和发布状态 |
| 进入 `STALE` | Terminal/Tracking 停止、无有效 Fusion、网络中断、网卡失效 | 检查 Terminal 状态和错误；恢复数据源/网络；不要等待 stop 包 |
| `invalid > 0` | 错误 Topic 前缀、multipart 数量错误、非法 UTF-8/JSON 或字段不兼容 | 查看 NDJSON 的 `error`、`framesBase64` 或 `payloadBase64`；核对协议版本 |
| frame gap 较多 | Fusion 本身跳 frame、发送端 coalescing、接收端处理过慢或网络问题 | 结合 Terminal coalesced/sendFailures、接收率和现场网络共同判断 |
| frame regression/repeat | 数据源/运行切换、上游异常或客户重复处理 | 保存原始 NDJSON，记录发生时间，检查 Terminal 运行和上游数据 |
| Terminal IP 变化后收不到 | endpoint 仍指向旧地址 | 更新 Terminal 绑定网卡配置和客户 endpoint；建议使用地址保留 |
| `uv` 命令不存在 | 未安装或 PATH 尚未刷新 | 重新打开终端，执行 `uv --version`，按客户软件安装策略修复 PATH |
| 可视化窗口无法打开 | 接收机无桌面或图形后端不可用 | 使用 `--snapshot`，或只运行 NDJSON 抓取工具 |

遇到问题时，至少保留以下信息再提交支持请求：Terminal 构建版本、界面 endpoint/Topic/
状态和完整错误、接收命令、接收机系统版本、端口检查结果、NDJSON 文件以及问题时间范围。

## 15. 数据和安全处理

- 只在受控可信网段启用 ZeroMQ 发布；
- 防火墙规则限制到明确的接收机 IP/网段；
- NDJSON 可能包含客户运行数据，按客户数据分级要求存储、传输和销毁；
- 不要修改 invalid 记录或删除错误行后再把文件作为原始验收证据；
- 变更 IP、端口或 Topic 时，Terminal 与所有客户接收端必须协调切换；
- 如果需要认证、加密、可靠重放或逐消息身份，请先建立新的接口需求，当前协议不提供这些
  能力。
