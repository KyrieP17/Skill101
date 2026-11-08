# scopes/

每个产品口径一个目录，每个版本一个子目录。**新研究 = 新 scope + 新 study**（study 在 `config/studies/`）；`laptops` 和 `lithium-ion` 是例子，不是模板。

```text
scopes/<scope_id>/v<k>/
  candidates*.csv, *.raw.json  hts_scope_resolver.py 的候选输出；status 单独列出，不当作口径文件
  reviewed.csv                 起草、审核中的口径（review_status = candidate 或 reviewed）
  approval-card*.json          scope-validate 写的审批卡片：没有批准效力，写了就不再改
  history/, hts-history.json   可选：历年 HTS 连续性证据
  scope.csv                    scope-approve 写的已批准口径（带 reviewer、approved_at_utc）
  manifest.json                scope-approve 写的冻结记录：scope_sha256、record_sha256、文件哈希、批准方式、连续性状态
```

规则：

- 列与 skill 里的 `assets/product_scope_template.csv` 相同；校验规则只在 `src/usitc_pipeline/scope.py` 一处定义。
- 只有用户本人批准才算：要么自己在交互终端运行 `scope-approve`、输入 `scope_sha256` 前 8 位；要么在对话里看了卡片后回复批准，agent 再带 `--chat-confirmation "<用户原话>" --confirm <前 8 位>` 运行（manifest 记下方式和原话，报告写"已批准（对话确认）"）。agent 不手写 `approved`、reviewer、批准时间或 manifest。批准是研究决定，不是海关归类裁定。
- 没有历年 HTS 证据也能批准，但只能显式地批：卡片写"连续性未核实"，终端批准时要输入 `UNVERIFIED`，对话批准时卡片和问题里要写明这一条，之后每份报告都会显示。
- 卡片不可变。证据后来补上了，就另写一张新卡片（如 `approval-card-2.json`）。
- 批准后不改 `scope.csv`，也不删 `manifest.json` 重来。编码、日期、单位、纳入或排除有任何变化，都另存为 `v<k+1>`。改过的 CSV 会被 `plan` 和 `acquire --plan` 拒绝。
- 编码变化用多行表示：旧编码写到最后一天，新编码从第一天开始，各自注明审核时的 HTS 版本。
- 用这个口径取到的快照放在 `data/raw/dataweb/scopes/<scope_id>/v<k>/`，元数据里记录 `scope_sha256`。

哪些版本已批准，用 `python run.py status` 看。`laptops/v1/scope.csv` 是早期的候选稿，不再使用；laptop 研究用 `laptops/v2`。
