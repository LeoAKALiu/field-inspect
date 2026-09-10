# 来源与授权状态

基于用户授权的私有源码快照建立新历史：车端 efb03c016ccf85d1f52e3541acac141070b01654；平台 eaf25fd36f2354a37ab6da6281a46fd04f956dd0。旧 Git 历史与 bundle 不公开。

保留包中 UNLICENSED 与 ROS 包已有 Apache-2.0 声明；尚未为整个产品指定统一许可证。公开可见不表示可以无条件再许可或商用。第三方依赖依各自许可证使用。

LIRIS 样例保留 assets/tunnel/liris/SOURCES.md 的哈希、来源和 ETALAB 2.0 署名，发布前还须核对上游。厂商 SDK/运行库未打包。

内部 ADR、客户数据库、标定报告、旧验收截图及报名/专利材料在私有来源保留，不批量复制到产品文档。

2026-09-10 核对：LIRIS 官方数据页 https://dataset-dl.liris.cnrs.fr/synthetic-cave-and-tunnel-systems/ 明示 ETALAB v2.0；OBJ/PLY 字节与 SOURCES.md 两项摘要一致。

两份补丁对应许可证已按构建脚本锁定版本保存：licenses/Livox-SDK2.txt（08f523c930b2f0ba1e98a6afaa8d7476bf479908）和 licenses/MCAP.txt（releases/cpp/v0.8.0）。保留各自版权声明；这些许可不扩展到整个产品。vendor 源码和硬件运行库不随快照发布。


服务端新增 opencv-python-headless 4.11.0.86（仅作为锁定安装依赖，不把二进制 wheel 纳入源码仓库）。其已安装发行包的 LICENSE.txt 与 LICENSE-3RD-PARTY.txt 原文保存于 licenses/opencv-python-headless-4.11.0.86-*；相关第三方组件须遵守对应声明。NumPy 保留依赖发行包自身许可证。以上不改变产品整体 UNLICENSED 状态。
