# AEGIS Platform — Firebase Deploy Guide

## What you have
```
aegis-deploy/
├── firebase.json        ← hosting config, headers, CSP
├── .firebaserc          ← project: aegis-phantom-ops
└── public/
    └── index.html       ← the full wired Aegis platform page
```

---

## One-time setup (if not done already)

Install Firebase CLI globally:
```powershell
npm install -g firebase-tools
```

Login to Firebase:
```powershell
firebase login
```

---

## Deploy steps — run these from inside aegis-deploy/

```powershell
cd C:\Users\VernonDunbar\Documents\Aegis_Phantom\aegis-deploy

firebase deploy --only hosting
```

That's it. Firebase will output:
```
Hosting URL: https://aegis-phantom-ops.web.app
```

---

## What the firebase.json does

- **public: "public"** — serves everything in the /public folder
- **Security headers** on every route:
  - X-Frame-Options: SAMEORIGIN — prevents clickjacking
  - X-Content-Type-Options: nosniff
  - Cache-Control: no-cache — always fresh build
- **Content Security Policy** on HTML files:
  - Allows scripts from self + Google Fonts only
  - Allows connect to api.anthropic.com — required for the live AI engine
  - Blocks all other external connections
- **Rewrites** — all routes fall back to index.html (SPA behavior)

---

## After deploy

Your page will be live at:
```
https://aegis-phantom-ops.web.app
```

Test the AI engine — click any scenario card or use the What-If query box.
If the AI doesn't respond, check that the Anthropic API is accessible from
your browser (not blocked by a corporate proxy or browser extension).

---

## Future updates

Every time you update index.html, just run:
```powershell
firebase deploy --only hosting
```

Firebase deploys in under 30 seconds.

---

## Adding a custom domain (optional)

In Firebase Console → Hosting → Add custom domain
Point your DNS CNAME to the Firebase hosting target.
Firebase handles SSL automatically.

---

## Git — save your deploy config

```powershell
cd C:\Users\VernonDunbar\Documents\Aegis_Phantom
git add aegis-deploy/
git commit -m "AEGIS platform page - Firebase deploy config"
git push origin master
```
