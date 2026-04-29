# screener_dashboard_server.ps1
# Live S&P 500 Mean-Reversion + RSI Loop Dashboard
# http://localhost:8766/  |  /run (screener)  |  /run-rsi (rsi loop)  |  /stop

param([int]$Port = 8766, [int]$RefreshSec = 60)

$ProjectDir  = $PSScriptRoot
$ResultsJson = "$ProjectDir\screener_results.json"
$PendingJson = "$ProjectDir\pending_entries.json"
$ScreenerPy  = "$ProjectDir\screener.py"
$RsiMainPy   = "$ProjectDir\rsi_loop\rsi_main.py"
$LogsDir     = "$ProjectDir\logs"

Add-Type -AssemblyName System.Web

# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function EscHtml($s) { [System.Web.HttpUtility]::HtmlEncode("$s") }
function PLC($v)  { if ([double]$v -ge 0) { "#16a34a" } else { "#dc2626" } }
function Fmtp($v) { $v=[double]$v; $s=if($v -ge 0){"+"} else {""}; "$s$([Math]::Round($v,2))%" }

# Background job tracking
$script:runnerJob = $null
$script:rsiJob    = $null

function Start-Screener {
    if ($script:runnerJob -and $script:runnerJob.State -eq 'Running') { return "already_running" }
    $script:runnerJob = Start-Job -ScriptBlock {
        param($py, $logDir)
        $ts  = Get-Date -Format "yyyyMMdd_HHmmss"
        $out = "$logDir\screener_$ts.log"
        New-Item -Path (Split-Path $out) -ItemType Directory -Force | Out-Null
        "=== Screener started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File $out -Encoding UTF8
        py -3 $py 2>&1 | Out-File $out -Append -Encoding UTF8
        "=== done (exit $LASTEXITCODE) $(Get-Date -Format 'HH:mm:ss') ===" | Out-File $out -Append -Encoding UTF8
    } -ArgumentList $ScreenerPy, $LogsDir
    return "started"
}

function Start-RsiLoop {
    if ($script:rsiJob -and $script:rsiJob.State -eq 'Running') { return "already_running" }
    $script:rsiJob = Start-Job -ScriptBlock {
        param($py, $logDir)
        $ts  = Get-Date -Format "yyyyMMdd_HHmmss"
        $out = "$logDir\rsi_loop_$ts.log"
        New-Item -Path (Split-Path $out) -ItemType Directory -Force | Out-Null
        "=== RSI Loop started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File $out -Encoding UTF8
        py -3 $py --no-screener 2>&1 | Out-File $out -Append -Encoding UTF8
        "=== done (exit $LASTEXITCODE) $(Get-Date -Format 'HH:mm:ss') ===" | Out-File $out -Append -Encoding UTF8
    } -ArgumentList $RsiMainPy, $LogsDir
    return "started"
}

function Get-RunnerStatus {
    if (-not $script:runnerJob) { return "idle" }
    if ($script:runnerJob.State -eq 'Running') { return "running" }
    return "idle"
}

function Get-RsiStatus {
    if (-not $script:rsiJob) { return "idle" }
    if ($script:rsiJob.State -eq 'Running') { return "running" }
    return "idle"
}

# â”€â”€ Filter badges â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function Get-FilterBadges($stock) {
    $f = $stock.filters
    $b = ""
    $b += if ($f.above_200ma)  { "<span class='by'>200MA</span>" } else { "<span class='bn'>200MA</span>" }
    $b += if ($f.below_bb)     { "<span class='by'>BelowBB</span>" } else { "<span class='bn'>BelowBB</span>" }
    $b += if ($f.rsi_oversold) { "<span class='by'>RSI</span>" } else { "<span class='bn'>RSI</span>" }
    $b += if ($f.volume_ok)    { "<span class='by'>Vol</span>" } else { "<span class='bn'>Vol</span>" }
    return $b
}

# â”€â”€ Score bar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function Get-ScoreBar($score) {
    $pct = [Math]::Round((1.0 - [double]$score) * 100)
    $col = if ($pct -gt 60) { "#22c55e" } elseif ($pct -gt 40) { "#f59e0b" } else { "#ef4444" }
    $sc  = [Math]::Round([double]$score, 3)
    return "<div class='sbar'><div class='sbfill' style='background:$col;width:$pct%'></div></div><span class='snum'>$sc</span>"
}

