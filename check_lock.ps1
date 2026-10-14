$file = 'C:\crypto\ema_vwap\data\candles.db'
Get-Process | ForEach-Object {
  try {
    $m = $_.Modules | Where-Object { $_.FileName -like '*candles.db*' }
    if ($m) {
      Write-Host "Process $($($_.Name)) (PID $($_.Id)) is holding the file"
      break
    }
  } catch {}
}
