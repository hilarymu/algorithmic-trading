# dashboard.ps1 - Multi-Politician Copy Trader Dashboard Generator

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$cfg       = Get-Content "$ScriptDir\config.json" | ConvertFrom-Json
$OUT       = "$ScriptDir\dashboard.html"

$H = @{
    "APCA-API-KEY-ID"     = $cfg.alpaca_key
    "APCA-API-SECRET-KEY" = $cfg.alpaca_secret
}
$BASE = $cfg.alpaca_base
$DATA = "https://data.alpaca.markets/v2"
$UA   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

Write-Host "Fetching Alpaca data..."
$acct      = Invoke-RestMethod -Uri "$BASE/account"   -Headers $H
$positions = Invoke-RestMethod -Uri "$BASE/positions" -Headers $H
$orders    = Invoke-RestMethod -Uri "$BASE/orders?status=all&limit=50" -Headers $H
$clock     = Invoke-RestMethod -Uri "$BASE/clock"     -Headers $H

$executed = @()
if (Test-Path "$ScriptDir\trades_executed.json") {
    $raw = Get-Content "$ScriptDir\trades_executed.json" -Raw
    if ($raw -and $raw.Trim() -ne "[]") { try { $executed = $raw | ConvertFrom-Json } catch {} }
}

$logLines = @()
if (Test-Path "$ScriptDir\copy_trader.log") {
    $logLines = Get-Content "$ScriptDir\copy_trader.log" -Tail 80
}

function Fmtc($v) { $v=[double]$v; $s=if($v -ge 0){"+"} else {""}; "$s$([Math]::Round($v,2).ToString('N2'))" }
function Fmtp($v) { $v=[double]$v; $s=if($v -ge 0){"+"} else {""}; "$s$([Math]::Round($v,2))%" }
function PLC($v)  { if([double]$v -ge 0){"#16a34a"} else {"#dc2626"} }
function PartyColor($p) { if($p -eq "R"){"#dc2626"} else {"#2563eb"} }
function PartyBg($p)    { if($p -eq "R"){"#fff1f2"} else {"#eff6ff"} }

# â”€â”€ Fetch Capitol Trades per politician â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Write-Host "Fetching Capitol Trades..."
$ctByPol = @{}
foreach ($pol in $cfg.politicians) {
    $trades = [System.Collections.Generic.List[psobject]]::new()
    try {
        $html = (Invoke-WebRequest -Uri "https://www.capitoltrades.com/trades?politician=$($pol.id)&pageSize=20&page=1" -UserAgent $UA -UseBasicParsing -TimeoutSec 20).Content
        $p_ticker = '\\\"issuerTicker\\\":\\\"([A-Z0-9.]+):US\\\"'
        $p_txType = '\\\"txType\\\":\\\"(buy|sell)\\\"'
        $p_txDate = '\\\"txDate\\\":\\\"(\d{4}-\d{2}-\d{2})\\\"'
        $p_txId   = '\\\"_txId\\\":(\d+)'
        $p_value  = '\\\"value\\\":(\d+)'
        $m_ticker = [regex]::Matches($html, $p_ticker, 'IgnoreCase')
        $m_txType = [regex]::Matches($html, $p_txType, 'IgnoreCase')
        $m_txDate = [regex]::Matches($html, $p_txDate)
        $m_txIdM  = [regex]::Matches($html, $p_txId)
        $m_value  = [regex]::Matches($html, $p_value)
        foreach ($tm in ($m_ticker | Select-Object -First 15)) {
            $tIdx     = $tm.Index
            $nearId   = $m_txIdM  | Where-Object { $_.Index -lt $tIdx } | Select-Object -Last 1
            $nearType = $m_txType | Where-Object { $_.Index -gt $tIdx } | Select-Object -First 1
            $nearDate = $m_txDate | Where-Object { $_.Index -gt $tIdx } | Select-Object -First 1
            $nearVal  = $m_value  | Where-Object { $_.Index -gt $tIdx } | Select-Object -First 1
            if (-not $nearType -or -not $nearDate) { continue }
            if (($nearType.Index - $tIdx) -gt 2000) { continue }
            $txId  = if ($nearId) { "$($pol.id)-$($nearId.Groups[1].Value)" } else { "" }
            $value = if ($nearVal -and ($nearVal.Index - $tIdx) -lt 2000) { try { [long]$nearVal.Groups[1].Value } catch { 0 } } else { 0 }
            $trades.Add([pscustomobject]@{
                txId    = $txId
                ticker  = $tm.Groups[1].Value.ToUpper()
                action  = $nearType.Groups[1].Value.ToUpper()
                date    = $nearDate.Groups[1].Value
                value   = $value
                copied  = $executed -contains $txId
            })
        }
    } catch { Write-Host "  Error fetching $($pol.name): $_" }
    $ctByPol[$pol.id] = $trades
    Start-Sleep -Milliseconds 800
}

