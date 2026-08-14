"""Der Dienst muss sich nach einem Update selbst neu starten können.

Ohne Zugriff auf den Pi ist das die einzige Stelle, an der neuer Code aktiv
wird. Scheitert sie still, liegt das Update auf der Platte und der Dienst
läuft weiter mit dem alten Stand.
"""

from types import SimpleNamespace

from api import update


def _lauf(rueckgabecodes, protokoll):
    """Fake für subprocess.run: liefert je Aufruf den nächsten Code."""
    aufrufe = []

    def runner(cmd, **kwargs):
        aufrufe.append(cmd)
        code = rueckgabecodes[len(aufrufe) - 1]
        if isinstance(code, Exception):
            raise code
        return SimpleNamespace(returncode=code, stdout="", stderr="nicht erlaubt")

    return runner, aufrufe


def test_first_candidate_wins():
    runner, aufrufe = _lauf([0], [])
    zeilen = []
    assert update._restart_service(zeilen.append, runner=runner) is True
    assert len(aufrufe) == 1


def test_falls_back_to_the_next_path():
    """Die sudoers-Regel nennt einen Pfad, der Suchpfad einen anderen."""
    runner, aufrufe = _lauf([1, 0], [])
    zeilen = []
    assert update._restart_service(zeilen.append, runner=runner) is True
    assert len(aufrufe) == 2
    assert aufrufe[0] != aufrufe[1]


def test_reports_failure_when_nothing_works():
    runner, aufrufe = _lauf([1] * len(update.RESTART_COMMANDS), [])
    zeilen = []
    assert update._restart_service(zeilen.append, runner=runner) is False
    assert len(aufrufe) == len(update.RESTART_COMMANDS)
    # Der Nutzer muss erfahren, dass der Code erst beim nächsten Start greift.
    assert any("naechsten Start" in z or "nächsten Start" in z for z in zeilen)


def test_survives_a_raising_runner():
    runner, aufrufe = _lauf([FileNotFoundError("kein systemctl"), 0], [])
    zeilen = []
    assert update._restart_service(zeilen.append, runner=runner) is True
    assert len(aufrufe) == 2


def test_every_candidate_is_non_interactive():
    """Ohne -n wartet sudo auf ein Passwort, das niemand eingeben kann."""
    for cmd in update.RESTART_COMMANDS:
        assert cmd[0] == "sudo"
        assert "-n" in cmd


def test_reboot_has_the_same_fallbacks():
    assert len(update.REBOOT_COMMANDS) >= 2
    for cmd in update.REBOOT_COMMANDS:
        assert cmd[0] == "sudo"
        assert "-n" in cmd
