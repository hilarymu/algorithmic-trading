# options_dashboard_server.ps1
# Options Screener Trader -- Live Dashboard
# http://localhost:8767/  |  /stop

param([int]$Port = 8767, [int]$RefreshSec = 30)

$ProjDir = $PSScriptRoot
$DataDir = "$ProjDir\data"
$LogsDir = "$ProjDir\logs"

Add-Type -AssemblyName System.Web
function Esc($s) { [System.Web.HttpUtility]::HtmlEncode("$s") }
function N1($v)  { try { [Math]::Round([double]$v,1) } catch { "N/A" } }
function N2($v)  { try { [Math]::Round([double]$v,2) } catch { "N/A" } }

# -- Data loaders --------------------------------------------------------------
function Load-Json($file) {
    $p = "$DataDir\$file"
    if (-not (Test-Path $p)) { return $null }
    try { Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $null }
}

function Load-IvTop15 {
    $p = "$DataDir\iv_rank_cache.json"
    if (-not (Test-Path $p)) { return @() }
    try {
        $cache = Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json
        $list  = @()
        foreach ($prop in $cache.PSObject.Properties) {
            $ivr = 0
            $ivc = 0
            try { $ivr = [double]$prop.Value.iv_rank    } catch {}
            try { $ivc = [double]$prop.Value.iv_current } catch {}
            $list += [PSCustomObject]@{ Symbol=$prop.Name; IvRank=$ivr; IvCurrent=$ivc }
        }
        $list | Sort-Object IvRank -Descending | Select-Object -First 15
    } catch { @() }
}

# -- Section builders ----------------------------------------------------------
function Build-CandRows($sq) {
    if (-not $sq -or -not $sq.candidates -or $sq.candidates.Count -eq 0) {
        return "<tr><td colspan='9' class='empty'>No candidates today</td></tr>"
    }
    $out = ""
    foreach ($c in $sq.candidates) {
        $sc   = 0
        $scOk = $false
        try { $sc = [double]$c.signal_strength; $scOk = $true } catch {}
        $scTx = if ($scOk) { N1 $sc } else { "N/A" }
        $scCl = if (-not $scOk)   { "neutral" }
                elseif ($sc -ge 50) { "high" }
                elseif ($sc -ge 20) { "med" }
                else                { "low" }
        $ne  = if ($c.near_earnings) { " (!)" } else { "" }
        $ivp = try { N1([double]$c.iv_current * 100) } catch { "N/A" }
        $ep  = try { N1 $c.est_premium_pct } catch { "N/A" }
        $ea  = try { N1 $c.est_annual_pct  } catch { "N/A" }
        $ek  = try { N2 $c.est_strike      } catch { "N/A" }
        $sym = Esc "$($c.symbol)$ne"
        $str = Esc "$($c.strategy)"
        $out += "<tr>"
        $out += "<td class='sym'>$sym</td>"
        $out += "<td>$str</td>"
        $out += "<td class='num'>$(N1 $c.rsi)</td>"
        $out += "<td class='num'>$(N1 $c.iv_rank)</td>"
        $out += "<td class='num'>${ivp}%</td>"
        $out += "<td class='num'>$(N2 $c.vol_ratio)x</td>"
        $out += "<td class='num'>`$$ek</td>"
        $out += "<td class='num'>${ep}% / ${ea}% ann.</td>"
        $out += "<td class='num'><span class='score $scCl'>$scTx</span></td>"
        $out += "</tr>"
    }
    return $out
}

