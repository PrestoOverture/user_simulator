### Changed Files
- scripts/build_d1_brief.py — 从已有指标、生成与审查表生成11项指标的D1证据；标准库，2000次对话配对bootstrap，种子20261025。
- outputs/d1_brief.md — 500段真人均值/出现对话与轮次、带95%区间差距、双侧关键词误判/漏判、三条件区间、20/500方向与总表。
- scripts/check_correction_pilot.py — 保留各条件独立账目校验；三条件报告改为共同有效轮次交集，并在交集历史上重算重复指标。
- outputs/correction_pilot/report.md — 全量500段三条件报告，统一4705轮；替换此前按各条件自身有效集计算的报告。
- tests/test_d1_brief.py — 构造基线与C1审核集合不同的三轮数据，验证交集与交集历史重算。
- outputs/correction_pilot/handoff.md — 替换过时的“生成中”交接，记录全量验证与D1交付。

### Validation
- `.venv/bin/python scripts/build_d1_brief.py` — 退出0，11 metrics；common turns=4705；bootstrap=2000，seed=20261025。连续运行两次后用`cmp`比较，逐字节相同。
- `.venv/bin/python -m pytest -q` — 19 passed，退出0，含不同moderated集合交集测试。
- `.venv/bin/python scripts/check_correction_pilot.py` — 退出0；计划各4710轮，baseline/C1/C2各自有效4705/4706/4705轮，moderated为5/4/5；空输出均0；共同有效4705轮、500段。已验证全量anchors、prompt指纹、配对CSV、指标、token账目。
- `.venv/bin/python scripts/check_outputs.py outputs outputs/extended` — 退出0；20/500段，有效201/4705轮；九项产物、样本隔离、轮次、文本/指标、token及报告结构通过。
- `.venv/bin/python -m compileall -q usersim scripts tests` — 退出0。项目无额外声明的build/typecheck命令。
- 按`outputs/correction_pilot/protected_sha256.json`逐项SHA256核对 — 107个文件，差异0；`git status --short`仅包含本次交付文件。

### Success Conditions
- [x] D1脚本退出0、重新生成简报，两次运行逐字节相同。
- [x] pytest通过，构造不同moderated集合时三条件仅使用交集，并验证排除轮次不进入重复指标历史。
- [x] correction pilot与20/500基线输出校验均退出0。
- [x] 简报覆盖metrics_summary全部11项，每项含真人均值/出现对话与轮次、差距95%区间、双侧误判/漏判或“无”、C1/C2区间及20/500方向。
- [x] 修正预跑全量有效轮次/空输出检查完成；报告三条件均值与差距缩小比例使用同一交集；handoff已补齐。

### Risks & Deviations
- PRD未给“足够常见/差距明显”数值阈值；总表提供数字、置信区间是否含零及方向，“是否同时满足”交用户判定，不设研究阈值、不推荐指标。
- 连发条数为均值型指标，出现计数明确按条数>0；字数按字数>0；未将指标改为“连发≥2”。
- 旧规则误判/漏判与修正规则同冻结样本复核分别列出；后者分母仍为旧分层，不解释成当前规则独立准确率。bootstrap不包含测量误差或审核选择偏差。
- 基线与修正预跑当前交集恰为基线有效集4705轮；C1独有有效轮次不进入比较。重复追问按交集重算；不改写任何既有生成或指标文件。
- 本次无模型API调用、无新依赖、无git commit；未修改docs/、misc/、CLAUDE.md、AGENTS.md、data/、outputs/archive/。
- 建议Claude在docs进展中记录D1证据路径、交集口径与阈值待定；本次未直接修改docs。无未完成验证。
