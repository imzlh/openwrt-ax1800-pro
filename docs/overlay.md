# RE-SS-01 Overlay 排查与修复

## 现象和原因

`overlayfs:/tmp/root` 表示**当前修改只在 RAM 中**，重启会丢失。`df` 显示的 tmpfs 204 MiB 是容量上限，不等于已占用 204 MiB；实际内存看 `free -h` 或 `/proc/meminfo`。

`/dev/loop0` 是 `fstools` 的 `rootdisk` 驱动创建的块设备。它通常把当前 eMMC 根分区上 SquashFS 镜像尾部的空间映射为可写区：读取 SquashFS 超级块的 `bytes_used`，向上按 64 KiB 对齐，再把该偏移之后的空间挂到 loop。**loop 并不等于 RAM**，其后端可以是 `/dev/mmcblk0p18` 一类 eMMC 分区。`ls -l /dev/loop*` 只说明设备节点存在；用 `losetup -a` 才能看当前映射，且启动失败后的自动清理可能让它为空。

`/rom/overlay` 是 SquashFS 内预留的挂载目录，不是 Overlay 镜像文件。用 `stat -c '%F' /rom/overlay` 验证应为 `directory`；不要尝试对它执行 `losetup`。

日志 `overlay filesystem in /dev/loop0 has not been formatted yet` 只是说 `fstools` 没识别出 ext4/F2FS 文件系统，随后会尝试格式化。原固件的 `rootfs` 分区约 2 GiB，大于上游 `fstools` 的 100 MiB 阈值，因此默认调用 `mkfs.f2fs`；原设备包没有选择 `f2fs-tools` 和 `kmod-fs-f2fs`，格式化无法完成，`mount_root` 才退回 tmpfs。仅有 `fstools` 包不保证相应的 `mkfs` 工具和内核模块也存在。最终原因还应结合设备上的完整启动日志与 `command -v mkfs.f2fs` 确认。

独立的 `rootfs_data`（例如 `mmcblk0p22`）是否被优先采用，要看内核命令行与 `/sys/class/block/*/uevent` 中的 `PARTNAME`。在相关 `fstools` 版本里，`root=/dev/mmcblk0p18` 可限定同盘扫描；`root=PARTUUID=...` 等形式若没有 `fstools_partname_fallback_scan=1`，则可能跳过分区名扫描并走上述 loop 路径。**仅凭 `lsblk` 有 `rootfs_data` 不能断言它已经被选中。** 不要直接格式化 p22 来猜测。

这里的内部 Overlay 在 preinit 阶段由 `mount_root` / `fstools` 处理，不依赖 `block-mount`、`/etc/config/fstab` 或自定义首次启动脚本。`block-mount` 主要用于额外分区挂载或 extroot，不能修复当前自动格式化缺少工具的问题。

## 先只读排查

在当前 25.12 固件上执行：

```sh
cat /proc/cmdline
lsblk -o NAME,SIZE,MAJ:MIN,PARTLABEL,FSTYPE,MOUNTPOINTS
cat /sys/class/block/mmcblk0p22/uevent
cat /proc/mounts | grep -E ' /rom | /overlay | /tmp/root | / '
stat -c '%F' /rom/overlay
losetup -a
command -v mkfs.ext4
command -v mkfs.f2fs
apk info | grep -E '^(fstools|e2fsprogs|f2fs-tools|kmod-fs-ext4|kmod-fs-f2fs)-'
dmesg | grep -Ei 'mount_root|overlay|mkfs|f2fs|ext4|loop'
free -h
```

若 `root=PARTUUID=...` 且没有 `fstools_partname_fallback_scan=1`，独立的 p22 多半未被 `fstools` 选中。若 `root=/dev/mmcblk0p18` 仍走 loop，继续核对 p22 `uevent` 的 `PARTNAME` 是否确为 `rootfs_data`，以及是否与启动根分区在同一盘。`/dev/loop0` 日志本身不能证明后端是哪一个分区；还须结合 `losetup -a`、根设备与分区信息。

## 当前固件手工改为持久化 ext4