# â”€â”€ Summary numbers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
$portfolioValue = [double]$acct.portfolio_value
$cash           = [double]$acct.cash
$lastEquity     = [double]$acct.last_equity
$todayPL        = [double]$acct.equity - $lastEquity
$todayPLPct     = if($lastEquity -gt 0){ $todayPL / $lastEquity * 100 } else { 0 }
$totalUnrealPL  = ($positions | Measure-Object -Property unrealized_pl -Sum).Sum
$totalCost      = ($positions | Measure-Object -Property cost_basis    -Sum).Sum
$totalUnrealPct = if($totalCost -gt 0){ $totalUnrealPL / $totalCost * 100 } else { 0 }
$totalMV        = ($positions | Measure-Object -Property market_value  -Sum).Sum
$marketStatus   = if($clock.is_open){"OPEN"} else {"CLOSED"}
$marketColor    = if($clock.is_open){"#16a34a"} else {"#dc2626"}
$marketBg       = if($clock.is_open){"#f0fdf4"} else {"#fef2f2"}
$now            = Get-Date -Format "ddd MMM d, yyyy  h:mm tt"

# â”€â”€ Position rows â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
$posRows = ""
foreach ($p in ($positions | Sort-Object { [double]$_.unrealized_pl } -Descending)) {
    $upl    = [double]$p.unrealized_pl
    $uplPct = [double]$p.unrealized_plpc * 100
    $dayPct = if($p.change_today){ [double]$p.change_today * 100 } else { 0 }
    $uplC   = PLC $upl
    $dayC   = PLC $dayPct
    $posRows += @"
<tr>
  <td><span class="sym">$($p.symbol)</span></td>
  <td class="num">$($p.qty)</td>
  <td class="num">$([Math]::Round([double]$p.avg_entry_price,2).ToString('N2'))</td>
  <td class="num">$([Math]::Round([double]$p.current_price,2).ToString('N2'))</td>
  <td class="num">$([Math]::Round([double]$p.market_value,2).ToString('N2'))</td>
  <td class="num" style="color:$uplC;font-weight:700">$(Fmtc $upl)</td>
  <td class="num" style="color:$uplC;font-weight:700">$(Fmtp $uplPct)</td>
  <td class="num" style="color:$dayC">$(Fmtp $dayPct)</td>
</tr>
"@
}
$posRows += @"
<tr class="total-row">
  <td colspan="4">TOTAL ($($positions.Count) positions)</td>
  <td class="num">$([Math]::Round($totalMV,2).ToString('N2'))</td>
  <td class="num" style="color:$(PLC $totalUnrealPL);font-weight:700">$(Fmtc $totalUnrealPL)</td>
  <td class="num" style="color:$(PLC $totalUnrealPL);font-weight:700">$(Fmtp $totalUnrealPct)</td>
  <td></td>
</tr>
"@

# â”€â”€ Politician panels â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
$polPanels = ""
foreach ($pol in $cfg.politicians) {
    $trades    = $ctByPol[$pol.id]
    $copiedCnt = ($trades | Where-Object { $_.copied }).Count
    $pColor    = PartyColor $pol.party
    $pBg       = PartyBg $pol.party
    $scoreBar  = [Math]::Round($pol.score)

    $tradeRows = ""
    foreach ($t in $trades) {
        $ac  = if($t.action -eq "BUY"){"#22c55e"} else {"#ef4444"}
        $cpd = if($t.copied){'<span class="badge-copied">Copied</span>'} else {'<span class="badge-pending">Pending</span>'}
        $val = if($t.value -gt 0){ '$' + ([long]$t.value).ToString('N0') } else { "-" }
        $tradeRows += "<tr><td><span class='sym2'>$($t.ticker)</span></td><td style='color:$ac;font-weight:700;font-size:11px'>$($t.action)</td><td style='font-size:11px;color:#64748b'>$($t.date)</td><td style='font-size:11px'>$val</td><td>$cpd</td></tr>"
    }

    $polPanels += @"
<div class="pol-panel">
  <div class="pol-header">
    <div>
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">
        <span style="font-size:16px;font-weight:800;color:#0f172a">$($pol.name)</span>
        <span style="background:$pBg;color:$pColor;border:1px solid $pColor;border-radius:10px;font-size:10px;font-weight:700;padding:2px 8px">$($pol.party)-$($pol.state)</span>
      </div>
      <div style="font-size:11px;color:#64748b">Score: $($pol.score)/100 &nbsp;|&nbsp; $copiedCnt copied of $($trades.Count) recent</div>
    </div>
    <div style="text-align:right">
      <div style="font-size:11px;color:#64748b;margin-bottom:4px">Composite Score</div>
      <div style="background:#e2e8f0;border-radius:6px;height:8px;width:100px;overflow:hidden">
        <div style="background:linear-gradient(90deg,#3b82f6,#22c55e);height:100%;width:$scoreBar%"></div>
      </div>
      <div style="font-size:13px;font-weight:800;color:#2563eb;margin-top:3px">$($pol.score)/100</div>
    </div>
  </div>
  <table style="width:100%;border-collapse:collapse;margin-top:12px">
    <thead><tr>
      <th style="$thStyle">Ticker</th>
      <th style="$thStyle">Action</th>
      <th style="$thStyle">Date</th>
      <th style="$thStyle">Value</th>
      <th style="$thStyle">Status</th>
    </tr></thead>
    <tbody>$tradeRows</tbody>
  </table>
  <div style="margin-top:10px;text-align:right">
    <a href="https://www.capitoltrades.com/politicians/$($pol.id)" target="_blank" style="font-size:11px;color:#3b82f6">View all on Capitol Trades &rarr;</a>
  </div>
</div>
"@
}

