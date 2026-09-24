# Qt 滚动列表卡片尺寸与折叠错位分析

本文记录 Steam 存档备份界面中“展开一张卡片后，其他卡片似乎变宽或发生错位”问题的完整分析过程。重点不是只说明最终改了哪几行代码，而是解释 Qt 的尺寸协商、布局缓存、样式初始化、滚动区域和折叠控件之间如何相互作用，以及为什么这个问题只有在特定操作顺序下才会稳定出现。

本文对应的主要代码位于：

- `src/gui.py` 中的 `StableWidthScrollArea`
- `src/gui.py` 中的 `GameCard`
- `src/gui.py` 中的 `SteamBackupWindow`
- `tests/test_gui.py` 中的卡片展开回归测试

## 1. 最初看到的现象

最初的主观现象是：

> 展开一张游戏卡片后，列表中的其他卡片似乎同时变宽了一点。

后来通过真实 Windows Qt 窗口、实际扫描结果和连续展开/折叠测试，发现这里其实混合了两个不同问题：

1. 长路径可能通过 `sizeHint` 影响横向布局，存在把内容区域撑宽的风险。
2. 真正稳定复现的“整列跳动”主要是纵向问题：折叠后卡片的推荐高度已经变小，但实际几何高度仍保留上一轮展开状态。

第二个问题在只检查“第一次展开”时不会暴露。准确的复现顺序是：

```text
程序启动
  → 第一次展开：正常
  → 第一次折叠：开始错位
  → 第二次展开：沿用错误的上一轮布局状态
  → 以后继续展开/折叠：错误重复出现
```

这也是本问题最容易被误判的地方。

## 2. 当前界面的控件树

从外向内，滚动列表的结构如下：

```text
QMainWindow
└─ Root QWidget
   └─ 页面 QVBoxLayout
      ├─ 标题区
      ├─ 操作按钮栏
      ├─ 筛选栏
      └─ StableWidthScrollArea（继承 QScrollArea）
         └─ viewport
            └─ cards_host（滚动内容控件）
               └─ cards_layout（QVBoxLayout）
                  ├─ GameCard 1
                  ├─ GameCard 2
                  ├─ GameCard 3
                  ├─ ...
                  ├─ empty_label
                  └─ stretch
```

每张游戏卡片内部又是：

```text
GameCard（QFrame）
└─ QVBoxLayout
   ├─ title_row（QHBoxLayout）
   │  ├─ 复选框
   │  ├─ 游戏名称
   │  ├─ 本地数据摘要
   │  ├─ 远程数据摘要
   │  ├─ Steam ID
   │  ├─ 修改状态
   │  ├─ 上次备份时间
   │  ├─ 备份此项按钮
   │  └─ 展开按钮
   └─ details（QWidget）
      └─ QGridLayout
         ├─ 本地数据路径
         ├─ Steam 账号路径
         └─ 注册表项目（如果存在）
```

折叠时 `details` 被隐藏，展开时重新显示。

## 3. Qt 中需要区分的几种尺寸

理解这个问题需要先区分 Qt 控件的几种尺寸概念。

### 3.1 `sizeHint()`

`sizeHint()` 是控件向父布局提交的“首选尺寸”。它表达的是：

> 如果空间允许，我希望自己有这么大。

它不是最终尺寸，也不是强制尺寸。

例如，折叠状态下卡片的首选高度可能是 56；展开并显示三行路径后，首选高度可能变成 147。

### 3.2 `minimumSizeHint()`

`minimumSizeHint()` 是控件建议的最小尺寸。父布局在空间不足时可能把控件压缩到这个值附近。

在本次诊断中，折叠卡片曾出现：

```text
sizeHint().height()        = 56
minimumSizeHint().height() = 53
实际 height()              = 53
```

这三个值不同是完全可能的。

### 3.3 `geometry()`、`width()` 和 `height()`

这些是布局最终分配给控件的真实几何尺寸。

因此，即使：

```python
card.sizeHint().height() == 56
```

