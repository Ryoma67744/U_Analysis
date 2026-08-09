"""ver52.6: アプリが名乗る「公開 URL」の出どころが 2 つあった。

■ 何が起きていたか

利用者は Caddy のリバースプロキシ経由で https://cciiumap.duckdns.org に公開している。
`Caddyfile` は `reverse_proxy msi-app:3838` + `header_up X-Forwarded-Proto https` で、
Caddy v2 は元の `Host` をそのまま上流へ渡す。材料は揃っているのに、
**同じ 1 リクエストからアプリが 2 通りの公開 URL を作っていた**:

    request.url_root      = http://cciiumap.duckdns.org/     ← OpenAPI の servers
    external_base_url()   = https://cciiumap.duckdns.org     ← 共有リンク

Flask の `request.scheme` は `X-Forwarded-Proto` を読まない (ProxyFix は入れていない)。
そのため `/api/gpt/openapi.json` は

    "servers": [{"url": "http://cciiumap.duckdns.org"}]

を名乗り、**https を要求する ChatGPT の Action から取り込めなかった**。
共有リンク側は正しく https だったので、症状は
「ブラウザでは普通に開けるのに、ChatGPT からだけ繋がらない」になる。

■ ここで固定すること

    (a) 経路ごと通して servers が https になること      ← 修正前のコードで落ちる
    (b) 公開 URL の出どころが 1 つであること            ← 型の番人
    (c) 利用者が設定する環境変数が雛形に載っていること  ← 型の番人 (GPT_API_KEY 漏れ)
    (d) /health が鍵を漏らさないこと                    ← (c) で情報を足した副作用止め

★ (a) が本丸。`build_openapi_spec("https://x")` を直接呼ぶ既存テストは
  **この欠陥を捕まえられない** —— 欠陥は「ハンドラが何を渡すか」の側にあり、
  純関数の側には無いから。だから経路ごと (Flask のテストクライアントで) 通す。
"""

import ast
from pathlib import Path

import flask
import pytest

from app.services import gpt_api as g

APP_DIR = Path(__file__).resolve().parent.parent / "app"
REPO = Path(__file__).resolve().parent.parent.parent

# 本番と同じ値。ここを固定値で書くのは「実際に繋がらなかった構成」を
# そのままテストに残すため。
PROD_HOST = "cciiumap.duckdns.org"


def _caddy_client(monkeypatch, share_base_url="", api_key="k"):
    """Caddy 配下のアプリと同じ条件のテストクライアントを作る。

    - 上流 (Caddy → アプリ) は素の HTTP        → base_url は http://
    - Caddy は元の Host を保持                  → HTTP_HOST は本番ドメイン
    - Caddy は X-Forwarded-Proto: https を付ける → ヘッダで付与
    """
    monkeypatch.setattr("app.config.SHARE_BASE_URL", share_base_url, raising=False)
    monkeypatch.setattr("app.config.GPT_API_KEY", api_key, raising=False)
    app = flask.Flask(__name__)
    g.register_gpt_api(app)
    return app.test_client()


def _caddy_get(client, path):
    return client.get(path,
                      base_url=f"http://{PROD_HOST}",       # 上流は平文 HTTP
                      headers={"X-Forwarded-Proto": "https"})


# ===========================================================================
# (a) 経路ごと通す —— 本丸
# ===========================================================================
class TestOpenApiAdvertisesHttps:

    def test_servers_is_https_behind_caddy(self, monkeypatch):
        """★ 修正前はここが http://cciiumap.duckdns.org になり ChatGPT が弾いた。"""
        c = _caddy_client(monkeypatch)
        spec = _caddy_get(c, "/api/gpt/openapi.json").get_json()
        assert spec["servers"] == [{"url": f"https://{PROD_HOST}"}], (
            f"OpenAPI の servers が {spec['servers']}。"
            "ChatGPT の Action は https を要求するため取り込めない")

    def test_share_base_url_wins_when_set(self, monkeypatch):
        """明示設定があればそれが正 (共有リンクと同じ優先順位)。"""
        c = _caddy_client(monkeypatch, share_base_url="https://msi.example.com/")
        spec = _caddy_get(c, "/api/gpt/openapi.json").get_json()
        # 末尾スラッシュは落ちること
        assert spec["servers"] == [{"url": "https://msi.example.com"}]

    def test_openapi_needs_no_key(self, monkeypatch):
        """契約の取得は鍵不要のまま (ChatGPT が最初に読むのはここ)。"""
        c = _caddy_client(monkeypatch)
        assert _caddy_get(c, "/api/gpt/openapi.json").status_code == 200

    def test_the_two_paths_agree(self, monkeypatch):
        """★ 共有リンクと OpenAPI が **同じ 1 リクエストで同じ答え**を出すこと。

        これが欠陥の本体。片方だけ直すと、次に URL 解決を触ったとき
        また食い違う。
        """
        from app.services.share_manager import build_share_url
        c = _caddy_client(monkeypatch)
        app = flask.Flask(__name__)
        g.register_gpt_api(app)

        with app.test_request_context(
                "/api/gpt/openapi.json",
                base_url=f"http://{PROD_HOST}",
                headers={"X-Forwarded-Proto": "https"}):
            share = build_share_url("TOKEN")
        spec = _caddy_get(c, "/api/gpt/openapi.json").get_json()

        assert share.startswith(spec["servers"][0]["url"] + "/"), (
            f"共有リンク {share!r} と OpenAPI の servers "
            f"{spec['servers'][0]['url']!r} が食い違っている")


