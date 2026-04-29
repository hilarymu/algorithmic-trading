$headers = @{
    "APCA-API-KEY-ID"     = "PKYEAB7TA7IZ7ACXXZDOE525US"
    "APCA-API-SECRET-KEY" = "GRXEKhAEGAX8Ayb31iHbv8x6kfgo22ADAn4Qp1MgfSFb"
}

Write-Host "=== RECENT ORDERS ==="
$orders = Invoke-RestMethod -Uri "https://paper-api.alpaca.markets/v2/orders?limit=20&status=all" -Headers $headers
if ($orders.Count -eq 0) { Write-Host "No orders found." }
foreach ($o in $orders) {
    Write-Host "$($o.submitted_at.ToString().Substring(0,19))  $($o.side.ToUpper()) $($o.qty) $($o.symbol)  status=$($o.status)"
}

Write-Host ""
Write-Host "=== OPEN POSITIONS ==="
$positions = Invoke-RestMethod -Uri "https://paper-api.alpaca.markets/v2/positions" -Headers $headers
if ($positions.Count -eq 0) { Write-Host "No open positions." }
foreach ($p in $positions) {
    Write-Host "$($p.symbol)  qty=$($p.qty)  avg_entry=$($p.avg_entry_price)  unrealized_pl=$($p.unrealized_pl)"
}
