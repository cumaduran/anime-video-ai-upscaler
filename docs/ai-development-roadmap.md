# AI motoru değerlendirmesi — Anime Video Upscaler & Encoder

Bu belge, 1.0.0 geliştirme sürümünün mevcut durumuna ve kullanıcının RTX 3070 Ti
8 GB GPU'suna göre hazırlanmış bir araştırma ve uygulama planıdır. Aşağıdaki yeni
motorlar henüz uygulamaya eklenmiş veya gerçek anime görüntüleriyle doğrulanmış değildir.

## Bugünkü durum

- Video2X tabanlı Real-ESRGAN, Real-CUGAN, Anime4K ve RIFE motorları bulunur.
  Real-ESRGAN'ın `realesr-animevideov3` modeli anime videoları için eğitilmiş küçük
  bir modeldir; mevcut uygulamada Real-ESRGAN kareleri tek tek işler. RIFE çözünürlük
  artırma değil ara kare üretimidir. Anime4K gölgelendirici tabanlı bir seçenektir.
- Karelerin bağımsız işlenmesi bazı görüntülerde ayrıntının kareler arasında
  değişmesine yol açabilir. Deneysel deflicker yalnız parlaklığı hedefler; çizgi
  titreşimini veya hatalı hareketi otomatik olarak çözmez.
- Kaynak koddan derlenen yerel motor, 8 bit RGB kareleri doğrudan son kodlayıcıya
  aktarır; tam uzunlukta AI ara dosyası kullanmaz. 10 bit çıktı kaybolan hassasiyeti
  geri getirmez. Eski motorlarda FFV1 veya PNG ara dosyalı uyumluluk yolu bulunur.
- Yeni motor NVIDIA RTX 3070 Ti üzerinde dört modelle ve 61. bölümün tamamıyla
  denendi; Vulkan kapanışındaki çökme bu testlerde görülmedi. AMD, diğer sürücü
  sürümleri ve farklı uzun kaynaklarda ek test gereklidir.

## Önerilen sıra

1. **Önce güvenilir temel.** Değiştirilmiş C++ motorunu derleyin; son kareleri,
   değişken kare zamanlarını, ses senkronunu, iptal/çökme davranışını ve 4K bellek
   kullanımını gerçek videolarla karşılaştırın. Mevcut dört motoru koruyun.
2. **Ölçüm seti oluşturun.** İzinli anime kesitlerinde sabit görüntü, hızlı hareket,
   ince çizgi, gren, flaş ve sahne kesmeleri bulunmalı. Ham girdiyi, mevcut motor
   çıktısını ve aday çıktıyı aynı ayarlarda karşılaştırın; hız, tepe VRAM, kare
   sayısı/ses eşleşmesi ve insan gözlemini kaydedin. PSNR/SSIM gibi ölçümler varsa
   yalnız başlarına anime görüntü kalitesini veya kare tutarlılığını kanıtlamaz.
3. **İsteğe bağlı zamansal motor prototipi.** RealBasicVSR/BasicVSR++ gibi ardışık
   karelerden yararlanan bir adayla başlayın; genel video modelleri anime için
   otomatik olarak daha iyi değildir. Sahne kesmelerinde geçmişi sıfırlayan,
   bellek sınırı olan parçalı işleme ve parça sınırı testleri gerekir. 8 GB VRAM'de
   hangi çözünürlük/hızın kullanılabildiği ancak ölçülerek belirlenebilir.
4. **Kendi modelimiz.** Önceden eğitilmiş bir modeli uygun lisanslı, temsil edici
   anime verisiyle ince ayarlamak sıfırdan eğitimden daha uygulanabilir bir ilk
   denemedir. Bu, başlangıç modelinin lisansını/atfını ortadan kaldırmaz. Gerçekten
   sıfırdan bir model için veri hazırlığı, eğitim, GPU süresi, değerlendirme ve
   model ağırlıklarının dağıtım koşulları ayrıca planlanmalıdır.