# ===========================================================================
# (b) 型の番人 — 公開 URL の出どころは 1 つ
# ===========================================================================
class TestOnlyOneSourceOfPublicUrl:
    """`request` から公開 URL を自前で組み立ててよいのは url_utils だけ。

    ★ 個別の再発テストではなく、**app/ の全ファイルを数える**番人。
      新しく `request.url_root` を使う箇所が増えたら落ちる。
    """

    # 直接参照を許す箇所。増やすときは理由を書くこと。
    # 「直したのに登録を消し忘れた」ときも落ちるよう、実在も検査する。
    KNOWN_DIRECT_REQUEST_URL = {
        "services/url_utils.py":
            "公開 URL 組み立ての唯一の出どころ。X-Forwarded-Proto を見る",
    }

    # 公開 URL を組み立てうる request の属性
    URL_ATTRS = {"url_root", "host_url", "base_url", "url", "host", "scheme"}

    @staticmethod
    def _offenders():
        found = {}
        for py in sorted(APP_DIR.rglob("*.py")):
            rel = py.relative_to(APP_DIR).as_posix()
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:                     # pragma: no cover
                continue
            hits = set()
            for n in ast.walk(tree):
                if (isinstance(n, ast.Attribute)
                        and isinstance(n.value, ast.Name)
                        and n.value.id == "request"
                        and n.attr in TestOnlyOneSourceOfPublicUrl.URL_ATTRS):
                    hits.add(n.attr)
            if hits:
                found[rel] = sorted(hits)
        return found

    def test_no_unregistered_direct_use(self):
        offenders = self._offenders()
        extra = {k: v for k, v in offenders.items()
                 if k not in self.KNOWN_DIRECT_REQUEST_URL}
        assert not extra, (
            f"`request` から公開 URL を自前で組み立てている箇所が増えた: {extra}。"
            "`SHARE_BASE_URL → url_utils.external_base_url()` を通すこと "
            "(ver52.6 で OpenAPI の servers が http:// になった原因)")

    def test_registry_has_no_dead_entries(self):
        """★ 直したのに登録を消し忘れたときも落ちること。"""
        offenders = self._offenders()
        dead = [k for k in self.KNOWN_DIRECT_REQUEST_URL if k not in offenders]
        assert not dead, (
            f"KNOWN_DIRECT_REQUEST_URL に実体の無い登録が残っている: {dead}")

    def test_gpt_api_goes_through_the_shared_helper(self):
        """`_openapi` / `_health` が `_public_base_url()` を呼んでいること。"""
        src = (APP_DIR / "services" / "gpt_api.py").read_text(encoding="utf-8")
        callers = set()
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(fn):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id == "_public_base_url"):
                    callers.add(fn.name)
        assert {"_openapi", "_health"} <= callers, (
            f"公開 URL の解決を通していない経路がある: {callers}")


