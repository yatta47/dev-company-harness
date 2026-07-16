---
name: work-list
description: 登録済みの開発タスクのID・タイトル・対象repo・現在工程・更新時刻・アクティブ状態を一覧表示する。
disable-model-invocation: true
allowed-tools: Bash, Read
---

「今どのタスクがある？」「何が止まってる？」で呼ばれる。

```bash
python3 -m harness.cli list
```

結果を読みやすい表にする。アクティブタスクには `*` を付ける。

表示項目:
- Active
- Task ID
- Title
- Repo
- State
- Updated

1件もない場合は `/work-init` を案内する。`approved` / `abandoned` は終端なので、進行中のものと視覚的に分けて示す。ユーザーが「次どれやる？」と聞いたら、状態と更新時刻から止まっているものを指摘してよい。

## 役割境界
- **する**: 一覧の表示のみ。
- **しない**: 切り替え（→ `/work-switch`）、状態の詳細（→ `/work-status`）、工程を進めること。
