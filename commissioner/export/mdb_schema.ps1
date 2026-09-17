param([string]$Path)
$cs = "Provider=Microsoft.Jet.OLEDB.4.0;Data Source=$Path"
$conn = New-Object System.Data.OleDb.OleDbConnection $cs
try { $conn.Open() } catch { $conn = New-Object System.Data.OleDb.OleDbConnection ("Provider=Microsoft.ACE.OLEDB.12.0;Data Source=$Path"); $conn.Open() }
$tables = $conn.GetSchema("Tables") | Where-Object { $_.TABLE_TYPE -eq "TABLE" }
foreach ($t in $tables) {
  $name = $t.TABLE_NAME
  $cmd = $conn.CreateCommand(); $cmd.CommandText = "SELECT COUNT(*) FROM [$name]"; $n = $cmd.ExecuteScalar()
  $c2 = $conn.CreateCommand(); $c2.CommandText = "SELECT TOP 1 * FROM [$name]"
  $r = $c2.ExecuteReader(); $cols = @(); for ($i = 0; $i -lt $r.FieldCount; $i++) { $cols += $r.GetName($i) }; $r.Close()
  "== $name ($n rows): " + ($cols -join ",")
}
$conn.Close()
