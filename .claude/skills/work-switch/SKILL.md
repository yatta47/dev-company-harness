---
name: work-switch
description: タスクIDまたはタイトルの一部を使って、現在作業するタスクを切り替える。
argument-hint: "<タスクIDまたはタイトルの一部>"
disable-model-invocation: true
allowed-tools: Bash, Read
---

「さっきのリトライの件に戻して」「auth のタスクに切り替え」で呼ばれる。`$ARGUMENTS` は厳密なIDでなくてよい。

```bash
python3 -m harness.cli switch "$ARGUMENTS"
python3 -m harness.cli status
```

切り替え後、タスクID・タイトル・対象repo・現在工程を報告する。

一致が複数ある場合、または一致しない場合は、勝手に1つへ寄せない。`python3 -m harness.cli list` の候補を示して、より具体的な語を求める。

## 役割境界
- **する**: アクティブタスクの切り替えのみ。
- **しない**: タスクの作成（→ `/work-init`）、工程を進めること（→ `/work-advance` `/work-run`）。
