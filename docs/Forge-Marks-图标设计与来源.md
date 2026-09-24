# Forge Marks：图标设计与来源

核查日期：2026-09-22。适用范围：当前保留的 ForgeAgent 前端。

## 已落地的方向

以折角工程铭牌为基本语言：24 × 24 网格、1.65 单位描边、方形线端、局部透明印色。保留搜索、返回、播放、暂停等操作符号的常见含义，为业务对象重新设计轮廓。用户在任务密集的界面中可以先认形状，再读名称。

| 功能 | 图形设计 | 含义 |
| --- | --- | --- |
| 品牌 | 锻造台与 F 的组合 | Forge 的构建与加工语义 |
| 工作台 | 分区工作台 | 多项任务的工作入口 |
| 任务 | 两侧带切口的工单 | 可追踪的执行单元 |
| 待我处理 | 向下进入托盘的箭头 | 等待接收和处理 |
| 交付物 | 封装立方体 | 已组织的交付包 |
| 项目 | 带目录线的文件夹 | 与交付包区分 |
| 知识与能力 | 双页索引册 | 可查阅、可复用的知识 |
| 运行分析 | 坐标与采样折线 | 执行过程的可观测数据 |
| 审批 | 折角盾牌与校验勾 | 授权和检查 |
| 检查点/分支 | 方形节点与分支连接 | 状态沿执行路径推进 |
| 验证结果 | 八边形通过/失败标记 | 不仅依赖颜色区分结果 |

主导航使用 22 px；正文操作保留既有 12–24 px 尺寸。局部印色默认透明度 0.16，导航选中时提高至 0.30。图标继承所在控件的文字颜色，避免在危险、成功或禁用操作中出现互相冲突的装饰色。

图标默认 `aria-hidden`、不可聚焦；含义由按钮文本、已有 `aria-label` 和状态文字提供。没有添加图标独立标签造成重复朗读。主要业务图形为本次项目绘制；“原创”不表示搜索、箭头等通用符号具有独占性。

## 网上资源与选型

| 资源 | 可核查依据 | 本次处理 |
| --- | --- | --- |
| [Iconoir](https://github.com/iconoir-icons/iconoir) | 24 px SVG；[固定版本 MIT 原文](https://raw.githubusercontent.com/iconoir-icons/iconoir/d7dfa4d0341df0670bfed9fc24221c9d7ef2112e/LICENSE)允许使用和修改，须保留许可 | 用作 5 个基础操作图标的来源，统一描边与几何风格 |
| [Pixelarticons](https://github.com/halfmage/pixelarticons) | 仓库 README 将免费集标为 MIT，并说明严格像素网格；Pro 另行授权 | 比较了像素方向，未纳入源文件；纯像素风与现有密集工作空间的视觉差异较大 |
| [Pepicons](https://github.com/CyCraft/pepicons) | README 提供 Print / Pop / Pencil 风格，存在 CC BY 4.0 与商业购买提示并列的情况 | 只作为风格参考，未复用资产；本次没有把许可歧义带入项目 |

比较依据是实际 README、源 SVG 和许可证，不以搜索摘要推断可商用。Iconoir 原始 SVG 的曲线较柔和，本次只选基础操作做适配；业务图标没有直接换成另一套默认图库。

## 复用清单

上游固定提交：`d7dfa4d0341df0670bfed9fc24221c9d7ef2112e`。
版权：Copyright (c) 2021 Luca Burgio。

| 本地图标 | 上游路径（相对仓库根目录） | 修改 |
| --- | --- | --- |
| ArrowRight | `icons/regular/arrow-right.svg` | 等价压缩路径指令；方形线端、折角连接；描边 1.5 → 1.65 |
| ArrowLeft | `icons/regular/arrow-right.svg` | 将右箭头水平镜像，统一描边样式 |
| ArrowUpRight | `icons/regular/arrow-up-right.svg` | 保留路径，统一描边样式 |
| X | `icons/regular/xmark.svg` | 保留路径，统一描边样式 |
| Search | `icons/regular/search.svg` | 保留把手，圆形镜框改为八边形，添加角部刻线与淡色面 |

原文链接格式：`https://raw.githubusercontent.com/iconoir-icons/iconoir/<提交>/<路径>`。
许可完整保存在 `public/licenses/Iconoir-MIT.txt`；上述派生 SVG 的下载文件也内嵌完整许可注释。

`Github` 是历史组件名，当前绘制为通用 git 仓库连接符号，不是修改过的 GitHub 官方商标；页面仍通过文字说明实际连接服务。`Clock` 是 `Clock3` 的兼容别名。

## 编辑与预览

- 源文件：`src/icons.tsx`，所有业务界面统一从此导入，已移除 `lucide-react`。
- 样张地址：`/icon-study.html`，可切换 12/16/20/24/32 px、单色、选中和深色预览，并下载单个 SVG。
- 独立资源：`public/icons/`，60 个导出文件，其中 `Clock` 与 `Clock3` 共用一个图形。
- 生成命令：`npm run icons:export`；`npm run build` 自动重新生成，避免样张与实际组件不一致。
- 深色开关仅属于样张，不代表工作空间新增了主题设置。

任务状态流转、审批策略、存储结构和后端模拟行为没有随图标调整改变。图表数据的 SVG 继续按原有数据绘制，避免为了装饰而改变数据表达。
