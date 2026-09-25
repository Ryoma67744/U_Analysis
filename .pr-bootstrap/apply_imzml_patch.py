"""配布済み差分を固定mainへ適用する一時処理。補助ファイルはPRに含めない。"""
import ast
import base64
import hashlib
import json
import lzma
import os
from pathlib import Path
import subprocess

BASE = "b4189b7107501b551320dc136e538eb76c66d549"
FINAL = "claude/imzml-autoimport-main-pr-ver70-0"
PATCH_SHA256 = "dfaca4efdc84ddb2529c0b8319204cd15234f5d0956d1f7c3669d2a63d7643a2"
PART_BLOBS = ["7fbc86208648a2dc2e758ae858ff7c0b89371f3d", "066d5054dc7f23fb605cabe45dd8192551e46da8", "5917c966be7038910a428ba05ebcefd98f3febbe"]

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def git(*args, data=None, env=None):
    return subprocess.run(["git", *args], input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True, env=env).stdout.decode().strip()

def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

require(os.environ.get("GITHUB_REPOSITORY") == "Ryoma67744/U_Analysis", "対象リポジトリが異なります")
require(os.environ.get("GITHUB_REF") == "refs/heads/claude/imzml-autoimport-ver70-0", "対象ブランチが異なります")
git("fetch", "--no-tags", "origin", "main")
require(git("rev-parse", "origin/main") == BASE, "mainが進んだため停止します。旧版へ差し戻しません")
require(not git("ls-remote", "--heads", "origin", "refs/heads/" + FINAL), "PR用ブランチは既に存在します。上書きしません")

root = Path(".pr-bootstrap")
checks = json.loads((root / "checks.json").read_text(encoding="utf-8"))
require(len(checks) == 25, "変更対象件数が異なります")
parts = []
for i, expected in enumerate(PART_BLOBS):
    data = (root / ("part%02d.txt" % i)).read_bytes()
    require(blob(data) == expected, "転送データのハッシュ不一致: part%02d" % i)
    parts.append(data)
patch = lzma.decompress(base64.b64decode(b"".join(parts), validate=True))
require(hashlib.sha256(patch).hexdigest() == PATCH_SHA256, "パッチSHA256不一致")

for name, (before, after) in checks.items():
    require(not Path(name).is_absolute() and ".." not in Path(name).parts, "不正なパス")
    entry = git("ls-tree", BASE, "--", name)
    if before is None:
        require(not entry, "追加予定のパスがmainに存在します: " + name)
    else:
        require(entry and entry.split()[2] == before, "変更前blob不一致: " + name)

# 一時転送用ファイルをindexから除外し、mainのtreeに25ファイルだけを適用する。
# 作業ブランチの履歴を書き換えず、新しいPR専用ブランチをmainの子として作成する。
git("read-tree", BASE)
git("update-index", "--refresh")
git("apply", "--index", "--check", "-", data=patch)
git("apply", "--index", "-", data=patch)
changed = set(git("diff", "--cached", "--name-only", BASE).splitlines())
require(changed == set(checks), "PR差分に対象外ファイルがあります")
for name, (before, after) in checks.items():
    data = Path(name).read_bytes()
    require(blob(data) == after, "適用後blob不一致: " + name)
    require(git("rev-parse", ":" + name) == after, "indexのblob不一致: " + name)
    if name.endswith(".py"):
        ast.parse(data, filename=name)
git("diff", "--cached", "--check")
tree = git("write-tree")
env = dict(os.environ)
for prefix in ("GIT_AUTHOR", "GIT_COMMITTER"):
    env[prefix + "_NAME"] = "github-actions[bot]"
    env[prefix + "_EMAIL"] = "41898282+github-actions[bot]@users.noreply.github.com"
commit = git("commit-tree", tree, "-p", BASE, "-m", "通常解析にimzML自動取り込みを追加 [ver70.0]", env=env)
# mainへのpushもforce-pushも行わない。
git("push", "origin", commit + ":refs/heads/" + FINAL)
result = {"base": BASE, "branch": FINAL, "commit": commit, "tree": tree,
          "changed_files": len(changed), "all_result_blobs_verified": True,
          "patch_sha256": PATCH_SHA256, "main_modified": False}
print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
if os.environ.get("GITHUB_STEP_SUMMARY"):
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
        handle.write("## 差分登録結果\n```json\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n```\n")
