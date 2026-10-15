# v6 変換生成用の固定環境

★ ver81.0: 現行変換コードは旧結果のspecと異なるため、旧コードと依存を独立して保持する。

`source/` は commit `b34fc86b942d405f4b52d54f0a91f11e7a5f9542` の Git blob を LF のまま格納したもの。`profile.json` に全ファイルのSHA256を固定する。6本の変換関連ファイルは ver74.1 の保存specと一致する。旧コードの改修、整形、改行変換は禁止し、追加profileは別ディレクトリにする。

専用venvはCPython 3.12で作成する。現行アプリのsite-packagesを共有しない。Dockerでは `/opt/ua-recovery-v6` に同梱し、ローカル検証のみ `MSI_IMZML_RECOVERY_V6_PYTHON` に専用venvの実行ファイルを指定できる。環境変数はサーバー設定用であり、HTTP/ジョブ要求の実行コマンドとしては受け取らない。

構築確認は専用Pythonから `-I -B -X utf8 App/tools/imzml_recovery_generate.py --probe --runtime-info <venv>/runtime-info.json` を実行する。専用コードの `.pyc` と追加された未承認 `.py` は受け付けない。実行ユーザーには構築記録の読み取り権限を与える。

`imzml_recovery_generate.py` は登録仕様とコード・依存版を照合して `checked_import` の生成・検証だけを呼ぶ。旧 `prepare_imzml` による最終保存先への公開は行わない。実行許可gateが届く前に変換を始めない。これはPython依存・importの分離であり、OSの権限sandboxではない。

専用環境を準備できない場合は unavailable とし、現行依存への置換や実行時のネットワーク取得は行わない。旧依存のWindows CPython 3.12 wheelとLinux x86_64 CPython 3.12 wheelの取得を開発時に確認した。実データの復元成功は別途、保存されたParquet・対応JSONのサイズとSHA256の完全一致で判定する。
