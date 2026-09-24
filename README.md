# Steam Saves Backup

这个项目目前提供一个只读扫描器，用于：

1. 从 Windows 注册表定位 Steam。
2. 读取 `steamapps/libraryfolders.vdf`，发现所有 Steam 库。
3. 读取每个 `appmanifest_*.acf`，列出已安装的游戏和软件。
4. 读取 `third_party/ludusavi-manifest/data/manifest.yaml` 中的存档规则。
5. 展开 Windows、Steam 库、安装目录和 Steam 用户 ID 等变量。
6. 只记录本机当前真实存在的文件、目录和注册表项。
7. 将普通本地数据与 `Steam/userdata` 数据分开输出。
8. 在常见用户数据目录中按游戏名和安装名补充发现实际含文件的目录，用于覆盖上游清单尚未收录或路径滞后的游戏。

## 运行

在项目根目录执行：

```powershell
python .\src\steam_save_scanner.py
```

默认生成 `scan_result.generated.json`。也可以指定路径：

```powershell
python .\src\steam_save_scanner.py `
  --steam-root E:\Steam `
  --manifest .\third_party\ludusavi-manifest\data\manifest.yaml `
  --output .\scan_result.generated.json
```

输出中每个应用包含：

- `local.directories`：实际存在的本地存档或配置目录。
- `local.files`：直接位于游戏根目录等位置、不适合整目录备份的文件。
- `local.heuristic_directories`：按游戏名或安装名在常见用户数据目录中自动匹配到的目录。
- `local.registry_keys`：清单声明且本机存在的注册表项。
- `steam_userdata.app_directories`：对应账号和 AppID 的完整 Steam userdata 目录。
- `steam_userdata.remote_directories`：其中实际存在的 `remote` 目录。
- `steam_userdata.manifest_directories`：Ludusavi 规则直接命中的 userdata 子目录。
- `steam_userdata.remote_file_count`：本机 `remote` 目录内实际存在的文件数。
- `missing_local`、`missing_steam_userdata`：扫描后没有找到相应数据的应用。
- `missing_remote_directory`、`missing_remote_files`：没有 `remote` 目录或目录内没有文件的应用。

程序不判断文件究竟是存档、配置还是缓存。只要 Ludusavi 清单声明了该路径并且路径存在，就会保留；`Steam/userdata/<账号>/<AppID>` 只要存在也会保留。

Ludusavi 清单包含普通本地路径、部分 Steam `userdata` 路径和云同步标记，但它不是 Steam 云服务器的在线文件目录。本程序会额外扫描本机 `Steam/userdata`；只有已经同步或下载到当前电脑的远程文件才能被检测到。

## 图形界面

界面使用 Qt 官方 Python 绑定 PySide6。首次运行前安装依赖：

```powershell
python -m pip install -r .\requirements.txt
```

然后启动：

```powershell
python .\src\gui.py
```

“检测所有游戏”会重新扫描本机已安装的全部 Steam 游戏并更新 `scan_result.generated.json`。每个游戏默认显示为单行折叠卡片，摘要包含本地路径数、远程账号和路径数、Steam AppID、修改状态和上次备份时间；展开后可以点击来源路径和备份文件夹并在文件资源管理器中打开。备份路径以程序目录为基准计算，因此程序整体移动后会在新位置重新生成绝对显示路径。搜索会忽略大小写、空格和标点，并按字符顺序进行非连续子序列匹配；名称和 Steam ID 均适用。搜索、本地和远程三个筛选条件为“且”关系。游戏名称前的钉子可以将游戏固定在所有未固定游戏之前，固定状态记录在本机 `ui_settings.json`；置顶不会绕过筛选，不符合条件的固定游戏仍然隐藏。界面还支持全选、备份选中、备份所有以及单项备份。“所有”范围的主要操作采用蓝底白字，“选中”范围采用白底深色字。

“检测所有更改”会在后台重新计算全部来源路径的 SHA-256；“检测选中更改”只处理勾选的游戏。两者都会与每个游戏 `hashes.txt` 中记录的来源路径和哈希比较。未备份、新增、删除、路径变化、内容变化或当前备份目录缺失都会标记为“有修改”；完全一致则标记为“无修改”。检测本身不会复制文件，之后可按需执行备份。

“查看历史版本”按时间列出已有备份。每个时间点可以清除或打包：清除会删除该时间点的备份目录；如果其中包含当前 `local` 或账号目录，程序也会同步移除其哈希追踪记录，但不会修改游戏的原始存档。“仅打包这个时间点更新的”只导出该时间点发生更新的分区，“打包这个时间点状态的全部存档”则为每个游戏及其本地/账号分区选择该时间点或之前最后一次记录。ZIP 中的目录统一使用 `local` 或账号名，不保留 `-2026-...` 一类历史时间后缀。

历史窗口支持全选、删除选中和打包选中。批量打包会生成一个 ZIP，以每个所选时间点作为顶层目录；每个时间目录内仍采用 `游戏/local` 或 `游戏/账号` 结构。选择“完整状态”时，会分别重建每个所选时间点当时的全部存档状态。

每个导出的 ZIP 都包含 `steam-saves-backup.json` 清单，其中记录实际导出时间、包含的备份时间点、打包模式以及恢复所需的原路径映射。全局历史窗口可以通过“从压缩包导入”重新导入这类 ZIP；导入内容会作为带时间的历史版本保存，不会直接覆盖当前备份或游戏原始存档。

展开游戏卡片后，可以从备份目录右侧进入该游戏自己的历史版本窗口。单游戏窗口不提供打包操作，支持全选、删除选中，并在每个时间点提供红色“清除”和黄色“恢复”按钮。恢复会选择该时间点或之前每个本地/账号分区的最后一份记录，将其中的文件合并复制回记录的原始路径；同名文件会覆盖，但不会删除原目录里额外存在的文件。新产生的历史目录会在 `history.json` 中保留对应路径映射；较早版本若没有独立映射，会尽量使用当前同分区记录，无法确定时会跳过并提示。

每次点击备份按钮时只记录一次批次时间。本次任务中所有发生更新的游戏、本地目录和账号目录都会使用同一个时间，因此在历史窗口中显示为同一个时间点，而不会因逐个复制耗时被拆成相邻的多个秒数。

检测期间会显示独立进度窗口，包括当前游戏、当前目录或文件、总进度及实时日志。完成后窗口会保留，点击右下角“确定”才关闭。游戏卡片随后按“有修改、无修改、未检测”的顺序排列，同一状态按名称排序，并自动勾选所有“有修改”的游戏。

## 备份

命令行执行：

```powershell
python .\src\backup_manager.py
```

程序会自动创建 `backup_result`，布局如下：

```text
backup_result/
  <AppID>_<游戏名>/
    local/
      001_<原目录名>/
    <Steam账号ID>/
      remote/
    hashes.txt