# â”€â”€ Recent orders â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
$orderRows = ""
foreach ($o in ($orders | Select-Object -First 20)) {
    $oc = if($o.side -eq "buy"){"#22c55e"} else {"#ef4444"}
    $ts = if($o.submitted_at){ $o.submitted_at.ToString().Substring(0,16) } else { "" }
    $st = $o.status
    $sc = if($st -eq "filled"){"#22c55e"} elseif($st -match "cancel"){"#475569"} else {"#f59e0b"}
    $orderRows += "<tr><td style='font-size:11px;color:#475569;white-space:nowrap'>$ts</td><td style='color:$oc;font-weight:700;font-size:12px'>$($o.side.ToUpper())</td><td><span class='sym2'>$($o.symbol)</span></td><td class='num' style='font-size:12px'>$($o.qty)</td><td style='color:$sc;font-size:11px'>$st</td></tr>"
}

# â”€â”€ Log rows â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
$logFiltered = $logLines | Where-Object { $_ -match "ORDER OK|ORDER FAILED|SKIP BUY|txId=|politician|CLOSED|OPEN" } | Select-Object -Last 30
$logRows = ""
foreach ($line in $logFiltered) {
    $ts  = if($line -match "^\[([^\]]+)\]"){ $Matches[1] } else { "" }
    $msg = $line -replace "^\[[^\]]+\]\[[^\]]+\]\s*",""
    $cl  = if($line -match "ORDER OK"){"#22c55e"} elseif($line -match "FAILED|WARN"){"#ef4444"} elseif($line -match "SKIP"){"#475569"} else {"#64748b"}
    $logRows += "<tr><td style='color:#94a3b8;font-size:10px;white-space:nowrap;padding:4px 6px'>$ts</td><td style='color:$cl;font-size:11px;font-family:Consolas,monospace;padding:4px 6px'>$msg</td></tr>"
}

# â”€â”€ Scheduler logs (screener / executor / monitor) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
$LOGS_DIR = Join-Path (Split-Path -Parent $PSScriptRoot) "screener_trader\logs"
function EscHtml($s) { $s -replace '&','&amp;' -replace '<','&lt;' -replace '>','&gt;' }
function Get-LogRows {
    param([string]$Prefix, [int]$Tail = 30)
    $f = Get-ChildItem "$LOGS_DIR\${Prefix}_*.log" -ErrorAction SilentlyContinue |
         Sort-Object Name -Descending | Select-Object -First 1
    if (-not $f) { return "(no log yet)", "" }
    $lines = Get-Content $f.FullName -Tail $Tail -ErrorAction SilentlyContinue
    $rows  = ""
    foreach ($line in $lines) {
        if (-not $line.Trim()) { continue }
        $cl = if   ($line -match "WARN|ERROR|FAILED|exit [^0]")        { "#ef4444" }
              elseif($line -match "ACTION\s*:\s*(?!None)|TRAILING|RUNG \d+ RE-PLACED|HARD STOP|raised") { "#22c55e" }
              elseif($line -match "starting|done \(exit 0\)")           { "#2563eb" }
              else                                                       { "#374151" }
        $esc   = EscHtml $line
        $rows += "<tr><td style='color:$cl;font-size:10px;font-family:Consolas,monospace;padding:2px 8px;border-bottom:1px solid #f1f5f9;white-space:pre;overflow:hidden;text-overflow:ellipsis'>$esc</td></tr>"
    }
    if (-not $rows) { $rows = "<tr><td style='color:#94a3b8;font-size:10px;padding:6px 8px'>(empty)</td></tr>" }
    return $f.Name, $rows
}
$screenerFile, $screenerRows = Get-LogRows "screener"
$executorFile, $executorRows = Get-LogRows "executor"
$monitorFile,  $monitorRows  = Get-LogRows "monitor" 40