function Build-PosRows($state) {
    $open = @(); $closed = @()
    if ($state) {
        if ($state -is [System.Array]) {
            foreach ($p in $state) {
                if ($p.status -eq "open")   { $open   += $p }
                if ($p.status -eq "closed") { $closed += $p }
            }
        } elseif ($state.positions) {
            foreach ($p in $state.positions) {
                if ($p.status -eq "open")   { $open   += $p }
                if ($p.status -eq "closed") { $closed += $p }
            }
        }
    }

    $oRows = ""
    if ($open.Count -gt 0) {
        foreach ($p in $open) {
            $ed = try { $p.entry_date.ToString().Substring(0,10) } catch { "N/A" }
            $oRows += "<tr>"
            $oRows += "<td class='sym'>$(Esc $p.symbol)</td>"
            $oRows += "<td>$(Esc $p.strategy)</td>"
            $oRows += "<td class='num'>`$$($p.strike)</td>"
            $oRows += "<td class='num'>$($p.expiry)</td>"
            $oRows += "<td class='num'>$($p.dte_at_entry)d</td>"
            $oRows += "<td class='num'>`$$($p.entry_premium)</td>"
            $oRows += "<td class='num'>$(N1 $p.iv_rank_at_entry)</td>"
            $oRows += "<td class='muted'>$ed</td>"
            $oRows += "</tr>"
        }
    } else {
        $oRows = "<tr><td colspan='8' class='empty'>No open positions</td></tr>"
    }

    $cRows = ""
    if ($closed.Count -gt 0) {
        $sorted = $closed | Sort-Object { $_.exit_date } -Descending
        foreach ($p in $sorted) {
            $pnl   = $null
            try { $pnl = [double]$p.pnl_pct } catch {}
            $pnlTx = if ($null -ne $pnl) { if ($pnl -ge 0) { "+$(N2 $pnl)%" } else { "$(N2 $pnl)%" } } else { "N/A" }
            $pnlCl = if ($null -ne $pnl) { if ($pnl -gt 0) { "profit" } elseif ($pnl -lt 0) { "loss" } else { "" } } else { "" }
            $xd    = try { $p.exit_date.ToString().Substring(0,10) } catch { "N/A" }
            $cRows += "<tr>"
            $cRows += "<td class='sym'>$(Esc $p.symbol)</td>"
            $cRows += "<td>$(Esc $p.strategy)</td>"
            $cRows += "<td class='num'>`$$($p.strike)</td>"
            $cRows += "<td class='muted'>$xd</td>"
            $cRows += "<td class='num'>$($p.hold_days)d</td>"
            $cRows += "<td class='num $pnlCl'>$pnlTx</td>"
            $cRows += "<td class='muted'>$(Esc $p.exit_reason)</td>"
            $cRows += "</tr>"
        }
    } else {
        $cRows = "<tr><td colspan='7' class='empty'>No closed positions yet</td></tr>"
    }

    return @{ Open=$oRows; Closed=$cRows; OpenCount=$open.Count; ClosedCount=$closed.Count }
}

function Build-IvBars($sq) {
    if (-not $sq -or -not $sq.iv_rank_distribution) { return "" }
    $dist  = $sq.iv_rank_distribution
    $order = @("<40","40-55","55-70","70-85","85-100")
    $cols  = @{ "<40"="#6e7781"; "40-55"="#0969da"; "55-70"="#1a7f37"; "70-85"="#9a6700"; "85-100"="#cf222e" }
    $out   = ""
    foreach ($b in $order) {
        $info = $dist.$b
        $pct  = 0
        $cnt  = 0
        if ($info) { try { $pct = [double]$info.pct } catch {}; try { $cnt = [int]$info.count } catch {} }
        $col  = $cols[$b]
        $w    = [Math]::Min([int]($pct * 2), 100)
        $pTx  = [Math]::Round($pct,1)
        $out += "<div class='iv-row'>"
        $out += "<span class='iv-lbl'>$b</span>"
        $out += "<div class='bar-wrap'><div class='bar' style='width:${w}%;background:$col'></div>"
        $out += "<span class='bar-txt'>${pTx}%</span></div>"
        $out += "<span class='iv-cnt'>$cnt</span>"
        $out += "</div>"
    }
    return $out
}

function Build-Top15Rows {
    $tops = Load-IvTop15
    if (-not $tops -or $tops.Count -eq 0) {
        return "<tr><td colspan='5' class='empty'>IV cache not loaded</td></tr>"
    }
    $i   = 1
    $out = ""
    foreach ($t in $tops) {
        $bw  = [int]$t.IvRank
        $ivr = [Math]::Round($t.IvRank,1)
        $ivc = [Math]::Round($t.IvCurrent * 100, 1)
        $out += "<tr>"
        $out += "<td class='muted' style='width:24px'>$i</td>"
        $out += "<td class='sym'>$(Esc $t.Symbol)</td>"
        $out += "<td class='num'>$ivr</td>"
        $out += "<td><div style='height:6px;background:#0969da;border-radius:3px;width:${bw}%;max-width:100%'></div></td>"
        $out += "<td class='num muted'>${ivc}%</td>"
        $out += "</tr>"
        $i++
    }
    return $out
}

