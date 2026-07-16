# ADR 0001: 執筆ハーネスを fork してリポジトリ作業ハーネスにする

- **Status**: Accepted
- **Date**: 2026-07-16
- **Fork 元**: `writing-company-claude-harness`（執筆ハーネス）
- **関連**: `loop-harness`（`docs/architecture.md` の §6 push 多層防御、コスト打切り）

## 凡例（このADRのラベル）

このADRは**事実・判断・根拠を分離**する。半年後に読む人が「どれが確かめた話で、どれがまだ確かめていない話か」を再調査せずに区別できることを目的とする。

| ラベル | 意味 |
|---|---|
| **[実測]** | 2026-07-16 に手元で動かして確認した。断りがなければ `cursor-agent 2026.07.09-a3815c0` |
| **[公式]** | ベンダー公式ドキュメント由来。出典URLを併記する |
| **[未検証]** | まだ確かめていない。**そう動くはずという想定に過ぎない** |

---

## Context

### 出自

執筆ハーネス `writing-company-claude-harness` は、次の骨格を持つ。

- 状態機械（1状態 = 1オーナー）
- 機械ゲート（状態遷移の可否をプログラムが判定する）
- subagent 委譲（`.claude/agents/` のロール定義）
- PreToolUse hook（書き込みの構造的ブロック）
- Self-Mirror（オーナーの判断代理）
- observability（イベントログ）

この骨格を「リポジトリでの開発作業」に転用する。**会社リポジトリでの利用が最終ゴール**であり、個人リポジトリでも使う。

### 決定的な制約

**会社PCでは Claude Code が使えない（未承認）。Cursor のみが正式提供**。

したがってこのハーネスは、Claude Code 専用では要件を満たさない。**同じハーネスが Cursor CLI (`cursor-agent`) 上でも成立する必要がある**。以降のほぼ全ての判断がこの制約から導かれる。

### 今日、前提が2回ひっくり返った

このADRを書く最大の理由がこれ。記録がないと同じ調査を必ずやり直す。

| # | 当初の前提 | 実際 | 根拠 |
|---|---|---|---|
| 1 | Cursor には subagent も hook も無いだろう。Claude Code 用の資産は Cursor に持ち込めない | **両方ある**。`.claude/agents/` と `.claude/settings.json` の hooks をそのまま読む | [公式] + [実測]（下記 Phase 0） |
| 2 | 共有できるものは symlink で `.claude/` と `.cursor/` の両方から見えるようにする | **symlink は不要**（判断1） | #1 の帰結 |

前提2は前提1の帰結である。**Cursor が `.claude/` を実体のまま読む以上、`.cursor/` へ向けた symlink は間接参照を増やすだけ**になった。

---

## Phase 0: 実測結果（2026-07-16）

ここがこのADRで一番重要な節。**事前調査での最大リスクは「forum に、CLI は shell 系 hook しか発火しないという報告がある」だったが、現行ビルドでは誤りだった**。

### 1. Cursor は `.claude/agents/` を subagent 定義として読む

- **[公式]** cursor.com/docs/subagents — 「Project subagents | `.claude/agents/` | Current project only (Claude compatibility)」。かつ「You can use subagents in the editor, CLI, and Cloud Agents」
- **[実測]** 執筆ハーネスの5ロール（`editor` / `researcher` / `reviewer` / `self-mirror` / `writer`）が Cursor CLI から列挙された。Cursor builtin の `generalPurpose` / `cursor-guide` / `bugbot` / `security-review` / `best-of-n-runner` と並んで見えている

### 2. Cursor は `.claude/settings.json` の hooks を読み、PreToolUse が発火する

- **[公式]** cursor.com/docs/reference/third-party-hooks — `PreToolUse` → `preToolUse` = Yes
- **[実測]** `approved` 状態の `draft.md` に対する編集が `guard_writes.py` によってブロックされた。**日本語の deny メッセージがそのまま agent に届き**、ファイルの md5 は不変だった

つまり「ハーネスの安全境界を hook で構造的に強制する」という設計が、Cursor 上でもそのまま成立する。**これは指示による禁止ではなく、構造による禁止が会社PCでも効くという意味**であり、判断3・判断7の前提になっている。

### 3. SessionEnd hook も発火する

- **[実測]** 会話が `.self-mirror-private/` にキャプチャされた

---

## Phase 0 で見つかったバグ（修正済み）

**症状**: SessionEnd hook は発火し、transcript の場所も正しく特定するのに、**抽出結果が常に0件**だった。

**原因**: `role` の位置が両者で違う。

```
Claude Code  {"type":"user", "message":{"role":"user", "content":[...]}}
Cursor       {"role":"user", "message":{"content":[...]}}     # role がトップレベル
```

