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
ENVIRONMENT=production
DEV_AUTH_BYPASS=false
ANTHROPIC_API_KEY=sk-ant-xxx
FIREBASE_PROJECT_ID=ditt-projekt-id
FIREBASE_STORAGE_BUCKET=ditt-projekt-id.appspot.com
FIREBASE_CREDENTIALS={"type":"service_account",...}  # hela JSON:en som sträng
```

### Viktigt (Async jobb / progress i UI)
Textbehandlaren använder `ask-async` + polling för att kunna visa progress (hämtar källor, skriver avsnitt, verifierar osv).

För Cloud Run kräver detta att **CPU allocation** är satt till **Always allocated**, annars kan bakgrundsjobbet stanna när HTTP-svaret redan är skickat.

Rekommenderat:
- Cloud Run → **Edit & Deploy New Revision** → **CPU allocation**: `Always allocated`
- `ENVIRONMENT=production` (krävs för att stänga dev-bypass och för att använda Firestore-baserad jobblagring)

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

## 4. Frontend

### Alternativ A (rekommenderat): Frontend via Cloud Run
Backend serverar redan `backend/app/static/index.html` på `/` och statiska filer under `/static`. Du kan alltså köra allt från samma Cloud Run‑service utan Vercel.

### Alternativ B: Frontend på Vercel
Om du vill lägga frontend separat behöver du antingen:
- Lägga en rewrite/proxy i Vercel så att `/api/*` skickas till Cloud Run, eller
- Ändra frontendkoden så att den använder en absolut API‑bas-URL (i stället för relativa `/api/...`).

---

## Kostnadsöversikt

| Tjänst | Gratis tier | Uppskattad kostnad |
|--------|-------------|-------------------|
| Firebase | Generös | $0 |
| Cloud Run | 2M req/mån | $0 |
| Vercel | 100GB | $0 |
| Anthropic | – | ~$10-20/mån |
| **Totalt** | | **~$10-20/mån** |