function Build-LogRows($pattern, [int]$tail = 50) {
    $f = Get-ChildItem "$LogsDir\$pattern" -ErrorAction SilentlyContinue |
         Sort-Object Name -Descending | Select-Object -First 1
    if (-not $f) {
        return @{ Name="(none)"; Rows="<tr><td style='color:#57606a;font-size:10px;padding:8px'>No log file yet</td></tr>" }
    }
    $lines = Get-Content $f.FullName -Tail $tail -ErrorAction SilentlyContinue
    $rows  = ""
    foreach ($line in ($lines | Where-Object { $_.Trim() })) {
        $cl = if   ($line -match "ERROR|FAILED|WARNING") { "#cf222e" }
              elseif ($line -match "closed|profit|applied|done \(exit 0\)") { "#1a7f37" }
              elseif ($line -match "starting|Running|Phase|checking") { "#0969da" }
              elseif ($line -match "candidates|screener|checked") { "#9a6700" }
              else { "#57606a" }
        $esc = Esc $line
        $rows += "<tr><td style='color:$cl;font-size:10px;font-family:Consolas,monospace;padding:2px 8px;border-bottom:1px solid #eaeef2;white-space:pre;overflow:hidden;text-overflow:ellipsis'>$esc</td></tr>"
    }
    if (-not $rows) { $rows = "<tr><td style='color:#57606a;font-size:10px;padding:8px'>Log is empty</td></tr>" }
    return @{ Name=$f.Name; Rows=$rows }
}

