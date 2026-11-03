# 流程工具用法

## 路径与状态

将每篇实际写作文件放到用户项目目录的独立主题文件夹，不放进技能目录。该文件夹由单个执行者写入 `workflow.json`，其他协作者只读或提交候选，避免并发更新覆盖。

使用以下相对技能路径时，先在当前环境解析技能的真实目录；示例中的 `$SKILL`、`$TOPIC_DIR` 是说明用变量，不是工具可直接接受的占位路径。执行前替换成已确认路径。

先准备题目，不因正在新建技能就开启某篇文章。

## 一篇文章的阶段（自动顺序，不等逐篇确认）

永久默认：思路展示后自动继续教材，教材复核后自动继续压缩正文与封面；质量门不跳过。

1. 新题目：创建独立项目文件夹，再初始化。
   `python "$SKILL/scripts/stage_gate.py" --state "$TOPIC_DIR/workflow.json" init --topic "用户给定题目"`
2. 思路写进 outline.txt 并在对话全文展示，登记后自动继续（思路不跑字数检查）。
   `python "$SKILL/scripts/stage_gate.py" --state "$TOPIC_DIR/workflow.json" record --stage outline --file "$TOPIC_DIR/outline.txt"`
   思路或教材后续要改，直接在同一选题目录重新 record：下游登记自动失效，历史文件与 doc URL 保留在 history_files，不要求另建主题/另建云文档。
3. 写教材、检验代码与图、创建并回读飞书云文档（先放完整教材），可计数字正文存 textbook.txt，结构化复核证据存 tb-review.json。
   先预计算教材 sha256（证据 JSON 要绑定它，避免“先 record 才能拿 sha”的循环）：
   `shasum -a 256 "$TOPIC_DIR/textbook.txt"`
   `python "$SKILL/scripts/check_content.py" count "$TOPIC_DIR/textbook.txt" --stage textbook`
   教材默认区间 5000—10000 字；确需超过 10000 时把 `--over-reason "一句非空理由"` 一并传给 record：
   `python "$SKILL/scripts/stage_gate.py" --state "$TOPIC_DIR/workflow.json" record --stage textbook --file "$TOPIC_DIR/textbook.txt" --url "云文档URL" --review "$TOPIC_DIR/tb-review.json" [--over-reason "不可删的具体数据/机制/反例"]`
   record 内部实调字数校验，并要求证据绑定当前教材 sha。sources/counts 必须 pass（不能 not_applicable）；code/diagram/render 可带理由 not_applicable；每个 pass 范围须有非空 evidence 说明。
   ```json
   {"textbook_sha256": "shasum 输出的 textbook sha256",
    "ranges": {
      "sources": {"status": "pass", "evidence": "读了哪几篇原文/版本"},
      "code": {"status": "pass", "evidence": "运行环境与结果"},
      "diagram": {"status": "pass", "evidence": "节点边清单"},
      "render": {"status": "pass", "evidence": "渲染检查发现项"},
      "counts": {"status": "pass", "evidence": "字数口径与结果"}}}
   ```
   同一选题目录的 doc URL 固定：换 URL 重新 record 会被拒，除非用户明确要求新云文档（加 `--new-doc`）。

4. 写 1500 字发布正文存 post.txt，作为独立模块**前置追加进同一份云文档**（教材模块保留在后）。实际回读云文档后，把两个模块正文各导出一份回读文本，写 doc-snapshot.json（脚本实读这些文件算 hash，不信声明字符串）：
   ```json
   {"doc_url": "云文档URL", "doc_version": "回读到的文档版本号",
    "modules": [{"role": "post", "body_file": "post-readback.txt"},
                {"role": "textbook", "body_file": "tb-readback.txt"}]}
   ```
   `python "$SKILL/scripts/stage_gate.py" --state "$TOPIC_DIR/workflow.json" record --stage post --file "$TOPIC_DIR/post.txt" --url "云文档URL" --snapshot "$TOPIC_DIR/doc-snapshot.json"`
   post 登记成功才进入 post_done（C 完成），否则 D 被阻断。