也不能推出：

```python
card.height() == 56
```

父布局仍然可能把它压缩到 53，或者因为旧布局尚未更新而让它继续保持 147。

### 3.4 `QSizePolicy`

`QSizePolicy` 告诉父布局如何看待控件的尺寸建议。

当前卡片使用：

```python
self.setSizePolicy(
    QSizePolicy.Policy.Ignored,
    QSizePolicy.Policy.Maximum,
)
```

这里两个方向分别处理：

- 横向使用 `Ignored`：不要因为详情里的长路径扩大整张卡片。
- 纵向使用 `Maximum`：倾向于使用首选高度，但空间不足时仍允许压缩。

正是“纵向允许压缩”这一点，使父容器高度错误时卡片可能落到 `minimumSizeHint()` 附近。

## 4. `QScrollArea` 实际有两层尺寸

`QScrollArea` 不等于它里面的内容控件。至少要区分：

```text
QScrollArea 外框
└─ viewport：用户实际看到的矩形区域
   └─ cards_host：可以高于 viewport 的滚动内容
```

例如本次测试中：

```text
viewport 宽度 = 1054
cards_host 宽度 = 1054
卡片宽度 = 1047
```

卡片比 `cards_host` 少 7 像素，是因为列表布局有 7 像素的右边距。

纵向上则可能是：

```text
viewport 高度 = 491
cards_host 高度 = 3877
```

用户只能看到其中 491 像素，剩余内容通过垂直滚动条访问。

当一张卡片展开后，如果它增加 91 像素，可能变为：

```text
viewport 高度 = 491       # 可视区域没有变化
cards_host 高度 = 3968    # 内容总高度增加
滚动条 maximum 增加 91
```

这才是正常的展开行为。

## 5. 横向尺寸为什么可能被长路径影响

展开详情后会出现类似路径：

```text
C:\Users\admin\AppData\Local\Astro\Saved\Config\WindowsNoEditor
```

如果路径标签、详情控件和卡片都使用普通的 `Preferred` 或 `Minimum` 横向策略，Qt 会把这条长文本纳入宽度计算：

```text
长路径的 sizeHint
  → details 的 sizeHint
  → GameCard 的 sizeHint
  → cards_layout 的 sizeHint
  → cards_host 的 sizeHint
  → QScrollArea 尝试重新分配宽度
```

因此代码中做了三层横向保护。

### 5.1 路径标签忽略横向建议

路径标签使用自动换行，并设置：

```python
label.setMinimumWidth(0)
label.setSizePolicy(
    QSizePolicy.Policy.Ignored,
    QSizePolicy.Policy.Preferred,
)
```

这样长路径可以在现有宽度内换行，而不是要求父控件继续变宽。

### 5.2 卡片忽略横向 `sizeHint`

`GameCard` 的横向策略同样为 `Ignored`。父列表给它多宽，它就使用多宽。

### 5.3 内容宽度绑定到 viewport

`StableWidthScrollArea.sync_content_width()` 执行：

```python
width = self.viewport().width()
content.setFixedWidth(width)
```

于是横向关系被明确规定为：

```text
cards_host 宽度 = viewport 宽度
卡片宽度 = cards_host 宽度 - 布局左右边距
```

同时：

```python
self.scroll.setHorizontalScrollBarPolicy(
    Qt.ScrollBarPolicy.ScrollBarAlwaysOff
)
```

表示这个界面不允许通过水平滚动来容纳长路径，长路径必须在现有宽度中布局。

垂直滚动条则设置为始终显示：

```python
self.scroll.setVerticalScrollBarPolicy(
    Qt.ScrollBarPolicy.ScrollBarAlwaysOn
)
```

这样可以避免“内容刚好溢出时垂直滚动条突然出现，viewport 宽度减少约 10 像素”的典型横向跳动。

## 6. 为什么最初会被认为是宽度问题

真实诊断数据表明，外框宽度一直没有变化：

