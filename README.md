# 📈 M15 Signal Bot – Ichimoku + MACD(5,35,5)

Bot wysyła sygnały tradingowe na Discord w czasie rzeczywistym (M15).  
Działa 24/7 na Railway.app.

## Aktywa
- Gold (XAU/USD)
- Cocoa (CC)
- Crude Oil WTI (CL)
- Brent Oil (BRENT)
- US100 / Nasdaq (NDX)

## Logika sygnału
1. **Trigger**: Przecięcie MACD (5, 35, 5)
2. **Filtr Ichimoku**: pozycja ceny względem chmury (Senkou A/B bez shift)
   - Bullish cross + cena **pod** chmurą → **LONG**
   - Bearish cross + cena **nad** chmurą → **SHORT**
   - Cena w chmurze → OBSERWUJ (brak sygnału)

## Setup na Railway.app

### 1. Utwórz repo na GitHubie z tymi plikami

### 2. Zaloguj się na [railway.app](https://railway.app)
- New Project → Deploy from GitHub repo
- Wybierz swoje repo

### 3. Dodaj zmienne środowiskowe (Variables)
```
TWELVEDATA_API_KEY=twój_klucz
DISCORD_WEBHOOK_URL=twój_webhook
```

### 4. Deploy
Railway automatycznie wykryje `requirements.txt` i uruchomi `python bot.py`.  
Bot skanuje rynek co 15 minut i wysyła sygnały natychmiast na Discord.

## Pliki
| Plik | Opis |
|------|------|
| `bot.py` | Główna logika bota |
| `requirements.txt` | Zależności Python |
| `railway.json` | Konfiguracja Railway |
| `Procfile` | Komenda startowa |
| `.gitignore` | Ignorowane pliki |
