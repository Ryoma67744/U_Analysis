#Requires -Version 5.1
<#
.SYNOPSIS
    解析が止まっているかどうかを PowerShell から確認する。

.DESCRIPTION
    「画面の進捗が動かない」ときに、それが
      (a) 本当に止まっている（プロセスが消えた）のか
      (b) 重い工程を計算中で、ログが伸びていないだけなのか
    を区別する。この 2 つは見え方が同じで対処が正反対のため、
    取り違えると完走間近の解析を自分で潰すことになる。

    実行場所は自動で判定する:
      - Docker コンテナ (既定: msi-analysis-app) が動いていればコンテナ内を見る
      - 無ければローカル (Windows ネイティブ実行) を見る

.PARAMETER Mode
    auto (既定) / docker / local

.PARAMETER Root
    解析結果の探索ルート。未指定ならアプリ設定（.env の OUTPUT_DATA_DIR など）と同じ場所を見る。

.PARAMETER StallMinutes
    ログも出力ファイルも更新されない時間がこの分数を超えたら「停滞の疑い」とする。
    既定 30 分。RPCA や DEG は無言で 30 分以上かかることがあるので、
    誤検出が多いようなら 60 などに伸ばす。

.EXAMPLE
    .\check_analysis.ps1
    .\check_analysis.ps1 -StallMinutes 60 -Tail 40
    .\check_analysis.ps1 -Mode local -Json
#>
[CmdletBinding()]
param(
    [ValidateSet('auto', 'local', 'docker')]
    [string]$Mode = 'auto',

    [string]$Container = 'msi-analysis-app',

    [double]$StallMinutes = 30,

    [int]$Tail = 15,

    [int]$Limit = 5,

    # 出力先を .env の OUTPUT_DATA_HOST 等で別の場所にしている場合に指定する。
    # 未指定ならアプリ設定と同じ場所を探す。
    [string[]]$Root,

    [switch]$RunningOnly,

    [switch]$Json
)

# 日本語が化けないようにする。Windows PowerShell 5.1 は既定で cp932 のため、
# コンテナ (UTF-8) の出力をそのまま受けると全部文字化けする。
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
    Write-Verbose "コンソールのエンコーディング設定に失敗（表示だけの問題）: $_"
}

$RepoRoot = $PSScriptRoot
$AppDir = Join-Path $RepoRoot 'App'
# Join-Path を入れ子にするのは、PowerShell 5.1 が複数要素の Join-Path に対応していないため
$ReporterLocal = Join-Path (Join-Path $AppDir 'tools') 'analysis_status_report.py'
$ReporterInContainer = '/app/App/tools/analysis_status_report.py'

function Write-Section {
    param([string]$Text)
    Write-Host ''
    Write-Host $Text -ForegroundColor Cyan
}

# ---------------------------------------------------------------------------
# 実行場所の判定
# ---------------------------------------------------------------------------

function Get-ContainerState {
    param([string]$Name)

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $null }

    $raw = & docker inspect $Name 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }

    try {
        $info = ($raw | ConvertFrom-Json)[0]
    } catch {
        return $null
    }
    return $info
}

$containerInfo = $null
if ($Mode -ne 'local') {
    $containerInfo = Get-ContainerState -Name $Container
}

$resolvedMode = $Mode
if ($Mode -eq 'auto') {
    if ($containerInfo -and $containerInfo.State.Running) {
        $resolvedMode = 'docker'
    } else {
        $resolvedMode = 'local'
    }
}

# ---------------------------------------------------------------------------
# コンテナ側の状況（解析が消える原因はほぼここに出る）
# ---------------------------------------------------------------------------

