#!/usr/bin/env bash
#
# init.sh — turn a clone of this template into a working instance.
#
# This repository is a template, not a tool you run in place. You instantiate
# it: once for your own machine, once for your work machine. The instance is
# where you actually work; this repo is what you copy from.
#
#   git clone <this repo> some-dir && cd some-dir
#   ./init.sh
#
# What an instance is: the framework, plus YOUR config living beside it but
# untracked. `config/` and `tasks/` are gitignored, so an instance commits
# exactly the same files as the template — which is what makes updating it a
# plain copy (see "Updating an instance" below).
#
# This script is idempotent: re-running it will not clobber a config you have
# already filled in. It tells you what it skipped.

set -euo pipefail

say()  { printf '  %s\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
skip() { printf '  \033[33m-\033[0m %s\n' "$1"; }
die()  { printf '\033[31mERROR:\033[0m %s\n' "$1" >&2; exit 1; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

[ -f .gitleaks.toml ] && [ -d config ] \
  || die "run this from the root of a clone of the harness template."

PROFILE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/dev-harness"
PROFILE_PATH="${DEV_HARNESS_PROFILE:-$PROFILE_DIR/profile.md}"

echo
echo "dev-company-harness — instance setup"
echo

# --- 1. Record which template commit this came from -------------------------
# An instance has no upstream: init deletes .git and starts fresh history, so
# git can never tell you how stale the framework is. This file is the only
# thing that can. Keep it — it is what makes "should I re-copy the framework?"
# an answerable question six months from now.
echo "[1/5] Recording template version"
if [ -d .git ]; then
  template_sha="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  template_date="$(git log -1 --format=%cI 2>/dev/null || echo unknown)"
else
  template_sha="unknown"
  template_date="unknown"
  say "no .git present — cannot read the template commit"
fi

# HARNESS_VERSION is also the "already instantiated" marker, read by step 5.
# It has to be, and it cannot be `[ -d .git ]`: step 5 *creates* .git, so a
# second run would see its own output and wipe the instance's history.
ALREADY_INSTANTIATED=false
if [ -f HARNESS_VERSION ] && grep -q '^template_commit:' HARNESS_VERSION 2>/dev/null; then
  ALREADY_INSTANTIATED=true
  skip "HARNESS_VERSION exists (leaving it; delete it to re-stamp)"
else
  cat > HARNESS_VERSION <<EOF
# Which template commit this instance was created from.
# This instance has NO upstream remote, so this file is the only record of
# how stale its framework is. To update: clone the template fresh, copy the
# framework files over this tree, and bump the values here. config/ and
# tasks/ are gitignored, so a copy never touches your setup.
template_commit: ${template_sha}
template_date: ${template_date}
instantiated_at: $(date -Iseconds)
EOF
  ok "HARNESS_VERSION -> template_commit: ${template_sha}"
fi

# --- 2. Drop template-only CI ----------------------------------------------
# The CI in .github/ exists to protect the PUBLIC template: it asserts no
# config, task or profile ever gets committed there. An instance is private
# and (per setup) does not run GitHub Actions, so this is dead weight that
# would sit in the repo looking authoritative while never running.
#
# gitleaks itself is worth keeping wherever it can actually run. If your
# instance IS on GitHub, re-add .github/workflows/gitleaks.yml and delete the
# config-leak-check job from it.
echo "[2/5] Removing template-only CI"
if [ -d .github ]; then
  rm -rf .github
  ok ".github/ removed (instances do not run the template's CI)"
else
  skip ".github/ already absent"
fi

# --- 3. Your workspace registry --------------------------------------------
echo "[3/5] Workspace registry"
if [ -f config/workspace-registry.yml ]; then
  skip "config/workspace-registry.yml exists (not overwriting)"
else
  cp config/workspace-registry.example.yml config/workspace-registry.yml
  ok "config/workspace-registry.yml created from the example"
  say "-> edit it: real repo names, paths, verify commands, advisors"
fi

# --- 4. The secretary's profile, deliberately outside this repo -------------
# .gitignore is a request: it can be edited, and `git add -f` walks past it.
# A file outside the repo cannot be committed at all. The profile is your
# judgment axes — on a work machine, a company-managed repo is the last place
# it should end up — so it is protected by construction, not by a rule.
echo "[4/5] Secretary profile (outside this repo, by design)"
if [ -f "$PROFILE_PATH" ]; then
  skip "profile exists: ${PROFILE_PATH/#$HOME/\~}"
elif [ -f secretary/profile.example.md ]; then
  mkdir -p "$(dirname "$PROFILE_PATH")"
  cp secretary/profile.example.md "$PROFILE_PATH"
  ok "profile created: ${PROFILE_PATH/#$HOME/\~}"
  say "-> edit it: your judgment axes. It never enters this repo."
else
  skip "secretary/profile.example.md not present yet (framework still in progress)"
fi

# --- 5. Fresh history -------------------------------------------------------
# Deleting .git is not housekeeping, it does two real things:
#   - severs the remote, so this instance can never push to the public
#     template. The leak path stops existing rather than being forbidden.
#   - drops the template author's commit identity, so your work machine's
#     own git config signs every commit here.
#
# Guarded by ALREADY_INSTANTIATED, never by `[ -d .git ]`: this step creates
# .git, so keying off its presence would make a second run destroy the
# instance's own history and remote — the exact opposite of idempotent, and
# silent about it. If you really want to re-stamp, delete HARNESS_VERSION
# first and understand that this wipes history.
echo "[5/5] Git history"
if [ "$ALREADY_INSTANTIATED" = true ]; then
  skip "already an instance — history and remote left alone"
elif [ -d .git ]; then
  rm -rf .git
  git init -q
  ok ".git reset — no remote, fresh history"
  say "-> point it at your own repo:  git remote add origin <your-repo>"
else
  git init -q
  ok "git repository initialised (template was fetched without .git)"
  say "-> point it at your own repo:  git remote add origin <your-repo>"
fi

echo
echo "Instance ready."
echo
say "Next:"
say "  1. edit config/workspace-registry.yml   (your repos + verify commands)"
say "  2. edit ${PROFILE_PATH/#$HOME/\~}   (your judgment axes)"
say "  3. git add -A && git commit            (framework only; config is ignored)"
echo
