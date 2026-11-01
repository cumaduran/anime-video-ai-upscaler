# Proje yapısı

Anime Video Upscaler & Encoder, video kuyruğu, önizleme, karşılaştırma ve kodlama işlemlerini bir masaüstü uygulamasında toplar.

- `app/anime_video_upscaler/`: arayüz, iş kuyruğu, ayarlar ve işleme akışı.
- `engine/core/`: yerel görüntü işleme ve kodlama motoru.
- `engine/include/`: motorun C++ başlıkları.
- `engine/cli/`: uygulamanın çalıştırdığı yerel motorun komut satırı katmanı.
- `models/`: desteklenen işleme modelleri ve shader dosyaları.
- `runtime/`: geliştirme ortamındaki çalıştırma bağımlılıkları.
- `scripts/`: geliştirme, motor derleme ve paket hazırlama araçları.
- `tests/anime_video_upscaler/`: masaüstü uygulaması testleri.
- `docs/`: kullanım, geliştirme ve proje geçmişi belgeleri.
- `vendor/`: kaynak olarak dahil edilen AI kütüphaneleri.

Yerel motor Video2X kaynaklarını temel alır. Uyumluluk için bazı iç arayüzler ve çalışma zamanı dosyaları `video2x` adını taşır. Bu adların varlığı ve kaynakların kökeni için `NOTICE` ve `LICENSE` korunur. Harici kütüphane ve model lisansları dağıtım hazırlanırken ayrıca değerlendirilmelidir.

Geliştirme testlerini proje kökünde `.venv-anime\Scripts\python.exe -m pytest` ile çalıştırın. Motor ve bağımlılıkları hazırlama adımları `docs/anime-upscaler.md` içindedir; taşınabilir paket için `scripts/build-dev-portable.ps1` kullanılır.
