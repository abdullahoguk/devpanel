# DevPanel

Linux masaüstü için minimal, GNOME (libadwaita) tasarımına uygun **dev sunucusu yöneticisi**.
`npm run dev`, `node server.js`, `cloudflared tunnel run …` gibi komutları terminale gitmeden
tek pencereden başlat, durdur, durumunu ve loglarını izle. Panelde bir tray simgesi de var.

Süreçler `systemd --user` ile çalışır. Bu sayede:
- Pencereyi kapatsan da projeler çalışmaya devam eder.
- Durdurunca alt süreçlerin tamamı kapanır (vite, esbuild, nodemon…).
- Loglar journald'de tutulur.

## Özellikler

- Proje listesi, durum göstergesi (🟢 çalışıyor · 🔴 hata · 🔵 dışarıda çalışıyor · 🟠 port başka projede · ⚪ kapalı) ve aç/kapa anahtarı
- Canlı log penceresi, çökünce bildirim
- Proje ekleme, düzenleme ve silme (klasör seçici ile)
- Tarayıcıda aç, klasörü aç, yeniden başlat
- Tray simgesinden hızlı başlat/durdur
- **Dış süreç algılama:** sunucu DevPanel dışında (terminal, Claude Code…) başlatılmışsa port + klasör (portsuz projelerde komut + klasör) eşleşmesiyle algılar ve durdurabilir. Aynı portu kullanan farklı klasördeki projeler birbirine karışmaz.
- Claude Code gibi araçlar için **komut satırı modu**
- Açık/koyu temaya uyum

## Gereksinimler

- Linux Mint 22+ / Ubuntu 24.04+ / Fedora 40+ (libadwaita ≥ 1.5, GTK 4.10+)
- systemd (user session)

## Kurulum

```bash
git clone https://github.com/KULLANICI/devpanel.git
cd devpanel
./install.sh
```

Elle kurmak istersen:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-ayatanaappindicator3-0.1 libnotify-bin psmisc
install -Dm755 devpanel.py ~/.local/bin/devpanel.py
~/.local/bin/devpanel.py install
```

`install` komutu uygulama menüsüne **DevPanel** kısayolunu ekler, tray simgesini de oturum açılışında başlatır.

## Yapılandırma

Ayarlar `~/.config/devpanel/projects.json` dosyasında tutulur. Projeleri arayüzden ekleyebilir
ya da dosyayı elle düzenleyebilirsin. Dosya değişince arayüz kendini yeniler.

```json
{
  "projects": [
    { "name": "Web App", "dir": "~/projeler/web", "cmd": "npm run dev", "url": "http://localhost:5173" },
    { "name": "API", "id": "api", "dir": "~/projeler/api", "cmd": "node server.js", "port": 3001, "env": { "NODE_ENV": "development" } },
    { "name": "Cloudflare Tunnel", "dir": "~", "cmd": "cloudflared tunnel run TUNEL_ADI" }
  ]
}
```

| Alan   | Zorunlu | Açıklama |
|--------|---------|----------|
| `name` | ✓ | Görünen ad |
| `cmd`  | ✓ | Çalıştırılacak komut (`bash -lc` ile çalışır, `&&` kullanılabilir) |
| `dir`  |   | Çalışma klasörü (varsayılan `~`) |
| `url`  |   | Tarayıcı butonu ve port algılama için |
| `port` |   | URL yoksa port algılama için |
| `id`   |   | Servis/CLI adı. Verilmezse addan üretilir (Türkçe karakter içeren adlarda verilmesi önerilir) |
| `env`  |   | Ek ortam değişkenleri |
| `match` |  | Dış süreç algılama için komut satırında aranacak metin (varsayılan: `cmd`) |
| `stop_timeout` | | Durdururken zorla kapatmadan önce beklenecek saniye (varsayılan 5) |

nvm kuruluysa otomatik yüklenir.

## Komut satırı

```bash
devpanel.py              # pencereyi aç
devpanel.py tray         # tray simgesi
devpanel.py status
devpanel.py start   <proje>
devpanel.py stop    <proje>
devpanel.py restart <proje>
devpanel.py logs    <proje> [satır]
```

Doğrudan systemd ile: `systemctl --user list-units 'devpanel-*'`, `journalctl --user -u devpanel-api -f`

## Claude Code / AI ajanlarıyla kullanım

Ajan dev sunucusunu kendi başlatıp kill ederse DevPanel bunu 🔵 *dışarıda çalışıyor* olarak gösterir.
Daha temiz bir akış için projedeki `CLAUDE.md` dosyasına şunu ekle:

```markdown
## Dev server
Dev server DevPanel (systemd) ile yönetiliyor. `npm run dev` çalıştırma, portu kill etme.
- Yeniden başlat: `devpanel.py restart <proje>`
- Durum: `devpanel.py status`
- Loglar: `devpanel.py logs <proje> 200`
```

## Kaldırma

```bash
pkill -f "devpanel.py tray"
rm ~/.local/bin/devpanel.py ~/.local/share/applications/devpanel.desktop ~/.config/autostart/devpanel-tray.desktop
rm -r ~/.config/devpanel   # ayarlar
```

## Lisans

MIT