# ===========================================================================
# (c) 型の番人 — 利用者が設定する環境変数が雛形に載っていること
# ===========================================================================
class TestEveryUserFacingEnvVarIsDocumented:
    """`GPT_API_KEY` が `.env.example` にも設定 UI にも無く、
    `.env.docker` ではコメントアウトされていた —— 利用者は
    「鍵を設定する」という手順の存在自体を知りようがなかった。

    鍵未設定なら `/api/gpt/*` は 503 (fail-closed、設計どおり) だが、
    `/health` は鍵不要で 200 を返すので「health は通るのに他は全部 503」
    としか見えない。**設定先が見つからないこと自体が欠陥**なので、
    個別に足すのではなく全数で表明する。
    """

    # 内部チューニング用。利用者が設定するものではないので雛形に載せない。
    KNOWN_INTERNAL_ONLY = {
        "ANALYSIS_BUSY_POLL_INTERVAL_SEC": "解析ビジー時のポーリング間隔 (内部既定で十分)",
        "DATA_EXPORT_TMP_DIR": "エクスポートの一時領域 (既定でコンテナ内 tmp)",
        "GPT_EXPORT_TMP_DIR": "GPT ダウンロードの一時領域 (既定でコンテナ内 tmp)",
    }

    @staticmethod
    def _env_names():
        tree = ast.parse((APP_DIR / "config.py").read_text(encoding="utf-8"))
        names = set()
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in ("get", "getenv")):
                continue
            v = n.func.value
            is_environ = ((isinstance(v, ast.Attribute) and v.attr == "environ")
                          or (isinstance(v, ast.Name) and v.id == "os"))
            if is_environ and n.args and isinstance(n.args[0], ast.Constant):
                names.add(n.args[0].value)
        return names

    @staticmethod
    def _documented_text():
        parts = []
        for p in (APP_DIR.parent / ".env.example",
                  REPO / ".env.docker",
                  REPO / "docker-compose.yml"):
            assert p.exists(), f"雛形が見つからない: {p}"
            parts.append(p.read_text(encoding="utf-8"))
        return "\n".join(parts)

    def test_all_env_vars_are_documented(self):
        text = self._documented_text()
        missing = sorted(n for n in self._env_names()
                         if n not in text and n not in self.KNOWN_INTERNAL_ONLY)
        assert not missing, (
            f"config.py が読むのに雛形にも compose にも載っていない環境変数: {missing}。"
            "利用者は設定先を見つけられない")

    def test_gpt_api_key_is_in_the_template(self):
        """★ 本件そのもの。`GPT_API_KEY` は **利用者が設定する**もの。"""
        example = (APP_DIR.parent / ".env.example").read_text(encoding="utf-8")
        assert "GPT_API_KEY" in example, (
            "App/.env.example に GPT_API_KEY が無い。"
            ".env をこの雛形から作る利用者は鍵の存在を知りようがなく、"
            "/api/gpt/* は 503 のままになる")
        assert "GPT_API_KEY" not in self.KNOWN_INTERNAL_ONLY, (
            "GPT_API_KEY を内部専用として登録してはいけない (本件の欠陥そのもの)")

    def test_docker_template_does_not_ship_a_usable_key(self):
        """★ `.env.docker` の鍵行はコメントのままであること。

        行頭 `#` を外しただけで「公開リポジトリに書いてある文字列」が
        有効な鍵になり、fail-closed の窓口が無防備に開く。
        """
        for line in (REPO / ".env.docker").read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("GPT_API_KEY="):
                assert not s.split("=", 1)[1].strip(), (
                    f".env.docker が値付きの鍵を配っている: {s!r}")

    def test_env_example_ships_an_empty_key(self):
        """雛形の既定は空 = 窓口は閉じたまま (fail-closed を壊さない)。"""
        for line in (APP_DIR.parent / ".env.example").read_text(
                encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("GPT_API_KEY="):
                assert not s.split("=", 1)[1].strip(), (
                    f".env.example の GPT_API_KEY に値が入っている: {s!r}")
                return
        pytest.fail(".env.example に GPT_API_KEY= の行が無い")


# ===========================================================================
# (d) /health は診断を出すが鍵は出さない
# ===========================================================================
class TestHealthDiagnoses:

    SECRET = "0123456789abcdef0123456789abcdef"

    def test_health_reports_the_url_it_advertises(self, monkeypatch):
        """★ 原因が見つからなかったのは、仕様書が何を名乗っているか
        確認する手段が無かったから。"""
        c = _caddy_client(monkeypatch, api_key=self.SECRET)
        body = _caddy_get(c, "/api/gpt/health").get_json()
        assert body["public_base_url"] == f"https://{PROD_HOST}"
        assert body["openapi_url"] == f"https://{PROD_HOST}/api/gpt/openapi.json"
        assert body["https"] is True
        assert body["gpt_api"] == "enabled"

    def test_health_flags_a_non_https_base(self, monkeypatch):
        """プロキシが X-Forwarded-Proto を送らない構成では False が立つこと。"""
        monkeypatch.setattr("app.config.SHARE_BASE_URL", "", raising=False)
        monkeypatch.setattr("app.config.GPT_API_KEY", "k", raising=False)
        app = flask.Flask(__name__)
        g.register_gpt_api(app)
        body = app.test_client().get(
            "/api/gpt/health", base_url=f"http://{PROD_HOST}").get_json()
        assert body["https"] is False, (
            "X-Forwarded-Proto の無い構成を https と誤判定している")

    def test_health_says_disabled_when_no_key(self, monkeypatch):
        c = _caddy_client(monkeypatch, api_key="")
        body = _caddy_get(c, "/api/gpt/health").get_json()
        assert body["gpt_api"] == "disabled"

    def test_health_never_leaks_the_key(self, monkeypatch):
        """★ 診断を足したことの副作用止め。鍵はサーバ側のみ (gpt_api.py の方針)。"""
        c = _caddy_client(monkeypatch, api_key=self.SECRET)
        raw = _caddy_get(c, "/api/gpt/health").get_data(as_text=True)
        assert self.SECRET not in raw, "health 応答に鍵が入っている"

    def test_openapi_never_leaks_the_key(self, monkeypatch):
        c = _caddy_client(monkeypatch, api_key=self.SECRET)
        raw = _caddy_get(c, "/api/gpt/openapi.json").get_data(as_text=True)
        assert self.SECRET not in raw, "OpenAPI 仕様書に鍵が入っている"
