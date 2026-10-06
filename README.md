# 京东亚瑟 AX1800 Pro · OpenWrt

为 **JDCloud RE-SS-01（亚瑟 AX1800 Pro）** 编译官方 OpenWrt 源码，支持 **24.10 和 25.12 同时构建**。两版共用精简配置和默认界面，各自应用匹配的内核、设备与 fstools 补丁，生成 **sysupgrade 和 factory** 镜像。

这是官方源码加社区板级适配的自编译固件，不是 OpenWrt 官方发布的机型镜像。设备接线和镜像布局来源见 [硬件说明](docs/hardware.md)。

| 系列 | 锁定版本 | 内核系列 | 包管理器 | 发布镜像 |
| --- | --- | --- | --- | --- |
| 24.x | 24.10.8 | Linux 6.6 | opkg | sysupgrade.bin、factory.bin |
| 25.x | 25.12.5 | Linux 6.12 | apk | sysupgrade.bin、factory.bin |

版本、源码提交、配置片段和补丁顺序统一由 [sources.lock.json](sources.lock.json) 管理。核心、官方 feeds 和 Argon 都固定到具体提交。

## 默认配置

- 目标：`qualcommax/ipq60xx`，设备 `jdcloud_re-ss-01`。
- eMMC 持久化 Overlay，首次初始化使用 ext4；排查见 [Overlay 说明](docs/overlay.md)。
- 简体中文 LuCI、Argon 暗色、HTTP / HTTPS；保留网络、无线、防火墙、IPv6 和 PPPoE 管理。
- LAN `192.168.10.1/24`；双频 Wi-Fi 开启，SSID 均为 `OpenWrt`，无密码。首次登录后设置管理员与无线密码。
- 保留官方 ath11k 和网口驱动 `kmod-qca-nss-dp`；不引入 NSS 转发加速、ECM、代理、Docker 等扩展。
- 内建 TUN、连接诊断、nftables socket / TPROXY 及必要基础依赖，附带 `ip-full` 策略路由工具，便于后续使用 Mihomo；具体范围见 [内核配置说明](docs/customization.md#常用网络能力内建)。

## GitHub Actions 构建与发布

推送构建相关改动到 `main` 会并行构建两个版本。也可进入 **Actions → Build AX1800 Pro → Run workflow**：`release=all` 构建两版，或选择 `24.10` / `25.12`；`jobs=auto` 使用 runner 全部 CPU，内存不足时可改为 `2`。

每个版本独立准备源码、下载、编译、验证和发布。一版失败不会取消另一版。下载缓存和 ccache 按版本隔离，每次运行保存更新。编译失败会单线程重试；失败时也收集来源信息和日志。

源码下载优先尝试 OpenWrt 官方源码 CDN，未命中时继续使用包定义的 GNU 等上游镜像，始终校验原有哈希。Actions 限制单镜像重试，并在连接失败或持续低速时切换，减少 GNU 镜像重定向超时造成的等待。

成功后的下载入口：

| 位置 | 内容 |
| --- | --- |
| Releases | `main` 自动创建每版独立的预发布；两种镜像、设备 manifest、profiles.json、build-info.json、SHA256SUMS |
| `openwrt-版本-jdcloud-ax1800pro-运行编号-尝试编号` artifact | 与 Release 相同的已校验附件，保留 30 天 |
| `packages-版本-运行编号-尝试编号` artifact | 同次编译的目标软件包，含内核模块，保留 30 天 |
| `openwrt-版本-系列-kernel-modules.tar.zst` Release 附件 | 同次编译的全部 `kmod-*` 包和索引，可解压后配置为本地软件源 |
| `build-info-版本-运行编号-尝试编号` artifact | 配置、来源提交、补丁摘要及日志，保留 14 天 |

发布前同时验证设备、版本、包管理器、必要运行包、镜像校验和与结构。必须同时生成可通过校验的 sysupgrade 和 factory，才会发布该版本。`SHA256SUMS` 覆盖所有发布附件。

构建还检查实际内核 `.config` 与 `modules.builtin`，确认选定功能真正内建；日志显示 FIT 内核大小及距离 6 MiB 的剩余空间。`build-info` artifact 保存实际内核配置和内建模块清单。

## 选择刷入文件

| 文件 | 用途 |
| --- | --- |
| `*jdcloud_re-ss-01-squashfs-sysupgrade.bin` | 已运行兼容 OpenWrt 系统的常规升级，使用 LuCI 升级页面或 `sysupgrade` |
| `*jdcloud_re-ss-01-squashfs-factory.bin` | 匹配本机分区布局的 U-Boot／恢复刷写：FIT 内核 + 补齐至 6 MiB + SquashFS + 元数据 |

**factory 不是整盘镜像，也没有验证可由京东原厂网页直接刷入。** 它不包含 GPT、引导程序或 ART 校准数据。必须按已安装 U-Boot／恢复工具支持的方式使用；不要将整个 factory 写入 `0:HLOS` 或传给 `sysupgrade`。本项目的升级检查会拒绝 factory。布局和容量要求见 [硬件说明](docs/hardware.md)。

24.x 与 25.x 之间切换，或从其他固件迁移时，先保存所需设置，升级使用 `sysupgrade -n`，不沿用旧配置和软件源。自编译内核 ABI 不保证与官方仓库一致；额外内核模块优先使用同次构建的 packages，或加入配置重新编译。

## 项目结构与定制

- [sources.lock.json](sources.lock.json)：唯一版本定义；升级时同步复核对应版本补丁和构建结果。
- [config/ax1800pro.config](config/ax1800pro.config)：两版共用设备、界面和软件包选择；`config/24.10.config` / `config/25.12.config` 选择版本依赖。
- [config/kernel-builtins.config](config/kernel-builtins.config)：两版共用的 Linux 内建符号，准备时复制到 OpenWrt 原生的 `env/kernel-config`。
- [patches/common](patches/common)：共用设备树；`patches/24.10` / `patches/25.12`：版本适配，按锁文件目录顺序、文件名顺序应用。
- [files](files)：首次启动网络、中文、时区和主题；具体行为见 [定制说明](docs/customization.md)。
- [scripts](scripts)：统一版本加载、源码准备、配置和固件校验、来源记录及发布附件收集。

## 本地检查与编译

使用 Linux / WSL 的 Linux 文件系统存放编译源码；依赖见 [.github/actions/setup-openwrt/action.yml](.github/actions/setup-openwrt/action.yml)。

```sh
python3 scripts/project.py validate
bash -n scripts/prepare.sh
shellcheck scripts/prepare.sh files/etc/uci-defaults/99-project-settings
python3 -m unittest discover -s tests -v

# 目标源码目录必须尚不存在。默认系列为 24.10。
bash scripts/prepare.sh "$HOME/build/ax1800pro-24" --release 24.10
bash scripts/prepare.sh "$HOME/build/ax1800pro-25" --release 25.12

cd "$HOME/build/ax1800pro-25"
# 与 Actions 相同的下载策略；不限制正常大文件下载的总时长。
export CURL_OPTIONS='--connect-timeout 10 --retry 1 --retry-delay 1 --speed-limit 1024 --speed-time 30'
export WGET_OPTIONS='--tries=2 --timeout=30 --dns-timeout=10 --connect-timeout=10'
make download -j8
make -j"$(nproc)" BUILD_LOG=1
```

构建完成后回到项目目录，校验并整理附件：

```sh
python3 scripts/check-config.py "$HOME/build/ax1800pro-25/.config" --release 25.12
python3 scripts/check-kernel.py "$HOME/build/ax1800pro-25" --release 25.12
python3 scripts/build-info.py "$HOME/build/ax1800pro-25" artifacts/25.12 --release 25.12
python3 scripts/collect-release.py \
  "$HOME/build/ax1800pro-25/bin/targets/qualcommax/ipq60xx" \
  artifacts/25.12/release --release 25.12 \
  --build-info artifacts/25.12/build-info.json
```

`Check project` 工作流执行静态检查及两版真实源码准备、补丁应用、feeds 安装、`make defconfig` 和 fstools 解包打补丁检查；完整固件由 `Build AX1800 Pro` 编译。自动检查不能替代真机启动、网口、双频无线、Overlay 持久化和升级回归验证。
