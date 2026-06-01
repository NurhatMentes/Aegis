# Aegis V3 - Dynamic Position Engine 🛡️

Aegis V3 is an advanced, automated position tracking and management engine designed specifically for the OKX Cryptocurrency Exchange. 

It tracks active futures/swap positions via OKX's Private WebSocket, automatically applies trailing stop losses (ATR-based), calculates partial take-profits (Eşik 1 and Eşik 2 targets), and provides a fully visualized, real-time control panel built with Streamlit.

## Özellikler (Features)
- **Gerçek Zamanlı Takip (Real-time Tracking):** OKX Private WebSocket ile açık pozisyonların anlık takibi.
- **Dinamik Eşik (Dynamic Targets):** Pozisyona girildiği an hedef Kar Al (TP) yüzdesi üzerinden Eşik 1 ve Eşik 2 seviyelerinin belirlenmesi. Küçük TP oranları (%0.05 ve altı) için 100x ölçekleme düzeltmesi entegre edilmiştir.
- **OKX Native Trailing Stop (Borsa Tarafı İzleyen Stop):** Takipçi stoplar simüle edilmek yerine OKX borsasına doğrudan native `move_order_stop` emri olarak iletilir.
- **Dinamik ATR Spread ve Daraltma Kuralı:** İzleyen stop mesafesi volatilite (ATR) ve emir defteri dengesine (OB Imbalance) göre anlık ayarlanır. Takipçi stop aralığı sadece daraltılabilir (tightening-only) ve 15 saniyelik güncelleme cooldown'ına tabidir.
- **Balina Baskısı Kalkanı (Squeeze Wall Defense):** Tahtada ani ve devasa bir karşı yönlü baskı oluştuğunda, 15 saniyelik cooldown yok sayılarak takip mesafesi anında `0.4x ATR` seviyesine daraltılır ve kâr kilitlenir.
- **Hata Toleransı ve Lot Boyutu Yuvarlama:** Boş/null OKX API yanıtlarına karşı `safe_float` koruması ve kısmi çıkış sonrasında bakiye küsurat kalmasını önleyen lot hassasiyeti yuvarlama mekanizması.
- **Gelişmiş Kontrol Paneli (UI Dashboard):** Streamlit ile tasarlanmış, anlık PNL, bağlantı durumu ve aktif pozisyon hedeflerini (radar ve lineer barlarla) gösteren şık arayüz.
- **Canlı Ayar Değişikliği (Hot Reloading):** Motoru durdurmadan Eşik oranlarının UI üzerinden anında değiştirilebilmesi.


---

## Kurulum (Installation)

### Gereksinimler (Requirements)
- Python 3.9 veya üzeri
- OKX API Anahtarları (API Key, Secret Key, Passphrase) - *(Eğer sadece Simülasyon/Demo Trading yapacaksanız Demo API Anahtarları almalısınız)*

### 1. Projeyi Klonlayın
```bash
git clone <remote-repo-url>
cd Aegis
```

### 2. Sanal Ortam Oluşturun ve Aktifleştirin
```bash
python -m venv .venv
# Windows için:
.venv\Scripts\activate
# MacOS/Linux için:
source .venv/bin/activate
```

### 3. Gerekli Kütüphaneleri Yükleyin
```bash
pip install -r requirements.txt
```

### 4. API Bilgilerini Ayarlayın
Panel (UI) üzerinden ayarları yapmak en kolay yöntemdir ancak manuel olarak yapmak isterseniz `aegis` klasörü içine `.env` adlı bir dosya oluşturup aşağıdaki bilgileri doldurun:
```ini
OKX_API_KEY=your_api_key_here
OKX_SECRET_KEY=your_secret_key_here
OKX_PASSPHRASE=your_passphrase_here
OKX_IS_SIMULATED=True
```

---

## Kullanım (Usage)

Aegis sistemi iki farklı süreçten (process) oluşur: **Motor** ve **Arayüz**. İkisini de aktif olarak çalıştırmanız gerekir.

