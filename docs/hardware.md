# 京东云亚瑟 AX1800 Pro 设备支持说明

本项目的设备是 **JDCloud RE-SS-01（亚瑟 AX1800 Pro）**，目标为 `qualcommax/ipq60xx`，profile 为 `jdcloud_re-ss-01`。不要用于雅典娜 RE-CS-02、百里 RE-CS-07、京东云版 Redmi AX5 或名称相近的其他设备。

## 官方源码与移植范围

基础源码同时支持 [OpenWrt v24.10.8](https://github.com/openwrt/openwrt/tree/0b795ce79e23b553aa184080c390f9ce92a2b6d4)（Linux 6.6）和 [OpenWrt v25.12.5](https://github.com/openwrt/openwrt/tree/f0a60eee2fe051741c643ea6118718aae1ef17fb)（Linux 6.12）。两版都有 IPQ60xx 平台支持，但没有 RE-SS-01 profile。版本和完整提交见 [统一锁文件](../sources.lock.json)。

[共用设备树](../patches/common/) 与 [24.10](../patches/24.10/) / [25.12](../patches/25.12/) 版本补丁增加以下设备支持：

| 项目 | 实现 |
| --- | --- |
| 设备树 | IPQ6000、512 MiB 内存布局、eMMC、QCA8075 网口、LED、按键及无线校准标识 |
| 网口 | `lan1 lan2 lan3` 与 `wan`，对应 `dp2` 至 `dp5` |
| FIT 内核配置 | `config@cp03-c2`，内核上限 6144 KiB |
| 无线 board 数据 | 注册官方 `ipq-wifi-jdcloud_re-ss-01` 包 |
| 每台机器的无线校准 | 从 eMMC 分区 `0:ART` 的 `0x1000` 偏移读取 `0x10000` 字节 |
| 升级 | 使用官方 `emmc_do_upgrade`，按分区标签寻找 `0:HLOS` 与 `rootfs` |
| Factory | FIT 内核补齐到 6144 KiB，再追加 SquashFS 和设备元数据 |

硬件接线、GPIO、FIT 配置和分区标签参考 [LiBwrt/LibWrt 的设备树](https://github.com/LiBwrt/LibWrt/blob/3a3d0b08595530070ad9cd8a20c0ae4ea3e77f70/target/linux/qualcommax/files/arch/arm64/boot/dts/qcom/ipq6000-re-ss-01.dts)、[镜像定义](https://github.com/LiBwrt/LibWrt/blob/3a3d0b08595530070ad9cd8a20c0ae4ea3e77f70/target/linux/qualcommax/image/ipq60xx.mk) 和同一提交下的 `ipq60xx/base-files`。固定参考提交为 `3a3d0b08595530070ad9cd8a20c0ae4ea3e77f70`，设备树保留原有 SPDX 许可声明。

移植时去掉了民间设备树的 `ipq6018-nss.dtsi` 引用，以及 CPU speed-bin/OPP 覆写，使用官方平台默认值。没有复制民间发行版的内核补丁集、软件源、管理插件或初始化脚本。

两版沿用官方 MMC、SDHCI、QCA8075、MDIO、交换机与以太网驱动。Linux 6.6 的设备树使用 `sdhc_1`，6.12 使用 `sdhc`；版本补丁处理这项差异。两版 `fstools` 的格式化实现也不同，各自携带匹配源码的 ext4 Overlay 补丁，保留对既有文件系统的识别。

## 无线数据来源

固件使用官方 ath11k 驱动和 IPQ6018 无线固件。DTS 校准标识为 `JDC-RE-SS-01`。

OpenWrt 自带的 `ipq-wifi` 软件包固定了 [qca-wireless](https://git.openwrt.org/project/firmware/qca-wireless.git) 源码：

| 版本 | qca-wireless 提交 |
| --- | --- |
| 24.10.8 | `ec6831a43ff066c9873e7c41fd6f68354bb67b4a` |
| 25.12.5 | `e20f4c6ff197823762319e4b7e31af01816503cf` |

两者均包含相同的 `board-jdcloud_re-ss-01.ipq6018`（65640 字节），其 SHA-256 为：

```text
64cedd60a69ff8bf4d8566d2d9ced132b343f7ed18ed82d9959427db533f6b6a
```

补丁只把这个已有文件注册成设备包，不额外下载来源不明的 BDF。该通用 board 数据与路由器 `0:ART` 中每台设备独有的校准数据是不同的文件，两者都需要。

## 不使用 NSS 加速

官方 `qualcommax` 平台的 `kmod-qca-nss-dp` 名称包含 NSS，但它是该平台网口所需的数据通路驱动。本项目保留它和官方交换机驱动。

本项目不引入 `qca-nss-drv`、NSS ECM、NSS 客户端或 ath11k NSS offload，也没有启用 NSS 固件节点。不要为了删除包名中的 `nss` 而移除官方网口驱动。

## 已扩容、运行 LibWrt等 的设备

两版都生成 `sysupgrade.bin` 和 `factory.bin`。常规升级使用 sysupgrade；factory 沿用上面固定 LibWrt 参考提交的设备专用配方，是供匹配布局的 U-Boot／恢复工具使用的原始内核与根文件系统组合。**没有验证京东原厂网页接受此格式。** factory 不包含 GPT、引导程序和 ART，不执行扩容或重分区，也不能当作整个 eMMC 的磁盘镜像。

factory 的 FIT 从偏移 0 开始，SquashFS 从偏移 `0x600000`（6 MiB）开始。使用前必须核对恢复工具对内核与 rootfs 的拆分、写入和启动槽位处理，不能将整个文件写进 `0:HLOS`。本项目的 `platform_check_image` 仅接受包含 CONTROL、kernel 和 root 的 sysupgrade tar，防止 factory 被误用作常规升级。

扩容后的布局仍须保留 RE-SS-01 对应的分区标签：

- `0:HLOS`：保存 FIT 内核，至少 6144 KiB。
- `rootfs`：保存 SquashFS 根文件系统，实际容量必须大于本次镜像中的 root payload。
- `0:ART`：保存本机校准数据；升级不会写入该分区。

升级函数按标签查找分区，不写死 `/dev/mmcblk0p20` 一类分区编号，也不重建 GPT。设备树沿用现有 U-Boot 的启动参数和 MAC 地址注入方式；未更改 `bootargs`、启动槽位或 U-Boot 环境。`ethernet1` 至 `ethernet4` 别名与参考设备树一致。参考源码的 `02_network` 没有 RE-SS-01 的另一个 MAC 提取分支。

从 LibWrt 迁移或在 24.10 / 25.12 之间切换时应不保留旧配置，以便重新生成网络、无线和 LuCI 配置；尤其不要直接迁移旧系统的挂载配置。升级时仍写入 `0:HLOS` 与 `rootfs`，正常首次启动只会在被选中的 Overlay 区域创建 ext4；不会自动格式化其他 eMMC 分区、搬迁 overlay 或扩容。若 `/proc/cmdline` 让 `fstools` 选中独立 `rootfs_data` 分区，则该分区可能被初始化，刷入前应先核对并备份。详细判断见 [Overlay 排查与修复](overlay.md)。

## 验证边界

两版设备补丁均对锁定官方源码执行过 `git apply --check`，真实 feeds 安装、`make defconfig` 和 `make package/system/fstools/prepare` 通过。设备树对两版对应的 Linux 源码及补丁完成过预处理和 dtc 编译。这些检查未完成整机固件编译，也不能替代实机验证；目前没有本项目镜像的真实 RE-SS-01 启动、无线、全部网口及升级回归结果。
