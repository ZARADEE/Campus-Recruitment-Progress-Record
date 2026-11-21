$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$toolchainRoot = Join-Path $projectRoot '.toolchain'
$localRustup = Join-Path $toolchainRoot 'cargo\bin\rustup.exe'
$localCargo = Join-Path $toolchainRoot 'cargo\bin\cargo.exe'
if ((Test-Path $localRustup) -and (Test-Path $localCargo)) {
    $env:RUSTUP_HOME = Join-Path $toolchainRoot 'rustup'
    $env:CARGO_HOME = Join-Path $toolchainRoot 'cargo'
    $env:RUSTUP_DIST_SERVER = 'https://rsproxy.cn'
    $env:RUSTUP_UPDATE_ROOT = 'https://rsproxy.cn/rustup'
    $env:TEMP = Join-Path $toolchainRoot 'tmp'
    $env:TMP = $env:TEMP
    $env:npm_config_cache = Join-Path $toolchainRoot 'npm-cache'
    $env:RUSTUP_TOOLCHAIN = 'stable-x86_64-pc-windows-msvc'
    New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
    $env:PATH = "$toolchainRoot\cargo\bin;$env:PATH"

    $vcRoot = Get-ChildItem "$toolchainRoot\msvc\VC\Tools\MSVC" -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
    $sdkRoot = Join-Path $toolchainRoot 'msvc\Windows Kits\10'
    $sdkVersion = Get-ChildItem "$sdkRoot\Include" -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
    if ($vcRoot -and $sdkVersion) {
        $vcPath = $vcRoot.FullName
        $sdkName = $sdkVersion.Name
        $env:VCToolsInstallDir = "$vcPath\"
        $env:VCToolsVersion = $vcRoot.Name
        $env:WindowsSdkDir = "$sdkRoot\"
        $env:WindowsSDKVersion = "$sdkName\"
        $env:VSCMD_ARG_HOST_ARCH = 'x64'
        $env:VSCMD_ARG_TGT_ARCH = 'x64'
        $env:INCLUDE = "$vcPath\include;$sdkRoot\Include\$sdkName\ucrt;$sdkRoot\Include\$sdkName\shared;$sdkRoot\Include\$sdkName\um;$sdkRoot\Include\$sdkName\winrt"
        $env:LIB = "$vcPath\lib\x64;$sdkRoot\Lib\$sdkName\ucrt\x64;$sdkRoot\Lib\$sdkName\um\x64"
        $env:PATH = "$vcPath\bin\Hostx64\x64;$sdkRoot\bin\$sdkName\x64;$env:PATH"
    }
}

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw 'Install Rust with the MSVC toolchain, or provide the project-local .toolchain directory.'
}
if (-not $env:CAMPUS_DATA_DIR) { $env:CAMPUS_DATA_DIR = Join-Path $projectRoot 'data' }