`scripts/transcript_utils.py` が `msg.get("role")` しか見ておらず、Cursor では `role=None` になる。続く type フォールバックも **Cursor には `type` が無いため効かず**、全メッセージが捨てられていた。

**修正**: フォールバック先に `obj` 側の role を加える。

```python
role = msg.get("role") or obj.get("role")
```

- **[実測]** 両形式で 2/2 件の抽出を確認（Claude Code 形式は挙動不変）
- 修正は fork 元 `writing-company-claude-harness` 側に commit 済み（`dec760e`）

> **この種のバグは今後も出る**。Cursor は「Claude 互換」を謳うが、互換なのは**ファイルの置き場所と発火タイミング**であって、**ペイロードの構造まで同一ではない**。Claude Code の transcript 形式に依存するコードを書いたら、Cursor 側で必ず実測すること。

---

## 判断1: symlink しない

**決定**: `.claude/` の実体1か所で両ネイティブを成立させる。`.cursor/` への symlink・併置はしない。

**根拠**:

- Cursor が `.claude/` を実体のまま読む（Phase 0）以上、symlink は**間接参照を1段増やすだけで、得るものがない**
- **[実測]** `.cursor/` は repo に作られてすらいない
- 同じ結論に Codex で既に到達済み（「`.claude/skills/` 実体1か所で両ネイティブが成立。symlink・`.agents/skills` 併置は不要」）。**Cursor は Codex と違い公式ドキュメントに明記しているぶん、さらに安全**

**ただし symlink が無意味なわけではない。切る軸が違う**。ここを混同すると議論が再燃する。

| 軸 | symlink | 理由 |
|---|---|---|
| **agent 軸**（claude / cursor） | **不要** | 両者が同じ `.claude/` を読む。分ける対象が存在しない |
| **環境軸**（会社 / 個人） | **有効** | registry・profile は環境ごとに中身が変わる。切り替えたいのはこちら |

---

## 判断2: public リポジトリにする、ただし framework と config を分離する

**決定**: framework は public、config と作業実体は repo 外 / gitignore。

**動機**: 会社PCで private repo を clone するには `gh` 認証が要る。**会社PCに個人のGitHub認証情報を置きたくない**。public なら素の `git clone` で済む。

**しかし writing 版の構造をそのまま public にすると漏れる**。writing 版は `articles/` と `self-mirror/profile.md` を commit する前提で作られている。そのまま持ってきてはいけない。

| 区分 | 中身 | 扱い |
|---|---|---|
| **framework** | harness CLI・workflows・schemas・ロール指示・advisor | **public** |
| **config** | registry・profile・tasks・decisions・events | **gitignore / repo 外** |

**profile.md の実体は repo の外に置く**: `~/.config/dev-harness/profile.md`（環境変数 `DEV_HARNESS_PROFILE` で上書き可）。

> 理由は明確に。**gitignore は編集できるが、repo 外のファイルは物理的に commit できない**。前者はお願い、後者は構造。`secretary/profile.md` に対する gitignore エントリは、誰かがここに置いてしまった場合の defence in depth として残してある。

### 漏洩ゲートは3層

`.github/workflows/gitleaks.yml` に3 job として実装済み。

| job | 検知対象 | 備考 |
|---|---|---|
| `gitleaks` | 秘密情報（トークン等） | repo-root の `.gitleaks.toml` を自動検出 |
| `home-path-check` | 絶対パス `/home/<user>/` | `~/...` は許可。gitleaks の default allowlist が汎用 unix 絶対パスを握り潰すため、**素の grep で別 job にしてある** |
| `config-leak-check` | **作業文脈**（tasks / registry / profile / decisions / events / 会話キャプチャ） | 下記 |

3つ目が肝。**`.gitignore` は `git add -f` で迂回できるお願いでしかない**。よって `config-leak-check` は**ルールを信じず、結果を検査する**（「その種のファイルが tracked になっていないこと」を assert する）。gitignore を弱めると CI が落ちる。

- **[実測]** 違反検知は確認済み

---

## 判断3: 外界ラインは push。`approved` = ローカルコミット済み

**決定**:

| 状態 | 意味 | 誰が |
|---|---|---|
| `approved` | **ローカルコミット済み・push 待ち** | ハーネス |
| `pushed` | 外界に出た | **人間のみ** |

執筆版は `published`（記事公開）が人間専用だった。その境界を **push** に移す。

**ハーネスは push 経路を持たない**。既存の loop-harness ADR も同じ結論に到達している（`produce(agent = commit まで) ≠ publish(人間 = push)`）ので、**その多層防御をそのまま流用する**。

loop-harness `docs/architecture.md` §6 の層構成（**層1+2+3は構造、層4は指示による補強**。最低でも1+2+3を全部）:

