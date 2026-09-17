# Run a SQL query against an Access MDB (32-bit Jet) and write CSV to stdout or -Out.
param([string]$Path, [string]$Sql, [string]$Out)
$conn = New-Object System.Data.OleDb.OleDbConnection ("Provider=Microsoft.Jet.OLEDB.4.0;Data Source=$Path")
$conn.Open()
$da = New-Object System.Data.OleDb.OleDbDataAdapter ($Sql, $conn)
$dt = New-Object System.Data.DataTable
[void]$da.Fill($dt)
$conn.Close()
if ($Out) { $dt | Export-Csv -NoTypeInformation -Encoding UTF8 -Path $Out } else { $dt | ConvertTo-Csv -NoTypeInformation }
