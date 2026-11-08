import importlib.util
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "qpuc_script", Path(__file__).resolve().parents[1] / "qpuc.py"
)
qpuc_script = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = qpuc_script
SPEC.loader.exec_module(qpuc_script)
Jeu = qpuc_script.Jeu


@pytest.fixture
def jeu(tmp_path, monkeypatch):
    monkeypatch.setattr(qpuc_script, "FICHIER_JOUEURS", str(tmp_path / "joueurs.json"))
    monkeypatch.setattr(qpuc_script, "FICHIER_HISTO", str(tmp_path / "historique.csv"))
    partie = Jeu()
    partie.roster = [
        {"id": "a", "nom": "Alice"},
        {"id": "b", "nom": "Benoit"},
        {"id": "c", "nom": "Claire"},
    ]
    partie.action({
        "type": "demarrer",
        "mode": "neuf",
        "joueurs": ["a", "b", "c"],
        "cible_neuf": 1,
        "nb_qualifies": 3,
    })
    return partie


def ouvrir_question(jeu):
    jeu.action({"type": "ouvrir"})


def donner_bonne_reponse(jeu, joueur):
    jeu.action({"type": "main_manuelle", "id": joueur})
    jeu.action({"type": "bonne"})


def test_valeur_automatique_cycle_1_2_3(jeu):
    valeurs = []

    for _ in range(6):
        ouvrir_question(jeu)
        valeurs.append(jeu.manche["valeur"])
        jeu.action({"type": "personne"})

    assert valeurs == [1, 2, 3, 1, 2, 3]


def test_qualification_change_la_valeur_automatique(jeu):
    ouvrir_question(jeu)
    donner_bonne_reponse(jeu, "a")
    assert jeu.manche["qualifies"] == ["a"]

    ouvrir_question(jeu)
    assert jeu.manche["valeur"] == 2
    donner_bonne_reponse(jeu, "b")
    assert jeu.manche["qualifies"] == ["a", "b"]

    ouvrir_question(jeu)
    assert jeu.manche["valeur"] == 3


def test_question_sans_reponse_compte_dans_le_cycle(jeu):
    ouvrir_question(jeu)
    jeu.action({"type": "personne"})

    assert jeu.manche["q"]["num"] == 1
    ouvrir_question(jeu)
    assert jeu.manche["q"]["num"] == 2
    assert jeu.manche["valeur"] == 2


def test_correction_manuelle_ne_change_que_la_question_courante(jeu):
    ouvrir_question(jeu)
    jeu.action({"type": "valeur", "v": 3})
    assert jeu.manche["valeur"] == 3

    jeu.action({"type": "personne"})
    ouvrir_question(jeu)
    assert jeu.manche["valeur"] == 2


def test_annuler_restaure_valeur_et_compteur_de_questions(jeu):
    ouvrir_question(jeu)
    jeu.action({"type": "personne"})
    ouvrir_question(jeu)
    jeu.action({"type": "valeur", "v": 3})

    jeu.action({"type": "annuler"})
    assert jeu.manche["q"]["num"] == 2
    assert jeu.manche["valeur"] == 2

    jeu.action({"type": "annuler"})
    assert jeu.manche["q"]["num"] == 1
    assert jeu.manche["valeur"] == 1


def test_neuf_qualifie_trois_joueurs(jeu):
    jeu.action({
        "type": "demarrer",
        "mode": "neuf",
        "joueurs": ["a", "b", "c"],
        "cible_neuf": 1,
        "nb_qualifies": 1,
    })

    assert jeu.manche["nb_qualifies"] == 3


def test_neuf_ne_qualifie_pas_plus_de_joueurs_que_presents(jeu):
    jeu.action({
        "type": "demarrer",
        "mode": "neuf",
        "joueurs": ["a", "b"],
        "cible_neuf": 1,
    })

    assert jeu.manche["nb_qualifies"] == 2


def test_fin_du_neuf_propose_les_qualifies_pour_quatre(jeu):
    jeu.manche["qualifies"] = ["a", "b", "c"]
    jeu.manche["fini"] = True

    assert jeu.prochaine_manche() == {"mode": "quatre", "joueurs": ["a", "b", "c"]}


def test_egalite_a_la_deuxieme_place_demande_un_barrage(jeu):
    jeu.action({"type": "demarrer", "mode": "quatre", "joueurs": ["a", "b", "c"]})
    jeu.manche["meilleur"].update({"a": 3, "b": 2, "c": 2})
    jeu.manche["fini"] = True

    assert jeu.prochaine_manche() == {
        "mode": "barrage",
        "joueurs": ["b", "c"],
        "qualifies": ["a"],
        "nb_qualifies": 2,
    }


