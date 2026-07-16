# dev-company-harness ROADMAP

このハーネスが存在する理由は1つ — **エージェントが単独で「取り返しのつかないこと」を決めないこと**。
したがって優先順位も、機能の多さではなく **その保証が本当に機構として立っているか** で決まる。

現在地: **層は揃ったが、まだ一度もエージェントに回させていない。**
人間が CLI を叩けば `dod → approved` を一周する。ロール層は書いてあるが、実タスクで走らせた実績がない。

---

## 次にやること（順序つき）

### 1. `requires_human` の強制テスト1本

**なぜ最初か**: ここが回帰したら、このハーネスの存在理由が消えるから。

実際に一度壊れている。`run_verify_and_record` が `requires_human` を計算して `verify.json` に書き、
CLAUDE.md・秘書ロール・policy・スキルの全部で「交渉不可」「機構」と書いていたのに、
**`validate_task()` がそれを読んでいなかった**（commit `b13b5f8` で修正）。
機構だと宣言してお願いを出荷していた。同じことが起きても、今の CI は緑のまま。

- `requires_human: true` の `verify.json` を食わせて `advance` が弾かれること
- 承認が**検知されたマーカーに紐づく**こと（`forces replacement` の承認が、後から現れた `DROP TABLE` を覆わない）
- `return` で承認が失効すること

**Done**: 上記3つが CI で走る。`validate_task()` から `requires_human` の検査を消すと CI が赤くなる。

### 2. 実走1周

**なぜ次か**: ロール層が最大の未知だから。ここを通さないと `doctor` が `ok: false` のままで、そもそも誰も使えない。

- `config/workspace-registry.toml` と `~/.config/dev-harness/profile.md` を置く
- 実リポジトリに対して `dod → approved` をエージェント委譲で一周させる
- **対象repo: 未定**。初回は verify が速く・認証が要らない repo を選ぶ
  （ADR が旗艦例に挙げる terraform の `forces replacement` は目玉機能まで発火させられるが、
  AWS 認証が要るので2周目に回す。初回にやるとハーネスではなく認証と格闘して終わる）

**Done**: `doctor` が `ok: true`。1周のコスト実測値がある。

### 3. 実走で分かった壊れ方を回帰テストに固定

順序が逆だと、想像で「壊れそうな場所」のテストを書くことになる。
2 で実データの壊れ方が出てから書けば、当たったところに置ける。

### 4. コスト停止条件

自律モード（`/work-run`）の従量課金対策。**唯一、コードを書く必要がある未実装機能。**

---

## 既知の負債

- **回帰テストが無い** — README の「[実際に機構で保証しているもの](README.md#ツール制限は機構的保証ではありません)」
  は実装時に手元で確認しただけで、テストとして repo に残っていない。CI が走らせるのは漏洩ゲート3層
  （`gitleaks` / `home-path-check` / `config-leak-check`）だけなので、**保証が壊れても CI は緑のまま**。
  このハーネスの売りは機構であって、その機構に回帰検知が無いのは弱い。
  上の 1 は、この負債のうち**最も落としてはいけない1本**を先に返すもの。

---

## 将来

- **Cursor 側の実効性検証** — `readonly:`（`tools:` の代替）も `model:` も現在**未検証**。
  ロードが壊れないことだけ実測済み。会社支給マシンで Cursor しか使えないのが出発点なので、
  ここが効かないなら「Cursor でも同じ保証が立つ」とは言えない

---

## 完了済み

**安全機構**

- `.gitignore` / `.gitleaks.toml` / 3層CI（`gitleaks` / `home-path-check` / `config-leak-check`）
- PreToolUse ガード（`scripts/guard_writes.py`）— registry / `verify.json` / `state.json` への書き込みを拒否

**状態機械**

- workspace registry（`config/workspace-registry.example.toml`）＋ ローダ（`harness/registry.py`、`tomllib` = 依存ゼロ）
- 状態機械と CLI（`harness/core.py` / `harness/cli.py`）
  - `init` / `list` / `switch` / `status` / `validate` / `advance` / `return` / `verify` / `confirm-dod` / `approve-irreversible` / `abandon` / `decision` / `event` / `metrics` / `doctor`
  - **push コマンドは存在しない**（意図的。`approved` が終点）
- ゲート: DoD の観測可能性 lint / 根拠必須（`file:line`）/ plan の Rollback 節 / **repo 自身の verify の exit code** / `approved` の git 検査（clean かつ未push）
- 不可逆検知（`irreversible_markers` → advisor 招集の必須化 ＋ 代理承認の禁止）
- `abandoned`（打ち切り）経路
- observability（イベントログ・判断ログ・`metrics` 集計）
- `init.sh`（template → instance）

**エージェント層**

- ロール定義（`.claude/agents/`）— 工程オーナー5（`secretary` / `researcher` / `architect` / `developer` / `reviewer`）＋ advisor 3（`aws-latest-architect` / `terraform-latest-advisor` / `proposal-auditor`）
- slash commands（`.claude/skills/`）— `/work-{init,list,switch,status,advance,run,return,abandon,doctor}`
- 秘書の policy（`secretary/policy.md`）と profile テンプレ（`secretary/profile.example.md`）