```text
viewport：1054 → 1054
cards_host：1054 → 1054
卡片宽度：1047 → 1047
卡片左边界：28 → 28
卡片右边界：1075 → 1075
水平滚动值：0 → 0
```

还对展开卡片上方的一张卡片进行了逐像素比较，差异区域为空：

```text
pixel difference bbox = None
```

也就是说，上方卡片不只是几何数据相同，实际渲染像素也完全相同。

但当多张卡片的高度、间距或纵向位置一起改变时，人的视觉会把整列白色矩形的变化理解为“卡片整体变大”。再加上垂直滚动条滑块长度会变化，很容易误判为宽度变化。

所以调试布局问题时，不能只依赖肉眼，至少要同时记录：

```text
控件 x / y
控件 width / height
sizeHint
minimumSizeHint
viewport 尺寸
内容控件尺寸
水平和垂直滚动条的 value / maximum
```

## 7. 第一个隐藏问题：样式 polish 发生得较晚

程序入口先设置 Fusion 风格和 QSS：

```python
app.setStyle("Fusion")
app.setStyleSheet(STYLE_SHEET)
app.setFont(QFont("Microsoft YaHei UI", 9))
```

但这并不代表所有新创建控件已经立即完成样式计算。

Qt 的样式处理包含一个常被称为 `polish` 的阶段。字体、边框、内边距、最小高度等最终尺寸，可能直到控件即将显示或事件循环开始处理后才完全确定。

样式表中会影响尺寸的属性包括：

```css
QToolButton#ExpandButton {
    min-height: 31px;
    border: 1px solid ...;
}

QPushButton#CardButton {
    min-height: 31px;
}

QFrame#GameCard {
    border: 1px solid ...;
}
```

而游戏卡片是在 `SteamBackupWindow` 构造期间创建的。窗口尚未正式显示时，父列表可能基于样式完成前的尺寸进行第一次计算。

曾经测得 58 张折叠卡片：

```text
卡片首选高度：56
卡片初始实际高度：53
每张相差：3
```

58 张卡片的总差值是：

```text
58 × 3 = 174
```

而容器高度的实际差值正好也是：

```text
3877 - 3703 = 174
```

这说明问题并不是随机抖动，而是整个列表使用了样式完成前的旧尺寸。

### 7.1 为什么使用 `QTimer.singleShot(0, ...)`

创建或筛选卡片后，代码执行：

```python
QTimer.singleShot(0, self.sync_cards_height)
```

这里的“0 毫秒”并不等于在当前代码行同步调用。它的含义更接近：

> 当前调用栈结束并回到 Qt 事件循环后，尽快执行这个函数。

此时窗口和子控件通常已经完成样式初始化，因此能得到正确的 `sizeHint()`。

## 8. 真正的循环 Bug：折叠后仍保留展开高度

只处理首次样式初始化还不够。真正的关键问题只有在完整循环中才能复现。

测试使用七张来自真实扫描结果的卡片。这七张折叠卡片刚好能放入 491 像素高的 viewport；展开 ASTRONEER 后，内容开始溢出并需要滚动。

修复前的状态序列如下。

### 8.1 初始折叠

```text
目标卡片实际高度：56
目标卡片 sizeHint：56
cards_host 实际高度：491
滚动条 maximum：0
```

因为七张卡片的内容总高度小于 viewport，`cards_host` 至少保持 viewport 的 491 像素高度。

### 8.2 第一次展开

```text
目标卡片实际高度：147
目标卡片 sizeHint：147
cards_host 实际高度：551
滚动条 maximum：60
```

这是正确状态。

### 8.3 第一次折叠

详情已经隐藏，卡片的尺寸建议也已经恢复：

```text
目标卡片 sizeHint：56
```

但实际几何尺寸仍然是：

```text
目标卡片实际高度：147
cards_host 实际高度：551
```

也就是说，模型状态已经是“折叠”，但布局几何仍然是“展开”。卡片内部出现多余空白，后续卡片的位置也继续按照展开状态计算。

### 8.4 第二次展开