$thStyle = "font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.8px;font-weight:600;padding:0 8px 8px;text-align:left;border-bottom:1px solid #e2e8f0"

# â”€â”€ Write HTML â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
$html = @"
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Copy Trader Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#f1f5f9;color:#1e293b;font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;padding:22px 26px;min-height:100vh}
a{color:inherit;text-decoration:none}
.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;flex-wrap:wrap;gap:10px}
.logo{font-size:20px;font-weight:800;color:#0f172a;letter-spacing:-0.5px}.logo span{color:#2563eb}
.market-badge{padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700;background:$marketBg;color:$marketColor;border:1px solid $marketColor}
.refresh-btn{padding:6px 14px;border-radius:8px;background:#fff;border:1px solid #cbd5e1;color:#374151;font-size:12px;cursor:pointer;font-weight:600}
.refresh-btn:hover{background:#f8fafc}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.card .lbl{font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.8px;font-weight:600;margin-bottom:7px}
.card .val{font-size:22px;font-weight:800;color:#0f172a;letter-spacing:-0.5px}
.card .sub{font-size:12px;margin-top:4px;font-weight:600}
.panel{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.panel h2{font-size:12px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:1px;margin-bottom:14px}
table{width:100%;border-collapse:collapse}
th{font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.8px;font-weight:600;padding:0 10px 10px;text-align:left;border-bottom:1px solid #e2e8f0}
td{padding:9px 10px;border-bottom:1px solid #f1f5f9;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#f8fafc}
.sym{font-weight:800;color:#2563eb;font-size:14px}.sym2{font-weight:700;color:#2563eb;font-size:12px}
.num{text-align:right;font-variant-numeric:tabular-nums}
.total-row td{font-weight:700;color:#0f172a;border-top:1px solid #e2e8f0;padding-top:11px;border-bottom:none}
.badge-copied{background:#dcfce7;color:#16a34a;font-size:10px;font-weight:700;padding:2px 7px;border-radius:8px}
.badge-pending{background:#f1f5f9;color:#94a3b8;font-size:10px;font-weight:700;padding:2px 7px;border-radius:8px}
.pol-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px;margin-bottom:16px}
.pol-panel{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:18px 20px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.pol-header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
@media(max-width:860px){.two-col{grid-template-columns:1fr}}
.footer{margin-top:22px;display:flex;gap:20px;flex-wrap:wrap;align-items:center;border-top:1px solid #e2e8f0;padding-top:16px}
.fs{font-size:11px;color:#94a3b8}.fs span{color:#64748b;font-weight:600}
</style>
</head>
<body>

<div class="topbar">
  <div style="display:flex;align-items:center;gap:12px">
    <div class="logo">Copy<span>Trader</span></div>
    <div class="market-badge">&#9679; Market $marketStatus</div>
  </div>
  <div style="display:flex;align-items:center;gap:10px">
    <span style="font-size:12px;color:#94a3b8">$now</span>
    <button class="refresh-btn" onclick="window.location.reload()">&#8635; Refresh</button>
  </div>
</div>

<!-- SUMMARY CARDS -->
<div class="cards">
  <div class="card">
    <div class="lbl">Portfolio Value</div>
    <div class="val">`$$([Math]::Round($portfolioValue,2).ToString('N2'))</div>
    <div class="sub" style="color:#475569">Cash: `$$([Math]::Round($cash,0).ToString('N0'))</div>
  </div>
  <div class="card">
    <div class="lbl">Today P&amp;L</div>
    <div class="val" style="color:$(PLC $todayPL)">$(Fmtc $todayPL)</div>
    <div class="sub" style="color:$(PLC $todayPL)">$(Fmtp $todayPLPct) today</div>
  </div>
  <div class="card">
    <div class="lbl">Unrealized P&amp;L</div>
    <div class="val" style="color:$(PLC $totalUnrealPL)">$(Fmtc $totalUnrealPL)</div>
    <div class="sub" style="color:$(PLC $totalUnrealPL)">$(Fmtp $totalUnrealPct) on cost</div>
  </div>
  <div class="card">
    <div class="lbl">Invested</div>
    <div class="val">`$$([Math]::Round($totalMV,0).ToString('N0'))</div>
    <div class="sub" style="color:#475569">$($positions.Count) positions</div>
  </div>
  <div class="card">
    <div class="lbl">Trades Copied</div>
    <div class="val" style="color:#2563eb">$($executed.Count)</div>
    <div class="sub" style="color:#475569">across 5 politicians</div>
  </div>
  <div class="card">
    <div class="lbl">Buying Power</div>
    <div class="val">`$$([Math]::Round([double]$acct.buying_power,0).ToString('N0'))</div>
    <div class="sub" style="color:#475569">Trade size: `$$($cfg.trade_amount_usd)</div>
  </div>
</div>

<!-- POSITIONS TABLE -->
<div class="panel">
  <h2>Open Positions</h2>
  <table>
    <thead><tr>
      <th>Symbol</th><th>Qty</th><th class="num">Avg Entry</th><th class="num">Price</th>
      <th class="num">Mkt Value</th><th class="num">Unreal P&amp;L</th><th class="num">P&amp;L %</th><th class="num">Day %</th>
    </tr></thead>
    <tbody>$posRows</tbody>
  </table>
</div>

<!-- POLITICIAN PANELS -->
<div class="panel" style="margin-bottom:16px">
  <h2>Politicians Being Followed (5)</h2>
  <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:4px">
$(foreach ($pol in $cfg.politicians) {
  $pc = PartyColor $pol.party
  $pb = PartyBg $pol.party
  "    <div style='background:$pb;border:1px solid $pc;border-radius:8px;padding:6px 14px;font-size:12px'>
      <span style='color:#0f172a;font-weight:700'>$($pol.name)</span>
      <span style='color:$pc;margin-left:6px;font-size:10px;font-weight:700'>$($pol.party)-$($pol.state)</span>
      <span style='color:#64748b;margin-left:6px;font-size:10px'>$($pol.score)/100</span>
    </div>"
})
  </div>
</div>

<div class="pol-grid">
$polPanels
</div>

<!-- ORDERS + LOG -->
<div class="two-col">
  <div class="panel">
    <h2>Recent Orders</h2>
    <table>
      <thead><tr>
        <th>Time</th><th>Side</th><th>Symbol</th><th class="num">Qty</th><th>Status</th>
      </tr></thead>
      <tbody>$orderRows</tbody>
    </table>
  </div>
  <div class="panel">
    <h2>Bot Activity Log</h2>
    <table>
      <tbody>$logRows</tbody>
    </table>
  </div>
</div>

<!-- SCHEDULER LOGS -->
<div class="panel">
  <h2>Scheduler Logs</h2>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px">
    <div>
      <div style="font-size:10px;color:#64748b;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">
        Screener &nbsp;<span style="color:#94a3b8;font-weight:400;text-transform:none;font-size:9px">$screenerFile</span>
      </div>
      <div style="max-height:220px;overflow-y:auto;border:1px solid #e2e8f0;border-radius:6px;background:#f8fafc">
        <table style="width:100%"><tbody>$screenerRows</tbody></table>
      </div>
    </div>
    <div>
      <div style="font-size:10px;color:#64748b;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">
        Executor &nbsp;<span style="color:#94a3b8;font-weight:400;text-transform:none;font-size:9px">$executorFile</span>
      </div>
      <div style="max-height:220px;overflow-y:auto;border:1px solid #e2e8f0;border-radius:6px;background:#f8fafc">
        <table style="width:100%"><tbody>$executorRows</tbody></table>
      </div>
    </div>
    <div>
      <div style="font-size:10px;color:#64748b;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">
        Monitor &nbsp;<span style="color:#94a3b8;font-weight:400;text-transform:none;font-size:9px">$monitorFile</span>
      </div>
      <div style="max-height:220px;overflow-y:auto;border:1px solid #e2e8f0;border-radius:6px;background:#f8fafc">
        <table style="width:100%"><tbody>$monitorRows</tbody></table>
      </div>
    </div>
  </div>
</div>

<div class="footer">
  <div class="fs">Account: <span>$($acct.account_number)</span></div>
  <div class="fs">Status: <span>$($acct.status)</span></div>
  <div class="fs">Last Equity: <span>`$$([Math]::Round($lastEquity,2).ToString('N2'))</span></div>
  <div class="fs">Generated: <span>$now</span></div>
  <div class="fs" style="margin-left:auto">dashboard.ps1</div>
</div>

</body>
</html>
"@

$html | Set-Content $OUT -Encoding UTF8
Write-Host "Dashboard written -> $OUT"
Start-Process $OUT

