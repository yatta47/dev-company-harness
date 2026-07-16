---
name: work-status
description: 現在アクティブなタスクの状態、担当ロール、必要成果物、次工程、ゲートの合否を表示する。
disable-model-invocation: true
allowed-tools: Bash, Read
---

「今どうなってる？」「次なにが必要？」で呼ばれる。タスクIDの指定は不要。

```bash
python3 -m harness.cli status
python3 -m harness.cli validate
```

`status` で state・owner・expected_artifacts・next_state を、`validate` で**今のゲートが通るか**を示す。「どこにいるか」だけでなく「次へ進むのに何が足りないか」まで答えるのがこのスキルの仕事。

報告に含めるもの:
- タスクID・タイトル・対象repo
- 現在工程と担当ロール（dod=secretary / researching=researcher / planning=architect / implementing・packaging=developer / reviewing=reviewer）
- 期待成果物と、それが揃っているか
- validate の errors / warnings（errors があれば**何を直せば通るか**を具体的に）

アクティブタスクがない場合は `/work-list` または `/work-init` を案内する。`approved` なら「ローカルコミット済み・push待ち」であること、**push は人間の操作**であることを明示する。

## 役割境界
- **する**: 現状とゲート合否の報告のみ（読み取り専用）。
- **しない**: 成果物の作成、advance、差し戻し（→ `/work-advance` `/work-run` `/work-return`）。
