# Run this script once as Administrator to register the copy trader scheduled task.
# Right-click PowerShell -> "Run as administrator", then:
#   powershell -ExecutionPolicy Bypass -File "C:\Users\hilary\Documents\Trading\Claude\politician_copy_trader\scripts\register_task.ps1"

$action  = New-ScheduledTaskAction `
               -Execute "powershell.exe" `
               -Argument "-ExecutionPolicy Bypass -NonInteractive -WindowStyle Hidden -File `"C:\Users\hilary\Documents\Trading\Claude\politician_copy_trader\copy_trader.ps1`""

$trigger = New-ScheduledTaskTrigger `
               -Weekly `
               -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
               -At "10:30AM"

$settings = New-ScheduledTaskSettingsSet `
               -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
               -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "Trading-CopyTrader" `
    -Action   $action `
    -Trigger  $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force

Write-Host ""
Write-Host "Registered. Verifying..." -ForegroundColor Cyan
schtasks /Query /TN "Trading-CopyTrader" /FO LIST
