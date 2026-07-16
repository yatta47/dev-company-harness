---
name: work-advance
description: 現在タスクを担当ロールへ委譲し、ゲート合格後に次工程へ1段だけ進める。
disable-model-invocation: true
allowed-tools: Bash, Read, Agent
---

「次へ進めて」「1段だけやって」で呼ばれる。**1回の呼び出しで1段だけ**進める。承認まで一気に走らせたいときは `/work-run`。

1. `python3 -m harness.cli status` で state・owner・expected_artifacts を得る。
2. 現在状態の owner へ委譲し、期待成果物を作らせる。
   - `dod` = secretary / `researching` = researcher / `planning` = architect
   - `implementing` = developer / `reviewing` = reviewer / `packaging` = developer
3. 対象repo の verify を走らせる: `python3 -m harness.cli verify`
   - `--check` を付けない。registry に定義された全チェックを走らせる（決め打ちすると repo によって詰まる／不可逆検出用のチェックが走らない）。
4. `python3 -m harness.cli validate`。不合格なら owner に直させ、**通るまで advance しない**。
5. 合格時だけ `python3 -m harness.cli advance --by user`。
6. 新しい状態・次に必要な成果物・未解決事項を報告する。

## 安全装置

- **`verify.json` の `requires_human: true`（不可逆な変更を検知）なら advance せず停止し、検知した文字列とともに人間へ返す。** registry の advisor 招集は任意ではない。
- `approved` は終点。**push はしない**（ハーネスに push 経路はない）。
- 事実確認（ユーザーしか知らない意向・可否）が必要なら、勝手に仮決めせず停止して聞く。

## 役割境界
- **する**: 1工程だけ進める。判断は人間（`--by user`）。
- **しない**: 承認までの連続実行（→ `/work-run`）、差し戻し（→ `/work-return`）、push。