5. 制作 1—2 个封面单元（默认 1 个；密度不可读时并列分块 2 个；每个单元 = 可编辑源 + 预览，竖版 3:4），结构存 cover.json；封面证据存 cover-review.json（绑定当前 manifest 与各单元实际源/预览文件 hash）：
   ```json
   {"textbook_sha256": "教材 sha256", "post_sha256": "post.txt sha256",
    "cover_manifest_sha256": "shasum cover.json",
    "cover_files": [
      {"source": {"path": "cover-1-source.svg", "sha256": "shasum"},
       "preview": {"path": "cover-1-preview.png", "sha256": "shasum"}},
      {"source": {"path": "cover-2-source.svg", "sha256": "shasum"},
       "preview": {"path": "cover-2-preview.png", "sha256": "shasum"}}
    ],
    "node_edge": {"status": "pass", "evidence": "逐页逐边走读：页① i→c→d 正常；页② c→r→c 重试分支"},
    "render": {"status": "pass", "evidence": "逐页渲染后逐节点/箭头核对；两页均为竖版 3:4"},
    "source_editable": "逐页说明可编辑源如何修改（改 SVG/脚本后重渲染对应页）",
    "preview_verified": "逐页说明预览如何渲染核验；拆分理由与两页分工记录于此",
    "semantic_review": {"status": "pass", "evidence": "逐 core_point 走读：cp1…cp7 按 page 0/1 落到两页，与正文位置一一对应"},
    "coverage_check": {"status": "pass", "evidence": "source_sections 逐章回读自教材与压缩正文，全集覆盖两页"},
    "render_mobile": {"status": "pass", "evidence": "手机宽度下逐页检查字/线/配色与 ①→② 阅读顺序"},
    "style_check": {"status": "pass", "evidence": "对照 assets 两张风格资产，两页米白手绘风格一致"}
   ```
   单单元时 `cover_files` 也可兼容旧写法 `{"source": {...}, "preview": {...}}` 对象；双单元必须用与 `covers` 同序的列表。
   `python "$SKILL/scripts/check_content.py" cover "$TOPIC_DIR/cover.json"`
   `python "$SKILL/scripts/stage_gate.py" --state "$TOPIC_DIR/workflow.json" finish --post "$TOPIC_DIR/post.txt" --cover-manifest "$TOPIC_DIR/cover.json" --cover-review "$TOPIC_DIR/cover-review.json" --doc-url "云文档URL"`
   finish 内部按序复核：post_done 前置 → 教材/post 文件未变动 → 教材证据绑定 → doc-url 一致 → 旧 snapshot 实读 body 文件 hash 与当前 post/tb 一致 → cover manifest（封面单元 1—2 个、canvas 竖版 3:4、双页 page 标注、core_points/必备类别/source_sections/coverage_na 完整结构校验）→ source_revision 绑定 → cover-review 绑定当前 manifest 与各单元源/预览 hash。

6. 封面后续单独修改：重做 cover-review.json 后 cover-edit；脚本先核教材/post 当前 hash 与旧 snapshot 一致性，stale 拒绝；不动 post/教材/云文档版本。
   `python "$SKILL/scripts/stage_gate.py" --state "$TOPIC_DIR/workflow.json" cover-edit --cover-manifest "$TOPIC_DIR/cover.json" --cover-review "$TOPIC_DIR/cover-review.json"`

旧版（schema 1，含 idea_wait/textbook_wait/approve）workflow 显式迁移：
`python "$SKILL/scripts/stage_gate.py" --state "$TOPIC_DIR/workflow.json" migrate`
历史确认记录、history、实际文件全部保留；旧“已批准”与旧 done 不视为质量通过——没有绑定当前版本的结构化证据就降级到 outline_shown，用真文件重新复核登记即可，不需要 approve。

工具只保证本地证据附件、hash、阶段与字数，不能核实真人授权或云文档真实内容；snapshot 由执行方实际回读后产出，本地脚本不代替云端核对。跨会话不从模型摘要推测授权。线上文档若被改动，先回读同步版本。

## 图结构清单

为封面/教材图保存 JSON，不必展示给读者：
```json
{
  "title": "技术审查流程示意",
  "diagram_type": "flow",
  "source_revision": "已登记核验教材 textbook.txt 的 sha256",
  "legend": "control=执行顺序；data=数据传递",
  "nodes": [
    {"id": "input", "label": "输入", "kind": "start"},
    {"id": "check", "label": "校验通过？", "kind": "decision"},
    {"id": "done", "label": "交付", "kind": "terminal"},
    {"id": "fix", "label": "修订", "kind": "process"}
  ],
  "edges": [
    {"from": "input", "to": "check", "kind": "control"},
    {"from": "check", "to": "done", "kind": "control", "label": "是"},
    {"from": "check", "to": "fix", "kind": "control", "label": "否"},
    {"from": "fix", "to": "check", "kind": "control"}
  ]
}
```
运行 `python "$SKILL/scripts/check_content.py" diagram "$TOPIC_DIR/diagram.json"`。

分类/架构关系图使用 diagram_type=structure，节点不要求 start/terminal，仍需标边类型。结构检查成功不能声称架构正确，人工逐边审阅及最终图渲染仍必做。

### 封面清单（cover manifest）

