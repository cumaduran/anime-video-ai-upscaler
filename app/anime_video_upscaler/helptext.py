"""User-facing, reviewed explanations of every setting in English and Turkish."""

HELP = {
    "subtitle_language": ("Language tag for the selectable external subtitle track. Burned subtitles have no language tag.", "Seçilebilir harici altyazı izinin dil etiketidir. Görüntüye işlenen altyazının dil etiketi olmaz."),
    "engine": (
        "Choose the processor. Real-ESRGAN and Real-CUGAN enlarge with AI; Anime4K uses a shader; RIFE increases frame rate, not detail. Resize / encode only skips AI. Start with Real-ESRGAN for anime.",
        "İşlemciyi seçer. Real-ESRGAN ve Real-CUGAN yapay zekâ ile büyütür; Anime4K gölgelendirici kullanır; RIFE ayrıntıyı değil kare hızını artırır. Yalnız boyutlandır/kodla AI kullanmaz. Anime için önce Real-ESRGAN deneyin."),
    "model": (
        "Real-ESRGAN model. realesr-animevideov3 supports 2×/3×/4× and is a good anime starting point. The other models require 4×; compare a short clip because line detail and texture can differ.",
        "Real-ESRGAN modelidir. realesr-animevideov3 2×/3×/4× destekler ve anime için iyi bir başlangıçtır. Diğer modeller 4× gerektirir; çizgi ve doku sonuçları değişebileceğinden kısa bir kesitte karşılaştırın."),
    "scale": (
        "AI enlargement factor: 2×, 3× or 4×. It is separate from the final output size. For 1080p to 4K, 2× is a natural starting point; 4× uses much more GPU memory and temporary disk space. Models may restrict the factor.",
        "AI büyütme oranı: 2×, 3× veya 4×. Son çıktı çözünürlüğünden ayrıdır. 1080p'den 4K'ya 2× iyi bir başlangıçtır; 4× çok daha fazla GPU belleği ve geçici disk alanı kullanır. Model seçimi oranı sınırlayabilir."),
    "gpu": (
        "Vulkan GPU index used by the AI engine. 0 is the first device, not a guarantee of the fastest device. Run avue-engine.exe --list-devices to see the available indices. The NVIDIA video encoder is selected separately.",
        "AI motorunun kullanacağı Vulkan GPU numarasıdır. 0 ilk aygıttır; en hızlı aygıt olduğu garanti değildir. Numaraları görmek için avue-engine.exe --list-devices kullanın. NVIDIA video kodlayıcı ayrıca seçilir."),
    "noise": (
        "Real-CUGAN denoise strength: -1 conservative, 0 no denoise, 1–3 progressively stronger where the selected model supports them. Higher values can erase grain and fine lines. Start at 0. Real-ESRGAN general-v3 uses a denoising variant when positive.",
        "Real-CUGAN gürültü giderme düzeyi: -1 korumacı, 0 gürültü giderme yok, 1–3 modelin desteklediği ölçüde giderek daha güçlüdür. Yüksek değer gren ve ince çizgileri silebilir. 0 ile başlayın. Real-ESRGAN general-v3 pozitif değerde gürültü azaltan varyantı kullanır."),
    "cugan_model": (
        "Real-CUGAN model family. models-se is the balanced default, models-pro has stricter scale/noise combinations, and models-nose requires 2× and noise 0. Compare results on a representative clip.",
        "Real-CUGAN model ailesidir. models-se dengeli varsayılandır, models-pro ölçek/gürültü birleşimlerini daha fazla sınırlar, models-nose ise 2× ölçek ve 0 gürültü düzeyi ister. Örnek bir kesitte sonuçları karşılaştırın."),
    "cugan_threads": (
        "Real-CUGAN worker threads, 1–256. Default 1. More threads can increase CPU and memory use and do not always make GPU processing faster; increase only after measuring.",
        "Real-CUGAN iş parçacığı sayısı, 1–256. Varsayılan 1'dir. Artırmak CPU/bellek kullanımını yükseltebilir ve GPU işlemini her zaman hızlandırmaz; ölçerek artırın."),
    "cugan_syncgap": (
        "Real-CUGAN synchronization mode: 0 no sync, 1 accurate, 2 rough, 3 very rough. Default 3. Mode 1 uses accurate tiled synchronization and can be slower; compare results on a representative clip. The bundled reference backend applies this setting; legacy engines may ignore it.",
        "Real-CUGAN eşzamanlama modu: 0 yok, 1 hassas, 2 kaba, 3 çok kaba. Varsayılan 3'tür. 1 modu döşemeler arasında hassas eşzamanlama kullanır ve daha yavaş olabilir; örnek görüntüde karşılaştırın. Paketli referans motor bu ayarı uygular; eski motorlar yok sayabilir."),
    "shader": (
        "Anime4K shader profile. A, B and C variants differ in reconstruction strength and cost; +a/+b combine passes, and GAN is heavier. Start with anime4k-v4-a and compare line detail, ringing and speed.",
        "Anime4K gölgelendirici profilidir. A, B ve C çeşitlerinin yeniden oluşturma gücü ve maliyeti farklıdır; +a/+b geçişleri birleştirir, GAN daha ağırdır. anime4k-v4-a ile başlayıp çizgileri, çınlamayı ve hızı karşılaştırın."),
    "rife_model": (
        "RIFE frame-interpolation model. Newer versions may need more GPU resources and can produce different artifacts. v4.26 is the starting choice if installed; compare difficult motion and scene changes.",
        "RIFE kare ara değeri modelidir. Yeni sürümler daha fazla GPU kaynağı isteyebilir ve farklı hatalar üretebilir. Kuruluysa v4.26 başlangıç seçeneğidir; hızlı hareketleri ve sahne değişimlerini karşılaştırın."),
    "fps_multiplier": (
        "RIFE output frame-rate multiplier, 2–8. For example 24 fps × 2 ≈ 48 fps. Larger values cost more time and can introduce motion artifacts; they do not increase spatial resolution.",
        "RIFE çıktı kare hızı çarpanı, 2–8. Örneğin 24 fps × 2 ≈ 48 fps. Yüksek değerler daha uzun sürer ve hareket hataları oluşturabilir; görüntü çözünürlüğünü artırmaz."),
    "scene_threshold": (
        "RIFE scene-change threshold, 0–100%. Lower values treat more changes as cuts and skip interpolation across them; higher values interpolate more aggressively. Start around 20 and check cuts and flashes.",
        "RIFE sahne değişimi eşiği, %0–100. Düşük değer daha çok değişimi kesme sayıp aralarında kare üretmez; yüksek değer daha fazla ara kare üretir. Yaklaşık 20 ile başlayıp kesmeleri ve parlamaları kontrol edin."),
    "rife_uhd": (
        "RIFE UHD mode for large input frames. It changes the interpolation processing strategy and resource use, not the requested final resolution. Leave off unless UHD input needs it and test quality.",
        "Büyük giriş kareleri için RIFE UHD modudur. Son çözünürlüğü değil ara kare işlem yöntemini ve kaynak kullanımını değiştirir. UHD girişte gerekmedikçe kapalı tutun ve kaliteyi kontrol edin."),
    "hwaccel": (
        "Hardware decoding mode for the Video2X stage. none uses software decoding; cuda, d3d11va, dxva2, vulkan and qsv depend on the installed build and hardware. Start with none; it does not disable GPU AI or NVENC.",
        "Video2X aşamasının donanımla çözme yöntemi. none yazılımla çözer; cuda, d3d11va, dxva2, vulkan ve qsv kurulu sürüme ve donanıma bağlıdır. none ile başlayın; GPU AI veya NVENC'i kapatmaz."),
    "recover_shutdown": (
        "Off by default. For the observed old Video2X shutdown crash only: after a reported success and matching frame count, decode and verify the full intermediate before continuing. Completion remains a warning; visual correctness is not guaranteed.",
        "Varsayılan kapalıdır. Yalnız görülen eski Video2X kapanış hatasında, başarı bildirimi ve kare sayısı eşleşirse devam etmeden önce ara videonun tamamını çözüp doğrular. Sonuç uyarılı kalır; görüntü doğruluğu garanti değildir."),
    "resume_segments": (
        "Save verified one-minute AI segments so a retry can reuse completed work. Supports Real-ESRGAN, Real-CUGAN with the bundled engine, Anime4K and RIFE; denoise must be off. Requires substantial extra free space. The current segment and final encoding restart after interruption.",
        "Doğrulanmış bir dakikalık AI parçalarını kaydeder; yeniden denemede tamamlanan parçalar kullanılır. Real-ESRGAN, paketli motorla Real-CUGAN, Anime4K ve RIFE desteklenir; gürültü giderme kapalı olmalıdır. Çok fazla ek boş alan gerektirir. Kesilen parça ve son kodlama yeniden başlar."),
    "target": (
        "Final canvas: 4K UHD = 3840×2160, QHD = 2560×1440, AI output size keeps the processed size, Custom uses your width/height. The AI scale remains a separate setting.",
        "Son çıktı alanı: 4K UHD = 3840×2160, QHD = 2560×1440, AI çıktı boyutu işlenmiş boyutu korur; Özel genişlik/yüksekliği kullanır. AI büyütme oranı ayrı bir ayardır."),
    "width": (
        "Custom output canvas width in pixels; used only when Custom size is selected. Use an even number from 2 to 16384. Typical examples: 1920, 2560, 3840. Aspect ratio is controlled separately.",
        "Özel çıktı alanının piksel cinsinden genişliği; yalnız Özel boyutta kullanılır. 2–16384 arası çift sayı girin. Örnekler: 1920, 2560, 3840. Görüntü oranı ayrıca seçilir."),
    "height": (
        "Custom output canvas height in pixels; used only when Custom size is selected. Use an even number from 2 to 16384. Typical examples: 1080, 1440, 2160. Aspect ratio is controlled separately.",
        "Özel çıktı alanının piksel cinsinden yüksekliği; yalnız Özel boyutta kullanılır. 2–16384 arası çift sayı girin. Örnekler: 1080, 1440, 2160. Görüntü oranı ayrıca seçilir."),
    "aspect": (
        "Both choices preserve the image aspect ratio. Black bars pads to the exact requested canvas; Fit produces the largest even-sized image inside it without padding. Neither stretches or crops the picture.",
        "İki seçenek de görüntü oranını korur. Siyah bant tam hedef alana tamamlar; Sığdır hedefe sığan en büyük çift boyutlu görüntüyü bant eklemeden üretir. Hiçbiri görüntüyü esnetmez veya kırpmaz."),
    "codec": (
        "Final video encoder. libx264/libx265 use CPU; NVENC uses NVIDIA hardware; AMF uses AMD hardware. AV1 encoding requires a supported GPU and driver, and playback support varies. AI processing separately requires a Vulkan GPU.",
        "Son video kodlayıcıdır. libx264/libx265 CPU kullanır; NVENC NVIDIA, AMF AMD donanımı kullanır. AV1 kodlama uyumlu GPU ve sürücü ister; oynatıcı desteği değişir. AI işlemi ayrıca Vulkan GPU gerektirir."),
    "quality": (
        "Used when bitrate is 0. CPU x264/x265 use CRF, NVIDIA NVENC uses CQ, AMD AMF uses constant QP. Lower usually means higher quality and a larger file; for H.264/HEVC try 18–22. NVENC CQ 0 is automatic, not lossless. AV1 NVENC CQ permits 0–63; AV1 AMF QP permits 0–255. Values cannot be compared directly between encoders.",
        "Bit hızı 0 iken kullanılır. CPU x264/x265 CRF, NVIDIA NVENC CQ, AMD AMF sabit QP kullanır. Düşük değer genellikle daha yüksek kalite ve büyük dosya demektir; H.264/HEVC için 18–22 deneyin. NVENC CQ 0 otomatiktir, kayıpsız değildir. AV1 NVENC CQ 0–63; AV1 AMF QP 0–255 kabul eder. Farklı kodlayıcı değerleri doğrudan kıyaslanamaz."),
    "preset": (
        "Encoder speed/efficiency, not the saved system preset. CPU slow uses more time than fast; NVENC p1 is fastest and p7 slowest. AMD AMF offers speed, balanced, quality and high_quality; support depends on hardware and drivers.",
        "Kodlama hızı/verimidir; sistem ön ayarı değildir. CPU'da slow fast'ten yavaştır; NVENC p1 en hızlı, p7 en yavaştır. AMD AMF speed, balanced, quality ve high_quality sunar; destek donanım ve sürücüye bağlıdır."),
    "pixel_format": (
        "Pixel format and bit depth. yuv420p is widely compatible; yuv420p10le/p010le require a compatible encoder/player; 4:4:4 is less widely supported. AI intermediates use 8-bit RGB, so 10-bit output cannot restore lost precision.",
        "Piksel biçimi ve bit derinliğidir. yuv420p yaygın uyumludur; yuv420p10le/p010le uyumlu kodlayıcı/oynatıcı ister; 4:4:4 desteği daha sınırlıdır. AI ara dosyaları 8 bit RGB'dir; 10 bit çıktı kaybolan hassasiyeti geri getirmez."),
    "container": (
        "MKV supports many subtitle/audio formats and is a flexible anime default. MP4 has broad device support, but bitmap subtitles cannot be copied and soft text subtitles become mov_text, which can lose ASS styling.",
        "MKV birçok altyazı/ses biçimini destekler ve anime için esnek varsayılandır. MP4 geniş aygıt desteği sunar; ancak resim tabanlı altyazılar kopyalanamaz, seçilebilir metin altyazısı mov_text olur ve ASS stilleri kaybolabilir."),
    "audio": (
        "Copy keeps original audio without re-encoding if the container supports it. AAC re-encodes at the chosen bitrate (192 kb/s by default). Off removes audio. Copy may fail when the source codec is unsupported by the container.",
        "Kopyala, kapsayıcı destekliyorsa özgün sesi yeniden kodlamadan korur. AAC seçilen bit hızında (varsayılan 192 kb/sn) yeniden kodlar. Kapalı sesi kaldırır. Kaynak ses codec'i kapsayıcıyla uyumsuzsa kopyalama başarısız olabilir."),
    "audio_bitrate": (
        "AAC output bitrate in bits per second, from 32,000 to 512,000. 192,000 means 192 kb/s. Active only when Audio is AAC. Increasing bitrate cannot restore details already lost in the source audio.",
        "AAC ses çıkış bit hızı, bit/sn cinsinden 32.000–512.000. 192.000, 192 kb/sn demektir. Yalnız Ses AAC seçiliyken etkindir. Bit hızını artırmak kaynak seste kaybolmuş ayrıntıları geri getirmez."),
    "restoration": (
        "Optional mild temporal and spatial denoise. Applied before AI, or before final encoding when AI is off. It can soften grain and fine detail or blur motion; compare a short sample. Off preserves the original input.",
        "İsteğe bağlı hafif zamansal ve uzamsal gürültü azaltmadır. AI öncesinde, AI kapalıysa son kodlama öncesinde uygulanır. Greni ve ince ayrıntıları yumuşatabilir veya harekette bulanıklık oluşturabilir; kısa örneği karşılaştırın. Kapalı giriş görüntüsünü korur."),
    "copy_subtitles": (
        "Keep compatible subtitle tracks from the source. MKV can retain attachments with them; MP4 converts text subtitles and cannot copy bitmap subtitles. This is separate from an external subtitle file.",
        "Kaynağın uyumlu altyazı izlerini korur. MKV ilgili ekleri de tutabilir; MP4 metin altyazısını dönüştürür ve resim tabanlı altyazıyı kopyalayamaz. Harici altyazı dosyasından ayrıdır."),
    "subtitle_mode": (
        "Off adds no external subtitle. Burn draws it permanently into the video. Soft adds a selectable track; MP4 text conversion can discard ASS styling. The selected file is shared by all waiting jobs.",
        "Kapalı harici altyazı eklemez. Burn altyazıyı görüntüye kalıcı işler. Soft seçilebilir iz ekler; MP4 metin dönüşümü ASS stillerini kaybettirebilir. Seçilen dosya tüm bekleyen işlerde kullanılır."),
    "subtitle_path": (
        "Choose an external SRT, ASS, SSA or VTT file when Burn or Soft is selected. The same file is applied to each waiting job; process separate batches for different subtitles. Burned fonts depend on installed fonts.",
        "Burn veya Soft seçiliyken harici SRT, ASS, SSA ya da VTT dosyasını seçin. Aynı dosya bekleyen tüm işlere uygulanır; farklı altyazılar için ayrı gruplar işleyin. Burn yazı tipleri kurulu fontlara bağlıdır."),
    "watermark_path": (
        "Optional PNG watermark. Transparency is supported. Leave blank for none. It is placed on the final video after resizing and subtitle burn-in, using the position, size, margin and opacity below.",
        "İsteğe bağlı PNG filigrandır. Şeffaflık desteklenir; istemiyorsanız boş bırakın. Boyutlandırma ve burn altyazıdan sonra, aşağıdaki konum, boyut, kenar ve opaklıkla son görüntüye eklenir."),
    "watermark_corner": (
        "Choose top-left, top-right, bottom-left or bottom-right. The margin is measured from that corner. Check that the logo does not cover subtitles or important picture content.",
        "Sol üst, sağ üst, sol alt veya sağ alt köşeyi seçer. Kenar boşluğu bu köşeden ölçülür. Logonun altyazıyı veya önemli görüntüyü kapatmadığını kontrol edin."),
    "watermark_x": (
        "Horizontal position in free placement mode, from 0 at the left to 1 at the right. Drag the watermark on the preview to change it. The image stays inside the canvas.",
        "Serbest konumda yatay yerleşim: 0 en sol, 1 en sağdır. Önizlemede filigranı sürükleyerek değiştirebilirsiniz. Görsel çerçevenin içinde kalır."),
    "watermark_y": (
        "Vertical position in free placement mode, from 0 at the top to 1 at the bottom. Drag the watermark on the preview to change it. The image stays inside the canvas.",
        "Serbest konumda dikey yerleşim: 0 en üst, 1 en alttır. Önizlemede filigranı sürükleyerek değiştirebilirsiniz. Görsel çerçevenin içinde kalır."),
    "watermark_width": (
        "Watermark width in output pixels, 8–4096; height follows the PNG aspect ratio. It is capped to the fitted video width. Example: 160 pixels on a 3840-pixel-wide 4K canvas.",
        "Filigranın çıktı pikseli cinsinden genişliği, 8–4096; yükseklik PNG oranına göre ayarlanır. Sığdırılmış video genişliğini aşmaz. Örnek: 3840 piksel genişliğindeki 4K alanda 160 piksel."),
    "watermark_margin": (
        "Distance from the selected output corner in pixels, 0–2000. Default 24. Increase it to move the mark inward. A large margin or image can place it outside the visible picture.",
        "Seçilen çıktı köşesinden piksel cinsinden uzaklık, 0–2000. Varsayılan 24'tür. Logoyu içeri taşımak için artırın. Çok büyük kenar veya resim filigranı görünür alanın dışına taşıyabilir."),
    "watermark_opacity": (
        "Additional opacity multiplier, 0.01–1.00: 0.75 = 75%, 1.00 = the PNG's original opacity. It multiplies the PNG alpha, so already transparent pixels remain transparent.",
        "Ek opaklık çarpanı, 0,01–1,00: 0,75 = %75, 1,00 = PNG'nin özgün opaklığı. PNG alfa değeriyle çarpılır; zaten şeffaf pikseller şeffaf kalır."),
    "flicker": (
        "Off is recommended by default. Experimental 3-frame mean smooths overall luminance variation; it does not fix all line shimmer, warping or motion flicker and can alter flashes/cuts. Compare a short clip; no guarantee.",
        "Varsayılan olarak Kapalı önerilir. Deneysel 3 kare ortalaması genel parlaklık değişimini yumuşatır; çizgi titreşimi, bozulma ve hareket titreşiminin tamamını gidermez, parlamaları/kesmeleri değiştirebilir. Kısa kesitte karşılaştırın; garanti yoktur."),
    "bit_rate": (
        "Target video bitrate in bits/second. 0 uses CRF/CQ; a positive value switches to bitrate mode. 192,000 = 192 kb/s and 8,000,000 = 8 Mb/s. Increasing it cannot recover details already lost in the source; it can reduce further encoding loss at the cost of size.",
        "Hedef video bit hızı, bit/sn. 0 CRF/CQ kullanır; pozitif değer bit hızı moduna geçer. 192.000 = 192 kb/sn; 8.000.000 = 8 Mb/sn. Artırmak kaynakta kaybolan ayrıntıları geri getirmez; daha büyük dosya karşılığında yeniden kodlama kaybını azaltabilir."),
    "rc_buffer_size": (
        "Rate-control buffer size in bits. 0 leaves it to the encoder. Set this with a maximum bitrate for constrained delivery; a smaller buffer restricts short bitrate peaks more tightly. Follow the target platform's requirements.",
        "Hız kontrol tamponu, bit cinsinden. 0 kararı kodlayıcıya bırakır. Sınırlı bit hızı için en yüksek hızla birlikte ayarlayın; küçük tampon kısa süreli hız artışlarını daha sıkı sınırlar. Hedef platformun gereksinimlerine bakın."),
    "rc_min_rate": (
        "Minimum bitrate in bits/second; 0 leaves it unset. Used with rate-control constraints and codec-specific options; it must not exceed the maximum. Leave 0 for ordinary quality-based files.",
        "En düşük bit hızı, bit/sn; 0 ayarlanmaz. Hız kontrol sınırları ve codec seçenekleriyle kullanılır; en yüksek değeri aşamaz. Normal kalite tabanlı dosyalarda 0 bırakın."),
    "rc_max_rate": (
        "Maximum bitrate in bits/second; 0 leaves it unset. Combine with a suitable buffer when a device or service limits peaks. Example: 12,000,000 = 12 Mb/s; the appropriate limit depends on content.",
        "En yüksek bit hızı, bit/sn; 0 ayarlanmaz. Aygıt veya servis hız sınırı istiyorsa uygun tamponla kullanın. Örnek: 12.000.000 = 12 Mb/sn; uygun sınır içeriğe bağlıdır."),
    "qmin": (
        "Minimum quantizer; -1 leaves it to the encoder. Lower quantizer generally means higher quality and more bits. These bounds are encoder-dependent; setting them unnecessarily can interfere with CRF/CQ. Keep -1 unless needed.",
        "En düşük kuantizer; -1 kararı kodlayıcıya bırakır. Düşük kuantizer genelde daha yüksek kalite ve daha fazla bit demektir. Sınırlar codec'e bağlıdır; gereksiz kullanım CRF/CQ'yu etkileyebilir. Gerekmedikçe -1 bırakın."),
    "qmax": (
        "Maximum quantizer; -1 leaves it to the encoder. Higher quantizer generally means lower quality and fewer bits. It must be at least qmin when both are set. Bounds differ between encoders; keep -1 unless needed.",
        "En yüksek kuantizer; -1 kararı kodlayıcıya bırakır. Yüksek kuantizer genelde daha düşük kalite ve daha az bit demektir. İkisi de ayarlıysa qmin'den küçük olamaz. Sınırlar codec'e göre değişir; gerekmedikçe -1 bırakın."),
    "gop_size": (
        "Maximum group-of-pictures/keyframe interval in frames. -1 lets the encoder decide. Smaller values can improve seeking but increase size; larger values may compress better. Requirements vary by codec and delivery platform.",
        "Kare cinsinden en uzun GOP/anahtar kare aralığı. -1 kararı kodlayıcıya bırakır. Düşük değer aramayı kolaylaştırabilir ama boyutu artırabilir; yüksek değer daha iyi sıkıştırabilir. Gereksinimler codec ve platforma bağlıdır."),
    "max_b_frames": (
        "Maximum consecutive B-frames. -1 lets the encoder decide; 0 disables B-frames. They can improve compression but add decoding latency and may reduce compatibility. Supported limits depend on the encoder.",
        "Art arda en fazla B karesi. -1 kararı kodlayıcıya bırakır; 0 B karelerini kapatır. Sıkıştırmayı iyileştirebilirler ancak çözme gecikmesini artırıp uyumluluğu azaltabilirler. Sınırlar kodlayıcıya bağlıdır."),
    "keyint_min": (
        "Minimum interval between keyframes in frames. -1 leaves it to the encoder. The actual behavior is codec-dependent and scene cuts can affect placement. Leave automatic unless your delivery target specifies otherwise.",
        "Kare cinsinden en kısa anahtar kare aralığı. -1 kararı kodlayıcıya bırakır. Gerçek davranış codec'e bağlıdır ve sahne kesmeleri yerleşimi etkileyebilir. Hedefiniz istemedikçe otomatik bırakın."),
    "refs": (
        "Number of reference frames; -1 lets the encoder choose. More references may improve compression but increase memory use and can exceed playback device profile limits. Leave automatic unless tested.",
        "Referans kare sayısı; -1 kararı kodlayıcıya bırakır. Fazlası sıkıştırmayı iyileştirebilir ama belleği artırabilir ve oynatıcı profil sınırını aşabilir. Test etmedikçe otomatik bırakın."),
    "thread_count": (
        "Final encoder threads, 0–256. 0 is automatic. More threads can increase CPU and memory use; codec behavior varies. It does not control the GPU AI stage.",
        "Son kodlayıcının iş parçacığı sayısı, 0–256. 0 otomatiktir. Daha fazla iş parçacığı CPU ve bellek kullanımını artırabilir; davranış codec'e bağlıdır. GPU AI aşamasını kontrol etmez."),
    "extra_options": (
        "Optional codec-specific name=value entries, one per line, such as tune=animation. Only listed safe encoder options are accepted; shared quality, bitrate, timing and GOP controls cannot be overridden here. Invalid or unsupported options fail the job.",
        "İsteğe bağlı codec seçeneği, her satıra ad=değer; örneğin tune=animation. Yalnız izin verilen kodlayıcı seçenekleri kabul edilir; ortak kalite, bit hızı, zamanlama ve GOP ayarları buradan geçersiz kılınamaz. Hatalı veya desteklenmeyen seçenek işi durdurur."),
    "output_dir": (
        "Output folder. Leave blank to save beside each source. Existing files are never overwritten; a number is appended on a name collision. Temporary files are also created on the output drive and can be large.",
        "Çıktı klasörüdür. Her kaynağın yanına kaydetmek için boş bırakın. Var olan dosyalar üzerine yazılmaz; ad çakışırsa numara eklenir. Geçici dosyalar da çıktı sürücüsünde oluşturulur ve büyük olabilir."),
}


def english_help(key):
    return HELP[key][0]