第二次点击后，`sizeHint` 又回到 147。由于实际几何本来就错误地停在 147，Qt 可能认为无需立即改变几何。

于是 UI 状态和布局状态开始相差一轮：

```text
用户操作状态 N
布局几何状态 N - 1
```

这就是为什么第一次展开看起来正常，而从第一次折叠开始持续出错。

## 9. 旧的纵向自动约束为什么会放大问题

列表布局曾使用：

```python
self.cards_layout.setSizeConstraints(
    QLayout.SizeConstraint.SetNoConstraint,
    QLayout.SizeConstraint.SetMinAndMaxSize,
)
```

目标是：

- 横向不要由内容决定。
- 纵向让 Qt 自动更新容器尺寸。

思路本身合理，但这里存在两个不同来源的高度：

1. 程序根据所有卡片的 `sizeHint()` 手动计算首选高度。
2. `SetMinAndMaxSize` 根据布局的最小尺寸和最大尺寸自动改写父控件约束。

它们并不总是使用同一套数值。一个可能使用首选高度 56，另一个可能使用最小高度 53。

当详情显示状态改变时，布局约束、`cards_host` 的当前高度、QScrollArea 的内容尺寸和卡片的真实几何不会保证在同一时刻一起更新。这就产生了延迟一轮的状态。

最终方案是取消列表层的自动尺寸约束：

```python
self.cards_layout.setSizeConstraint(
    QLayout.SizeConstraint.SetNoConstraint
)
```

横向和纵向都不再让布局自动改写 `cards_host` 的尺寸约束，转而由程序在明确的状态变化点统一同步。

## 10. 最终修复：每次展开和折叠都同步容器高度

### 10.1 卡片发出展开状态变化信号

`GameCard` 新增信号：

```python
expansion_changed = Signal()
```

在详情可见性改变后发出：

```python
def set_expanded(self, expanded: bool) -> None:
    self.details.setVisible(expanded)
    self.expand_button.setText("∧" if expanded else "∨")
    self.expand_button.setToolTip("收起路径" if expanded else "展开路径")
    self.expansion_changed.emit()
```

这里必须在 `details.setVisible()` 之后发出信号，因为新的 `sizeHint()` 取决于详情是否参与布局。

### 10.2 主窗口监听每张卡片

创建卡片时连接：

```python
card.expansion_changed.connect(self.sync_cards_height)
```

Qt 默认使用直接连接。因为发出者和接收者都在 GUI 主线程中，`sync_cards_height()` 会在当前展开/折叠调用期间执行，而不是等到下一次用户操作。

### 10.3 重新计算所有可见卡片

同步函数的核心过程如下：

```python
def sync_cards_height(self) -> None:
    self.cards_host.ensurePolished()

    for card in self.visible_cards():
        card.ensurePolished()
        card_layout = card.layout()
        if card_layout is not None:
            card_layout.invalidate()
            card_layout.activate()

    self.cards_layout.invalidate()
    self.cards_layout.activate()

    preferred_height = self.cards_layout.sizeHint().height()
    self.cards_host.setMinimumHeight(preferred_height)
    self.cards_host.resize(
        self.cards_host.width(),
        max(preferred_height, self.scroll.viewport().height()),
    )
    self.cards_host.updateGeometry()
```

各步骤的作用如下。

#### `ensurePolished()`

确保 QSS、字体和风格相关尺寸已经应用。

#### `invalidate()`

告诉布局：旧缓存不再可信，需要重新计算。

#### `activate()`

立即执行新的布局计算，而不是只等待未来某个事件触发布局。

#### `sizeHint().height()`

获取当前所有可见卡片、间距和边距所需的首选总高度。

#### `setMinimumHeight()` 和 `resize()`

把 `cards_host` 调整到当前状态真正需要的高度。

这里使用：

```python
max(preferred_height, viewport.height())
```

是为了处理列表项目很少的情况：

- 内容比 viewport 高：内容控件使用内容高度，滚动条产生范围。
- 内容比 viewport 矮：内容控件至少填满 viewport，不会在底部留下异常背景区域。

