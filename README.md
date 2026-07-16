# dev-company-harness

Claude Code / Cursor CLI 上で、リポジトリでの開発作業を **状態機械** として進めるハーネス。

`DoD確定 → 調査 → 設計 → 実装 → レビュー → PR準備 → 承認` の各工程は、**そのリポジトリ自身の build / lint / test の exit code** を通らないと次へ進めません。ゲートはハーネスが定義した独自基準ではなく、あなたのリポジトリが既に持っている「壊れていないことの定義」です。

> **Status: 🚧 実装途上。**
> 現時点で動くのは **安全機構（.gitignore / .gitleaks.toml / 3層CI）と workspace registry のサンプル** だけです。
> **状態機械も CLI もロール定義もまだありません。** 以下の設計は「これから何になるか」であって、今 clone しても作業は回りません。
> 何が実装済みで何が未実装かは [ロードマップ](#ロードマップ) を参照してください。
> 安全機構を先に作ったのは意図的です — 漏洩ゲートが無い状態で中身を足すと、最初のコミットが事故になります。

## 設計の芯

**エージェントが単独で「取り返しのつかないこと」を決めない。** 他のすべては、この一点のための手段です。

- **ハーネスは push 経路を持たない。** `approved` は「ローカルコミット済み・push待ち」を意味します。外の世界へ出す操作は人間のものです。
- **破壊的変更を検知したら人間に返す。** Terraform の `forces replacement`、`DROP TABLE` などを verify 出力に見つけた時点で、秘書(secretary)の代理承認を**禁止**します。エージェントの自信度は関係ありません。

## 状態機械

```
dod → researching → planning → implementing → reviewing → packaging → approved ┃ pushed(人間)
```

| 状態 | owner | やること | ゲート |
|---|---|---|---|
| `dod` | secretary | DoD確定（対話 or 提供） | acceptance_criteria が観測可能な事象になっているか |
| `researching` | researcher | 現状調査 | 主張に `file:line` の根拠があるか |
| `planning` | architect | 設計 | 変更対象・手順・ロールバックがあるか |
| `implementing` | developer | 実装 | registry の `verify.lint` / `verify.build` が通るか |
| `reviewing` | reviewer | レビュー・テスト | `verify.test` が通り、critical/high の指摘が open でないか |
| `packaging` | developer | PR本文・コミットメッセージ | Issue参照があるか |
| `approved` | — | ローカルコミット済み | 作業ツリーが clean かつ未push |

`pushed` はハーネスの状態ではありません。人間の操作です。

## ロールは2層

**工程オーナー**（状態機械の owner。1状態 = 1人）

`secretary` / `researcher` / `architect` / `developer` / `reviewer`

**advisor**（状態を持たない相談役。必要時のみ呼ぶ）

`aws-latest-architect` / `terraform-latest-advisor` / `proposal-auditor`

advisor は registry の `advisors:` でリポジトリごとに有効化します。**無条件に毎回呼びません**（Cursor は従量課金のため、相談役の常時起動はそのままコストになる）。ただし破壊的変更を検知した場合、対象リポジトリの advisor 招集は任意ではなく必須です。

## DoDの2モード

| モード | 流れ |
|---|---|
| 対話 | 秘書がドラフト → **人間が確定（confirmゲート）** → 以降 `approved` まで自律 |
| 提供 | DoD ファイルを渡す → 即自律 → `approved` まで |

どちらも終点は `approved` です。自律の範囲は「push の手前まで」で固定されています。

## Framework vs. your config

このリポジトリは public ですが、駆動する対象は **private な作業** です。**framework は public、あなたの config と作業は public ではありません。**

| 中身 | public | 置き場所 |
|---|---|---|
| harness CLI・状態機械・schemas・ロール指示・advisor | ✅ | この repo |
| workspace registry（実リポジトリ名とパス） | ❌ | `config/workspace-registry.yml`（gitignore） |
| secretary の profile（あなたの判断軸） | ❌ | **repo の外**: `~/.config/dev-harness/profile.md`（`DEV_HARNESS_PROFILE` で上書き可） |
| `tasks/`（DoD と成果物 = 実際の業務内容） | ❌ | `tasks/`（gitignore） |
| 判断ログ・観測ログ | ❌ | gitignore |

**なぜ profile だけ repo の外なのか。** `.gitignore` は編集できるし、`git add -f` で迂回もできます — つまり「commit しない」という約束でしかありません。repo の外にあるファイルは、約束ではなく**物理的に commit できない**。判断軸はあなた自身が最も色濃く出る場所なので、そこだけは仕組みで守ります。

### 漏洩ゲートは3層（CIが強制）

| ジョブ | 検査するもの |
|---|---|
| `gitleaks` | 秘密情報（キー・トークン） |
| `home-path-check` | 絶対パス（`~/` を使うこと。実ユーザー名を含むホームパスは落とす） |
| `config-leak-check` | 作業文脈。**`.gitignore` を弱めるとCIが落ちる** |

`config-leak-check` が見るのは **ルールではなく結果**です。「ignore ルールが書いてあるか」ではなく「そういうファイルが tracked になっていないか」を検査するため、`.gitignore` から行を消しても静かに漏れるのではなく、CI が赤くなります。

## セットアップ

```bash
cp config/workspace-registry.example.yml config/workspace-registry.yml
```

コピー先は gitignore 済みです。ここに実リポジトリ名・パス・verify コマンド・advisor を書きます。パスは `~/...` で書いてください（マシンごとに変わるため、DoD はリポジトリを path ではなく `name` で参照します）。

サンプルには application repo / infrastructure repo / read-only な reference repo の3例と、`irreversible_markers` の意味がコメントで入っています。

profile は repo の外に置きます。

```bash
mkdir -p ~/.config/dev-harness
$EDITOR ~/.config/dev-harness/profile.md
```

> ⚠️ 現時点では registry と profile を置いても、それを読む CLI がまだありません。セットアップだけ先に通ることは確認できますが、作業は回りません。

## 対応エージェント

会社支給マシンで Cursor しか使えない、という制約が出発点にあります。したがって **Claude Code と Cursor CLI の両方で動くことは必須要件**で、下の ✅ は実測です。

| | Claude Code | Cursor CLI |
|---|---|---|
| subagent（`.claude/agents/`） | ✅ | ✅ 実測確認済み |
| PreToolUse hook（`.claude/settings.json`） | ✅ | ✅ 実測確認済み |
| SessionEnd hook | ✅ | ✅ 実測確認済み |
| subagent の `tools:` によるツール制限 | ✅ | ❌ Cursorに該当キーなし（`readonly:` で代替、**未検証**） |
| ロール別モデル割当（`model:`） | ✅ | 有料プランのみ（無料枠は auto 固定） |

**なぜ `.cursor/` が無いのか。** Cursor が `.claude/` を互換ロードするためです。定義を二重に持つと片方だけ更新される事故が起きるので、意図的に `.claude/` へ一本化しています。

**未解決の穴が1つあります。** Cursor 側にツール制限の等価物がありません。Claude Code では `researcher` に書き込みツールを渡さないことで「調査役は実装しない」を機構で保証できますが、Cursor では現状ロール指示（お願い）に留まります。`readonly:` が代替になるかは未検証です。

## ロードマップ

**実装済み**

- 安全機構: `.gitignore` / `.gitleaks.toml` / 3層CI（`gitleaks` / `home-path-check` / `config-leak-check`）
- workspace registry のサンプル（`config/workspace-registry.example.yml`）

**未実装**

- 状態機械と CLI
- ロール定義（工程オーナー5 + advisor 3）
- slash commands
- observability（判断ログ・イベントログ）
- `abandoned`（打ち切り）経路
- コスト停止条件

## 出自

既存の執筆ハーネス（`writing-company-claude-harness`）を「リポジトリ作業ハーネス」に作り替えたものです。漏洩ゲートの設計は `loop-harness` から引き継いでいます。

作り替えで最も変わったのは **ゲートの正体**です。執筆ハーネスのゲートは「ドラフトが500字以上あるか」「`[TODO]` が消えたか」でした。コードではどちらも何も意味しません。ここでのゲートは、あなたのリポジトリ自身のコマンドの exit code です。