# â”€â”€ Build HTML â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function Build-HTML {
    $now       = Get-Date -Format "ddd MMM d, yyyy  h:mm:ss tt"
    $runStatus = Get-RunnerStatus
    $rsiStatus = Get-RsiStatus

    # â”€â”€ Load JSON files â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    $results = $null; $pending = $null; $scfg = $null
    if (Test-Path $ResultsJson) { try { $results = Get-Content $ResultsJson -Raw | ConvertFrom-Json } catch {} }
    if (Test-Path $PendingJson) { try { $pending = Get-Content $PendingJson -Raw | ConvertFrom-Json } catch {} }
    $cfgPath = "$ProjectDir\screener_config.json"
    if (Test-Path $cfgPath) { try { $scfg = Get-Content $cfgPath -Raw | ConvertFrom-Json } catch {} }

    $regimeData = $null
    if (Test-Path "$ProjectDir\market_regime.json") {
        try { $regimeData = Get-Content "$ProjectDir\market_regime.json" -Raw | ConvertFrom-Json } catch {}
    }

    $cfgHistory = @()
    if (Test-Path "$ProjectDir\config_history.json") {
        try {
            $raw = Get-Content "$ProjectDir\config_history.json" -Raw
            if ($raw.Trim() -ne "") { $cfgHistory = $raw | ConvertFrom-Json }
        } catch {}
    }

    $rsiReport = $null
    if (Test-Path "$ProjectDir\improvement_report.json") {
        try { $rsiReport = Get-Content "$ProjectDir\improvement_report.json" -Raw | ConvertFrom-Json } catch {}
    }

    $picksHistory = $null
    if (Test-Path "$ProjectDir\picks_history.json") {
        try { $picksHistory = Get-Content "$ProjectDir\picks_history.json" -Raw | ConvertFrom-Json } catch {}
    }

    $researchPicks = $null
    if (Test-Path "$ProjectDir\research_picks.json") {
        try { $researchPicks = Get-Content "$ProjectDir\research_picks.json" -Raw | ConvertFrom-Json } catch {}
    }

    # â”€â”€ Config display values â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    $rsiThr = if ($scfg) { $scfg.indicators.rsi_oversold }      else { 35 }
    $rsiPer = if ($scfg) { $scfg.indicators.rsi_period }        else { 14 }
    $bbPer  = if ($scfg) { $scfg.indicators.bb_period }         else { 20 }
    $bbStd  = if ($scfg) { $scfg.indicators.bb_std }            else { 2.0 }
    $maPer  = if ($scfg) { $scfg.indicators.ma_trend_period }   else { 200 }
    $volMin = if ($scfg) { $scfg.indicators.volume_ratio_min }  else { 1.5 }
    $posSz  = if ($scfg) { $scfg.auto_entry.position_size_usd } else { 1000 }
    $maxPos = if ($scfg) { $scfg.max_positions }                else { 10 }

    # â”€â”€ Screener summary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    $runDate  = if ($results) { $results.run_time_utc } else { "N/A" }
    $screened = if ($results) { $results.screened }     else { "--" }
    $passed   = if ($results) { $results.passed }       else { "--" }
    $radarCnt = if ($results) { $results.radar_count }  else { "--" }
    $universe = if ($results) { $results.universe }     else { "SP500" }

    $passedColor = if ($results -and [int]$results.passed -gt 0) { "#16a34a" } else { "#94a3b8" }
    $passedBg    = if ($results -and [int]$results.passed -gt 0) { "#f0fdf4" } else { "#f8fafc" }
    $passedBord  = if ($results -and [int]$results.passed -gt 0) { "#86efac" } else { "#e2e8f0" }

    # â”€â”€ Screener button/status â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    $isRunning   = ($runStatus -eq "running")
    $runBtnText  = if ($isRunning) { "Running..." } else { "Run Screener" }
    $runBtnHref  = if ($isRunning) { "#" } else { "/run" }
    $runBtnCol   = if ($isRunning) { "#f59e0b" } else { "#2563eb" }
    $statusText  = if ($isRunning) { "RUNNING" } else { "IDLE" }
    $statusBg    = if ($isRunning) { "#fef3c7" } else { "#f1f5f9" }
    $statusCol   = if ($isRunning) { "#d97706" } else { "#94a3b8" }
    $statusBord  = if ($isRunning) { "#f59e0b" } else { "#e2e8f0" }

    # â”€â”€ RSI loop button/status â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    $rsiRunning  = ($rsiStatus -eq "running")
    $rsiBtnText  = if ($rsiRunning) { "RSI Running..." } else { "Run RSI Loop" }
    $rsiBtnHref  = if ($rsiRunning) { "#" } else { "/run-rsi" }
    $rsiBtnCol   = if ($rsiRunning) { "#f59e0b" } else { "#7c3aed" }
    $rsiStatText = if ($rsiRunning) { "RUNNING" } else { "IDLE" }
    $rsiStatBg   = if ($rsiRunning) { "#fef3c7" } else { "#f5f3ff" }
    $rsiStatCol  = if ($rsiRunning) { "#d97706" } else { "#7c3aed" }
    $rsiStatBord = if ($rsiRunning) { "#f59e0b" } else { "#ddd6fe" }

    # â”€â”€ Top Picks rows â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    $pickRows = ""
    if ($results -and $results.top_picks -and $results.top_picks.Count -gt 0) {
        $rank = 1
        foreach ($s in $results.top_picks) {
            $bbClr = if ($s.pct_below_bb -lt 0) { "#16a34a" } else { "#ef4444" }
            $maClr = PLC $s.pct_above_200ma
            $sb    = Get-ScoreBar $s.composite_score
            $fb    = Get-FilterBadges $s
            $pickRows += "<tr><td style='text-align:center;font-weight:800'>$rank</td><td><span class='sym'>$($s.symbol)</span></td><td class='num' style='font-weight:700'>`$$($s.price)</td><td class='num' style='color:#7c3aed;font-weight:700'>$($s.rsi)</td><td class='num' style='color:$bbClr;font-weight:700'>$(Fmtp $s.pct_below_bb)</td><td class='num' style='color:$maClr'>$(Fmtp $s.pct_above_200ma)</td><td class='num'>$($s.vol_ratio)x</td><td>$sb</td><td>$fb</td></tr>"
            $rank++
        }
    }
    $pickSection = if ($pickRows) {
        "<table><thead><tr><th style='text-align:center'>#</th><th>Symbol</th><th class='num'>Price</th><th class='num'>RSI</th><th class='num'>% vs BB</th><th class='num'>% vs 200MA</th><th class='num'>Vol Ratio</th><th>Score</th><th>Filters</th></tr></thead><tbody>$pickRows</tbody></table>"
    } else {
        "<div class='nopicks'><p style='font-size:15px;font-weight:700;color:#64748b;margin-bottom:8px'>No stocks passed all 4 filters</p><p style='font-size:12px;color:#94a3b8'>Market is not offering clean mean-reversion setups. Check Radar below for approaching setups.</p></div>"
    }

    # â”€â”€ Radar rows â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    $radarRows = ""
    if ($results -and $results.radar -and $results.radar.Count -gt 0) {
        foreach ($s in $results.radar) {
            $bbClr = if ($s.pct_below_bb -lt 0) { "#16a34a" } else { "#ef4444" }
            $maClr = PLC $s.pct_above_200ma
            $fp    = $s.filters_passed
            $fpClr = if ($fp -ge 3) { "#2563eb" } else { "#64748b" }
            $fb    = Get-FilterBadges $s
            $radarRows += "<tr><td><span class='sym2'>$($s.symbol)</span></td><td class='num' style='font-weight:600'>`$$($s.price)</td><td class='num' style='color:#7c3aed'>$($s.rsi)</td><td class='num' style='color:$bbClr'>$(Fmtp $s.pct_below_bb)</td><td class='num' style='color:$maClr'>$(Fmtp $s.pct_above_200ma)</td><td class='num'>$($s.vol_ratio)x</td><td style='text-align:center;color:$fpClr;font-weight:700'>$fp/4</td><td>$fb</td></tr>"
        }
    } else {
        $radarRows = "<tr><td colspan='8' style='text-align:center;color:#94a3b8;padding:16px;font-size:12px'>No radar stocks</td></tr>"
    }

    # â”€â”€ Pending Entries section â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    $pendingSection = ""
    if ($pending) {
        $pStatus = "$($pending.status)"
        $pExec   = "$($pending.executes_at_utc)"
        $pGen    = "$($pending.generated_utc)"
        $pSz     = "$($pending.position_size_usd)"
        $stCol  = switch ($pStatus) { "pending" {"#d97706"} "executed" {"#16a34a"} "cancelled" {"#dc2626"} default {"#64748b"} }
        $stBg   = switch ($pStatus) { "pending" {"#fef3c7"} "executed" {"#dcfce7"} "cancelled" {"#fee2e2"} default {"#f1f5f9"} }
        $stBord = switch ($pStatus) { "pending" {"#f59e0b"} "executed" {"#86efac"} "cancelled" {"#fca5a5"} default {"#e2e8f0"} }

        $eRows = ""
        $entries = $pending.entries
        if ($entries -and $entries.Count -gt 0) {
            foreach ($e in $entries) {
                $skipCol  = if ($e.skip) { "#ef4444" } else { "#16a34a" }
                $skipText = if ($e.skip) { "SKIP" } else { "ENTER" }
                $eRows += "<tr><td style='text-align:center;font-weight:700'>$($e.rank)</td><td><span class='sym2'>$($e.symbol)</span></td><td class='num'>`$$($e.screened_price)</td><td class='num' style='color:#7c3aed'>$($e.rsi)</td><td class='num'>$($e.planned_shares)</td><td class='num'>`$$($e.planned_usd)</td><td style='text-align:center;color:$skipCol;font-weight:700;font-size:11px'>$skipText</td></tr>"
            }
        } else {
            $eRows = "<tr><td colspan='7' style='text-align:center;color:#94a3b8;padding:12px;font-size:12px'>No entries (no picks passed all 4 filters)</td></tr>"
        }

        $pendingSection = @"
<div class="panel">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
    <h2 style="margin:0">Auto-Entry Queue</h2>
    <span style="background:$stBg;color:$stCol;border:1px solid $stBord;border-radius:12px;font-size:11px;font-weight:700;padding:3px 12px;text-transform:uppercase">$pStatus</span>
  </div>
  <div style="font-size:11px;color:#64748b;margin-bottom:14px">Generated: <strong style="color:#1e293b">$pGen</strong> &nbsp;|&nbsp; Executes: <strong style="color:#1e293b">$pExec</strong> &nbsp;|&nbsp; Size: <strong style="color:#1e293b">`$$pSz</strong></div>
  <table>
    <thead><tr><th style="text-align:center">#</th><th>Symbol</th><th class="num">Price</th><th class="num">RSI</th><th class="num">Shares</th><th class="num">Est. USD</th><th style="text-align:center">Action</th></tr></thead>
    <tbody>$eRows</tbody>
  </table>
</div>
"@
    }

    # â”€â”€ Performance Tracker pre-compute â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    $totalPicks  = 0
    $filledPicks = 0
    $perfRows    = ""
    $phUpdated   = "Never"

    if ($picksHistory -and $picksHistory.picks) {
        $totalPicks = $picksHistory.picks.Count

        foreach ($px in $picksHistory.picks) {
            $rv5 = $px.returns."5d"
            if ($null -ne $rv5 -and "$rv5" -ne "") { $filledPicks++ }
        }

        $phRaw = "$($picksHistory.last_updated)"
        if ($phRaw.Length -ge 16) { $phUpdated = $phRaw.Substring(0,16).Replace("T"," ") + " UTC" }

        $sortedPicks = $picksHistory.picks | Sort-Object { $_.screened_date } -Descending
        foreach ($p in $sortedPicks) {
            $sym    = EscHtml "$($p.symbol)"
            $dt     = EscHtml "$($p.screened_date)"
            $reg    = EscHtml "$($p.regime)"
            $ep     = [Math]::Round([double]$p.entry_price, 2)
            $fp     = "$($p.filters_passed)"
            $src    = "$($p.source)"
            $srcBg  = if ($src -eq "top_picks") { "#dcfce7" } else { "#dbeafe" }
            $srcCol = if ($src -eq "top_picks") { "#16a34a" } else { "#1d4ed8" }
            $srcLbl = if ($src -eq "top_picks") { "PICK" } else { "RADAR" }

            $cells = ""
            foreach ($h in @("1d","5d","10d","20d")) {
                $rv = $p.returns.$h
                if ($null -eq $rv -or "$rv" -eq "") {
                    $cells += "<td class='num' style='color:#cbd5e1;font-size:10px'>--</td>"
                } else {
                    $dv  = [double]$rv
                    $col = if ($dv -ge 0) { "#16a34a" } else { "#dc2626" }
                    $sgn = if ($dv -ge 0) { "+" } else { "" }
                    $cells += "<td class='num' style='color:$col;font-weight:600;font-size:11px'>$sgn$([Math]::Round($dv,2))%</td>"
                }
            }

            $perfRows += "<tr><td><span class='sym2'>$sym</span></td><td style='font-size:11px;color:#374151;white-space:nowrap'>$dt</td><td><span style='background:$srcBg;color:$srcCol;font-size:9px;font-weight:700;padding:2px 6px;border-radius:4px;white-space:nowrap'>$srcLbl</span></td><td style='font-size:10px;color:#64748b'>$reg</td><td class='num' style='font-size:11px'>`$$ep</td><td style='text-align:center;font-size:10px;color:#64748b'>$fp/4</td>$cells</tr>"
        }
    }

    if (-not $perfRows) {
        $perfRows = "<tr><td colspan='10' style='text-align:center;color:#94a3b8;padding:24px;font-size:12px'>No picks tracked yet &mdash; run the screener, then Run RSI Loop to begin.</td></tr>"
    }

    $pendingReturns = $totalPicks - $filledPicks
    $perfSubtitle   = if ($totalPicks -gt 0) {
        "$totalPicks tracked &nbsp;&bull;&nbsp; $filledPicks with 5d data &nbsp;&bull;&nbsp; $pendingReturns pending &nbsp;&bull;&nbsp; $phUpdated"
    } else { "No data yet" }

    # â”€â”€ RSI panels pre-compute â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    $rsiPanels = ""

    # Panel A: Market Regime
    if ($regimeData) {
        $regimeName = "$($regimeData.regime)"
        $regimeBadgeColor = switch ($regimeName) {
            "bull"               { "#16a34a" }
            "mild_correction"    { "#f59e0b" }
            "correction"         { "#f97316" }
            "recovery"           { "#3b82f6" }
            "geopolitical_shock" { "#dc2626" }
            "bear"               { "#7f1d1d" }
            default              { "#94a3b8" }
        }
        $spyM      = $regimeData.spy_metrics
        $vixyM     = $regimeData.vixy_metrics
        $vixyElev  = if ($vixyM.vix_elevated) { "Yes" } else { "No" }
        $vixyElCol = if ($vixyM.vix_elevated) { "#dc2626" } else { "#16a34a" }
        $vs200     = EscHtml "$($spyM.spy_vs_200ma_pct)%"
        $ret20     = EscHtml "$($spyM.spy_20d_return_pct)%"
        $ret5      = EscHtml "$($spyM.spy_5d_return_pct)%"
        $compAt    = EscHtml "$($regimeData.computed_at)"
        $rsiPanels += @"
<div class="panel">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
    <h2 style="margin:0">Market Regime</h2>
    <span style="background:$regimeBadgeColor;color:#fff;border-radius:12px;font-size:11px;font-weight:700;padding:3px 12px;text-transform:uppercase;letter-spacing:0.5px">$regimeName</span>
  </div>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:12px">
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px">
      <div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.8px;font-weight:600;margin-bottom:4px">SPY vs 200MA</div>
      <div style="font-size:16px;font-weight:800;color:#0f172a">$vs200</div>
    </div>
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px">
      <div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.8px;font-weight:600;margin-bottom:4px">SPY 20d Return</div>
      <div style="font-size:16px;font-weight:800;color:#0f172a">$ret20</div>
    </div>
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px">
      <div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.8px;font-weight:600;margin-bottom:4px">SPY 5d Return</div>
      <div style="font-size:16px;font-weight:800;color:#0f172a">$ret5</div>
    </div>
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px">
      <div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.8px;font-weight:600;margin-bottom:4px">VIXY Elevated</div>
      <div style="font-size:16px;font-weight:800;color:$vixyElCol">$vixyElev</div>
    </div>
  </div>
  <div style="font-size:10px;color:#94a3b8">Computed: $compAt</div>
</div>
"@
    } else {
        $rsiPanels += @"
<div class="panel">
  <h2>Market Regime</h2>
  <div style="color:#94a3b8;font-size:12px;padding:8px 0">Click <strong>Run RSI Loop</strong> to detect regime</div>
</div>
"@
    }

    # Panel B: Config Evolution
    $lastFive = if ($cfgHistory.Count -gt 5) { $cfgHistory[($cfgHistory.Count-5)..($cfgHistory.Count-1)] } else { $cfgHistory }
    if ($lastFive -and $lastFive.Count -gt 0) {
        $cfgRows = ""
        foreach ($entry in ($lastFive | Sort-Object { $_.timestamp } -Descending)) {
            $ts     = EscHtml ($entry.timestamp.ToString().Substring(0,[Math]::Min(16,$entry.timestamp.ToString().Length)).Replace("T"," "))
            $eReg   = EscHtml "$($entry.regime)"
            $mMeth  = "$($entry.method)"
            $mBg    = if ($mMeth -eq "data_derived") { "#dbeafe" } else { "#f1f5f9" }
            $mCol   = if ($mMeth -eq "data_derived") { "#1d4ed8" } else { "#64748b" }
            $mLbl   = EscHtml $mMeth
            $chgs   = $entry.changes
            $chgTxt = if ($chgs -and $chgs.Count -gt 0) { EscHtml ($chgs -join "; ") } else { "<span style='color:#94a3b8;font-style:italic'>no changes</span>" }
            $cfgRows += "<tr><td style='font-size:10px;font-family:Consolas,monospace;color:#374151;white-space:nowrap'>$ts</td><td><span class='sym2'>$eReg</span></td><td><span style='background:$mBg;color:$mCol;font-size:9px;font-weight:700;padding:2px 7px;border-radius:5px'>$mLbl</span></td><td style='font-size:10px;color:#374151;max-width:340px;overflow:hidden;text-overflow:ellipsis'>$chgTxt</td></tr>"
        }
        $rsiPanels += @"
<div class="panel">
  <h2>Config Evolution &mdash; Last 5 Optimizations</h2>
  <table>
    <thead><tr><th>Timestamp</th><th>Regime</th><th>Method</th><th>Changes</th></tr></thead>
    <tbody>$cfgRows</tbody>
  </table>
</div>
"@
    } else {
        $rsiPanels += @"
<div class="panel">
  <h2>Config Evolution</h2>
  <div style="color:#94a3b8;font-size:12px;padding:8px 0">No optimization history yet &mdash; run the RSI loop to start.</div>
</div>
"@
    }

    # Panel C: Improvement Report
    if ($rsiReport) {
        $rptGen    = EscHtml ($rsiReport.generated_at.ToString().Substring(0,[Math]::Min(16,$rsiReport.generated_at.ToString().Length)).Replace("T"," "))
        $rptRegime = EscHtml "$($rsiReport.regime)"
        $rptCount  = EscHtml "$($rsiReport.sample_count)"
        $rptSrc    = "$($rsiReport.source)"
        $srcBg2    = if ($rptSrc -eq "claude_api") { "#ede9fe" } else { "#f1f5f9" }
        $srcCol2   = if ($rptSrc -eq "claude_api") { "#7c3aed" } else { "#64748b" }
        $srcLbl2   = EscHtml $rptSrc
        $rptText   = EscHtml "$($rsiReport.report)"
        $rsiPanels += @"
<div class="panel">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
    <h2 style="margin:0">RSI Improvement Report</h2>
    <span style="background:$srcBg2;color:$srcCol2;font-size:9px;font-weight:700;padding:2px 8px;border-radius:5px;text-transform:uppercase">$srcLbl2</span>
  </div>
  <div style="font-size:11px;color:#64748b;margin-bottom:10px">
    Generated: <strong style="color:#1e293b">$rptGen</strong>
    &nbsp;|&nbsp; Regime: <strong style="color:#1e293b">$rptRegime</strong>
    &nbsp;|&nbsp; Samples: <strong style="color:#1e293b">$rptCount</strong>
  </div>
  <div style="max-height:200px;overflow-y:auto;font-size:11px;font-family:Consolas,monospace;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:10px;white-space:pre-wrap;line-height:1.5;color:#374151">$rptText</div>
</div>
"@
    } else {
        $rsiPanels += @"
<div class="panel">
  <h2>RSI Improvement Report</h2>
  <div style="color:#94a3b8;font-size:12px;padding:8px 0">Click <strong>Run RSI Loop</strong> to generate first report</div>
</div>
"@
    }

    # Panel D: Research Layer Picks
    if ($researchPicks) {
        $rpGen      = EscHtml ($researchPicks.generated_at.ToString().Substring(0,[Math]::Min(16,$researchPicks.generated_at.ToString().Length)).Replace("T"," "))
        $rpRegime   = EscHtml "$($researchPicks.regime)"
        $rpScanned  = EscHtml "$($researchPicks.symbols_scanned)"
        $rpFound    = EscHtml "$($researchPicks.candidates_found)"
        $rpSrc      = "$($researchPicks.source)"
        $rpSrcBg    = if ($rpSrc -eq "claude_api") { "#ede9fe" } else { "#f1f5f9" }
        $rpSrcCol   = if ($rpSrc -eq "claude_api") { "#7c3aed" } else { "#64748b" }
        $rpSrcLbl   = EscHtml $rpSrc
        $rpAnalysis = EscHtml "$($researchPicks.analysis)"

        # Candidate mini-table
        $rpCandRows = ""
        if ($researchPicks.top_candidates -and $researchPicks.top_candidates.Count -gt 0) {
            foreach ($rc in ($researchPicks.top_candidates | Select-Object -First 8)) {
                $rcSym  = EscHtml "$($rc.symbol)"
                $rcRsi  = EscHtml "$($rc.rsi)"
                $rcPr   = EscHtml "$($rc.price)"
                $rcMA   = if ($null -ne $rc.pct_vs_200ma -and "$($rc.pct_vs_200ma)" -ne "") {
                    $v = [double]$rc.pct_vs_200ma
                    $c2 = if ($v -ge 0) { "#16a34a" } else { "#dc2626" }
                    $sg = if ($v -ge 0) { "+" } else { "" }
                    "<span style='color:$c2'>$sg$([Math]::Round($v,1))%</span>"
                } else { "<span style='color:#94a3b8'>N/A</span>" }
                $rcBB   = if ($null -ne $rc.pct_from_lower_bb -and "$($rc.pct_from_lower_bb)" -ne "") {
                    $v = [double]$rc.pct_from_lower_bb
                    $c2 = if ($v -le 0) { "#16a34a" } else { "#94a3b8" }
                    $sg = if ($v -ge 0) { "+" } else { "" }
                    "<span style='color:$c2'>$sg$([Math]::Round($v,1))%</span>"
                } else { "<span style='color:#94a3b8'>N/A</span>" }
                $rcVol  = if ($null -ne $rc.vol_ratio -and "$($rc.vol_ratio)" -ne "") { EscHtml "$($rc.vol_ratio)x" } else { "N/A" }
                $rcScore = EscHtml "$($rc.oversold_score)"
                $rpCandRows += "<tr><td><span class='sym2'>$rcSym</span></td><td class='num' style='font-size:11px'>`$$rcPr</td><td class='num' style='color:#7c3aed;font-weight:700'>$rcRsi</td><td class='num'>$rcMA</td><td class='num'>$rcBB</td><td class='num' style='font-size:10px;color:#64748b'>$rcVol</td><td class='num' style='font-size:10px;color:#94a3b8'>$rcScore</td></tr>"
            }
        }
        if (-not $rpCandRows) {
            $rpCandRows = "<tr><td colspan='7' style='text-align:center;color:#94a3b8;padding:12px;font-size:12px'>No oversold candidates found &mdash; market breadth strong</td></tr>"
        }

        $rsiPanels += @"
<div class="panel" style="border-color:#ddd6fe">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
    <h2 style="margin:0;color:#7c3aed">Research Layer &mdash; Oversold Candidate Scan</h2>
    <span style="background:$rpSrcBg;color:$rpSrcCol;font-size:9px;font-weight:700;padding:2px 8px;border-radius:5px;text-transform:uppercase">$rpSrcLbl</span>
  </div>
  <div style="font-size:11px;color:#64748b;margin-bottom:12px">
    Generated: <strong style="color:#1e293b">$rpGen</strong>
    &nbsp;|&nbsp; Regime: <strong style="color:#1e293b">$rpRegime</strong>
    &nbsp;|&nbsp; Symbols scanned: <strong style="color:#1e293b">$rpScanned</strong>
    &nbsp;|&nbsp; Candidates (RSI&lt;40): <strong style="color:#7c3aed">$rpFound</strong>
  </div>
  <table style="margin-bottom:14px">
    <thead><tr>
      <th>Symbol</th><th class="num">Price</th><th class="num">RSI</th>
      <th class="num">vs 200MA</th><th class="num">vs LowerBB</th>
      <th class="num">Vol</th><th class="num">Score</th>
    </tr></thead>
    <tbody>$rpCandRows</tbody>
  </table>
  <div style="font-size:10px;color:#64748b;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Claude Research Analysis</div>
  <div style="max-height:260px;overflow-y:auto;font-size:11px;font-family:Consolas,monospace;background:#faf5ff;border:1px solid #ddd6fe;border-radius:6px;padding:10px;white-space:pre-wrap;line-height:1.6;color:#374151">$rpAnalysis</div>
</div>
"@
    } else {
        $rsiPanels += @"
<div class="panel" style="border-color:#ddd6fe">
  <h2 style="color:#7c3aed">Research Layer &mdash; Oversold Candidate Scan</h2>
  <div style="color:#94a3b8;font-size:12px;padding:8px 0">Click <strong>Run RSI Loop</strong> to run the research scan (Step 5 of 8)</div>
</div>
"@
    }

    # â”€â”€ Screener log â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    $logFile = Get-ChildItem "$LogsDir\screener_*.log" -ErrorAction SilentlyContinue |
               Sort-Object Name -Descending | Select-Object -First 1
    $logName = if ($logFile) { $logFile.Name } else { "" }
    $logRows = ""
    if ($logFile) {
        $lines = Get-Content $logFile.FullName -Tail 40 -ErrorAction SilentlyContinue
        foreach ($line in $lines) {
            if (-not $line.Trim()) { continue }
            $cl = if   ($line -match "ERROR|FAILED")                  { "#ef4444" }
                  elseif($line -match "Passed\s*:\s*[1-9]")           { "#22c55e" }
                  elseif($line -match "done \(exit 0\)|Results saved") { "#2563eb" }
                  elseif($line -match "^===")                          { "#7c3aed" }
                  else                                                  { "#374151" }
            $esc = EscHtml $line
            $logRows += "<tr><td style='color:$cl;font-size:10px;font-family:Consolas,monospace;padding:2px 8px;border-bottom:1px solid #f1f5f9;white-space:pre;overflow:hidden;text-overflow:ellipsis'>$esc</td></tr>"
        }
    }
    if (-not $logRows) {
        $logRows = "<tr><td style='color:#94a3b8;font-size:10px;padding:8px'>No screener log yet -- click Run Screener to generate one.</td></tr>"
    }

    # â”€â”€ RSI loop log â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    $rsiLogFile = Get-ChildItem "$LogsDir\rsi_loop_*.log" -ErrorAction SilentlyContinue |
                  Sort-Object Name -Descending | Select-Object -First 1
    $rsiLogName = if ($rsiLogFile) { $rsiLogFile.Name } else { "" }
    $rsiLogRows = ""
    if ($rsiLogFile) {
        $rsiLines = Get-Content $rsiLogFile.FullName -Tail 50 -ErrorAction SilentlyContinue
        foreach ($line in $rsiLines) {
            if (-not $line.Trim()) { continue }
            $cl = if   ($line -match "ERROR|FAILED")                      { "#ef4444" }
                  elseif($line -match "data_driven|regime_defaults")       { "#2563eb" }
                  elseif($line -match "Regime:|regime:")                   { "#7c3aed" }
                  elseif($line -match "new picks|updated|Changes applied") { "#16a34a" }
                  elseif($line -match "^={2,}|STEP [0-9]|SUMMARY")        { "#7c3aed" }
                  else                                                      { "#374151" }
            $esc = EscHtml $line
            $rsiLogRows += "<tr><td style='color:$cl;font-size:10px;font-family:Consolas,monospace;padding:2px 8px;border-bottom:1px solid #f1f5f9;white-space:pre;overflow:hidden;text-overflow:ellipsis'>$esc</td></tr>"
        }
    }
    if (-not $rsiLogRows) {
        $rsiLogRows = "<tr><td style='color:#94a3b8;font-size:10px;padding:8px'>No RSI loop log yet -- click Run RSI Loop to generate one.</td></tr>"
    }

    # â”€â”€ Assemble full HTML â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    return @"
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="$RefreshSec">
<title>Screener Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#f1f5f9;color:#1e293b;font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;padding:22px 26px;min-height:100vh}
a{color:inherit;text-decoration:none}
.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:10px}
.logo{font-size:20px;font-weight:800;color:#0f172a;letter-spacing:-0.5px}
.logo span{color:#7c3aed}
.btn{padding:6px 14px;border-radius:8px;font-size:12px;cursor:pointer;font-weight:600;display:inline-block}
.btn-refresh{background:#fff;border:1px solid #cbd5e1;color:#374151}
.btn-refresh:hover{background:#f8fafc}
.btn-stop{background:#fff;border:1px solid #e2e8f0;color:#94a3b8;font-size:11px}
.btn-stop:hover{color:#dc2626;border-color:#dc2626}
.countdown{font-size:11px;color:#94a3b8;font-variant-numeric:tabular-nums}
.pbar{height:3px;background:#e2e8f0;position:fixed;top:0;left:0;right:0;z-index:999}
.pfill{height:100%;background:linear-gradient(90deg,#7c3aed,#2563eb);transition:width 1s linear}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}
.chip{background:#fff;border:1px solid #e2e8f0;border-radius:20px;padding:4px 12px;font-size:11px;font-weight:600;color:#374151;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.chip span{color:#7c3aed;font-weight:800}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px;margin-bottom:18px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:14px 16px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.card .lbl{font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.8px;font-weight:600;margin-bottom:6px}
.card .val{font-size:22px;font-weight:800;color:#0f172a;letter-spacing:-0.5px}
.card .sub{font-size:11px;margin-top:3px;color:#64748b}
.panel{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:18px 20px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.panel h2{font-size:12px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:1px;margin-bottom:14px}
table{width:100%;border-collapse:collapse}
th{font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.8px;font-weight:600;padding:0 10px 10px;text-align:left;border-bottom:1px solid #e2e8f0}
td{padding:8px 10px;border-bottom:1px solid #f1f5f9;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#fafbff}
.sym{font-weight:800;color:#2563eb;font-size:14px}
.sym2{font-weight:700;color:#2563eb;font-size:12px}
.num{text-align:right;font-variant-numeric:tabular-nums}
.by{background:#dcfce7;color:#16a34a;font-size:9px;font-weight:700;padding:2px 5px;border-radius:5px;margin-right:2px;display:inline-block}
.bn{background:#f1f5f9;color:#94a3b8;font-size:9px;font-weight:700;padding:2px 5px;border-radius:5px;margin-right:2px;display:inline-block;text-decoration:line-through}
.sbar{display:inline-block;width:60px;height:6px;background:#e2e8f0;border-radius:3px;overflow:hidden;vertical-align:middle;margin-right:5px}
.sbfill{height:100%}
.snum{font-size:10px;color:#64748b;font-variant-numeric:tabular-nums;vertical-align:middle}
.nopicks{text-align:center;padding:28px;background:#f8fafc;border-radius:10px;border:1px dashed #c7d2fe}
.divider{border:none;border-top:2px dashed #e2e8f0;margin:22px 0 16px}
.section-hdr{font-size:11px;font-weight:700;color:#7c3aed;text-transform:uppercase;letter-spacing:1.2px;margin-bottom:14px;display:flex;align-items:center;gap:8px}
.section-hdr::after{content:'';flex:1;height:1px;background:#ede9fe}
.footer{margin-top:18px;display:flex;gap:16px;flex-wrap:wrap;align-items:center;border-top:1px solid #e2e8f0;padding-top:14px;font-size:11px;color:#94a3b8}
.footer b{color:#64748b;font-weight:600}
</style>
</head>
<body>

<div class="pbar"><div class="pfill" id="prog"></div></div>

<div class="topbar">
  <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
    <div class="logo">MeanReversion <span>Screener</span></div>
    <span style="background:$statusBg;color:$statusCol;border:1px solid $statusBord;border-radius:12px;font-size:10px;font-weight:700;padding:2px 10px">$statusText</span>
    <span style="background:$rsiStatBg;color:$rsiStatCol;border:1px solid $rsiStatBord;border-radius:12px;font-size:10px;font-weight:700;padding:2px 10px">RSI $rsiStatText</span>
  </div>
  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
    <span class="countdown">Refresh in <span id="cd">$RefreshSec</span>s</span>
    <a href="$runBtnHref" class="btn" style="background:$runBtnCol;color:#fff;border:none">$runBtnText</a>
    <a href="$rsiBtnHref" class="btn" style="background:$rsiBtnCol;color:#fff;border:none">$rsiBtnText</a>
    <a href="/" class="btn btn-refresh">&#8635; Refresh</a>
    <a href="/stop" class="btn btn-stop" onclick="return confirm('Stop dashboard server?')">&#9632; Stop</a>
    <span style="font-size:12px;color:#94a3b8">$now</span>
  </div>
</div>

<div class="chips">
  <div class="chip">RSI(<span>$rsiPer</span>) &lt; <span>$rsiThr</span></div>
  <div class="chip">Below Lower BB (<span>$bbPer</span>, <span>${bbStd}</span>sd)</div>
  <div class="chip">Price &gt; <span>$maPer</span>-day MA</div>
  <div class="chip">Volume &gt; <span>$volMin</span>x avg</div>
  <div class="chip">Universe: <span>$universe</span></div>
  <div class="chip">Pos size: <span>`$$posSz</span> | Max: <span>$maxPos</span></div>
</div>

<div class="cards">
  <div class="card">
    <div class="lbl">Last Run</div>
    <div class="val" style="font-size:13px;padding-top:5px">$runDate</div>
    <div class="sub">UTC</div>
  </div>
  <div class="card">
    <div class="lbl">Screened</div>
    <div class="val">$screened</div>
    <div class="sub">$universe stocks</div>
  </div>
  <div class="card" style="border-color:$passedBord;background:$passedBg">
    <div class="lbl">Actionable</div>
    <div class="val" style="color:$passedColor">$passed</div>
    <div class="sub">passed all 4 filters</div>
  </div>
  <div class="card">
    <div class="lbl">Radar</div>
    <div class="val" style="color:#2563eb">$radarCnt</div>
    <div class="sub">2-3 filters passed</div>
  </div>
  <div class="card">
    <div class="lbl">Picks Tracked</div>
    <div class="val" style="color:#7c3aed">$totalPicks</div>
    <div class="sub">$filledPicks with 5d return</div>
  </div>
  <div class="card">
    <div class="lbl">Screener Status</div>
    <div class="val" style="font-size:16px;padding-top:6px;color:$statusCol">$statusText</div>
    <div class="sub">$(if ($isRunning) {'screener running...'} else {'click Run Screener'})</div>
  </div>
</div>

<div class="panel">
  <h2>Top Actionable Picks &mdash; All 4 Filters Passed</h2>
  $pickSection
</div>

<div class="panel">
  <h2>Radar &mdash; 2 or 3 Filters Passed (Watching)</h2>
  <table>
    <thead><tr>
      <th>Symbol</th><th class="num">Price</th><th class="num">RSI</th>
      <th class="num">% vs BB</th><th class="num">% vs 200MA</th>
      <th class="num">Vol Ratio</th><th style="text-align:center">Filters</th><th>Passed</th>
    </tr></thead>
    <tbody>$radarRows</tbody>
  </table>
</div>

$pendingSection

<hr class="divider">
<div class="section-hdr">RSI Self-Improvement Loop &nbsp;&mdash;&nbsp; auto-runs every Monday 07:00</div>

$rsiPanels

<div class="panel">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
    <h2 style="margin:0">Performance Tracker &mdash; Forward Returns</h2>
    <span style="font-size:11px;color:#64748b">$perfSubtitle</span>
  </div>
  <table>
    <thead><tr>
      <th>Symbol</th><th>Date</th><th>Source</th><th>Regime</th>
      <th class="num">Entry</th><th style="text-align:center">Filters</th>
      <th class="num">1d</th><th class="num">5d</th><th class="num">10d</th><th class="num">20d</th>
    </tr></thead>
    <tbody>$perfRows</tbody>
  </table>
</div>

<div class="panel">
  <h2>Latest RSI Loop Log &nbsp;<span style="font-weight:400;font-size:9px;color:#94a3b8;text-transform:none">$rsiLogName</span></h2>
  <div style="max-height:220px;overflow-y:auto;border:1px solid #e2e8f0;border-radius:6px;background:#f8fafc">
    <table style="width:100%"><tbody>$rsiLogRows</tbody></table>
  </div>
</div>

<div class="panel">
  <h2>Latest Screener Log &nbsp;<span style="font-weight:400;font-size:9px;color:#94a3b8;text-transform:none">$logName</span></h2>
  <div style="max-height:220px;overflow-y:auto;border:1px solid #e2e8f0;border-radius:6px;background:#f8fafc">
    <table style="width:100%"><tbody>$logRows</tbody></table>
  </div>
</div>

<div class="footer">
  <b>$universe</b> &nbsp;|&nbsp; RSI&lt;$rsiThr | BelowBB(${bbPer},${bbStd}sd) | Above ${maPer}MA | Vol&gt;${volMin}x
  &nbsp;|&nbsp; Screener: Mon 06:00 &nbsp;|&nbsp; RSI Loop: Mon 07:00
  <span style="margin-left:auto">http://localhost:$Port/</span>
</div>

<script>
let s = $RefreshSec;
const cd   = document.getElementById('cd');
const prog = document.getElementById('prog');
if (prog) prog.style.width = '100%';
const iv = setInterval(function() {
  s--;
  if (s <= 0) { clearInterval(iv); return; }
  if (cd)   cd.textContent = s;
  if (prog) prog.style.width = (s / $RefreshSec * 100) + '%';
}, 1000);
</script>
</body>
</html>
"@
}

# â”€â”€ HTTP server â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://localhost:$Port/")
$listener.Start()

Write-Host ""
Write-Host "  Screener Dashboard  ->  http://localhost:$Port/"
Write-Host "  Run screener        ->  http://localhost:$Port/run"
Write-Host "  Run RSI loop        ->  http://localhost:$Port/run-rsi"
Write-Host "  Stop server         ->  http://localhost:$Port/stop  (or Ctrl+C)"
Write-Host ""

Start-Process "http://localhost:$Port/"

try {
    while ($listener.IsListening) {
        $ctx  = $listener.GetContext()
        $req  = $ctx.Request
        $resp = $ctx.Response
        $path = $req.Url.LocalPath

        if ($path -eq "/stop") {
            $msg   = "<html><body style='background:#f1f5f9;font-family:sans-serif;padding:40px;text-align:center'><h2>Dashboard stopped.</h2><p style='color:#64748b;margin-top:10px'>You can close this tab.</p></body></html>"
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($msg)
            $resp.ContentType = "text/html; charset=utf-8"
            $resp.ContentLength64 = $bytes.Length
            $resp.OutputStream.Write($bytes, 0, $bytes.Length)
            $resp.Close()
            $listener.Stop(); break
        }

        if ($path -eq "/run") {
            $r = Start-Screener
            Write-Host "  Screener trigger: $r"
            $resp.StatusCode = 302
            $resp.RedirectLocation = "/"
            $resp.Close(); continue
        }

        if ($path -eq "/run-rsi") {
            $r = Start-RsiLoop
            Write-Host "  RSI loop trigger: $r"
            $resp.StatusCode = 302
            $resp.RedirectLocation = "/"
            $resp.Close(); continue
        }

        if ($path -eq "/favicon.ico") { $resp.StatusCode = 204; $resp.Close(); continue }

        Write-Host "  $($req.HttpMethod) $path"
        try {
            $html  = Build-HTML
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($html)
            $resp.ContentType = "text/html; charset=utf-8"
            $resp.ContentLength64 = $bytes.Length
            $resp.OutputStream.Write($bytes, 0, $bytes.Length)
        } catch {
            $err   = "<html><body style='font-family:monospace;padding:40px;color:#dc2626'><pre>Error: $_</pre></body></html>"
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($err)
            $resp.StatusCode = 500
            $resp.ContentType = "text/html; charset=utf-8"
            $resp.ContentLength64 = $bytes.Length
            $resp.OutputStream.Write($bytes, 0, $bytes.Length)
        }
        $resp.Close()
    }
} finally {
    if ($listener.IsListening) { $listener.Stop() }
    Write-Host "  Server stopped."
}