def test_sans_egalite_les_deux_meilleurs_vont_au_face_a_face(jeu):
    jeu.action({"type": "demarrer", "mode": "quatre", "joueurs": ["a", "b", "c"]})
    jeu.manche["meilleur"].update({"a": 3, "b": 2, "c": 1})
    jeu.manche["fini"] = True

    assert jeu.prochaine_manche() == {"mode": "face", "joueurs": ["a", "b"]}


def test_trois_joueurs_ex_aequo_departagent_deux_places(jeu):
    jeu.action({"type": "demarrer", "mode": "quatre", "joueurs": ["a", "b", "c"]})
    jeu.manche["meilleur"].update({"a": 2, "b": 2, "c": 2})
    jeu.manche["fini"] = True

    assert jeu.prochaine_manche() == {
        "mode": "barrage",
        "joueurs": ["a", "b", "c"],
        "qualifies": [],
        "nb_qualifies": 2,
    }


def test_barrage_a_deux_points_qualifie_le_nombre_de_places_restantes(jeu):
    jeu.action({
        "type": "demarrer",
        "mode": "barrage",
        "joueurs": ["b", "c"],
        "qualifies": ["a"],
        "nb_qualifies": 2,
    })

    assert jeu.manche["cible"] == 2
    assert jeu.manche["valeur"] == 1
    for _ in range(2):
        ouvrir_question(jeu)
        donner_bonne_reponse(jeu, "b")

    assert jeu.manche["qualifies"] == ["a", "b"]
    assert jeu.manche["fini"]
    assert jeu.prochaine_manche() == {"mode": "face", "joueurs": ["a", "b"]}


@pytest.mark.parametrize("points", [1, 2, 3, 4])
def test_face_le_lecteur_choisit_les_points_apres_une_bonne_reponse(jeu, points):
    jeu.action({
        "type": "demarrer",
        "mode": "face",
        "joueurs": ["a", "b"],
        "cible_face": 12,
    })
    ouvrir_question(jeu)
    jeu.action({"type": "main_manuelle", "id": "a"})

    jeu.action({"type": "bonne"})

    assert jeu.manche["q"]["etat"] == "points"
    assert jeu.manche["scores"] == {"a": 0, "b": 0}
    jeu.action({"type": "attribuer_points", "v": points})

    assert jeu.manche["scores"] == {"a": points, "b": 0}
    assert jeu.manche["q"]["etat"] == "fermee"
    assert jeu.manche["q"]["dernier"]["points"] == points


def test_face_le_choix_des_points_peut_faire_gagner(jeu):
    jeu.action({
        "type": "demarrer",
        "mode": "face",
        "joueurs": ["a", "b"],
        "cible_face": 3,
    })
    ouvrir_question(jeu)
    jeu.action({"type": "main_manuelle", "id": "a"})
    jeu.action({"type": "bonne"})
    jeu.action({"type": "attribuer_points", "v": 3})

    assert jeu.manche["scores"]["a"] == 3
    assert jeu.manche["gagnant"] == "a"
    assert jeu.manche["fini"]


def test_face_refuse_un_choix_de_points_invalide(jeu):
    jeu.action({"type": "demarrer", "mode": "face", "joueurs": ["a", "b"]})
    ouvrir_question(jeu)
    jeu.action({"type": "main_manuelle", "id": "a"})
    jeu.action({"type": "bonne"})

    jeu.action({"type": "attribuer_points", "v": 5})

    assert jeu.manche["scores"] == {"a": 0, "b": 0}
    assert jeu.manche["q"]["etat"] == "points"


def test_annuler_un_choix_face_revient_au_choix_des_points(jeu):
    jeu.action({
        "type": "demarrer",
        "mode": "face",
        "joueurs": ["a", "b"],
        "cible_face": 3,
    })
    ouvrir_question(jeu)
    jeu.action({"type": "main_manuelle", "id": "a"})
    jeu.action({"type": "bonne"})
    jeu.action({"type": "attribuer_points", "v": 3})

    jeu.action({"type": "annuler"})

    assert jeu.manche["scores"] == {"a": 0, "b": 0}
    assert jeu.manche["q"]["etat"] == "points"
    assert not jeu.manche["fini"]


def test_charger_conserve_un_fichier_joueurs_invalide_en_copie_de_securite(tmp_path, monkeypatch):
    fichier = tmp_path / "joueurs.json"
    fichier.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(qpuc_script, "FICHIER_JOUEURS", str(fichier))

    partie = Jeu()

    assert partie.roster == []
    assert partie.presents == []
    assert list(tmp_path.glob("joueurs.json.corrompu-*"))