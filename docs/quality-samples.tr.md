# Görüntü kalitesi karşılaştırması

`python scripts/create-quality-samples.py` komutu, `build/quality-samples` altında
beş sentetik kısa video üretir: ince çizgiler, hareket, karanlık görüntü, düşük
bit hızı ve ani sahne geçişi. Var olan klasörün üzerine yazılmaz; farklı bir
konum için `--output` kullanılır. Dosyalar FFmpeg kaynaklarından üretilir, gerçek
anime veya telifli görüntü içermez.

Her videoda aynı zaman aralığını, çıktı boyutunu ve kodlayıcıyı kullanarak önce
yalnız boyutlandırmayı, sonra modelleri AI karşılaştırma ekranında deneyin.
Çizgi kırılması ve titreşimini, hareketli kenarları, karanlık bölgelerdeki
ayrıntıyı, sıkıştırma bloklarını ve sahne geçişlerini inceleyin. İşlem süresi ve
örnek dosya boyutunu kaydedin. Çıktıyı %100 piksel görünümünde ve oynatma
modunda kontrol edin; oynatma eşzamanlaması yaklaşıktır. Sentetik örnekler gerçek
anime kalitesinin ölçümü değildir; izinli gerçek sahnelerle tamamlayın.

Kısa örnekten hesaplanan toplam süre ve video boyutu yaklaşık değerlerdir; ses,
altyazı, filigran, içerik değişiklikleri ve işlem başlangıç maliyeti sonucu
değiştirir. Örnek sonucu bütün videonun kalitesini garanti etmez.
