# =============================================================================
# MSI Analysis Application - .env Bootstrap
# 初回起動時に `App/.env` を用意し、起動・ログインに必須の秘密値を自動生成する。
# =============================================================================
"""デスクトップ起動 (setup.bat / run_app.bat / setup.sh / run_app.sh) 用の
`.env` 自動生成。

★ ver64.1: `.env` が無い状態で `run_app.bat` を叩くと、アプリは
`FLASK_SECRET_KEY env var is required` で **起動すらできなかった**。

前の実装が駄目だった理由:

- `.env` は `.gitignore` 済みなので、GitHub の ZIP を展開しただけの環境には
  **存在しない**。`setup.bat` も `run_app.bat` も `.env` を作らず、
  `SETUP_GUIDE.html` にも「作れ」と書いていなかった。
- Docker 経由 (`docker compose`) はリポジトリ直下の `.env` から
  `FLASK_SECRET_KEY` を渡すため、サーバ運用では表面化しない。
  **壊れていたのは配布物のデスクトップ起動だけ**で、気づきにくかった。
- 秘密値が要るのは 1 つではない。仮に `FLASK_SECRET_KEY` だけ手で書いても、
  次は `auth_service.init_from_env()` が `INITIAL_PASSWORD_B` 未設定で
  RuntimeError になり、それも越えると今度は `MASTER_PASSWORD` が空で
  **ログイン画面を突破できない** (verify_master が常に False)。
  3 段構えの壁なので、まとめて用意する必要がある。

アプリ本体の「未設定なら起動失敗」(フェイルファースト) は**変えない**。
サーバ運用で秘密値の設定漏れを黙って握り潰すと、全員が同じ鍵でセッション
Cookie を偽造できる状態のまま動いてしまうため。ここで面倒を見るのは
ランチャ (bat/sh) から明示的に呼ばれたときだけである。

依存は標準ライブラリのみ。`setup.bat` の `pip install` より前 —
python-dotenv すら入っていない段階 — でも動く必要があるため。

単体実行:
    cd App && python -m app.services.env_bootstrap
"""

from __future__ import annotations

import logging
import os
import platform
import re
import secrets
import sys
from pathlib import Path
from typing import Optional

# app/services/env_bootstrap.py → App/
APP_DIR = Path(__file__).resolve().parent.parent.parent
ENV_PATH = APP_DIR / ".env"
ENV_EXAMPLE_PATH = APP_DIR / ".env.example"

# `.env.example` / `.env.docker` が配っているプレースホルダの接頭辞。
# これが残ったままの値は「未設定」と同じ扱いにする。特に
# FLASK_SECRET_KEY は公開リポジトリに載っている文字列なので、
# そのまま起動すると誰でもセッション Cookie を偽造できる。
_PLACEHOLDER_PREFIXES = ("CHANGE_ME", "ChangeMe_")

# 値が無い/プレースホルダのままなら生成するキー。
#   FLASK_SECRET_KEY   … 未設定だと main.py が RuntimeError で起動しない
#   INITIAL_PASSWORD_B … 未設定だと auth_service.init_from_env() が RuntimeError
#   MASTER_PASSWORD    … 未設定だとログイン画面を突破できない (Tier A の鍵)
#   INITIAL_PASSWORD_A … ver4.0 以降ログインには使わないが、プレースホルダを
#                        そのまま auth.json に bcrypt 保存させない
GENERATED_KEYS: tuple[str, ...] = (
    "FLASK_SECRET_KEY",
    "MASTER_PASSWORD",
    "INITIAL_PASSWORD_A",
    "INITIAL_PASSWORD_B",
)

# ログイン時に人が手で打つパスワードなので、読み違えやすい文字を外す。
# 除外: l/I/1, o/O/0 (紙・コンソール越しの転記ミスが実際に起きる帯域)
_PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
_PASSWORD_GROUPS = 3      # 4 文字 x 3 グループ = 12 文字
_PASSWORD_GROUP_LEN = 4
# 32 種の文字を 12 桁 = 約 60 bit。ローカル LAN 用の初期パスワードとして十分で、
# かつ "kfd3-92mx-7ptr" 程度なら口頭でも伝えられる。


