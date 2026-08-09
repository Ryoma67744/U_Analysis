"""ver52.7: `/api/gpt/*` が「なぜ失敗したか」を言えなかった。

■ 何が起きたか（実障害）

ChatGPT の Custom GPT から `listProjects` が失敗し続けた。返っていたのは

    invalid or missing API key (header: X-API-Key)

の 1 文だけ。**「鍵が無い」と「鍵が違う」を区別していない**ため、利用者は
どちらを直せばよいか分からない。実際に切り分けるには

    1. リバースプロキシ (Caddy) のアクセスログを掘る
    2. ChatGPT のリクエストだけを抜き出す
    3. `X-API-Key` ヘッダの有無を数える
    4. 送られてきた値の指紋を取ってサーバ側と比べる

が必要で、**6 往復かかった**。結果は「Action に古い 44 文字の値が入っていた」
——アプリの欠陥ではないが、**アプリが特定を助けられなかったこと**が欠陥。

■ なぜアプリ側で追えなかったか（実測した 4 つの穴）

    ⓐ `access_logger.log_access(status)` が**どこからも呼ばれていない**
       (docstring は「before_request で呼び」と書いてあるのに未結線)
    ⓐ' WSGI サーバが **waitress** でアクセスログを持たない (`run_app.py`)
       → ⓐ と合わせて **HTTP リクエストの記録が 1 本も存在しない**
    ⓑ `/api/gpt/*` に **errorhandler が無い** → 想定外例外は HTML 500 になり、
       JSON の契約 (`{"ok": false, ...}`) を破る
    ⓒ `_gpt_before_request` が 401/503 を**ログに残さない**

■ ここで固定すること

鍵そのものは**絶対に出さない**。出すのは真偽値と有無だけ。
"""

import logging

import flask
import pytest

from app.services import gpt_api as g

PROD_HOST = "cciiumap.duckdns.org"

# 実障害と同じ形: 正しい鍵 (hex 64) と、Action に残っていた別の値 (base64 44)
GOOD_KEY = "c" * 64
WRONG_KEY = "d" * 44


def _client(monkeypatch, api_key=GOOD_KEY):
    monkeypatch.setattr("app.config.SHARE_BASE_URL", "", raising=False)
    monkeypatch.setattr("app.config.GPT_API_KEY", api_key, raising=False)
    app = flask.Flask(__name__)
    g.register_gpt_api(app)
    return app.test_client()


def _get(client, path, key=None):
    headers = {"X-Forwarded-Proto": "https"}
    if key is not None:
        headers["X-API-Key"] = key
    return client.get(path, base_url=f"http://{PROD_HOST}", headers=headers)


# ===========================================================================
# ⓒ 認証の結線状況が 1 回の呼び出しで分かること
# ===========================================================================
class TestHealthAnswersWhetherAuthIsWired:
    """`/api/gpt/health` は鍵不要なので、**認証を設定した Action なら
    header が届く**。そこを見れば切り分けられる。"""

    def test_no_header_says_not_received(self, monkeypatch):
        """★ 「Action の認証が未設定」の形。実障害の第 1 段階がこれだった。"""
        body = _get(_client(monkeypatch), "/api/gpt/health").get_json()
        assert body["key_header_received"] is False
        assert body["authenticated"] is False

    def test_wrong_key_says_received_but_not_authenticated(self, monkeypatch):
        """★★ 本丸。実障害の第 2 段階——**届いてはいるが値が違う**。

        従来はこれも 401 の `invalid or missing` としか出ず、
        「送っていない」のか「違う値を送っている」のか判別できなかった。
        """
        body = _get(_client(monkeypatch), "/api/gpt/health",
                    key=WRONG_KEY).get_json()
        assert body["key_header_received"] is True, "ヘッダの到達を見ていない"
        assert body["authenticated"] is False, "違う鍵を通してしまっている"

    def test_correct_key_says_authenticated(self, monkeypatch):
        body = _get(_client(monkeypatch), "/api/gpt/health",
                    key=GOOD_KEY).get_json()
        assert body["key_header_received"] is True
        assert body["authenticated"] is True

    def test_unconfigured_server_is_never_authenticated(self, monkeypatch):
        """鍵未設定 (fail-closed) のとき、何を送っても認証済みにしない。

        ★ 空文字どうしが `compare_digest` で一致してしまう事故を止める。
        """
        for sent in ("", WRONG_KEY, GOOD_KEY):
            body = _get(_client(monkeypatch, api_key=""), "/api/gpt/health",
                        key=sent).get_json()
            assert body["gpt_api"] == "disabled"
            assert body["authenticated"] is False, f"送信値={sent!r}"

    def test_health_stays_key_free(self, monkeypatch):
        """診断を足しても health 自体は鍵不要のままであること。"""
        assert _get(_client(monkeypatch), "/api/gpt/health").status_code == 200


