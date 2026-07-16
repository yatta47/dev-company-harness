---
name: work-return
description: 現在タスクを、問題に応じて適切な前工程へ差し戻す。
argument-hint: "<戻り先と理由>"
disable-model-invocation: true
allowed-tools: Bash, Read
---

「設計からやり直して」「調査が足りてないので戻して」で呼ばれる。`$ARGUMENTS` から戻り先と理由を解釈する。

```bash
python3 -m harness.cli return --to "<state>" --reason "<理由>"
```

戻り先: `dod` / `researching` / `planning` / `implementing` / `reviewing` / `packaging`（現在工程より前のものだけ。前へ戻れない組み合わせはハーネスが弾く）

## 戻り先の選び方

問題の**発生源**まで戻す。手前で止めない。

- 完了の定義そのものがズレていた → `dod`
- 前提や現状認識が間違っていた（根拠が違う） → `researching`
- 方針・変更対象・手順の設計が悪い → `planning`
- 設計は妥当だが実装が違う → `implementing`

## 守ること

- **単なる好みでは差し戻さない**。`--reason` には修正対象と理由を、次の担当が読んで動ける具体さで書く。
- 差し戻しは履歴に残り、`python3 -m harness.cli metrics` で工程別の手戻りコストとして集計される。理由が薄いとこのログが死ぬ。
- 差し戻し後の状態を報告し、誰が何を直すのかを1行で示す。

## 役割境界
- **する**: 前工程への差し戻しと理由の記録のみ。
- **しない**: 前へ進めること（→ `/work-advance` `/work-run`）、タスクの打ち切り（→ `/work-abandon`）。
