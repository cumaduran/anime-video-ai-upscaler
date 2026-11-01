# Uygulama incelemesi — 1.0.0 geliştirme önizlemesi

61. bölümdeki 0xC0000005 kapanış hatası, Vulkan kaynakları süreç kapanırken serbest
bırakılırken tekrar üretildi. Motorun CLI ve yerel kütüphanesi proje kaynağından
derlendi; Vulkan kaynakları süreç kapanışından önce serbest bırakılıyor. NVIDIA
RTX 3070 Ti üzerinde dört motor kısa testlerde normal kapandı. Test edilen
Real-ESRGAN kareleri eski motorla birebir aynıydı. Başka sürücü ve kartlarda
bağımsız doğrulama gereklidir.

61. bölüm, önceki 4× Real-ESRGAN ve HEVC ayarlarıyla tamamlandı: 35.688 kare,
24:48.487 süre, 2880×2160 (4:3 oran korunarak), AAC ses ve seçilebilir altyazı.
Başta, ortada ve sonda ses dalgası farkı yaklaşık 1 ms veya altındaydı. Yeni
akışta tam uzunlukta AI ara dosyası oluşmadı; ölçülen çalışma alanı en çok
754 MiB idi ve büyük kısmı son çıktıydı. Bu tek video tüm kaynakları veya ses
senkronu senaryolarını temsil etmez.

Gerçek hızda A/B, orijinal ses, bölünmüş görünüm, birlikte yakınlaştırma ve
%100 çıktı piksel ölçeği eklendi. Oynatma senkronu yaklaşık kontrol edilir;
farklı çözücüler veya ağır sistem yükü altında sonuç değişebilir.

## Kalan doğrulama ve geliştirme alanları

- AMD AMF/Vulkan, AV1 destekli NVIDIA/AMD, diğer sürücüler ve temiz Windows testi.
- Saatlerce süren ve farklı kare hızı/ses/altyazı düzenleri içeren test seti.
- HDR/10 bit, döndürülmüş ve değişken kare hızlı kaynakların güvenilir işlenmesi.
- Gelişmiş dosya/süre tahmini, kuyruk devamı ve dillerin ana dil incelemesi.
- Çizgi titremesini ölçen izinli değerlendirme verisi; deneysel filtre her
  sahnede titreme veya kaymayı gidermeyi garanti etmez.
- CMD2X/CMD4X halen araştırma adlarıdır; eğitilmiş veya kullanıma sunulmuş model yoktur.

Windows akışı sabitlenmiş bağımlılıkları doğrular ve yerel motoru kaynak koddan
derler. GPU dışı testleri geçen varsayılan dal push'u otomatik geliştirme ön
sürümü yayımlar. İş akışı henüz GitHub'da gerçek bir çalıştırmayla doğrulanmadı;
standart sunucuda gerçek GPU testi yapılmaz.