if ($containerInfo) {
    Write-Section "■ コンテナ ($Container)"

    $state = $containerInfo.State
    $stateColor = if ($state.Running) { 'Green' } else { 'Red' }
    Write-Host ("  状態          : {0}" -f $state.Status) -ForegroundColor $stateColor
    Write-Host ("  起動時刻      : {0}" -f $state.StartedAt)
    Write-Host ("  再起動回数    : {0}" -f $containerInfo.RestartCount)

    if ($state.Health) {
        Write-Host ("  ヘルスチェック: {0}" -f $state.Health.Status)
    }

    if ($state.OOMKilled) {
        Write-Host '  OOMKilled     : True' -ForegroundColor Red
        Write-Host '    → メモリ不足でコンテナごとカーネルに強制終了されています。' -ForegroundColor Red
        Write-Host '      docker-compose.yml の mem_limit を見直すか、' -ForegroundColor Red
        Write-Host '      .env の R_MAX_VSIZE_GB を設定して R 側で先に止めてください。' -ForegroundColor Red
    }

    if (-not $state.Running) {
        Write-Host ("  終了コード    : {0}" -f $state.ExitCode) -ForegroundColor Red
        Write-Host '  → コンテナが動いていないため、解析も確実に止まっています。' -ForegroundColor Red
        Write-Host '    docker compose up -d で起動し直してください。' -ForegroundColor Red
    }
} elseif ($Mode -eq 'docker') {
    Write-Host "コンテナ '$Container' が見つかりません（docker が無い / 名前違い）。" -ForegroundColor Red
    Write-Host 'docker ps -a で名前を確認するか、-Mode local を指定してください。' -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------------------
# 解析ジョブのレポート
# ---------------------------------------------------------------------------

$reporterArgs = @(
    '--stall-minutes', $StallMinutes,
    '--tail', $Tail,
    '--limit', $Limit
)
foreach ($r in $Root) { $reporterArgs += @('--root', $r) }
if ($RunningOnly) { $reporterArgs += '--running-only' }
if ($Json) { $reporterArgs += '--json' }

Write-Section "■ 解析ジョブ（$resolvedMode）"

$exitCode = 1
if ($resolvedMode -eq 'docker') {
    if (-not $containerInfo.State.Running) {
        Write-Host 'コンテナが停止しているため、ジョブの詳細は取得できません。' -ForegroundColor Yellow
        exit 3
    }
    & docker exec $Container python3 $ReporterInContainer @reporterArgs
    $exitCode = $LASTEXITCODE
} else {
    if (-not (Test-Path $ReporterLocal)) {
        Write-Host "レポートスクリプトが見つかりません: $ReporterLocal" -ForegroundColor Red
        exit 1
    }

    $python = $null
    foreach ($candidate in @('python', 'python3')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { $python = $cmd.Source; break }
    }
    if (-not $python -and (Get-Command py -ErrorAction SilentlyContinue)) {
        $python = 'py'
        $reporterArgs = @('-3', $ReporterLocal) + $reporterArgs
    }
    if (-not $python) {
        Write-Host 'Python が見つかりません。setup.bat を実行して環境を作ってください。' -ForegroundColor Red
        exit 1
    }

    # .env はアプリと同じ App/ から読ませる（出力先の外部パス指定を反映するため）
    Push-Location $AppDir
    try {
        if ($python -eq 'py') {
            & py @reporterArgs
        } else {
            & $python $ReporterLocal @reporterArgs
        }
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

# ---------------------------------------------------------------------------
# 結論
# ---------------------------------------------------------------------------

Write-Host ''
switch ($exitCode) {
    0 {
        Write-Host '結論: 解析は止まっていません（実行中、または正常に終了済み）。' -ForegroundColor Green
    }
    3 {
        Write-Host '結論: 解析は停止しています。' -ForegroundColor Red
        Write-Host '  次に見るもの:' -ForegroundColor Yellow
        if ($resolvedMode -eq 'docker') {
            Write-Host ("    docker logs --tail 200 {0}" -f $Container) -ForegroundColor Yellow
            Write-Host ("    docker inspect {0} --format '{{{{.State.OOMKilled}}}}'" -f $Container) -ForegroundColor Yellow
        }
        Write-Host '  上のログ末尾に [EXIT] 行があれば、そこに終了の理由が出ています。' -ForegroundColor Yellow
    }
    4 {
        Write-Host '結論: 進行が止まっているように見えます（停滞の疑い）。' -ForegroundColor Yellow
        Write-Host '  CPU 使用率が出ていれば、それが 0% かどうかで判断してください。' -ForegroundColor Yellow
        Write-Host ('  0% でないなら計算中です。-StallMinutes {0} のように閾値を伸ばせます。' -f ($StallMinutes * 2)) -ForegroundColor Yellow
    }
    5 {
        Write-Host '結論: 実行中の解析は見つかりませんでした。' -ForegroundColor Gray
        Write-Host '  一度も解析していないか、探索場所が違います（-Mode を切り替えて再確認）。' -ForegroundColor Gray
    }
    default {
        Write-Host "結論: 確認に失敗しました (exit=$exitCode)。" -ForegroundColor Red
    }
}

exit $exitCode