def is_placeholder(value: Optional[str]) -> bool:
    """`.env.example` のプレースホルダ (CHANGE_ME... / ChangeMe_...) か。"""
    v = (value or "").strip().strip('"').strip("'")
    return v.startswith(_PLACEHOLDER_PREFIXES)


def needs_generation(value: Optional[str]) -> bool:
    """空 or プレースホルダなら生成が要る。"""
    v = (value or "").strip().strip('"').strip("'")
    return not v or is_placeholder(v)


# 秘密値が未設定/雛形のまま起動しようとしたときに出す案内。
# ★ ver64.1: 従来の `Generate with: openssl rand -hex 32` だけでは、
#   Windows のデスクトップ起動でここに当たった利用者が動けなかった
#   (openssl が無い / `.env` をどこに置くのか分からない)。
HOWTO = (
    "\n  デスクトップ起動: App フォルダで `python -m app.services.env_bootstrap` を"
    "実行すると App/.env を自動生成します"
    "\n                    (setup.bat / run_app.bat / setup.sh / run_app.sh は自動実行)"
    "\n  Docker: .env.docker を .env にコピーし FLASK_SECRET_KEY を設定してください"
    "\n  手動生成: openssl rand -hex 32"
)

# 推奨長 (openssl rand -hex 32 = 64 桁)。下回っても起動は止めない。
_RECOMMENDED_SECRET_LEN = 32


def validate_secret_key(raw: Optional[str]) -> str:
    """FLASK_SECRET_KEY を検証して返す。未設定/プレースホルダなら RuntimeError。

    ★ ver64.1: プレースホルダを弾くようにした。従来は「空でなければ通す」
    判定で、`.env.example` の `CHANGE_ME_TO_RANDOM_HEX_64` を
    コピーしただけの状態が **起動はするがセッション署名鍵は公開値** という
    最も危険な形で通っていた。この鍵で署名した Cookie は、リポジトリを見た
    人なら誰でも偽造できる (= 認証が実質無効)。
    """
    value = (raw or "").strip()
    if not value:
        raise RuntimeError(
            "FLASK_SECRET_KEY env var is required (32+ random bytes)." + HOWTO
        )
    if is_placeholder(value):
        raise RuntimeError(
            "FLASK_SECRET_KEY is still the template placeholder "
            f"({value[:16]}...). この値ではセッション Cookie を誰でも偽造できます。"
            + HOWTO
        )
    if len(value) < _RECOMMENDED_SECRET_LEN:
        # 既存デプロイを止めたくないので失敗にはしないが、記録は残す
        logging.getLogger("msi.startup").warning(
            "FLASK_SECRET_KEY is shorter than %d chars (len=%d). "
            "推奨は `openssl rand -hex 32` 相当の 64 桁 hex。",
            _RECOMMENDED_SECRET_LEN, len(value),
        )
    return value


def generate_secret_key() -> str:
    """Flask セッション署名鍵。32 バイト = 64 桁の hex (openssl rand -hex 32 相当)。"""
    return secrets.token_hex(32)


def generate_password() -> str:
    """人が打てる初期パスワード (例: kfd3-92mx-7ptr)。"""
    groups = [
        "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_GROUP_LEN))
        for _ in range(_PASSWORD_GROUPS)
    ]
    return "-".join(groups)


def _generate_for(key: str) -> str:
    return generate_secret_key() if key == "FLASK_SECRET_KEY" else generate_password()


def _key_line_re(key: str) -> re.Pattern:
    # `export KEY=...` 形式も拾う (手書きの .env で稀にある)
    return re.compile(
        r"^[ \t]*(?:export[ \t]+)?" + re.escape(key) + r"[ \t]*=(?P<value>.*)$"
    )


