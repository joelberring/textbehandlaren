# Textbehandlaren – Deployment Guide

## 1. Förberedelser

### Firebase
1. Gå till [Firebase Console](https://console.firebase.google.com)
2. Skapa projekt (eller använd befintligt)
3. Aktivera:
   - **Authentication** → Email/Password
   - **Firestore Database** → Skapa i test mode
   - **Storage** → Skapa bucket
4. Ladda ner service account:
   - ⚙️ Project Settings → Service Accounts → Generate new private key
   - Spara som `service-account.json`

### Anthropic
1. Skaffa API-nyckel på [console.anthropic.com](https://console.anthropic.com)

---

## 2. Konfigurera miljövariabler

I Google Cloud Console → Cloud Run → din service → **Edit & Deploy New Revision** → **Variables**:

```
ANTHROPIC_API_KEY=sk-ant-xxx
FIREBASE_PROJECT_ID=ditt-projekt-id
FIREBASE_STORAGE_BUCKET=ditt-projekt-id.appspot.com
FIREBASE_CREDENTIALS={"type":"service_account",...}  # hela JSON:en som sträng
```

---

## 3. Deploy

### Alternativ A: Manuell deploy
```bash
# Bygg och pusha image
gcloud builds submit --config cloudbuild.yaml

# Eller direkt:
gcloud run deploy textbehandlaren \
  --source . \
  --region europe-north1 \
  --allow-unauthenticated
```

### Alternativ B: Auto-deploy vid push
1. Gå till Cloud Build → Triggers
2. Skapa trigger kopplad till din GitHub-repo
3. Vid varje push till `main` deployas automatiskt

---

## 4. Frontend på Vercel

1. Skapa konto på [vercel.com](https://vercel.com)
2. Importera repo
3. Root directory: `backend/app/static`
4. Sätt env var `VITE_API_URL` till din Cloud Run-URL

---

## Kostnadsöversikt

| Tjänst | Gratis tier | Uppskattad kostnad |
|--------|-------------|-------------------|
| Firebase | Generös | $0 |
| Cloud Run | 2M req/mån | $0 |
| Vercel | 100GB | $0 |
| Anthropic | – | ~$10-20/mån |
| **Totalt** | | **~$10-20/mån** |
