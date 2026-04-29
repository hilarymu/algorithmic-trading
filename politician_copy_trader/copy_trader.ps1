# copy_trader.ps1 - Multi-Politician Copy Trading Bot
# Follows 5 politicians via Capitol Trades -> Alpaca Paper Account
# Usage: .\copy_trader.ps1 [-DryRun] [-Force]

param(
    [switch]$DryRun,
    [switch]$Force
)

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$cfg           = Get-Content "$ScriptDir\config.json" | ConvertFrom-Json
$ALPACA_KEY    = $cfg.alpaca_key
$ALPACA_SECRET = $cfg.alpaca_secret
$ALPACA_BASE   = $cfg.alpaca_base
$DATA_BASE     = "https://data.alpaca.markets/v2"
$TRADE_USD     = $cfg.trade_amount_usd
$PAGES         = $cfg.pages_to_fetch
$POLITICIANS   = $cfg.politicians

$EXECUTED_FILE = "$ScriptDir\trades_executed.json"
$LOG_FILE      = "$ScriptDir\copy_trader.log"

$ALP_HEADERS = @{
    "APCA-API-KEY-ID"     = $ALPACA_KEY
    "APCA-API-SECRET-KEY" = $ALPACA_SECRET
    "Content-Type"        = "application/json"
}

function Log {
    param($msg, $level = "INFO")
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts][$level] $msg"
    Write-Host $line
    Add-Content -Path $LOG_FILE -Value $line -ErrorAction SilentlyContinue
}

function Is-MarketOpen {
    try {
        $c = Invoke-RestMethod -Uri "$ALPACA_BASE/clock" -Headers $ALP_HEADERS -Method GET -TimeoutSec 10
        return $c.is_open
    } catch {
        Log "Market clock check failed: $_" "WARN"
        return $false
    }
}

function Get-CapitolTradesPage {
    param([string]$PolId, [int]$PageNum = 1)
    $url = "https://www.capitoltrades.com/trades?politician=$PolId&pageSize=20&page=$PageNum"
    $ua  = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    try {
        $r = Invoke-WebRequest -Uri $url -UserAgent $ua -UseBasicParsing -TimeoutSec 30
        return $r.Content
    } catch {
        Log "Error fetching $PolId page $PageNum : $_" "ERROR"
        return $null
    }
}

function Parse-Trades {
    param([string]$html, [string]$polId, [string]$polName)
    $trades = [System.Collections.Generic.List[psobject]]::new()
    if (-not $html) { return $trades }

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

    foreach ($tickerMatch in $m_ticker) {
        $tIdx   = $tickerMatch.Index
        $ticker = $tickerMatch.Groups[1].Value.ToUpper()

        $nearId   = $m_txIdM  | Where-Object { $_.Index -lt $tIdx } | Select-Object -Last 1
        $nearType = $m_txType | Where-Object { $_.Index -gt $tIdx } | Select-Object -First 1
        $nearDate = $m_txDate | Where-Object { $_.Index -gt $tIdx } | Select-Object -First 1
        $nearVal  = $m_value  | Where-Object { $_.Index -gt $tIdx } | Select-Object -First 1

        if (-not $nearType -or -not $nearDate) { continue }
        if (($nearType.Index - $tIdx) -gt 2000) { continue }

        $txId  = if ($nearId) { "$polId-$($nearId.Groups[1].Value)" } else { "$polId-$ticker-$($nearDate.Groups[1].Value)" }
        $value = if ($nearVal -and ($nearVal.Index - $tIdx) -lt 2000) { [int]$nearVal.Groups[1].Value } else { 0 }

        $trades.Add([pscustomobject]@{
            txId      = $txId
            polId     = $polId
            polName   = $polName
            ticker    = $ticker
            action    = $nearType.Groups[1].Value.ToUpper()
            date      = $nearDate.Groups[1].Value
            value     = $value
        })
    }
    return $trades
}

function Get-Price {
    param([string]$ticker)
    try {
        $r = Invoke-RestMethod -Uri "$DATA_BASE/stocks/$ticker/quotes/latest" -Headers $ALP_HEADERS -Method GET -TimeoutSec 10
        $ap = $r.quote.ap; $bp = $r.quote.bp
        if ($ap -gt 0 -and $bp -gt 0) { return ($ap + $bp) / 2 }
        if ($ap -gt 0) { return $ap }
        if ($bp -gt 0) { return $bp }
    } catch {}
    try {
        $r2 = Invoke-RestMethod -Uri "$DATA_BASE/stocks/$ticker/trades/latest" -Headers $ALP_HEADERS -Method GET -TimeoutSec 10
        return $r2.trade.p
    } catch {}
    return 0
}

function Get-Positions {
    try {
        return Invoke-RestMethod -Uri "$ALPACA_BASE/positions" -Headers $ALP_HEADERS -Method GET -TimeoutSec 10
    } catch { return @() }
}