def read_key(text: str, key: str) -> Optional[str]:
    """`.env` テキストから key の値を読む。無ければ None。

    同じキーが複数あれば **最後** を採る (python-dotenv と同じく後勝ち)。
    コメントアウト行 (`# KEY=...`) は未設定として扱う。
    """
    pattern = _key_line_re(key)
    found: Optional[str] = None
    for line in text.splitlines():
        m = pattern.match(line)
        if m:
            found = m.group("value").strip()
    return found


def set_key(text: str, key: str, value: str) -> str:
    """key の行を書き換える。無ければ末尾に追記する。他の行・コメントは保全。"""
    pattern = _key_line_re(key)
    lines = text.splitlines()
    last_idx = None
    for i, line in enumerate(lines):
        if pattern.match(line):
            last_idx = i
    if last_idx is not None:
        lines[last_idx] = f"{key}={value}"
        return "\n".join(lines) + ("\n" if text.endswith("\n") or text else "")
    tail = "" if (not text or text.endswith("\n")) else "\n"
    return f"{text}{tail}{key}={value}\n"


def _adjust_example_for_platform(text: str, is_windows: bool) -> str:
    """`.env.example` は Windows 既定の R_HOME を持つ。Windows 以外では無効化する。

    ★ ver64.1: `R_HOME=C:\\Program Files\\R\\R-4.4.2` を macOS/Linux にそのまま
    複製すると 2 つ壊れる:
      1. `config.py` が存在しないパスを見る (PATH の Rscript へ落ちるので実害は
         小さいが、`.env` の記述と実際の R がズレて切り分けを誤らせる)
      2. `run_app.sh` は `.env` を `set -a; . ./.env` で **シェルとして読む**ため、
         空白入りの Windows パスで `Files\\R\\R-4.4.2: command not found` となり
         `set -e` でランチャごと落ちる
    行を消さずコメント化するのは、Windows パスの見本を残しておくため。
    """
    if is_windows:
        return text
    return re.sub(
        r"^(R_HOME=.*)$",
        r"# \1   # ← Windows 用の既定値 (macOS/Linux では未設定で自動判定)",
        text,
        count=1,
        flags=re.MULTILINE,
    )


_GENERATED_HEADER = (
    "\n"
    "# ---------------------------------------------------------------------------\n"
    "# 以下は初回セットアップ (app.services.env_bootstrap) が自動生成した値です。\n"
    "# このファイルは共有・コミットしないでください (.gitignore 済み)。\n"
    "# ---------------------------------------------------------------------------\n"
)


def bootstrap(
    env_path: Optional[Path] = None,
    example_path: Optional[Path] = None,
    is_windows: Optional[bool] = None,
) -> dict:
    """`.env` を用意し、足りない秘密値だけを生成する (冪等)。

    Returns:
        {"path": Path, "created": bool, "generated": {key: value}}
        `generated` には **今回生成した** キーだけが入る。既にユーザーが
        設定済みの値は読みも書きもしない。
    """
    env_path = Path(env_path) if env_path is not None else ENV_PATH
    example_path = Path(example_path) if example_path is not None else ENV_EXAMPLE_PATH
    if is_windows is None:
        is_windows = platform.system() == "Windows"

    created = False
    if env_path.exists():
        text = env_path.read_text(encoding="utf-8-sig")
    elif example_path.exists():
        text = _adjust_example_for_platform(
            example_path.read_text(encoding="utf-8-sig"), is_windows
        )
        created = True
    else:
        # .env.example ごと欠けている場合でも起動できる最小構成を作る
        text = (
            "# MSI Analysis Application - 環境設定 (自動生成)\n"
            "APP_PORT=3838\n"
            "APP_HOST=0.0.0.0\n"
        )
        created = True

    generated: dict[str, str] = {}
    header_added = False
    for key in GENERATED_KEYS:
        if not needs_generation(read_key(text, key)):
            continue
        if read_key(text, key) is None and not header_added:
            # `.env` に行ごと無いキーは末尾に追記される。その前に一度だけ見出しを入れる
            text = text.rstrip("\n") + "\n" + _GENERATED_HEADER
            header_added = True
        value = _generate_for(key)
        text = set_key(text, key, value)
        generated[key] = value

    if created or generated:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(text, encoding="utf-8")
        # 秘密値を含むので、可能な環境では所有者のみ読み書きにする
        # (Windows では chmod がほぼ無視されるが、失敗しても致命的ではない)
        try:
            os.chmod(env_path, 0o600)
        except OSError:
            pass

    return {"path": env_path, "created": created, "generated": generated}


