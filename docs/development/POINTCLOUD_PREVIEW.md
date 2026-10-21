# 实录点云预览

任务回放通过受保护的 `GET /api/tasks/{task_id}/pointcloud.ply` 读取本运行的点云。接口只读服务器本地归档，不接收文件路径，不写入或替换原始证据，也不改变验收状态。

服务端将导入账本中的 v2 manifest 与运行、场景版本和对齐 ID 绑定，只使用 `kind=pointcloud` 且 `display=true` 的资产。读取期间持有既有导入锁，拒绝符号链接与特殊文件，逐个核对实际读取文件的 inventory SHA-256。此检查针对本次预览来源，不是重新验收完整 MCAP 或现场几何精度。

支持两种既有产物：

- 单份 PLY：核对摘要、完整头部及全部记录后原样返回；最多 32 MiB、50 万顶点。接受 ASCII、binary little-endian 和 binary big-endian，保留标量附加属性及列表属性。必须有标量 x/y/z，实际数据须与声明的元素/属性数量完全一致；截断、额外数据、无效数值、非有限坐标或绝对值超过 10000 米的坐标拒绝预览，不以头部点数冒充实际点数。
- 车端 `inspection_pointcloud_export` 的 PCD 分片：读取 `artifacts/pointcloud/manifest.json`，要求分片顺序连续、全部展示资产与分片索引一致、摘要和点数一致，坐标为 `scene_local_yup`、对齐声明为经现场核对。只接受 PCL 导出的 binary FLOAT32 XYZI 子集，拒绝非有限数和绝对值超过 10000 米的坐标。单片最多 32 MiB，总来源最多 512 MiB，最多 1000 片。

PCD 按全部分片的点序列每隔 `ceil(总点数/500000)` 点保留一处，输出最多 50 万个 XYZ 点的 binary little-endian PLY。抽稀跨分片连续，原坐标不再变换，不补点、不生成颜色。输出是整次运行的静态叠加预览，不是随回放时间逐帧展开的建图，也不替代完整原始点云。PLY 注释与 `X-Pointcloud-*` 响应头保留来源点数、展示点数和抽样步长；页面显示来源份数和点数。

沿用已锁定的 NumPy（OpenCV 依赖）解析 PCD，不增加依赖或修改锁文件。目标 Python 缺少该依赖时返回 503；使用项目冻结依赖环境后才能读取 PCD。导入锁占用返回 409；不支持的格式、缺失文件、摘要不符或超限返回 422，页面可重试，不以其他场景点云代替。

422 沿用统一 `error.code/message` 响应；页面按原因码显示中文说明和服务端提供的安全原因（例如 512 MiB 来源上限），不返回私有文件路径或原始解析异常。原因码如下：

| code | 原因 |
| --- | --- |
| pointcloud_not_found | 没有该实录运行或可展示点云 |
| pointcloud_binding_mismatch | 运行、场景对齐或分片索引不一致 |
| pointcloud_unsafe_path | 路径、链接或文件类型不安全 |
| pointcloud_size_limit | 超出来源大小、点数或分片数量上限 |
| pointcloud_digest_mismatch | 实际文件摘要与 inventory 不一致 |
| pointcloud_invalid_format | 格式、元数据、标量或记录数量无效/不完整 |
| pointcloud_invalid_coordinates | 坐标非有限或越界 |
| pointcloud_count_mismatch | PCD 实际点数与分片索引不一致 |
| pointcloud_file_missing | 来源文件缺失 |
| pointcloud_file_unavailable | 来源文件无法读取 |

PLY 校验使用 Python 标准库；原文件、坐标及合法文件响应字节均不改写。新增三种编码、截断/额外数据/坐标异常、API 原因码及前端提示的回归用例保留，按本阶段约束未执行。

前端请求在运行切换/卸载时取消，过期结果不进入新运行，Blob URL 随后释放。三维引擎将网格和点云坐标变换分开：只对明确的 LIRIS 原始资产 URL 应用其打包矩阵；本接口已在场景坐标中的点云使用单位矩阵。引擎接收页面传入的场景元数据，并在已销毁场景的异步加载完成后释放资源。

响应为 `application/octet-stream`，使用 `Cache-Control: private, no-store` 和 `X-Content-Type-Options: nosniff`。访问控制沿用 API 身份与 HttpOnly 会话；归档绝对路径不返回浏览器。

回归用例涵盖分片合并、跨片抽样、篡改/符号链接/路径/大小/对齐拒绝、原 PLY 保留、导入锁、访问保护、运行切换与坐标变换。依本阶段要求，用例保留但未执行；编译不代表功能或实车验证通过。
