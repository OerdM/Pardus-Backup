# PardusBackup
CLI ve GUI Tabanlı Pardus Linux işletim sistemi için yedek alma uygulaması.

rsync ve sabit bağ (hardlink) tabanlı artımlı yedekleme aracı. GTK3 arayüzü ve
komut satırı aracı içerir.

Değişmeyen dosyalar bir önceki yedeğe bağlandığı için her yedek eksiksiz bir
kopya gibi görünür, ancak diskte yalnızca değişen veri kadar yer kaplar.
Örneğin 17 MB'lık bir dizinde tek bir metin dosyası değiştiyse yeni yedek
diske yalnızca birkaç bayt ekler.

---

## Gereksinimler

### Çalıştırmak için

| Bileşen | Paket | Not |
|---|---|---|
| Python 3.9+ | `python3` | Pardus'ta kurulu gelir; çekirdek yalnızca standart kütüphaneyi kullanır |
| rsync | `rsync` | Yedekleme motoru, zorunlu |
| PyGObject | `python3-gi` | Yalnızca grafik arayüz için |
| GTK 3 tanıtımları | `gir1.2-gtk-3.0` | Yalnızca grafik arayüz için |

```bash
sudo apt install python3 rsync python3-gi gir1.2-gtk-3.0
```

`.deb` paketiyle kurulduğunda bu bağımlılıklar `apt` tarafından otomatik
çözülür; listeyi elle kurmak yalnızca kaynaktan çalıştırırken gerekir.

Komut satırı aracı yalnızca `python3` ve `rsync` ile çalışır; PyGObject ve GTK
kurulu değilse `pardusbackup gui` dışındaki tüm komutlar sorunsuz çalışmaya
devam eder.

### Geliştirme ve test için

| Bileşen | Paket |
|---|---|
| pytest | `python3-pytest` |

### `.deb` paketi derlemek için

```bash
sudo apt install debhelper dh-python pybuild-plugin-pyproject \
                 python3-all python3-setuptools devscripts
```

---

## Kurulum

### Hazır paketten

```bash
sudo apt install ./pardus-backup_0.1.0_all.deb
```

Bağımlılıklar otomatik çözülür. Kurulumdan sonra uygulama menüsünde
**Yedekleme** girdisi belirir; `pardusbackup` ve `pardusbackup-gui` komutları
`PATH` üzerinden kullanılabilir.

### Kaynaktan

```bash
git clone https://github.com/OerdM/PardusBackup.git
cd PardusBackup
pip install --user .
```

Kurulum yapmadan doğrudan çalıştırmak için:

```bash
python -m pardusbackup list -d ~/yedekler
```

---

## Kullanım

### Grafik arayüz

```bash
pardusbackup-gui
```

Yedeklenecek yolları **Klasör ekle** ve **Dosya ekle** düğmeleriyle listeye
ekleyip yedek konumunu seçtikten sonra **Yedek Al** düğmesine basmak yeterlidir.
Birden çok klasör ve tek tek dosyalar aynı yedekte birleştirilebilir; tüm ev
dizinini seçmek zorunda değilsiniz. Uygulama daha önce alınmış bir yedek varsa otomatik olarak artımlı
moda geçer; kullanıcının "tam mı artımlı mı" kararını vermesi gerekmez.

Listeden bir yedek seçildiğinde sağ panelde kaynağı, boyutu, hariç tutulan
desenleri ve diske eklediği veri miktarı görünür. Çöp kutusu simgesiyle seçili
yedek silinebilir.

### Komut satırı

```bash
# Yedek al (ilk seferde tam, sonrakilerde otomatik artımlı)
pardusbackup backup -s ~/Belgeler -d ~/yedekler

# Birden çok klasör ve tek tek dosyalar
pardusbackup backup -s ~/Belgeler -s ~/Resimler -s ~/notlar.txt -d ~/yedekler

# Yedek almadan önce denetle (disk alanı, dosya sistemi, erişim)
pardusbackup check -s ~/Belgeler -d ~/yedekler

# Yedekleri listele
pardusbackup list -d ~/yedekler
pardusbackup list -d ~/yedekler --fast     # disk taramasını atla

# Bir yedeği sil
pardusbackup delete ~/yedekler/2026-07-23_10-00-00
pardusbackup delete ~/yedekler/2026-07-23_10-00-00 --yes

# Grafik arayüzü başlat
pardusbackup gui
```

Kullanışlı seçenekler:

```bash
# Belirli desenleri hariç tut (birden çok kez verilebilir)
pardusbackup backup -s ~/ -d /yedek -e '*.tmp' -e '.cache/*'

# Tüm sistemi yedeklerken sanal dizinleri hariç tut
sudo pardusbackup backup -s / -d /yedek --system-excludes -x
```

`--system-excludes`, `/proc`, `/sys`, `/dev`, `/run`, `/tmp`, `/mnt`, `/media`
ve takas dosyalarını dışlar. `-x` (`--one-file-system`) rsync'in bağlama
noktalarını geçmesini engeller.

---

## Nasıl çalışır

Her yedek, hedef dizinde zaman damgalı ayrı bir klasördür. Yanında aynı adlı
bir `.json` dosyası bulunur:

```
yedekler/
├── 2026-07-23_10-00-00/         ilk yedek (tam)
├── 2026-07-23_10-00-00.json
├── 2026-07-23_18-30-00/         ikinci yedek (artımlı)
└── 2026-07-23_18-30-00.json
```

Metadata dosyası yalnızca rsync başarıyla tamamlandıktan sonra yazılır. Yarıda
kesilen bir yedeğin `.json` dosyası olmaz ve listelemede görünmez; böylece
eksik bir yedek yanlışlıkla sağlam sanılmaz.

İkinci yedek alınırken rsync `--link-dest` ile bir öncekini referans alır.
Değişmeyen dosyalar kopyalanmaz, aynı inode'a bağlanır. Bu yüzden arayüzde iki
ayrı boyut gösterilir:

- **İçerik boyutu** — yedeğin içerdiği verinin toplamı (`du` karşılığı)
- **Diske eklenen** — o yedeğin diske gerçekten yazdığı yeni veri

Bir yedeği silmek diğerlerini bozmaz: ortak dosyalar birden çok bağa sahip
olduğu için veri, son bağ da silinene kadar diskte kalır. Silinen yedeğe özel
olan bloklar boşa çıkar.

### Birden çok kaynak

Tek bir yol seçildiğinde snapshot dizini o yolun içeriğini birebir yansıtır.
Birden çok yol seçildiğinde her biri kendi adıyla ayrı bir girdi olur:

```
2026-07-23_10-00-00/
├── Belgeler/
├── Resimler/
└── notlar.txt
```

İki kaynağın adı aynıysa (`/veri/Belgeler` ve `/yedek/Belgeler`) tek girdide
birleşecekleri için yedekleme başlamadan reddedilir.

Tek kaynaktan çok kaynağa geçerken düzen değiştiği için o yedek tam kopya
olarak alınır; sonraki yedekler yeniden artımlı devam eder.

### Önemli kısıt

Yedek konumu ile kaynağın **aynı dosya sisteminde** olması gerekir. Sabit bağ
yalnızca tek bir dosya sistemi içinde kurulabilir; ayrı bir disk veya bölüm
seçilirse rsync sessizce tam kopyaya düşer ve artımlı kazanç kaybolur.
Uygulama bunu yedekten önce denetler ve farklı dosya sistemi tespit ederse
işlemi başlatmadan uyarır.

---

## Geliştirme

```bash
python -m pytest              # 66 test
python -m pytest -k hardlink  # tek bir konuyu çalıştır
```

Testler rsync kurulu değilse ilgili bölümleri atlar; saf mantık testleri her
durumda çalışır.

Proje yapısı:

```
pardusbackup/
├── config.py      yapılandırma ve yol yardımcıları
├── backend.py     rsync argümanları, denetimler, yedek alma
├── listing.py     yedekleri listeleme, boyut hesabı, silme
├── planning.py    kaynak/hedeften çalıştırılabilir yapılandırma üretme
├── __main__.py    komut satırı arayüzü
└── gui.py         GTK3 arayüzü
```

Arayüz katmanı çekirdek mantık içermez; yalnızca `backend`, `listing` ve
`planning` API'sini çağırır. rsync bilgisi, yol normalizasyonu ve metadata
biçimi çekirdekte kalır.

### `.deb` paketi derlemek

```bash
dpkg-buildpackage -us -uc -b
sudo apt install ../pardus-backup_0.1.0_all.deb
```

---

## Bilinen sınırlar

- Geri yükleme (restore) henüz arayüzde yok. Bir yedek normal bir dizin
  olduğundan dosyalar dosya yöneticisiyle veya `rsync -a` ile elle geri
  alınabilir.
- Yedek konumu ile kaynak aynı dosya sisteminde olmalıdır.
- Zamanlanmış otomatik yedekleme yoktur; `cron` veya `systemd` zamanlayıcısı
  ile `pardusbackup backup` çalıştırılabilir.

## Lisans