下面只适用于**已确认当前 `/rom` 来自 `/dev/mmcblk0p18`，且决定使用该分区 SquashFS 尾部空间**的情况。操作前把需要的设置备份到电脑；格式化会清除该尾部已有数据。若设备实际从 `rootfs_1` 或其他槽启动，不要照抄 p18。不要对整个 `/dev/mmcblk0p18` 运行 `mkfs.ext4`，那会抹掉可启动的 SquashFS。

先确认 `mkfs.ext4` 和 `losetup` 存在，再核对 `/rom` 的设备号和 SquashFS 魔数。以下命令中的 `rootdev` 必须由你根据 `cat /proc/cmdline`、`lsblk` 和实际挂载核定：

```sh
rootdev=/dev/mmcblk0p18
command -v mkfs.ext4
command -v losetup
stat -c '%d' /rom
stat -c '%r' "$rootdev"
hexdump -v -n 4 -e '4/1 "%02x"' "$rootdev"; echo
```

前两个 `stat` 数字应一致；魔数应为 `68737173`（`hsqs`）。任一项不符就停止。接着只创建**带偏移**的 loop 映射：

```sh
used=$(hexdump -v -s 40 -n 8 -e '1/8 "%u"' "$rootdev")
offset=$(( (used + 65535) / 65536 * 65536 ))
size=$(( $(cat /sys/class/block/$(basename "$rootdev")/size) * 512 ))
printf 'SquashFS bytes_used=%s, loop offset=%s, partition size=%s\n' "$used" "$offset" "$size"
test "$used" -gt 0 && test "$offset" -gt 0 && test "$offset" -lt "$size" || exit 1
loopdev=$(losetup -f --show -o "$offset" "$rootdev") || exit 1
losetup -a
```

确认 `losetup -a` 的后端确为刚核定的 `rootdev`、偏移确为 `offset`，且 loop 没有挂载重要数据，才执行下面的写入操作：

```sh
mkfs.ext4 -F -L rootfs_data "$loopdev"
sync
losetup -d "$loopdev"
reboot
```

重启后 `mount_root` 会识别已存在的 ext4，不再按容量选择 F2FS。若 `losetup -f --show` 不被当前版本支持、找不到空闲 loop，或上面的核对不通过，停止，不要改为直接格式化根分区。由于当前 `/tmp/root` 中的设置是 RAM 数据，若未另行保存，重启后会丢失。

## 新编译镜像与三种模式

本项目现在锁定 OpenWrt 24.10.8 / Linux 6.6，并给固定版本的 `fstools` 增加 ext4 初始化补丁。设备仍可依启动参数在 **独立 `rootfs_data` 分区** 和 **SquashFS 尾部 loop** 两种 eMMC 持久化路径之间选择；补丁只改变首次格式化时的文件系统类型，不会擅自改写分区选择。升级不保留配置时，旧 Overlay 数据可能被清除，请先备份。它也不能保证 6.6 的实际内存占用一定比 6.12 低；应在相同服务配置下比较 `free -h`。

刷入后按来源区分：

| 模式 | `mount` / `df` 特征 | 是否持久化 |
| --- | --- | --- |
| Loop Overlay | `/dev/loopN` 以 ext4 挂到 `/overlay`；`losetup -a` 显示后端为 eMMC 根分区及偏移 | 是 |
| Rootfs_data | `/dev/mmcblk0p22` 等以 ext4 挂到 `/overlay`，`PARTNAME=rootfs_data` | 是 |
| Live RAM Overlay | `/tmp/root` 为 tmpfs，`overlayfs:/tmp/root` 挂到 `/` | 否 |

验证命令：

```sh
cat /proc/cmdline
cat /proc/mounts | grep -E ' /rom | /overlay | /tmp/root | / '
df -h / /overlay
losetup -a
touch /etc/overlay-persistence-test
sync
reboot
# 重连后：
test -e /etc/overlay-persistence-test && echo persistent
```

若仍是 RAM 模式，先看 `dmesg` 中格式化、ext4 模块和 eMMC I/O 错误；不要反复刷写或直接格式化 p22。独立 p22 模式需要确认启动参数使 `fstools` 选中它，并先备份该分区；单装 `block-mount` 不会自动切换内部 Overlay。
