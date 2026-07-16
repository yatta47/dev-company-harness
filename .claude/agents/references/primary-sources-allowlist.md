# Primary Sources Allowlist

このファイルは「一次情報として扱ってよいサイト」と「補助情報に落とすべきサイト」を整理したものです。

## 判定ルール

### Tier 1: 強い一次情報
以下は、原則として最優先で参照してよい。
- 公式ドキュメント
- 公式リリースノート / changelog
- 公式仕様書 / spec
- 公式レジストリ / provider docs
- 公式 API reference / CLI reference

### Tier 2: 補助的な一次情報
以下は公式だが、仕様の唯一根拠にはしない。
- 公式 Architecture Center
- 公式 Blog
- 公式サンプル / examples
- 公式 whitepaper / guide

### Tier 3: 二次情報
以下は一次情報不足時のみ補助的に使う。
- ベンダー以外のブログ
- 個人記事
- Qiita / Zenn / Medium / Stack Overflow
- YouTube 解説
- 生成AI要約

### 原則
- Tier 1 を優先。
- Tier 2 は実装例や設計補足に使う。
- Tier 3 は裏取りができる場合だけ使う。
- Tier 3 を根拠に断定しない。

---

# AWS

## Strong Primary Sources
- `docs.aws.amazon.com`
- `aws.amazon.com/about-aws/whats-new/`
- `docs.aws.amazon.com/general/latest/gr/`
- `docs.aws.amazon.com/wellarchitected/`

## Supplemental Official Sources
- `aws.amazon.com/architecture/`
- `aws.amazon.com/blogs/`
- `github.com/aws-samples`
- AWS service pages on `aws.amazon.com`

## Notes
- サービス機能の有無は docs / What's New を優先。
- ベストプラクティスは Well-Architected / Architecture Center を優先。
- リージョン差・クォータ・制約は General Reference や該当サービス docs で確認。

---

# Terraform

## Strong Primary Sources
- `developer.hashicorp.com/terraform`
- `developer.hashicorp.com/terraform/language`
- `developer.hashicorp.com/terraform/language/upgrade-guides`
- `registry.terraform.io`

## Supplemental Official Sources
- `github.com/hashicorp/terraform/releases`
- Official provider GitHub releases / changelogs
- HashiCorp support / product docs on `developer.hashicorp.com`

## Notes
- Terraform core と provider behavior を混同しない。
- provider docs は必ず該当 version / resource docs を見る。
- release notes は新機能・breaking-ish change・upgrade caution の確認に使う。

---

# OpenStack

## Strong Primary Sources
- `docs.openstack.org`
- `releases.openstack.org`
- `specs.openstack.org`

## Supplemental Official Sources
- `www.openstack.org`
- `openinfra.dev`
- `opendev.org`
- deployment-tool-specific official docs under `docs.openstack.org`

## Notes
- OpenStack は release 差異が大きいので、必ず対象 release を意識する。
- upstream と vendor distribution を混同しない。
- 実装仕様に踏み込むときは specs と release notes を優先。

---

# Recommended Search Order per Domain

## AWS
1. docs.aws.amazon.com
2. aws.amazon.com/about-aws/whats-new/
3. docs.aws.amazon.com/general/latest/gr/
4. docs.aws.amazon.com/wellarchitected/
5. aws.amazon.com/architecture/

## Terraform
1. developer.hashicorp.com/terraform
2. developer.hashicorp.com/terraform/language
3. developer.hashicorp.com/terraform/language/upgrade-guides
4. registry.terraform.io
5. github.com/hashicorp/terraform/releases

## OpenStack
1. docs.openstack.org
2. releases.openstack.org
3. specs.openstack.org
4. docs for specific projects (Nova / Neutron / Cinder / Keystone)
5. official governance / foundation pages

---

# Exclusion / Caution List

原則として以下は単独根拠にしない:
- 個人ブログ
- 企業ブログ（公式ベンダー以外）
- フォーラム回答
- Stack Overflow 単体
- 古い Qiita / Zenn 記事
- gist / sample repo 単体

以下は「必ず注意」:
- 数年前の blog 記事
- provider の古いバージョンの docs
- OpenStack の旧 release docs
- AWS の古い whitepaper だけを根拠にした設計判断
