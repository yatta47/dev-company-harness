---
name: work-run
description: 現在タスクを、秘書(secretary)を整合ゲートにして承認(approved)まで自律的に進める。push は行わない。
argument-hint: "(不要。アクティブタスクが対象)"
disable-model-invocation: true
allowed-tools: Bash, Read, Agent, AskUserQuestion
---

アクティブタスクを、各境界で secretary を判断の代理として `approved` まで自律的に進める。**push は絶対に行わない**（外の世界へ出す操作は人間のもの）。

DoD が確定していること（`confirmed_by` が `user` / `supplied`）が前提。未確定なら停止して `/work-init` へ戻す。秘書は自分のドラフトを confirm できない。

## ループ

`python3 -m harness.cli status` の state が `approved` になるまで繰り返す。

1. **状態確認**: `python3 -m harness.cli status` で state・owner・expected_artifacts を得る。
2. **終端判定**: `approved` なら停止し、「ローカルコミット済み・push待ち」であることをユーザーへ報告して終了する。`abandoned` も終端。
3. **委譲**: 現在状態の owner へ委譲し、期待成果物を作らせる（`researching` = researcher / `planning` = architect / `implementing`・`packaging` = developer / `reviewing` = reviewer）。registry に `advisors` があり、設計・レビューで論点になるなら招集する。
4. **verify（ゲートは repo 自身の exit code）**: `python3 -m harness.cli verify`
   - `--check` を付けない。registry の `[repos.verify]` に定義された**全チェック**を走らせる。チェック名を決め打ちすると、その repo に無いチェック（例: terraform repo に `test` は無い）で詰まり、逆に不可逆を検出する `plan` が一度も走らないという事故が起きる。
   - 失敗したら owner に直させ、再実行する。エージェントの自己申告で代替しない。
5. **機械ゲート**: `python3 -m harness.cli validate`。不合格なら owner に直させ、**通るまで advance しない**。
6. **不可逆検知（交渉不可）**: `verify.json` の `requires_human: true` なら、**confidence に関係なく必ず停止して人間に返す**。検知した文字列（`irreversible_hits`）と該当箇所を提示し、registry の advisor を招集する（任意ではない）。秘書の代理承認を禁止する。停止時も 8 の記録を残す（`--ask-user true`）。
7. **整合ゲート（secretary）**: secretary エージェントを呼ぶ。渡すもの:
   - 問い: 「この `<state>` の成果物を承認し、次工程へ進めてよいか」
   - タスクID・現在状態・成果物ファイル・`dod.json`（problem / acceptance_criteria / out_of_scope）・verify と validate は pass 済みである事実
   - この境界に、ユーザー本人しか決められない fork（事実確認・意向・価値観衝突）が含まれていないか
   - secretary は JSON（`recommendation` / `confidence` / `ask_user` / `risk` / `reversible` / `matched_principles` / `evidence`）を返す。
8. **分岐**:
   - `ask_user=true`（事実確認 / 高影響 / 不可逆 / 価値観衝突 / confidence<0.80 / 反証あり）なら **停止**。その問いだけを secretary の所見付きでユーザーへ提示し、回答を待つ。
   - 採用条件を**すべて**満たすときだけ advance: `confidence>=0.80` / `ask_user=false` / low-risk / 可逆 / profile に根拠がある / 事実確認でない。
9. **記録（毎境界で必須。ログを育てる目的）**:
   - 実際の判断 fork があったかで `--kind` を**正直に**付ける（`judgment` = 価値観で決めた本当の分岐 / `mechanical` = ゲート通過のみ）。区別しないと育成ログが薄まる。
   - `--principle` には secretary が依拠した profile の判断軸をそのまま入れる（複数可）。
   ```bash
   python3 -m harness.cli decision \
     --actor secretary --stage "<state>" \
     --question "<この工程を承認して次へ進めてよいか>" \
     --decision "advance: <state> -> <next>" \
     --confidence <c> --ask-user <true|false> --risk <low|medium|high> --reversible <true|false> \
     --kind <judgment|mechanical> \
     --principle "<判断軸1>" --principle "<判断軸2>" \
     --reason "<共有可能な短い根拠>"
   ```
10. **advance**: 採用時のみ `python3 -m harness.cli advance --by secretary`。history に `by: secretary` が残り、人の承認と代理承認を区別できる。
11. **approve の特別扱い（`packaging -> approved`）**: secretary が行ってよいが、9 の decision に **必ず `--basis`** を付け、何を根拠に approve したかを具体的に残す（verify が全pass / acceptance_criteria を充足 / critical・high の指摘が open でない / 作業ツリーが clean 等）。
12. 1 に戻る。

## Observability（毎エージェント実行後に記録）

owner・advisor・secretary を問わず、**エージェントを呼び終えるたびに** usage を記録する。`Agent` の結果に出る `subagent_tokens` / `tool_uses` / `duration_ms` をそのまま渡す。これが工程別のコスト・所要時間・手戻りの可視化データ（`python3 -m harness.cli metrics`）を育てる。

```bash
python3 -m harness.cli event --type agent_run \
  --stage "<state>" --role <secretary|researcher|architect|developer|reviewer> \
  --tokens <subagent_tokens> --tool-uses <tool_uses> --duration-ms <duration_ms> \
  --attempt <1=新規 / 2以上=差し戻し後の再実行> \
  --outcome <completed|returned_midtask|error>
```

`validate` / `advance` / `return` / 不可逆検知の構造イベントは CLI が自動記録する（手入力不要）。

## 絶対に守る境界

- **`approved` を超えない。** ハーネスに push 経路は無い。push は人間。
- **`requires_human: true` なら必ず停止。** 自信度と交換できない。エージェントが単独で「取り返しのつかないこと」を決めないための一点であり、他はすべてその手段。
- **事実確認**（ユーザー本人しか知らない意向・可否・実名の扱い等）は必ず `ask_user=true` で人間へ。secretary は事実を検証できない。
- verify / validate 不合格を解消できないまま advance しない。ゲートを緩めて通さない。

## 役割境界
- **する**: `approved` までの自律実行と、境界ごとの判断・記録。
- **しない**: push（人間）、DoDの確定（→ `/work-init`）、差し戻し（→ `/work-return`）、打ち切り（→ `/work-abandon`）。
