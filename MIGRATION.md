# Repo reorg: `gini5` → `gini` (lean rename + tidy)

Run these on your Mac, in the repo, with a clean working tree (`git status` shows nothing
to commit). `git mv` preserves history. After it's done you can delete this file.

## 1. Rename the app directory (history preserved)

```bash
cd ~/Programs/gini5
git mv frontend-ng gbuilder
```

The one code reference that pointed at `frontend-ng/src` (the backend's
`multirouter_test.py`) is already rename-proof — it looks for `gbuilder/src` first. The
frontend→backend coupling (`orchestrator.py`'s `parents[4]/"backend"`) is depth-based and
unaffected.

## 2. Tidy the top level

```bash
mkdir -p docs examples
git mv ARCHITECTURE.md docs/
git mv test-01.gini test2.gini test3.gini test4.gini test5.gini test6.gini examples/

# fix the remaining doc mentions of the old name (docs only — code is already handled)
grep -rl --include='*.md' frontend-ng . | xargs sed -i '' 's#frontend-ng#gbuilder#g'

# drop any now-ignored cruft from tracking (caches, the stray pytest dir)
git rm -r --cached --ignore-unmatch frontend-ng/pytest-cache-files-* 2>/dev/null || true
```

(Optional) copy the living planning docs into the repo so it's self-contained:
the GINI master plan / palette status currently live in your Cowork "GINI Project" folder.

## 3. Rename the repo root folder

```bash
cd ~/Programs
mv gini5 gini
cd gini
```

## 4. Commit

```bash
git add -A
git commit -m "Reorg: frontend-ng -> gbuilder, docs/, examples/, cleaner .gitignore"
```

## 5. New GitHub repo (preserving this history)

```bash
# create the repo (needs the gh CLI + a login), then push current history to it:
gh repo create gini --private --source=. --remote=neworigin --push
# — or, if you made the empty repo on github.com manually:
#   git remote add neworigin git@github.com:<you>/gini.git
#   git push -u neworigin main
```

Keep the old `origin` if you want; `neworigin` carries the renamed tree + full history.

## 6. Verify (macOS, native — no EGL stub needed)

```bash
cd gbuilder
pip install -e .            # makes the `gini` package importable
QT_QPA_PLATFORM=offscreen python -m pytest -q     # expect ~290 passing
python -m gini             # launch gBuilder
```

That's it — the structure is now `gini/{gbuilder, backend, legacy, docs, examples}` with the
app package unchanged at `gbuilder/src/gini/`.
