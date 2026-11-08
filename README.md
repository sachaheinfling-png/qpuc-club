# QPUC Club

Application simple pour un club de quiz : le lecteur suit les scores sur son PC, et les joueurs buzzent depuis leur téléphone.

## Télécharger

- **Windows (exécutable)** : [télécharger QPUC_Club.zip](https://github.com/sachaheinfling-png/qpuc-club/releases/latest).
- **Code source** : les fichiers Python, les interfaces web et les tests sont disponibles dans ce dépôt.

## Lancer le projet

```powershell
uv sync                     # installe les dépendances
uv run qpuc.py              # lance le lecteur QPUC
uv run main.py              # idem, point d'entrée standard
uv run pytest               # lance les tests
```

## Démarrage à l'usage

- Ouvrez la fenêtre noire de QPUC et laissez-la ouverte.
- Le lecteur utilise l'URL locale de l'ordinateur : `http://localhost:8080/`.
- Les téléphones doivent être sur le même Wi‑Fi que le PC.
- Le QR code affiche l'URL du buzzer. Les joueurs appuient sur leur nom puis sur le gros bouton.
- Si le téléphone ne trouve pas le PC, activez le point d'accès mobile Windows ou vérifiez le Wi‑Fi.

## Structure

- `qpuc.py` : cœur du programme et serveur local
- `main.py` : point d'entrée simple pour les développeurs
- `web/` : interface du lecteur et des buzzers téléphones
- `tests/` : tests automatiques
- `docs/` : notes, schémas, comptes rendus
- `data/` : données locales et fichiers volumineux

## Journal

- 2026-10-27 : création du projet
- 2026-10-27 : correction des points d'entrée et sécurisation du chargement des joueurs