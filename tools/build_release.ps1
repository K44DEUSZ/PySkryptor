param(
  [Parameter(Mandatory=$true)][string]$GuiPython,
  [Parameter(Mandatory=$true)][string]$CpuPython,
  [string]$CudaPython,
  [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot 'dist\PySkryptor' }
$releaseRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $releaseRoot) {
  throw 'Choose a new output directory to avoid mixing files from different builds.'
}
$buildRoot = Join-Path $projectRoot ('build\release-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$variants = @(
  @{ Name='gui'; Python=$GuiPython; Spec='PySkryptor.spec'; Bundle='PySkryptor' },
  @{ Name='cpu'; Python=$CpuPython; Spec='AIModelHost.spec'; Bundle='AIModelHost' }
)
if ($CudaPython) {
  $variants += @{ Name='cuda'; Python=$CudaPython; Spec='AIModelHost.spec'; Bundle='AIModelHost' }
}
Push-Location -LiteralPath $projectRoot
try {
  foreach ($variant in $variants) {
    $variantRoot = Join-Path $buildRoot $variant.Name
    & $variant.Python tools/build_bundle.py --spec $variant.Spec --distpath (Join-Path $variantRoot 'dist') --workpath (Join-Path $variantRoot 'work')
    if ($LASTEXITCODE -ne 0) { throw ('Build failed: ' + $variant.Name) }
    $bundleRoot = Join-Path $variantRoot ('dist\' + $variant.Bundle)
    if ($variant.Name -eq 'gui') {
      New-Item -ItemType Directory -Path (Split-Path $releaseRoot) -Force | Out-Null
      Copy-Item -LiteralPath $bundleRoot -Destination $releaseRoot -Recurse
    } else {
      $destination = Join-Path $releaseRoot ('runtimes\' + $variant.Name)
      New-Item -ItemType Directory -Path (Split-Path $destination) -Force | Out-Null
      Copy-Item -LiteralPath $bundleRoot -Destination $destination -Recurse
    }
    & $variant.Python tools/collect_notices.py --toc (Join-Path $variantRoot ('work\' + $variant.Bundle + '\Analysis-00.toc')) --output (Join-Path $releaseRoot ('licenses\' + $variant.Name))
    if ($LASTEXITCODE -ne 0) { throw ('Notice collection failed: ' + $variant.Name) }
  }
  Copy-Item -LiteralPath (Join-Path $projectRoot 'assets') -Destination $releaseRoot -Recurse
  $germanLocale = Join-Path $releaseRoot 'assets\locales\de.json'
  if (Test-Path -LiteralPath $germanLocale) { Remove-Item -LiteralPath $germanLocale }
  foreach ($file in @('LICENSE', 'THIRD_PARTY_NOTICES.txt')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination $releaseRoot
  }
  $ffmpegSource = Join-Path $projectRoot 'bin\ffmpeg'
  if (-not (Test-Path -LiteralPath (Join-Path $ffmpegSource 'ffmpeg.exe'))) { throw 'Missing bin/ffmpeg/ffmpeg.exe' }
  New-Item -ItemType Directory -Path (Join-Path $releaseRoot 'bin') -Force | Out-Null
  Copy-Item -LiteralPath $ffmpegSource -Destination (Join-Path $releaseRoot 'bin') -Recurse
  $denoPath = & $GuiPython -c 'from deno._find_deno import find_deno_bin; print(find_deno_bin())'
  if ($LASTEXITCODE -ne 0) { throw 'Deno binary not found in the GUI environment' }
  $denoDirectory = Join-Path $releaseRoot 'bin\deno'
  New-Item -ItemType Directory -Path $denoDirectory -Force | Out-Null
  Copy-Item -LiteralPath $denoPath.Trim() -Destination (Join-Path $denoDirectory 'deno.exe')
  & $GuiPython tools/write_manifest.py --root $releaseRoot
  if ($LASTEXITCODE -ne 0) { throw 'Release manifest generation failed' }
  Write-Output ('Built: ' + $releaseRoot)
} finally {
  Pop-Location
}