function Place-Order {
    param([string]$ticker, [string]$side, [int]$qty)
    $body = @{ symbol=$ticker; qty="$qty"; side=$side.ToLower(); type="market"; time_in_force="day" } | ConvertTo-Json
    try {
        $r = Invoke-RestMethod -Uri "$ALPACA_BASE/orders" -Method POST -Headers $ALP_HEADERS -Body $body -TimeoutSec 15
        Log "  ORDER OK: $side $qty $ticker | id=$($r.id) status=$($r.status)"
        return $r.id
    } catch {
        $msg = $_.ErrorDetails.Message
        if (-not $msg) { $msg = $_.Exception.Message }
        Log "  ORDER FAILED: $side $ticker | $msg" "ERROR"
        return $null
    }
}

function Execute-Trade {
    param($trade)
    $ticker = ($trade.ticker -replace "\.[A-Z]+$","").ToUpper()
    $action = $trade.action

    if ($action -eq "BUY") {
        # Skip OTC/Pink Sheet stocks — Alpaca IEX feed doesn't cover them
        # OTC tickers: end in F (foreign), Y (ADR), E (delinquent), or contain a dot
        if ($ticker -match "F$|Y$|E$|\." -and $ticker.Length -ge 5) {
            Log "  SKIP BUY $ticker - OTC/Pink Sheet ticker, not exchange-listed" "WARN"
            return $false
        }
        $price = Get-Price $ticker
        if ($price -le 0) {
            Log "  SKIP BUY $ticker - could not get price (not on IEX feed)" "WARN"
            return $false
        }
        $qty = [Math]::Max(1, [Math]::Floor($TRADE_USD / $price))
        if ($DryRun) {
            $px = [Math]::Round($price, 2)
            Log "  [DRY RUN] BUY $qty x $ticker at approx $px"
            return $true
        }
        return ($null -ne (Place-Order $ticker "buy" $qty))

    } elseif ($action -eq "SELL") {
        $positions = Get-Positions
        $pos = $positions | Where-Object { $_.symbol -eq $ticker }
        if (-not $pos -or [int]$pos.qty -le 0) {
            Log "  SKIP SELL $ticker - no position held" "INFO"
            return $true
        }
        $qty = [int]$pos.qty
        if ($DryRun) {
            Log "  [DRY RUN] SELL $qty x $ticker"
            return $true
        }
        return ($null -ne (Place-Order $ticker "sell" $qty))

    } else {
        Log "  SKIP $ticker - unknown action $action" "WARN"
        return $true
    }
}

# === MAIN ===
Log "=================================================="
Log "Multi-Politician Copy Trader | $($POLITICIANS.Count) politicians"
Log "DryRun=$DryRun  Force=$Force  TradeSize=$TRADE_USD USD"

if (-not $Force -and -not $DryRun) {
    $open = Is-MarketOpen
    if (-not $open) {
        Log "Market is CLOSED - exiting. Use -Force to override."
        exit 0
    }
    Log "Market is OPEN - proceeding."
}

# Load executed IDs
$executed = @()
if (Test-Path $EXECUTED_FILE) {
    $raw = Get-Content $EXECUTED_FILE -Raw -ErrorAction SilentlyContinue
    if ($raw -and $raw.Trim() -ne "" -and $raw.Trim() -ne "[]") {
        try { $executed = $raw | ConvertFrom-Json } catch {}
    }
}
Log "Previously executed: $($executed.Count) trade IDs"

$grandTotal  = 0
$grandNew    = 0
$grandDone   = 0

# Loop each politician
foreach ($pol in $POLITICIANS) {
    Log ""
    Log "--- $($pol.name) ($($pol.party)-$($pol.state)) [score: $($pol.score)] ---"

    $allTrades = [System.Collections.Generic.List[psobject]]::new()
    for ($pg = 1; $pg -le $PAGES; $pg++) {
        $html   = Get-CapitolTradesPage -PolId $pol.id -PageNum $pg
        $parsed = Parse-Trades -html $html -polId $pol.id -polName $pol.name
        Log "  Page $pg -> $($parsed.Count) trades"
        foreach ($t in $parsed) { $allTrades.Add($t) }
        if ($pg -lt $PAGES) { Start-Sleep -Seconds 1 }
    }

    $newTrades = $allTrades | Where-Object { $executed -notcontains $_.txId }
    Log "  New trades: $($newTrades.Count) of $($allTrades.Count)"
    $grandTotal += $allTrades.Count
    $grandNew   += $newTrades.Count

    foreach ($trade in $newTrades) {
        Log "  txId=$($trade.txId): $($trade.action) $($trade.ticker) on $($trade.date)"
        $ok = Execute-Trade -trade $trade
        if ($ok) {
            $executed += $trade.txId
            $grandDone++
        }
        Start-Sleep -Milliseconds 300
    }
    Start-Sleep -Seconds 1
}

if (-not $DryRun) {
    $executed | ConvertTo-Json | Set-Content $EXECUTED_FILE
    Log ""
    Log "Saved $($executed.Count) total executed IDs."
} else {
    Log "[DRY RUN] Not persisting IDs."
}

Log "Done. Processed $grandDone / $grandNew new trades across $($POLITICIANS.Count) politicians."
Log "=================================================="
