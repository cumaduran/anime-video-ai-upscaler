# CMD2X / CMD4X — veri kümesi hazırlama rehberi

CMD2X ve CMD4X şu anda **eğitilmiş bir model değil**; önerilen araştırma adlarıdır.
Veri, eğitim, karşılaştırma ve ağırlıkların lisans/doğrulama adımları tamamlanmadan
uygulamada seçenek veya kalite garantisi olarak sunulmazlar.

## Kaynak videolar

- Eğitim ve değerlendirme için kullanma hakkına sahip olduğun anime görüntüleri
  kullan. Kendi oluşturduğun animasyonlar, açıkça eğitim izni verilmiş kaynaklar
  veya yazılı izin alınmış görüntüler uygun olabilir. Bir videoya erişebilmek veya
  onu satın almak tek başına eğitim izni verildiğini kanıtlamaz.
- Tercihen 1080p veya üstü, mümkünse yüksek kaliteli asıl dosyalar hazırla.
  Kayıplı, 480p veya sıkıştırma izi belirgin video da araştırma için yararlı olabilir,
  ancak otomatik olarak temiz yüksek çözünürlüklü hedef sayılmaz.
- Filigran, videoya işlenmiş altyazı, kanal logosu ve ciddi sıkıştırma izleri
  bulunmayan sahneleri tercih et. Bunları modelin öğrenmemesi için gözle kontrol et.
- Yakın plan yüzler, ince çizgiler, düz renk ve degrade alanlar, hızlı hareket,
  koyu sahneler, doku/gren, kamera kaydırması, sahne kesmesi ve flaşlardan örnekler
  ayır. Farklı çizim tarzları ve eski/yeni yapımlar genelleme için yararlıdır.
- İmkân varsa farklı kaynaklardan en az üç bağımsız seriyle küçük bir pilot başlat;
  tek bir kesitten genel üstünlük çıkarılamaz. Daha geniş ve çeşitli veri hedefle;
  gerekli saat ve GPU süresi sonuçlara göre ölçülür.

## Klasör düzeni

```text
CMD-Dataset/
  provenance.csv
  train/
    seri_a/bolum_01.mkv
    seri_b/kesit_01.mp4
  val/
    seri_c/kesit_01.mkv
  test/
    seri_d/kesit_01.mkv
```

`train` eğitim, `val` ara seçim, `test` yalnız son değerlendirme içindir. Aynı
seriyi birden fazla gruba koyma; farklı adla yinelenmiş dosyalardan ve aynı
sahnenin başka kopyalarından da kaçın. Aynı dosya karma değeri denetimi aynen
kopyaları yakalar; yeniden kodlanmış veya kırpılmış aynı sahneyi yakalamaz.

`provenance.csv` UTF-8 dosyası, her seri için kullanıcının kendi beyanını içerir:

```csv
series,source,rights,training_permitted
seri_a,kaynak-adresi-veya-aciklamasi,izin-belgesi-veya-lisans,yes
seri_b,kaynak-adresi-veya-aciklamasi,izin-belgesi-veya-lisans,yes
seri_c,kaynak-adresi-veya-aciklamasi,izin-belgesi-veya-lisans,yes
seri_d,kaynak-adresi-veya-aciklamasi,izin-belgesi-veya-lisans,yes
```

Yukarıdaki satırlar şablondur, mevcut bir lisans veya izin iddiası değildir.
`training_permitted` alanı yalnız `yes`/`no` kabul eder. Araç, beyanın hukuken
doğru olup olmadığını veya görüntüde filigran/altyazı bulunup bulunmadığını belirlemez.

## Denetim

Proje kökünde, klasör yollarını kendi bilgisayarına göre değiştirerek:

```powershell
.\.venv-anime\Scripts\python.exe .\scripts\cmd_dataset_audit.py `
  --root 'D:\CMD-Dataset' `
  --ffprobe '.\runtime\ffmpeg\ffprobe.exe' `
  --output 'D:\cmd-dataset-audit.json' --hash
```

Araç videoları **yalnızca okur**, kaynak dosyaları yeniden kodlamaz veya taşımaz;
manifesti veri klasörü dışına yeni bir dosya olarak yazar ve mevcut çıktının üzerine
yazmaz. Üç ayrım, seri çakışması, kullanıcı beyanı, çözünürlük ve bazı zamanlama
uyarıları denetlenir. `--hash` büyük koleksiyonlarda zaman alabilir. Manifest dosya
adları ve hak beyanları içerebilir; paylaşmadan önce içeriğini gözden geçir.

## Eğitim ve karşılaştırma planı

İlk aday CMD2X için yüksek kaliteli hedef karelerden kontrollü 2× küçültülmüş
girişler, CMD4X için 4× küçültülmüş girişler üretilecek; sıkıştırma, yeniden
boyutlandırma ve gürültü bozulmaları gerçek kaynaklara uygun biçimde çeşitlendirilecek.
Sahne kesmelerinde durumu sıfırlayabilen kareler arası bir mimari ile daha hafif
tek-kare temeli ayrı adaylar olarak değerlendirilebilir. Mevcut model mimarisi veya
ağırlıkları kullanılacaksa her birinin kullanım ve dağıtım koşulu ayrıca incelenir.

Test, eğitimde görülmemiş sahnelerde orijinal ve aday çıktıları birlikte incelemeli:
çizgi ayrıntısı, doku kaybı, yanlış üretilmiş ayrıntı, kareler arası titreşim,
kayma/ghosting, kesmeler, hız, VRAM ve dosya boyutu. PSNR/SSIM gibi sayılar tek
başına son kararı vermez. Kullanıcının RTX 3070 Ti 8 GB GPU'sunda uygun kırpma,
batch ve hız denemeyle belirlenir; eğitim süresi veya üstünlük garantisi verilmez.