| 層 | ガードレール | 性質 |
|---|---|---|
| 1 | agent 環境に会社 remote への到達手段を置かない（認証情報なし＋origin を会社に向けない） | 構造 |
| 2 | work repo に `pre-push` フック（無条件 block） | 構造 |
| 3 | executor のコマンド allowlist で `git push` を拒否 | 構造 |
| 4 | 役割指示「push するな」 | 補強のみ |

> 層4だけに頼らないことが要点。**指示は破れるが、認証情報が無い環境は破れない**。

---

## 判断4: ゲートを exit code ベースに作り替える

**これが執筆版から一番大きい差分**。

| | 執筆版のゲート | 開発版のゲート |
|---|---|---|
| 判定 | 「`draft.md` が500文字以上」「`[TODO]` が残っていない」 | **repo 自身のコマンドの exit code** |

**根拠**: 執筆版のゲートは開発では**無意味**。**500文字の diff が正しい保証はゼロ**。正しさを決めるのは `make test` の exit code であって、量ではない。

**実装**: `config/workspace-registry.yml` の `verify:` に repo ごとの build / lint / test コマンドを宣言し、その exit code をゲートにする。

```yaml
verify:
  lint: "npm run lint"
  build: "npm run build"
  test: "npm test"
```

キーを省けばそのチェックはスキップ（lint の無い repo も正常）。コマンドは repo root を cwd として実行する。

> 実例の注意（`config/workspace-registry.example.yml` に記載済み）: `terraform plan -detailed-exitcode` は 0 = 差分なし / 2 = 差分あり / 1 = エラー。**ハーネスは 1 を失敗、0/2 を pass として扱う**。「差分がある」は異常ではなく通常だから。

---

## 判断5: ロールを2層に分ける（工程オーナー と advisor）

**決定**: 状態を持つ「工程オーナー」と、状態を持たない「advisor」を分ける。

**根拠**: 状態機械は **1状態 = 1 owner**。`aws-latest-architect` のようなドメイン専門家は「工程のオーナー」ではなく「必要時に呼ぶ相談役」であり、**軸が違う**。混ぜると `STAGE_OWNER` が壊れる。

| 層 | メンバー | 状態 |
|---|---|---|
| **工程オーナー** | `secretary` / `researcher` / `architect` / `developer` / `reviewer` | 持つ |
| **advisor** | `aws-latest-architect` / `terraform-latest-advisor` / `proposal-auditor` | **持たない** |

**advisor は registry の `advisors:` で repo ごとに有効化する。無条件に毎回呼ばない**（会社PCの Cursor は従量課金。毎回呼ぶと課金が膨らむ）。

**呼ぶトリガ**は registry の `irreversible_markers`。verify 出力にこれらの文字列が現れたら、その変更は何かを破壊 / 再作成する。

```yaml
irreversible_markers:
  - "forces replacement"
  - "must be destroyed"
  - "will be destroyed"
```

マッチ時の帰結は2つ、いずれも交渉不可:

1. 列挙された advisor を**必ず**呼ぶ（「agent がその気になれば」ではない）
2. `reviewing` → `packaging` の境界が **`ask_user=true` 固定**になる。**秘書の代理承認を禁止する**（confidence がいくら高くても）

> これがハーネス全体の芯。**agent が単独で、何かを破壊する判断をしてはならない**。他は全部そのための手段。

---

## 判断6: advisor は life-project から二重管理でコピーする

**決定**: advisor の実体を dev-company-harness にコピーして自己完結させる。**二重管理は許容する**。

**根拠**:

- 実体は life-project の `.claude/agents/` にあり、`references/common-base-rules.md` を**相対パスで参照している**
- **会社PCには life-project が存在しない**。したがって cross-repo symlink は**原理的に壊れる**（判断1の「環境軸」に該当するケース）

**正典は dev-company-harness 側**とする。理由: **会社でも使う＝実戦で鍛えられる側**だから。life-project 側は古くなったらコピーし直す。

- **[実測]** 検査済み: advisor 3本 + references 2本に固有情報（IP・内部ドメイン・社名等）は**ゼロ**。public に出せる

---

## 判断7: 会社PCでは profile を read-only にする

**決定**: 秘書（`secretary`）の profile は、**母艦と同じ「濃い版」を会社PCでも使う**。ただし**会社PCでは読むだけ**にする。

**受け入れたリスク**（オーナーがリスクを理解した上で選択。判断代理の精度を優先した）:

> 個人の判断軸が会社PCに置かれ、**Cursor 経由で LLM ベンダーに毎回送信される**。

**ただし逆方向（会社 → 個人）の汚染は塞ぐ**。ここが非対称なのが重要。

profile は**育つ設計**（`candidates/` + mirror-sync + 会話キャプチャ）である。会社PCで育てると、次の経路が開通してしまう:

```
会社の業務内容 → profile → 母艦 → public repo / 記事
```

**対策**: 会社PCでは以下を**無効化**する。

