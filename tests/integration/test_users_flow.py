"""
Test d'intégration E2E : chaîne complète UI -> Engine -> UsersJob.

Couvre la régression Lot 1 : les slots QML stockent fullName
en camelCase, mais UsersJob lit fullname en snake_case.
applySelectionsToContext() doit normaliser les clés avant de les transmettre à
l'Engine, sinon les données utilisateur sont perdues.

Ce test ÉCHOUE sur le code AVANT le fix Lot 1 et PASSE après.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# PySide6 est requis pour EngineBridge (QObject). Skip propre si absent.
pytest.importorskip("PySide6")

from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from xen.jobs.base import JobContext  # noqa: E402
from xen.jobs.users import UsersJob  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MINIMAL_CONFIG = PROJECT_ROOT / "config" / "examples" / "minimal.yaml"


@pytest.fixture(scope="session")
def qapp() -> object:
    """Fournit une QApplication unique pour toute la session de test."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def bridge(qapp: object) -> EngineBridge:
    """Construit un EngineBridge réel adossé à un Engine minimal.

    `qapp` garantit l'existence d'une QApplication avant l'instanciation du
    QObject ; la valeur n'est pas utilisée directement.
    """
    assert qapp is not None
    engine = Engine.from_config_file(MINIMAL_CONFIG)
    theme_base = MINIMAL_CONFIG.parent
    return EngineBridge(engine, theme_base, debug=False, dry_run=True)


class TestUiToEngineSelectionFlow:
    """Chaîne EngineBridge.set* -> applySelectionsToContext -> Engine."""

    def test_camel_case_keys_are_normalized(self, bridge: EngineBridge) -> None:
        """
        Régression Lot 1 : après applySelectionsToContext, l'Engine doit voir
        les clés snake_case attendues par UsersJob, avec les bonnes valeurs.
        """
        bridge.setUsername("john")
        bridge.setFullName("John Doe")
        bridge.setPassword("secret123")
        bridge.setHostname("mypc")

        bridge.applySelectionsToContext()

        selections = bridge._engine._selections

        # Les clés snake_case doivent exister avec les bonnes valeurs.
        assert selections.get("fullname") == "John Doe"

        # Les clés camelCase ne doivent PAS fuiter vers l'Engine.
        assert "fullName" not in selections

        # Les autres champs restent inchangés.
        assert selections.get("username") == "john"
        assert selections.get("password") == "secret123"
        assert selections.get("hostname") == "mypc"

    def test_root_password_keys_are_normalized(self, bridge: EngineBridge) -> None:
        """
        Lot v0.4.2 : les clés root saisies via les slots QML
        (setRootPassword/setRootSameAsUser) doivent être normalisées en
        snake_case (root_password/root_same_as_user) avant transmission à
        l'Engine, sans fuite des clés camelCase.
        """
        bridge.setRootPassword("rootpass1")
        bridge.setRootSameAsUser(False)

        bridge.applySelectionsToContext()

        selections = bridge._engine._selections
        assert selections.get("root_password") == "rootpass1"
        assert selections.get("root_same_as_user") is False
        assert "rootPassword" not in selections
        assert "rootSameAsUser" not in selections

    def test_root_same_as_user_defaults_true(self, bridge: EngineBridge) -> None:
        """
        Par défaut, rootSameAsUser vaut True et traverse la normalisation en
        root_same_as_user=True sans intervention de l'utilisateur.
        """
        bridge.applySelectionsToContext()

        selections = bridge._engine._selections
        assert selections.get("root_same_as_user") is True
        # La valeur par défaut du mot de passe root reste vide.
        assert selections.get("root_password") == ""

    def test_selections_property_keeps_camel_case(self, bridge: EngineBridge) -> None:
        """
        La Property QML `selections` (résumé d'install) doit conserver le
        camelCase : applySelectionsToContext ne doit pas muter self._selections.
        """
        bridge.setFullName("Jane Doe")

        bridge.applySelectionsToContext()

        summary = bridge.selections
        assert summary.get("fullName") == "Jane Doe"
        assert "fullname" not in summary

    def test_full_chain_to_users_job_context(self, bridge: EngineBridge) -> None:
        """
        Chaîne complète jusqu'au JobContext : un UsersJob recevant le contexte
        construit à partir des sélections normalisées valide sans erreur.
        """
        bridge.setUsername("alice")
        bridge.setFullName("Alice Liddell")
        bridge.setPassword("wonderland42")
        bridge.setHostname("rabbit-hole")

        bridge.applySelectionsToContext()

        context = JobContext(selections=bridge._engine._selections.copy())
        job = UsersJob()
        result = job.validate(context)

        assert result.success is True
        assert context.selections["fullname"] == "Alice Liddell"


