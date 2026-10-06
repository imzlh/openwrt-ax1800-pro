# 精简配置与默认界面

基础源码使用官方 OpenWrt 24.10.8（Linux 6.6）或 25.12.5（Linux 6.12），保留设备 profile 默认选择的网卡、无线、闪存等驱动，包括官方 `kmod-qca-nss-dp` 以太网驱动；虽然名字带有 NSS，这个包负责网口工作。项目不引入额外的 NSS 加速栈、ECM 或相关加速补丁。设备移植方式见项目 README。

## 包选择

[`config/ax1800pro.config`](../config/ax1800pro.config) 增加以下组件：

- LuCI 基础管理界面：状态、系统、网络、无线和防火墙；保留 IPv6、PPPoE 的网页配置。
- `uhttpd`、`uhttpd-mod-ubus`，以及 `libustream-mbedtls`、`px5g-mbedtls`，提供 HTTP / HTTPS 管理。首次访问 HTTPS 使用路由器生成的自签名证书。
- Argon 主题和简体中文翻译。

没有选择 DDNS、UPnP、广告过滤、代理、Docker 等扩展，也没有安装单独的 Argon 设置插件或网页软件包管理器。基础路由功能和上游默认的软件包管理命令仍然保留。

这里直接选择 LuCI 所需模块，没有使用 `luci`、`luci-light` 或 `luci-ssl` 集合：这些集合会带入 Bootstrap 主题，部分还会带入网页软件包管理器。HTTPS 的依赖与官方 `luci-ssl` 相同。

调整两版通用的软件包时修改 `config/ax1800pro.config`，版本差异放在 `config/24.10.config` / `config/25.12.config`；构建中的 `make defconfig` 会自动补齐依赖。不要删除设备 profile 自带的驱动及 ext4 初始化所需的 `e2fsprogs`、`kmod-fs-ext4`。添加插件前分别确认它支持两个版本的 OpenWrt / LuCI，且会增加相应依赖。

## Argon 与暗色设置

官方 LuCI feed 不包含 Argon。本项目只额外接入 [jerrykuku/luci-theme-argon](https://github.com/jerrykuku/luci-theme-argon)，固定提交为 [`23c3e525578374d6b20f5e7b93d27874cd01a252`](https://github.com/jerrykuku/luci-theme-argon/tree/23c3e525578374d6b20f5e7b93d27874cd01a252)（2.4.7）。该提交的依赖声明同时覆盖 `opkg` 与 APK；24.10 使用 `opkg`，25.12 使用 `apk`。

[`files/etc/config/argon`](../files/etc/config/argon) 设置 `global` 段的 `mode='dark'`。主题直接读取这个文件，所以无需安装 `luci-app-argon-config`。主题自身依赖的 `wget-any`、`jsonfilter` 由编译系统补齐。

需要跟随浏览器的明暗偏好时，在路由器 SSH 中执行：

```sh
uci set 'argon.@global[0].mode=normal'
uci commit argon
```

将 `normal` 换为 `dark` 或 `light` 可强制暗色或亮色，随后刷新页面。

## 首次启动与保留配置升级

[`99-project-settings`](../files/etc/uci-defaults/99-project-settings) 在首次初始化时设置：简体中文、Argon 暗色、`Asia/Shanghai` 时区（UTC+8），以及以下默认网络：

- LAN 地址 `192.168.10.1`，掩码 `255.255.255.0`，DHCP 客户端获得 `192.168.10.x` 地址。
- 2.4 GHz 和 5 GHz Wi-Fi 均开启，SSID 均为 `OpenWrt`，无密码开放网络，接入 LAN。
- WAN、DHCP 服务、防火墙及管理员初始密码状态沿用官方默认值。首次登录后设置管理员密码，按需要调整无线密码。

初始化按无线频段识别接口，不依赖 `radio0` / `radio1` 的顺序；只有双频设置完成后才记录成功标记。若无线设备尚未就绪，保留初始化脚本供后续启动重试。

脚本成功后会记录 `/etc/config/project_settings`。从本项目的旧版固件保留配置升级时，此标记与网络、无线、`/etc/config/argon`、LuCI 等配置一起保留，脚本不会重新覆盖网络、语言、主题或时区；因此修改仓库里的默认值**不会自动替换路由器已保留的配置**。可在网页 / SSH 中修改，或在准备好网络配置备份后选择不保留配置刷入。

从其他固件迁移并保留配置时，如果不存在上述标记，初始化脚本会运行一次并应用上述网络、界面和时区默认值，管理地址也会改为 `192.168.10.1`。跨固件迁移建议不保留配置，具体要求以设备安装文档为准。

## 上游核对依据

- [OpenWrt v24.10.8 发布页](https://github.com/openwrt/openwrt/releases/tag/v24.10.8) 与 [固定版本的 feeds.conf.default](https://github.com/openwrt/openwrt/blob/v24.10.8/feeds.conf.default)。
- [官方 LuCI 主题列表](https://github.com/openwrt/luci/tree/cac97ed67cfbcad90db49f5f3b1245c2c4cbfae5/themes)、[luci-light 的依赖](https://github.com/openwrt/luci/blob/cac97ed67cfbcad90db49f5f3b1245c2c4cbfae5/collections/luci-light/Makefile) 与 [luci-ssl 的依赖](https://github.com/openwrt/luci/blob/cac97ed67cfbcad90db49f5f3b1245c2c4cbfae5/collections/luci-ssl/Makefile)。
- [Argon 读取暗色模式的模板](https://github.com/jerrykuku/luci-theme-argon/blob/23c3e525578374d6b20f5e7b93d27874cd01a252/ucode/template/themes/argon/header.ut) 与 [软件包依赖](https://github.com/jerrykuku/luci-theme-argon/blob/23c3e525578374d6b20f5e7b93d27874cd01a252/Makefile)。