```

没有任何现存数据的游戏会被跳过。程序会比较来源路径和 SHA-256：没有变化便不重复复制；发生变化时，先把旧目录按其上次备份时间改名（如 `local-2026-09-24-12-37-55` 或 `<账号>-2026-09-24-12-37-55`），再把新副本放回固定的 `local` 或账号目录。不同层独立判断，因此本地数据变化不会重复复制未变化的账号数据。

复制会先在临时目录完成并核对 SHA-256。`hashes.txt` 记录每个来源的哈希、文件数、大小、来源路径、备份内路径和该层的备份时间。注册表项目前只在 `hashes.txt` 中注明，不会导出注册表内容。

## 测试

```powershell
python -m unittest discover -s tests -v
```

## 第三方清单

`third_party/ludusavi-manifest` 是 [Ludusavi manifest](https://github.com/mtkennerly/ludusavi-manifest) 的 vendored snapshot。其许可证和上游说明保留在该子目录中。

当前目录本身是一个独立的浅克隆。更新清单时可执行：

```powershell
.\scripts\update_manifest.ps1
```

`update_manifest.ps1` 使用 Git 默认网络设置，不配置代理，适合其他电脑。

本机若无法直连 GitHub，可以执行：

```powershell
.\scripts\update_manifest_local.ps1
```

`update_manifest_local.ps1` 默认只让本次 Git 命令通过 `http://127.0.0.1:7892`，不会修改全局 Git 代理配置；也可以通过 `-Proxy` 指定其他 HTTP 代理。