class TestSelectionSourceOfTruth:
    """
    Régression v0.4.2 : les sélections ne persistaient pas d'une vue à l'autre.

    Cause racine (QML) : les vues utilisaient un miroir local réassigné
    impérativement (`text: root.x; onTextChanged: root.x = text`), ce qui
    cassait le binding descendant `x: engine.x` dès la première saisie. Les
    vues lisent désormais directement `engine.*` (miroirs readonly) et écrivent
    via `engine.setX(...)`, faisant de `_selections` l'unique source de vérité.

    Ces tests verrouillent le contrat côté bridge dont dépend le fix QML :
    chaque setter met à jour le getter notifié ET la Property `selections`
    (lue par SummaryView), et émet `selectionsChanged` (qui rafraîchit les
    bindings descendants des vues).
    """

    def test_getters_reflect_setters(self, bridge: EngineBridge) -> None:
        """Chaque setter de sélection se reflète dans son getter notifié."""
        bridge.setUsername("alice")
        bridge.setFullName("Alice Example")
        bridge.setHostname("alicebox")
        bridge.setSelectedDisk("sda")
        bridge.setPartitionMode("manual")
        bridge.setFilesystem("xfs")
        bridge.setSwapStrategy("none")
        bridge.setEncryption(True)

        assert bridge.username == "alice"
        assert bridge.fullName == "Alice Example"
        assert bridge.hostname == "alicebox"
        assert bridge.selectedDisk == "sda"
        assert bridge.partitionMode == "manual"
        assert bridge.filesystem == "xfs"
        assert bridge.swapStrategy == "none"
        assert bridge.encryption is True

    def test_selections_property_reflects_all_sets(self, bridge: EngineBridge) -> None:
        """
        La Property `selections` (lue par SummaryView) reflète toutes les
        sélections courantes : c'est la preuve que le résumé affiche l'état
        réellement saisi.
        """
        bridge.setSelectedLocale("fr_FR.UTF-8")  # auto-dérive le keymap 'fr'
        bridge.setSelectedTimezone("Europe/Paris")
        bridge.setUsername("bob")
        bridge.setHostname("bobbox")
        bridge.setSelectedDisk("nvme0n1")

        summary = bridge.selections
        assert summary["locale"] == "fr_FR.UTF-8"
        assert summary["timezone"] == "Europe/Paris"
        assert summary["keymap"] == "fr"  # dérivé du locale, visible au résumé
        assert summary["username"] == "bob"
        assert summary["hostname"] == "bobbox"
        assert summary["disk"] == "nvme0n1"

    def test_selection_set_emits_selectionsChanged(self, bridge: EngineBridge) -> None:
        """
        Chaque changement de sélection émet `selectionsChanged`. C'est le signal
        de notification qui, côté QML, rafraîchit les bindings descendants des
        vues (username: engine.username, etc.), donc la valeur re-apparaît au
        retour arrière et dans le résumé.
        """
        received: list[int] = []
        bridge.selectionsChanged.connect(lambda: received.append(1))

        bridge.setUsername("charlie")
        bridge.setSelectedDisk("sda")

        assert len(received) >= 2

    def test_backward_propagation_engine_to_getter(self, bridge: EngineBridge) -> None:
        """
        Preuve du sens descendant : après une 1re valeur, une mise à jour
        ultérieure côté engine reste visible via le getter (le fix QML garantit
        que la vue suit ce getter au lieu d'un miroir figé).
        """
        bridge.setUsername("first")
        assert bridge.username == "first"

        # Simule une mise à jour ultérieure (retour arrière / Edit-from-summary).
        bridge.setUsername("second")
        assert bridge.username == "second"
        assert bridge.selections["username"] == "second"

    def test_passwords_are_write_only_in_summary(self, bridge: EngineBridge) -> None:
        """
        Sécurité : les mots de passe (compte, root, LUKS) ne sont jamais
        re-bindés en descendant côté vue. Ils restent stockés côté engine mais
        ne doivent pas être exposés en clair dans le résumé QML : SummaryView
        n'affiche aucune de ces clés. On vérifie ici qu'elles ne servent qu'au
        contexte d'install, pas à un affichage descendant.
        """
        bridge.setPassword("usersecret1")
        bridge.setRootPassword("rootsecret1")
        bridge.setEncryptionPassphrase("lukssecret1")

        # Les valeurs sont bien mémorisées (source de vérité), mais SummaryView
        # (cf. SummaryView.qml) ne lit aucune de ces clés : elles n'apparaissent
        # jamais à l'écran. On documente ici l'intention write-only.
        summary = bridge.selections
        assert summary.get("password") == "usersecret1"
        assert summary.get("rootPassword") == "rootsecret1"
        assert summary.get("encryptionPassphrase") == "lukssecret1"

    def test_summaryview_qml_never_reads_password_keys(self) -> None:
        """
        Garde-fou sécurité automatisé : SummaryView.qml ne doit référencer
        aucune clé de mot de passe. Empêche une régression future qui
        exposerait un secret à l'écran via `selections.password`, etc.
        """
        summary_qml = (
            PROJECT_ROOT / "src" / "xen" / "gui" / "qml" / "components" / "SummaryView.qml"
        ).read_text(encoding="utf-8")
        for secret_key in ("password", "rootPassword", "encryptionPassphrase"):
            assert f"selections.{secret_key}" not in summary_qml, (
                f"SummaryView expose le secret '{secret_key}'"
            )

    def test_users_qml_passwords_not_bound_downward(self) -> None:
        """
        Garde-fou sécurité : les champs mot de passe de UsersView ne doivent
        pas avoir de binding descendant `text: engine.password` (write-only).
        """
        users_qml = (
            PROJECT_ROOT / "src" / "xen" / "gui" / "qml" / "components" / "UsersView.qml"
        ).read_text(encoding="utf-8")
        # On ignore les lignes de commentaire (//) : seuls les vrais bindings
        # QML comptent. Un binding descendant sur un secret est une régression.
        code_lines = [
            line.strip() for line in users_qml.splitlines() if not line.strip().startswith("//")
        ]
        for forbidden in ("text: engine.password", "text: engine.rootPassword"):
            assert not any(forbidden in line for line in code_lines), (
                f"UsersView binde un secret en descendant : '{forbidden}'"
            )


