param(
    [ValidateSet('dev', 'build', 'check', 'test')]
    [string]$Action = 'dev'
)
. "$PSScriptRoot\toolchain-env.ps1"
Set-Location (Split-Path $PSScriptRoot -Parent)
if ($Action -eq 'check' -or $Action -eq 'test') {
    & cargo $Action --manifest-path src-tauri/Cargo.toml
} else {
    if ($Action -eq 'build') {
        & '.\node_modules\.bin\tauri.cmd' build --no-bundle
    } else {
        & '.\node_modules\.bin\tauri.cmd' dev
    }
}
exit $LASTEXITCODE