5. **Motor arayüzü ve dağıtım.** Mevcut ncnn motorları bozulmadan, ayrı bir
   model/backend tanımı ekleyin: model dosyası ve SHA-256, girdi/çıktı tensör biçimi,
   ölçek, renk dönüşümü, desteklenen donanım ve lisans açıkça doğrulanmalı. ONNX
   Runtime bir seçenek; Windows'ta WinML, NVIDIA'da CUDA gibi yürütme sağlayıcıları
   mevcut olabilir. CUDA/cuDNN ve model operatör uyumluluğu, paket boyutu ve çevrimdışı
   çalışma ayrıca test edilmelidir; her ONNX modeli otomatik uyumlu değildir.

Yeni bir motor, eski modellere göre daha iyi ya da titreşimsiz ilan edilmeden önce
ölçülmeli; kullanıcıya model seçimi ve karşılaştırma imkânı sunulmalıdır.

## Animeye özel ek adaylar

- [AnimeSR](https://github.com/TencentARC/AnimeSR), anime videolarına yönelik bir
  araştırma modelidir ve karşılaştırma adayıdır. Kod, ağırlık ve bağımlılıkların
  dağıtım koşulları ayrı incelenmeli; VRAM ve gerçek sahne başarısı ölçülmelidir.
- [APISR](https://github.com/Kiteretsu77/APISR), animeye özel başka bir araştırma
  adayıdır. Proje deposu akademik kullanım açıklaması içerir; ağırlıklar ve kullanım
  koşulları netleştirilmeden ürünle birlikte dağıtım için uygun kabul edilmemelidir.
- [Re-Anime600](https://openaccess.thecvf.com/content/CVPR2026W/AIGENS/html/Fargetta_Re-Anime600_A_Curated_Benchmark_Dataset_for_Anime_Video_Quality_Assessment_CVPRW_2026_paper.html)
  araştırması birkaç anime SR modelini birlikte değerlendirir. Tek bir sayısal
  puan veya veri kümesi, Naruto gibi farklı kaynaklarda kesin kazananı göstermez;
  kendi gerçek kesitlerimiz ve görsel değerlendirme hâlâ gereklidir.

## CMD2X / CMD4X veri hazırlığı

CMD2X/CMD4X henüz eğitilmiş bir model veya uygulamada kullanılabilen ağırlık değildir.
Kullanım ve eğitim hakkı olan çeşitli 1080p+ kaynaklarla deney hazırlamak için
[veri kümesi hazırlama rehberi](cmd-dataset-guide.md) ve `scripts/cmd_dataset_audit.py`
eklendi. Bu araç videoları yerel olarak okur; seri ayrımı, kullanıcı hak beyanı ve
isteğe bağlı birebir tekrar denetimi yapar. Görsel kaliteyi veya hakların doğruluğunu
otomatik onaylamaz. Eğitim ve mevcut modellerle adil karşılaştırma veri hazır olduktan
sonra yapılmalıdır; hiçbir modelin daha iyi veya titreşimsiz olduğu henüz gösterilmedi.

## Teknik kaynaklar

- [Real-ESRGAN anime video modeli](https://github.com/xinntao/Real-ESRGAN/blob/master/docs/anime_video_model.md)
- [RealBasicVSR araştırması](https://arxiv.org/abs/2111.12704) ve [referans kodu](https://github.com/ckkelvinchan/RealBasicVSR)
- [BasicVSR++ referans kodu](https://github.com/ckkelvinchan/BasicVSR_PlusPlus)
- [ONNX Runtime yürütme sağlayıcıları](https://onnxruntime.ai/docs/execution-providers/)
- [ONNX Runtime Windows / WinML](https://onnxruntime.ai/docs/get-started/with-windows.html)
- [ONNX Runtime CUDA bağımlılıkları](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)

Kodun ve indirilecek her ağırlık dosyasının lisansı ayrı ayrı kontrol edilmelidir;
referans verilmesi, bir modelin bu uygulamayla dağıtılmasına otomatik izin vermez.
