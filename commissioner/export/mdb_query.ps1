# Run a SQL query against an Access MDB (32-bit Jet) and write CSV to stdout or -Out.
param([string]$Path, [string]$Sql, [string]$Out)
# Stop on error, because a .NET method exception is NON-TERMINATING in a -File script:
# without this a missing Jet provider printed nothing, exited 0, and the caller parsed the
# silence as an empty result set. It must sit AFTER param(), which has to be first.
$ErrorActionPreference = "Stop"
# stdout MUST be UTF-8. Without this, PowerShell hands the pipe whatever the console codepage
# is (cp1252 here) and any name the game spells with an accent - Gerald Rudloff is Gerald with
# an acute in the save - arrives as a byte the caller cannot decode. That killed the reader
# thread and returned an EMPTY result set, so prep and college archived 390 player seasons with
# every single name blank while pro, whose names happen to be ASCII, looked perfect.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$conn = New-Object System.Data.OleDb.OleDbConnection ("Provider=Microsoft.Jet.OLEDB.4.0;Data Source=$Path")
$conn.Open()
$da = New-Object System.Data.OleDb.OleDbDataAdapter ($Sql, $conn)
$dt = New-Object System.Data.DataTable
[void]$da.Fill($dt)
$conn.Close()
if ($Out) { $dt | Export-Csv -NoTypeInformation -Encoding UTF8 -Path $Out } else { $dt | ConvertTo-Csv -NoTypeInformation }
