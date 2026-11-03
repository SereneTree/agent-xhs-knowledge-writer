# agent-xhs-knowledge-writer

面向 **AI Agent 方向校招 / 实习面试**的小红书发布稿写作技能（user skill）。服务计算机专业本科生与研究生（含研三 / 博三、应届硕士，开发岗为主），用可溯源的原理讲解、工程取舍与教学案例建立专业形象，产出「教材 + 发布正文 + 手绘总结封面」三件套。

> 本技能在豆包 / Doubao Agent 环境内挂载使用，配合 `lark-doc`、`doubao-visualization` 等宿主技能完成云文档与渲染；不是独立 pip 包，脚本均为本地流程校验工具。

## 产出形态

| 产物 | 规格（非空白可见字符口径，脚本实测） |
|---|---|
| 完整教材 | 5000—10000 字；确需超长须 `--over-reason` 说明不可删内容 |
| 发布正文 | 1500 字，前置追加进同一份飞书云文档，教材模块完整保留在后 |
| 总结封面 | 1—2 个竖版 3:4 单元（参考 1080×1440），默认 1 张；密度不可读时并列分块为 2 张；0 张或 ≥3 张拒绝 |

正文不是 100—200 字导语，封面不是吸睛海报：封面承担「一眼看完答题骨架」，正文承担「把面试问题讲清楚」。

## 四阶段流程（自动顺序执行，质量门不跳过）

```
A 选题思路（对话展示并登记 outline）
   ↓
B 教材写作与复核（来源核验 / 代码可复现 / 字数与图结构检查 → 飞书云文档）
   ↓
C 同文档前置追加 1500 字发布正文（云端回读 snapshot 证据）
   ↓
D 1—2 张竖版 3:4 手绘总结封面（manifest 结构校验 + 逐页渲染证据）
```

本地状态机：`new → outline_shown → textbook_done → post_done → done`。脚本只做本地证据与 hash 防呆，不替代云端内容回读；云端结论以实际回读为准。

## 目录结构

| 路径 | 作用 |
|---|---|
| `SKILL.md` | 技能主规则：目标边界、四阶段流程、质量门与交付约束 |
| `references/editorial.md` | 写作规范、分层讲解、字数口径、小红书竖屏排版规则、发布稿形态 |
| `references/cover-style.md` | 手绘封面硬性规范：手绘感三要素、3:4 尺寸、1—2 单元双页规则、验收清单 |
| `references/technical-review.md` | 事实 / 代码 / 图结构 / 反例复核要求与云文档回读证据要求 |
| `references/operations.md` | 命令用法、cover manifest 与 cover-review 最小字段示例 |
| `references/sample-notes.md` | 历史对标笔记复盘与可信度边界（存档口径，不反向约束现行规则） |
| `references/acceptance-cases.md` | 修改技能后的行为验收场景 |
| `scripts/check_content.py` | 字数统计、教材 diagram 结构检查、封面 manifest 结构检查 |
| `scripts/stage_gate.py` | 阶段登记与阻断校验：hash 绑定、证据齐全、顺序与数量门 |
| `scripts/sketch.py` | 零依赖手绘 SVG 图元生成器（标准库 random + math，固定种子可复现） |
| `assets/` | 两张封面风格参考图（仅提取版式 / 线条 / 色彩气质，不直接发布） |

## 快速上手

在每篇选题的独立项目目录（`$TOPIC_DIR`）操作；`$SKILL` 为本技能根目录绝对路径。