class TestNothingLeaksTheKey:
    """★ 診断を足したことの副作用止め。鍵はサーバ側のみ (モジュール冒頭の方針)。"""

    @pytest.mark.parametrize("path", ["/api/gpt/health", "/api/gpt/openapi.json"])
    def test_key_free_endpoints_do_not_echo_the_key(self, monkeypatch, path):
        raw = _get(_client(monkeypatch), path, key=GOOD_KEY).get_data(as_text=True)
        assert GOOD_KEY not in raw, f"{path} の応答に鍵が入っている"

    def test_rejection_body_does_not_echo_the_key(self, monkeypatch):
        raw = _get(_client(monkeypatch), "/api/gpt/projects",
                   key=WRONG_KEY).get_data(as_text=True)
        assert WRONG_KEY not in raw, "401 応答が受け取った値をそのまま返している"


# ===========================================================================
# ⓒ 拒否をサーバ側のログに残すこと
# ===========================================================================
class TestRejectionsAreLogged:
    """★ これが無いと、運用側は「拒否した」事実すら知れない。

    実障害では、リバースプロキシのログを掘るまで
    「ChatGPT が鍵を送っていない」ことが分からなかった。
    """

    def _reject(self, monkeypatch, caplog, key=None, api_key=GOOD_KEY):
        with caplog.at_level(logging.WARNING, logger="msi.gpt_api"):
            r = _get(_client(monkeypatch, api_key=api_key),
                     "/api/gpt/projects", key=key)
        return r, [rec.getMessage() for rec in caplog.records]

    def test_missing_key_is_logged_as_absent(self, monkeypatch, caplog):
        r, msgs = self._reject(monkeypatch, caplog, key=None)
        assert r.status_code == 401
        hit = [m for m in msgs if "GPT API 拒否" in m]
        assert hit, f"401 を返したのにログが無い: {msgs}"
        assert "なし" in hit[0], hit[0]

    def test_wrong_key_is_logged_as_present(self, monkeypatch, caplog):
        """★ 「届いているが違う」をログでも区別できること。"""
        r, msgs = self._reject(monkeypatch, caplog, key=WRONG_KEY)
        assert r.status_code == 401
        hit = [m for m in msgs if "GPT API 拒否" in m]
        assert hit, f"401 を返したのにログが無い: {msgs}"
        assert "あり" in hit[0], hit[0]

    def test_unconfigured_server_logs_the_503(self, monkeypatch, caplog):
        r, msgs = self._reject(monkeypatch, caplog, key=GOOD_KEY, api_key="")
        assert r.status_code == 503
        assert any("GPT API 拒否" in m for m in msgs), msgs

    def test_the_log_never_contains_the_key(self, monkeypatch, caplog):
        """★★ 有無だけを出す。値も先頭数文字も出さない。

        ログは平文でディスクに残り `docker logs` でも読めるので、
        ここに鍵が出ると「サーバ側だけに保持」の方針が崩れる。
        """
        _, msgs = self._reject(monkeypatch, caplog, key=WRONG_KEY)
        blob = "\n".join(msgs)
        assert WRONG_KEY not in blob, "受け取った値がログに出ている"
        assert GOOD_KEY not in blob, "サーバ側の鍵がログに出ている"
        assert WRONG_KEY[:8] not in blob, "受け取った値の先頭がログに出ている"

    def test_the_log_line_has_a_fixed_shape(self, monkeypatch, caplog):
        """★ 出す項目を**白紙のリストではなく固定書式**で縛る。

        「鍵を出さない」を禁止語の列挙で守ろうとすると、次に項目を足した
        誰かが素通りする（長さ・先頭 8 文字・ハッシュなど、値そのものでは
        ないが手がかりになるものはいくらでもある）。書式そのものを固定して、
        **項目を足したらこのテストが落ちる**ようにする。
        """
        import re
        _, msgs = self._reject(monkeypatch, caplog, key=WRONG_KEY)
        hit = [m for m in msgs if "GPT API 拒否" in m]
        assert len(hit) == 1, hit
        assert re.fullmatch(
            r"GPT API 拒否: path=/api/gpt/\S+ status=\d{3} X-API-Key=(あり|なし)",
            hit[0]), (
            f"ログの書式が変わった: {hit[0]!r}。"
            "項目を足すときは鍵の手がかりにならないか確認し、"
            "この番人の正規表現も更新すること")

    def test_success_is_not_logged_as_a_rejection(self, monkeypatch, caplog):
        """通った要求を拒否として記録しないこと（ログの信頼性）。"""
        with caplog.at_level(logging.WARNING, logger="msi.gpt_api"):
            _get(_client(monkeypatch), "/api/gpt/health", key=GOOD_KEY)
        assert not [r for r in caplog.records if "GPT API 拒否" in r.getMessage()]