## 11. 修复后的完整状态序列

使用七张真实卡片测试：

```text
初始折叠
  target = 56
  host = 491
  scrollbar maximum = 0

第一次展开
  target = 147
  host = 551
  scrollbar maximum = 60

第一次折叠
  target = 56
  host = 491
  scrollbar maximum = 0

第二次展开
  target = 147
  host = 551
  scrollbar maximum = 60

第二次折叠
  target = 56
  host = 491
  scrollbar maximum = 0

第三次展开
  target = 147
  host = 551
  scrollbar maximum = 60
```

每次操作后，其他六张卡片始终保持：

```text
宽度 = 1047
高度 = 56
```

## 12. 滚动后再展开的验证

为了覆盖滚动相关行为，还使用全部 58 张真实卡片执行：

```text
先把垂直滚动值设为 650
再展开 Goat Simulator 3
折叠
第二次展开
再次折叠
第三次展开
```

修复后的结果：

```text
滚动值始终保持：650

折叠时：
  cards_host = 3877
  target = 56
  其他卡片 = 56

展开时：
  cards_host = 3968
  target = 147
  其他卡片 = 56
```

滚动条的 `maximum` 在 3386 和 3477 之间变化，这是正常的，因为内容总高度增加或减少了 91 像素。

重要的是滚动条的当前 `value` 没有变化，所以列表没有因为展开操作自行跳到其他位置。

## 13. 为什么“本地和远程都无数据”的卡片看起来正常

四类卡片的实测展开高度为：

| 数据类型 | 折叠高度 | 展开高度 | 增加量 |
|---|---:|---:|---:|
| 本地和远程都有 | 56 | 147 | 91 |
| 只有本地 | 56 | 121 | 65 |
| 只有远程 | 56 | 121 | 65 |
| 本地和远程都没有 | 56 | 119 | 63 |

无数据卡片展开后只显示两个“无”，没有长路径，也不容易跨越滚动区域的临界高度。即使残留了一点错误空间，视觉上也不如包含多行路径的卡片明显。

这并不代表无数据卡片走了完全不同的布局代码；它们仍然使用相同的 `GameCard` 和 `details`。区别主要在于详情内容的首选高度和视觉特征。

## 14. 为什么前几次修复没有完全解决

### 14.1 始终显示垂直滚动条

这能解决滚动条突然出现导致 viewport 变窄的问题，但不能解决卡片折叠后高度没有及时收缩的问题。

### 14.2 把 `cards_host` 宽度固定为 viewport 宽度

这能解决长路径影响横向布局的问题，但与纵向高度缓存无关。

### 14.3 忽略路径和卡片的横向 `sizeHint`

这同样只解决横向传播链。

### 14.4 只在首次显示时调用 `sync_cards_height()`

它能修复程序刚启动时 53 → 56 的高度跳变，但无法覆盖后续的：

```text
展开 → 折叠 → 再展开
```

最终必须把同步动作绑定到每一次 `details` 可见性变化。

## 15. 如何调试类似 Qt 布局问题

### 15.1 不要只打印 `sizeHint`

同时记录：

```python
print(
    widget.geometry(),
    widget.sizeHint(),
    widget.minimumSizeHint(),
    widget.minimumSize(),
    widget.maximumSize(),
    widget.sizePolicy(),
)
```

如果 `sizeHint` 正确但 `geometry` 错误，问题通常在父布局或父容器尺寸。

### 15.2 记录滚动区域三层尺寸

```python
print("scroll", scroll.size())
print("viewport", scroll.viewport().size())
print("content", scroll.widget().size())
print("vbar", scroll.verticalScrollBar().value(),
      scroll.verticalScrollBar().maximum())
```

只看 `QScrollArea.width()` 或 `height()` 通常不够。

### 15.3 测试完整状态机，而不是单次操作

折叠控件至少应该测试：

```text
初始
展开
折叠
再次展开
再次折叠
```

