# =============================================================================
# MSI Analysis Application - 解析の完了処理
#
# 「解析が終わったあとにやること」を 1 か所に集め、ブラウザの有無に依存せず
# 実行できるようにする。
#
# なぜ必要か:
#   これまで完了処理は analysis_callbacks.update_progress（dcc.Interval 駆動）
#   の中にしか無かった。この callback は app_state["is_running"] が無いと
#   冒頭で return するが、app_state は storage_type 未指定（= memory）の
#   dcc.Store なのでタブを閉じると消える。結果として **ブラウザを閉じた瞬間に
#   後片付けが永久に行われなくなり**、
#     - analysis_status.txt が running のまま残る
#     - receipt.json / RECEIPT.md が作られない
#     - 結果フォルダがプロジェクトに紐づかない（＝画面から見つけられない）
#     - 子プロセスが回収されずゾンビになる
#   という状態になっていた。
#
#   本モジュールの finalize() は冪等で、ウォッチャースレッド（サーバ側）と
#   ポーリング callback（ブラウザ側）のどちらから呼ばれても安全。
# =============================================================================

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.services import job_registry

logger = logging.getLogger("msi.analysis_finalizer")


def finalize(output_dir, *, status: str, job: Optional[dict] = None,
             source: str = "unknown") -> dict:
    """解析完了後の後片付けをまとめて行う。何度呼んでも安全。

    Parameters
    ----------
    output_dir : 結果フォルダ
    status     : "finished" | "error" | "stopped"
    job        : ジョブ台帳の内容。None なら output_dir から読む
    source     : 呼び出し元（"watcher" / "callback" / "startup"）。ログ用

    Returns
    -------
    {"done": bool, "skipped": bool, "errors": list[str]}
        done=True なら本呼び出しが後片付けを実施した。
        skipped=True は既に誰かが済ませていた（＝正常）。
    """
    result = {"done": False, "skipped": False, "errors": []}
    if not output_dir:
        result["errors"].append("output_dir が空です")
        return result

    # 二重実行の防止。台帳に印を付けられた側だけが実処理を行う。
    if not job_registry.mark_finalized(output_dir):
        logger.debug("完了処理は実施済み（%s からの呼び出しをスキップ）: %s",
                     source, output_dir)
        result["skipped"] = True
        return result

    job = job or job_registry.read_job(output_dir) or {}
    logger.info("解析の完了処理を開始 (%s, status=%s): %s",
                source, status, output_dir)

    if status == "finished":
        _link_to_project(output_dir, job, result)
        _write_receipt(output_dir, result)
    else:
        logger.info("status=%s のためプロジェクト登録とレシート生成は行いません",
                    status)

    result["done"] = True
    if result["errors"]:
        logger.warning("完了処理は一部失敗しました: %s", result["errors"])
    else:
        logger.info("解析の完了処理が完了しました: %s", output_dir)
    return result


