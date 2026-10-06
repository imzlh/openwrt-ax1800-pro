# 京东亚瑟 AX1800 Pro · OpenWrt Actions

使用 **官方 OpenWrt 24.10.8 源码和官方软件源**，为京东亚瑟 AX1800 Pro（JDCloud RE-SS-01）补充设备支持，通过 GitHub Actions 编译。界面为 **简体中文 + Argon 固定暗色**，只保留基础路由管理功能。

官方版本尚未收录此设备，所以这是“官方源码 + 社区板级适配”的自编译固件，不是 OpenWrt 官方发布的机型镜像。适配来源和改动范围见 [硬件说明](docs/hardware.md)。

| 项目 | 配置 |
| --- | --- |
| 设备 | 京东亚瑟 AX1800 Pro / RE-SS-01；不是京东鲁班 |
| 编译目标 | `qualcommax/ipq60xx`，`jdcloud_re-ss-01` |
| 基础系统 | OpenWrt 24.10.8，Linux 6.6 系列，官方 feeds |
| 持久化 Overlay | eMMC 上的 ext4；首次启动由 `fstools` 创建，不使用 RAM 作为正常 Overlay |
| 界面 | 中文 LuCI、Argon dark、HTTP / HTTPS |
| 默认网络 | LAN `192.168.10.1/24`；双频 Wi-Fi 开启，SSID 均为 `OpenWrt`，开放无密码 |
| 保留功能 | WAN / LAN、Wi-Fi、DHCP、DNS、防火墙、IPv6、PPPoE |
| 扩展插件 | 默认只有基础防火墙界面；不附加代理、广告过滤、Docker、DDNS、UPnP |
| 转发加速 | 不引入 NSS offload 固件、ECM 或相关加速补丁 |
| 版本管理 | 核心、feeds、主题均固定到具体提交，不跟随浮动分支 |

官方默认的 **`kmod-qca-nss-dp` 必须保留**：它是这个平台的网口驱动，名称含 NSS，但不等于 NSS 转发加速。无线沿用官方 ath11k 和板级数据。性能以官方驱动和 CPU 的实际能力为准。

## 使用 GitHub Actions

1. 在 GitHub 新建一个空仓库，把本项目完整推送上去，务必包含 `.github` 目录。例如在项目目录执行：

   ```sh
   git add .
   git commit -m "Add AX1800 Pro OpenWrt builder"
   git remote add origin https://github.com/imzlh/openwrt-ax1800-pro.git
   git push -u origin main
   ```

2. 推送到 `main` 且修改构建相关文件时会自动编译；也可进入仓库 **Actions → Build AX1800 Pro → Run workflow** 手动编译。并发数默认 `auto`，自动使用 runner 全部 CPU；出现构建机内存不足时可手动改为 `2`。
3. 等待构建完成，在该次运行底部的 **Artifacts** 下载：
   - `openwrt-24.10.8-jdcloud-ax1800pro-运行编号`：设备镜像、校验和、软件包清单及同次编译的目标软件包。
   - `build-info-运行编号`：完整配置、来源提交、补丁摘要、编译日志。失败时也会尝试上传。

构建和镜像校验全部成功后，`main` 分支的运行会自动创建带唯一构建编号的 **预发布 Release**，附带 sysupgrade 镜像、SHA256、设备 manifest、profiles.json 和来源信息；同次编译的软件包与完整日志继续保存在 Actions Artifacts。失败构建不会发布。首次编译通常需要较长时间，具体受 GitHub runner 和上游下载速度影响；任务上限为 6 小时。

工作流按速度优先配置：并行下载和编译，复用源码下载缓存及 ccache 编译缓存；磁盘空间足够时跳过 SDK 清理，首轮编译减少控制台输出，失败后以单线程详细日志重试。失败构建也会尝试保存缓存，便于下次继续复用。首次构建仍需生成工具链，缓存提速主要体现在后续构建；没有完整构建耗时数据前不承诺固定完成时间。

下载附件后解压，升级文件为 `*jdcloud_re-ss-01*sysupgrade.bin`。`initramfs` 用于临时启动或恢复，不作为常规升级文件。附件保留 30 天，日志保留 14 天，建议自行保存需要的版本。

## 修改配置

- [config/ax1800pro.config](config/ax1800pro.config)：设备和软件包选择；修改插件主要改这里。
- [sources.lock.json](sources.lock.json)：官方版本和 Argon 提交。升级核心版本时需同时复核补丁、feeds、内核空间和编译结果。
- [patches/](patches/)：针对固定官方版本的设备支持补丁，按文件名顺序应用，失败即停止。
- [files/](files/)：默认 LAN、双频开放 Wi-Fi、中文、Argon 暗色和上海时区。
- [.github/workflows/build.yml](.github/workflows/build.yml)：手动编译流程。

构建会在 `make defconfig` 后确认设备没有被 Kconfig 丢弃，并检查驱动、主题、中文和 HTTPS 依赖。发布附件前还会验证实际镜像校验和、设备元数据与软件包清单，阻止缺少目标镜像或混入 NSS 加速包的结果。

软件包默认使用 OpenWrt 官方仓库。自编译内核与官方发行镜像的内核 ABI 不保证一致；安装额外内核模块时，优先使用同次构建附件中的目标包，或修改配置后重新编译。OpenWrt 24.10 使用 `opkg`，不要混用 25.12 的软件源或配置备份。此次跨版本切换建议使用 `sysupgrade -n`，先保存所需设置。

当前 25.12 固件若显示 `overlayfs:/tmp/root`，配置修改仍只在 RAM 中。`/dev/loop0` 可映射到 eMMC 根分区的 SquashFS 尾部，并不代表 RAM；这个设备配置之前漏掉大分区自动格式化所需的 F2FS 工具。本项目现改为 ext4 格式化策略。原因、只读排查、手工修复及新镜像验证见 [Overlay 排查与修复](docs/overlay.md)。

## 本地检查与编译

静态检查可以在 Linux / WSL 运行：

```sh
bash -n scripts/prepare.sh
shellcheck scripts/prepare.sh files/etc/uci-defaults/99-project-settings
python3 -m unittest discover -s tests -v
```

完整编译使用 Linux 的本地文件系统，依赖列表见工作流。WSL 建议将源码放在 Linux 文件系统中，而不是 `/mnt/c` 或 `/mnt/d`：

```sh
# 目标目录必须尚不存在，脚本不会清理已有源码。
bash scripts/prepare.sh "$HOME/build/ax1800pro-openwrt"
cd "$HOME/build/ax1800pro-openwrt"
make download -j8
make -j"$(nproc)" BUILD_LOG=1
```

本项目的自动检查不等于真机验证。只有完整编译成功才能得到固件；启动、网口顺序、双频无线和你这台设备的扩容布局仍需要实机确认。
