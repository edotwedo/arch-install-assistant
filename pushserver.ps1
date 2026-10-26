$root = "$env:USERPROFILE\archproject\push"
$l = New-Object System.Net.HttpListener
$l.Prefixes.Add("http://127.0.0.1:8123/")
$l.Start()
while ($l.IsListening) {
  $c = $l.GetContext()
  $name = [IO.Path]::GetFileName($c.Request.Url.LocalPath)
  $p = Join-Path $root $name
  if ($name -and (Test-Path $p -PathType Leaf)) {
    $b = [IO.File]::ReadAllBytes($p)
    $c.Response.ContentLength64 = $b.Length
    $c.Response.OutputStream.Write($b,0,$b.Length)
  } else { $c.Response.StatusCode = 404 }
  $c.Response.OutputStream.Close()
}