### 1. Aegis Motorunu Başlatma (Arka Plan Takipçisi)
Yeni bir terminal/komut istemcisi penceresi açın, sanal ortamı aktif edin ve motoru başlatın:
```bash
python aegis/main.py
```
*Motor, açık pozisyonları dinlemeye ve ATR hesaplamalarını yaparak yönetmeye başlayacaktır.*

### 2. Kontrol Panelini Başlatma (UI)
Başka bir terminal penceresi açın, sanal ortamı aktif edin ve Streamlit arayüzünü başlatın:
```bash
python -m streamlit run aegis/ui.py
```
*Otomatik olarak tarayıcınızda `http://localhost:8501` adresinde kontrol paneli açılacaktır.*

### 3. Panel Üzerinden Yapılabilecekler:
- **API Bilgileri:** Sol taraftaki menüden API bilgilerinizi güncelleyip anında test edebilirsiniz.
- **Motor Ayarları:** "Eşik 1 Kar Alma Oranı" nı (örneğin %50) motoru durdurmadan değiştirebilirsiniz. Motor bu değişikliği anında algılayacaktır.
- **Radar ve Metrikler:** Açık olan pozisyonlarınızın kâr/zarar durumunu ve Eşik 1 / Eşik 2 mesafelerini anlık olarak görebilirsiniz.

---

## 🧠 Aegis Algoritma Kılavuzu & Sistem Mantığı

Aegis, pozisyon riskini sıfırlamak ve kârı maksimize etmek için 5 temel kural üzerine inşa edilmiş kademeli bir çıkış stratejisi kullanır. Dış sistemler (örn. Skynet) pozisyona girdikten sonra Aegis bu kuralları katı bir şekilde uygular:

1. **Eşik 1'de Tam Temizlik (Emir İptalleri):**
   Eşik 1 seviyesi tetiklendiğinde (örneğin %30 kâr alımı yapılırken), mevcut tüm algo emirleri (eski SL/TP) ve Skynet'in önceden kurmuş olabileceği normal limit emirleri (bekleyen satış/alış emirleri) anında iptal edilir. Bu sayede emir defterinde çakışma yaşanmaz ve bakiye boşa çıkarılır.

2. **Smart Breakeven (Akıllı Başa Baş & %0.06 Kâr Kilidi):**
   Eşik 1 kâr alımı gerçekleştiğinde, Stop-Loss seviyesi risk-free (risksiz) hale getirilir:
   - Eğer Eşik 1'in getirdiği hedef kâr oranı **>%0.15** ise, Stop-Loss doğrudan pozisyon giriş seviyesinin **%0.06 kâr yönüne** (Long için %0.06 üstüne, Short için %0.06 altına) kilitlenir. Böylece komisyonlar karşılanır ve kesin küçük bir kâr garanti edilir.
   - Eğer Eşik 1 hedefi dar bir aralıktaysa (<= %0.15), Stop-Loss tam **giriş seviyesine (başa baş)** konur. Böylece piyasa gürültüsü nedeniyle SL'nin anında patlaması önlenir ve fiyata nefes alacak alan bırakılır.

3. **Sabit Eşik 2 Mesafesi:**
   Eşik 2 hedef seviyesi dinamik hesaplanmaz, Eşik 1 noktasından itibaren her zaman **sabit %0.10 daha kârlı yöndedir** (Örn: Long için Eşik 1 + %0.10). 

4. **Eşik 2'de Trailing Stop Geçişi:**
   Fiyat Eşik 2 seviyesine ulaştığında, Smart Breakeven aşamasında kurulan kilitli Stop-Loss emri iptal edilir ve tamamen dinamik **Trailing Stop (İzleyen Stop)** mekanizması devreye girer. Artık pozisyon kârı piyasa elverdiğince takip edilir.

5. **Minimum Trailing Gap (%0.06 Kuralı):**
   Devreye giren Trailing Stop mesafesi anlık piyasa volatilitesine (ATR) göre belirlenir. Ancak fiyat ne kadar hareketsiz olursa olsun (ATR çok düşse bile), takipçi stop mesafesi **hiçbir zaman fiyatın %0.06'sından daha dar olamaz**. Bu sayede ufak fiyat sıçramalarında haksız yere pozisyondan atılma engellenir.

