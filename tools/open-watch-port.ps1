# Jalankan ini sebagai Administrator.
#
# Kenapa perlu: Windows Firewall memblokir semua koneksi masuk secara default,
# termasuk dari hape Anda ke laptop ini. Membuka port 8730 hanya untuk
# LocalSubnet, jadi hanya perangkat di Wi-Fi/jaringan rumah yang bisa menyentuh
# halaman monitor, bukan siapa pun di internet.
#
# Cara pakai: klik kanan file ini -> Run with PowerShell -> pilih Yes saat
# muncul prompt. Setelah selesai, tutup jendela ini.
$ErrorActionPreference = 'Stop'

$rule = 'KernelSUSUSFSWatch'
$port = 8730

Write-Host ''
Write-Host 'Membuka port untuk monitor SUSFS...' -ForegroundColor Cyan

Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue

New-NetFirewallRule -DisplayName $rule `
    -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $port `
    -Profile Any `
    -RemoteAddress LocalSubnet `
    -Description 'Halaman monitor port SUSFS, hanya dari jaringan lokal' | Out-Null

Write-Host "  aturan firewall dibuat untuk port $port" -ForegroundColor Green

# Laporkan jalur mana yang bisa dipakai hape.
$lines = @()
foreach ($a in (Get-NetIPAddress -AddressFamily IPv4 |
                Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' })) {
    $up = (Get-NetAdapter -Name $a.InterfaceAlias -ErrorAction SilentlyContinue).Status
    $lines += [pscustomobject]@{
        Interface = $a.InterfaceAlias
        Alamat    = $a.IPAddress
        Status    = $up
        BisaDihat = ($up -eq 'Up')
    }
}

Write-Host ''
Write-Host 'Alamat yang bisa dibuka dari hape:' -ForegroundColor Cyan
$lines | Format-Table -AutoSize | Out-String | Write-Host

$ts = "$env:ProgramFiles\Tailscale\tailscale.exe"
if (Test-Path $ts) {
    $ip = & $ts ip -4 2>$null
    if ($ip -match '\d+\.\d+\.\d+\.\d+') {
        Write-Host "Tailscale aktif, bisa dibuka dari mana saja (selagi hape ikut login):" -ForegroundColor Green
        Write-Host "  http://$ip`:8730/gta9susfs1`n" -ForegroundColor White
    } else {
        Write-Host 'Tailscale terpasang tapi belum login. Untuk dipakai di luar rumah:' -ForegroundColor Yellow
        Write-Host '  tailscale up' -ForegroundColor White
        Write-Host ' lalu buka browser untuk login, lalu hape juga install Tailscale dan login akun yang sama.' -ForegroundColor DarkGray
        Write-Host ''
    }
} else {
    Write-Host 'Tailscale tidak terpasang, jadi hanya bisa diakses dari Wi-Fi rumah.' -ForegroundColor Yellow
    Write-Host ''
}

$ok = ($lines | Where-Object { $_.BisaDihat } | Select-Object -First 1)
if ($ok) {
    Write-Host "Buka ini di browser hape, di Wi-Fi yang sama:" -ForegroundColor Green
    Write-Host "  http://$($ok.Alamat):8730/gta9susfs1`n" -ForegroundColor White
} else {
    Write-Host 'Tidak ada adapter yang Up. Sambungkan Wi-Fi laptop dulu, lalu ulangi.' -ForegroundColor Yellow
    Write-Host ''
}

Write-Host 'Selesai. Jendela ini boleh ditutup.' -ForegroundColor Cyan
Write-Host ''
Read-Host 'Tekan Enter untuk menutup'