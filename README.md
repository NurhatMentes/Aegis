# Aegis V3 - Dynamic Position Engine 🛡️

Aegis V3 is an advanced, automated position tracking and management engine designed specifically for the OKX Cryptocurrency Exchange. 

It tracks active futures/swap positions via OKX's Private WebSocket, automatically applies trailing stop losses (ATR-based), calculates partial take-profits (Eşik 1 and Eşik 2 targets), and provides a fully visualized, real-time control panel built with Streamlit.

## Özellikler (Features)
- **Gerçek Zamanlı Takip (Real-time Tracking):** OKX Private WebSocket ile açık pozisyonların anlık takibi.
- **Dinamik Eşik (Dynamic Targets):** Pozisyona girildiği an hedef Kar Al (TP) yüzdesi üzerinden Eşik 1 ve Eşik 2 seviyelerinin belirlenmesi.
- **Dinamik Trailing Stop (İzleyen Stop):** ATR (Average True Range) verilerini kullanarak volatiliteye göre esnek izleyen stop mesafeleri.
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