def _link_to_project(output_dir, job: dict, result: dict) -> None:
    """結果フォルダをサブプロジェクトへ登録する。

    これが行われないと projects.json の last_result_dir と
    結果フォルダの _project_meta.json が更新されず、
    インタラクティブ画面から解析結果を見つけられなくなる。
    """
    proj_id = job.get("project_id") or ""
    sub_id = job.get("sub_project_id") or ""
    data_folder = job.get("data_folder") or ""
    if not (proj_id and sub_id):
        logger.info("プロジェクト未指定のため結果の紐づけは行いません")
        return
    try:
        from app.services.data_manager import has_msi_data
        from app.services.project_manager import (
            save_sub_project_result_dir, update_sub_project,
        )
        save_sub_project_result_dir(proj_id, sub_id, str(output_dir))
        # 解析に使った生データフォルダも保存しておく（出力時の自動推定を不要にする）。
        # 旧実装ではここが未定義変数を参照して NameError になっており、
        # 成功のたびに「結果ディレクトリの保存に失敗」と表示されていた。
        #
        # ★ ver62.4: 中身を見てから保存する。従来は真偽値だけで**無条件に上書き**して
        #   いたため、生データの無いフォルダ（サイドバーの既定値＝装置別データの
        #   ルート等）が来ると、**それまで正しかった登録が塗り潰されていた**。
        #   解析は成功しているので画面にはどこにも異常が出ず、次にデータ出力を
        #   押したときに初めて「入力ファイルが見つかりません」として現れる。
        #   実際にこれで TIMS プロジェクトの登録が `Data/DESI/Data` になった。
        #   間違った値で上書きするくらいなら、古くても正しい値を残す方がよい。
        if data_folder and has_msi_data(data_folder):
            update_sub_project(proj_id, sub_id, {"data_folder": data_folder})
        elif data_folder:
            logger.warning(
                "解析に使った生データフォルダに入力が見つからないため、"
                "サブプロジェクトの登録は更新しません: %s", data_folder)
        logger.info("結果をサブプロジェクトに登録: %s/%s", proj_id, sub_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("結果ディレクトリの保存に失敗")
        result["errors"].append(f"結果ディレクトリの保存に失敗: {e}")


def _write_receipt(output_dir, result: dict) -> None:
    """analysis_params.json と R サイドカーを 1 つのレシートに集約する。"""
    try:
        from app.services import receipt as _receipt
        from app.version import version_label
        _receipt.finalize_receipt(
            str(output_dir),
            app_version=version_label(),
            ended_at=datetime.now().isoformat(),
        )
        logger.info("解析レシートを作成: %s", output_dir)
    except Exception as e:  # noqa: BLE001
        logger.exception("解析レシートの作成に失敗")
        result["errors"].append(f"解析レシートの作成に失敗: {e}")


def reconcile_stale_jobs(search_roots) -> list:
    """アプリ起動時の後始末。

    「実行中の記録が残っているのにプロセスが居ない」ジョブを締める。
    コンテナ再起動などで R ごと殺された場合、これが無いと
    analysis_status.txt が running のまま永久に残り、
    何が起きたのか後から分からない。
    """
    closed = []
    try:
        stale = job_registry.find_stale_jobs(search_roots)
    except Exception as e:  # noqa: BLE001
        logger.warning("停止済みジョブの探索に失敗: %s", e)
        return closed

    for job in stale:
        output_dir = job.get("output_dir")
        if not output_dir:
            continue
        status_file = Path(output_dir) / "log" / "analysis_status.txt"
        prev = ""
        try:
            if status_file.is_file():
                prev = status_file.read_text(encoding="utf-8").strip()
        except OSError:
            prev = ""

        # 既に決着済み（finished/error/stopped）なら台帳に印だけ付けて終わり
        if prev in ("finished", "error", "stopped"):
            job_registry.mark_finalized(output_dir)
            continue

        logger.warning(
            "前回の解析がプロセス消失のまま残っていました（pid=%s, status=%r）: %s",
            job.get("pid"), prev, output_dir,
        )
        try:
            status_file.parent.mkdir(parents=True, exist_ok=True)
            status_file.write_text("error", encoding="utf-8")
        except OSError as e:
            logger.warning("ステータスの更新に失敗: %s", e)
        _append_log_note(
            output_dir,
            "[RECOVER] アプリの再起動時に、この解析のプロセスが存在しないことを"
            "検出しました。コンテナの再起動などで中断された可能性があります。"
            "ステータスを error に更新しました。",
        )
        finalize(output_dir, status="error", job=job, source="startup")
        closed.append(output_dir)

    if closed:
        logger.info("中断されていた解析 %d 件を締めました", len(closed))
    return closed


def _append_log_note(output_dir, message: str) -> None:
    """解析ログの末尾に一行足す。ユーザーが原因を見る場所に残すため。"""
    try:
        log_file = Path(output_dir) / "log" / "analysis_log.txt"
        if log_file.is_file():
            with open(log_file, "a", encoding="utf-8") as fh:
                fh.write(f"\n{message}\n")
    except OSError as e:
        logger.debug("解析ログへの追記に失敗（非重大）: %s", e)
