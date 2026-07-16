---
name: work-init
description: やりたいことと対象リポジトリから新しい開発タスクを開始し、DoD(完了の定義)を確定させる。
argument-hint: "<やりたいこと>（対象repo名やDoDファイルのパスを含めてよい）"
disable-model-invocation: true
allowed-tools: Bash, Read, Agent, AskUserQuestion
---

`$ARGUMENTS` はタスクIDでも確定タイトルでもなく、ユーザーがやりたいことの自由入力である。「認証まわりのリトライを直したい」「このDoDで進めて: ~/notes/dod.json」のように来る。

## 対象リポジトリの決め方

`--repo` は **workspace registry の `name`**（パスではない）。入力に repo 名が含まれなければ `config/workspace-registry.toml` の `[[repos]]` を読み、`write = "agent-commit-only"` のものだけを候補として AskUserQuestion で選ばせる。読み取り専用 repo を指定するとタスク作成時点で落ちる。ユーザーにパスを考えさせない。

## タスク作成

仮タイトル（20〜60文字程度の日本語）と内部ID（短いASCII kebab-case）はこちらで作る。ユーザーに内部IDを考えさせない。

```bash
python3 -m harness.cli init --seed "<やりたいこと>" --repo "<registry名>" --title "<仮タイトル>" --slug "<内部ID>"
```

作成されたタスクは自動的にアクティブになる。初期状態は `dod`、owner は secretary。

## DoDの2モード（ここが芯）

**提供モード**: ユーザーがDoDファイルを渡した場合。内容を `tasks/<ID>/dod.json` に取り込み、`confirmed_by: "supplied"` / `confirmed_at` を入れる。人間が既に書いたものなので確認ゲートは不要 → 即 `/work-run` で自律可。

**対話モード（既定）**: 秘書がドラフトを書き、**人間が確定する**。

1. secretary へ委譲し、seed から `dod.json` のドラフトを作らせる（`title` / `repo` / `problem` / `acceptance_criteria` / `verification` は必須。`scope` / `out_of_scope` / `constraints` / `rollback` も埋める）。
2. 曖昧な点は AskUserQuestion で詰める。特に **acceptance_criteria と out_of_scope** を優先して聞く。
3. ドラフトをユーザーに提示し、確定を取る（confirmゲート）。確定できたら `confirmed_by: "user"` / `confirmed_at` を書き込む。
4. **秘書は自分のドラフトを自分で confirm できない**。`confirmed_by` が `user` / `supplied` 以外だとゲートが落ちる。これは仕様であり、迂回しない。

## acceptance_criteria は観測可能な事象で書く

ゲートが機械的に弾く。「きれいにする」「適切に改善」「ちゃんと動く」「robust に」は落ちる。**何が起きたのを目で見て確認できるか**を書く。

- ✅ `hello.txt に2行目が存在する` / `npm test が exit 0` / `POST /login が429時にRetry-Afterを返す`
- ❌ `コードをきれいにする` / `パフォーマンスを最適化する` / `使いやすくする`

書けたら `python3 -m harness.cli validate` で確認し、落ちたら文言を直す。通ってから報告する。

## 報告

内部コマンドではなく、**仮タイトル・タスクID・対象repo・現在工程・DoDの確定状況**を報告する。次の一手（`/work-run` で自律、`/work-advance` で1段ずつ）を1行添える。

## 役割境界
- **する**: タスク作成とDoD確定まで。
- **しない**: 調査・実装・工程を進めること（→ `/work-advance` `/work-run`）。