对外封面 1—2 个单元（默认 1 个；密度不可读时并列分块 2 个），结构存 cover.json。每个单元都是竖版 3:4、可编辑源 + 预览（不硬锁格式）。以下为双单元示例；单单元把 `covers` 换成 `cover: {source, preview}` 并省略 core_points 的 `page` 字段即可：
```json
{
  "title": "主问题",
  "diagram_type": "flow",
  "legend": "control=执行顺序；data=数据传递",
  "canvas": {"ratio": "3:4", "width": 1080, "height": 1440},
  "source_revision": "已登记教材 textbook.txt 的 sha256（shasum -a 256 计算，与登记 sha 一致）",
  "covers": [
    {"source": "cover-1-source.svg", "preview": "cover-1-preview.png"},
    {"source": "cover-2-source.svg", "preview": "cover-2-preview.png"}
  ],
  "nodes": [
    {"id": "input", "label": "用户输入", "kind": "start"},
    {"id": "check", "label": "校验通过？", "kind": "decision"},
    {"id": "done", "label": "结果", "kind": "terminal"},
    {"id": "retry", "label": "重试/降级", "kind": "process"}
  ],
  "edges": [
    {"from": "input", "to": "check", "kind": "control"},
    {"from": "check", "to": "done", "kind": "control", "label": "是"},
    {"from": "check", "to": "retry", "kind": "control", "label": "否"},
    {"from": "retry", "to": "check", "kind": "control"}
  ],
  "source_sections": [
    {"doc": "textbook", "section": "§1 问题"},
    {"doc": "textbook", "section": "§3 机制"},
    {"doc": "post", "section": "正文"}
  ],
  "core_points": [
    {"id": "cp1", "category": "problem", "page": 0, "source": {"doc": "textbook", "section": "§1 问题"},
     "summary": "主问题", "maps_to": ["input"]},
    {"id": "cp2", "category": "judgment", "page": 0, "source": {"doc": "post", "section": "正文"},
     "summary": "核心判断", "maps_to": ["check"]},
    {"id": "cp3", "category": "module", "page": 0, "source": {"doc": "textbook", "section": "§3 机制"},
     "summary": "校验模块", "maps_to": ["check"]},
    {"id": "cp4", "category": "mechanism", "page": 1, "source": {"doc": "textbook", "section": "§3 机制"},
     "summary": "重试机制", "maps_to": ["retry"]},
    {"id": "cp5", "category": "failure", "page": 1, "source": {"doc": "textbook", "section": "§3 机制"},
     "summary": "重试失败分支", "maps_to": ["retry"]},
    {"id": "cp6", "category": "boundary", "page": 1, "source": {"doc": "post", "section": "正文"},
     "summary": "适用边界", "maps_to": ["done"]},
    {"id": "cp7", "category": "tradeoff", "page": 1, "source": {"doc": "textbook", "section": "§3 机制"},
     "summary": "重试成本与延迟取舍", "maps_to": ["retry"]}
  ]
}
```
运行 `python "$SKILL/scripts/check_content.py" cover "$TOPIC_DIR/cover.json"`。交付形式：`covers: [{source, preview}, ...]` 为 1—2 个可编辑单元（不锁格式）；单单元也兼容旧写法 `cover: {source, preview}`；`covers: ["单文件"]` 是历史单文件成品兼容写法（仅允许 1 项字符串），不声称可编辑源+预览能力——若该单文件本身真正可编辑/可交互预览，cover-review 必须说明修改方式。相对路径按 cover.json 所在目录解析，不强制两个不同格式。`canvas` 必填：`{"ratio": "3:4", "width": 1080, "height": 1440}`，脚本校验竖版（height > width 且 height ≈ width×4/3，±1%）；横版 4:3 或其他比例拒绝。双单元时每个 core_points 必须带 `page`（0/1），page 越界拒绝；单单元可省略 page。diagram_type 选 flow/structure/mixed：flow 做完整控制分支检查（节点 id 唯一、label 非空、边类型合法、有 start/terminal、无孤立/不可达节点、带两条标注控制边的 decision；失败确不适用时写 coverage_na.failure 理由，不硬造无关 decision）；structure 只做基本关系检验，不硬造 start/terminal/decision；mixed = flow 主干 + 框架分区。程序对 mixed 只做全图基本关系检查 + 已有 decision 的两条标注边校验；流程区域内的 start/terminal/可达性由人工逐区域走读，程序不声称完整流程检查。core_points 从教材核心章节+压缩正文提炼，source 为 `{doc: textbook|post, section}`，双页时 page 标明落点单元；全集 core_points 必须覆盖全部必备类别，不因双页拆分而缺类。source_sections 必须按真实教材/post 完整列出章节，不得删核心章节；coverage_na.sections 为 `[{doc: textbook|post, section, reason: 非核心且允许排除的依据}]`，只允许排除真正非核心内容，排除不能掩盖核心遗漏；regions 须有 label 和有效成员节点。`source_revision` 必须等于已登记教材 textbook.txt 的 sha256，finish 时核对，不一致拒绝。人工逐点逐边审阅与预览图渲染检查仍必做（双页逐页走读），字段齐不等于语义覆盖完整；一条"已核对全部"不能当逐点记录，semantic_review 须逐点列出。

## 复核记录最小字段

记录题目、教材版本/hash、核验资料、代码执行环境及覆盖、未验证外部服务、图节点边清单、正常/失败路径走查、教材字数（含是否落在 5000—10000 默认区间）与超长理由（仅当教材超过 10000 字时必填）、发布正文字数（是否落在 1500）、封面单元数量（1—2；双单元须记录拆分理由、两页分工与各自 page 落点）、封面尺寸（逐单元竖版 3:4 与导出像素核对结果）与失败分支检查结果、渲染检查（双页逐页）、修复项、剩余限制。不得把“生成成功”写成“测试通过”。