```bash
# 1. 初始化选题状态
python "$SKILL/scripts/stage_gate.py" --state "$TOPIC_DIR/workflow.json" init --topic "题目"

# 2. 登记思路（思路全文先在对话展示，落盘仅为登记）
python "$SKILL/scripts/stage_gate.py" --state "$TOPIC_DIR/workflow.json" record --stage outline --file "$TOPIC_DIR/outline.txt"

# 3. 教材字数校验 + 登记（绑定 tb-review.json 与云文档 URL）
python "$SKILL/scripts/check_content.py" count "$TOPIC_DIR/textbook.txt" --stage textbook
python "$SKILL/scripts/stage_gate.py" --state "$TOPIC_DIR/workflow.json" record --stage textbook \
  --file "$TOPIC_DIR/textbook.txt" --url "飞书云文档URL" --review "$TOPIC_DIR/tb-review.json"

# 4. 发布正文字数校验 + 登记（须带云端回读 snapshot）
python "$SKILL/scripts/check_content.py" count "$TOPIC_DIR/post.txt" --stage post
python "$SKILL/scripts/stage_gate.py" --state "$TOPIC_DIR/workflow.json" record --stage post \
  --file "$TOPIC_DIR/post.txt" --url "飞书云文档URL" --snapshot "$TOPIC_DIR/doc-snapshot.json"

# 5. 封面 manifest 校验 + finish 登记
python "$SKILL/scripts/check_content.py" cover "$TOPIC_DIR/cover.json"
python "$SKILL/scripts/stage_gate.py" --state "$TOPIC_DIR/workflow.json" finish \
  --post "$TOPIC_DIR/post.txt" --cover-manifest "$TOPIC_DIR/cover.json" \
  --cover-review "$TOPIC_DIR/cover-review.json" --doc-url "飞书云文档URL"

# 封面单独修改（不动教材 / 正文 / 云文档版本）
python "$SKILL/scripts/stage_gate.py" --state "$TOPIC_DIR/workflow.json" cover-edit \
  --cover-manifest "$TOPIC_DIR/cover.json" --cover-review "$TOPIC_DIR/cover-review.json"

# 旧版 schema 1 工作流迁移（历史确认记录保留，不视为质量通过）
python "$SKILL/scripts/stage_gate.py" --state "$TOPIC_DIR/workflow.json" migrate
```

## 封面规则要点

- **尺寸硬约束**：每个单元竖版 `3:4`（宽:高 = 3:4，参考 1080×1440）；manifest 必填 `canvas: {"ratio": "3:4", "width": 1080, "height": 1440}`，脚本校验 `height > width` 且 `height ≈ width×4/3（±1%）`。
- **数量规则**：默认 1 单元；提炼层次后仍密度不可读（字号过小、节点重叠、连线交叉、安全区被占）才拆 2 页并列分块；拆分理由与两页分工写入证据文件。双页无独立总览页，共用一份 manifest / diagram_type / 节点清单，`core_points` 逐个标注 `page: 0|1`，全集覆盖必备类别。
- **可编辑形式**：每个单元 = 可编辑源 + 预览；manifest 用 `covers: [{source, preview}, ...]`（1—2 项），单张兼容 `cover: {source, preview}`。
- **手绘特征**：框线抖动重描、手绘两笔箭头（不用规整 marker）、英文数字用手写体；渲染后须逐节点、逐标签、手机宽度复核。

## 质量门摘要

- 教材：来源原文与版本留痕；代码 / schema / 参数可复现；`sources`、`counts` 必须 pass，其余范围可带理由 not_applicable。
- 正文：1500 字硬边界；云文档模块顺序必须为 `post → textbook`，snapshot 实读 body 文件 hash。
- 封面：单元数量、canvas 比例、page 标注、必备类别（problem / judgment / module / mechanism；failure / tradeoff / boundary 缺项须 `coverage_na` 理由）、`source_revision` 与教材 sha256 绑定；六项封面证据均须 `{status: pass, evidence: 逐项说明}`。

## 边界（不做事项）

- 不编造面试真题来源、录取率、履历与「面了多少人」类表述；无真实来源时标明模拟面试题 / 备考讲解。
- 不做发布、私信、评论、关注、收费等账号动作；不承诺 offer 结果。
- 不把教材内部教学插图混进对外封面集合；不把截图中间件当封面交付。
