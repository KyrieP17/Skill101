# Approval card: what the agent shows the user

Fill this in from the `approval-card.json` that `scope-validate --approval-card` wrote. Show it once in chat and ask one question with three answers: **批准 / 要改 / 不批准** (use your question tool if you have one, with the card in the question or its preview, because the user may see only the dialog). Then stop until the user answers.

`<python>` is the interpreter from the skill's `config.json`, `<skill>` the skill folder. Paths after the command are relative to the project, because the wrapper runs there.

---

**Scope `<scope_id>` v`<k>`: `<product_name>`**, HTS`<level>`, release `<hts_release>`
`scope_sha256` `<first 12 characters>…` (confirmation prefix `<first 8>`)

| Decision | HTS | Description | Unit | Valid | Why |
|---|---|---|---|---|---|
| include | `<code>` | `<description>` | `<unit>` | `<from>`–`<to>` | `<rationale>` |
| exclude | `<code>` | `<description>` | | `<from>`–`<to>` | `<rationale>` |

- **Quantity**: `<one unit, e.g. No.: quantity and unit value are comparable>` or `<mixed or missing: quantity and unit value will not be shown>`
- **Second unit**: `<kg on every included line: not fetched, kept for later analysis>` or `<none>`
- **HTS continuity**: `<已核实, from hts-history.json>` or `<连续性未核实: why>`. Approving an unverified scope approves that line too, and every report will say so.
- **This scope can answer**: `<...>`. **It cannot**: `<...>`.
- **Open questions that would change the codes**: `<none, or the question>`

Answers: **批准** (when continuity is unverified, label it 批准（连续性未核实）), **要改** (say what), **不批准**.

---

## After the answer

- **批准**: only when this answer is the user's latest message and approves this card. Reviewer is the name the user gave; ask once if you have none. Pass their words exactly as they came (the option label, plus any note they added):

  ```powershell
  & "<python>" "<skill>\scripts\tariff_pipeline.py" scope-approve `
    --scope scopes\<scope_id>\v<k>\reviewed.csv `
    --approval-card scopes\<scope_id>\v<k>\approval-card.json `
    --reviewer "<user's name>" `
    --continuity-unverified `
    --approved-scope scopes\<scope_id>\v<k>\scope.csv `
    --manifest scopes\<scope_id>\v<k>\manifest.json `
    --chat-confirmation "<the user's exact words>" --confirm <first 8>
  ```

  With continuity evidence, replace `--continuity-unverified` with `--history-evidence scopes\<scope_id>\v<k>\hts-history.json`; the card and the command must agree. The manifest records the method (`chat_confirmation`) and the words, and every report says 已批准（对话确认）.
- **要改**: change the reviewed scope; a changed scope gets a new card (`approval-card-2.json`) and a new question.
- **不批准**: stop. Acquire nothing.
- **Terminal instead**: if the project sets `[approval] allow_chat_confirmation = false`, or the user prefers it, give them the same command without the last line to run in PowerShell or cmd (Git Bash: `winpty` in front). It shows the card and asks them to type `<first 8>` (and `UNVERIFIED` when continuity is unverified).
