#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QPUC Club — tableau des scores + buzzers sur téléphone.

Lancement :  python qpuc.py      (ou double-clic sur « Lancer QPUC.bat »)
Aucune bibliothèque à installer : uniquement Python 3.8+.

Le PC du lecteur ouvre automatiquement http://localhost:<port>/
Les joueurs scannent le QR code (même Wi-Fi) et arrivent sur leur buzzer.
"""
import asyncio
import base64
import copy
import csv
import datetime
import hashlib
import json
import os
import socket
import sys
import time
import uuid
import webbrowser

# ----------------------------------------------------------------- chemins
if getattr(sys, "frozen", False):          # version .exe (PyInstaller)
    DOSSIER = os.path.dirname(sys.executable)
    WEB = os.path.join(getattr(sys, "_MEIPASS", DOSSIER), "web")
else:
    DOSSIER = os.path.dirname(os.path.abspath(__file__))
    WEB = os.path.join(DOSSIER, "web")

FICHIER_JOUEURS = os.path.join(DOSSIER, "joueurs.json")
FICHIER_HISTO = os.path.join(DOSSIER, "historique.csv")
PORTS = [8080, 8081, 8000, 8888, 5000]

FENETRE_EQUITE = 0.06      # s : on attend 60 ms après le 1er buzz pour départager
                           # les appuis quasi simultanés selon l'heure réelle d'appui

NOMS_MANCHES = {
    "neuf": "9 points gagnants",
    "quatre": "4 à la suite",
    "barrage": "Départage",
    "face": "Face-à-face",
    "libre": "Partie libre",
}


def maintenant_ms():
    return time.perf_counter() * 1000.0


# ================================================================ WebSocket
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def trame(texte):
    data = texte.encode("utf-8")
    n = len(data)
    if n < 126:
        tete = bytes([0x81, n])
    elif n < 65536:
        tete = bytes([0x81, 126]) + n.to_bytes(2, "big")
    else:
        tete = bytes([0x81, 127]) + n.to_bytes(8, "big")
    return tete + data


async def lire_message(reader, writer):
    """Retourne le texte reçu, ou None si la connexion est fermée."""
    morceaux = []
    while True:
        b1, b2 = await reader.readexactly(2)
        fin, op = b1 & 0x80, b1 & 0x0F
        masque, n = b2 & 0x80, b2 & 0x7F
        if n == 126:
            n = int.from_bytes(await reader.readexactly(2), "big")
        elif n == 127:
            n = int.from_bytes(await reader.readexactly(8), "big")
        cle = await reader.readexactly(4) if masque else b""
        data = bytearray(await reader.readexactly(n))
        if masque:
            for i in range(n):
                data[i] ^= cle[i & 3]
        if op == 0x8:                       # fermeture
            return None
        if op == 0x9:                       # ping -> pong
            writer.write(bytes([0x8A, len(data)]) + bytes(data))
            continue
        if op == 0xA:
            continue
        morceaux.append(bytes(data))
        if fin:
            return b"".join(morceaux).decode("utf-8", "replace")


class Client:
    def __init__(self, writer, genre, local):
        self.writer = writer
        self.genre = genre          # "lecteur" ou "joueur"
        self.local = local
        self.pid = None             # joueur associé à ce téléphone
        self.vivant = True

    def envoyer(self, obj):
        if not self.vivant:
            return
        try:
            self.writer.write(trame(json.dumps(obj, ensure_ascii=False)))
        except Exception:
            self.vivant = False


# ================================================================ Le jeu
class Jeu:
    def __init__(self):
        self.clients = set()
        self.roster = []            # [{"id":..., "nom":...}]
        self.presents = []          # ids cochés « présent ce soir »
        self.config = {"cible_neuf": 9, "nb_qualifies": 3, "cible_face": 12,
                       "cible_libre": 0}
        self.mode = None
        self.manche = None
        self.dernier = None         # résultat de la dernière manche terminée
        self.pile = []              # pour « Annuler »
        self.candidats = []         # buzz en cours de départage
        self.minuteur = None
        self.url = ""
        self.adresses = []
        self.charger()

    # ------------------------------------------------ persistance
    def charger(self):
        try:
            with open(FICHIER_JOUEURS, encoding="utf-8") as f:
                d = json.load(f)
            self.roster = d.get("joueurs", [])
            self.presents = [p for p in d.get("presents", [])
                             if any(j["id"] == p for j in self.roster)]
            self.config.update(d.get("config", {}))
        except FileNotFoundError:
            pass
        except json.JSONDecodeError as e:
            backup = FICHIER_JOUEURS + ".corrompu" + datetime.datetime.now().strftime("-%Y%m%d-%H%M%S")
            try:
                os.replace(FICHIER_JOUEURS, backup)
            except OSError:
                backup = None
            print("Attention : joueurs.json est illisible ; le fichier original a été conservé. "
                  "Une liste vide a été chargée pour continuer proprement.")
            if backup:
                print(f"  Fichier de secours : {backup}")
            print(f"  Détail technique : {e}")
            self.roster = []
            self.presents = []
            self.config = {"cible_neuf": 9, "nb_qualifies": 3, "cible_face": 12,
                           "cible_libre": 0}
        except Exception as e:
            print("Attention : joueurs.json illisible :", e)

    def sauver(self):
        try:
            tmp = FICHIER_JOUEURS + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"joueurs": self.roster, "presents": self.presents,
                           "config": self.config}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, FICHIER_JOUEURS)
        except Exception as e:
            print("Impossible d'enregistrer les joueurs :", e)

    def nom(self, pid):
        for j in self.roster:
            if j["id"] == pid:
                return j["nom"]
        return "?"

    # ------------------------------------------------ aide aux règles
    def eligibles(self):
        """Joueurs qui ont encore le droit de buzzer sur la question en cours."""
        m = self.manche
        if not m or self.mode == "quatre" or m.get("fini"):
            return []
        q = m["q"]
        out = []
        for pid in m["joueurs"]:
            if pid in q["bloques"]:
                continue
            if self.mode in ("neuf", "barrage") and pid in m["qualifies"]:
                continue
            out.append(pid)
        return out

    def classement(self):
        m = self.manche
        if not m:
            return []
        if self.mode == "quatre":
            return sorted(m["joueurs"], key=lambda p: -m["meilleur"][p])
        if self.mode in ("neuf", "barrage"):
            reste = sorted([p for p in m["joueurs"] if p not in m["qualifies"]],
                           key=lambda p: -m["scores"][p])
            return list(m["qualifies"]) + reste
        return sorted(m["joueurs"], key=lambda p: -m["scores"][p])

    def prochaine_manche(self):
        m = self.manche
        if not m or not m.get("fini"):
            return None
        if self.mode == "neuf":
            return {"mode": "quatre", "joueurs": list(m["qualifies"])}
        if self.mode == "barrage":
            return {"mode": "face", "joueurs": list(m["qualifies"])}
        if self.mode == "quatre":
            classement = self.classement()
            if len(classement) < 2:
                return None
            m2 = m["meilleur"][classement[1]]
            directs = [p for p in classement if m["meilleur"][p] > m2]
            egaux = [p for p in classement if m["meilleur"][p] == m2]
            places = 2 - len(directs)
            if len(egaux) > places:
                return {
                    "mode": "barrage",
                    "joueurs": egaux,
                    "qualifies": directs,
                    "nb_qualifies": 2,
                }
            return {"mode": "face", "joueurs": (directs + egaux)[:2]}
        return None

    def memoriser(self):
        self.pile.append(copy.deepcopy((self.mode, self.manche)))
        del self.pile[:-300]

    def regler_valeur_automatique(self):
        m = self.manche
        if self.mode != "neuf":
            return
        qualifies = len(m["qualifies"])
        if qualifies >= 2:
            m["valeur"] = 3
        elif qualifies == 1:
            m["valeur"] = 2
        else:
            m["valeur"] = (m["q"]["num"] - 1) % 3 + 1

    # ------------------------------------------------ actions du lecteur
    def action(self, a):
        t = a.get("type")
        m = self.manche

        # ---- gestion des joueurs du club
        if t == "ajouter_joueur":
            nom = " ".join(str(a.get("nom", "")).split())[:30]
            if nom and not any(j["nom"].lower() == nom.lower() for j in self.roster):
                pid = uuid.uuid4().hex[:8]
                self.roster.append({"id": pid, "nom": nom})
                self.roster.sort(key=lambda j: j["nom"].lower())
                self.presents.append(pid)
                self.sauver()
        elif t == "renommer":
            nom = " ".join(str(a.get("nom", "")).split())[:30]
            for j in self.roster:
                if j["id"] == a.get("id") and nom:
                    j["nom"] = nom
            self.roster.sort(key=lambda j: j["nom"].lower())
            self.sauver()
        elif t == "supprimer_joueur":
            pid = a.get("id")
            if not (m and pid in m["joueurs"]):
                self.roster = [j for j in self.roster if j["id"] != pid]
                self.presents = [p for p in self.presents if p != pid]
                self.sauver()
        elif t == "present":
            pid = a.get("id")
            if a.get("oui") and pid not in self.presents:
                self.presents.append(pid)
            if not a.get("oui"):
                self.presents = [p for p in self.presents if p != pid]
            self.sauver()
        elif t == "tous_presents":
            self.presents = [j["id"] for j in self.roster] if a.get("oui") else []
            self.sauver()

        # ---- démarrer / terminer une manche
        elif t == "demarrer":
            mode = a.get("mode")
            ids = [p for p in a.get("joueurs", []) if any(j["id"] == p for j in self.roster)]
            if mode not in NOMS_MANCHES or not ids:
                return
            if self.manche and self.manche.get("fini"):
                self.enregistrer_historique()
                self.dernier = {"mode": self.mode, "classement": self.classement(),
                                "qualifies": list(self.manche.get("qualifies", []))}
            for k in ("cible_neuf", "nb_qualifies", "cible_face", "cible_libre"):
                if k in a:
                    try:
                        self.config[k] = max(0, int(a[k]))
                    except (TypeError, ValueError):
                        pass
            self.sauver()
            self.pile = []
            self.mode = mode
            self.manche = {
                "joueurs": ids,
                "scores": {p: 0 for p in ids},
                "valeur": 1,
                "q": {"etat": "fermee", "bloques": [], "main": None, "num": 0},
                "fini": False,
                "gagnant": None,
                "debut": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            if mode == "neuf":
                self.manche["cible"] = self.config["cible_neuf"] or 9
                self.manche["nb_qualifies"] = min(3, len(ids))
                self.manche["qualifies"] = []
            elif mode == "barrage":
                roster_ids = {j["id"] for j in self.roster}
                directs = list(dict.fromkeys(
                    p for p in a.get("qualifies", []) if p in roster_ids and p not in ids
                ))
                self.manche.update({
                    "cible": 2,
                    "nb_qualifies": min(2, len(ids) + len(directs)),
                    "qualifies": directs.copy(),
                    "directs": directs,
                })
            elif mode == "face":
                self.manche["cible"] = self.config["cible_face"] or 12
            elif mode == "libre":
                self.manche["cible"] = self.config["cible_libre"]
            elif mode == "quatre":
                self.manche.update({"tour": None, "serie": 0, "reponses": [],
                                    "meilleur": {p: 0 for p in ids}, "passes": []})
        elif t == "terminer":
            if m:
                self.enregistrer_historique()
                cl = self.classement()
                self.dernier = {"mode": self.mode, "classement": cl,
                                "qualifies": list(m.get("qualifies", []))}
            self.mode, self.manche, self.pile = None, None, []
            self.annuler_departage()

        elif t == "annuler":
            if self.pile:
                self.mode, self.manche = self.pile.pop()
                self.annuler_departage()

        elif not m:
            return

        # ---- pendant une manche avec buzzer
        elif t == "valeur":
            if self.mode != "barrage" and a.get("v") in (1, 2, 3):
                self.memoriser()
                m["valeur"] = a["v"]
        elif t == "ajuster":
            pid = a.get("id")
            if pid in m["scores"]:
                self.memoriser()
                cle = "meilleur" if self.mode == "quatre" else "scores"
                m[cle][pid] = max(0, m[cle][pid] + int(a.get("d", 0)))
                if self.mode != "quatre":
                    self.verifier_fin(pid)
        elif t == "ouvrir" and self.mode != "quatre" and not m["fini"]:
            self.memoriser()
            q = m["q"]
            q.update({"etat": "ouverte", "bloques": [], "main": None})
            q["num"] += 1
            self.regler_valeur_automatique()
            self.annuler_departage()
        elif t == "main_manuelle" and self.mode != "quatre":
            pid = a.get("id")
            if m["q"]["etat"] in ("ouverte", "fermee") and pid in self.eligibles():
                self.memoriser()
                if m["q"]["etat"] == "fermee":
                    m["q"]["num"] += 1
                    m["q"]["bloques"] = []
                    self.regler_valeur_automatique()
                self.donner_main(pid)
        elif t == "bonne" and self.mode != "quatre":
            q = m["q"]
            if q["etat"] == "buzz" and q["main"]:
                self.memoriser()
                pid = q["main"]
                if self.mode == "face":
                    q["etat"] = "points"
                    q["dernier"] = {"id": pid, "ok": True, "points_en_attente": True}
                else:
                    m["scores"][pid] += m["valeur"]
                    q["etat"] = "fermee"
                    q["dernier"] = {"id": pid, "ok": True}
                    self.verifier_fin(pid)
        elif t == "attribuer_points":
            q = m["q"]
            points = a.get("v")
            if (self.mode == "face" and q["etat"] == "points" and q["main"]
                    and type(points) is int and 1 <= points <= 4):
                self.memoriser()
                pid = q["main"]
                m["scores"][pid] += points
                q["etat"] = "fermee"
                q["dernier"] = {"id": pid, "ok": True, "points": points}
                self.verifier_fin(pid)
        elif t == "mauvaise" and self.mode != "quatre":
            q = m["q"]
            if q["etat"] == "buzz" and q["main"]:
                self.memoriser()
                q["bloques"].append(q["main"])
                q["dernier"] = {"id": q["main"], "ok": False}
                q["main"] = None
                q["etat"] = "ouverte" if self.eligibles() else "fermee"
        elif t == "personne" and self.mode != "quatre":
            if m["q"]["etat"] in ("ouverte", "buzz"):
                self.memoriser()
                m["q"].update({"etat": "fermee", "main": None})
                self.annuler_departage()

        # ---- 4 à la suite
        elif self.mode == "quatre":
            if t == "tour":
                pid = a.get("id")
                if pid in m["joueurs"] and not m["tour"]:
                    self.memoriser()
                    m.update({"tour": pid, "serie": 0, "reponses": []})
                    m["passes"] = [p for p in m["passes"] if p != pid]
            elif t in ("bonne", "mauvaise") and m["tour"]:
                self.memoriser()
                pid = m["tour"]
                if t == "bonne":
                    m["serie"] += 1
                    m["reponses"].append("b")
                    m["meilleur"][pid] = max(m["meilleur"][pid], m["serie"])
                    if m["serie"] >= 4:
                        self.fin_tour()
                else:
                    m["serie"] = 0
                    m["reponses"].append("m")
            elif t == "fin_tour" and m["tour"]:
                self.memoriser()
                self.fin_tour()

    def fin_tour(self):
        m = self.manche
        m["passes"].append(m["tour"])
        m["dernier_tour"] = {"id": m["tour"], "meilleur": m["meilleur"][m["tour"]]}
        m["tour"], m["serie"] = None, 0
        if all(p in m["passes"] for p in m["joueurs"]):
            m["fini"] = True

    def verifier_fin(self, pid):
        m = self.manche
        if self.mode in ("neuf", "barrage"):
            if m["scores"][pid] >= m["cible"] and pid not in m["qualifies"]:
                m["qualifies"].append(pid)
            if m["scores"][pid] < m["cible"] and pid in m["qualifies"]:
                m["qualifies"].remove(pid)       # correction manuelle
            m["fini"] = len(m["qualifies"]) >= m["nb_qualifies"]
        elif self.mode in ("face", "libre"):
            cible = m.get("cible") or 0
            gagnants = [p for p in m["joueurs"] if cible and m["scores"][p] >= cible]
            m["gagnant"] = gagnants[0] if gagnants else None
            m["fini"] = bool(gagnants)
        if m["fini"]:
            m["q"].update({"etat": "fermee", "main": None})

    # ------------------------------------------------ le buzz
    def buzz(self, client, t_tel):
        m = self.manche
        if not m or m["q"]["etat"] != "ouverte" or client.pid not in self.eligibles():
            return
        arrivee = maintenant_ms()
        t = arrivee
        if isinstance(t_tel, (int, float)) and arrivee - 1500 <= t_tel <= arrivee + 30:
            t = float(t_tel)          # heure réelle d'appui (horloge synchronisée)
        if any(c[0] == client.pid for c in self.candidats):
            return
        self.candidats.append((client.pid, t, arrivee))
        if self.minuteur is None:
            boucle = asyncio.get_running_loop()
            self.minuteur = boucle.call_later(FENETRE_EQUITE, self.departager)

    def departager(self):
        self.minuteur = None
        cands, self.candidats = self.candidats, []
        m = self.manche
        if not m or m["q"]["etat"] != "ouverte" or not cands:
            return
        cands = [c for c in cands if c[0] in self.eligibles()]
        if not cands:
            return
        cands.sort(key=lambda c: (c[1], c[2]))
        self.memoriser()
        ecarts = [round(c[1] - cands[0][1]) for c in cands[1:]]
        self.donner_main(cands[0][0], [(self.nom(c[0]), e) for c, e in zip(cands[1:], ecarts)])
        self.diffuser()

    def donner_main(self, pid, suivants=None):
        q = self.manche["q"]
        q["etat"], q["main"] = "buzz", pid
        q["suivants"] = suivants or []
        q["t_buzz"] = time.time()
        self.annuler_departage()

    def annuler_departage(self):
        if self.minuteur:
            self.minuteur.cancel()
        self.minuteur, self.candidats = None, []

    # ------------------------------------------------ historique
    def enregistrer_historique(self):
        m = self.manche
        if not m:
            return
        cl = self.classement()
        if not any(m["scores"].values()) and not any(m.get("meilleur", {}).values()):
            return
        neuf = not os.path.exists(FICHIER_HISTO)
        try:
            with open(FICHIER_HISTO, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                if neuf:
                    w.writerow(["Date", "Manche", "Rang", "Joueur", "Points", "Meilleure série"])
                for rang, pid in enumerate(cl, 1):
                    w.writerow([m["debut"], NOMS_MANCHES[self.mode], rang, self.nom(pid),
                                m["scores"].get(pid, 0) if self.mode != "quatre" else "",
                                m.get("meilleur", {}).get(pid, "") if self.mode == "quatre" else ""])
        except Exception as e:
            print("Impossible d'écrire l'historique :", e)

    # ------------------------------------------------ diffusion
    def etat_lecteur(self):
        connectes = sorted({c.pid for c in self.clients if c.genre == "joueur" and c.pid})
        return {
            "type": "etat",
            "roster": self.roster,
            "presents": self.presents,
            "config": self.config,
            "mode": self.mode,
            "nom_mode": NOMS_MANCHES.get(self.mode, ""),
            "manche": self.manche,
            "eligibles": self.eligibles(),
            "classement": self.classement(),
            "connectes": connectes,
            "dernier": self.dernier,
            "prochaine_manche": self.prochaine_manche(),
            "peut_annuler": bool(self.pile),
            "url": self.url,
            "adresses": self.adresses,
        }

    def etat_joueur(self, c):
        pid = c.pid
        base = {"type": "etat", "id": pid, "nom": self.nom(pid) if pid else None,
                "mode": self.mode, "nom_mode": NOMS_MANCHES.get(self.mode, ""),
                "liste": [j for j in self.roster if j["id"] in self.presents] or self.roster}
        m = self.manche
        if not pid or not any(j["id"] == pid for j in self.roster):
            base["id"] = None
            return base
        if not m:
            base.update(statut="attente", texte="En attente du lecteur…")
            return base
        if pid not in m["joueurs"]:
            base.update(statut="spectateur", texte="Vous ne jouez pas cette manche")
            return base
        score = m["meilleur"][pid] if self.mode == "quatre" else m["scores"][pid]
        base["score"] = score
        base["cible"] = m.get("cible")
        q = m["q"]
        if self.mode == "quatre":
            if m["tour"] == pid:
                base.update(statut="tour", texte="C'est votre tour !", serie=m["serie"])
            else:
                base.update(statut="attente", texte="4 à la suite : pas de buzzer")
        elif m["fini"]:
            base.update(statut="fini", texte="Manche terminée")
        elif self.mode in ("neuf", "barrage") and pid in m["qualifies"]:
            base.update(statut="qualifie", texte="Qualifié(e) !")
        elif q["etat"] == "buzz":
            if q["main"] == pid:
                base.update(statut="main", texte="À VOUS !")
            else:
                base.update(statut="autre", texte="Main à " + self.nom(q["main"]))
        elif pid in q["bloques"]:
            base.update(statut="bloque", texte="Vous avez déjà répondu")
        elif q["etat"] == "ouverte":
            base.update(statut="ouvert", texte="")
        else:
            base.update(statut="ferme", texte="Attendez la question")
        base["num"] = q["num"]
        return base

    def diffuser(self):
        el = None
        for c in list(self.clients):
            if not c.vivant:
                self.clients.discard(c)
                continue
            if c.genre == "lecteur":
                if el is None:
                    el = self.etat_lecteur()
                c.envoyer(el)
            else:
                c.envoyer(self.etat_joueur(c))


JEU = Jeu()


# ================================================================ HTTP
TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml",
         ".ico": "image/x-icon", ".mp4": "video/mp4"}
ROUTES = {"/": "lecteur.html", "/j": "joueur.html", "/joueur": "joueur.html"}


async def repondre_fichier(writer, chemin):
    nom = ROUTES.get(chemin, chemin.lstrip("/"))
    fichier = os.path.normpath(os.path.join(WEB, nom))
    if not fichier.startswith(os.path.normpath(WEB)) or not os.path.isfile(fichier):
        corps = "Page introuvable".encode()
        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain; charset=utf-8\r\n"
                     b"Content-Length: " + str(len(corps)).encode() + b"\r\nConnection: close\r\n\r\n" + corps)
    else:
        with open(fichier, "rb") as f:
            corps = f.read()
        typ = TYPES.get(os.path.splitext(fichier)[1], "application/octet-stream")
        writer.write(("HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
                      "Cache-Control: no-store\r\nConnection: close\r\n\r\n" % (typ, len(corps))).encode() + corps)
    await writer.drain()
    writer.close()


async def connexion(reader, writer):
    sock = writer.get_extra_info("socket")
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # pas de délai d'envoi
    except Exception:
        pass
    pair = writer.get_extra_info("peername") or ("?",)
    local = pair[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")
    try:
        ligne = (await reader.readline()).decode("latin-1").strip()
        entetes = {}
        while True:
            l = (await reader.readline()).decode("latin-1")
            if l in ("\r\n", "\n", ""):
                break
            k, _, v = l.partition(":")
            entetes[k.strip().lower()] = v.strip()
        parts = ligne.split()
        if len(parts) < 2:
            writer.close()
            return
        chemin = parts[1].split("?")[0]
    except Exception:
        writer.close()
        return

    if entetes.get("upgrade", "").lower() != "websocket":
        try:
            await repondre_fichier(writer, chemin)
        except Exception:
            pass
        return

    # ---- poignée de main WebSocket
    cle = entetes.get("sec-websocket-key", "")
    accept = base64.b64encode(hashlib.sha1((cle + GUID).encode()).digest()).decode()
    writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                  "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
    genre = "lecteur" if chemin == "/ws/lecteur" and local else "joueur"
    client = Client(writer, genre, local)
    JEU.clients.add(client)
    JEU.diffuser() if genre == "lecteur" else client.envoyer(JEU.etat_joueur(client))
    try:
        while True:
            texte = await lire_message(reader, writer)
            if texte is None:
                break
            try:
                msg = json.loads(texte)
            except ValueError:
                continue
            t = msg.get("type")
            if t == "ping":                         # synchronisation d'horloge
                client.envoyer({"type": "pong", "t0": msg.get("t0"), "ts": maintenant_ms()})
                continue
            if genre == "joueur":
                if t == "buzz":
                    JEU.buzz(client, msg.get("t"))
                    continue                        # diffusion faite par departager()
                if t == "choisir":
                    pid = msg.get("id")
                    client.pid = pid if any(j["id"] == pid for j in JEU.roster) else None
                elif t == "quitter":
                    client.pid = None
                JEU.diffuser()
            else:
                JEU.action(msg)
                JEU.diffuser()
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        pass
    finally:
        client.vivant = False
        JEU.clients.discard(client)
        try:
            writer.close()
        except Exception:
            pass
        JEU.diffuser()


# ================================================================ réseau
def adresses_locales():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))          # aucun paquet n'est envoyé
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    ips = [ip for ip in ips if not ip.startswith("127.")]
    privees = [ip for ip in ips if ip.startswith(("192.168.", "10.", "172."))]
    return privees + [ip for ip in ips if ip not in privees] or ["127.0.0.1"]


async def principal():
    serveur, port = None, None
    for p in PORTS:
        try:
            serveur = await asyncio.start_server(connexion, "0.0.0.0", p)
            port = p
            break
        except OSError:
            continue
    if not serveur:
        print("Aucun port disponible (8080, 8081…). Fermez l'autre programme et relancez.")
        return
    ips = adresses_locales()
    JEU.adresses = ["http://%s:%d/j" % (ip, port) for ip in ips]
    JEU.url = JEU.adresses[0] if JEU.adresses else "http://127.0.0.1:%d/j" % port
    print("=" * 60)
    print("  QPUC Club est lancé.")
    print("  Écran du lecteur :  http://localhost:%d/" % port)
    if ips:
        print("  Buzzers joueurs  :  " + JEU.url)
        for autre in JEU.adresses[1:]:
            print("                      " + autre)
    else:
        print("  Buzzers joueurs  :  http://127.0.0.1:%d/j (seulement le PC est visible)" % port)
    print("  Vérifiez que le PC et les téléphones sont sur le même Wi‑Fi.")
    print("  Si aucun téléphone ne trouve l'adresse, activez le point d'accès mobile de Windows.")
    print("  Laissez cette fenêtre ouverte pendant la partie.")
    print("=" * 60)
    if "--sans-navigateur" not in sys.argv:
        try:
            webbrowser.open("http://localhost:%d/" % port)
        except Exception:
            pass
    async with serveur:
        await serveur.serve_forever()


def main() -> None:
    try:
        asyncio.run(principal())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