- mirror-sync
- candidates 生成
- 会話キャプチャ

profile の育成は**母艦のみ**。会社PCで見つけた判断軸は、**会社情報を落としながら手で持ち帰る**。

| | 母艦（個人） | 会社PC |
|---|---|---|
| profile を読む | ✓ | ✓（同じ濃い版） |
| profile を育てる | ✓ | **✗** |

---

## 状態機械の写像

**工程の状態名の差分は4つだけ**（`briefing`→`dod`、`outlining`→`planning`、`writing`→`implementing`、`finalizing`→`packaging`）。`researching` / `reviewing` / `approved` は改名なし。加えて終端の `published`→`pushed`（意味も変わる。判断3）。

**workflows の移植がほぼ機械的になるよう意図的に揃えた**。

| 執筆版 | 開発版 | owner | 通常の開発フローで言うと |
|---|---|---|---|
| `briefing` | `dod` | secretary | 要件定義（ドラフト→人間 confirm） |
| `researching` | `researching` | researcher | 現状調査 |
| `outlining` | `planning` | architect | 設計 |
| `writing` | `implementing` | developer | 実装 |
| `reviewing` | `reviewing` | reviewer | レビュー・テスト |
| `finalizing` | `packaging` | developer | PR 準備 |
| `approved` | `approved` | orchestrator | **ローカルコミット済み**（判断3） |
| `published` | `pushed` | **人間** | |

### なぜ `dod` の owner が secretary なのか

DoD ドラフトが **loop-harness の秘書役の定義そのもの**だから。

> 秘書役 = 判断プロキシ（DoD ドラフト = refine ＋ 実行時トリアージ ＋ 成果物プレチェック）

### DoD の2モード（排他ではない）

| モード | 流れ |
|---|---|
| **対話** | 秘書がドラフト → **人間 confirm ゲート** |
| **提供** | `--dod file` → 即自律 → `approved` まで |

**DoD ゲートの芯は「acceptance_criteria が観測可能な事象になっているか」**。文字数でも網羅性でもない。

---

## Consequences

### 得たもの

- 会社PC（Cursor のみ）と母艦（Claude Code）で**同一のハーネスが動く**。ロール定義・hook・ゲートを二重に書かなくてよい
- ゲートが exit code になり、**「通った」が検証可能な事実**になった（判断4）
- 不可逆操作に対して、**agent が単独で承認できない構造**ができた（判断5）

### 払ったもの / 抱えたリスク

- advisor の**二重管理**を受け入れた（判断6）。同期は手動であり、ズレる
- 個人の判断軸が会社PCに置かれ、**LLM ベンダーに送信される**（判断7、受容済み）
- Cursor の互換性は**ファイルの置き場所と発火タイミングまで**であり、**ペイロード構造は同一ではない**（Phase 0 のバグ）。Claude Code 側で書いたコードは Cursor で実測しない限り動く保証がない

---

## 未解決 / 今後

### Phase 1 で対応する

| 項目 | 内容 |
|---|---|
| **`abandoned`（打ち切り）経路が無い** | 状態機械が**必ず `approved` まで行く前提**になっている。これは執筆由来の欠陥（**記事は書き切るものだから成立していた**）。開発では「調査したら、やらないほうがいいと分かった」が頻出する。**足す** |
| **コスト停止条件が無い** | mode2（DoD を渡して approve まで自律）は**実質無人**で、会社PCの Cursor は従量課金。loop-harness は既に持っている（`MAX_DAILY_RUNS` / `MAX_DAILY_DURATION_MS`）。**Cursor は token / cost を出力しないので、時間と run 数で代理する**という結論まで出ている（loop-harness `docs/architecture.md` §12、2026-07-02 確認）ので、それを流用する |

### 未検証（**そう動くはずという想定に過ぎない**）

| 項目 | 内容 |
|---|---|
| **advisor / researcher のツール制限** | **[実測]** Cursor subagent に **`tools:` キーが無い**（Cursor が持つのは `name` / `description` / `model` / `readonly` / `is_background`）。Claude Code の「researcher に Write を渡さない」というツール制限が**そのままでは移らない**。**[未検証]** `readonly: true` で代替する想定 |
| **ロール別モデル割当** | **[実測]** 無料枠では named model が使えない（`Free plans can only use Auto`）。したがって Cursor subagent の `model:` によるロール別モデル割当は**無料枠では効かない**。**[未検証]** 会社PCは有料従量なので効くはずだが、確かめていない |
| **会社ネットワークからの到達性** | **[未検証]** 会社のネットワークが github.com に到達できるか**未確認**。**public にしても到達できなければ clone できない**（判断2の前提が崩れる） |

### Phase 2 送り

- 分割（親 epic / 子 DoD）
- `security-review` advisor
- テスト有無のゲート