如果只测第一次展开，很容易漏掉缓存或延迟一轮的问题。

### 15.4 测试滚动临界点

应准备三种列表规模：

1. 单张卡片，内容远小于 viewport。
2. 多张卡片，折叠时刚好不滚动，展开后刚好需要滚动。
3. 大量卡片，展开前后都需要滚动。

第二种最容易暴露滚动条范围和内容尺寸切换问题。

### 15.5 使用像素对比排除视觉误判

如果怀疑上方卡片变化，可以在展开前后截取同一个矩形区域，通过像素差异判断：

```python
difference = ImageChops.difference(before, after)
print(difference.getbbox())
```

结果为 `None` 表示两张图在该区域完全相同。

### 15.6 注意悬停和焦点样式

鼠标点击后可能触发：

```css
QFrame#GameCard:hover
QToolButton#ExpandButton:hover
QPushButton:pressed
```

边框颜色变化可能让控件看起来扩大，但它不一定改变 `geometry()`。因此需要区分：

- 实际几何变化
- 样式绘制变化
- 滚动位置变化
- 相邻项目被顺延

## 16. 回归测试应该保护什么

当前测试不再只检查一次展开后的宽度，而是验证完整循环：

```python
collapsed_height = card.height()
other_heights = [other.height() for other in siblings]

card.expand_button.click()
expanded_height = card.height()

card.expand_button.click()
assert card.height() == collapsed_height
assert sibling_heights_are_unchanged()

card.expand_button.click()
assert card.height() == expanded_height
assert sibling_heights_are_unchanged()
```

这个测试保护了三个核心不变量：

1. 展开只改变目标卡片高度。
2. 折叠必须在同一次操作中恢复目标卡片高度。
3. 第二次以及后续展开不能依赖上一轮残留几何。

## 17. 当前方案的边界和后续改进方向

### 17.1 窗口宽度改变与路径换行

路径标签开启了自动换行。窗口显著变窄后，同一路径可能从一行变成两行，进而改变展开卡片的首选高度。

当前代码会在展开/折叠、创建列表和筛选后同步高度。更完整的实现还可以在 viewport 宽度变化后安排一次高度同步，从而覆盖窗口拖动缩放时的路径重新换行。

### 17.2 大量卡片的同步成本

`sync_cards_height()` 会遍历所有可见卡片并激活布局。58 张卡片的成本很低，但如果未来列表增长到数千项，应考虑：

- 只同步受影响卡片和父布局。
- 使用模型/视图架构，例如 `QListView` 和自定义 delegate。
- 使用虚拟化列表，避免同时创建全部卡片控件。

### 17.3 PySide6 版本兼容性

双轴 `setSizeConstraints(horizontal, vertical)` 是较新的 Qt API，而 `requirements.txt` 允许从 PySide6 6.7 开始安装。当前横向和纵向使用相同约束，因此代码使用较早版本也提供的单参数 API：

```python
layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
```

## 18. 总结

这个问题的核心经验可以归纳为：

1. Qt 的 `sizeHint` 是建议值，不是实际几何尺寸。
2. `QScrollArea` 的 viewport 和内容控件是两个不同的尺寸系统。
3. 显示或隐藏子控件会使尺寸建议失效，但不保证父容器在同一个时刻立即调整。
4. QSS 的最终尺寸可能要到 polish 阶段才确定。
5. 横向长文本问题和纵向折叠问题必须分开处理。
6. 只测试第一次展开不足以覆盖折叠组件的状态机。
7. 对动态卡片列表，最可靠的策略是明确规定宽度来源，并在每次内容可见性变化后统一同步高度。

最终采用的规则可以简化为：

```text
横向：viewport 决定 cards_host，cards_host 决定卡片宽度
纵向：所有当前可见卡片的 sizeHint 总和决定 cards_host 高度
状态变化：每次展开、折叠、筛选和首次显示后重新同步
```

这种做法牺牲了一部分完全自动的尺寸协商，但换来了明确、稳定且容易测试的布局行为。
