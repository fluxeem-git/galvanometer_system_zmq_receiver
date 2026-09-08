# Galvanometer ZMQ Receiver

这是面向客户和系统集成人员的独立 ZeroMQ 融合转速接收工程，不依赖
Galvanometer System Terminal 源码。

完整的 Terminal 配置、网络要求、wire 协议、字段单位、验收方法和故障排查见
[ZMQ 融合转速数据接收指南](docs/zmq-rotation-receiver.md)。

## 目录

| 路径 | 内容 |
| --- | --- |
| `cpp/` | C++17 ZeroMQ SUB 例程 |
| `python/capture_rotation_stream.py` | 严格校验并保存 NDJSON |
| `python/visualize_rotation_stream.py` | 实时散点可视化与无桌面 PNG 快照 |
| `docs/zmq-rotation-receiver.md` | 客户中文使用手册 |
| `third_party/` | 固定版本的 libzmq、cppzmq、nlohmann-json 源码与许可证 |

默认连接参数：

- endpoint：`tcp://127.0.0.1:5556`，跨机器时把地址改为 Terminal 主机 IPv4；
- Topic：`terminal/rotation/v1`；
- 消息：两个 multipart frame，依次为 exact Topic 和 UTF-8 JSON payload。

## C++ / CMake

要求 CMake 3.21+ 和支持 C++17 的编译器。默认直接使用 `third_party/` 中固定版本的依赖
源码，不要求 vcpkg、apt 开发包、Git 或构建时联网，并把 libzmq 静态链接进程序。

Windows PowerShell：

```powershell
cmake -S . -B build
cmake --build build --config Release
./build/Release/rotation_receiver.exe `
  --endpoint tcp://192.168.2.10:5556 `
  --topic terminal/rotation/v1
```

Ubuntu：

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel
./build/rotation_receiver \
  --endpoint tcp://192.168.2.10:5556 \
  --topic terminal/rotation/v1
```

例程严格检查 exact Topic、multipart 数量、字段集合、类型、有限数值、坐标系和
`mocapFrameNo` 安全整数范围，并从 `omegaWorldRadPerSecond` 计算 rad/s 与 RPM。
`--stale-ms` 可调整本地无数据 watchdog，默认 `1000` ms。按 Ctrl+C 停止。

默认构建为静态 libzmq，因此 Windows 交付 `rotation_receiver.exe` 时不需要额外携带 ZeroMQ
DLL。Ubuntu 二进制仍会依赖系统 C/C++ 运行库；跨 Ubuntu 版本分发时，应在与目标系统相同
或更老的受控环境中产出。

已使用 vcpkg 或 Ubuntu 系统开发包的环境也可选择外部依赖：

```bash
cmake -S . -B build-system \
  -DGALVANOMETER_USE_SYSTEM_DEPENDENCIES=ON \
  -DCMAKE_BUILD_TYPE=Release
```

Windows vcpkg 用户先执行 `vcpkg install cppzmq nlohmann-json`，再传入 vcpkg toolchain
文件。Ubuntu 用户可安装 `libzmq3-dev cppzmq-dev nlohmann-json3-dev`。默认客户构建不需要
这一步。

## Python

安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 后：

```powershell
cd python
uv run capture_rotation_stream.py --endpoint tcp://192.168.2.10:5556 --duration-seconds 60
uv run visualize_rotation_stream.py --endpoint tcp://192.168.2.10:5556
```

脚本内含 Python 3.10+ 与固定版本依赖声明，`uv run` 会创建隔离环境。抓取结果默认
写入 `python/rotation-captures/`。

## 重要语义

- Terminal 只为 `SINGLE_SOURCE` / `CONSENSUS` 发布数据；`NONE` / `CONFLICT` 不发消息。
- 发布链路是 latest-only/coalescing，不保证每个 Fusion 结果都抵达。
- 协议没有 heartbeat、停止包、认证或加密；接收端必须使用 receipt-time watchdog，
  且只能部署在可信受控网络。
- 本工程的本机 loopback/synthetic 验证不能替代真实 Terminal、Node、现场网络和客户
  接收机验收。

第三方版本与许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
