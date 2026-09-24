# My Steam Save Backup & Restore Tool

面向 Windows 的 Steam 游戏存档发现、增量备份、历史版本管理和恢复工具。

项目会读取本机 Steam 库与 [Ludusavi manifest](https://github.com/mtkennerly/ludusavi-manifest)，定位实际存在的游戏存档、配置目录和 Steam `userdata` 数据。扫描阶段只读；备份、历史清除和恢复功能会写入或删除备份数据，恢复时会向原始存档路径复制文件。

## 主要功能

- 从 Windows 注册表或默认安装路径定位 Steam。
- 读取 `libraryfolders.vdf` 和 `appmanifest_*.acf`，发现所有 Steam 库及已安装游戏。
- 根据 Ludusavi manifest 展开 Windows、Steam 库、游戏安装目录和 Steam 用户 ID 等路径变量。
- 只收集本机当前存在的文件、目录和注册表项。
- 扫描本机 `Steam/userdata`，并按 Steam 账号组织数据。
- 在常见 Windows 用户目录中按游戏名补充发现存档目录。
- 按 `local` 和各 Steam 账号独立计算 SHA-256，只备份发生变化的分区。
- 将旧备份保存为带时间戳的历史版本。
- 查看、打包、导入、清除和恢复历史版本。
- 提供扫描与备份命令行入口，以及基于 PySide6 的图形界面。

## 环境要求

- Windows
- Python 3
- Git（仅更新 Ludusavi manifest 时需要）
- PowerShell（仅运行 manifest 更新脚本时需要）

安装图形界面依赖：

```powershell
python -m pip install -r .\requirements.txt
```

主项目目前唯一声明的第三方 Python 依赖是 `PySide6>=6.7,<7`。`third_party/ludusavi-manifest` 中的 Rust 工具链属于上游数据项目，正常使用本工具不需要安装 Rust 或 Cargo。

## 快速开始

以下命令均在项目根目录执行。

### 1. 获取或更新 Ludusavi manifest

```powershell
.\scripts\update_manifest.ps1
```

脚本首次运行时会将上游仓库浅克隆到 `third_party/ludusavi-manifest`；以后使用 `git pull --ff-only` 更新。该目录是独立 Git 仓库，不会提交到本项目。

### 2. 启动图形界面

```powershell
python .\src\gui.py
```

图形界面固定使用项目根目录下的：

- `scan_result.generated.json`
- `third_party/ludusavi-manifest/data/manifest.yaml`
- `backup_result/`
- `ui_settings.json`

首次使用时点击“检测所有游戏”生成扫描报告，然后检测更改并执行备份。

## 图形界面

图形界面支持：

- 检测全部已安装 Steam 游戏。
- 检测全部或选中游戏的存档变化。
- 备份全部、选中或单个游戏。
- 按游戏名或 Steam AppID 搜索。
- 按本地数据和 Steam 账号数据筛选。
- 置顶常用游戏；状态保存在本机 `ui_settings.json`。
- 展开游戏卡片，查看并在资源管理器中打开来源路径和备份目录。
- 查看全局历史或单游戏历史。
- 将单个或多个时间点导出为 ZIP。
- 导入本工具生成的 ZIP，保存为历史版本。
- 清除历史备份。
- 将单个游戏恢复到指定时间点。

搜索会忽略大小写、空格和标点，并支持非连续字符顺序匹配。检测任务会比较当前来源与 `hashes.txt` 中保存的路径和 SHA-256；新增、删除、路径变化、内容变化或当前备份目录缺失都会标记为“有修改”。检测本身不会复制文件。

## 扫描命令行

默认扫描：

```powershell
python .\src\steam_save_scanner.py
```

默认生成 `scan_result.generated.json`。也可以指定参数：

```powershell
python .\src\steam_save_scanner.py `
  --steam-root "E:\Steam" `
  --manifest ".\third_party\ludusavi-manifest\data\manifest.yaml" `
  --output ".\scan_result.generated.json"
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--steam-root` | 自动检测 | Steam 安装根目录 |
| `--manifest` | `third_party/ludusavi-manifest/data/manifest.yaml` | Ludusavi manifest 路径 |
| `--output` | `scan_result.generated.json` | 扫描报告输出路径 |

扫描报告中的每个应用主要包含：

- `local.directories`：实际存在的本地目录。
- `local.files`：不适合按整个目录备份的独立文件。
- `local.heuristic_directories`：按游戏名在常见用户目录中补充匹配的目录。
- `local.registry_keys`：manifest 声明且本机存在的注册表项。
- `steam_userdata.app_directories`：对应账号和 AppID 的完整 userdata 目录。
- `steam_userdata.remote_directories`：实际存在的 `remote` 目录。
- `steam_userdata.manifest_directories`：manifest 规则命中的 userdata 子目录。
- `missing_local`、`missing_steam_userdata`：没有找到对应数据的应用。

程序不判断文件是存档、配置还是缓存。只要 manifest 声明的路径存在，或对应 `Steam/userdata/<账号>/<AppID>` 存在，就可能被纳入结果。启发式扫描也可能漏报或命中同名的无关目录，建议在首次备份前检查来源路径。

## 备份命令行

先生成扫描报告，再执行：

```powershell
python .\src\backup_manager.py
```

也可以指定输入和输出：

```powershell
python .\src\backup_manager.py `
  --report ".\scan_result.generated.json" `
  --output ".\backup_result"
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--report` | `scan_result.generated.json` | 扫描报告路径 |
| `--output` | `backup_result` | 备份根目录 |

没有任何现存来源的游戏会被跳过。一个游戏备份失败不会阻止其他游戏继续处理，但命令最终会返回非零退出码。

## 备份结构与增量策略

```text
backup_result/
  <AppID>_<游戏名>/
    local/
      001_<原名称>/
      002_<原名称>/
    <Steam账号ID>/
      ...
    local-2026-09-25-12-30-00/
      ...
    <Steam账号ID>-2026-09-25-12-30-00/
      ...
    hashes.txt
    history.json
```

备份以分区为单位独立判断变化：

- `local`：普通本地文件和目录。
- `remote:<账号ID>`：对应 Steam 账号的 userdata。

发生变化的分区会先复制到临时目录并重新核对 SHA-256。校验成功后，旧的当前目录会改名为带时间戳的历史目录，新副本再成为当前目录。未变化的分区不会重复复制；因此本地数据变化不会同时复制未变化的账号数据。

`hashes.txt` 记录当前来源的类型、SHA-256、文件数、大小、原始绝对路径、备份内路径和备份时间。`history.json` 保存历史快照到原始路径的映射，用于恢复。

注册表项目前只会被扫描并记录，不会导出注册表内容，也不会在恢复时写回注册表。

## 历史版本、导出与导入

历史窗口提供两种导出模式：

- **仅打包这个时间点更新的内容**：只导出该时间点发生变化的分区。
- **打包这个时间点状态的全部存档**：为每个游戏及分区选择该时间点或之前最后一份记录，重建当时的完整状态。

多时间点批量导出会以时间点作为 ZIP 顶层目录。每个 ZIP 都包含 `steam-saves-backup.json`，记录导出模式、时间点、快照和原始路径映射。

从 ZIP 导入只接受本工具生成的包。导入内容会保存为历史版本，不会直接覆盖当前备份，也不会立即写回游戏存档。

## 恢复行为

恢复会选取指定时间点或之前每个分区的最后一份快照，并复制回元数据记录的原始绝对路径：

- 同名文件会覆盖。
- 原始目录中备份之外的额外文件不会删除。
- 注册表不会恢复。
- 恢复不是镜像同步，恢复后的目录不一定与快照完全一致。
- 跨电脑导入时，用户名、盘符或 Steam 安装位置可能不同；当前没有路径重映射界面，原始绝对路径失效时相关分区会无法恢复。

建议恢复前退出对应游戏，必要时先另行复制当前存档。

## 生成文件

以下内容是本机生成数据，已加入 `.gitignore`：

- `scan_result.generated.json`：扫描结果，可能包含用户名、Steam 账号 ID 和绝对路径。
- `ui_settings.json`：GUI 置顶状态。
- `backup_result/`：当前备份、历史版本和导出文件。
- `third_party/ludusavi-manifest/`：独立克隆的上游仓库。

## 已知限制

- 当前仅支持 Windows。
- 不连接 Steam Cloud API，只能发现已经同步或下载到本机的数据。
- manifest 使用面向当前需求的轻量解析器，不支持 Ludusavi YAML 规范中的所有结构和路径变量。
- 启发式目录扫描依赖名称匹配，可能漏报或误匹配。
- 注册表项只检测、不备份、不恢复。
- 恢复不会删除目标目录中的额外文件。
- 历史元数据保存绝对来源路径，跨电脑恢复可能因路径不同而失败。
- 扫描与备份 CLI 的默认路径相对于当前工作目录；从其他目录运行时应显式传入路径参数。

## 测试

安装依赖后，在项目根目录执行：

```powershell
python -m unittest discover -s tests -v
```

测试覆盖扫描器解析、备份变化检测与历史归档、ZIP 导入导出、恢复/清除行为，以及主要 GUI 交互。GUI 测试使用 Qt offscreen 平台，可在无显示器环境运行。

## 第三方数据

本项目使用 [Ludusavi manifest](https://github.com/mtkennerly/ludusavi-manifest) 提供的游戏存档规则。其仓库会独立克隆到 `third_party/ludusavi-manifest`，许可证和上游说明以该仓库内容为准。
