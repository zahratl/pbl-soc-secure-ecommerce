# PBL SOC - Secure E-Commerce Toko Pakaian

Aplikasi e-commerce (Flask + MySQL) yang dipantau SOC berbasis Wazuh.

## Struktur repo
- `app/` : kode aplikasi Flask
- `docker/` : Dockerfile dan docker-compose
- `scripts/` : script backup dan restore
- `docs/` : dokumentasi dan evidence
- `.github/workflows/` : pipeline CI/CD (GitHub Actions)

## Alur branch
- `main` : stabil, dilindungi, hanya lewat pull request
- `dev` : integrasi kerja tim
- `feature/<nama>` : pekerjaan harian, digabung ke `dev` lewat PR

## Aturan
1. Jangan commit secret (`.env`, key, token). Pakai `.env.example` sebagai contoh.
2. Jangan tulis IP langsung di kode, gunakan variabel lingkungan.
3. Setiap PR minimal satu reviewer.