class TestUsersJobRunGecos:
    """UsersJob.run() doit transmettre le GECOS à useradd."""

    @patch("xen.jobs.users.subprocess.run")
    @patch("xen.jobs.users.Path")
    def test_run_passes_gecos_to_useradd(
        self, mock_path: MagicMock, mock_subprocess: MagicMock
    ) -> None:
        """run() avec subprocess mocké appelle useradd avec -c "Nom Complet"."""
        # /etc/group existe et contient wheel (chemin admin).
        mock_group_file = MagicMock()
        mock_group_file.exists.return_value = True
        mock_group_file.read_text.return_value = "wheel:x:998:\n"
        mock_path.return_value.__truediv__.return_value = mock_group_file

        mock_subprocess.return_value = MagicMock(returncode=0)

        job = UsersJob()
        context = JobContext(
            target_root="/mnt",
            selections={
                "username": "john",
                "password": "secret123",
                "fullname": "John Doe",
                # pas de hostname pour limiter les appels système.
            },
        )

        result = job.run(context)
        assert result.success is True

        # Retrouver l'appel useradd parmi les invocations subprocess.run.
        useradd_calls = [
            call_args
            for call_args in mock_subprocess.call_args_list
            if "useradd" in call_args[0][0]
        ]
        assert len(useradd_calls) == 1

        cmd = useradd_calls[0][0][0]
        assert "-c" in cmd
        gecos_value = cmd[cmd.index("-c") + 1]
        assert gecos_value == "John Doe"
