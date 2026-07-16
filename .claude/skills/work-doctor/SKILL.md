---
name: work-doctor
description: ハーネスの構成（workflow・ロール定義・スキル・registry・profile・依存コマンド）を診断する。
disable-model-invocation: true
allowed-tools: Bash, Read
---

「ちゃんと動く状態？」「セットアップできてる？」や、原因不明の失敗が出たときに呼ばれる。

```bash
python3 -m harness.cli doctor
```

`ok: false` の項目があれば、切り分けて説明する。混ぜない。

| 種別 | 例 | 直し方 |
|---|---|---|
| エージェント本体 | `git` / `claude` が PATH にない | 環境側の問題 |
| ハーネス構成 | `workflow` / `claude_settings` / `agents` / `skills` が足りない | この repo の欠損 |
| ユーザー設定 | `registry` / `profile` が無い | 下記のセットアップ |

## セットアップの案内（未設定のとき）

```bash
cp config/workspace-registry.example.toml config/workspace-registry.toml   # registry
mkdir -p ~/.config/dev-harness && $EDITOR ~/.config/dev-harness/profile.md  # profile
```

registry と profile は**意図的に repo の外／gitignore 側**にある。この repo は public であり、実リポジトリ名・パス・判断軸は公開物ではない。`doctor` がこれらを「無い」と言うのは正常な初期状態であって、バグではない。**registry のパスは `~/...` で書く**（実ユーザー名を含む絶対パスはCIで落ちる）。

`agents` / `skills` の数が閾値未満のときは、ロール定義やスキルの未配置を疑う。

## 役割境界
- **する**: 構成の診断と直し方の提示のみ（読み取り専用）。
- **しない**: 設定ファイルの自動生成・自動修復、タスクの状態に関する診断（→ `/work-status`）。