# -- Main HTML builder ---------------------------------------------------------
function Build-HTML {
    $sq     = Load-Json "options_signal_quality.json"
    $report = Load-Json "options_improvement_report.json"
    $state  = Load-Json "positions_state.json"

    $now    = Get-Date -Format "ddd MMM d, yyyy  h:mm:ss tt"
    $regime = if ($sq)     { "$($sq.regime)".ToUpper() } else { "UNKNOWN" }
    $quality= if ($sq)     { "$($sq.data_quality)" }     else { "N/A" }
    $sellZn = try { [Math]::Round([double]$sq.sell_zone_pct,0) } catch { "N/A" }
    $nSyms  = try { [int]$sq.n_symbols_with_rank } catch { "N/A" }
    $updAt  = if ($sq -and $sq.generated_at) {
        $s = $sq.generated_at.ToString()
        $s.Substring(0,[Math]::Min(16,$s.Length)).Replace("T"," ") + " UTC"
    } else { "N/A" }

    $regimeCl = switch ($regime) { "BULL" { "#1a7f37" } "BEAR" { "#cf222e" } default { "#9a6700" } }
    $qualLbl  = switch ($quality) { "real_iv" { "Live IV" } "proxy" { "HV30 Proxy" } default { $quality } }

    $nClosed = 0; try { $nClosed = [int]$report.n_closed_positions } catch {}
    $minIns  = 10; try { $minIns  = [int]$report.min_for_insights   } catch {}
    $minChg  = 50; try { $minChg  = [int]$report.min_for_changes    } catch {}
    $optStat = if ($report) { Esc "$($report.status)" } else { "No report available" }
    $autoOpt = ($report -and "$($report.auto_optimize)" -eq "True")
    $pIns    = if ($minIns -gt 0) { [Math]::Min([int]($nClosed/$minIns*100),100) } else { 100 }
    $pChg    = if ($minChg -gt 0) { [Math]::Min([int]($nClosed/$minChg*100),100) } else { 100 }
    $autoCl  = if ($autoOpt) { "color:#1a7f37" } else { "color:#cf222e" }
    $autoTx  = if ($autoOpt) { "AUTO-APPLY ON" } else { "AUTO-APPLY OFF" }

    $cfgRows = ""
    if ($report -and $report.config_snapshot) {
        foreach ($prop in $report.config_snapshot.PSObject.Properties) {
            $cfgRows += "<tr><td class='muted'>$(Esc $prop.Name)</td><td class='num'>$(Esc "$($prop.Value)")</td></tr>"
        }
    }

    $nCands   = if ($sq -and $sq.candidates) { $sq.candidates.Count } else { 0 }
    $outN     = try { [int]$sq.outcome_stats.n }            catch { 0 }
    $outWR    = try { N1 $sq.outcome_stats.win_rate_pct }   catch { "N/A" }
    $outPnl   = $null; try { $outPnl = [double]$sq.outcome_stats.avg_pnl_pct } catch {}
    $pnlTx    = if ($null -ne $outPnl) { if ($outPnl -ge 0) { "+$(N2 $outPnl)%" } else { "$(N2 $outPnl)%" } } else { "N/A" }
    $pnlCl    = if ($null -ne $outPnl) { if ($outPnl -gt 0) { "profit" } elseif ($outPnl -lt 0) { "loss" } else { "" } } else { "" }

    $candRows = Build-CandRows $sq
    $pos      = Build-PosRows  $state
    $ivBars   = Build-IvBars   $sq
    $top15    = Build-Top15Rows
    $iLog     = Build-LogRows  "options_intraday_*.log" 60
    $dLog     = Build-LogRows  "options_loop_*.log"     50

    return @"
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="$RefreshSec">
<title>Options Screener Trader - Live Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;background:#f6f8fa;color:#24292f;min-height:100vh}
.pbar{height:3px;background:#d0d7de;position:fixed;top:0;left:0;right:0;z-index:999}
.pfill{height:100%;background:linear-gradient(90deg,#0969da,#1a7f37);transition:width 1s linear}
header{display:flex;justify-content:space-between;align-items:flex-start;padding:1.2rem 2rem;background:#ffffff;border-bottom:1px solid #d0d7de;flex-wrap:wrap;gap:.75rem}
header h1{font-size:1.2rem;font-weight:700}
.sub{font-size:.75rem;color:#57606a;display:block;margin-top:2px}
.hr{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}
.pill{font-size:.75rem;padding:.25rem .65rem;border-radius:20px;background:#f6f8fa;border:1px solid #d0d7de;color:#57606a}
.upd{font-size:.75rem;color:#57606a}
.cd{font-size:.75rem;color:#57606a;font-variant-numeric:tabular-nums}
.btn{font-size:.75rem;padding:.25rem .75rem;border-radius:6px;border:1px solid #d0d7de;background:#f6f8fa;color:#57606a;text-decoration:none;display:inline-block}
.btn-stop{border-color:#cf222e;color:#cf222e;background:#ffebe9}
main{padding:1.5rem 2rem;max-width:1400px;margin:0 auto}
.card{background:#ffffff;border:1px solid #d0d7de;border-radius:8px;padding:1.25rem 1.5rem;margin-bottom:1.25rem}
.card h2{font-size:1rem;font-weight:600;color:#24292f;margin-bottom:1rem;display:flex;align-items:center;gap:.5rem}
.card h3{font-size:.8rem;font-weight:600;color:#57606a;margin-bottom:.5rem;text-transform:uppercase;letter-spacing:.05em;margin-top:1rem}
.badge{font-size:.7rem;padding:.1rem .5rem;background:#f6f8fa;border:1px solid #d0d7de;border-radius:20px;color:#57606a;font-weight:400}
.obar{display:flex;gap:2rem;margin-bottom:.75rem;font-size:.82rem;color:#57606a}
.obar strong{color:#24292f}
table{width:100%;border-collapse:collapse;font-size:.82rem}
thead th{text-align:left;padding:.35rem .6rem;color:#57606a;font-weight:500;font-size:.75rem;text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid #d0d7de}
tbody tr:hover{background:#f6f8fa}
tbody td{padding:.4rem .6rem;border-bottom:1px solid #eaeef2;color:#24292f}
.sym{font-weight:600;color:#0969da}
.num{text-align:right;font-variant-numeric:tabular-nums}
.muted{color:#57606a}
.empty{text-align:center;color:#57606a;padding:1.5rem!important;font-style:italic}
.score{font-size:.72rem;font-weight:700;padding:.15rem .5rem;border-radius:4px;white-space:nowrap}
.high{background:#dafbe1;color:#1a7f37;border:1px solid #1a7f37}
.med{background:#fff8c5;color:#9a6700;border:1px solid #d4a72c}
.low{background:#ffebe9;color:#cf222e;border:1px solid #cf222e}
.neutral{background:#f6f8fa;color:#57606a;border:1px solid #d0d7de}
.profit{color:#1a7f37;font-weight:600}
.loss{color:#cf222e;font-weight:600}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:2rem}
.iv-row{display:flex;align-items:center;gap:.5rem;margin-bottom:.4rem}
.iv-lbl{font-size:.78rem;color:#57606a;width:85px;flex-shrink:0}
.iv-cnt{font-size:.78rem;color:#57606a;width:36px;text-align:right;flex-shrink:0}
.bar-wrap{flex:1;background:#eaeef2;border-radius:3px;height:14px;position:relative;display:flex;align-items:center;overflow:hidden}
.bar{height:100%;border-radius:3px}
.bar-txt{position:absolute;right:6px;font-size:.7rem;color:#24292f;font-weight:600}
.pi{display:flex;align-items:center;gap:.75rem;font-size:.8rem;color:#57606a;margin-bottom:.5rem}
.pi span{width:200px;flex-shrink:0}
.pb{flex:1;background:#eaeef2;border-radius:4px;height:8px;overflow:hidden}
.pf{height:100%;border-radius:4px}
.log-box{max-height:220px;overflow-y:auto;border:1px solid #d0d7de;border-radius:6px;background:#f6f8fa}
.log-nm{font-size:.7rem;color:#57606a;font-weight:400;margin-left:.5rem}
footer{text-align:center;padding:1.5rem;font-size:.75rem;color:#6e7781;border-top:1px solid #d0d7de;margin-top:1rem}
@media(max-width:900px){.two-col{grid-template-columns:1fr};main{padding:1rem};header{padding:1rem}}
</style>
</head>
<body>
<div class="pbar"><div class="pfill" id="prog" style="width:100%"></div></div>
<header>
  <div>
    <h1>Options Screener Trader</h1>
    <span class="sub">Live Dashboard - Refreshes every ${RefreshSec}s - http://localhost:$Port/</span>
  </div>
  <div class="hr">
    <div class="pill" style="color:$regimeCl;border-color:$regimeCl">Regime: $regime</div>
    <div class="pill">IV: $qualLbl</div>
    <div class="pill">Sell Zone: ${sellZn}% of $nSyms</div>
    <span class="cd">Refresh in <span id="cd">$RefreshSec</span>s</span>
    <a href="/" class="btn">Refresh</a>
    <a href="/stop" class="btn btn-stop" onclick="return confirm('Stop dashboard server?')">Stop</a>
    <span class="upd">$now</span>
  </div>
</header>
<main>

<div class="card">
  <h2>Today's Signals <span class="badge">$nCands</span></h2>
  <div class="obar">
    <span>Closed: <strong>$outN</strong></span>
    <span>Win Rate: <strong>${outWR}%</strong></span>
    <span>Avg P&amp;L: <strong class="$pnlCl">$pnlTx</strong></span>
    <span class="muted" style="margin-left:auto">Data: $updAt</span>
  </div>
  <table>
    <thead><tr>
      <th>Symbol</th><th>Strategy</th><th>RSI</th><th>IV Rank</th>
      <th>IV%</th><th>Vol</th><th>Est Strike</th><th>Est Premium</th><th>Score</th>
    </tr></thead>
    <tbody>$candRows</tbody>
  </table>
  <p style="font-size:.75rem;color:#57606a;margin-top:.6rem">
    Score: <span class="score high">50+ High</span>
    <span class="score med">20-49 Med</span>
    <span class="score low">under 20 Low</span>
    - (!) Near earnings
  </p>
</div>

<div class="card">
  <h2>Positions</h2>
  <h3>Open <span class="badge">$($pos.OpenCount)</span></h3>
  <table>
    <thead><tr>
      <th>Symbol</th><th>Strategy</th><th>Strike</th><th>Expiry</th>
      <th>DTE</th><th>Premium</th><th>IV Rank</th><th>Entry Date</th>
    </tr></thead>
    <tbody>$($pos.Open)</tbody>
  </table>
  <h3>Closed <span class="badge">$($pos.ClosedCount)</span></h3>
  <table>
    <thead><tr>
      <th>Symbol</th><th>Strategy</th><th>Strike</th><th>Exit Date</th>
      <th>Hold</th><th>P&amp;L</th><th>Reason</th>
    </tr></thead>
    <tbody>$($pos.Closed)</tbody>
  </table>
</div>

<div class="card two-col">
  <div>
    <h2>IV Rank Universe</h2>
    <div style="margin-top:.5rem">$ivBars</div>
  </div>
  <div>
    <h2>Top 15 by IV Rank</h2>
    <table>
      <thead><tr><th>#</th><th>Symbol</th><th>IV Rank</th><th></th><th>IV%</th></tr></thead>
      <tbody>$top15</tbody>
    </table>
  </div>
</div>

<div class="card">
  <h2>Self-Optimizing Loop <span style="font-size:.75rem;$autoCl">$autoTx</span></h2>
  <p style="font-size:.82rem;color:#57606a;margin-bottom:.75rem">$optStat</p>
  <div class="pi">
    <span>Insights ($nClosed / $minIns closed)</span>
    <div class="pb"><div class="pf" style="background:#0969da;width:${pIns}%"></div></div>
  </div>
  <div class="pi">
    <span>Auto-apply ($nClosed / $minChg closed)</span>
    <div class="pb"><div class="pf" style="background:#1a7f37;width:${pChg}%"></div></div>
  </div>
  <div style="margin-top:1.5rem">
    <h3>Config Snapshot</h3>
    <table style="max-width:400px"><tbody>$cfgRows</tbody></table>
  </div>
</div>

<div class="card">
  <h2>Intraday Monitor Log <span class="log-nm">$($iLog.Name)</span></h2>
  <div class="log-box"><table style="width:100%"><tbody>$($iLog.Rows)</tbody></table></div>
</div>

<div class="card">
  <h2>Daily Pipeline Log <span class="log-nm">$($dLog.Name)</span></h2>
  <div class="log-box"><table style="width:100%"><tbody>$($dLog.Rows)</tbody></table></div>
</div>

</main>
<footer>Options Screener Trader - Paper Trading Only - Not Financial Advice - http://localhost:$Port/</footer>
<script>
let s = $RefreshSec;
const cd = document.getElementById('cd');
const pg = document.getElementById('prog');
setInterval(function() {
  s--; if (s < 0) s = 0;
  if (cd) cd.textContent = s;
  if (pg) pg.style.width = (s / $RefreshSec * 100) + '%';
}, 1000);
</script>
</body>
</html>
"@
}

# -- HTTP server ---------------------------------------------------------------
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://localhost:$Port/")
$listener.Start()

Write-Host ""
Write-Host "  Options Dashboard  ->  http://localhost:$Port/"
Write-Host "  Stop server        ->  http://localhost:$Port/stop  (or Ctrl+C)"
Write-Host "  Auto-refresh       ->  every $RefreshSec seconds"
Write-Host ""

Start-Process "http://localhost:$Port/"

try {
    while ($listener.IsListening) {
        $ctx  = $listener.GetContext()
        $req  = $ctx.Request
        $resp = $ctx.Response
        $path = $req.Url.LocalPath

        if ($path -eq "/stop") {
            $msg   = "<!DOCTYPE html><html><body style='background:#ffffff;font-family:sans-serif;padding:40px;text-align:center;color:#24292f'><h2>Dashboard stopped.</h2><p style='color:#57606a;margin-top:10px'>Close this tab.</p></body></html>"
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($msg)
            $resp.ContentType     = "text/html; charset=utf-8"
            $resp.ContentLength64 = $bytes.Length
            $resp.OutputStream.Write($bytes, 0, $bytes.Length)
            $resp.Close()
            $listener.Stop()
            break
        }

        if ($path -eq "/favicon.ico") { $resp.StatusCode = 204; $resp.Close(); continue }

        Write-Host "  $(Get-Date -Format 'HH:mm:ss')  $($req.HttpMethod) $path"
        try {
            $html  = Build-HTML
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($html)
            $resp.ContentType     = "text/html; charset=utf-8"
            $resp.ContentLength64 = $bytes.Length
            $resp.OutputStream.Write($bytes, 0, $bytes.Length)
        } catch {
            $err   = "<!DOCTYPE html><html><body style='background:#ffffff;font-family:monospace;padding:40px;color:#cf222e'><pre>Dashboard Error: $_</pre></body></html>"
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($err)
            $resp.StatusCode      = 500
            $resp.ContentType     = "text/html; charset=utf-8"
            $resp.ContentLength64 = $bytes.Length
            $resp.OutputStream.Write($bytes, 0, $bytes.Length)
        }
        $resp.Close()
    }
} finally {
    if ($listener.IsListening) { $listener.Stop() }
    Write-Host "  Server stopped."
}