# ---------------------------------------------------------------------------
# CLI (ランチャから呼ばれる)
# ---------------------------------------------------------------------------

_BAR = "=" * 62


def _auth_config_exists() -> bool:
    """パスワードの実体 (auth.json) が既に保存済みか。

    `INITIAL_PASSWORD_A/B` は **auth.json が無いときの初期化にしか使われない**
    (`auth_service.init_from_env`)。一度アプリを起動した後に `.env` だけを
    失って再生成すると、新しい共有用パスワードを表示しても実際には効かない
    (auth.json 側の bcrypt ハッシュが優先される)。効かない値を「これを使え」と
    表示するのは誤案内なので、その場合だけ但し書きを出す。

    パスは `auth_service._default_auth_path()` と同じ規則
    (`AUTH_CONFIG_PATH` 優先、既定は `<リポジトリルート>/Data/Other/common/auth.json`)。
    ここで config を import しないのは、python-dotenv 未導入でも動かすため。
    """
    override = os.environ.get("AUTH_CONFIG_PATH", "").strip()
    path = Path(override) if override else (
        APP_DIR.parent / "Data" / "Other" / "common" / "auth.json"
    )
    try:
        return path.is_file()
    except OSError:
        return False


def _safe_print(text: str) -> None:
    """コンソールのコードページ次第で日本語が出せないことがあるため保険をかける。

    Windows で `chcp 65001` が効かなかった環境 (cp932) でも、
    UnicodeEncodeError でランチャを落とさない。
    """
    try:
        print(text)
    except UnicodeEncodeError:
        enc = (sys.stdout.encoding or "ascii")
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def main(argv: Optional[list] = None) -> int:
    try:
        result = bootstrap()
    except OSError as e:
        _safe_print(f"[WARN] .env を作成できませんでした: {e}")
        _safe_print("       App/.env を手動で用意してください (App/.env.example が雛形です)。")
        return 1

    path = result["path"]
    generated = result["generated"]

    if not generated:
        _safe_print(f"環境設定は準備済みです: {path}")
        _safe_print("  ログインパスワードは .env の MASTER_PASSWORD 行で確認できます。")
        return 0

    _safe_print(_BAR)
    _safe_print("  環境設定 (.env) を初期化しました")
    _safe_print(_BAR)
    _safe_print(f"  ファイル: {path}")
    _safe_print("")
    if "MASTER_PASSWORD" in generated:
        _safe_print("  ログインパスワードを自動生成しました:")
        _safe_print("")
        _safe_print(f"      >>>  {generated['MASTER_PASSWORD']}  <<<")
        _safe_print("")
        _safe_print("  ブラウザのログイン画面でこの値を入力してください。")
        _safe_print("  控えを忘れても .env の MASTER_PASSWORD 行で確認できます。")
        _safe_print("  アプリ内のパスワード変更 UI から好きな値に変更できます。")
    if "INITIAL_PASSWORD_B" in generated:
        _safe_print("")
        _safe_print("  共有 URL 閲覧用パスワード (INITIAL_PASSWORD_B):")
        _safe_print(f"      {generated['INITIAL_PASSWORD_B']}")
        if _auth_config_exists():
            _safe_print("      ※ 既に設定済みのパスワードがあるため、この値は使われません")
            _safe_print("         (変更はアプリ内のパスワード変更 UI から)")
    _safe_print("")
    _safe_print("  ※ .env は秘密情報です。共有・コミットしないでください。")
    _safe_print(_BAR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
