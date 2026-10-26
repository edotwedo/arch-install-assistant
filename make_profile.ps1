<#
  Builds an encrypted personal profile (profile.enc) for the Linux installer.
  Run this yourself in a terminal so YOU type the passphrase. Claude never sees it.

    pwsh -File .\make_profile.ps1 -MemoryDir .\claude-memory

  Includes ONLY: your Claude memory notes, Claude settings.json, and installer defaults.
  Never includes: login tokens (.credentials.json), chat history, passwords, browser data.
  Output format is what `openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha256` reads.
#>
param(
  [string]$OutFile = "$PSScriptRoot\profile.enc",
  [string]$MemoryDir = "",
  [string]$SettingsFile = "$env:USERPROFILE\.claude\settings.json",
  [string]$TestPassphraseEnvVar = ""   # for automated tests only; leave empty for real use
)
$ErrorActionPreference = "Stop"
$iter = 600000

function Read-Secret($prompt) {
  $s = Read-Host -AsSecureString $prompt
  $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
  try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

if ($TestPassphraseEnvVar) {
  $pass = [Environment]::GetEnvironmentVariable($TestPassphraseEnvVar)
} else {
  $pass = Read-Secret "Choose a passphrase for your profile (12+ characters, write it down somewhere safe)"
  if ($pass.Length -lt 12) { throw "Use at least 12 characters." }
  if ($pass -ne (Read-Secret "Type it again")) { throw "Passphrases did not match." }
}

if (-not $MemoryDir) { throw "Pass -MemoryDir pointing at the folder of markdown notes to include." }
$stage = Join-Path $env:TEMP ("profile_stage_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path "$stage\claude\memory" -Force | Out-Null
Set-Content "$stage\PROFILE-V1" "profile v1"
Get-ChildItem $MemoryDir -Filter *.md | ForEach-Object { Copy-Item $_.FullName "$stage\claude\memory\" }
if (Test-Path $SettingsFile) { Copy-Item $SettingsFile "$stage\claude\settings.json" }
@{ version = 1; defaults = @{ timezone = "America/Los_Angeles"; kb = "us"; desktop = "kde"; extras = "phone,dev,audio,media,claude,apps" } } | ConvertTo-Json -Depth 4 | Set-Content "$stage\manifest.json"

$forbidden = Get-ChildItem $stage -Recurse -File | Where-Object { $_.Name -match 'credential|token|secret|history|\.env' }
if ($forbidden) { throw "Refusing: forbidden file in profile: $($forbidden.Name -join ', ')" }

$tar = "$stage.tar"
tar -cf $tar -C $stage .
$plain = [IO.File]::ReadAllBytes($tar)

$salt = New-Object byte[] 8
[Security.Cryptography.RandomNumberGenerator]::Fill($salt)
$kdf = [Security.Cryptography.Rfc2898DeriveBytes]::new([Text.Encoding]::UTF8.GetBytes($pass), $salt, $iter, [Security.Cryptography.HashAlgorithmName]::SHA256)
$km = $kdf.GetBytes(48)
$aes = [Security.Cryptography.Aes]::Create(); $aes.KeySize = 256; $aes.Mode = "CBC"; $aes.Padding = "PKCS7"
$aes.Key = $km[0..31]; $aes.IV = $km[32..47]
$cipher = $aes.CreateEncryptor().TransformFinalBlock($plain, 0, $plain.Length)
$out = [byte[]]([Text.Encoding]::ASCII.GetBytes("Salted__") + $salt + $cipher)
[IO.File]::WriteAllBytes($OutFile, $out)

# round-trip check with the same math the Linux side uses
$blob = [IO.File]::ReadAllBytes($OutFile)
$salt2 = $blob[8..15]
$kdf2 = [Security.Cryptography.Rfc2898DeriveBytes]::new([Text.Encoding]::UTF8.GetBytes($pass), [byte[]]$salt2, $iter, [Security.Cryptography.HashAlgorithmName]::SHA256)
$km2 = $kdf2.GetBytes(48)
$aes2 = [Security.Cryptography.Aes]::Create(); $aes2.KeySize = 256; $aes2.Mode = "CBC"; $aes2.Padding = "PKCS7"
$aes2.Key = $km2[0..31]; $aes2.IV = $km2[32..47]
$back = $aes2.CreateDecryptor().TransformFinalBlock($blob, 16, $blob.Length - 16)
if ([Convert]::ToBase64String($back) -ne [Convert]::ToBase64String($plain)) { throw "Round-trip check failed; do not use this file." }

[IO.File]::Delete($tar)
[IO.Directory]::Delete($stage, $true)
"Wrote $OutFile ($([math]::Round($out.Length/1KB,1)) KB). Round-trip check passed. Keep your passphrase safe: it cannot be recovered."
